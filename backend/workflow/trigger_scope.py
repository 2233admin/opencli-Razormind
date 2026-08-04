"""Source-level trigger scope selection for Studio workflow execution.

The selector operates on a raw ``WorkflowProject`` (before authoritative
compilation) so Run, Studio validation, and immutable publication can share
one trigger-reachable subgraph definition. It reuses the canonical origin /
runtime binding resolvers and intentionally returns a small, pure data
result — no I/O, no global state, no authoritative execution.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.schemas.workflow import (
    WorkflowAdapterBinding,
    WorkflowCompileError,
    WorkflowProject,
    WorkflowProjectEdge,
    WorkflowProjectNode,
)
from backend.workflow.node_registry import resolve_node_origin
from backend.workflow.runtime_registry import (
    SCHEDULE_TRIGGER_BINDING_ID,
    WEBHOOK_TRIGGER_BINDING_ID,
    resolve_runtime_metadata,
)


@dataclass
class TriggerScopeResult:
    """Trigger-scoped execution graph returned to Run and validation callers."""

    project: WorkflowProject
    active_node_ids: set[str]
    parked_node_ids: list[str]
    trigger_node_id: str | None
    trigger_kind: str | None
    selection_error: WorkflowCompileError | None = None
    selected_kind: str | None = None


@dataclass
class ActiveUnionResult:
    """Active union of every supported trigger-reachable component."""

    active_node_ids: set[str]
    parked_node_ids: list[str]
    trigger_node_ids: list[str]
    has_supported_trigger: bool


_NORMALIZED_TRIGGER_KIND = {"manual", "schedule", "webhook"}


def _normalize_requested_kind(trigger_kind: str | None) -> str:
    if trigger_kind == "ai":
        return "manual"
    if trigger_kind in _NORMALIZED_TRIGGER_KIND:
        return trigger_kind
    return "manual"


def _resolve_trigger_kind(
    node: WorkflowProjectNode,
    adapter: WorkflowAdapterBinding | None,
) -> str | None:
    """Reuse the canonical binding metadata to recognise a trigger node.

    Native nodes are skipped first so ``resolve_runtime_metadata`` reaches the
    dedicated ``_is_webhook_trigger`` / ``_is_schedule_trigger`` branches. The
    only binding ids that map to a supported trigger kind are webhook
    (``workflow.trigger.webhook_input``) and schedule
    (``workflow.trigger.schedule_tick``) — the schedule branch is then split
    between ``manual`` and ``schedule`` exactly like the compiled selector.
    """

    origin = resolve_node_origin(node)
    if origin.kind == "legacy" and origin.notes:
        return None

    metadata = resolve_runtime_metadata(node, adapter)
    binding = metadata.get("binding") if isinstance(metadata, dict) else None
    binding_id = binding.get("binding_id") if isinstance(binding, dict) else None
    if binding_id == WEBHOOK_TRIGGER_BINDING_ID:
        return "webhook"
    if binding_id == SCHEDULE_TRIGGER_BINDING_ID:
        builder = node.params.get("builder") if isinstance(node.params, dict) else None
        node_type = (
            builder.get("nodeType") if isinstance(builder, dict) else None
        )
        mode = node.params.get("mode") if isinstance(node.params, dict) else None
        if node_type == "manual-trigger" or mode == "manual":
            return "manual"
        return "schedule"
    return None


def _trigger_candidates(
    nodes: list[WorkflowProjectNode],
    *,
    adapters: dict[str, WorkflowAdapterBinding],
) -> list[tuple[WorkflowProjectNode, str]]:
    pairs: list[tuple[WorkflowProjectNode, str]] = []
    for node in nodes:
        adapter = adapters.get(node.adapter) if node.adapter else None
        kind = _resolve_trigger_kind(node, adapter)
        if kind is not None:
            pairs.append((node, kind))
    return pairs


def _downstream_active_ids(
    *,
    trigger_ids: list[str],
    nodes: list[WorkflowProjectNode],
    edges: list[WorkflowProjectEdge],
) -> set[str]:
    adjacency: dict[str, list[str]] = {node.id: [] for node in nodes}
    edge_target_index: dict[str, list[str]] = {}
    for edge in edges:
        if edge.source not in adjacency:
            adjacency[edge.source] = []
        adjacency.setdefault(edge.target, [])
        adjacency[edge.source].append(edge.target)
        edge_target_index.setdefault(edge.target, []).append(edge.source)

    active: set[str] = set()
    pending: list[str] = list(trigger_ids)
    while pending:
        current = pending.pop()
        if current in active:
            continue
        active.add(current)
        for downstream in adjacency.get(current, []):
            if downstream not in active:
                pending.append(downstream)
    return active


def _external_workflow_ids(nodes: list[WorkflowProjectNode]) -> set[str]:
    ids: set[str] = set()
    for node in nodes:
        params = node.params if isinstance(node.params, dict) else {}
        if isinstance(params.get("externalWorkflow"), dict):
            ids.add(node.id)
    return ids


def _parked_ids(
    *,
    nodes: list[WorkflowProjectNode],
    active_ids: set[str],
    external_ids: set[str],
) -> list[str]:
    parked: list[str] = []
    for node in nodes:
        if node.id in active_ids or node.id in external_ids:
            continue
        parked.append(node.id)
    return parked


def _scoped_edges(
    *,
    edges: list[WorkflowProjectEdge],
    active_ids: set[str],
) -> list[WorkflowProjectEdge]:
    scoped: list[WorkflowProjectEdge] = []
    for edge in edges:
        if edge.source in active_ids and edge.target in active_ids:
            scoped.append(
                WorkflowProjectEdge(
                    id=edge.id,
                    source=edge.source,
                    target=edge.target,
                    sourcePort=edge.sourcePort,
                    targetPort=edge.targetPort,
                    label=edge.label,
                    condition=edge.condition,
                    semantic=edge.semantic,
                    weight=edge.weight,
                    contractId=edge.contractId,
                    proposalState=edge.proposalState,
                    ui=edge.ui,
                )
            )
    return scoped


def scoped_project(
    *,
    project: WorkflowProject,
    active_ids: set[str],
    external_ids: set[str],
) -> WorkflowProject:
    include = active_ids | external_ids
    scoped_nodes = [node for node in project.nodes if node.id in include]
    scoped_node_ids = {node.id for node in scoped_nodes}
    scoped_edges = _scoped_edges(edges=project.edges, active_ids=scoped_node_ids)
    return WorkflowProject(
        id=project.id,
        name=project.name,
        profile=project.profile,
        version=project.version,
        nodes=scoped_nodes,
        edges=scoped_edges,
        settings=project.settings,
        adapters=list(project.adapters),
        agentPermissions=project.agentPermissions,
    )


def has_supported_triggers(project: WorkflowProject) -> bool:
    """Return True when the authored graph contains at least one supported
    trigger entry — manual, schedule, or webhook.  Callers use this to
    decide whether trigger-scoped selection applies without importing
    the private ``_trigger_candidates``."""
    return bool(
        _trigger_candidates(
            project.nodes,
            adapters={a.id: a for a in project.adapters},
        )
    )


def select_trigger_scope(
    project: WorkflowProject,
    *,
    trigger_kind: str | None = None,
    trigger_node_id: str | None = None,
) -> TriggerScopeResult:
    """Pick exactly one supported trigger and its downstream subgraph.

    ``trigger_kind`` accepts the runtime literal ``"ai"`` and normalises it to
    ``"manual"`` before matching. ``trigger_node_id`` (when supplied) must
    exist, be a supported trigger, and match the normalised request kind.
    Without an id, exactly one matching trigger is required; zero is a
    mismatch and more than one is ambiguous. The scoped project always
    preserves the existing governed external-workflow inclusion behavior
    (an empty ``externalWorkflow`` dictionary counts).
    """

    adapters_by_id = {adapter.id: adapter for adapter in project.adapters}
    candidates = _trigger_candidates(project.nodes, adapters=adapters_by_id)

    requested_kind = _normalize_requested_kind(trigger_kind)
    selection_error: WorkflowCompileError | None = None
    selected: WorkflowProjectNode | None = None

    if trigger_node_id is not None:
        match = next(
            (
                (node, kind)
                for node, kind in candidates
                if node.id == trigger_node_id
            ),
            None,
        )
        if match is None:
            selection_error = WorkflowCompileError(
                code="workflow_trigger_not_found",
                message=f'Workflow trigger node "{trigger_node_id}" was not found.',
                node_id=trigger_node_id,
                path=["trigger", "triggerNodeId"],
            )
        else:
            node, kind = match
            if kind != requested_kind:
                selection_error = WorkflowCompileError(
                    code="workflow_trigger_kind_mismatch",
                    message=(
                        f'Workflow trigger node "{trigger_node_id}" is "{kind}", '
                        f'not "{trigger_kind or requested_kind}".'
                    ),
                    node_id=trigger_node_id,
                    path=["trigger", "kind"],
                )
            else:
                selected = node
    else:
        matches = [
            node for node, kind in candidates if kind == requested_kind
        ]
        if len(matches) == 1:
            selected = matches[0]
        elif len(matches) > 1:
            selection_error = WorkflowCompileError(
                code="workflow_trigger_ambiguous",
                message=(
                    f'Workflow has multiple "{trigger_kind or requested_kind}" '
                    "trigger entries; triggerNodeId is required."
                ),
                path=["trigger", "triggerNodeId"],
            )
        elif not candidates:
            selection_error = WorkflowCompileError(
                code="workflow_trigger_not_found",
                message=(
                    "Workflow has no supported trigger entry."
                ),
                path=["trigger", "kind"],
            )
        else:
            selection_error = WorkflowCompileError(
                code="workflow_trigger_kind_mismatch",
                message=(
                    f'Workflow has no "{trigger_kind or requested_kind}" '
                    "trigger entry."
                ),
                path=["trigger", "kind"],
            )

    if selected is None:
        return TriggerScopeResult(
            project=project,
            active_node_ids=set(),
            parked_node_ids=[node.id for node in project.nodes],
            trigger_node_id=trigger_node_id,
            trigger_kind=requested_kind,
            selection_error=selection_error,
            selected_kind=None,
        )

    active_ids = _downstream_active_ids(
        trigger_ids=[selected.id],
        nodes=project.nodes,
        edges=project.edges,
    )
    external_ids = _external_workflow_ids(project.nodes)
    parked_ids = _parked_ids(
        nodes=project.nodes,
        active_ids=active_ids,
        external_ids=external_ids,
    )
    scoped = scoped_project(
        project=project,
        active_ids=active_ids,
        external_ids=external_ids,
    )
    return TriggerScopeResult(
        project=scoped,
        active_node_ids=active_ids,
        parked_node_ids=parked_ids,
        trigger_node_id=selected.id,
        trigger_kind=requested_kind,
        selection_error=None,
        selected_kind=requested_kind,
    )


def select_active_union(project: WorkflowProject) -> ActiveUnionResult:
    """Compute the union of every supported trigger-reachable component.

    Studio validation calls this so the immutable graph stored for the
    validation row contains exactly the executable authority. Parked
    canvas nodes remain in the editable draft but never enter the
    compiled or persisted graph.
    """

    adapters_by_id = {adapter.id: adapter for adapter in project.adapters}
    candidates = _trigger_candidates(project.nodes, adapters=adapters_by_id)
    trigger_ids = [node.id for node, _kind in candidates]
    active_ids = _downstream_active_ids(
        trigger_ids=trigger_ids,
        nodes=project.nodes,
        edges=project.edges,
    )
    external_ids = _external_workflow_ids(project.nodes)
    parked_ids = _parked_ids(
        nodes=project.nodes,
        active_ids=active_ids,
        external_ids=external_ids,
    )
    return ActiveUnionResult(
        active_node_ids=active_ids,
        parked_node_ids=parked_ids,
        trigger_node_ids=trigger_ids,
        has_supported_trigger=bool(trigger_ids),
    )