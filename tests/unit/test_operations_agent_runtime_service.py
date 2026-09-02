import asyncio
import pytest

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.identity import Team, User, Workspace
from backend.models.operations_agent import (
    AgentPermissionProfile,
    OperationsAgentIdentity,
    OperationsAgentRun,
    PublishedOperationsAgentVersion,
)
from backend.services import operations_agent_runtime_service


def _model_configuration() -> dict:
    return {
        "agent_contract": {
            "schema_version": "agent.contract.v2",
            "role": "operations_reviewer",
            "input_schema": {
                "type": "object",
                "properties": {"target_id": {"type": "string"}},
                "required": ["target_id"],
            },
            "output_schema": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
            "state_schema": {
                "type": "object",
                "properties": {"last_target_id": {"type": "string"}},
            },
            "required_capabilities": ["streaming", "tool_events"],
            "tool_policy": {"allow": ["read"]},
            "budget": {"max_turns": 8},
            "quality_gates": [],
            "evidence_requirements": [],
        },
        "runtime_binding": {
            "schema_version": "agent.runtime-binding.v2",
            "workflow": "operations-agent",
            "preferred_agent_urls": ["http://agent-runtime.test:19823"],
            "preferred_runtimes": ["pi"],
            "model_binding": {
                "schema_version": "agent.model-binding.v1",
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet",
                "auth_profile": "operations",
            },
            "config": {},
        },
    }


def _stub_runtime_selection(monkeypatch, runtime: str = "pi") -> None:
    async def select_agent_runtime(*args, **kwargs):
        return {
            "schema_version": "agent.runtime-selection.v1",
            "agent_url": "http://agent-runtime.test:19823",
            "runtime": runtime,
            "workflow": "operations-agent",
            "capabilities": ["model_selection", "streaming", "tool_events"],
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet",
            "auth_profile": "operations",
        }

    monkeypatch.setattr(
        operations_agent_runtime_service,
        "select_agent_runtime",
        select_agent_runtime,
    )


async def _seed_run(db_session) -> OperationsAgentRun:
    user = User(subject="runtime-owner")
    workspace = Workspace(name="Runtime workspace", slug="runtime-workspace")
    db_session.add_all((user, workspace))
    await db_session.flush()
    team = Team(workspace_id=workspace.id, name="Operations", slug="operations")
    db_session.add(team)
    await db_session.flush()
    agent = OperationsAgentIdentity(
        workspace_id=workspace.id,
        owning_team_id=team.id,
        name="Runtime agent",
        current_profile_version=1,
        current_published_version=1,
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add(
        PublishedOperationsAgentVersion(
            operations_agent_id=agent.id,
            version=1,
            draft_revision=1,
            instructions="Inspect the requested target",
            model_configuration=_model_configuration(),
            tool_configuration={},
            published_by_user_id=user.id,
            reason="Runtime test",
        )
    )
    db_session.add(
        AgentPermissionProfile(
            operations_agent_id=agent.id,
            version=1,
            mode="observe_only",
            tool_scope=[],
            resource_scope=[],
            action_scope=[],
            assigned_by_user_id=user.id,
            reason="Runtime test",
        )
    )
    run = OperationsAgentRun(
        workspace_id=workspace.id,
        operations_agent_id=agent.id,
        published_version=1,
        profile_version=1,
        trigger_type="manual",
        target_resource_type="plan",
        target_resource_id="daily-news",
        input_payload={"target_id": "daily-news"},
        state_payload={},
        status="queued",
        started_by_user_id=user.id,
    )
    db_session.add(run)
    await db_session.commit()
    return run


@pytest.mark.parametrize("runtime", ["pi", "codex"])
async def test_dispatch_uses_existing_runtime_protocol_and_validates_result(
    db_engine, db_session, monkeypatch, runtime
):
    run = await _seed_run(db_session)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(operations_agent_runtime_service, "AsyncSessionLocal", session_factory)
    _stub_runtime_selection(monkeypatch, runtime=runtime)
    captured_task = {}

    async def send_agent_task(agent_url, task, on_event, timeout):
        captured_task.update(task)
        assert agent_url == "http://agent-runtime.test:19823"
        assert timeout == 1800.0
        await on_event(
            {
                "type": "state",
                "state": {"last_target_id": "daily-news"},
            }
        )
        await on_event(
            {
                "type": "audit",
                "audit": {"action": "read", "api_key": "must-not-persist"},
            }
        )
        return {
            "type": "done",
            "result": {"summary": "Target inspected"},
        }

    monkeypatch.setattr(
        operations_agent_runtime_service,
        "send_agent_task",
        send_agent_task,
    )

    await operations_agent_runtime_service.dispatch_operations_agent_run(run.id)

    await db_session.refresh(run)
    assert captured_task["runtime"] == runtime
    assert captured_task["instructions"] == "Inspect the requested target"
    assert captured_task["input"] == {"target_id": "daily-news"}
    assert captured_task["config"]["permission_mode"] == "observe_only"
    assert captured_task["config"]["timeout_seconds"] == 1800
    assert run.status == "completed"
    assert run.state_payload == {"last_target_id": "daily-news"}
    assert run.output_payload == {"summary": "Target inspected"}
    assert run.error_message is None
    assert run.execution_binding["runtime"] == runtime
    assert run.evidence_payload["schema_version"] == "agent.run-evidence.v1"
    assert run.evidence_payload["audit"][1]["api_key"] == "[REDACTED]"


async def test_duplicate_dispatch_is_harmless_after_queued_claim(
    db_engine,
    db_session,
    monkeypatch,
):
    run = await _seed_run(db_session)
    session_factory = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(operations_agent_runtime_service, "AsyncSessionLocal", session_factory)
    _stub_runtime_selection(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def send_agent_task(agent_url, task, on_event, timeout):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"type": "done", "result": {"summary": "Target inspected"}}

    monkeypatch.setattr(
        operations_agent_runtime_service,
        "send_agent_task",
        send_agent_task,
    )
    first = asyncio.create_task(
        operations_agent_runtime_service.dispatch_operations_agent_run(run.id)
    )
    await started.wait()
    await operations_agent_runtime_service.dispatch_operations_agent_run(run.id)
    release.set()
    await first

    await db_session.refresh(run)
    assert calls == 1
    assert run.status == "completed"


async def test_dispatch_fails_closed_when_runtime_output_breaks_contract(
    db_engine, db_session, monkeypatch
):
    run = await _seed_run(db_session)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(operations_agent_runtime_service, "AsyncSessionLocal", session_factory)
    _stub_runtime_selection(monkeypatch)

    async def send_agent_task(agent_url, task, on_event, timeout):
        return {"type": "done", "result": {"unexpected": True}}

    monkeypatch.setattr(
        operations_agent_runtime_service,
        "send_agent_task",
        send_agent_task,
    )

    await operations_agent_runtime_service.dispatch_operations_agent_run(run.id)

    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.output_payload is None
    assert "output_schema" in run.error_message


async def test_dispatch_fails_closed_when_required_evidence_is_missing(
    db_engine, db_session, monkeypatch
):
    run = await _seed_run(db_session)
    version = await db_session.scalar(
        select(PublishedOperationsAgentVersion).where(
            PublishedOperationsAgentVersion.operations_agent_id == run.operations_agent_id
        )
    )
    configuration = _model_configuration()
    configuration["agent_contract"]["evidence_requirements"] = ["citation"]
    version.model_configuration = configuration
    await db_session.commit()
    session_factory = async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(
        operations_agent_runtime_service,
        "AsyncSessionLocal",
        session_factory,
    )
    _stub_runtime_selection(monkeypatch)

    async def send_agent_task(agent_url, task, on_event, timeout):
        return {"type": "done", "result": {"summary": "No evidence"}}

    monkeypatch.setattr(
        operations_agent_runtime_service,
        "send_agent_task",
        send_agent_task,
    )

    await operations_agent_runtime_service.dispatch_operations_agent_run(run.id)

    await db_session.refresh(run)
    assert run.status == "failed"
    assert "citation" in run.error_message
    assert run.evidence_payload["lineage"][0]["type"] == "agent_runtime"


async def test_dispatch_rejects_automatic_profile_without_governed_gateway(
    db_engine, db_session, monkeypatch
):
    run = await _seed_run(db_session)
    await db_session.execute(
        update(AgentPermissionProfile)
        .where(AgentPermissionProfile.operations_agent_id == run.operations_agent_id)
        .values(mode="low_risk_automatic")
    )
    await db_session.commit()
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(operations_agent_runtime_service, "AsyncSessionLocal", session_factory)

    async def send_agent_task(*args, **kwargs):
        raise AssertionError("automatic run must not reach the runtime")

    monkeypatch.setattr(operations_agent_runtime_service, "send_agent_task", send_agent_task)

    await operations_agent_runtime_service.dispatch_operations_agent_run(run.id)

    await db_session.refresh(run)
    assert run.status == "failed"
    assert run.error_message == (
        "Automatic Operations Agent runs require the governed action gateway"
    )


async def test_paused_run_is_not_overwritten_by_terminal_result(db_engine, db_session, monkeypatch):
    run = await _seed_run(db_session)
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(operations_agent_runtime_service, "AsyncSessionLocal", session_factory)
    _stub_runtime_selection(monkeypatch)

    async def send_agent_task(agent_url, task, on_event, timeout):
        async with session_factory() as session:
            await session.execute(
                update(OperationsAgentRun)
                .where(OperationsAgentRun.id == run.id)
                .values(status="paused")
            )
            await session.commit()
        return {"type": "done", "result": {"summary": "Too late"}}

    monkeypatch.setattr(
        operations_agent_runtime_service,
        "send_agent_task",
        send_agent_task,
    )

    await operations_agent_runtime_service.dispatch_operations_agent_run(run.id)

    await db_session.refresh(run)
    assert run.status == "paused"
    assert run.output_payload is None


async def test_cancel_operations_agent_run_cancels_owned_dispatch(monkeypatch):
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def dispatch(run_id):
        started.set()
        await blocked.wait()

    monkeypatch.setattr(
        operations_agent_runtime_service,
        "dispatch_operations_agent_run",
        dispatch,
    )

    operations_agent_runtime_service.schedule_operations_agent_run("run-cancel")
    await started.wait()
    task = operations_agent_runtime_service._ACTIVE_DISPATCHES["run-cancel"]
    operations_agent_runtime_service.cancel_operations_agent_run("run-cancel")
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "run-cancel" not in operations_agent_runtime_service._ACTIVE_DISPATCHES
