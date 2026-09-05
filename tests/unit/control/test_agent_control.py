from backend.api.v1.chat import TOOLS
from backend.control.agent_control import ACTION_REGISTRY
from backend.security.workspace_rbac import WorkspacePermission


def test_agent_control_registry_is_the_complete_chat_write_surface():
    assert ACTION_REGISTRY.action_names == {
        "toggle_source",
        "trigger_task",
        "update_schedule",
        "update_provider",
        "create_project",
        "update_workflow_draft",
    }


def test_registry_assigns_write_permissions_per_action():
    assert (
        ACTION_REGISTRY.get("trigger_task").permission
        == WorkspacePermission.RUN_OPERATIONS_AGENTS
    )
    for action_name in (
        "toggle_source",
        "update_schedule",
        "update_provider",
        "create_project",
        "update_workflow_draft",
    ):
        assert (
            ACTION_REGISTRY.get(action_name).permission
            == WorkspacePermission.MANAGE_CONFIGURATION
        )


def test_project_tool_contract_uses_server_bound_workspace():
    functions = {tool["function"]["name"]: tool["function"] for tool in TOOLS}
    assert {
        "list_projects",
        "list_workflows",
        "get_workflow_draft",
        "create_project",
        "update_workflow_draft",
    } <= functions.keys()
    for name in (
        "list_projects",
        "list_workflows",
        "get_workflow_draft",
        "create_project",
        "update_workflow_draft",
    ):
        assert "workspace_id" not in functions[name]["parameters"]["properties"]
