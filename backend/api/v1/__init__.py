"""API v1 router package."""

from fastapi import APIRouter

from backend.api.v1 import (
    agents,
    automations,
    browser_act,
    browsers,
    chat,
    consumer_grants,
    control,
    cookies,
    dashboard,
    dify_imports,
    geo_acquisition,
    identity,
    model_defaults,
    nodes,
    notifications,
    operations_agents,
    operations_inbox,
    plan_ir,
    plans,
    plugins,
    presets,
    project_source_bindings,
    providers,
    records,
    schedules,
    skill_bridge,
    skill_record,
    skills,
    sources,
    studio,
    system,
    tasks,
    webhooks,
    workers,
    workflows,
    workspace_sources,
    workspaces,
)

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(agents.router)
v1_router.include_router(geo_acquisition.router)
v1_router.include_router(automations.router)
v1_router.include_router(browser_act.router)
v1_router.include_router(browsers.router)
v1_router.include_router(chat.router)
v1_router.include_router(control.router)
v1_router.include_router(consumer_grants.router)
v1_router.include_router(cookies.router)
v1_router.include_router(model_defaults.router)
v1_router.include_router(nodes.router)
v1_router.include_router(plan_ir.router)
v1_router.include_router(plans.router)
v1_router.include_router(plugins.router)
v1_router.include_router(presets.router)
v1_router.include_router(providers.router)
v1_router.include_router(sources.router)
v1_router.include_router(studio.router)
v1_router.include_router(tasks.router)
v1_router.include_router(records.router)
v1_router.include_router(schedules.router)
v1_router.include_router(skills.router)
v1_router.include_router(skill_bridge.router)
v1_router.include_router(skill_record.router)
v1_router.include_router(webhooks.router)
v1_router.include_router(workflows.router)
v1_router.include_router(dify_imports.router)
v1_router.include_router(notifications.router)
v1_router.include_router(operations_inbox.router)
v1_router.include_router(operations_agents.router)
v1_router.include_router(workers.router)
v1_router.include_router(dashboard.router)
v1_router.include_router(system.router)
v1_router.include_router(identity.router)
v1_router.include_router(workspaces.router)
v1_router.include_router(workspace_sources.router)
v1_router.include_router(project_source_bindings.router)
