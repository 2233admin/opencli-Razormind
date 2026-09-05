"""Strict Admin-to-ODP reconciliation transport.

The public API must derive this module's requests from durable Admin attempt
context. It deliberately exposes builders for only exact, attempt-page, and
DLQ reconciliation: browser-provided predicates, JSONB paths, and arbitrary
ODP request bodies never cross this seam.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID

import httpx

MAX_ALLOWED_SOURCES = 100
MAX_KEYS = 100
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 4096
MAX_RESPONSE_BYTES = 128 * 1024
REDACTION_PROFILE_VERSION = "odp-query-reference-v1"
SAFE_FIELDS = frozenset(
    {"source_id", "event_id", "odp_record_id", "committed_at", "provider", "source_ts"}
)
QueryMode = Literal["exact", "attempt_page", "dlq"]


class OdpQueryError(RuntimeError):
    """Redacted query-service failure safe to surface through Admin."""


class OdpQueryUnavailableError(OdpQueryError):
    """The read service cannot establish a reconciliation result."""


class OdpQueryRejectedError(OdpQueryError):
    """The read service rejected an invalid delegated request."""


# Compatibility names for callers migrating to the PEP 8 ``Error`` suffix.
OdpQueryUnavailable = OdpQueryUnavailableError
OdpQueryRejected = OdpQueryRejectedError



@dataclass(frozen=True)
class OdpRecordKey:
    source_id: UUID
    event_id: str

    def to_wire(self) -> dict[str, str]:
        if not self.event_id or len(self.event_id) > 512:
            raise OdpQueryRejected("ODP reconciliation request was rejected")
        return {"source_id": str(self.source_id), "event_id": self.event_id}


@dataclass(frozen=True)
class OdpReconciliationDelegation:
    """Immutable Admin-derived scope accepted by ``odp-query``.

    This type is intentionally constructed by server-side Admin lifecycle code,
    not from browser input. Its fingerprint algorithm is shared with the Rust
    service's canonical scope fingerprint.
    """

    workspace_id: str
    project_id: str
    workflow_id: str
    run_id: str
    batch_id: str
    attempt_id: str
    task_id: UUID
    trace_id: UUID
    allowed_source_ids: tuple[UUID, ...]
    expires_at: datetime
    allowed_modes: tuple[QueryMode, ...] = ("exact", "attempt_page", "dlq")
    allowed_fields: frozenset[str] = SAFE_FIELDS

    def to_wire(self) -> dict[str, Any]:
        if (
            not all(
                value.strip()
                for value in (
                    self.workspace_id,
                    self.project_id,
                    self.workflow_id,
                    self.run_id,
                    self.batch_id,
                    self.attempt_id,
                )
            )
            or not self.allowed_source_ids
            or len(self.allowed_source_ids) > MAX_ALLOWED_SOURCES
            or len(set(self.allowed_source_ids)) != len(self.allowed_source_ids)
            or set(self.allowed_fields) != SAFE_FIELDS
            or len(self.allowed_modes) != len(set(self.allowed_modes))
            or any(mode not in {"exact", "attempt_page", "dlq"} for mode in self.allowed_modes)
        ):
            raise OdpQueryRejected("ODP reconciliation request was rejected")
        expires_at = _rfc3339_utc(self.expires_at)
        if self.expires_at.astimezone(UTC) <= datetime.now(UTC):
            raise OdpQueryRejected("ODP reconciliation request was rejected")

        scope = {
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "attempt_id": self.attempt_id,
            "task_id": str(self.task_id),
            "trace_id": str(self.trace_id),
            "allowed_source_ids": sorted(map(str, self.allowed_source_ids)),
            "allowed_fields": sorted(self.allowed_fields),
            "allowed_modes": _canonical_modes(self.allowed_modes),
        }
        return {
            **scope,
            "expires_at": expires_at,
            "query_fingerprint": _fingerprint(scope),
        }


def build_exact_request(
    delegation: OdpReconciliationDelegation, keys: list[OdpRecordKey]
) -> dict[str, Any]:
    return _request_with_keys(delegation, "exact", keys)


def build_dlq_request(
    delegation: OdpReconciliationDelegation, keys: list[OdpRecordKey]
) -> dict[str, Any]:
    return _request_with_keys(delegation, "dlq", keys)


def build_attempt_page_request(
    delegation: OdpReconciliationDelegation,
    *,
    cursor: str | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    scope = delegation.to_wire()
    if "attempt_page" not in delegation.allowed_modes:
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    if cursor is not None and (not cursor or len(cursor) > MAX_CURSOR_LENGTH):
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    if page_size is not None and not 1 <= page_size <= MAX_PAGE_SIZE:
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    request: dict[str, Any] = {"delegation": scope, "mode": "attempt_page"}
    if cursor is not None:
        request["cursor"] = cursor
    if page_size is not None:
        request["page_size"] = page_size
    return request


async def post_reconciliation_query(request: dict[str, Any]) -> dict[str, Any]:
    """Invoke the internal service and return only its safe response projection."""

    base_url = os.environ.get("ODP_QUERY_URL", "").strip().rstrip("/")
    credential = os.environ.get("ODP_QUERY_ADMIN_CREDENTIAL", "").strip()
    if not base_url or not credential:
        raise OdpQueryUnavailable("ODP reconciliation is unavailable")
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            response = await client.post(
                f"{base_url}/internal/v1/evidence-records:query",
                headers={"Authorization": f"Bearer {credential}"},
                json=request,
            )
    except httpx.HTTPError as exc:
        raise OdpQueryUnavailable("ODP reconciliation is unavailable") from exc
    if response.status_code >= 500:
        raise OdpQueryUnavailable("ODP reconciliation is unavailable")
    if response.status_code >= 400:
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    try:
        return sanitize_query_response(response.json(), request)
    except (TypeError, ValueError, KeyError) as exc:
        raise OdpQueryUnavailable("ODP reconciliation is unavailable") from exc


def sanitize_query_response(payload: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Drop unexpected service output before it can reach the Admin presentation layer."""

    delegation = request.get("delegation")
    mode = request.get("mode")
    if (
        not isinstance(payload, dict)
        or not isinstance(delegation, dict)
        or mode not in {"exact", "attempt_page", "dlq"}
        or payload.get("query_fingerprint") != delegation.get("query_fingerprint")
        or payload.get("mode") != mode
        or payload.get("retention_state") != "unknown"
        or payload.get("redaction_profile_version") != REDACTION_PROFILE_VERSION
    ):
        raise ValueError("invalid odp-query response")
    output: dict[str, Any] = {
        "mode": payload["mode"],
        "query_fingerprint": payload["query_fingerprint"],
        "retention_state": "unknown",
        "redaction_profile_version": REDACTION_PROFILE_VERSION,
        "records": [_sanitize_reference(record) for record in payload.get("records", [])],
        "results": [_sanitize_result(result) for result in payload.get("results", [])],
    }
    for name in ("as_of", "next_cursor"):
        if name in payload:
            value = payload[name]
            if value is not None and not isinstance(value, str):
                raise ValueError("invalid odp-query response")
            if name == "next_cursor" and value is not None and len(value) > MAX_CURSOR_LENGTH:
                raise ValueError("invalid odp-query response")
            output[name] = value
    _validate_response_scope(output, request)
    if len(json.dumps(output, separators=(",", ":")).encode()) > MAX_RESPONSE_BYTES:
        raise ValueError("oversized odp-query response")
    return output


def _request_with_keys(
    delegation: OdpReconciliationDelegation,
    mode: Literal["exact", "dlq"],
    keys: list[OdpRecordKey],
) -> dict[str, Any]:
    scope = delegation.to_wire()
    if mode not in delegation.allowed_modes or not 1 <= len(keys) <= MAX_KEYS:
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    if len(set(keys)) != len(keys):
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    allowed_sources = set(delegation.allowed_source_ids)
    if any(key.source_id not in allowed_sources for key in keys):
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    return {"delegation": scope, "mode": mode, "keys": [key.to_wire() for key in keys]}


def _sanitize_reference(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid odp-query record reference")
    reference = {
        name: value[name]
        for name in (
            "source_id",
            "event_id",
            "odp_record_id",
            "committed_at",
            "provider",
            "source_ts",
        )
    }
    if (
        not isinstance(reference["source_id"], str)
        or not isinstance(reference["event_id"], str)
        or not isinstance(reference["odp_record_id"], int)
        or not all(
            isinstance(reference[name], str)
            for name in ("committed_at", "provider", "source_ts")
        )
    ):
        raise ValueError("invalid odp-query record reference")
    return reference


def _sanitize_result(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("classification") not in {"present", "dlq", "unknown"}
    ):
        raise ValueError("invalid odp-query reconciliation result")
    if value.get("retention_state") != "unknown":
        raise ValueError("invalid odp-query reconciliation result")
    result = {
        "key": _sanitize_key(value["key"]),
        "classification": value["classification"],
        "retention_state": "unknown",
    }
    if "record" in value:
        if value["record"] is None:
            raise ValueError("invalid odp-query reconciliation result")
        result["record"] = _sanitize_reference(value["record"])
    return result


def _sanitize_key(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not isinstance(value.get("source_id"), str) or not isinstance(
        value.get("event_id"), str
    ):
        raise ValueError("invalid odp-query record key")
    return {"source_id": value["source_id"], "event_id": value["event_id"]}


def _fingerprint(scope: dict[str, Any]) -> str:
    return sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()


def _canonical_modes(modes: tuple[QueryMode, ...]) -> list[QueryMode]:
    return sorted(modes, key={"exact": 0, "attempt_page": 1, "dlq": 2}.__getitem__)


def _validate_response_scope(response: dict[str, Any], request: dict[str, Any]) -> None:
    delegation = request["delegation"]
    allowed_sources = set(delegation["allowed_source_ids"])
    if any(record["source_id"] not in allowed_sources for record in response["records"]):
        raise ValueError("out-of-scope odp-query record")
    keys = request.get("keys")
    if keys is None:
        if response["results"]:
            raise ValueError("unexpected reconciliation results")
        return
    expected = {(key["source_id"], key["event_id"]) for key in keys}
    actual = {
        (result["key"]["source_id"], result["key"]["event_id"])
        for result in response["results"]
    }
    if actual != expected or len(response["results"]) != len(keys):
        raise ValueError("incomplete reconciliation results")
    if any(
        (record["source_id"], record["event_id"]) not in expected
        for record in response["records"]
    ):
        raise ValueError("out-of-scope odp-query record")
    for result in response["results"]:
        record = result.get("record")
        if record is not None and (
            (record["source_id"], record["event_id"])
            != (result["key"]["source_id"], result["key"]["event_id"])
        ):
            raise ValueError("mismatched odp-query result record")


def _rfc3339_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise OdpQueryRejected("ODP reconciliation request was rejected")
    normalized = value.astimezone(UTC)
    if normalized.microsecond:
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _timeout() -> float:
    try:
        return float(os.environ.get("ODP_QUERY_TIMEOUT", "10"))
    except ValueError:
        return 10.0
