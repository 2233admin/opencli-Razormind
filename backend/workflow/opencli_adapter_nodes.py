"""OpenCLI adapter command manifest projected as node-capability manifests.

These are not all Canvas-visible nodes. The registry gives every OpenCLI
adapter command a stable manifest so business presets can materialize the
right Source Slot or Tool Capability later without hand-writing 1000+ nodes.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from collections import Counter
from functools import lru_cache
from typing import Any, Literal

from backend.opencli_runtime import resolve_opencli_bin
from backend.schemas.workflow import (
    WorkflowAdapterBinding,
    WorkflowOpenCLIAdapterNode,
    WorkflowOpenCLIAdapterNodeArg,
    WorkflowOpenCLIAdapterNodesResponse,
    WorkflowProjectNode,
)
from backend.workflow.runtime_registry import EXTERNAL_TOOL_BINDING_ID, OPENCLI_BINDING_ID

logger = logging.getLogger(__name__)

_OPENCLI_LIST_TIMEOUT_SECONDS = 15
_OPENCLI_CATALOG_LOCK = threading.Lock()
_OPENCLI_CATALOG_GENERATION = 0
_OPENCLI_SOURCE_CATALOG_ID = "intelligence.source.opencli-slot"
_EXTERNAL_TOOL_CATALOG_ID = "external.tool.capability"
_KNOWN_UNAVAILABLE_COMMANDS = {
    ("sse", "company-list"): "upstream_http_404",
}
_TOP_LEVEL_PARAM_KEYS = {
    "format",
    "mode",
    "sourceGroup",
}


class OpenCLIAdapterNodeMaterializationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        missing_params: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.missing_params = missing_params or []


def list_opencli_adapter_nodes(
    *,
    site: str | None = None,
    q: str | None = None,
    include_write: bool = True,
    access: Literal["read", "write"] | None = None,
    capability: Literal["fetch", "store"] | None = None,
    browser: bool | None = None,
    preset_kind: Literal["source_slot", "tool_capability"] | None = None,
    runtime_readiness: Literal[
        "source_slot_ready",
        "source_slot_requires_params",
        "tool_capability_review_required",
    ]
    | None = None,
    limit: int | None = None,
    refresh: bool = False,
) -> WorkflowOpenCLIAdapterNodesResponse:
    catalog = get_opencli_adapter_catalog(refresh=refresh)
    nodes = [_build_adapter_node(entry) for entry in catalog]
    if not include_write:
        nodes = [node for node in nodes if node.access != "write"]
    if access:
        nodes = [node for node in nodes if node.access == access]
    if capability:
        nodes = [node for node in nodes if node.capability == capability]
    if browser is not None:
        nodes = [node for node in nodes if node.browser is browser]
    if preset_kind:
        nodes = [node for node in nodes if node.presetKind == preset_kind]
    if runtime_readiness:
        nodes = [
            node
            for node in nodes
            if node.runtimeReadiness == runtime_readiness
        ]
    if site:
        site_lower = site.lower()
        nodes = [node for node in nodes if node.site.lower() == site_lower]
    if q:
        needle = q.lower()
        nodes = [
            node
            for node in nodes
            if needle in node.id.lower()
            or needle in node.label.lower()
            or needle in node.description.lower()
            or needle in node.site.lower()
            or needle in node.command.lower()
        ]
    nodes.sort(key=lambda node: (node.site, node.command))
    summary = _summarize_nodes(nodes)
    facets = _facet_nodes(nodes)
    total = len(nodes)
    if limit is not None:
        nodes = nodes[:limit]
    return WorkflowOpenCLIAdapterNodesResponse(
        total=total,
        summary=summary,
        facets=facets,
        nodes=nodes,
    )


def refresh_opencli_adapter_catalog() -> None:
    """Invalidate discovery so the next read reflects the selected OpenCLI binary."""

    global _OPENCLI_CATALOG_GENERATION
    with _OPENCLI_CATALOG_LOCK:
        _load_opencli_catalog.cache_clear()
        _OPENCLI_CATALOG_GENERATION += 1


def get_opencli_adapter_catalog(*, refresh: bool = False) -> tuple[dict[str, Any], ...]:
    """Return the shared adapter catalog used by nodes, presets, and runtime lookup."""

    global _OPENCLI_CATALOG_GENERATION
    requested_generation = _OPENCLI_CATALOG_GENERATION
    with _OPENCLI_CATALOG_LOCK:
        if refresh and requested_generation == _OPENCLI_CATALOG_GENERATION:
            _load_opencli_catalog.cache_clear()
            catalog = _load_opencli_catalog()
            _OPENCLI_CATALOG_GENERATION += 1
            return catalog
        return _load_opencli_catalog()


def get_opencli_adapter_node_summary() -> dict[str, Any]:
    catalog = get_opencli_adapter_catalog()
    return _summarize_nodes([_build_adapter_node(entry) for entry in catalog])


def resolve_opencli_adapter_node(adapter_node_id: str) -> WorkflowOpenCLIAdapterNode | None:
    return next(
        (
            node
            for node in (
                _build_adapter_node(entry)
                for entry in get_opencli_adapter_catalog()
            )
            if node.id == adapter_node_id
        ),
        None,
    )


def materialize_opencli_adapter_node(
    adapter_node_id: str,
    *,
    node_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[WorkflowProjectNode, WorkflowAdapterBinding | None]:
    adapter_node = resolve_opencli_adapter_node(adapter_node_id)
    if adapter_node is None:
        raise OpenCLIAdapterNodeMaterializationError(
            "unknown_opencli_adapter_node",
            f'OpenCLI adapter node "{adapter_node_id}" is not registered.',
        )
    if adapter_node.status in {"preview_only", "design_only"}:
        raise OpenCLIAdapterNodeMaterializationError(
            "opencli_adapter_node_unavailable",
            f'OpenCLI adapter node "{adapter_node_id}" is not currently runnable.',
        )
    materialized_params = _materialized_params(adapter_node, params or {})
    missing = _missing_required_args(adapter_node, materialized_params)
    if missing:
        raise OpenCLIAdapterNodeMaterializationError(
            "missing_opencli_adapter_params",
            (
                f'OpenCLI adapter node "{adapter_node_id}" requires params: '
                f"{', '.join(missing)}"
            ),
            missing_params=missing,
        )
    adapter = WorkflowAdapterBinding.model_validate(adapter_node.adapter)
    if adapter_node.access == "read":
        return (
            WorkflowProjectNode(
                id=node_id or _materialized_node_id("source", adapter_node),
                kind="source",
                capability="fetch",
                adapter=adapter.id,
                params={
                    **materialized_params,
                    "opencliAdapterNodeId": adapter_node.id,
                },
                ui={
                    "label": adapter_node.label,
                    "description": adapter_node.description,
                    "icon": "Globe",
                    "color": "var(--chart-4)",
                    "catalogId": _OPENCLI_SOURCE_CATALOG_ID,
                    "adapterNodeId": adapter_node.id,
                },
            ),
            adapter,
        )
    return (
        WorkflowProjectNode(
            id=node_id or _materialized_node_id("tool", adapter_node),
            kind="action",
            capability="store",
            adapter=adapter.id,
            params={
                **materialized_params,
                "opencliAdapterNodeId": adapter_node.id,
                "opencliAccess": adapter_node.access,
            },
            proposalState="proposed",
            ui={
                "label": adapter_node.label,
                "description": adapter_node.description,
                "icon": "Wrench",
                "color": "var(--chart-3)",
                "catalogId": _EXTERNAL_TOOL_CATALOG_ID,
                "adapterNodeId": adapter_node.id,
            },
        ),
        adapter,
    )


@lru_cache(maxsize=1)
def _load_opencli_catalog() -> tuple[dict[str, Any], ...]:
    bin_path = resolve_opencli_bin()
    try:
        result = subprocess.run(
            [bin_path, "list", "-f", "json"],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_OPENCLI_LIST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("opencli list -f json failed: %s", exc)
        return ()
    if result.returncode != 0:
        logger.warning(
            "opencli list -f json exited %s; stderr=%s",
            result.returncode,
            (result.stderr or "")[:500],
        )
        return ()
    raw = result.stdout or ""
    if not raw:
        logger.warning("opencli list -f json produced no stdout")
        return ()
    json_start = next((idx for idx, char in enumerate(raw) if char in ("[", "{")), None)
    if json_start is None:
        logger.warning("opencli list -f json produced no JSON")
        return ()
    try:
        data = json.loads(raw[json_start:])
    except Exception as exc:
        logger.warning("opencli list -f json output was not parseable JSON: %s", exc)
        return ()
    if not isinstance(data, list):
        return ()
    catalog: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not _read_string(entry.get("site")) or not _read_string(entry.get("name")):
            continue
        catalog.append(dict(entry))
    return tuple(catalog)


def _build_adapter_node(entry: dict[str, Any]) -> WorkflowOpenCLIAdapterNode:
    site = _read_string(entry.get("site")) or "unknown"
    command = _read_string(entry.get("name")) or site
    access = _read_string(entry.get("access")) or "read"
    browser = bool(entry.get("browser"))
    args = [_adapter_arg(arg) for arg in _read_args(entry.get("args"))]
    required_args = [arg.name for arg in args if arg.required]
    is_read = access == "read"
    unavailable_reason = _unavailability_reason(
        site=site,
        command=command,
        browser=browser,
        strategy=_read_string(entry.get("strategy")),
    )
    status = (
        "preview_only"
        if is_read and unavailable_reason
        else "runnable"
        if is_read and not required_args
        else "blocked"
    )
    catalog_id = _OPENCLI_SOURCE_CATALOG_ID if is_read else _EXTERNAL_TOOL_CATALOG_ID
    kind = "source" if is_read else "action"
    capability = "fetch" if is_read else "store"
    preset_kind = "source_slot" if is_read else "tool_capability"
    runtime_readiness = _materialization(access, required_args)
    node_id = f"opencli.adapter.{_safe_id(site)}.{_safe_id(command)}"
    positional_required = [arg.name for arg in args if arg.required and arg.positional]
    named_required = [arg.name for arg in args if arg.required and not arg.positional]
    params = {
        "site": site,
        "command": command,
        "format": entry.get("defaultFormat") or "json",
        "args": {},
    }
    if positional_required:
        params["positional_args"] = []
    return WorkflowOpenCLIAdapterNode(
        id=node_id,
        label=f"{site} · {command}",
        description=_read_string(entry.get("description")) or "",
        status=status,
        site=site,
        command=command,
        access=access,
        browser=browser,
        strategy=_read_string(entry.get("strategy")),
        domain=_read_string(entry.get("domain")),
        catalogId=catalog_id,
        kind=kind,
        capability=capability,
        presetKind=preset_kind,
        runtimeReadiness=runtime_readiness,
        requiredArgs=required_args,
        args=args,
        adapter={
            "id": f"opencli-{_safe_id(site)}",
            "type": "source" if is_read else "utility",
            "provider": "opencli",
            "mode": "live",
            "config": {"channel": "opencli"},
        },
        params=params,
        manifest={
            "schema": "opencli.adapter-node.v1",
            "opencli": {
                "site": site,
                "command": command,
                "access": access,
                "browser": browser,
                "strategy": _read_string(entry.get("strategy")),
                "domain": _read_string(entry.get("domain")),
                "columns": entry.get("columns") if isinstance(entry.get("columns"), list) else [],
                "example": _read_string(entry.get("example")),
            },
            "canvas": {
                "node": True,
                "runBlocked": status != "runnable" or runtime_readiness != "source_slot_ready",
                "catalogId": catalog_id,
                "materialization": runtime_readiness,
                "presetKind": preset_kind,
                "requiredArgs": required_args,
                "positionalRequiredArgs": positional_required,
                "namedRequiredArgs": named_required,
            },
            "availability": {
                "available": unavailable_reason is None,
                "reason": unavailable_reason,
            },
            "runtime": {
                "binding": OPENCLI_BINDING_ID if is_read else EXTERNAL_TOOL_BINDING_ID,
            },
            "trace": {
                "events": [
                    "queued",
                    "started",
                    "batch_ready" if is_read else "tool_call_started",
                    "partial",
                    "completed",
                ],
            },
        },
    )


def _unavailability_reason(
    *,
    site: str,
    command: str,
    browser: bool,
    strategy: str | None,
) -> str | None:
    known_reason = _KNOWN_UNAVAILABLE_COMMANDS.get((site.lower(), command.lower()))
    if known_reason:
        return known_reason
    if browser:
        return "browser_session_readiness_unverified"
    if strategy == "cookie":
        return "cookie_readiness_unverified"
    return None


def _adapter_arg(value: dict[str, Any]) -> WorkflowOpenCLIAdapterNodeArg:
    return WorkflowOpenCLIAdapterNodeArg(
        name=_read_string(value.get("name")) or "arg",
        type=_read_string(value.get("type")),
        required=bool(value.get("required")),
        valueRequired=bool(value.get("valueRequired")),
        positional=bool(value.get("positional")),
        choices=value.get("choices") if isinstance(value.get("choices"), list) else [],
        default=value.get("default"),
        help=_read_string(value.get("help")),
    )


def _materialization(access: str, required_args: list[str]) -> str:
    if access != "read":
        return "tool_capability_review_required"
    if required_args:
        return "source_slot_requires_params"
    return "source_slot_ready"


def _materialized_params(
    adapter_node: WorkflowOpenCLIAdapterNode,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    params = dict(adapter_node.params)
    params["args"] = dict(params.get("args") or {})
    if "positional_args" in params:
        params["positional_args"] = list(params.get("positional_args") or [])

    explicit_args = overrides.get("args")
    if isinstance(explicit_args, dict):
        params["args"].update(explicit_args)
    explicit_positionals = overrides.get("positional_args") or overrides.get("positionalArgs")
    if isinstance(explicit_positionals, list):
        params["positional_args"] = [str(value) for value in explicit_positionals]

    positional_arg_names = {arg.name for arg in adapter_node.args if arg.positional}
    known_arg_names = {arg.name for arg in adapter_node.args}
    for key, value in overrides.items():
        if key in {"args", "positional_args", "positionalArgs"}:
            continue
        if key in _TOP_LEVEL_PARAM_KEYS:
            params[key] = value
            continue
        if key in positional_arg_names:
            positionals = list(params.get("positional_args") or [])
            positionals.append(str(value))
            params["positional_args"] = positionals
            continue
        if key in known_arg_names:
            params["args"][key] = value
            continue
        params[key] = value
    if not params["args"]:
        params["args"] = {}
    return params


def _missing_required_args(
    adapter_node: WorkflowOpenCLIAdapterNode,
    params: dict[str, Any],
) -> list[str]:
    args = params.get("args") if isinstance(params.get("args"), dict) else {}
    positionals = (
        params.get("positional_args")
        if isinstance(params.get("positional_args"), list)
        else []
    )
    missing: list[str] = []
    positional_index = 0
    for arg in adapter_node.args:
        if not arg.required:
            continue
        if arg.positional:
            if positional_index >= len(positionals):
                missing.append(arg.name)
            positional_index += 1
        elif arg.name not in args or args[arg.name] in (None, ""):
            missing.append(arg.name)
    return missing


def _materialized_node_id(prefix: str, adapter_node: WorkflowOpenCLIAdapterNode) -> str:
    return f"{prefix}-{_safe_id(adapter_node.site)}-{_safe_id(adapter_node.command)}"


def _summarize_nodes(nodes: list[WorkflowOpenCLIAdapterNode]) -> dict[str, Any]:
    return {
        "total": len(nodes),
        "sites": len({node.site for node in nodes}),
        "access": dict(Counter(node.access for node in nodes)),
        "browser": dict(Counter("browser" if node.browser else "non_browser" for node in nodes)),
        "sourceSlotReady": sum(
            1
            for node in nodes
            if node.manifest.get("canvas", {}).get("materialization") == "source_slot_ready"
        ),
        "sourceSlotRequiresParams": sum(
            1
            for node in nodes
            if node.manifest.get("canvas", {}).get("materialization")
            == "source_slot_requires_params"
        ),
        "toolCapabilityReviewRequired": sum(
            1
            for node in nodes
            if node.manifest.get("canvas", {}).get("materialization")
            == "tool_capability_review_required"
        ),
    }


def _facet_nodes(nodes: list[WorkflowOpenCLIAdapterNode]) -> dict[str, dict[str, int]]:
    return {
        "site": dict(Counter(node.site for node in nodes)),
        "capability": dict(Counter(node.capability for node in nodes)),
        "access": dict(Counter(node.access for node in nodes)),
        "browser": dict(
            Counter("browser" if node.browser else "non_browser" for node in nodes)
        ),
        "status": dict(Counter(node.status for node in nodes)),
        "presetKind": dict(Counter(node.presetKind for node in nodes)),
        "runtimeReadiness": dict(
            Counter(node.runtimeReadiness for node in nodes)
        ),
    }


def _read_args(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [arg for arg in value if isinstance(arg, dict)]


def _read_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "adapter"
