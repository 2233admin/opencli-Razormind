"""Contracts for workflow runtime conformance transcripts and passports."""

# ruff: noqa: N815

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.schemas.workflow_runtime import WorkflowNodeRunEventType, WorkflowRunStatus
from backend.workflow.block_reasons import BlockReasonCategory, block_reason_category


class ExpectedWorkflowRunEvent(BaseModel):
    nodeId: str = Field(..., min_length=1)
    eventType: WorkflowNodeRunEventType
    bindingId: str | None = None
    expectedNodeStatus: WorkflowRunStatus | None = None
    blockReasonCode: str | None = None
    blockReasonCategory: BlockReasonCategory | None = None
    messageContains: str | None = None
    detailsSubset: dict[str, Any] = Field(default_factory=dict)
    blockReasonDetailsSubset: dict[str, Any] = Field(default_factory=dict)


class TranscriptMatchResult(BaseModel):
    passed: bool
    failures: list[str] = Field(default_factory=list)
    matchedEvents: list[dict[str, Any]] = Field(default_factory=list)


class ConformanceCaseResult(BaseModel):
    id: str = Field(..., min_length=1)
    status: Literal["passed", "failed"]
    bindings: list[str] = Field(default_factory=list)
    blockedReasons: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)


class RuntimePassportBinding(BaseModel):
    status: Literal["conformance-known", "failed", "preview-only"]
    evidenceCases: list[str] = Field(default_factory=list)
    blockedReasons: list[str] = Field(default_factory=list)


class RuntimePassport(BaseModel):
    schemaVersion: Literal[1] = 1
    generatedAt: str
    eventSource: Literal["workflow-run-events"] = "workflow-run-events"
    status: Literal["conformant", "partial", "preview-only", "failed"]
    cases: list[ConformanceCaseResult]
    bindings: dict[str, RuntimePassportBinding] = Field(default_factory=dict)


def load_expected_events(path: Path) -> list[ExpectedWorkflowRunEvent]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected event transcript must be a list: {path}")
    return [ExpectedWorkflowRunEvent.model_validate(item) for item in payload]


def parse_sse_node_events(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        event_name = ""
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())

        if event_name != "node_event" or not data_lines:
            continue
        payload = json.loads("\n".join(data_lines))
        if not isinstance(payload, dict):
            raise ValueError("SSE node_event payload must be a JSON object")
        events.append(payload)
    return events


def match_expected_events(
    actual_events: list[dict[str, Any]],
    expected_events: list[ExpectedWorkflowRunEvent],
) -> TranscriptMatchResult:
    failures: list[str] = []
    matched: list[dict[str, Any]] = []
    cursor = 0

    for expected in expected_events:
        match_index = None
        mismatch_notes: list[str] = []
        for index in range(cursor, len(actual_events)):
            actual = actual_events[index]
            event_failures = _event_failures(actual, expected)
            if not event_failures:
                match_index = index
                break
            mismatch_notes = event_failures

        if match_index is None:
            failures.append(
                "No matching event for "
                f"{expected.nodeId}/{expected.eventType}: "
                + "; ".join(mismatch_notes or ["event not present"])
            )
            continue

        matched.append(actual_events[match_index])
        cursor = match_index + 1

    return TranscriptMatchResult(
        passed=not failures,
        failures=failures,
        matchedEvents=matched,
    )


def write_runtime_passport(
    artifact_dir: Path,
    case_results: list[ConformanceCaseResult],
    *,
    status: Literal["conformant", "partial", "preview-only", "failed"] | None = None,
) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    passport = RuntimePassport(
        generatedAt=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        status=status
        or ("failed" if any(case.status == "failed" for case in case_results) else "partial"),
        cases=case_results,
        bindings=_binding_evidence(case_results),
    )
    path = artifact_dir / "opencli-runtime-passport.json"
    path.write_text(
        json.dumps(passport.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _event_failures(
    actual: dict[str, Any],
    expected: ExpectedWorkflowRunEvent,
) -> list[str]:
    failures: list[str] = []
    if actual.get("nodeId") != expected.nodeId:
        failures.append(f"nodeId {actual.get('nodeId')!r} != {expected.nodeId!r}")
    if actual.get("eventType") != expected.eventType:
        failures.append(f"eventType {actual.get('eventType')!r} != {expected.eventType!r}")

    if expected.bindingId is not None and _event_binding_id(actual) != expected.bindingId:
        failures.append(f"bindingId {_event_binding_id(actual)!r} != {expected.bindingId!r}")

    if "blockReasonCode" in expected.model_fields_set:
        actual_block = actual.get("blockReason")
        actual_code = actual_block.get("code") if isinstance(actual_block, dict) else None
        if actual_code != expected.blockReasonCode:
            failures.append(f"blockReasonCode {actual_code!r} != {expected.blockReasonCode!r}")

    if "blockReasonCategory" in expected.model_fields_set:
        actual_block = actual.get("blockReason")
        actual_code = actual_block.get("code") if isinstance(actual_block, dict) else None
        actual_category = (
            block_reason_category(actual_code) if isinstance(actual_code, str) else None
        )
        if actual_category != expected.blockReasonCategory:
            failures.append(
                f"blockReasonCategory {actual_category!r} != {expected.blockReasonCategory!r}"
            )

    if expected.messageContains:
        message = actual.get("message")
        if not isinstance(message, str) or expected.messageContains not in message:
            failures.append(f"message does not contain {expected.messageContains!r}")

    if expected.detailsSubset and not _is_subset(
        expected.detailsSubset,
        _read_dict(actual.get("details")),
    ):
        failures.append("detailsSubset did not match")

    if expected.blockReasonDetailsSubset and not _is_subset(
        expected.blockReasonDetailsSubset,
        _read_dict(_read_dict(actual.get("blockReason")).get("details")),
    ):
        failures.append("blockReasonDetailsSubset did not match")

    return failures


def _binding_evidence(
    case_results: list[ConformanceCaseResult],
) -> dict[str, RuntimePassportBinding]:
    by_binding: dict[str, RuntimePassportBinding] = {}
    for case in case_results:
        for binding in case.bindings:
            evidence = by_binding.setdefault(
                binding,
                RuntimePassportBinding(
                    status="conformance-known" if case.status == "passed" else "failed"
                ),
            )
            if case.id not in evidence.evidenceCases:
                evidence.evidenceCases.append(case.id)
            for reason in case.blockedReasons:
                if reason not in evidence.blockedReasons:
                    evidence.blockedReasons.append(reason)
            if case.status == "failed":
                evidence.status = "failed"
    return by_binding


def _event_binding_id(event: dict[str, Any]) -> str | None:
    details = _read_dict(event.get("details"))
    if isinstance(details.get("bindingId"), str):
        return details["bindingId"]
    block_details = _read_dict(_read_dict(event.get("blockReason")).get("details"))
    binding_id = block_details.get("bindingId") or block_details.get("binding_id")
    return binding_id if isinstance(binding_id, str) else None


def _is_subset(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        if key not in actual:
            return False
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict) or not _is_subset(expected_value, actual_value):
                return False
        elif actual_value != expected_value:
            return False
    return True


def _read_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
