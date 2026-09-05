"""Resolve OpenCLI runtime resources from existing catalog and fleet metadata."""


import uuid

from backend.schemas.workflow import (
    CompiledWorkflowNode,
    WorkflowFleetCapabilityMatchResponse,
    WorkflowOpenCLIHDATraceDispatch,
)
from backend.schemas.workflow_runtime import (
    WorkflowRunBlockReason,
    WorkflowRuntimeResourceRequirement,
    WorkflowRuntimeResourceResolution,
)
from backend.workflow.block_reasons import (
    MISSING_ADAPTER_RESOURCE,
    MISSING_OPENCLI_COMMAND,
    MISSING_PROFILE_BINDING,
    MISSING_SESSION_SNAPSHOT,
    MISSING_WORKER_CAPACITY,
    PROFILE_LOCK_CONTENDED,
)
from backend.workflow.opencli_adapter_nodes import resolve_opencli_adapter_node


def resolve_runtime_resources(
    dispatch: WorkflowOpenCLIHDATraceDispatch,
    node: CompiledWorkflowNode,
    match: WorkflowFleetCapabilityMatchResponse | None,
) -> tuple[WorkflowRuntimeResourceRequirement, WorkflowRuntimeResourceResolution]:
    adapter_node_id = _read_string(node.params.get("opencliAdapterNodeId"))
    adapter_node = resolve_opencli_adapter_node(adapter_node_id) if adapter_node_id else None
    mutation_mode = "write" if adapter_node and adapter_node.access == "write" else "read"
    requested_capability = f"opencli.{dispatch.site}.{dispatch.command or 'unresolved'}"
    requirement = WorkflowRuntimeResourceRequirement(
        nodeId=dispatch.nodeId,
        sourceGroup=dispatch.sourceGroup,
        site=dispatch.site,
        mutationMode=mutation_mode,
        requestedCapability=requested_capability,
        adapterNodeId=adapter_node_id,
    )

    if not dispatch.command:
        return requirement, _blocked(
            MISSING_OPENCLI_COMMAND,
            "OpenCLI command could not be resolved from runtime metadata.",
            requirement,
        )

    if adapter_node_id and adapter_node is None:
        return requirement, _blocked(
            MISSING_ADAPTER_RESOURCE,
            f'OpenCLI adapter capability "{adapter_node_id}" is not registered.',
            requirement,
        )

    if match is None:
        return requirement, WorkflowRuntimeResourceResolution(
            status="resolved",
            adapterNodeId=adapter_node_id or (node.adapter.id if node.adapter else None),
            command=dispatch.command,
            workerSlotId="iii:collector-opencli",
            concurrencyLimit=1,
        )

    if (
        not adapter_node_id
        and node.adapter is not None
        and node.adapter.provider == "opencli"
        and not match.matched
    ):
        return requirement, WorkflowRuntimeResourceResolution(
            status="resolved",
            adapterNodeId=node.adapter.id,
            command=dispatch.command,
            workerSlotId="iii:collector-opencli",
            concurrencyLimit=1,
        )

    if not match.matched or match.selected is None:
        code = _block_code(match.missing, mutation_mode=mutation_mode)
        return requirement, _blocked(
            code,
            _block_message(code, dispatch.site),
            requirement,
            missing=match.missing,
        )

    selected = match.selected
    profile_binding_id = None
    session_snapshot_id = None
    lock_id = None
    if match.requiresSiteBinding:
        profile_binding_id = _resource_id(
            "profile-binding",
            dispatch.site,
            selected.endpoint,
        )
        if mutation_mode == "write":
            lock_id = _resource_id("profile-lock", dispatch.site, selected.endpoint)
        else:
            session_snapshot_id = _resource_id(
                "session-snapshot",
                dispatch.site,
                selected.endpoint,
            )

    return requirement, WorkflowRuntimeResourceResolution(
        status="resolved",
        adapterNodeId=match.adapterNodeId or adapter_node_id,
        command=match.command or dispatch.command,
        workerSlotId=selected.endpoint,
        profileBindingId=profile_binding_id,
        sessionSnapshotId=session_snapshot_id,
        lockId=lock_id,
        concurrencyLimit=1,
    )


def _blocked(
    code: str,
    message: str,
    requirement: WorkflowRuntimeResourceRequirement,
    *,
    missing: list[str] | None = None,
) -> WorkflowRuntimeResourceResolution:
    return WorkflowRuntimeResourceResolution(
        status="blocked",
        adapterNodeId=requirement.adapterNodeId,
        blockReason=WorkflowRunBlockReason(
            code=code,
            message=message,
            source="workflow_runtime_resources",
            details={
                "nodeId": requirement.nodeId,
                "sourceGroup": requirement.sourceGroup,
                "site": requirement.site,
                "mutationMode": requirement.mutationMode,
                "requestedCapability": requirement.requestedCapability,
                "adapterNodeId": requirement.adapterNodeId,
                "missing": missing or [],
            },
        ),
    )


def _block_code(missing: list[str], *, mutation_mode: str) -> str:
    if "opencli_adapter_node" in missing:
        return MISSING_ADAPTER_RESOURCE
    if any(value.startswith("site_binding:") for value in missing):
        return MISSING_PROFILE_BINDING
    if "profile_lock" in missing and mutation_mode == "write":
        return PROFILE_LOCK_CONTENDED
    if "session_snapshot" in missing:
        return MISSING_SESSION_SNAPSHOT
    return MISSING_WORKER_CAPACITY


def _block_message(code: str, site: str) -> str:
    if code == MISSING_ADAPTER_RESOURCE:
        return "OpenCLI adapter capability is unavailable in the registered catalog."
    if code == MISSING_PROFILE_BINDING:
        return f'No browser profile/site binding is available for "{site}".'
    if code == MISSING_SESSION_SNAPSHOT:
        return f'No shareable browser session snapshot is available for "{site}".'
    if code == PROFILE_LOCK_CONTENDED:
        return f'The browser profile lock for "{site}" is already held.'
    return "No connected worker slot currently has capacity for this OpenCLI task."


def _resource_id(kind: str, site: str, endpoint: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"opencli-admin/runtime-resource/{kind}/{site}/{endpoint}",
        )
    )


def _read_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["resolve_runtime_resources"]
