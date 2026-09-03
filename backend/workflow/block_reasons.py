"""Stable workflow-run block reason taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BlockReasonCategory = Literal[
    "missing_config",
    "missing_source_credential",
    "missing_runtime_resource",
    "missing_permission",
    "missing_runtime_binding",
]

FETCH_PERMISSION_REQUIRED = "fetch_permission_required"
OPENCLI_WRITE_APPROVAL_REQUIRED = "opencli_write_approval_required"
OPENCLI_WRITE_PERMISSION_REQUIRED = "opencli_write_permission_required"
SEND_PERMISSION_REQUIRED = "send_permission_required"
MISSING_DELIVERY_PROJECTION = "missing_delivery_projection"
MISSING_FEISHU_CONNECTION = "missing_feishu_connection"
INVALID_FEISHU_RECORD_INPUT = "invalid_feishu_record_input"
FEISHU_WRITE_PERMISSION_REQUIRED = "feishu_write_permission_required"
MISSING_ADAPTER_RESOURCE = "missing_adapter_resource"
MISSING_OPENCLI_COMMAND = "missing_opencli_command"
MISSING_PROFILE_BINDING = "missing_profile_binding"
MISSING_RUNTIME_BINDING = "missing_runtime_binding"
MISSING_RUNTIME_IO_CONTRACT = "missing_runtime_io_contract"
MISSING_RUNTIME_PARAMETER = "missing_runtime_parameter"
MISSING_SOURCE_CREDENTIAL = "missing_source_credential"
MISSING_SESSION_SNAPSHOT = "missing_session_snapshot"
MISSING_TOOL_CAPABILITY_BINDING = "missing_tool_capability_binding"
MISSING_TURBOPUSH_CONTENT_TYPE = "missing_turbopush_content_type"
MISSING_TURBOPUSH_SERVICE = "missing_turbopush_service"
SOURCE_OUTPUT_REQUIRED = "source_output_required"
MISSING_WORKER_CAPACITY = "missing_worker_capacity"
PROFILE_LOCK_CONTENDED = "profile_lock_contended"


@dataclass(frozen=True)
class WorkflowBlockReasonDefinition:
    code: str
    category: BlockReasonCategory
    stable_fields: tuple[str, ...]
    volatile_fields: tuple[str, ...] = ()
    description: str = ""


WORKFLOW_BLOCK_REASON_TAXONOMY: dict[str, WorkflowBlockReasonDefinition] = {
    MISSING_ADAPTER_RESOURCE: WorkflowBlockReasonDefinition(
        code=MISSING_ADAPTER_RESOURCE,
        category="missing_runtime_resource",
        stable_fields=("code", "source", "details.adapterNodeId"),
        volatile_fields=("message",),
        description="OpenCLI adapter capability is not present in the registered catalog.",
    ),
    MISSING_OPENCLI_COMMAND: WorkflowBlockReasonDefinition(
        code=MISSING_OPENCLI_COMMAND,
        category="missing_runtime_resource",
        stable_fields=("code", "source", "details.site"),
        volatile_fields=("message",),
        description="The adapter catalog cannot resolve an OpenCLI command.",
    ),
    MISSING_PROFILE_BINDING: WorkflowBlockReasonDefinition(
        code=MISSING_PROFILE_BINDING,
        category="missing_runtime_resource",
        stable_fields=("code", "source", "details.site"),
        volatile_fields=("message",),
        description="Browser execution requires a site/profile binding that is unavailable.",
    ),
    MISSING_SESSION_SNAPSHOT: WorkflowBlockReasonDefinition(
        code=MISSING_SESSION_SNAPSHOT,
        category="missing_runtime_resource",
        stable_fields=("code", "source", "details.site"),
        volatile_fields=("message",),
        description="Read-only browser fanout requires a shareable session snapshot.",
    ),
    MISSING_WORKER_CAPACITY: WorkflowBlockReasonDefinition(
        code=MISSING_WORKER_CAPACITY,
        category="missing_runtime_resource",
        stable_fields=("code", "source", "details.requestedCapability"),
        volatile_fields=("message",),
        description="No connected worker slot currently satisfies the runtime requirement.",
    ),
    PROFILE_LOCK_CONTENDED: WorkflowBlockReasonDefinition(
        code=PROFILE_LOCK_CONTENDED,
        category="missing_runtime_resource",
        stable_fields=("code", "source", "details.site"),
        volatile_fields=("message",),
        description="A mutating browser task cannot acquire the exclusive profile lock.",
    ),
    FETCH_PERMISSION_REQUIRED: WorkflowBlockReasonDefinition(
        code=FETCH_PERMISSION_REQUIRED,
        category="missing_permission",
        stable_fields=("code", "source", "details.bindingId", "details.requiredPermission"),
        description="Source fetch is blocked because canFetchNetwork is false.",
    ),
    OPENCLI_WRITE_APPROVAL_REQUIRED: WorkflowBlockReasonDefinition(
        code=OPENCLI_WRITE_APPROVAL_REQUIRED,
        category="missing_permission",
        stable_fields=("code", "source", "details.bindingId", "details.proposalState"),
        description="OpenCLI mutation is blocked until the action node is accepted.",
    ),
    OPENCLI_WRITE_PERMISSION_REQUIRED: WorkflowBlockReasonDefinition(
        code=OPENCLI_WRITE_PERMISSION_REQUIRED,
        category="missing_permission",
        stable_fields=("code", "source", "details.bindingId", "details.requiredPermission"),
        description="OpenCLI mutation is blocked because canMutateExternalSites is false.",
    ),
    SEND_PERMISSION_REQUIRED: WorkflowBlockReasonDefinition(
        code=SEND_PERMISSION_REQUIRED,
        category="missing_permission",
        stable_fields=("code", "source", "details.bindingId", "details.requiredPermission"),
        description="Notification delivery is blocked because canSendNotifications is false.",
    ),
    MISSING_DELIVERY_PROJECTION: WorkflowBlockReasonDefinition(
        code=MISSING_DELIVERY_PROJECTION,
        category="missing_config",
        stable_fields=("code", "source", "details.bindingId", "details.required_params"),
        volatile_fields=("message",),
        description="Delivery is blocked until webhook URL and projection inputs are configured.",
    ),
    MISSING_FEISHU_CONNECTION: WorkflowBlockReasonDefinition(
        code=MISSING_FEISHU_CONNECTION,
        category="missing_config",
        stable_fields=("code", "source", "details.bindingId", "details.connectionId"),
        volatile_fields=("message",),
        description="Feishu Bitable delivery requires an enabled saved connection.",
    ),
    INVALID_FEISHU_RECORD_INPUT: WorkflowBlockReasonDefinition(
        code=INVALID_FEISHU_RECORD_INPUT,
        category="missing_config",
        stable_fields=("code", "source", "details.bindingId", "details.nodeId"),
        volatile_fields=("message",),
        description="Feishu Bitable delivery accepts only materialized certified Record refs.",
    ),
    FEISHU_WRITE_PERMISSION_REQUIRED: WorkflowBlockReasonDefinition(
        code=FEISHU_WRITE_PERMISSION_REQUIRED,
        category="missing_permission",
        stable_fields=("code", "source", "details.bindingId", "details.requiredPermission"),
        description=(
            "Feishu sheet synchronization or Bitable delivery is blocked because "
            "external-site mutation is disabled."
        ),
    ),
    MISSING_RUNTIME_BINDING: WorkflowBlockReasonDefinition(
        code=MISSING_RUNTIME_BINDING,
        category="missing_runtime_binding",
        stable_fields=("code", "source", "details.kind", "details.capability"),
        volatile_fields=("message",),
        description="Compiled node has no registered runtime binding.",
    ),
    MISSING_RUNTIME_IO_CONTRACT: WorkflowBlockReasonDefinition(
        code=MISSING_RUNTIME_IO_CONTRACT,
        category="missing_runtime_binding",
        stable_fields=("code", "source", "details.bindingId"),
        volatile_fields=("message",),
        description="Runtime binding exists but has no declared node I/O contract.",
    ),
    MISSING_RUNTIME_PARAMETER: WorkflowBlockReasonDefinition(
        code=MISSING_RUNTIME_PARAMETER,
        category="missing_config",
        stable_fields=("code", "source", "details.required_params"),
        volatile_fields=("message",),
        description="Runtime binding cannot be built because required node params are absent.",
    ),
    MISSING_SOURCE_CREDENTIAL: WorkflowBlockReasonDefinition(
        code=MISSING_SOURCE_CREDENTIAL,
        category="missing_source_credential",
        stable_fields=(
            "code",
            "source",
            "details.bindingId",
            "details.requiredCredentialKey",
        ),
        volatile_fields=("message",),
        description="Source fetch requires a saved credential reference that is absent.",
    ),
    MISSING_TOOL_CAPABILITY_BINDING: WorkflowBlockReasonDefinition(
        code=MISSING_TOOL_CAPABILITY_BINDING,
        category="missing_runtime_binding",
        stable_fields=("code", "source", "details.toolCapabilityId"),
        volatile_fields=("message",),
        description="Tool-capability node has no registered backend tool binding.",
    ),
    MISSING_TURBOPUSH_CONTENT_TYPE: WorkflowBlockReasonDefinition(
        code=MISSING_TURBOPUSH_CONTENT_TYPE,
        category="missing_config",
        stable_fields=("code", "source", "details.required_params"),
        volatile_fields=("message",),
        description="TurboPush publish cannot bind without a supported content type.",
    ),
    MISSING_TURBOPUSH_SERVICE: WorkflowBlockReasonDefinition(
        code=MISSING_TURBOPUSH_SERVICE,
        category="missing_runtime_resource",
        stable_fields=("code", "source", "details.provider"),
        volatile_fields=("message", "details.required_params"),
        description="TurboPush local runtime service resource is not configured.",
    ),
    SOURCE_OUTPUT_REQUIRED: WorkflowBlockReasonDefinition(
        code=SOURCE_OUTPUT_REQUIRED,
        category="missing_config",
        stable_fields=("code", "source", "details.bindingId", "details.liveMode"),
        volatile_fields=("message",),
        description="Fixture/mock source fetch needs source outputs before downstream execution.",
    ),
}


def block_reason_definition(code: str) -> WorkflowBlockReasonDefinition | None:
    return WORKFLOW_BLOCK_REASON_TAXONOMY.get(code)


def block_reason_category(code: str) -> BlockReasonCategory | None:
    definition = block_reason_definition(code)
    return definition.category if definition else None


__all__ = [
    "BlockReasonCategory",
    "FETCH_PERMISSION_REQUIRED",
    "FEISHU_WRITE_PERMISSION_REQUIRED",
    "MISSING_ADAPTER_RESOURCE",
    "MISSING_DELIVERY_PROJECTION",
    "MISSING_FEISHU_CONNECTION",
    "INVALID_FEISHU_RECORD_INPUT",
    "MISSING_OPENCLI_COMMAND",
    "MISSING_PROFILE_BINDING",
    "MISSING_RUNTIME_BINDING",
    "MISSING_RUNTIME_IO_CONTRACT",
    "MISSING_RUNTIME_PARAMETER",
    "MISSING_SOURCE_CREDENTIAL",
    "MISSING_SESSION_SNAPSHOT",
    "MISSING_TOOL_CAPABILITY_BINDING",
    "MISSING_TURBOPUSH_CONTENT_TYPE",
    "MISSING_TURBOPUSH_SERVICE",
    "SEND_PERMISSION_REQUIRED",
    "SOURCE_OUTPUT_REQUIRED",
    "MISSING_WORKER_CAPACITY",
    "OPENCLI_WRITE_APPROVAL_REQUIRED",
    "OPENCLI_WRITE_PERMISSION_REQUIRED",
    "PROFILE_LOCK_CONTENDED",
    "WORKFLOW_BLOCK_REASON_TAXONOMY",
    "WorkflowBlockReasonDefinition",
    "block_reason_category",
    "block_reason_definition",
]
