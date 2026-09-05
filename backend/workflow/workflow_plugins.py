"""Domain-neutral workflow plugin contracts and isolated adapter registries."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any, NamedTuple, Protocol

from backend.schemas.workflow_runtime import WorkflowNodeRunEvent


class WorkflowPluginCapability:
    EVENT_CONTRIBUTION = "event-contribution"
    EVENT_VALIDATION = "event-validation"
    ROUTES = "routes"
    LIFECYCLE = "lifecycle"


class WorkflowPluginEventContext(NamedTuple):
    workflow_id: str
    run_id: str
    trace_id: str
    node_id: str
    sequence: int
    output_items: list[dict[str, Any]]


class WorkflowPlugin(Protocol):
    key: str
    capabilities: frozenset[str]

    def contribute_events(
        self, context: WorkflowPluginEventContext
    ) -> list[WorkflowNodeRunEvent]: ...

    def validate_events(
        self,
        events: list[WorkflowNodeRunEvent],
        *,
        run_id: str,
        trace_id: str,
    ) -> None: ...

    def register_routes(self, v1_router: object, studio_router: object) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class WorkflowPluginRegistrationError(ValueError):
    """A registry update would leave an ambiguous or incomplete adapter set."""


class WorkflowPluginLifecycleError(RuntimeError):
    """An adapter failed while starting or stopping within its registry instance."""


class WorkflowPluginRegistry:
    """An explicitly assembled, instance-local set of workflow adapters."""

    def __init__(self, plugins: Iterable[WorkflowPlugin] = ()) -> None:
        self._plugins: tuple[WorkflowPlugin, ...] = ()
        self._started: tuple[WorkflowPlugin, ...] = ()
        self._lifecycle_lock = asyncio.Lock()
        self.register(*plugins)

    @property
    def plugins(self) -> tuple[WorkflowPlugin, ...]:
        return self._plugins

    def snapshot(self) -> tuple[WorkflowPlugin, ...]:
        """Return an immutable view that is unaffected by later registrations."""

        return self._plugins

    def has_capability(self, capability: str) -> bool:
        return any(capability in plugin.capabilities for plugin in self._plugins)

    def register(self, *plugins: WorkflowPlugin) -> None:
        """Atomically add adapters after validating the whole candidate batch."""

        candidate = tuple(plugins)
        existing_keys = {plugin.key for plugin in self._plugins}
        candidate_keys = [plugin.key for plugin in candidate]
        if any(not key for key in candidate_keys):
            raise WorkflowPluginRegistrationError("workflow plugin keys must be non-empty")
        if len(candidate_keys) != len(set(candidate_keys)):
            raise WorkflowPluginRegistrationError(
                "workflow plugin keys must be unique per registration"
            )
        duplicate = existing_keys.intersection(candidate_keys)
        if duplicate:
            raise WorkflowPluginRegistrationError(
                f"workflow plugin already registered: {sorted(duplicate)[0]}"
            )
        for plugin in candidate:
            self._validate_capabilities(plugin)
        self._plugins = (*self._plugins, *candidate)

    def contribute_events(self, context: WorkflowPluginEventContext) -> list[WorkflowNodeRunEvent]:
        events: list[WorkflowNodeRunEvent] = []
        for plugin in self._plugins:
            if WorkflowPluginCapability.EVENT_CONTRIBUTION in plugin.capabilities:
                events.extend(plugin.contribute_events(context))
        return events

    def validate_events(
        self,
        events: list[WorkflowNodeRunEvent],
        *,
        run_id: str,
        trace_id: str,
    ) -> None:
        for plugin in self._plugins:
            if WorkflowPluginCapability.EVENT_VALIDATION in plugin.capabilities:
                plugin.validate_events(events, run_id=run_id, trace_id=trace_id)

    def register_routes(self, v1_router: object, studio_router: object) -> None:
        for plugin in self._plugins:
            if WorkflowPluginCapability.ROUTES in plugin.capabilities:
                plugin.register_routes(v1_router, studio_router)

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            started: list[WorkflowPlugin] = []
            try:
                for plugin in self._plugins:
                    if WorkflowPluginCapability.LIFECYCLE in plugin.capabilities:
                        await plugin.start()
                        started.append(plugin)
            except Exception as exc:
                failed_key = plugin.key
                for plugin in reversed(started):
                    try:
                        await plugin.stop()
                    except Exception:
                        pass
                raise WorkflowPluginLifecycleError(
                    f"workflow plugin startup failed: {failed_key}"
                ) from exc
            self._started = tuple(started)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                return
            started = self._started
            self._started = ()
            failures: list[Exception] = []
            for plugin in reversed(started):
                try:
                    await plugin.stop()
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise WorkflowPluginLifecycleError("workflow plugin shutdown failed") from failures[
                    0
                ]

    @staticmethod
    def _validate_capabilities(plugin: WorkflowPlugin) -> None:
        for capability in plugin.capabilities:
            method = {
                WorkflowPluginCapability.EVENT_CONTRIBUTION: "contribute_events",
                WorkflowPluginCapability.EVENT_VALIDATION: "validate_events",
                WorkflowPluginCapability.ROUTES: "register_routes",
                WorkflowPluginCapability.LIFECYCLE: "start",
            }.get(capability)
            if method is None or not callable(getattr(plugin, method, None)):
                raise WorkflowPluginRegistrationError(
                    f"workflow plugin {plugin.key!r} does not implement {capability}"
                )
            if capability == WorkflowPluginCapability.LIFECYCLE and not callable(
                getattr(plugin, "stop", None)
            ):
                raise WorkflowPluginRegistrationError(
                    f"workflow plugin {plugin.key!r} does not implement lifecycle stop"
                )
