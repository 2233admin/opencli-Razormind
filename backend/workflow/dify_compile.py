"""Managed Dify compile preflight through the Graphon compatibility boundary."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.dify_compat import DifyInspection
from backend.schemas.workflow import (
    WorkflowCompileResponse,
    WorkflowProject,
    WorkflowProjectNode,
)
from backend.schemas.workflow_compile import WorkflowCompileError
from backend.workflow.compiler import compile_workflow_project
from backend.workflow.dify_grants import resolve_dify_ephemeral_grants
from backend.workflow.dify_graphon_client import (
    DIFY_GRAPHON_COMMIT,
    DIFY_GRAPHON_NAME,
    DIFY_GRAPHON_VERSION,
    DifyGraphonClient,
    DifyGraphonUnavailableError,
)


async def compile_managed_dify_workflow_project(
    project: WorkflowProject,
    *,
    graphon_client: DifyGraphonClient,
    session: AsyncSession | None = None,
) -> WorkflowCompileResponse:
    """Refresh managed Dify inspections, then invoke the canonical compiler."""

    nodes = list(project.nodes)
    errors: list[WorkflowCompileError] = []
    for index, node in enumerate(nodes):
        if not is_managed_dify_package(node):
            continue
        refreshed, node_errors = await _refresh_package_inspection(
            project,
            node,
            graphon_client=graphon_client,
            grants=await resolve_dify_ephemeral_grants(node, session=session),
        )
        nodes[index] = refreshed
        errors.extend(node_errors)

    if errors:
        return WorkflowCompileResponse(valid=False, errors=errors, plan=None)
    return compile_workflow_project(project.model_copy(update={"nodes": nodes}))


def is_managed_dify_package(node: WorkflowProjectNode) -> bool:
    compat_runtime = _record(node.params.get("compatRuntime"))
    return (
        node.params.get("packageFormat") == "dify"
        and node.params.get("packageExecution") == "managed"
        and compat_runtime.get("engine") == "graphon"
        and node.internals is not None
    )


async def _refresh_package_inspection(
    project: WorkflowProject,
    node: WorkflowProjectNode,
    *,
    graphon_client: DifyGraphonClient,
    grants: dict[str, Any],
) -> tuple[WorkflowProjectNode, list[WorkflowCompileError]]:
    compat_runtime = _record(node.params.get("compatRuntime"))
    source_content_value = compat_runtime.get("sourceContent")
    source_content = (
        source_content_value
        if isinstance(source_content_value, str) and source_content_value
        else None
    )
    source_sha256 = _read_string(compat_runtime.get("sourceSha256"))
    if not source_content or not source_sha256:
        return node, [
            _compile_error(
                node,
                "dify_source_digest_mismatch",
                "Managed Dify package is missing its canonical source or digest.",
            )
        ]
    actual_sha256 = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    if actual_sha256 != source_sha256:
        return node, [
            _compile_error(
                node,
                "dify_source_digest_mismatch",
                "Managed Dify package source does not match its canonical digest.",
            )
        ]

    execution_policy = {
        "allowNetwork": project.agentPermissions.canFetchNetwork,
        "allowedDomains": project.agentPermissions.allowedDomains,
        "allowCode": False,
        "allowTools": False,
    }
    try:
        inspect_kwargs: dict[str, Any] = {
            "source_content": source_content,
            "source_sha256": source_sha256,
            "policy": execution_policy,
        }
        if grants:
            inspect_kwargs["grants"] = grants
        inspection_value = await graphon_client.inspect(**inspect_kwargs)
    except DifyGraphonUnavailableError:
        return node, [
            _compile_error(
                node,
                "dify_graphon_unavailable",
                "The pinned Graphon compatibility runtime is unavailable.",
            )
        ]

    inspection = DifyInspection.model_validate(inspection_value)
    if (
        inspection.engine.name != DIFY_GRAPHON_NAME
        or inspection.engine.version != DIFY_GRAPHON_VERSION
        or inspection.engine.commit != DIFY_GRAPHON_COMMIT
    ):
        return node, [
            _compile_error(
                node,
                "dify_graphon_unavailable",
                "The compatibility runtime does not match the pinned Graphon build.",
            )
        ]
    app_mode = _read_string(node.params.get("appMode"))
    if inspection.app_mode != app_mode:
        return node, [
            _compile_error(
                node,
                "dify_graphon_unavailable",
                "The pinned Graphon runtime inspected a different Dify app mode.",
            )
        ]

    refreshed = _with_inspection(node, inspection, policy=execution_policy)
    errors = [
        WorkflowCompileError(
            code=blocker.code,
            message=blocker.message,
            node_id=blocker.node_id or node.id,
            path=["nodes", node.id, "params", "compatRuntime", "inspection"],
        )
        for blocker in inspection.blockers
    ]
    if inspection.load_status != "ready" and not errors:
        errors.append(
            _compile_error(
                node,
                inspection.load_reason or "dify_runtime_failed",
                "Graphon inspection did not mark the managed Dify workflow as ready.",
            )
        )
    return refreshed, errors


def _with_inspection(
    node: WorkflowProjectNode,
    inspection: DifyInspection,
    *,
    policy: dict[str, Any],
) -> WorkflowProjectNode:
    params = deepcopy(node.params)
    params["compatRuntime"] = {
        **_record(params.get("compatRuntime")),
        "inspection": inspection.model_dump(mode="json", by_alias=True),
        "executionPolicy": policy,
    }
    return node.model_copy(update={"params": params})


def _compile_error(
    node: WorkflowProjectNode,
    code: str,
    message: str,
) -> WorkflowCompileError:
    return WorkflowCompileError(
        code=code,
        message=message,
        node_id=node.id,
        path=["nodes", node.id, "params", "compatRuntime"],
    )


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
