import ast
import asyncio
from pathlib import Path

import httpx
import pytest

import backend.main as main
import backend.workflow.workflow_plugins as workflow_plugins


class FakeWorkflowPlugin:
    def __init__(
        self,
        key: str,
        capabilities: frozenset[str],
        lifecycle_log: list[str] | None = None,
        *,
        fail_start: bool = False,
    ) -> None:
        self.key = key
        self.capabilities = capabilities
        self.lifecycle_log = lifecycle_log if lifecycle_log is not None else []
        self.fail_start = fail_start
        self.contribution_calls = 0
        self.validation_calls = 0
        self.route_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    def contribute_events(
        self, context: workflow_plugins.WorkflowPluginEventContext
    ) -> list:
        self.contribution_calls += 1
        return []

    def validate_events(self, events: list, *, run_id: str, trace_id: str) -> None:
        self.validation_calls += 1

    def register_routes(self, v1_router: object, studio_router: object) -> None:
        self.route_calls += 1

    async def start(self) -> None:
        self.start_calls += 1
        self.lifecycle_log.append(f"{self.key}:start")
        if self.fail_start:
            raise RuntimeError(self.key)

    async def stop(self) -> None:
        self.stop_calls += 1
        self.lifecycle_log.append(f"{self.key}:stop")


def _context() -> workflow_plugins.WorkflowPluginEventContext:
    return workflow_plugins.WorkflowPluginEventContext(
        workflow_id="workflow",
        run_id="run",
        trace_id="trace",
        node_id="node",
        sequence=1,
        output_items=[],
    )


def _route_paths(router: object) -> set[str]:
    prefix = getattr(router, "prefix", "")
    paths: set[str] = set()
    for route in router.routes:
        nested_router = getattr(route, "router", None) or getattr(
            route, "original_router", None
        )
        if nested_router is not None and hasattr(nested_router, "routes"):
            paths.update(
                path if not prefix or path.startswith(prefix) else f"{prefix}{path}"
                for path in _route_paths(nested_router)
            )
            continue
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path if not prefix or path.startswith(prefix) else f"{prefix}{path}")
    return paths


def test_registration_is_atomic_and_snapshots_are_isolated():
    primary = FakeWorkflowPlugin("primary", frozenset())
    registry = workflow_plugins.WorkflowPluginRegistry((primary,))
    before = registry.snapshot()

    with pytest.raises(
        workflow_plugins.WorkflowPluginRegistrationError, match="already registered"
    ):
        registry.register(
            FakeWorkflowPlugin("candidate", frozenset()),
            FakeWorkflowPlugin("primary", frozenset()),
        )

    assert registry.snapshot() == before
    snapshot = registry.snapshot()
    registry.register(FakeWorkflowPlugin("later", frozenset()))
    assert [plugin.key for plugin in snapshot] == ["primary"]
    assert [plugin.key for plugin in registry.snapshot()] == ["primary", "later"]


@pytest.mark.asyncio
async def test_replacement_dispatches_only_declared_capabilities_and_never_calls_real_adapter():
    replacement = FakeWorkflowPlugin(
        "replacement",
        frozenset({workflow_plugins.WorkflowPluginCapability.EVENT_CONTRIBUTION}),
    )
    real_adapter = main.build_workflow_plugin_registry(
        main.Settings(workflow_plugins="research-graph")
    ).snapshot()[0]
    registry = workflow_plugins.WorkflowPluginRegistry((replacement,))

    assert registry.contribute_events(_context()) == []
    registry.validate_events([], run_id="run", trace_id="trace")
    registry.register_routes(object(), object())
    await registry.start()
    await registry.stop()

    assert replacement.contribution_calls == 1
    assert replacement.validation_calls == 0
    assert replacement.route_calls == 0
    assert replacement.start_calls == 0
    assert replacement.stop_calls == 0
    assert real_adapter.contribution_count == 0
    assert real_adapter.validation_count == 0
    assert real_adapter.start_count == 0
    assert real_adapter.stop_count == 0


@pytest.mark.asyncio
async def test_lifecycle_is_reverse_idempotent_and_rolls_back_after_failure():
    log: list[str] = []
    first = FakeWorkflowPlugin(
        "first", frozenset({workflow_plugins.WorkflowPluginCapability.LIFECYCLE}), log
    )
    broken = FakeWorkflowPlugin(
        "broken",
        frozenset({workflow_plugins.WorkflowPluginCapability.LIFECYCLE}),
        log,
        fail_start=True,
    )
    registry = workflow_plugins.WorkflowPluginRegistry((first, broken))

    with pytest.raises(workflow_plugins.WorkflowPluginLifecycleError, match="broken"):
        await registry.start()
    assert log == ["first:start", "broken:start", "first:stop"]

    broken.fail_start = False
    await registry.start()
    await registry.start()
    await registry.stop()
    await registry.stop()

    assert log == [
        "first:start",
        "broken:start",
        "first:stop",
        "first:start",
        "broken:start",
        "broken:stop",
        "first:stop",
    ]


@pytest.mark.asyncio
async def test_concurrent_registry_instances_do_not_cross_dispatch_or_lifecycle():
    left = FakeWorkflowPlugin(
        "left", frozenset({workflow_plugins.WorkflowPluginCapability.LIFECYCLE})
    )
    right = FakeWorkflowPlugin(
        "right", frozenset({workflow_plugins.WorkflowPluginCapability.LIFECYCLE})
    )
    left_registry = workflow_plugins.WorkflowPluginRegistry((left,))
    right_registry = workflow_plugins.WorkflowPluginRegistry((right,))

    await asyncio.gather(left_registry.start(), right_registry.start())
    await asyncio.gather(left_registry.stop(), right_registry.stop())

    assert (left.start_calls, left.stop_calls) == (1, 1)
    assert (right.start_calls, right.stop_calls) == (1, 1)
    assert left_registry.snapshot() == (left,)
    assert right_registry.snapshot() == (right,)


def test_enabled_and_disabled_app_route_snapshots_preserve_legacy_paths():
    disabled_app = main.create_app(app_settings=main.Settings(workflow_plugins=""))
    disabled_paths = _route_paths(disabled_app.router)
    assert not any("research-graph" in path.split("/") for path in disabled_paths)
    assert "/api/v1/workflows/compile" in disabled_paths
    assert "/api/v1/workflows/runs" in disabled_paths
    assert "/api/v1/workflows/runs/{run_id}/events" in disabled_paths

    enabled_paths = _route_paths(
        main.create_app(
            app_settings=main.Settings(workflow_plugins="research-graph")
        ).router
    )
    legacy_graph_paths = {
        "/api/v1/workflows/runs/{run_id}/research-graph",
        "/api/v1/workflows/runs/{run_id}/research-graph/mutations",
        "/api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/research-graph",
        "/api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/research-graph/mutations",
    }
    assert legacy_graph_paths.issubset(enabled_paths)


@pytest.mark.asyncio
async def test_disabled_plugin_rejects_generic_and_scoped_graph_http_routes():
    app = main.create_app(app_settings=main.Settings(workflow_plugins=""))
    generic = "/api/v1/workflows/runs/run/research-graph"
    scoped = (
        "/api/v1/workspaces/workspace/projects/project/workflows/workflow"
        "/runs/run/research-graph"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        for route in (generic, scoped):
            assert (await client.get(route)).status_code == 404
            assert (await client.post(f"{route}/mutations", json={})).status_code == 404


def test_core_sources_have_no_research_graph_imports_types_or_ui_state():
    repository_root = Path(__file__).resolve().parents[2]
    python_paths = (
        "backend/schemas/workflow_runtime.py",
        "backend/workflow/workflow_run_events.py",
        "backend/workflow/opencli_hda_tracer.py",
        "backend/api/v1/__init__.py",
        "backend/api/v1/studio.py",
        "backend/schemas/workflow.py",
    )
    for relative_path in python_paths:
        tree = ast.parse((repository_root / relative_path).read_text(encoding="utf-8"))
        imported_modules = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ] + [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        assert not any("research_graph" in module.casefold() for module in imported_modules)
        assert "researchgraph" not in (
            repository_root / relative_path
        ).read_text(encoding="utf-8").casefold()

    panel_source = (
        repository_root / "frontend/components/flow/run-trace-panel.tsx"
    ).read_text(encoding="utf-8").casefold()
    assert "researchgraph" not in panel_source
    assert "research-graph" not in panel_source
