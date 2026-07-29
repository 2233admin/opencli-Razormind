import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.acquisition.registry import (
    DOUBAO_CAPABILITY_COMMIT,
    OFFICIAL_SITE_CAPABILITY_COMMIT,
)
from backend.browser_pool import init_pool
from backend.channels.base import ChannelResult


def _runtime_command(capabilities, *, dirty="", doubao_ready=True):
    async def run(*args, env=None):
        if args[0] == "git" and "rev-parse" in args:
            return 0, f"{capabilities.OHMYOPENCLI_COMMIT}\n"
        if args[0] == "git" and "merge-base" in args:
            return 0, ""
        if args[0] == "git" and "status" in args:
            return 0, dirty
        if args[-1] == "--version":
            return 0, "opencli 1.8.5\n"
        if args[-1] == "--help":
            marker = (
                "official-site observe help"
                if "official-site" in args
                else "doubao capture help"
            )
            return 0, marker
        if env and env.get("OPENCLI_CDP_ENDPOINT") == "http://127.0.0.1:9":
            return 1, "CDP not reachable at http://127.0.0.1:9"
        if "session-probe" in args:
            return 0, json.dumps(
                {
                    "unattendedReady": doubao_ready,
                    "loginDetected": not doubao_ready,
                    "promptInputDetected": doubao_ready,
                    "sendButtonDetected": doubao_ready,
                    "url": "https://www.doubao.com/chat/",
                }
            )
        raise AssertionError(f"unexpected command: {args!r}")

    return AsyncMock(side_effect=run)


@pytest.mark.asyncio
async def test_runtime_probe_kills_a_timed_out_child(monkeypatch):
    from backend.acquisition import capabilities

    class HangingProcess:
        returncode = None

        def __init__(self):
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    process = HangingProcess()
    monkeypatch.setattr(capabilities, "COMMAND_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )

    assert await capabilities._command("opencli", "--version") == (1, "")
    assert process.killed is True
    assert process.waited is True


@pytest.mark.asyncio
async def test_catalog_does_not_publish_unpinned_runtime(monkeypatch):
    from backend.acquisition import capabilities

    command = AsyncMock(return_value=(0, "wrong-commit\n"))
    monkeypatch.setattr(capabilities, "_command", command)

    assert await capabilities.probe_capabilities() == []


@pytest.mark.asyncio
async def test_catalog_reports_profile_and_doubao_session_readiness(monkeypatch):
    from backend.acquisition import capabilities

    command = _runtime_command(capabilities, doubao_ready=False)
    monkeypatch.setattr(capabilities, "_command", command)
    endpoint = "http://default-profile:9222"
    pool = init_pool([endpoint], use_redis=False)

    descriptors = {
        item.capability_id: item for item in await capabilities.probe_capabilities()
    }
    official = descriptors["official-site.observe"]
    doubao = descriptors["chat-ai.capture"]
    assert official.ready is False
    assert official.unavailable_reason == "no_clean_profile"
    assert official.runtime["capability_source_commit"] == OFFICIAL_SITE_CAPABILITY_COMMIT
    assert doubao.ready is False
    assert doubao.unavailable_reason == "doubao_session_not_ready"
    assert doubao.runtime["capability_source_commit"] == DOUBAO_CAPABILITY_COMMIT

    pool.set_profile_kind(endpoint, "anonymous")
    descriptors = {
        item.capability_id: item for item in await capabilities.probe_capabilities()
    }
    assert descriptors["official-site.observe"].ready is True
    assert descriptors["chat-ai.capture"].unavailable_reason == (
        "no_authenticated_profile"
    )


@pytest.mark.asyncio
async def test_catalog_publishes_doubao_only_after_authenticated_session_probe(
    monkeypatch,
):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities,
        "_command",
        _runtime_command(capabilities, doubao_ready=True),
    )
    endpoint = "http://doubao-profile:9222"
    pool = init_pool([endpoint], use_redis=False)
    pool.set_profile_kind(endpoint, "authenticated")

    descriptors = {
        item.capability_id: item for item in await capabilities.probe_capabilities()
    }

    assert descriptors["chat-ai.capture"].ready is True
    assert descriptors["chat-ai.capture"].target == "doubao"
    assert descriptors["chat-ai.capture"].unavailable_reason is None
    assert descriptors["official-site.observe"].unavailable_reason == "no_clean_profile"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol", "collector_name"),
    [("http", "_collect_via_agent"), ("ws", "_collect_via_ws_agent")],
)
async def test_agent_mode_accepts_ready_session_before_send_button_is_rendered(
    monkeypatch,
    protocol,
    collector_name,
):
    from backend.acquisition import capabilities
    from backend.channels import opencli_channel

    endpoint = "agent-node-1"
    agent_url = (
        "http://agent-node-1:19823"
        if protocol == "http"
        else "ws://agent-node-1/session"
    )
    pool = init_pool([endpoint], use_redis=False)
    pool.set_mode(endpoint, "cdp")
    pool.set_profile_kind(endpoint, "authenticated")
    pool.set_agent_url(endpoint, agent_url)
    pool.set_agent_protocol(endpoint, protocol)
    collector = AsyncMock(
        return_value=ChannelResult.ok(
            [
                {
                    "unattendedReady": True,
                    "loginDetected": False,
                    "promptInputDetected": True,
                    "sendButtonDetected": False,
                    "url": "https://www.doubao.com/chat/",
                }
            ]
        )
    )
    monkeypatch.setattr(opencli_channel, collector_name, collector)
    monkeypatch.setattr(
        "backend.config.get_settings",
        lambda: SimpleNamespace(collection_mode="agent"),
    )
    local_command = AsyncMock()
    monkeypatch.setattr(capabilities, "_command", local_command)
    registration = next(
        item
        for item in capabilities.list_capability_registrations()
        if item.target == "doubao"
    )

    assert await capabilities._session_is_ready(registration, pool, endpoint) is True
    collector.assert_awaited_once_with(
        agent_url,
        "doubao",
        "session-probe",
        {},
        [],
        "json",
        "cdp",
        None,
    )
    local_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_probe_uses_the_configured_opencli_binary(monkeypatch):
    from backend.acquisition import capabilities

    configured_bin = r"C:\managed\opencli.cmd"
    monkeypatch.setenv("OPENCLI_BIN", configured_bin)
    monkeypatch.setenv("OPENCLI_DAEMON_PORT", "19825")
    command = _runtime_command(capabilities)
    monkeypatch.setattr(capabilities, "_command", command)

    assert await capabilities._runtime_is_installed() is True
    version_call = next(
        call for call in command.await_args_list if call.args[-1] == "--version"
    )
    assert version_call.args == (configured_bin, "--version")
    assert "OPENCLI_DAEMON_PORT" not in version_call.kwargs["env"]
    registration = capabilities.list_capability_registrations()[0]
    assert await capabilities._registration_is_available(registration) is True
    help_call = next(
        call
        for call in command.await_args_list
        if call.args[-3:] == ("official-site", "observe", "--help")
    )
    assert help_call.args[0] == configured_bin


@pytest.mark.asyncio
async def test_runtime_probe_rejects_tracked_checkout_changes(monkeypatch):
    from backend.acquisition import capabilities

    command = _runtime_command(
        capabilities,
        dirty=" M adapters/official-site/observe.js\n",
    )
    monkeypatch.setattr(capabilities, "_command", command)

    assert await capabilities._runtime_is_installed() is False
    assert any(
        call.args[-2:] == ("--porcelain", "--untracked-files=no")
        for call in command.await_args_list
    )


@pytest.mark.asyncio
async def test_catalog_stays_ready_while_anonymous_inventory_is_busy(monkeypatch):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities, "_runtime_is_installed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        capabilities, "_registration_is_available", AsyncMock(return_value=True)
    )
    endpoint = "http://anonymous-profile:9222"
    pool = init_pool([endpoint], use_redis=False)
    pool.set_profile_kind(endpoint, "anonymous")

    async with pool.acquire():
        descriptors = await capabilities.probe_capabilities()

    official = next(
        item for item in descriptors if item.capability_id == "official-site.observe"
    )
    assert official.ready is True
    assert official.unavailable_reason is None


@pytest.mark.asyncio
async def test_catalog_omits_capabilities_whose_real_commands_are_not_registered(
    monkeypatch,
):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities, "_runtime_is_installed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        capabilities,
        "_command",
        AsyncMock(return_value=(1, "unknown command")),
    )

    assert await capabilities.probe_capabilities() == []


@pytest.mark.asyncio
async def test_catalog_rejects_opencli_root_help_for_unknown_sites(monkeypatch):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities, "_runtime_is_installed", AsyncMock(return_value=True)
    )
    command = AsyncMock(return_value=(0, "Usage: opencli [options] [command]"))
    monkeypatch.setattr(capabilities, "_command", command)

    assert await capabilities.probe_capabilities() == []
    assert command.await_count == 2
