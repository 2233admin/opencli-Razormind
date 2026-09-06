"""Application-owned connector startup without running business recovery."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from backend.config import Settings
from backend.main import connector_reply_lifespan, create_app


def _app(*, reply: bool = False, artifact: bool = False):
    return create_app(
        app_settings=Settings(
            _env_file=None,
            workflow_plugins="",
            connector_reply_enabled=reply,
            connector_artifact_delivery_enabled=artifact,
        )
    )


@pytest.fixture
def lifecycle_boundary(monkeypatch):
    """Replace only the worker module boundary; use actual app settings/wiring."""

    calls = []
    module = ModuleType("backend.services.connector_reply_worker")

    async def start(session_factory, *, reply_enabled, artifact_enabled):
        calls.append(("start", reply_enabled, artifact_enabled, session_factory))

    async def recover():
        calls.append(("recover",))
        return 0

    async def stop():
        calls.append(("stop",))

    module.start_connector_reply_worker = start
    module.recover_connector_receipts = recover
    module.stop_connector_reply_worker = stop
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls, module


@pytest.mark.asyncio
async def test_disabled_worker_does_not_start_even_if_artifacts_requested(lifecycle_boundary):
    calls, _ = lifecycle_boundary
    async with connector_reply_lifespan(_app(artifact=True)):
        assert calls == []
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact", [False, True])
async def test_factory_settings_drive_start_recover_and_cleanup(lifecycle_boundary, artifact):
    from backend.database import AsyncSessionLocal

    calls, _ = lifecycle_boundary
    async with connector_reply_lifespan(_app(reply=True, artifact=artifact)):
        assert calls == [("start", True, artifact, AsyncSessionLocal), ("recover",)]
    assert calls[-1] == ("stop",)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["start", "recover", "body"])
async def test_worker_is_stopped_on_start_recovery_or_serving_failure(
    lifecycle_boundary, failure_stage
):
    calls, module = lifecycle_boundary

    async def fail(*args, **kwargs):
        del args, kwargs
        calls.append((failure_stage,))
        raise RuntimeError("isolated lifecycle failure")

    if failure_stage == "start":
        module.start_connector_reply_worker = fail
    elif failure_stage == "recover":
        module.recover_connector_receipts = fail

    with pytest.raises(RuntimeError, match="isolated lifecycle failure"):
        async with connector_reply_lifespan(_app(reply=True)):
            if failure_stage == "body":
                await fail()
    assert calls[-1] == ("stop",)
