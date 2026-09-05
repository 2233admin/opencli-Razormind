"""M3: OpenCLI snapshot collector — opencli → Record v2 → odp.ingest::batch."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from iii_observability import Logger

from iii import InitOptions, register_worker

# /app/worker/src/main.py → parents[2] == /app (schedule-bootstrap uses parents[3])
III_ROOT = Path(__file__).resolve().parents[2]
if str(III_ROOT) not in sys.path:
    sys.path.insert(0, str(III_ROOT))

from lib import opencli_cli  # noqa: E402
from lib.env_bootstrap import bootstrap_worker_env  # noqa: E402
from lib.odp_record import opencli_items_to_events, source_id_for_opencli  # noqa: E402

bootstrap_worker_env()

worker = register_worker(
    os.environ.get("III_URL", "ws://localhost:49134"),
    InitOptions(
        worker_name="collector-opencli",
        worker_description="OpenCLI site snapshots (B站/小红书/Twitter等) into ODP ingest",
    ),
)
logger = Logger()


def _unwrap_admin_command(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = payload.get("admin_command_json")
    if encoded is None:
        return payload
    if not isinstance(encoded, str):
        raise ValueError("admin_command_json must be a JSON object string")
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("admin_command_json must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("admin_command_json must decode to an object")
    return decoded

def _admin_collection_metadata(
    payload: dict[str, Any], *, site: str, command: str, source_id: str, task_id: str, trace_id: str
) -> dict[str, Any] | None:
    metadata = payload.get("admin_collection")
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValueError("admin_collection must be an object")
    required = (
        "workspace_id",
        "project_id",
        "workflow_id",
        "studio_workflow_version_id",
        "run_id",
        "node_id",
        "command_id",
        "attempt_id",
        "attempt_number",
        "task_id",
        "trace_id",
        "source_id",
        "payload_sha256",
    )
    if metadata.get("version") != "v1" or any(not metadata.get(key) for key in required):
        raise ValueError("admin_collection is missing immutable V1 correlation fields")
    if (
        str(metadata["task_id"]) != task_id
        or str(metadata["trace_id"]) != trace_id
        or str(metadata["source_id"]) != source_id
    ):
        raise ValueError("admin_collection IDs must match the collector payload")
    canonical_payload = {
        "site": site,
        "command": command,
        "args": payload.get("args") or {},
        "format": str(payload.get("format") or "json"),
        "source_id": source_id,
    }
    if payload.get("mode") is not None:
        canonical_payload["mode"] = payload["mode"]
    payload_sha256 = hashlib.sha256(
        json.dumps(
            canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if metadata["payload_sha256"] != payload_sha256:
        raise ValueError("admin_collection payload hash does not match the executed payload")
    if not os.environ.get("ADMIN_III_LIFECYCLE_URL", "").strip():
        raise ValueError("ADMIN_III_LIFECYCLE_URL is required for governed Admin collection")

    return metadata


def _emit_admin_lifecycle(
    metadata: dict[str, Any] | None,
    *,
    event_type: str,
    sequence: int,
    summary: dict[str, int] | None = None,
) -> None:
    if metadata is None:
        return
    lifecycle_url = os.environ.get("ADMIN_III_LIFECYCLE_URL", "").strip()
    if not lifecycle_url:
        logger.warning(
            "admin III lifecycle callback is not configured",
            {"command_id": metadata["command_id"], "event_type": event_type},
        )
        return
    body = {
        **metadata,
        "sequence": sequence,
        "event_type": event_type,
        "summary": summary or {},
    }
    headers = {"content-type": "application/json"}
    fleet_token = os.environ.get("API_AUTH_TOKEN", "")
    if fleet_token:
        headers["authorization"] = f"Bearer {fleet_token}"

    lifecycle_token = os.environ.get("ADMIN_III_LIFECYCLE_TOKEN", "")
    if lifecycle_token:
        headers["x-iii-bridge-token"] = lifecycle_token
    try:
        response = httpx.post(lifecycle_url, json=body, headers=headers, timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # Do not continue collection as if a correlated lifecycle observation
        # had reached Admin. Re-delivery of the immutable attempt replays the
        # observation key; Admin's ledger deduplicates it.
        logger.warning(
            "admin III lifecycle callback failed",
            {"command_id": metadata["command_id"], "event_type": event_type},
        )
        raise RuntimeError("admin III lifecycle callback failed") from exc


def _expected_key_set_sha256(events: list[dict[str, Any]]) -> str:
    keys = sorted(
        {(str(event["source_id"]), str(event["event_id"])) for event in events},
        key=lambda key: (key[0], key[1]),
    )
    return hashlib.sha256(
        json.dumps(
            {
                "expected_keys": [
                    {"source_id": source_id, "event_id": event_id}
                    for source_id, event_id in keys
                ]
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _emit_expected_key_report(
    metadata: dict[str, Any] | None, events: list[dict[str, Any]], *, rejected_count: int
) -> None:
    if metadata is None:
        return
    lifecycle_url = os.environ["ADMIN_III_LIFECYCLE_URL"].rstrip("/")
    if not lifecycle_url.endswith("/lifecycle"):
        raise RuntimeError("ADMIN_III_LIFECYCLE_URL must name the Admin lifecycle endpoint")
    expected_keys = [
        {"source_id": source_id, "event_id": event_id}
        for source_id, event_id in sorted(
            {(str(event["source_id"]), str(event["event_id"])) for event in events},
            key=lambda key: (key[0], key[1]),
        )
    ]
    report = {
        **metadata,
        "report_id": f"collector:{metadata['attempt_id']}:1",
        "report_sequence": 1,
        "expected_keys": expected_keys,
        "expected_key_set_sha256": _expected_key_set_sha256(events),
        "item_count": len(events),
        "zero_count": int(not events),
        "rejected_count": rejected_count,
        "reported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    report["report_hash"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    headers = {"content-type": "application/json"}
    fleet_token = os.environ.get("API_AUTH_TOKEN", "")
    if fleet_token:
        headers["authorization"] = f"Bearer {fleet_token}"
    bridge_token = os.environ.get("ADMIN_III_LIFECYCLE_TOKEN", "")
    if bridge_token:
        headers["x-iii-bridge-token"] = bridge_token
    try:
        response = httpx.post(
            f"{lifecycle_url.rsplit('/', 1)[0]}/expected-key-reports",
            json=report,
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("Admin expected-key report callback failed") from exc


def opencli_snapshot_handler(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _unwrap_admin_command(payload)
    site = str(payload.get("site") or "").strip()
    command = str(payload.get("command") or "").strip()
    if not site or not command:
        raise ValueError("site and command are required")

    source_id = source_id_for_opencli(site, command, payload.get("source_id"))
    task_id = str(payload.get("task_id") or uuid.uuid4())
    trace_id = str(payload.get("trace_id") or uuid.uuid4())
    metadata = _admin_collection_metadata(
        payload, site=site, command=command, source_id=source_id, task_id=task_id, trace_id=trace_id
    )
    _emit_admin_lifecycle(metadata, event_type="bridge_accepted", sequence=1)
    _emit_admin_lifecycle(metadata, event_type="collector_started", sequence=2)

    logger.info(
        "odp.collect::opencli_snapshot",
        {
            "site": site,
            "command": command,
            "schedule_id": payload.get("schedule_id"),
            "cron_job_id": (payload.get("cron") or {}).get("job_id"),
        },
    )

    collect_result = opencli_cli.run_collect(
        site=site,
        command=command,
        args=payload.get("args"),
        positional_args=payload.get("positional_args"),
        output_format=str(payload.get("format") or "json"),
        mode=payload.get("mode"),
        chrome_endpoint=payload.get("chrome_endpoint"),
    )
    items = collect_result.get("items") or []
    events = opencli_items_to_events(
        items,
        site=site,
        command=command,
        source_id=source_id,
        task_id=task_id,
        trace_id=trace_id,
    )
    if len({(str(event["source_id"]), str(event["event_id"])) for event in events}) != len(events):
        raise ValueError("collector produced duplicate expected source/event keys")
    if len(events) > 1000:
        raise ValueError("collector expected-key report exceeds the 1000-key bound")

    ingest_result: dict[str, Any] = {"sent": 0, "accepted": 0, "duplicates": 0, "rejected": 0}
    if events:
        ingest_payload: dict[str, Any] = {
            "events": events,
            "trace_id": trace_id,
            "task_id": task_id,
        }
        if metadata is not None:
            ingest_payload["admin_collection"] = {
                **metadata,
                "expected_key_set_sha256": _expected_key_set_sha256(events),
            }
        ingest_result = worker.trigger(
            {
                "function_id": "odp.ingest::batch",
                "payload": ingest_payload,
            }
        )
    _emit_expected_key_report(
        metadata,
        events,
        rejected_count=min(len(events), max(0, int(ingest_result.get("rejected", 0)))),
    )
    _emit_admin_lifecycle(
        metadata,
        event_type="collector_returned",
        sequence=3,
        summary={"items_fetched": len(items)},
    )

    response = {
        "ok": True,
        "site": site,
        "command": command,
        "source_id": source_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "schedule_id": payload.get("schedule_id"),
        "collect": collect_result,
        "items_fetched": len(items),
        "ingest": ingest_result,
    }
    if metadata is not None:
        response["admin_collection"] = {
            "command_id": metadata["command_id"],
            "attempt_id": metadata["attempt_id"],
            "payload_sha256": metadata["payload_sha256"],
        }
    return response


def status_handler(_payload: dict[str, Any]) -> dict[str, Any]:
    import shutil

    bin_path = os.environ.get("OPENCLI_BIN", "opencli")
    return {
        "opencli_bin": bin_path,
        "opencli_found": bool(shutil.which(bin_path) or os.path.isfile(bin_path)),
        "mode": os.environ.get("OPENCLI_MODE", "bridge"),
        "daemon_host": os.environ.get("OPENCLI_DAEMON_HOST", "agent-1"),
        "daemon_port": os.environ.get("OPENCLI_DAEMON_PORT", "19825"),
    }


worker.register_function(
    "odp.collect::opencli_snapshot",
    opencli_snapshot_handler,
    description="Run opencli site/command and ingest items as Record v2",
)
worker.register_function(
    "opencli::status",
    status_handler,
    description="opencli binary and bridge/cdp connection settings",
)

print("collector-opencli started — odp.collect::opencli_snapshot, opencli::status")
