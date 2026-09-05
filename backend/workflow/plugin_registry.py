"""Shared optional workflow plugin construction for API and workers."""

from importlib import import_module

from backend.config import Settings
from backend.workflow.workflow_plugins import WorkflowPluginRegistry, WorkflowPluginRegistrationError


def build_workflow_plugin_registry(app_settings: Settings) -> WorkflowPluginRegistry:
    """Assemble optional workflow adapters before the application is returned."""

    enabled = app_settings.workflow_plugin_ids
    unknown = set(enabled).difference({"research-graph"})
    if unknown:
        raise WorkflowPluginRegistrationError(
            f"unknown workflow plugins: {', '.join(sorted(unknown))}"
        )
    registry = WorkflowPluginRegistry()
    if "research-graph" in enabled:
        plugin_module = import_module("backend.workflow.research_graph")
        registry.register(plugin_module.ResearchGraphWorkflowPlugin())
    return registry

