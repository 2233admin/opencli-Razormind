"""Compile Canvas WorkflowProject documents into executable-plan previews."""

import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from backend.plan_ir.validation import validate_plan_graph
from backend.schemas.plan_ir import PlanEdge, PlanGraph, PlanNode, PlanPort
from backend.schemas.workflow import (
    CompiledWorkflowAdapterBinding,
    CompiledWorkflowEdge,
    CompiledWorkflowNode,
    WorkflowAdapterBinding,
    WorkflowAuthoringMetadata,
    WorkflowCompiledPlanPreview,
    WorkflowCompileResponse,
    WorkflowProject,
    WorkflowProjectEdge,
    WorkflowProjectNode,
    WorkflowRuntimePreview,
)
from backend.schemas.workflow_compile import WorkflowCompileError
from backend.workflow.data_operators import (
    list_data_operator_specs,
    resolve_data_operator,
)
from backend.workflow.hda_templates import materialize_hda_templates
from backend.workflow.native_intelligence_executor import (
    NATIVE_INTELLIGENCE_ACTION_BY_TOOL_ID,
)
from backend.workflow.node_registry import (
    forbidden_node_definition_keys,
    resolve_node_origin,
)
from backend.workflow.runtime_contracts import runtime_io_contract
from backend.workflow.runtime_registry import resolve_runtime_metadata
from backend.workflow.tool_capabilities import (
    validate_workflow_tool_capability_version_pin,
)

INTERNAL_ID_SEPARATOR = "::"
MAX_NODE_PATH_DEPTH = 4
_LEGACY_DATA_OPERATOR_PACK_VERSION = "1.0.0"
COLLECTION_SOURCE_CATALOG_IDS = {
    "collection.source.web",
    "collection.source.api",
    "collection.source.rss",
    "collection.source.cli",
}


@dataclass(frozen=True)
class _PortContract:
    id: str
    direction: Literal["input", "output"]
    type: str
    required: bool = True


_PORT_CONTRACTS: dict[str, tuple[list[_PortContract], list[_PortContract]]] = {
    "media.image-generation": (
        [
            _PortContract("prompt", "input", "text", required=False),
            _PortContract("assets", "input", "mediaAsset[]", required=False),
        ],
        [
            _PortContract("assets", "output", "mediaAsset[]"),
            _PortContract("generation", "output", "mediaGenerationResult"),
        ],
    ),
    "media.image-asset": (
        [],
        [_PortContract("assets", "output", "mediaAsset[]")],
    ),
    "intelligence.input.collection-need": (
        [_PortContract("in", "input", "collectionNeed", required=False)],
        [_PortContract("patch", "output", "workflowPatch")],
    ),
    "intelligence.schedule.cron": (
        [],
        [_PortContract("tick", "output", "trigger")],
    ),
    "intelligence.source.opencli-slot": (
        [_PortContract("in", "input", "trigger", required=False)],
        [_PortContract("out", "output", "items[]")],
    ),
    "intelligence.source.rss": (
        [_PortContract("in", "input", "trigger", required=False)],
        [_PortContract("out", "output", "items[]")],
    ),
    "intelligence.source.pool": (
        [_PortContract("in", "input", "trigger", required=False)],
        [_PortContract("out", "output", "trigger")],
    ),
    **{
        catalog_id: (
            [_PortContract("in", "input", "trigger", required=False)],
            [_PortContract("out", "output", "CollectorOutputV1")],
        )
        for catalog_id in COLLECTION_SOURCE_CATALOG_IDS
    },
    "intelligence.processing.normalize": (
        [_PortContract("in", "input", "items[]")],
        [_PortContract("out", "output", "recordCandidate[]")],
    ),
    "intelligence.processing.dedupe": (
        [_PortContract("in", "input", "recordCandidate[]")],
        [_PortContract("out", "output", "recordCandidate[]")],
    ),
    "intelligence.data.generate": (
        [_PortContract("in", "input", "recordCandidate[]")],
        [_PortContract("out", "output", "recordCandidate[]")],
    ),
    "intelligence.data.filter": (
        [_PortContract("in", "input", "recordCandidate[]")],
        [_PortContract("out", "output", "recordCandidate[]")],
    ),
    "intelligence.data.evaluate": (
        [_PortContract("in", "input", "recordCandidate[]")],
        [_PortContract("out", "output", "recordCandidate[]")],
    ),
    "intelligence.data.refine": (
        [_PortContract("in", "input", "recordCandidate[]")],
        [_PortContract("out", "output", "recordCandidate[]")],
    ),
    "intelligence.flow.merge": (
        [_PortContract("in", "input", "CollectorMergeInputV1")],
        [_PortContract("out", "output", "recordCandidate[]")],
    ),
    "intelligence.control.record-acceptance": (
        [_PortContract("candidates", "input", "recordCandidate[]")],
        [_PortContract("records", "output", "record[]")],
    ),
    "intelligence.sink.records": (
        [_PortContract("records", "input", "record[]")],
        [_PortContract("stored", "output", "storedItems[]", required=False)],
    ),
    "intelligence.sink.feishu-bitable": (
        [_PortContract("records", "input", "storedItems[]")],
        [_PortContract("delivery", "output", "deliveryAttempt[]", required=False)],
    ),
    "intelligence.output.collection-result": (
        [_PortContract("in", "input", "recordCandidate[]")],
        [_PortContract("out", "output", "items[]", required=False)],
    ),
    "intelligence.output.inbox": (
        # recordCandidate[] like its kind-level fallback (kind=inbox/sink +
        # store) and the sibling collection-result contract — the old
        # items[] here predated the recordCandidate[] type chain and made
        # normalize -> inbox unconnectable.
        [_PortContract("in", "input", "recordCandidate[]")],
        [_PortContract("out", "output", "storedItems[]", required=False)],
    ),
    "intelligence.output.webhook": (
        [_PortContract("in", "input", "any", required=False)],
        [_PortContract("payload", "output", "notificationPayload", required=False)],
    ),
    "external.tool.capability": (
        [_PortContract("in", "input", "unknown", required=False)],
        [_PortContract("out", "output", "unknown", required=False)],
    ),
    "package.intelligence.situation-awareness": (
        [_PortContract("in", "input", "unknown", required=False)],
        [_PortContract("out", "output", "unknown", required=False)],
    ),
    "package.simulation.swarm-forecast": (
        [_PortContract("in", "input", "unknown", required=False)],
        [_PortContract("out", "output", "unknown", required=False)],
    ),
    "package.intelligence.native-lifecycle": (
        [_PortContract("in", "input", "unknown", required=False)],
        [_PortContract("out", "output", "unknown", required=False)],
    ),
    "package.intelligence.pipeline": (
        [_PortContract("in", "input", "any", required=False)],
        [_PortContract("out", "output", "any", required=False)],
    ),
    "package.review.human-review": (
        [_PortContract("in", "input", "any", required=False)],
        [_PortContract("out", "output", "any", required=False)],
    ),
    "package.dispatch.fanout": (
        [_PortContract("in", "input", "any", required=False)],
        [_PortContract("out", "output", "any", required=False)],
    ),
    "package.compat.dify-workflow": (
        [_PortContract("in", "input", "any", required=False)],
        [_PortContract("out", "output", "any", required=False)],
    ),
}


def compile_workflow_project(project: WorkflowProject) -> WorkflowCompileResponse:
    """Validate and compile a WorkflowProject without dispatching execution."""

    project = materialize_hda_templates(project)
    errors = _validate_project(project)
    if errors:
        return WorkflowCompileResponse(valid=False, errors=errors, plan=None)

    adapter_by_id = {adapter.id: adapter for adapter in project.adapters}
    depends_on = _expanded_dependency_map(project.nodes, project.edges, parent_path=())
    compiled_nodes: list[CompiledWorkflowNode] = []
    compiled_edges: list[CompiledWorkflowEdge] = []
    plan_nodes: list[PlanNode] = []
    plan_edges: list[PlanEdge] = []

    for node in project.nodes:
        _compile_node_tree(
            node,
            node_path=(node.id,),
            adapter_by_id=adapter_by_id,
            depends_on=depends_on[node.id],
            compiled_nodes=compiled_nodes,
            compiled_edges=compiled_edges,
            plan_nodes=plan_nodes,
            plan_edges=plan_edges,
        )

    root_compiled_edges: list[CompiledWorkflowEdge] = []
    root_plan_edges: list[PlanEdge] = []
    _append_expanded_edges(
        project.edges,
        nodes=project.nodes,
        parent_path=(),
        compiled_edges=root_compiled_edges,
        plan_edges=root_plan_edges,
    )
    compiled_edges = [*root_compiled_edges, *compiled_edges]
    plan_edges = [*root_plan_edges, *plan_edges]
    compiled_nodes = _topologically_order_compiled_nodes(compiled_nodes)
    plan_ir = PlanGraph(name=project.name, draft=True, nodes=plan_nodes, edges=plan_edges)
    _widen_merge_fan_in(plan_ir)
    # Compile previews adopt registry pins for unpinned tools; the strict
    # require-pin gate stays on authoritative Plan persistence.
    plan_validation = validate_plan_graph(plan_ir, require_tool_pins=False)
    if not plan_validation.valid:
        return WorkflowCompileResponse(
            valid=False,
            errors=[
                WorkflowCompileError(
                    code=f"plan_ir_{error.code}",
                    message=error.message,
                    node_id=error.node_id,
                    edge_id=error.edge_id,
                    path=["plan", "runtime", "plan_ir"],
                )
                for error in plan_validation.errors
            ],
            plan=None,
        )

    return WorkflowCompileResponse(
        valid=True,
        errors=[],
        plan=WorkflowCompiledPlanPreview(
            authoring=WorkflowAuthoringMetadata(
                project_id=project.id,
                project_name=project.name,
                project_version=project.version,
                profile=project.profile,
                node_count=len(project.nodes),
                edge_count=len(project.edges),
                adapter_count=len(project.adapters),
                settings=project.settings,
                agentPermissions=project.agentPermissions,
            ),
            runtime=WorkflowRuntimePreview(
                node_ids=[node.id for node in compiled_nodes],
                nodes=compiled_nodes,
                edges=compiled_edges,
                plan_ir=plan_ir,
            ),
        ),
    )


def _topologically_order_compiled_nodes(
    nodes: list[CompiledWorkflowNode],
) -> list[CompiledWorkflowNode]:
    """Return a stable execution order independent of authoring array order."""

    node_by_id = {node.id: node for node in nodes}
    original_index = {node.id: index for index, node in enumerate(nodes)}
    indegree = {node.id: 0 for node in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for upstream_id in node.depends_on:
            if upstream_id not in node_by_id:
                continue
            adjacency[upstream_id].append(node.id)
            indegree[node.id] += 1

    ready = deque(node.id for node in nodes if indegree[node.id] == 0)
    ordered_ids: list[str] = []
    while ready:
        node_id = ready.popleft()
        ordered_ids.append(node_id)
        newly_ready: list[str] = []
        for target_id in adjacency[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                newly_ready.append(target_id)
        for target_id in sorted(newly_ready, key=original_index.__getitem__):
            ready.append(target_id)

    if len(ordered_ids) != len(nodes):
        return nodes
    return [node_by_id[node_id] for node_id in ordered_ids]


def _validate_project(project: WorkflowProject) -> list[WorkflowCompileError]:
    errors: list[WorkflowCompileError] = []
    node_counts = Counter(node.id for node in project.nodes)
    duplicate_nodes = {node_id for node_id, count in node_counts.items() if count > 1}
    for node_id in sorted(duplicate_nodes):
        errors.append(
            WorkflowCompileError(
                code="duplicate_node_id",
                message=f'Workflow node id "{node_id}" is duplicated',
                node_id=node_id,
                path=["nodes"],
            )
        )

    edge_counts = Counter(edge.id for edge in project.edges)
    for edge_id, count in sorted(edge_counts.items()):
        if count > 1:
            errors.append(
                WorkflowCompileError(
                    code="duplicate_edge_id",
                    message=f'Workflow edge id "{edge_id}" is duplicated',
                    edge_id=edge_id,
                    path=["edges"],
                )
            )

    node_ids = {node.id for node in project.nodes}
    adapter_by_id = {adapter.id: adapter for adapter in project.adapters}
    for edge in project.edges:
        if edge.source not in node_ids:
            errors.append(
                WorkflowCompileError(
                    code="missing_edge_source",
                    message=f'Workflow edge "{edge.id}" references missing source "{edge.source}"',
                    edge_id=edge.id,
                    path=["edges", edge.id, "source"],
                )
            )
        if edge.target not in node_ids:
            errors.append(
                WorkflowCompileError(
                    code="missing_edge_target",
                    message=f'Workflow edge "{edge.id}" references missing target "{edge.target}"',
                    edge_id=edge.id,
                    path=["edges", edge.id, "target"],
                )
            )

    for node in project.nodes:
        if (
            not _is_structural_container(node)
            and node.adapter
            and node.adapter not in adapter_by_id
        ):
            errors.append(
                WorkflowCompileError(
                    code="missing_adapter_binding",
                    message=(
                        f'Workflow node "{node.id}" references missing adapter "{node.adapter}"'
                    ),
                    node_id=node.id,
                    path=["nodes", node.id, "adapter"],
                )
            )
        elif not _is_structural_container(node) and _requires_adapter(node) and not node.adapter:
            errors.append(
                WorkflowCompileError(
                    code="missing_adapter_binding",
                    message=f'Workflow node "{node.id}" requires an adapter binding',
                    node_id=node.id,
                    path=["nodes", node.id, "adapter"],
                )
            )

        if _has_package_internals(node):
            errors.extend(
                _validate_package_internals(
                    node,
                    adapter_by_id,
                    node_path=(node.id,),
                    path_prefix=["nodes", node.id],
                )
            )

        errors.extend(_validate_node_origin(node, ["nodes", node.id]))
        errors.extend(_validate_data_operator_node(node, ["nodes", node.id]))
        errors.extend(_validate_capability_version_pin(node))
        errors.extend(_validate_node_capability_gaps(node, ["nodes", node.id]))

    errors.extend(_validate_edge_mappings(project.edges, path_prefix=["edges"]))
    errors.extend(
        _validate_visible_merge_nodes(
            project.nodes,
            project.edges,
            path_prefix=["edges"],
        )
    )
    errors.extend(_validate_typed_edges(project.nodes, project.edges, path_prefix=["edges"]))
    errors.extend(_cycle_errors(project))
    return errors


def _validate_capability_version_pin(
    node: WorkflowProjectNode,
) -> list[WorkflowCompileError]:
    tool_capability = node.params.get("toolCapability")
    if not isinstance(tool_capability, dict):
        return []
    tool_id = _read_string(tool_capability.get("id"))
    if tool_id is None:
        return []
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    if catalog_id not in {"external.tool.capability", tool_id}:
        return []

    issue = validate_workflow_tool_capability_version_pin(
        tool_id,
        tool_capability.get("versionPin"),
        # Compile-time contract: an absent pin adopts the registry's current
        # version; only explicit mismatches fail. Authoritative Plan
        # persistence keeps the strict require-pin gate.
        require_pin=False,
    )
    if issue is None:
        return []
    if issue.code == "unknown_tool_capability" and isinstance(
        node.params.get("externalWorkflow"), dict
    ):
        # Import is a drafting surface: preserve unknown external tools as a
        # visible Capability Gap. Authoritative Plan validation still rejects
        # the same unknown capability before publication or execution.
        return []
    return [
        WorkflowCompileError(
            code=issue.code,
            message=f'Workflow node "{node.id}" {issue.message}',
            node_id=node.id,
            path=[
                "nodes",
                node.id,
                "params",
                "toolCapability",
                "versionPin",
            ],
        )
    ]


def _requires_adapter(node: WorkflowProjectNode) -> bool:
    if _read_string((node.ui or {}).get("catalogId")) == "media.image-asset":
        return False
    if _read_string((node.ui or {}).get("catalogId")) in COLLECTION_SOURCE_CATALOG_IDS:
        return False
    return node.kind == "source" or node.capability in {"fetch", "send"}


def _is_structural_container(node: WorkflowProjectNode) -> bool:
    return _has_package_internals(node) and not _is_managed_runtime_package(node)


def _has_package_internals(node: WorkflowProjectNode) -> bool:
    return bool(node.internals and node.internals.nodes)


def _is_managed_runtime_package(node: WorkflowProjectNode) -> bool:
    return _has_package_internals(node) and node.params.get("packageExecution") == "managed"


def _validate_node_origin(
    node: WorkflowProjectNode,
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    errors: list[WorkflowCompileError] = []
    for key in forbidden_node_definition_keys(node):
        errors.append(
            WorkflowCompileError(
                code="forbidden_node_definition",
                message=(
                    f'Workflow node "{node.id}" includes forbidden implementation '
                    f'data "{key}". Use an existing node-library primitive/package '
                    "or an n8n-translated node instead."
                ),
                node_id=node.id,
                path=[*path_prefix, *key.split(".")],
            )
        )

    origin = resolve_node_origin(node)
    if origin.kind == "legacy" and origin.notes:
        errors.append(
            WorkflowCompileError(
                code="unknown_node_library_binding",
                message=(
                    f'Workflow node "{node.id}" references an unknown node-library '
                    "binding. Use an existing catalog/primitive id, or import the "
                    "missing capability from n8n."
                ),
                node_id=node.id,
                path=[*path_prefix, "ui"],
            )
        )
    return errors


def _validate_data_operator_node(
    node: WorkflowProjectNode,
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    catalog_id = _read_string((node.ui or {}).get("catalogId"))
    prefix = "intelligence.data."
    expected_kind = (
        catalog_id.removeprefix(prefix) if catalog_id and catalog_id.startswith(prefix) else None
    )
    operator_id = _read_string(node.params.get("operatorId"))
    if expected_kind not in {"generate", "filter", "evaluate", "refine"}:
        if "operatorId" not in node.params:
            return []
        return [
            WorkflowCompileError(
                code="data_operator_catalog_required",
                message=(
                    f'Workflow node "{node.id}" declares params.operatorId but does '
                    "not reference a registered intelligence.data catalog node"
                ),
                node_id=node.id,
                path=[*path_prefix, "ui", "catalogId"],
            )
        ]
    if operator_id is None:
        return [
            WorkflowCompileError(
                code="missing_data_operator_id",
                message=f'Workflow data node "{node.id}" requires params.operatorId',
                node_id=node.id,
                path=[*path_prefix, "params", "operatorId"],
            )
        ]

    pack_version_provided = "packVersion" in node.params
    requested_pack_version = _read_string(node.params.get("packVersion"))
    if pack_version_provided and requested_pack_version is None:
        return [
            WorkflowCompileError(
                code="unsupported_data_operator_version",
                message=(
                    f'Workflow data node "{node.id}" requires params.packVersion '
                    "to be a non-empty string when provided"
                ),
                node_id=node.id,
                path=[*path_prefix, "params", "packVersion"],
            )
        ]
    resolved_pack_version = requested_pack_version or _LEGACY_DATA_OPERATOR_PACK_VERSION
    spec = resolve_data_operator(operator_id, resolved_pack_version)
    if spec is None:
        if any(registered.id == operator_id for registered in list_data_operator_specs()):
            return [
                WorkflowCompileError(
                    code="unsupported_data_operator_version",
                    message=(
                        f'Workflow data node "{node.id}" references unsupported '
                        f'version "{resolved_pack_version}" of operator '
                        f'"{operator_id}"'
                    ),
                    node_id=node.id,
                    path=[*path_prefix, "params", "packVersion"],
                )
            ]
        return [
            WorkflowCompileError(
                code="unknown_data_operator",
                message=(
                    f'Workflow data node "{node.id}" references unknown operator "{operator_id}"'
                ),
                node_id=node.id,
                path=[*path_prefix, "params", "operatorId"],
            )
        ]
    if spec.kind != expected_kind:
        return [
            WorkflowCompileError(
                code="data_operator_kind_mismatch",
                message=(
                    f'Data operator "{operator_id}" has kind "{spec.kind}", but node '
                    f'"{node.id}" requires "{expected_kind}"'
                ),
                node_id=node.id,
                path=[*path_prefix, "params", "operatorId"],
            )
        ]
    return []


def _validate_node_capability_gaps(
    node: WorkflowProjectNode,
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    ui = node.ui or {}
    builder = ui.get("builder")
    if not isinstance(builder, dict):
        return []
    gaps = builder.get("capabilityGaps")
    if not isinstance(gaps, list):
        return []

    errors: list[WorkflowCompileError] = []
    for index, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            continue
        blocking_actions = gap.get("blockingActions")
        blocking_action_names = (
            {value for value in blocking_actions if isinstance(value, str)}
            if isinstance(blocking_actions, list)
            else set()
        )
        if blocking_actions is not None and not {"publish", "run"}.intersection(
            blocking_action_names
        ):
            continue
        gap_id = _read_string(gap.get("id")) or f"gap-{index + 1}"
        title = _read_string(gap.get("title")) or "Capability Gap"
        detail = _read_string(gap.get("detail")) or "Required runtime capability is incomplete"
        errors.append(
            WorkflowCompileError(
                code="capability_gap",
                message=f'Workflow node "{node.id}" is blocked by {title}: {detail}',
                node_id=node.id,
                path=[*path_prefix, "ui", "builder", "capabilityGaps", gap_id],
            )
        )
    return errors


def _validate_typed_edges(
    nodes: list[WorkflowProjectNode],
    edges: list[WorkflowProjectEdge],
    *,
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    errors: list[WorkflowCompileError] = []
    node_by_id = {node.id: node for node in nodes}
    for edge in edges:
        source_node = node_by_id.get(edge.source)
        target_node = node_by_id.get(edge.target)
        if source_node is None or target_node is None:
            continue
        if _is_structural_container(source_node) or _is_structural_container(target_node):
            continue
        source_contract = _node_port_contracts(source_node)
        target_contract = _node_port_contracts(target_node)
        if source_contract is None or target_contract is None:
            continue

        source_port = _resolve_output_port(source_contract[1], edge.sourcePort)
        target_catalog_id = _read_string((target_node.ui or {}).get("catalogId"))
        merge_alias = (
            target_catalog_id == "intelligence.flow.merge"
            and edge.targetPort
            and re.fullmatch(r"in\d+", edge.targetPort)
        )
        target_port = (
            _PortContract("in", "input", "CollectorMergeInputV1")
            if merge_alias
            else _resolve_input_port(target_contract[0], edge.targetPort)
        )
        if source_port is None:
            errors.append(
                WorkflowCompileError(
                    code="invalid_edge_source_port",
                    message=(
                        f'Workflow edge "{edge.id}" references invalid source port '
                        f'"{edge.sourcePort}" on node "{edge.source}"'
                    ),
                    edge_id=edge.id,
                    path=[*path_prefix, edge.id, "sourcePort"],
                )
            )
            continue
        if target_port is None:
            errors.append(
                WorkflowCompileError(
                    code="invalid_edge_target_port",
                    message=(
                        f'Workflow edge "{edge.id}" references invalid target port '
                        f'"{edge.targetPort}" on node "{edge.target}"'
                    ),
                    edge_id=edge.id,
                    path=[*path_prefix, edge.id, "targetPort"],
                )
            )
            continue
        if not _port_types_compatible(source_port.type, target_port.type):
            errors.append(
                WorkflowCompileError(
                    code="incompatible_edge_ports",
                    message=(
                        f'Workflow edge "{edge.id}" connects incompatible port types: '
                        f"{source_port.type} -> {target_port.type}"
                    ),
                    edge_id=edge.id,
                    path=[*path_prefix, edge.id],
                )
            )
    return errors


def _validate_edge_mappings(
    edges: list[WorkflowProjectEdge],
    *,
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    errors: list[WorkflowCompileError] = []
    for edge in edges:
        ui = edge.ui or {}
        mapping = ui.get("mapping")
        if not isinstance(mapping, dict) or mapping.get("compatible") is not False:
            continue
        conflicts = mapping.get("conflicts")
        conflict_messages = (
            [value.strip() for value in conflicts if isinstance(value, str) and value.strip()]
            if isinstance(conflicts, list)
            else []
        )
        detail = "; ".join(conflict_messages) or "field mapping is incompatible"
        errors.append(
            WorkflowCompileError(
                code="incompatible_edge_mapping",
                message=f'Workflow edge "{edge.id}" cannot compile: {detail}',
                edge_id=edge.id,
                path=[*path_prefix, edge.id, "ui", "mapping"],
            )
        )
    return errors


def _validate_visible_merge_nodes(
    nodes: list[WorkflowProjectNode],
    edges: list[WorkflowProjectEdge],
    *,
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    incoming = Counter(edge.target for edge in edges)
    errors: list[WorkflowCompileError] = []
    for node in nodes:
        builder = (node.ui or {}).get("builder")
        if (
            incoming[node.id] <= 1
            or not isinstance(builder, dict)
            or (node.kind == "flow" and node.capability == "merge")
        ):
            continue
        errors.append(
            WorkflowCompileError(
                code="multiple_inputs_require_merge",
                message=(
                    f'Workflow node "{node.id}" has {incoming[node.id]} inputs. '
                    "Agent Builder requires a visible Merge node before any multi-input node."
                ),
                node_id=node.id,
                path=[*path_prefix, node.id],
            )
        )
    return errors


def _node_port_contracts(
    node: WorkflowProjectNode,
) -> tuple[list[_PortContract], list[_PortContract]] | None:
    native_contract = _native_intelligence_port_contracts(node)
    if native_contract is not None:
        return native_contract
    ui = node.ui or {}
    catalog_id = _read_string(ui.get("catalogId"))
    if catalog_id in COLLECTION_SOURCE_CATALOG_IDS:
        return (
            [_PortContract("in", "input", "trigger", required=False)],
            [_PortContract("out", "output", "CollectorOutputV1")],
        )
    if catalog_id in _PORT_CONTRACTS:
        return _PORT_CONTRACTS[catalog_id]
    primitive_id = _read_string(ui.get("primitiveId"))
    node_library_id = primitive_id or catalog_id
    if node_library_id in {
        "primitive.core.webhook-trigger",
        "primitive.ops.trigger-webhook",
    }:
        return ([], [_PortContract("request", "output", "webhookRequest")])
    if not node_library_id or not node_library_id.startswith("primitive."):
        return _inferred_node_port_contracts(node)

    declared_ports = ui.get("primitivePorts")
    if not isinstance(declared_ports, list):
        return None
    inputs: list[_PortContract] = []
    outputs: list[_PortContract] = []
    for value in declared_ports:
        if not isinstance(value, dict):
            return None
        port_id = _read_string(value.get("id"))
        direction = _read_string(value.get("direction"))
        port_type = _read_string(value.get("type"))
        if not port_id or direction not in {"input", "output"} or not port_type:
            return None
        port = _PortContract(
            port_id,
            direction,
            port_type,
            required=value.get("required") is not False,
        )
        (inputs if direction == "input" else outputs).append(port)
    return inputs, outputs


def _native_intelligence_port_contracts(
    node: WorkflowProjectNode,
) -> tuple[list[_PortContract], list[_PortContract]] | None:
    tool_capability = node.params.get("toolCapability")
    if not isinstance(tool_capability, dict):
        return None
    tool_id = _read_string(tool_capability.get("id"))
    action = NATIVE_INTELLIGENCE_ACTION_BY_TOOL_ID.get(tool_id or "")
    if action is None:
        return None
    binding_id = f"workflow.native-intelligence.{action.name.replace('.', '-')}"
    contract = runtime_io_contract(binding_id)
    if contract is None:
        return None
    return (
        [_PortContract(name, "input", type_) for name, type_ in contract.input_ports],
        [_PortContract(name, "output", type_) for name, type_ in contract.output_ports],
    )


def _inferred_node_port_contracts(
    node: WorkflowProjectNode,
) -> tuple[list[_PortContract], list[_PortContract]] | None:
    alias_input = _PortContract("records", "input", "any", required=False)
    alias_output = _PortContract("records", "output", "any", required=False)
    if node.kind == "source" and node.capability == "fetch":
        return (
            [_PortContract("in", "input", "any", required=False), alias_input],
            [_PortContract("out", "output", "items[]"), alias_output],
        )
    if node.kind == "agent" and node.capability == "normalize":
        return (
            [_PortContract("in", "input", "items[]"), alias_input],
            [_PortContract("out", "output", "recordCandidate[]"), alias_output],
        )
    if node.kind in {"inbox", "sink"} and node.capability == "store":
        return (
            [_PortContract("in", "input", "recordCandidate[]"), alias_input],
            [_PortContract("out", "output", "storedItems[]", required=False)],
        )
    if node.kind == "notify" and node.capability == "send":
        return (
            [_PortContract("in", "input", "recordCandidate[]"), alias_input],
            [_PortContract("out", "output", "notificationPayload", required=False)],
        )
    return None


def _resolve_output_port(
    outputs: list[_PortContract],
    requested_port: str | None,
) -> _PortContract | None:
    if requested_port:
        return next((port for port in outputs if port.id == requested_port), None)
    if len(outputs) == 1:
        return outputs[0]
    return next((port for port in outputs if port.required), outputs[0] if outputs else None)


def _resolve_input_port(
    inputs: list[_PortContract],
    requested_port: str | None,
) -> _PortContract | None:
    if requested_port:
        return next((port for port in inputs if port.id == requested_port), None)
    if len(inputs) == 1:
        return inputs[0]
    return next((port for port in inputs if port.required), inputs[0] if inputs else None)


def _port_types_compatible(source_type: str, target_type: str) -> bool:
    if source_type == target_type:
        return True
    if target_type == "CollectorMergeInputV1" and source_type in {
        "CollectorOutputV1",
        "recordCandidate[]",
    }:
        return True
    return source_type in {"any", "unknown"} or target_type in {"any", "unknown"}


def _read_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _cycle_errors(project: WorkflowProject) -> list[WorkflowCompileError]:
    node_ids = [node.id for node in project.nodes]
    indegree = {node_id: 0 for node_id in node_ids}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in project.edges:
        if edge.source in indegree and edge.target in indegree:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

    queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
    visited: set[str] = set()
    while queue:
        node_id = queue.popleft()
        visited.add(node_id)
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    cycle_node_ids = sorted(set(node_ids) - visited)
    return [
        WorkflowCompileError(
            code="cycle",
            message=f'Workflow graph contains a cycle at node "{node_id}"',
            node_id=node_id,
            path=["nodes", node_id],
        )
        for node_id in cycle_node_ids
    ]


def _expanded_dependency_map(
    nodes: list[WorkflowProjectNode],
    edges: list[WorkflowProjectEdge],
    *,
    parent_path: tuple[str, ...],
) -> dict[str, list[str]]:
    node_by_id = {node.id: node for node in nodes}
    upstream: dict[str, list[str]] = {node.id: [] for node in nodes}
    for edge in edges:
        source = node_by_id.get(edge.source)
        if source is None or edge.target not in upstream:
            continue
        source_path = (*parent_path, source.id)
        upstream[edge.target].extend(
            _node_path_id(path) for path in _boundary_node_paths(source, source_path, "output")
        )
    return upstream


def _boundary_node_paths(
    node: WorkflowProjectNode,
    node_path: tuple[str, ...],
    direction: Literal["input", "output"],
) -> list[tuple[str, ...]]:
    return [path for path, _ in _boundary_nodes(node, node_path, direction)]


def _boundary_nodes(
    node: WorkflowProjectNode,
    node_path: tuple[str, ...],
    direction: Literal["input", "output"],
) -> list[tuple[tuple[str, ...], WorkflowProjectNode]]:
    if _is_managed_runtime_package(node) or not node.internals or not node.internals.nodes:
        return [(node_path, node)]

    connected_ids = {
        edge.target if direction == "input" else edge.source for edge in node.internals.edges
    }
    boundary_nodes = [
        internal_node
        for internal_node in node.internals.nodes
        if internal_node.id not in connected_ids
    ]
    boundaries: list[tuple[tuple[str, ...], WorkflowProjectNode]] = []
    for internal_node in boundary_nodes:
        boundaries.extend(
            _boundary_nodes(
                internal_node,
                (*node_path, internal_node.id),
                direction,
            )
        )
    return boundaries


def _boundary_edge_port(
    node: WorkflowProjectNode,
    direction: Literal["input", "output"],
    requested_port: str | None,
    *,
    traversed_container: bool,
) -> str:
    contracts = _node_port_contracts(node)
    if contracts is None:
        return requested_port or "records"

    ports = contracts[0] if direction == "input" else contracts[1]
    requested = requested_port
    if traversed_container and requested_port not in {port.id for port in ports}:
        requested = None
    resolved = (
        _resolve_input_port(ports, requested)
        if direction == "input"
        else _resolve_output_port(ports, requested)
    )
    return resolved.id if resolved is not None else requested_port or "records"


def _append_expanded_edges(
    edges: list[WorkflowProjectEdge],
    *,
    nodes: list[WorkflowProjectNode],
    parent_path: tuple[str, ...],
    compiled_edges: list[CompiledWorkflowEdge],
    plan_edges: list[PlanEdge],
) -> None:
    node_by_id = {node.id: node for node in nodes}
    parent_id = _node_path_id(parent_path) if parent_path else None
    for edge in edges:
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if source is None or target is None:
            continue
        source_boundaries = _boundary_nodes(
            source,
            (*parent_path, source.id),
            "output",
        )
        target_boundaries = _boundary_nodes(
            target,
            (*parent_path, target.id),
            "input",
        )
        pairs = [
            (source_boundary, target_boundary)
            for source_boundary in source_boundaries
            for target_boundary in target_boundaries
        ]
        for pair_index, (source_boundary, target_boundary) in enumerate(pairs):
            source_path, source_leaf = source_boundary
            target_path, target_leaf = target_boundary
            edge_id = _internal_id(parent_id, edge.id) if parent_id else edge.id
            if len(pairs) > 1:
                edge_id = _internal_id(edge_id, str(pair_index + 1))
            source_id = _node_path_id(source_path)
            target_id = _node_path_id(target_path)
            source_port = _boundary_edge_port(
                source_leaf,
                "output",
                edge.sourcePort,
                traversed_container=_is_structural_container(source),
            )
            target_port = _boundary_edge_port(
                target_leaf,
                "input",
                edge.targetPort,
                traversed_container=_is_structural_container(target),
            )
            compiled_edges.append(
                CompiledWorkflowEdge(
                    id=edge_id,
                    source=source_id,
                    target=target_id,
                    sourcePort=source_port,
                    targetPort=target_port,
                    contractId=edge.contractId,
                    condition=edge.condition,
                )
            )
            plan_edges.append(
                PlanEdge(
                    id=edge_id,
                    source_node=source_id,
                    source_port=source_port,
                    target_node=target_id,
                    target_port=target_port,
                )
            )


def _compile_node_tree(
    node: WorkflowProjectNode,
    *,
    node_path: tuple[str, ...],
    adapter_by_id: dict[str, WorkflowAdapterBinding],
    depends_on: list[str],
    compiled_nodes: list[CompiledWorkflowNode],
    compiled_edges: list[CompiledWorkflowEdge],
    plan_nodes: list[PlanNode],
    plan_edges: list[PlanEdge],
) -> None:
    node_id = _node_path_id(node_path)
    parent_id = _node_path_id(node_path[:-1]) if len(node_path) > 1 else None
    package_metadata = _package_metadata(node, node_path=node_path)
    is_structural = _is_structural_container(node)
    runtime: dict[str, object] = {
        "node_path": list(node_path),
        "structural": is_structural,
        "executable": not is_structural,
    }
    if parent_id is not None:
        runtime.update(
            {
                "package_parent_id": parent_id,
                "package_internal_id": node.id,
                "editable": not _ancestor_package_locked(node_path, compiled_nodes),
            }
        )
    compiled_nodes.append(
        _compile_node(
            node,
            adapter_by_id.get(node.adapter or ""),
            depends_on,
            id_override=node_id,
            package=package_metadata,
            runtime=runtime,
        )
    )
    plan_nodes.append(_to_plan_node(node, id_override=node_id, node_path=node_path))

    if not _is_structural_container(node):
        return

    bound_internal_nodes = _bind_internal_parameters(node, node_path=node_path)
    internal_depends_on = _expanded_dependency_map(
        bound_internal_nodes,
        node.internals.edges,
        parent_path=node_path,
    )
    for internal_node in bound_internal_nodes:
        child_path = (*node_path, internal_node.id)
        internal_upstream = internal_depends_on[internal_node.id]
        _compile_node_tree(
            internal_node,
            node_path=child_path,
            adapter_by_id=adapter_by_id,
            depends_on=internal_upstream or depends_on,
            compiled_nodes=compiled_nodes,
            compiled_edges=compiled_edges,
            plan_nodes=plan_nodes,
            plan_edges=plan_edges,
        )

    _append_expanded_edges(
        node.internals.edges,
        nodes=bound_internal_nodes,
        parent_path=node_path,
        compiled_edges=compiled_edges,
        plan_edges=plan_edges,
    )


def _ancestor_package_locked(
    node_path: tuple[str, ...],
    compiled_nodes: list[CompiledWorkflowNode],
) -> bool:
    if len(node_path) <= 1:
        return False
    parent_id = _node_path_id(node_path[:-1])
    parent = next((node for node in reversed(compiled_nodes) if node.id == parent_id), None)
    return bool(
        parent
        and (
            (parent.package and parent.package.get("locked"))
            or parent.runtime.get("editable") is False
        )
    )


def _node_path_id(node_path: tuple[str, ...]) -> str:
    return INTERNAL_ID_SEPARATOR.join(node_path)


def _internal_id(package_node_id: str, internal_node_id: str) -> str:
    return f"{package_node_id}{INTERNAL_ID_SEPARATOR}{internal_node_id}"


def _package_locked(node: WorkflowProjectNode) -> bool:
    if node.internals and node.internals.locked is not None:
        return node.internals.locked
    return bool(node.topicCollapse and node.topicCollapse.mode == "locked")


def _package_metadata(
    node: WorkflowProjectNode,
    *,
    node_path: tuple[str, ...],
) -> dict[str, object] | None:
    if not (
        node.topicCollapse
        or node.miniNetwork
        or _is_structural_container(node)
        or _is_managed_runtime_package(node)
    ):
        return None

    locked = _package_locked(node)
    managed = _is_managed_runtime_package(node)
    node_id = _node_path_id(node_path)
    internal_node_ids = (
        [_internal_id(node_id, internal_node.id) for internal_node in node.internals.nodes]
        if node.internals
        else []
    )
    return {
        "miniNetwork": node.miniNetwork.model_dump() if node.miniNetwork else None,
        "topicCollapse": node.topicCollapse.model_dump() if node.topicCollapse else None,
        "locked": locked,
        "editable": not locked,
        "managed": managed,
        "structural": not managed,
        "executable": managed,
        "node_path": list(node_path),
        "internal_node_ids": internal_node_ids,
        "internal_edge_ids": (
            [_internal_id(node_id, edge.id) for edge in node.internals.edges]
            if node.internals
            else []
        ),
    }


def _resolve_internal_binding_id(
    binding_node_id: str,
    internal_node_ids: set[str],
    *,
    package_aliases: tuple[str, ...],
) -> str | None:
    if binding_node_id in internal_node_ids:
        return binding_node_id

    for package_alias in package_aliases:
        for separator in ("__", INTERNAL_ID_SEPARATOR):
            prefix = f"{package_alias}{separator}"
            if not binding_node_id.startswith(prefix):
                continue
            candidate = binding_node_id[len(prefix) :]
            if candidate in internal_node_ids:
                return candidate
    return None


def _bind_internal_parameters(
    node: WorkflowProjectNode,
    *,
    node_path: tuple[str, ...],
) -> list[WorkflowProjectNode]:
    if not node.internals:
        return []

    internal_by_id = {internal_node.id: internal_node for internal_node in node.internals.nodes}
    internal_node_ids = set(internal_by_id)
    package_aliases = tuple(dict.fromkeys((node.id, _node_path_id(node_path))))
    params_by_node = {
        internal_node.id: dict(internal_node.params) for internal_node in node.internals.nodes
    }
    if node.parameterInterface:
        for field in node.parameterInterface.fields:
            if field.binding.source != "params":
                continue
            if field.id in node.params:
                value = node.params[field.id]
            elif field.binding.fieldId in node.params:
                value = node.params[field.binding.fieldId]
            else:
                value = field.value
            if value is None:
                continue
            binding_node_id = _resolve_internal_binding_id(
                field.binding.nodeId,
                internal_node_ids,
                package_aliases=package_aliases,
            )
            if binding_node_id is None:
                continue
            params_by_node[binding_node_id][field.binding.fieldId] = value

    return [
        internal_by_id[internal_node.id].model_copy(
            update={"params": params_by_node[internal_node.id]}
        )
        for internal_node in node.internals.nodes
    ]


def _validate_package_internals(
    node: WorkflowProjectNode,
    adapter_by_id: dict[str, WorkflowAdapterBinding],
    *,
    node_path: tuple[str, ...],
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    errors: list[WorkflowCompileError] = []
    assert node.internals is not None
    package_node_id = _node_path_id(node_path)

    internal_counts = Counter(internal_node.id for internal_node in node.internals.nodes)
    for internal_node_id, count in sorted(internal_counts.items()):
        if count > 1:
            errors.append(
                WorkflowCompileError(
                    code="duplicate_internal_node_id",
                    message=(
                        f'Package node "{package_node_id}" has duplicated internal node '
                        f'"{internal_node_id}"'
                    ),
                    node_id=package_node_id,
                    path=[*path_prefix, "internals", "nodes", internal_node_id],
                )
            )

    internal_edge_counts = Counter(edge.id for edge in node.internals.edges)
    for edge_id, count in sorted(internal_edge_counts.items()):
        if count > 1:
            errors.append(
                WorkflowCompileError(
                    code="duplicate_edge_id",
                    message=(
                        f'Package node "{package_node_id}" has duplicated internal edge "{edge_id}"'
                    ),
                    node_id=package_node_id,
                    edge_id=edge_id,
                    path=[*path_prefix, "internals", "edges", edge_id],
                )
            )

    internal_node_ids = {internal_node.id for internal_node in node.internals.nodes}
    package_aliases = tuple(dict.fromkeys((node.id, package_node_id)))
    for edge in node.internals.edges:
        if edge.source not in internal_node_ids:
            errors.append(
                WorkflowCompileError(
                    code="missing_internal_edge_source",
                    message=(
                        f'Package node "{package_node_id}" internal edge "{edge.id}" '
                        f'references missing source "{edge.source}"'
                    ),
                    node_id=package_node_id,
                    edge_id=edge.id,
                    path=[*path_prefix, "internals", "edges", edge.id, "source"],
                )
            )
        if edge.target not in internal_node_ids:
            errors.append(
                WorkflowCompileError(
                    code="missing_internal_edge_target",
                    message=(
                        f'Package node "{package_node_id}" internal edge "{edge.id}" '
                        f'references missing target "{edge.target}"'
                    ),
                    node_id=package_node_id,
                    edge_id=edge.id,
                    path=[*path_prefix, "internals", "edges", edge.id, "target"],
                )
            )

    if _is_managed_runtime_package(node):
        return errors

    for internal_node in node.internals.nodes:
        internal_path_prefix = [
            *path_prefix,
            "internals",
            "nodes",
            internal_node.id,
        ]
        internal_node_path = (*node_path, internal_node.id)
        internal_node_id = _node_path_id(internal_node_path)
        if len(internal_node_path) > MAX_NODE_PATH_DEPTH:
            errors.append(
                WorkflowCompileError(
                    code="node_path_depth_exceeded",
                    message=(
                        f'Workflow node "{internal_node_id}" exceeds the maximum '
                        f"nesting depth of {MAX_NODE_PATH_DEPTH}"
                    ),
                    node_id=internal_node_id,
                    path=internal_path_prefix,
                )
            )
            continue

        if (
            not _is_structural_container(internal_node)
            and internal_node.adapter
            and internal_node.adapter not in adapter_by_id
        ):
            errors.append(
                WorkflowCompileError(
                    code="missing_adapter_binding",
                    message=(
                        f'Package node "{package_node_id}" internal node '
                        f'"{internal_node.id}" references missing adapter '
                        f'"{internal_node.adapter}"'
                    ),
                    node_id=internal_node_id,
                    path=[*internal_path_prefix, "adapter"],
                )
            )
        elif (
            not _is_structural_container(internal_node)
            and _requires_adapter(internal_node)
            and not internal_node.adapter
        ):
            errors.append(
                WorkflowCompileError(
                    code="missing_adapter_binding",
                    message=(
                        f'Package node "{package_node_id}" internal node '
                        f'"{internal_node.id}" requires an adapter binding'
                    ),
                    node_id=internal_node_id,
                    path=[*internal_path_prefix, "adapter"],
                )
            )

        errors.extend(
            _validate_node_origin(
                internal_node,
                internal_path_prefix,
            )
        )
        errors.extend(_validate_data_operator_node(internal_node, internal_path_prefix))
        errors.extend(
            _validate_node_capability_gaps(
                internal_node,
                internal_path_prefix,
            )
        )
        if _is_structural_container(internal_node):
            errors.extend(
                _validate_package_internals(
                    internal_node,
                    adapter_by_id,
                    node_path=internal_node_path,
                    path_prefix=internal_path_prefix,
                )
            )

    if node.parameterInterface:
        for field in node.parameterInterface.fields:
            binding_node_id = _resolve_internal_binding_id(
                field.binding.nodeId,
                internal_node_ids,
                package_aliases=package_aliases,
            )
            if binding_node_id is None:
                errors.append(
                    WorkflowCompileError(
                        code="invalid_parameter_binding",
                        message=(
                            f'Package node "{package_node_id}" public parameter '
                            f'"{field.id}" binds missing internal node '
                            f'"{field.binding.nodeId}"'
                        ),
                        node_id=package_node_id,
                        path=[
                            *path_prefix,
                            "parameterInterface",
                            "fields",
                            field.id,
                            "binding",
                        ],
                    )
                )

    internal_edge_path = [*path_prefix, "internals", "edges"]
    errors.extend(
        _validate_edge_mappings(
            node.internals.edges,
            path_prefix=internal_edge_path,
        )
    )
    errors.extend(
        _validate_visible_merge_nodes(
            node.internals.nodes,
            node.internals.edges,
            path_prefix=internal_edge_path,
        )
    )
    errors.extend(
        _validate_typed_edges(
            node.internals.nodes,
            node.internals.edges,
            path_prefix=internal_edge_path,
        )
    )
    errors.extend(
        _cycle_errors_for_nodes(
            package_node_id,
            node.internals.nodes,
            node.internals.edges,
            path_prefix=path_prefix,
        )
    )
    return errors


def _cycle_errors_for_nodes(
    package_node_id: str,
    nodes: list[WorkflowProjectNode],
    edges: list,
    *,
    path_prefix: list[str],
) -> list[WorkflowCompileError]:
    node_ids = [node.id for node in nodes]
    indegree = {node_id: 0 for node_id in node_ids}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.source in indegree and edge.target in indegree:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

    queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
    visited: set[str] = set()
    while queue:
        node_id = queue.popleft()
        visited.add(node_id)
        for target in adjacency[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    return [
        WorkflowCompileError(
            code="cycle",
            message=(
                f'Package node "{package_node_id}" internal graph contains '
                f'a cycle at node "{node_id}"'
            ),
            node_id=package_node_id,
            path=[*path_prefix, "internals", "nodes", node_id],
        )
        for node_id in sorted(set(node_ids) - visited)
    ]


def _compile_node(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
    depends_on: list[str],
    *,
    id_override: str | None = None,
    package: dict[str, object] | None = None,
    runtime: dict[str, object] | None = None,
) -> CompiledWorkflowNode:
    node_id = id_override or node.id
    runtime_metadata: dict[str, object] = {
        "node_id": node_id,
        "authoring_node_id": node.id,
        "status_anchor": node_id,
        "capability": node.capability,
        "dispatch": "preview",
        "origin": resolve_node_origin(node).model_dump(exclude_none=True),
        **({"proposal_state": node.proposalState} if node.proposalState is not None else {}),
    }
    if runtime:
        runtime_metadata.update(runtime)
    source_label = (node.ui or {}).get("label")
    if node.kind == "source" and isinstance(source_label, str) and source_label.strip():
        runtime_metadata["display_name"] = source_label.strip()
    if _is_structural_container(node):
        runtime_metadata.update({"structural": True, "executable": False})
    else:
        runtime_metadata.update(resolve_runtime_metadata(node, adapter, node_id=node_id))

    return CompiledWorkflowNode(
        id=node_id,
        kind=node.kind,
        capability=node.capability,
        params=_compiled_node_params(node),
        depends_on=depends_on,
        adapter=(
            CompiledWorkflowAdapterBinding(
                id=adapter.id,
                type=adapter.type,
                provider=adapter.provider,
                mode=adapter.mode,
                config=adapter.config,
            )
            if adapter
            else None
        ),
        sourceAnchor=node.sourceAnchor,
        runArtifact=node.runArtifact,
        package=package,
        runtime=runtime_metadata,
    )


def _widen_merge_fan_in(plan: PlanGraph) -> None:
    """Grow merge nodes' declared inputs to cover every in<N> edge that
    actually targets them. The static contract names only in1/in2, but the
    demand assembler fans in one branch per matched catalog source."""
    nodes_by_id = {node.id: node for node in plan.nodes}
    for edge in plan.edges:
        node = nodes_by_id.get(edge.target_node)
        if node is None or node.kind != "merge":
            continue
        port = edge.target_port
        if not port or not re.fullmatch(r"in\d+", port):
            continue
        if any(existing.name == port for existing in node.inputs):
            continue
        node.inputs.append(PlanPort(name=port, type="recordCandidate[]"))


def _to_plan_ir(project: WorkflowProject) -> PlanGraph:
    fan_in_by_node: dict[str, int] = defaultdict(int)
    for edge in project.edges:
        if edge.targetPort and re.fullmatch(r"in\d+", edge.targetPort):
            fan_in_by_node[edge.target] += 1
    return PlanGraph(
        name=project.name,
        draft=True,
        nodes=[
            _to_plan_node(node, merge_fan_in=fan_in_by_node.get(node.id)) for node in project.nodes
        ],
        edges=[
            PlanEdge(
                id=edge.id,
                source_node=edge.source,
                source_port=edge.sourcePort or "records",
                target_node=edge.target,
                target_port=edge.targetPort or "records",
            )
            for edge in project.edges
        ],
    )


def _to_plan_node(
    node: WorkflowProjectNode,
    id_override: str | None = None,
    *,
    node_path: tuple[str, ...] | None = None,
    merge_fan_in: int | None = None,
) -> PlanNode:
    kind: Literal["source", "transform", "merge", "sink"]
    if node.kind in {"schedule", "source"}:
        kind = "source"
    elif node.kind in {"notify", "inbox", "sink"}:
        kind = "sink"
    elif node.kind == "flow" and node.capability == "merge":
        kind = "merge"
    else:
        kind = "transform"
    inputs, outputs = _plan_ports_for_node(node, kind, merge_fan_in=merge_fan_in)
    return PlanNode(
        id=id_override or node.id,
        kind=kind,
        type=f"workflow.{node.kind}.{node.capability}",
        label=node.id,
        params={
            **_compiled_node_params(node),
            "workflow": {
                "kind": node.kind,
                "capability": node.capability,
                "adapter": node.adapter,
                "node_path": list(node_path or (node.id,)),
                "structural": _is_structural_container(node),
                "executable": not _is_structural_container(node),
            },
        },
        inputs=inputs,
        outputs=outputs,
        source_id=None,
        draft=kind == "source",
    )


def _compiled_node_params(node: WorkflowProjectNode) -> dict[str, object]:
    params = dict(node.params)
    if not _is_managed_runtime_package(node):
        return params
    compat_runtime = params.get("compatRuntime")
    if isinstance(compat_runtime, dict):
        safe_runtime = dict(compat_runtime)
        safe_runtime.pop("sourceContent", None)
        safe_runtime.pop("ephemeralGrants", None)
        params["compatRuntime"] = safe_runtime
    return params


def _plan_ports_for_node(
    node: WorkflowProjectNode,
    kind: Literal["source", "transform", "merge", "sink"],
    *,
    merge_fan_in: int | None = None,
) -> tuple[list[PlanPort], list[PlanPort]]:
    ui = node.ui or {}
    catalog_id = ui.get("catalogId")
    node_library_id = ui.get("primitiveId") or catalog_id
    if node_library_id in {
        "primitive.core.webhook-trigger",
        "primitive.ops.trigger-webhook",
    }:
        return ([], [PlanPort(name="request", type="webhookRequest")])
    if catalog_id == "intelligence.input.collection-need":
        return (
            [PlanPort(name="in", type="collectionNeed")],
            [PlanPort(name="patch", type="workflowPatch")],
        )
    if catalog_id == "intelligence.flow.merge":
        fan_in = max(2, merge_fan_in or 2)
        return (
            [
                PlanPort(name=f"in{index}", type="recordCandidate[]")
                for index in range(1, fan_in + 1)
            ],
            [PlanPort(name="out", type="recordCandidate[]")],
        )
    if catalog_id == "intelligence.control.record-acceptance":
        return (
            [PlanPort(name="candidates", type="recordCandidate[]")],
            [PlanPort(name="records", type="record[]")],
        )
    if catalog_id == "intelligence.sink.records":
        return (
            [PlanPort(name="records", type="record[]")],
            [PlanPort(name="stored", type="storedItems[]")],
        )
    if catalog_id == "intelligence.sink.feishu-bitable":
        return (
            [PlanPort(name="records", type="storedItems[]")],
            [PlanPort(name="delivery", type="deliveryAttempt[]")],
        )
    declared_contract = _node_port_contracts(node)
    if declared_contract is not None:
        inputs, outputs = declared_contract
        return (
            [PlanPort(name=port.id, type=port.type) for port in inputs],
            [PlanPort(name=port.id, type=port.type) for port in outputs],
        )
    return (
        [PlanPort(name="records", type="any")],
        [] if kind == "sink" else [PlanPort(name="records", type="any")],
    )
