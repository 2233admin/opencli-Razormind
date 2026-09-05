"""Certified Workflow executor for the native intelligence lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.intelligence import IntelligenceSession
from backend.workflow.intelligence.stages import NativeIntelligenceStages
from backend.workflow.intelligence_store import (
    IntelligenceCommandResult,
    IntelligenceConflictError,
    IntelligenceReferenceError,
    IntelligenceStore,
)
from backend.workflow.native_intelligence_contracts import (
    ArtifactKind,
    GraphArtifact,
    OntologyArtifact,
    PersonaArtifact,
    ReportArtifact,
    ResearchArtifact,
    SimulationArtifact,
    WorkflowConversationOrigin,
    canonical_hash,
)
from backend.workflow.runtime_contracts import (
    NATIVE_INTELLIGENCE_ACTION_CONTRACT_ROWS,
    NATIVE_INTELLIGENCE_COMMON_ERRORS,
    runtime_io_contract,
)

logger = logging.getLogger(__name__)

NATIVE_INTELLIGENCE_EXECUTOR = "native_intelligence"
NATIVE_INTELLIGENCE_BINDING_ID = "workflow.external-tool.capability"
NATIVE_INTELLIGENCE_FIXTURE_ID = "native-intelligence-offline-v1"
MAX_EXPLICIT_ARTIFACT_REFS = 64
MAX_INTERVIEW_ARTIFACT_REFS = 50
_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "native_intelligence_offline.json"
_FIXTURE_SHA256 = "a25378f83c0f4158c0aa240e82bfd484f103fb0db330da31ff596cf8d9532f96"
NATIVE_INTELLIGENCE_FIXTURES = {
    NATIVE_INTELLIGENCE_FIXTURE_ID: {
        "path": _FIXTURE_PATH,
        "sha256": _FIXTURE_SHA256,
        "inputKey": "evidence",
        "expectedKey": "expected",
    }
}

_REQUIRED_EXPLICIT_REF_ARTIFACT_KINDS = {
    "ontology": (ArtifactKind.RESEARCH,),
    "graph": (ArtifactKind.RESEARCH, ArtifactKind.ONTOLOGY),
    "personas": (
        ArtifactKind.RESEARCH,
        ArtifactKind.ONTOLOGY,
        ArtifactKind.GRAPH,
    ),
    "simulation.prepare": (ArtifactKind.PERSONA,),
    "simulation.start": (ArtifactKind.PERSONA,),
    "interviews.one": (ArtifactKind.PERSONA, ArtifactKind.SIMULATION),
    "interviews.batch": (ArtifactKind.PERSONA, ArtifactKind.SIMULATION),
    "interviews.all": (ArtifactKind.PERSONA, ArtifactKind.SIMULATION),
    "report.start": (
        ArtifactKind.PERSONA,
        ArtifactKind.SIMULATION,
        ArtifactKind.INTERVIEW,
    ),
    "report.read": (ArtifactKind.REPORT,),
    "report.ask": (ArtifactKind.REPORT,),
}


@dataclass(frozen=True)
class NativeIntelligenceAction:
    name: str
    label: str
    input_type: str
    output_type: str
    errors: tuple[str, ...]
    mutates: bool = True

    @property
    def tool_id(self) -> str:
        return f"tool.intelligence.native.{self.name}"

    @property
    def catalog_id(self) -> str:
        return f"intelligence.native.{self.name}"


_ACTION_LABELS = {
    "research": "Native Research",
    "ontology": "Build Ontology",
    "graph": "Build Evidence Graph",
    "personas": "Prepare Personas",
    "simulation.prepare": "Prepare Simulation",
    "simulation.start": "Start Simulation",
    "simulation.step": "Step Simulation",
    "simulation.run": "Run Simulation",
    "simulation.stop": "Stop Simulation",
    "simulation.resume": "Resume Simulation",
    "simulation.status": "Simulation Status",
    "simulation.actions": "Simulation Actions",
    "simulation.timeline": "Simulation Timeline",
    "simulation.stats": "Simulation Stats",
    "interviews.one": "Interview One Persona",
    "interviews.batch": "Interview Persona Batch",
    "interviews.all": "Interview All Personas",
    "interviews.step": "Step Interviews",
    "interviews.run": "Run Interviews",
    "interviews.history": "Interview History",
    "report.start": "Start Report",
    "report.step": "Step Report",
    "report.run": "Run Report",
    "report.progress": "Report Progress",
    "report.read": "Read Report",
    "report.ask": "Ask Report",
    "report.answers": "Report Answers",
    "cancel": "Cancel Intelligence Session",
    "close": "Close Intelligence Session",
}

NATIVE_INTELLIGENCE_ACTIONS = tuple(
    NativeIntelligenceAction(
        name,
        _ACTION_LABELS[name],
        input_type,
        output_type,
        tuple(dict.fromkeys((*errors, *NATIVE_INTELLIGENCE_COMMON_ERRORS))),
        mutates,
    )
    for name, input_type, output_type, errors, mutates in (
        NATIVE_INTELLIGENCE_ACTION_CONTRACT_ROWS
    )
)
NATIVE_INTELLIGENCE_LIFECYCLE_ACTIONS = (
    "research",
    "ontology",
    "graph",
    "personas",
    "simulation.start",
    "simulation.run",
    "simulation.timeline",
    "simulation.stats",
    "interviews.all",
    "interviews.run",
    "interviews.history",
    "report.start",
    "report.progress",
    "report.run",
    "report.read",
    "report.ask",
    "report.answers",
    "close",
)
NATIVE_INTELLIGENCE_ACTION_BY_NAME = {
    action.name: action for action in NATIVE_INTELLIGENCE_ACTIONS
}
NATIVE_INTELLIGENCE_ACTION_BY_TOOL_ID = {
    action.tool_id: action for action in NATIVE_INTELLIGENCE_ACTIONS
}

_PERMISSION_GATE_RESOLVERS: dict[str, str] = {}
_CONFIG_GATE_RESOLVERS: dict[str, str] = {}


def _resolve_database_session(context: dict[str, Any]) -> bool:
    return isinstance(context.get("session"), AsyncSession)


_RESOURCE_GATE_RESOLVERS: dict[str, dict[str, Any]] = {
    "database_session": {
        "resolver": _resolve_database_session,
        "blockReason": "native_intelligence_database_session_unavailable",
    },
}


def native_intelligence_action_manifest(action: NativeIntelligenceAction) -> dict[str, Any]:
    """Return the complete versioned contract and machine readiness result."""

    action_binding = f"workflow.native-intelligence.{action.name.replace('.', '-')}"
    registered_contract = runtime_io_contract(action_binding)
    registered_manifest = _registered_contract_manifest(registered_contract)
    contract = {
        "schemaVersion": 1,
        "action": action.name,
        "input": {"type": action.input_type, "schema": f"{action.input_type}.v1"},
        "output": {"type": action.output_type, "schema": f"{action.output_type}.v1"},
        "errors": [{"code": code, "stable": True} for code in action.errors],
        "determinism": {
            "deterministic": True,
            "seedParameter": "seed",
            "canonicalJson": True,
        },
        "offline": {"credentialFree": True, "networkRequired": False},
        "grounding": {
            "artifactReferencesRequired": action.name not in {"research", "cancel"},
            "simulatedContentSeparated": True,
        },
        "provenance": registered_manifest["provenance"],
        "limits": registered_manifest["limits"],
        "permissionGate": registered_manifest["permissionGate"],
        "configGate": registered_manifest["configGate"],
        "resourceGate": registered_manifest["resourceGate"],
        "eventShape": registered_manifest["eventShape"],
        "fixtureEvidence": _fixture_evidence(action.name),
        "runtime": {
            "binding": NATIVE_INTELLIGENCE_BINDING_ID,
            "actionBinding": action_binding,
            "executorMode": NATIVE_INTELLIGENCE_EXECUTOR,
            "mutates": action.mutates,
        },
        "runtimeContract": registered_manifest if registered_contract else None,
    }
    readiness = certify_native_intelligence_action(action, contract)
    return {**contract, "readiness": readiness}


def certify_native_intelligence_action(
    action: NativeIntelligenceAction,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Derive readiness; callers cannot promote a capability by setting status."""

    executor_registered = _readiness_predicate(
        action.name,
        "executor_registered",
        lambda: action.name in _ACTION_HANDLERS,
    )
    contract_complete = _readiness_predicate(
        action.name,
        "contract_complete",
        lambda: _contract_complete(action, contract),
    )
    fixture_evidence_registered = _readiness_predicate(
        action.name,
        "fixture_evidence_registered",
        lambda: _fixture_evidence_valid(
            contract.get("fixtureEvidence"),
            action.name,
        ),
    )
    gates_resolvable = _readiness_predicate(
        action.name,
        "gates_resolvable",
        lambda: _gates_resolvable(contract),
    )
    predicates = {
        "executor_registered": executor_registered,
        "contract_complete": contract_complete,
        "fixture_evidence_registered": fixture_evidence_registered,
        "gates_resolvable": gates_resolvable,
    }
    missing = [name for name, value in predicates.items() if not value]
    if missing:
        from backend.workflow.native_intelligence_metrics import record_readiness_blocked

        record_readiness_blocked(missing)
    return {
        "status": "runnable" if all(predicates.values()) else "blocked",
        "runnable": all(predicates.values()),
        "predicates": predicates,
        "missingReasons": missing,
    }


def _registered_contract_manifest(registered_contract: Any) -> dict[str, Any]:
    blocked_manifest = {
        "permissionGate": {"required": []},
        "configGate": {"required": []},
        "resourceGate": {"required": []},
        "eventShape": {"events": []},
        "provenance": {"required": False, "fields": []},
        "limits": {},
    }
    if registered_contract is None:
        return blocked_manifest
    try:
        manifest = registered_contract.to_manifest()
    except Exception:
        from backend.workflow.native_intelligence_metrics import record_rejected_contract

        record_rejected_contract("readiness_contract")
        return blocked_manifest
    required_sections = {
        "permissionGate",
        "configGate",
        "resourceGate",
        "eventShape",
        "provenance",
        "limits",
    }
    if not isinstance(manifest, dict) or not required_sections <= manifest.keys():
        from backend.workflow.native_intelligence_metrics import record_rejected_contract

        record_rejected_contract("readiness_contract")
        return blocked_manifest
    return manifest


def _readiness_predicate(
    action_name: str,
    predicate_name: str,
    predicate: Callable[[], bool],
) -> bool:
    try:
        return predicate() is True
    except Exception:
        logger.exception(
            "native intelligence readiness predicate failed",
            extra={
                "action": action_name,
                "predicate": predicate_name,
            },
        )
        return False


async def execute_native_intelligence_action(
    *,
    action_name: str,
    input_items: list[dict[str, Any]],
    params: dict[str, Any],
    session: AsyncSession,
    workflow_id: str,
    run_id: str,
    trace_id: str,
    node_id: str,
    conversation_origin: WorkflowConversationOrigin | None = None,
    commit_each_command: bool = True,
) -> dict[str, Any]:
    """Execute one registered action against the durable aggregate."""

    action = NATIVE_INTELLIGENCE_ACTION_BY_NAME.get(action_name)
    if action is None:
        raise ValueError("native_intelligence_action_not_registered")
    manifest = native_intelligence_action_manifest(action)
    readiness = manifest["readiness"]
    if not readiness["runnable"]:
        return {
            "schema": "native-intelligence.blocked.v1",
            "status": "blocked",
            "action": action_name,
            "blockedReasons": readiness["missingReasons"],
        }
    gate_failures = _resolve_runtime_gates(manifest, {"session": session})
    if gate_failures:
        return {
            "schema": "native-intelligence.blocked.v1",
            "status": "blocked",
            "action": action_name,
            "blockedReasons": gate_failures,
        }

    params = dict(params)
    session_ref = _intelligence_session_ref(input_items, params)
    session_id = _session_id(session_ref, workflow_id, run_id)
    run_context = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "trace_id": trace_id,
        "node_id": node_id,
    }
    if conversation_origin is not None:
        run_context["conversation_id"] = conversation_origin.conversation_id
    store = IntelligenceStore(
        session,
        run_context=run_context,
        commit_each_command=commit_each_command,
    )
    stages = NativeIntelligenceStages(store, worker_id=f"workflow:{run_id}")
    aggregate = await session.get(IntelligenceSession, session_id)
    if session_ref is not None:
        if aggregate is None:
            raise ValueError("intelligence_session_not_found")
        validated_refs = await _validate_artifact_refs(
            store,
            session_id,
            session_ref,
            current_version=aggregate.version,
            required_kinds=_REQUIRED_EXPLICIT_REF_ARTIFACT_KINDS.get(
                action_name,
                (),
            ),
        )
    elif aggregate is None:
        if action_name != "research":
            raise ValueError("intelligence_session_not_found")
        await store.create_session(
            session_id=session_id,
            idempotency_key=f"workflow:create:{run_id}",
            request={"workflowId": workflow_id, "traceId": trace_id},
            created_by_run_id=run_id,
        )
        validated_refs = {}
    else:
        validated_refs = {}
    params["_explicitIntelligenceSessionRef"] = session_ref is not None
    params["_validatedArtifactRefs"] = validated_refs
    result = await _ACTION_HANDLERS[action_name](
        stages, store, session_id, input_items, params
    )
    aggregate = await store.load_session(session_id)
    output_ref = _output_session_ref(
        session_id,
        aggregate.version,
        session_ref,
        result,
    )
    return {
        "schema": "native-intelligence.action-result.v1",
        "status": "completed",
        "action": action_name,
        "sessionId": session_id,
        "state": aggregate.state.value,
        "version": aggregate.version,
        "intelligenceSessionRef": output_ref,
        "result": result,
        "readiness": readiness,
        "provenance": {
            "source": "opencli-admin-native",
            "offline": True,
            "credentialFree": True,
            "workflowId": workflow_id,
            "runId": run_id,
            "traceId": trace_id,
            "nodeId": node_id,
        },
    }


async def _research(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.research(
        session_id=session_id,
        expected_version=aggregate.version,
        input_items=_evidence_items(items, params),
        params={
            key: value
            for key, value in params.items()
            if key not in {"action", "sessionId"} and not key.startswith("_")
        },
        seed=_int_param(params, "seed", 0),
    )
    return _result_artifacts(result, [artifact])


async def _ontology(stages, store, session_id, items, params):
    research = await _latest(
        store, session_id, ArtifactKind.RESEARCH, ResearchArtifact, params
    )
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.build_ontology(
        session_id=session_id,
        expected_version=aggregate.version,
        research=research,
        seed=_int_param(params, "seed", 0),
    )
    return _result_artifacts(result, [artifact])


async def _graph(stages, store, session_id, items, params):
    research = await _latest(
        store, session_id, ArtifactKind.RESEARCH, ResearchArtifact, params
    )
    ontology = await _latest(
        store, session_id, ArtifactKind.ONTOLOGY, OntologyArtifact, params
    )
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.build_graph(
        session_id=session_id,
        expected_version=aggregate.version,
        research=research,
        ontology=ontology,
        seed=_int_param(params, "seed", 0),
    )
    return _result_artifacts(result, [artifact])


async def _personas(stages, store, session_id, items, params):
    research = await _latest(
        store, session_id, ArtifactKind.RESEARCH, ResearchArtifact, params
    )
    ontology = await _latest(
        store, session_id, ArtifactKind.ONTOLOGY, OntologyArtifact, params
    )
    graph = await _latest(store, session_id, ArtifactKind.GRAPH, GraphArtifact, params)
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.prepare(
        session_id=session_id,
        expected_version=aggregate.version,
        research=research,
        ontology=ontology,
        graph=graph,
        persona_count=_int_param(params, "personaCount", 5),
        seed=_int_param(params, "seed", 0),
    )
    return _result_artifacts(result, [artifact])


async def _simulation_prepare(stages, store, session_id, items, params):
    personas = await _latest(
        store, session_id, ArtifactKind.PERSONA, PersonaArtifact, params
    )
    return {
        "sessionId": session_id,
        "personaArtifactId": personas.artifact_id,
        "seed": _int_param(params, "seed", 0),
        "agentCount": _int_param(params, "agentCount", len(personas.payload.get("personas", []))),
        "maxRounds": _int_param(params, "maxRounds", 8),
        "platforms": _string_list(params.get("platforms")) or ["twitter", "reddit"],
        "simulated": True,
    }


async def _simulation_start(stages, store, session_id, items, params):
    personas = await _latest(
        store, session_id, ArtifactKind.PERSONA, PersonaArtifact, params
    )
    aggregate = await store.load_session(session_id)
    result = await stages.start_simulation(
        session_id=session_id,
        expected_version=aggregate.version,
        personas=personas,
        seed=_int_param(params, "seed", 0),
        agent_count=_optional_int(params.get("agentCount")),
        max_rounds=_int_param(params, "maxRounds", 8),
        platforms=_string_list(params.get("platforms")) or None,
        requirement=_string_param(params, "requirement", "Explore evidence-grounded reactions."),
    )
    return _result_artifacts(result, [])


async def _simulation_step(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.step_simulation(
        session_id=session_id, expected_version=aggregate.version
    )
    return _result_artifacts(result, [artifact] if artifact else [])


async def _simulation_run(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.run_simulation(
        session_id=session_id, expected_version=aggregate.version
    )
    return _result_artifacts(result, [artifact])


async def _simulation_stop(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    return _result_artifacts(
        await stages.stop_simulation(session_id=session_id, expected_version=aggregate.version),
        [],
    )


async def _simulation_resume(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    return _result_artifacts(
        await stages.resume_simulation(session_id=session_id, expected_version=aggregate.version),
        [],
    )


async def _simulation_status(stages, store, session_id, items, params):
    return await stages.simulation_status(session_id=session_id)


async def _simulation_actions(stages, store, session_id, items, params):
    return await stages.simulation_actions(
        session_id=session_id,
        offset=_int_param(params, "offset", 0),
        limit=_int_param(params, "limit", 100),
    )


async def _simulation_timeline(stages, store, session_id, items, params):
    return await stages.simulation_timeline(
        session_id=session_id,
        offset=_int_param(params, "offset", 0),
        limit=_int_param(params, "limit", 100),
    )


async def _simulation_stats(stages, store, session_id, items, params):
    return await stages.simulation_stats(session_id=session_id)


async def _interviews_start(stages, store, session_id, items, params, mode):
    personas = await _latest(
        store, session_id, ArtifactKind.PERSONA, PersonaArtifact, params
    )
    simulation = await _latest(
        store, session_id, ArtifactKind.SIMULATION, SimulationArtifact, params
    )
    aggregate = await store.load_session(session_id)
    kwargs = {
        "session_id": session_id,
        "expected_version": aggregate.version,
        "persona_artifact_id": personas.artifact_id,
        "simulation_artifact_id": simulation.artifact_id,
        "question": _string_param(params, "question", "How do you interpret the outcome?"),
        "seed": _int_param(params, "seed", 0),
    }
    if mode == "one":
        result = await stages.interview_one(
            persona_id=_string_param(params, "personaId", ""), **kwargs
        )
    elif mode == "batch":
        result = await stages.interview_batch(
            persona_ids=_string_list(params.get("personaIds")), **kwargs
        )
    else:
        result = await stages.interview_all(**kwargs)
    return _result_artifacts(result, [])


async def _interviews_step(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.step_interviews(
        session_id=session_id, expected_version=aggregate.version
    )
    return _result_artifacts(result, [artifact] if artifact else [])


async def _interviews_run(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifacts = await stages.run_interviews(
        session_id=session_id, expected_version=aggregate.version
    )
    return _result_artifacts(result, artifacts)


async def _interviews_history(stages, store, session_id, items, params):
    artifacts = await stages.interview_history(
        session_id=session_id,
        offset=_int_param(params, "offset", 0),
        limit=_int_param(params, "limit", 20),
    )
    return [_artifact_summary(item) for item in artifacts]


async def _report_start(stages, store, session_id, items, params):
    personas = await _latest(
        store, session_id, ArtifactKind.PERSONA, PersonaArtifact, params
    )
    simulation = await _latest(
        store, session_id, ArtifactKind.SIMULATION, SimulationArtifact, params
    )
    validated_refs = params.get("_validatedArtifactRefs", {})
    if params.get("_explicitIntelligenceSessionRef"):
        interviews = validated_refs.get(ArtifactKind.INTERVIEW, [])
        if not interviews:
            raise ValueError("interview_artifact_missing")
    else:
        interviews = await store.load_artifacts(
            session_id, ArtifactKind.INTERVIEW, limit=100
        )
    aggregate = await store.load_session(session_id)
    result = await stages.start_report(
        session_id=session_id,
        expected_version=aggregate.version,
        persona_artifact_id=personas.artifact_id,
        simulation_artifact_id=simulation.artifact_id,
        interview_artifact_ids=[item.artifact_id for item in interviews],
        section_plan=_string_list(params.get("sectionPlan")) or None,
        seed=_int_param(params, "seed", 0),
    )
    return _result_artifacts(result, [])


async def _report_step(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.step_report(
        session_id=session_id, expected_version=aggregate.version
    )
    return _result_artifacts(result, [artifact] if artifact else [])


async def _report_run(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.run_report(
        session_id=session_id, expected_version=aggregate.version
    )
    return _result_artifacts(result, [artifact])


async def _report_progress(stages, store, session_id, items, params):
    return await stages.report_progress(session_id=session_id)


async def _report_read(stages, store, session_id, items, params):
    if params.get("_explicitIntelligenceSessionRef"):
        reports = params["_validatedArtifactRefs"].get(ArtifactKind.REPORT, [])
        artifact_id = (
            params.get("artifactId")
            if isinstance(params.get("artifactId"), str)
            else None
        )
        artifact = next(
            (
                report
                for report in reversed(reports)
                if artifact_id is None or report.artifact_id == artifact_id
            ),
            None,
        )
        if not isinstance(artifact, ReportArtifact):
            raise ValueError("report_artifact_missing")
    else:
        artifact = await stages.read_report(
            session_id=session_id,
            artifact_id=(
                params.get("artifactId")
                if isinstance(params.get("artifactId"), str)
                else None
            ),
        )
    return _artifact_summary(artifact, include_payload=True)


async def _report_ask(stages, store, session_id, items, params):
    report = await _latest(store, session_id, ArtifactKind.REPORT, ReportArtifact, params)
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.ask_report(
        session_id=session_id,
        expected_version=aggregate.version,
        report_artifact_id=report.artifact_id,
        question=_string_param(params, "question", "What is the most likely outcome?"),
        seed=_int_param(params, "seed", 0),
    )
    value = _result_artifacts(result, [])
    value["artifacts"] = [_artifact_summary(artifact, include_payload=True)]
    return value


async def _report_answers(stages, store, session_id, items, params):
    artifacts = await stages.report_answers(
        session_id=session_id,
        offset=_int_param(params, "offset", 0),
        limit=_int_param(params, "limit", 20),
    )
    return [_artifact_summary(item, include_payload=True) for item in artifacts]


async def _cancel(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    state = aggregate.state.value
    if state in {"running", "stopped"}:
        result = await stages.cancel_simulation(
            session_id=session_id, expected_version=aggregate.version
        )
    elif state == "interviewing":
        result = await stages.cancel_interviews(
            session_id=session_id, expected_version=aggregate.version
        )
    elif state == "reporting":
        result = await stages.cancel_report(
            session_id=session_id, expected_version=aggregate.version
        )
    else:
        from backend.workflow.native_intelligence_contracts import (
            IntelligenceCommand,
            IntelligenceCommandName,
            canonical_hash,
        )

        request = {"state": state}
        result = await store.apply(
            IntelligenceCommand(
                command=IntelligenceCommandName.CANCEL,
                session_id=session_id,
                expected_version=aggregate.version,
                idempotency_key=f"workflow:cancel:{canonical_hash(request)}",
                request=request,
            )
        )
    return _result_artifacts(result, [])


async def _close(stages, store, session_id, items, params):
    aggregate = await store.load_session(session_id)
    result, artifact = await stages.close(
        session_id=session_id,
        expected_version=aggregate.version,
        seed=_int_param(params, "seed", 0),
    )
    return _result_artifacts(result, [artifact])


Handler = Callable[
    [NativeIntelligenceStages, IntelligenceStore, str, list[dict[str, Any]], dict[str, Any]],
    Awaitable[Any],
]

_ACTION_HANDLERS: dict[str, Handler] = {
    "research": _research,
    "ontology": _ontology,
    "graph": _graph,
    "personas": _personas,
    "simulation.prepare": _simulation_prepare,
    "simulation.start": _simulation_start,
    "simulation.step": _simulation_step,
    "simulation.run": _simulation_run,
    "simulation.stop": _simulation_stop,
    "simulation.resume": _simulation_resume,
    "simulation.status": _simulation_status,
    "simulation.actions": _simulation_actions,
    "simulation.timeline": _simulation_timeline,
    "simulation.stats": _simulation_stats,
    "interviews.one": lambda *args: _interviews_start(*args, "one"),
    "interviews.batch": lambda *args: _interviews_start(*args, "batch"),
    "interviews.all": lambda *args: _interviews_start(*args, "all"),
    "interviews.step": _interviews_step,
    "interviews.run": _interviews_run,
    "interviews.history": _interviews_history,
    "report.start": _report_start,
    "report.step": _report_step,
    "report.run": _report_run,
    "report.progress": _report_progress,
    "report.read": _report_read,
    "report.ask": _report_ask,
    "report.answers": _report_answers,
    "cancel": _cancel,
    "close": _close,
}


async def _latest(store, session_id, kind, expected_type, params):
    validated_refs = params.get("_validatedArtifactRefs", {})
    if params.get("_explicitIntelligenceSessionRef"):
        artifacts = validated_refs.get(kind, [])
        artifact = artifacts[-1] if artifacts else None
        if not isinstance(artifact, expected_type):
            raise ValueError(f"{kind.value}_artifact_missing")
        return artifact
    artifact = await store.load_latest_artifact(session_id, kind)
    if not isinstance(artifact, expected_type):
        raise ValueError(f"{kind.value}_artifact_missing")
    return artifact


def _result_artifacts(
    result: IntelligenceCommandResult, artifacts: list[Any]
) -> dict[str, Any]:
    return {
        "sessionId": result.session_id,
        "state": result.state.value,
        "version": result.version,
        "transitionEventId": result.transition_event_id,
        "artifactIds": list(result.artifact_ids),
        "artifacts": [_artifact_summary(item) for item in artifacts],
        "idempotentReplay": result.idempotent_replay,
        "noOp": result.no_op,
    }


def _artifact_summary(artifact: Any, *, include_payload: bool = False) -> dict[str, Any]:
    value = {
        "artifactId": artifact.artifact_id,
        "kind": artifact.kind.value,
        "schemaVersion": artifact.schema_version,
        "groundingArtifactIds": artifact.grounding_artifact_ids,
        "simulated": artifact.simulated,
        "algorithmVersion": artifact.algorithm_version,
        "seed": artifact.seed,
        "provenance": artifact.provenance.model_dump(mode="json"),
        "contentHash": canonical_hash(artifact.model_dump(mode="json")),
    }
    if include_payload:
        value["payload"] = artifact.payload
    return value


def _evidence_items(
    items: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    evidence = []
    for item in items:
        raw = item.get("normalizedData") or item.get("raw") or item
        if isinstance(raw, dict):
            record = dict(raw)
            lineage = item.get("lineage")
            if isinstance(lineage, list):
                record["workflowLineage"] = [
                    dict(entry) for entry in lineage if isinstance(entry, dict)
                ]
            evidence.append(record)
    if evidence:
        return evidence
    if params.get("sourceMode") != "offline_fixture":
        raise ValueError("research_input_required")
    fixture_id = params.get("fixtureId")
    fixture_entry = (
        NATIVE_INTELLIGENCE_FIXTURES.get(fixture_id)
        if isinstance(fixture_id, str)
        else None
    )
    if fixture_entry is None:
        raise ValueError("research_input_required")
    fixture = json.loads(fixture_entry["path"].read_text(encoding="utf-8"))
    return [dict(item) for item in fixture["evidence"]]


def _intelligence_session_ref(
    items: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any] | None:
    if "intelligenceSessionRef" in params:
        authored = params["intelligenceSessionRef"]
        if not isinstance(authored, dict):
            raise ValueError("intelligence_session_ref_invalid")
        return authored
    for item in reversed(items):
        raw = item.get("raw") if isinstance(item, dict) else None
        if not isinstance(raw, dict):
            continue
        if "intelligenceSessionRef" not in raw:
            continue
        value = raw["intelligenceSessionRef"]
        if not isinstance(value, dict):
            raise ValueError("intelligence_session_ref_invalid")
        return value
    return None


def _session_id(
    value: dict[str, Any] | None,
    workflow_id: str,
    run_id: str,
) -> str:
    if value is not None:
        raw_session_id = value.get("sessionId")
        if not isinstance(raw_session_id, str) or not raw_session_id:
            raise ValueError("intelligence_session_ref_invalid")
        try:
            return str(uuid.UUID(raw_session_id))
        except ValueError as exc:
            raise ValueError("intelligence_session_ref_invalid") from exc
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"opencli-admin/{workflow_id}/{run_id}"))


async def _validate_artifact_refs(
    store: IntelligenceStore,
    session_id: str,
    session_ref: dict[str, Any] | None,
    *,
    current_version: int,
    required_kinds: tuple[ArtifactKind, ...],
) -> dict[ArtifactKind, list[Any]]:
    if session_ref is None:
        return {}
    if session_ref.get("schema") != "intelligence-session-ref.v1":
        raise ValueError("intelligence_session_ref_invalid")
    version = session_ref.get("version")
    if (
        not isinstance(version, int) or isinstance(version, bool) or version < 0
    ):
        raise ValueError("intelligence_session_ref_invalid")
    if version != current_version:
        raise IntelligenceConflictError("intelligence_version_conflict")
    raw_refs = session_ref.get("artifactRefs")
    if not isinstance(raw_refs, list):
        raise ValueError("intelligence_artifact_ref_invalid")
    if len(raw_refs) > MAX_EXPLICIT_ARTIFACT_REFS:
        raise ValueError("intelligence_artifact_ref_invalid")
    parsed_refs: list[tuple[str, ArtifactKind, str]] = []
    seen_refs: set[tuple[str, ArtifactKind]] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, dict):
            raise ValueError("intelligence_artifact_ref_invalid")
        artifact_id = raw_ref.get("artifactId")
        kind_value = raw_ref.get("kind")
        content_hash = raw_ref.get("contentHash")
        if not all(
            isinstance(value, str) and value
            for value in (artifact_id, kind_value, content_hash)
        ):
            raise ValueError("intelligence_artifact_ref_invalid")
        try:
            kind = ArtifactKind(kind_value)
        except ValueError as exc:
            raise ValueError("intelligence_artifact_ref_kind_mismatch") from exc
        ref_key = (artifact_id, kind)
        if ref_key in seen_refs:
            raise ValueError("intelligence_artifact_ref_invalid")
        seen_refs.add(ref_key)
        parsed_refs.append((artifact_id, kind, content_hash))
    if (
        sum(kind is ArtifactKind.INTERVIEW for _, kind, _ in parsed_refs)
        > MAX_INTERVIEW_ARTIFACT_REFS
    ):
        raise ValueError("intelligence_artifact_ref_invalid")

    artifacts_by_id = await store.load_artifacts_by_ids(
        session_id,
        [artifact_id for artifact_id, _, _ in parsed_refs],
    )
    validated: dict[ArtifactKind, list[Any]] = {}
    for artifact_id, kind, content_hash in parsed_refs:
        artifact = artifacts_by_id.get(artifact_id)
        if artifact is None:
            raise IntelligenceReferenceError("artifact is not in this session")
        if artifact.kind != kind:
            raise ValueError("intelligence_artifact_ref_kind_mismatch")
        if canonical_hash(artifact.model_dump(mode="json")) != content_hash:
            raise ValueError("intelligence_artifact_ref_hash_mismatch")
        validated.setdefault(kind, []).append(artifact)
    for kind in required_kinds:
        if not validated.get(kind):
            raise ValueError(f"{kind.value}_artifact_missing")
    return validated


def _output_session_ref(
    session_id: str,
    version: int,
    input_ref: dict[str, Any] | None,
    result: Any,
) -> dict[str, Any]:
    ordered_refs: list[dict[str, Any]] = [
        dict(ref)
        for ref in (input_ref or {}).get("artifactRefs", [])
        if isinstance(ref, dict) and isinstance(ref.get("artifactId"), str)
    ]
    for artifact in _artifact_summaries(result):
        artifact_id = artifact.get("artifactId")
        if isinstance(artifact_id, str):
            ordered_refs.append(
                {
                    key: artifact[key]
                    for key in ("artifactId", "kind", "contentHash")
                    if key in artifact
                }
            )
    return {
        "schema": "intelligence-session-ref.v1",
        "sessionId": session_id,
        "version": version,
        "artifactRefs": _compact_artifact_refs(ordered_refs),
    }


def _compact_artifact_refs(
    refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the newest singleton per kind and the newest 50 interviews.

    Repeated artifact IDs move to their newest position so artifacts emitted by
    the current command always win over an older copy carried in the input.
    """

    refs_by_id: dict[str, dict[str, Any]] = {}
    for ref in refs:
        artifact_id = ref.get("artifactId")
        if not isinstance(artifact_id, str):
            continue
        refs_by_id.pop(artifact_id, None)
        refs_by_id[artifact_id] = ref

    ordered = list(refs_by_id.values())
    positions_by_kind: dict[str, list[int]] = {}
    for index, ref in enumerate(ordered):
        kind = ref.get("kind")
        if isinstance(kind, str):
            positions_by_kind.setdefault(kind, []).append(index)

    selected_positions: set[int] = set()
    for kind, positions in positions_by_kind.items():
        keep = (
            MAX_INTERVIEW_ARTIFACT_REFS
            if kind == ArtifactKind.INTERVIEW.value
            else 1
        )
        selected_positions.update(positions[-keep:])
    compacted = [
        ref for index, ref in enumerate(ordered) if index in selected_positions
    ]
    return compacted[-MAX_EXPLICIT_ARTIFACT_REFS:]


def _artifact_summaries(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            item
            for nested in value
            for item in _artifact_summaries(nested)
        ]
    if not isinstance(value, dict):
        return []
    summaries = []
    if {
        "artifactId",
        "kind",
        "contentHash",
    } <= value.keys():
        summaries.append(value)
    for nested in value.values():
        summaries.extend(_artifact_summaries(nested))
    return summaries


def _invalid_fixture_evidence() -> dict[str, Any]:
    return {
        "id": NATIVE_INTELLIGENCE_FIXTURE_ID,
        "path": "backend/workflow/fixtures/native_intelligence_offline.json",
        "inputKey": "evidence",
        "expectedArtifactKinds": [],
        "expectedActionTranscript": [],
        "scenario": None,
        "transcriptHash": _FIXTURE_SHA256,
    }


def _fixture_evidence(action_name: str) -> dict[str, Any]:
    if not _FIXTURE_PATH.is_file():
        return _invalid_fixture_evidence()
    try:
        fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        expected = fixture["expected"]
        expected_artifact_kinds = expected["artifactKinds"]
        expected_action_transcript = expected["actionTranscript"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return _invalid_fixture_evidence()
    scenarios = expected.get("actionScenarios", {})
    scenario = scenarios.get(action_name) if isinstance(scenarios, dict) else None
    return {
        "id": NATIVE_INTELLIGENCE_FIXTURE_ID,
        "path": "backend/workflow/fixtures/native_intelligence_offline.json",
        "inputKey": "evidence",
        "expectedArtifactKinds": expected_artifact_kinds,
        "expectedActionTranscript": expected_action_transcript,
        "scenario": scenario,
        "transcriptHash": _FIXTURE_SHA256,
    }


def _fixture_evidence_valid(value: Any, action_name: str) -> bool:
    if not isinstance(value, dict) or value.get("id") != NATIVE_INTELLIGENCE_FIXTURE_ID:
        return False
    if not _FIXTURE_PATH.is_file():
        return False
    registered = NATIVE_INTELLIGENCE_FIXTURES.get(value.get("id"))
    if registered is None:
        return False
    try:
        fixture = json.loads(registered["path"].read_text(encoding="utf-8"))
        expected_scenario = fixture["expected"].get("actionScenarios", {}).get(
            action_name
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return False
    return (
        value.get("transcriptHash") == registered["sha256"]
        and hashlib.sha256(registered["path"].read_bytes()).hexdigest()
        == registered["sha256"]
        and bool(value.get("expectedArtifactKinds"))
        and bool(value.get("expectedActionTranscript"))
        and isinstance(value.get("scenario"), dict)
        and value["scenario"] == expected_scenario
        and value["scenario"].get("action") == action_name
    )


def _contract_complete(
    action: NativeIntelligenceAction,
    contract: dict[str, Any],
) -> bool:
    runtime = contract.get("runtime")
    if not isinstance(runtime, dict):
        return False
    expected_binding = f"workflow.native-intelligence.{action.name.replace('.', '-')}"
    action_contract = runtime_io_contract(expected_binding)
    if action_contract is None or action_contract.status != "executable":
        return False
    manifest = action_contract.to_manifest()
    return (
        contract.get("action") == action.name
        and runtime.get("binding") == NATIVE_INTELLIGENCE_BINDING_ID
        and runtime.get("actionBinding") == expected_binding
        and runtime.get("executorMode") == NATIVE_INTELLIGENCE_EXECUTOR
        and runtime.get("mutates") is action.mutates
        and manifest == contract.get("runtimeContract")
        and manifest["inputShape"]["ports"]
        == [
            {
                "name": "in",
                "type": (
                    "storedItems[]"
                    if action.name == "research"
                    else "intelligenceSessionEnvelope"
                ),
            }
        ]
        and manifest["outputShape"]["ports"]
        == [{"name": "out", "type": "intelligenceSessionEnvelope"}]
        and contract.get("input", {}).get("type") == action.input_type
        and contract.get("output", {}).get("type") == action.output_type
        and {item["code"] for item in manifest["errors"]}
        == {item["code"] for item in contract["errors"]}
        and contract.get("permissionGate") == manifest["permissionGate"]
        and contract.get("configGate") == manifest["configGate"]
        and contract.get("resourceGate") == manifest["resourceGate"]
        and contract.get("eventShape") == manifest["eventShape"]
        and contract.get("provenance") == manifest["provenance"]
        and contract.get("limits") == manifest["limits"]
        and bool(manifest["eventShape"]["events"])
        and bool(manifest["provenance"]["fields"])
        and bool(manifest["limits"])
    )


def _gates_resolvable(contract: dict[str, Any]) -> bool:
    groups = (
        ("permissionGate", _PERMISSION_GATE_RESOLVERS),
        ("configGate", _CONFIG_GATE_RESOLVERS),
        ("resourceGate", _RESOURCE_GATE_RESOLVERS),
    )
    for key, registry in groups:
        for name in contract.get(key, {}).get("required", []):
            entry = registry.get(name)
            if key == "resourceGate":
                if (
                    not isinstance(entry, dict)
                    or not callable(entry.get("resolver"))
                    or not isinstance(entry.get("blockReason"), str)
                    or not entry["blockReason"]
                ):
                    return False
            elif entry is None:
                return False
    return True


def _resolve_runtime_gates(
    contract: dict[str, Any], context: dict[str, Any]
) -> list[str]:
    blocked = []
    for name in contract.get("resourceGate", {}).get("required", []):
        entry = _RESOURCE_GATE_RESOLVERS.get(name)
        if (
            not isinstance(entry, dict)
            or not callable(entry.get("resolver"))
            or not entry["resolver"](context)
        ):
            blocked.append(
                entry.get("blockReason", "native_intelligence_resource_unresolved")
                if isinstance(entry, dict)
                else "native_intelligence_resource_unresolved"
            )
    return blocked


def _int_param(params: dict[str, Any], key: str, default: int) -> int:
    value = params.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string_param(params: dict[str, Any], key: str, default: str) -> str:
    value = params.get(key)
    return value if isinstance(value, str) and value else default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


__all__ = [
    "NATIVE_INTELLIGENCE_ACTIONS",
    "NATIVE_INTELLIGENCE_ACTION_BY_NAME",
    "NATIVE_INTELLIGENCE_ACTION_BY_TOOL_ID",
    "NATIVE_INTELLIGENCE_EXECUTOR",
    "NATIVE_INTELLIGENCE_LIFECYCLE_ACTIONS",
    "certify_native_intelligence_action",
    "execute_native_intelligence_action",
    "native_intelligence_action_manifest",
]
