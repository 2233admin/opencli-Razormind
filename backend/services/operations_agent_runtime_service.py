"""Dispatch Operations Agent runs through the existing edge-runtime protocol."""

import asyncio
import logging
from typing import Any, cast

from pydantic import JsonValue, ValidationError
from sqlalchemy import select, update

from backend.database import AsyncSessionLocal
from backend.models.operations_agent import (
    AgentPermissionProfile,
    AgentProfileMode,
    OperationsAgentRun,
    PublishedOperationsAgentVersion,
)
from backend.schemas.operations_agent import (
    AgentContractV2,
    AgentRunEvidenceEnvelopeV1,
    agent_contract_from_model_configuration,
    agent_runtime_binding_from_model_configuration,
    validate_agent_contract_payload,
)
from backend.services.agent_runtime_selection import select_agent_runtime
from backend.ws_agent_manager import send_agent_task

logger = logging.getLogger(__name__)

# ponytail: process-local dispatch ownership; move this run_id map to a durable
# broker if Operations Agent runs must survive API process loss mid-flight.
_ACTIVE_DISPATCHES: dict[str, asyncio.Task[None]] = {}

_SENSITIVE_EVENT_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "refresh_token",
        "secret",
    }
)


def _redact_runtime_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.lower().replace("-", "_") in _SENSITIVE_EVENT_KEYS
                else _redact_runtime_value(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_runtime_value(child) for child in value]
    return value


def _event_record(sequence: int, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "type": str(event.get("type") or "unknown"),
        "payload": _redact_runtime_value(
            {key: value for key, value in event.items() if key not in {"type", "task_id"}}
        ),
    }


def schedule_operations_agent_run(run_id: str) -> None:
    task = asyncio.create_task(dispatch_operations_agent_run(run_id))
    _ACTIVE_DISPATCHES[run_id] = task
    task.add_done_callback(lambda completed: _forget_dispatch(run_id, completed))


def cancel_operations_agent_run(run_id: str) -> None:
    task = _ACTIVE_DISPATCHES.get(run_id)
    if task is not None:
        task.cancel()




def _forget_dispatch(run_id: str, task: asyncio.Task[None]) -> None:
    if _ACTIVE_DISPATCHES.get(run_id) is task:
        _ACTIVE_DISPATCHES.pop(run_id, None)


async def dispatch_operations_agent_run(run_id: str) -> None:
    recorded_events: list[dict[str, Any]] = []
    selection: dict[str, Any] | None = None
    try:
        async with AsyncSessionLocal() as session:
            claimed = await session.execute(
                update(OperationsAgentRun)
                .where(OperationsAgentRun.id == run_id)
                .where(OperationsAgentRun.status == "queued")
                .values(status="running")
            )
            await session.commit()
            if getattr(claimed, "rowcount", 0) != 1:
                return

        async with AsyncSessionLocal() as session:
            run = await session.get(OperationsAgentRun, run_id)
            if run is None:
                return
            version = await session.scalar(
                select(PublishedOperationsAgentVersion)
                .where(
                    PublishedOperationsAgentVersion.operations_agent_id == run.operations_agent_id
                )
                .where(PublishedOperationsAgentVersion.version == run.published_version)
            )
            if version is None:
                await _fail_run(run_id, "Published Agent Version is missing")
                return
            profile = await session.scalar(
                select(AgentPermissionProfile)
                .where(AgentPermissionProfile.operations_agent_id == run.operations_agent_id)
                .where(AgentPermissionProfile.version == run.profile_version)
            )
            if profile is None:
                await _fail_run(run_id, "Pinned Agent Permission Profile is missing")
                return
            if profile.mode == AgentProfileMode.LOW_RISK_AUTOMATIC:
                await _fail_run(
                    run_id,
                    "Automatic Operations Agent runs require the governed action gateway",
                )
                return
            try:
                binding = agent_runtime_binding_from_model_configuration(
                    version.model_configuration
                )
                contract = agent_contract_from_model_configuration(version.model_configuration)
            except ValidationError:
                await _fail_run(run_id, "Published Operations Agent configuration is invalid")
                return
            if binding is None or contract is None:
                await _fail_run(
                    run_id,
                    "Published Agent Version requires contract and runtime binding",
                )
                return

            selection = await select_agent_runtime(
                session,
                contract=contract,
                binding=binding,
            )
            run.execution_binding = selection
            await session.commit()

            runtime_input = cast(dict[str, Any], run.input_payload)
            runtime_config = dict(binding.config)
            configured_timeout = runtime_config.get("timeout_seconds")
            if (
                not isinstance(configured_timeout, (int, float))
                or isinstance(configured_timeout, bool)
                or configured_timeout < binding.dispatch_timeout_seconds
            ):
                # The edge runtime must not expire before the governed outer
                # deep-run profile. Binding validation supplies the hard
                # ceiling; this fills/raises the inner timeout to that profile.
                runtime_config["timeout_seconds"] = binding.dispatch_timeout_seconds
            runtime_config["permission_mode"] = profile.mode
            permissions = contract.tool_policy

        state_contract_error: str | None = None

        async def on_event(event: dict[str, Any]) -> None:
            nonlocal state_contract_error
            recorded_events.append(_event_record(len(recorded_events) + 1, event))
            if event.get("type") != "state":
                return
            state = event.get("state")
            if not isinstance(state, dict):
                state_contract_error = "Runtime state event must contain an object"
                return
            try:
                validate_agent_contract_payload(
                    contract,
                    "state_schema",
                    cast(dict[str, JsonValue], state),
                )
            except ValueError as exc:
                state_contract_error = str(exc)
                return
            await _persist_state(run_id, state)

        terminal = await send_agent_task(
            str(selection["agent_url"]),
            {
                "runtime": selection["runtime"],
                "workflow": selection["workflow"],
                "instructions": version.instructions,
                "input": runtime_input,
                "config": runtime_config,
                "session_id": None,
                "provider": selection.get("provider"),
                "model": selection.get("model"),
                "required_capabilities": contract.required_capabilities,
                "permissions": permissions,
                "budget": contract.budget,
                "evidence_requirements": contract.evidence_requirements,
            },
            on_event,
            timeout=float(binding.dispatch_timeout_seconds),
        )
        if not recorded_events or recorded_events[-1].get("type") != terminal.get("type"):
            recorded_events.append(_event_record(len(recorded_events) + 1, terminal))

        evidence_payload = _build_evidence_payload(
            selection,
            recorded_events,
            terminal.get("result") if terminal.get("type") == "done" else None,
        )
        if state_contract_error is not None:
            await _fail_run(
                run_id,
                f"Runtime state violates AgentContractV2: {state_contract_error}",
                evidence_payload=evidence_payload,
            )
            return
        if terminal.get("type") == "error":
            await _fail_run(
                run_id,
                str(terminal.get("message") or "Runtime failed"),
                evidence_payload=evidence_payload,
            )
            return
        if terminal.get("type") != "done":
            await _fail_run(
                run_id,
                "Runtime returned no terminal done/error event",
                evidence_payload=evidence_payload,
            )
            return
        output = terminal.get("result") or {}
        if not isinstance(output, dict):
            await _fail_run(
                run_id,
                "Runtime output must be an object",
                evidence_payload=evidence_payload,
            )
            return
        try:
            validate_agent_contract_payload(
                contract,
                "output_schema",
                cast(dict[str, JsonValue], output),
            )
        except ValueError as exc:
            await _fail_run(
                run_id,
                f"Runtime output violates AgentContractV2: {exc}",
                evidence_payload=evidence_payload,
            )
            return
        gate_failures = _quality_gate_failures(contract, output)
        if gate_failures:
            await _fail_run(
                run_id,
                "Runtime output failed required quality gates: " + ", ".join(gate_failures),
                evidence_payload=evidence_payload,
            )
            return
        missing_evidence = _missing_evidence_requirements(
            contract,
            evidence_payload,
        )
        if missing_evidence:
            await _fail_run(
                run_id,
                "Runtime output is missing required evidence: " + ", ".join(missing_evidence),
                evidence_payload=evidence_payload,
            )
            return
        await _complete_run(run_id, output, evidence_payload)
    except Exception as exc:
        logger.exception("Operations Agent run dispatch failed | run_id=%s", run_id)
        await _fail_run(
            run_id,
            str(exc),
            evidence_payload=(
                _build_evidence_payload(selection, recorded_events, None)
                if selection is not None
                else None
            ),
        )


def _output_objects(output: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(output, dict):
        return []
    values = output.get(key)
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _quality_gate_failures(
    contract: AgentContractV2,
    output: dict[str, Any],
) -> list[str]:
    results = {
        str(item.get("id")): item
        for item in _output_objects(output, "quality_gates")
        if item.get("id")
    }
    failures: list[str] = []
    for gate in contract.quality_gates:
        if not gate.required:
            continue
        result = results.get(gate.id)
        if result is None or not (
            result.get("passed") is True or result.get("status") in {"passed", "completed"}
        ):
            failures.append(gate.id)
    return failures


def _missing_evidence_requirements(
    contract: AgentContractV2,
    envelope: dict[str, Any],
) -> list[str]:
    available = {"runtime_events"} if envelope.get("events") else set()
    for category in ("artifacts", "evidence", "lineage", "audit"):
        values = envelope.get(category)
        if not isinstance(values, list) or not values:
            continue
        available.add(category)
        available.add(category.removesuffix("s"))
        for value in values:
            if not isinstance(value, dict):
                continue
            for key in ("id", "kind", "type", "schema"):
                identifier = value.get(key)
                if isinstance(identifier, str):
                    available.add(identifier)
    return sorted(set(contract.evidence_requirements) - available)


def _build_evidence_payload(
    selection: dict[str, Any],
    events: list[dict[str, Any]],
    output: dict[str, Any] | None,
) -> dict[str, Any]:
    artifacts = [
        record["payload"]["artifact"]
        for record in events
        if record.get("type") == "artifact"
        and isinstance(record.get("payload"), dict)
        and isinstance(record["payload"].get("artifact"), dict)
    ]
    evidence = [
        record["payload"]["evidence"]
        for record in events
        if record.get("type") == "evidence"
        and isinstance(record.get("payload"), dict)
        and isinstance(record["payload"].get("evidence"), dict)
    ]
    audit = [
        {
            "type": "runtime_selected",
            "runtime": selection["runtime"],
            "agent_url": selection["agent_url"],
            "capabilities": selection["capabilities"],
        },
        *[
            record["payload"]["audit"]
            for record in events
            if record.get("type") == "audit"
            and isinstance(record.get("payload"), dict)
            and isinstance(record["payload"].get("audit"), dict)
        ],
    ]
    lineage = [
        {
            "type": "agent_runtime",
            "runtime": selection["runtime"],
            "workflow": selection["workflow"],
            "provider": selection.get("provider"),
            "model": selection.get("model"),
        }
    ]
    envelope = AgentRunEvidenceEnvelopeV1(
        runtime=cast(dict[str, JsonValue], _redact_runtime_value(selection)),
        events=cast(list[dict[str, JsonValue]], events),
        artifacts=cast(
            list[dict[str, JsonValue]],
            [*artifacts, *_output_objects(output, "artifacts")],
        ),
        evidence=cast(
            list[dict[str, JsonValue]],
            [*evidence, *_output_objects(output, "evidence")],
        ),
        lineage=cast(
            list[dict[str, JsonValue]],
            [*lineage, *_output_objects(output, "lineage")],
        ),
        audit=cast(
            list[dict[str, JsonValue]],
            [*audit, *_output_objects(output, "audit")],
        ),
    )
    return envelope.model_dump(mode="json")


async def _persist_state(run_id: str, state: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(OperationsAgentRun)
            .where(OperationsAgentRun.id == run_id)
            .where(OperationsAgentRun.status == "running")
            .values(state_payload=state)
        )
        await session.commit()


async def _complete_run(
    run_id: str,
    output: dict[str, Any],
    evidence_payload: dict[str, Any],
) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(OperationsAgentRun)
            .where(OperationsAgentRun.id == run_id)
            .where(OperationsAgentRun.status == "running")
            .values(
                output_payload=output,
                evidence_payload=evidence_payload,
                error_message=None,
                status="completed",
            )
        )
        await session.commit()


async def _fail_run(
    run_id: str,
    message: str,
    *,
    evidence_payload: dict[str, Any] | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(OperationsAgentRun)
            .where(OperationsAgentRun.id == run_id)
            .where(OperationsAgentRun.status.in_(("queued", "running")))
            .values(
                error_message=message[:4000],
                evidence_payload=evidence_payload,
                status="failed",
            )
        )
        await session.commit()
