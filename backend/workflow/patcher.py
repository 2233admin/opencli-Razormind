"""Apply structured AI WorkflowProject patch operations."""

from __future__ import annotations

from backend.schemas.workflow import (
    WorkflowMissingCapability,
    WorkflowPackageInternals,
    WorkflowPatchOperation,
    WorkflowPatchPreview,
    WorkflowPatchResponse,
    WorkflowProject,
)
from backend.schemas.workflow_compile import WorkflowCompileError
from backend.workflow.compiler import compile_workflow_project
from backend.workflow.node_registry import forbidden_node_definition_keys, resolve_node_origin
from backend.workflow.opencli_adapter_nodes import (
    OpenCLIAdapterNodeMaterializationError,
    materialize_opencli_adapter_node,
)


def preview_workflow_patch(
    project: WorkflowProject,
    operations: list[WorkflowPatchOperation],
) -> WorkflowPatchResponse:
    """Apply reviewable patch operations without persisting or dispatching."""

    patched = project.model_copy(deep=True)
    errors: list[WorkflowCompileError] = []
    missing: list[WorkflowMissingCapability] = []
    accepted: list[dict] = []

    for index, operation in enumerate(operations):
        path = ["operations", str(index)]
        if operation.op == "add_node":
            if operation.node is None:
                errors.append(_operation_error(operation.op, "add_node requires node", path))
                continue
            node_errors = _validate_added_node(operation.node, path)
            if node_errors:
                errors.extend(node_errors)
                continue
            if any(node.id == operation.node.id for node in patched.nodes):
                errors.append(
                    WorkflowCompileError(
                        code="duplicate_node_id",
                        message=f'Workflow node id "{operation.node.id}" is duplicated',
                        node_id=operation.node.id,
                        path=[*path, "node", "id"],
                    )
                )
                continue
            patched.nodes.append(operation.node)
            accepted.append(operation.model_dump(exclude_none=True))
            continue

        if operation.op == "update_parameters":
            if not operation.nodeId:
                errors.append(
                    _operation_error(operation.op, "update_parameters requires nodeId", path)
                )
                continue
            node = _find_node(patched, operation.nodeId)
            if node is None:
                errors.append(
                    WorkflowCompileError(
                        code="missing_patch_node",
                        message=(
                            f'Patch operation update_parameters references missing node '
                            f'"{operation.nodeId}"'
                        ),
                        node_id=operation.nodeId,
                        path=[*path, "nodeId"],
                    )
                )
                continue
            node.params.update(operation.params)
            accepted.append(operation.model_dump(exclude_none=True))
            continue

        if operation.op == "add_adapter":
            if operation.adapter is None:
                errors.append(_operation_error(operation.op, "add_adapter requires adapter", path))
                continue
            if any(adapter.id == operation.adapter.id for adapter in patched.adapters):
                errors.append(
                    WorkflowCompileError(
                        code="duplicate_adapter_id",
                        message=f'Workflow adapter id "{operation.adapter.id}" is duplicated',
                        path=[*path, "adapter", "id"],
                    )
                )
                continue
            patched.adapters.append(operation.adapter)
            accepted.append(operation.model_dump(exclude_none=True))
            continue

        if operation.op == "materialize_opencli_adapter":
            if not operation.adapterNodeId:
                errors.append(
                    _operation_error(
                        operation.op,
                        "materialize_opencli_adapter requires adapterNodeId",
                        path,
                    )
                )
                continue
            try:
                node, adapter = materialize_opencli_adapter_node(
                    operation.adapterNodeId,
                    node_id=operation.nodeId,
                    params=operation.params,
                )
            except OpenCLIAdapterNodeMaterializationError as exc:
                errors.append(
                    WorkflowCompileError(
                        code=exc.code,
                        message=exc.message,
                        path=[*path, "adapterNodeId"],
                    )
                )
                if exc.missing_params:
                    reason = (
                        "Missing OpenCLI adapter params: "
                        f"{', '.join(exc.missing_params)}"
                    )
                    missing.append(
                        WorkflowMissingCapability(
                            capability="opencli.adapter.params",
                            reason=reason,
                            n8n_search_hint=operation.adapterNodeId,
                        )
                    )
                continue
            if any(existing.id == node.id for existing in patched.nodes):
                errors.append(
                    WorkflowCompileError(
                        code="duplicate_node_id",
                        message=f'Workflow node id "{node.id}" is duplicated',
                        node_id=node.id,
                        path=[*path, "nodeId"],
                    )
                )
                continue
            node_errors = _validate_added_node(node, path)
            if node_errors:
                errors.extend(node_errors)
                continue
            if adapter is not None:
                existing_adapter = next(
                    (item for item in patched.adapters if item.id == adapter.id),
                    None,
                )
                if existing_adapter is None:
                    patched.adapters.append(adapter)
                elif existing_adapter.model_dump() != adapter.model_dump():
                    errors.append(
                        WorkflowCompileError(
                            code="duplicate_adapter_id",
                            message=(
                                f'Workflow adapter id "{adapter.id}" already exists '
                                "with different configuration"
                            ),
                            path=[*path, "adapterNodeId"],
                        )
                    )
                    continue
            patched.nodes.append(node)
            accepted.append(operation.model_dump(exclude_none=True))
            continue

        if operation.op == "connect_nodes":
            if operation.edge is None:
                errors.append(_operation_error(operation.op, "connect_nodes requires edge", path))
                continue
            patched.edges.append(operation.edge)
            accepted.append(operation.model_dump(exclude_none=True))
            continue

        if operation.op == "request_missing_capability":
            if not operation.capability:
                errors.append(
                    _operation_error(
                        operation.op,
                        "request_missing_capability requires capability",
                        path,
                    )
                )
                continue
            missing.append(
                WorkflowMissingCapability(
                    capability=operation.capability,
                    reason=operation.reason,
                    n8n_search_hint=operation.capability,
                )
            )
            accepted.append(operation.model_dump(exclude_none=True))
            continue

        if operation.op == "package_nodes":
            if operation.packageNode is None:
                errors.append(
                    _operation_error(operation.op, "package_nodes requires packageNode", path)
                )
                continue
            if not operation.internalNodeIds:
                errors.append(
                    _operation_error(
                        operation.op,
                        "package_nodes requires internalNodeIds",
                        path,
                    )
                )
                continue
            package_errors = _validate_added_node(operation.packageNode, path)
            if package_errors:
                errors.extend(package_errors)
                continue
            package_result = _package_nodes(patched, operation, path)
            if isinstance(package_result, list):
                errors.extend(package_result)
                continue
            patched = package_result
            accepted.append(operation.model_dump(exclude_none=True))
            continue

        errors.append(
            _operation_error(operation.op, f"unsupported patch operation {operation.op}", path)
        )

    if errors:
        return WorkflowPatchResponse(
            valid=False,
            errors=errors,
            missing_capabilities=missing,
            patch=WorkflowPatchPreview(operations=accepted),
            project=None,
            compile=None,
        )

    compile_result = compile_workflow_project(patched)
    return WorkflowPatchResponse(
        valid=compile_result.valid,
        errors=compile_result.errors,
        missing_capabilities=missing,
        patch=WorkflowPatchPreview(operations=accepted),
        project=patched if compile_result.valid else None,
        compile=compile_result,
    )


def _validate_added_node(node, path: list[str]) -> list[WorkflowCompileError]:
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
                path=[*path, "node", *key.split(".")],
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
                path=[*path, "node", "ui"],
            )
        )
    return errors


def _find_node(project: WorkflowProject, node_id: str):
    parts = [part for part in node_id.split("::") if part]
    if not parts:
        return None
    node = next((candidate for candidate in project.nodes if candidate.id == parts[0]), None)
    for part in parts[1:]:
        if node is None or node.internals is None:
            return None
        node = next(
            (candidate for candidate in node.internals.nodes if candidate.id == part),
            None,
        )
    return node


def _package_nodes(
    project: WorkflowProject,
    operation: WorkflowPatchOperation,
    path: list[str],
) -> WorkflowProject | list[WorkflowCompileError]:
    assert operation.packageNode is not None
    selected_ids = set(operation.internalNodeIds)
    selected_nodes = [node for node in project.nodes if node.id in selected_ids]
    missing_ids = selected_ids - {node.id for node in selected_nodes}
    if missing_ids:
        return [
            WorkflowCompileError(
                code="missing_patch_node",
                message=f'package_nodes references missing node "{node_id}"',
                node_id=node_id,
                path=[*path, "internalNodeIds"],
            )
            for node_id in sorted(missing_ids)
        ]

    if operation.packageNode.id in {node.id for node in project.nodes}:
        return [
            WorkflowCompileError(
                code="duplicate_node_id",
                message=f'Workflow node id "{operation.packageNode.id}" is duplicated',
                node_id=operation.packageNode.id,
                path=[*path, "packageNode", "id"],
            )
        ]

    internal_edges = [
        edge
        for edge in project.edges
        if edge.source in selected_ids and edge.target in selected_ids
    ]
    remaining_nodes = [node for node in project.nodes if node.id not in selected_ids]
    remaining_edges = [
        edge
        for edge in project.edges
        if edge.source not in selected_ids and edge.target not in selected_ids
    ]
    locked = (
        operation.packageNode.topicCollapse.mode == "locked"
        if operation.packageNode.topicCollapse
        else None
    )
    package_node = operation.packageNode.model_copy(
        update={
            "internals": WorkflowPackageInternals(
                locked=locked,
                nodes=selected_nodes,
                edges=internal_edges,
            )
        }
    )
    return project.model_copy(
        update={"nodes": [*remaining_nodes, package_node], "edges": remaining_edges}
    )


def _operation_error(op: str, message: str, path: list[str]) -> WorkflowCompileError:
    return WorkflowCompileError(code="invalid_patch_operation", message=message, path=[*path, op])
