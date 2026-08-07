import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.acquisition.registry import (
    CHAT_AI_CAPABILITY_COMMIT,
    OFFICIAL_SITE_CAPABILITY_COMMIT,
)
from backend.browser_pool import init_pool


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
async def test_catalog_reports_runtime_identity_and_clean_profile_readiness(monkeypatch):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities, "_runtime_is_installed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        capabilities, "_registration_is_available", AsyncMock(return_value=True)
    )
    pool = init_pool(["http://default-profile:9222"], use_redis=False)
    pool.set_profile_kind("http://default-profile:9222", "authenticated")

    descriptors = await capabilities.probe_capabilities()
    official, chat = descriptors
    assert official.ready is False
    assert official.unavailable_reason == "no_clean_profile"
    assert official.runtime == {
        "ohmyopencli_repo_commit": capabilities.OHMYOPENCLI_COMMIT,
        "capability_source_commit": OFFICIAL_SITE_CAPABILITY_COMMIT,
        "opencli_version": "1.8.5",
    }
    assert chat.ready is True
    assert chat.unavailable_reason is None
    assert chat.runtime["capability_source_commit"] == CHAT_AI_CAPABILITY_COMMIT

    pool.set_profile_kind("http://default-profile:9222", "anonymous")
    official, chat = await capabilities.probe_capabilities()
    assert official.ready is True
    assert official.unavailable_reason is None
    assert chat.ready is False
    assert chat.unavailable_reason == "no_authenticated_profile"


@pytest.mark.asyncio
async def test_runtime_probe_uses_the_configured_opencli_binary(monkeypatch):
    from backend.acquisition import capabilities

    configured_bin = r"C:\managed\opencli.cmd"
    monkeypatch.setenv("OPENCLI_BIN", configured_bin)
    command = AsyncMock(
        side_effect=[
            (0, f"{capabilities.OHMYOPENCLI_COMMIT}\n"),
            (0, ""),
            (0, ""),
            (0, ""),
            (0, "1.8.5\n"),
            (0, "official-site observe help"),
            (1, "CDP not reachable at http://127.0.0.1:9"),
        ]
    )
    monkeypatch.setattr(capabilities, "_command", command)

    assert await capabilities._runtime_is_installed() is True
    assert command.await_args_list[4].args == (configured_bin, "--version")
    registration = capabilities.list_capability_registrations()[0]
    assert await capabilities._registration_is_available(registration) is True
    assert command.await_args_list[5].args == (
        configured_bin,
        "official-site",
        "observe",
        "--help",
    )


@pytest.mark.asyncio
async def test_runtime_probe_rejects_tracked_checkout_changes(monkeypatch):
    from backend.acquisition import capabilities

    command = AsyncMock(
        side_effect=[
            (0, f"{capabilities.OHMYOPENCLI_COMMIT}\n"),
            (0, ""),
            (0, ""),
            (0, " M adapters/official-site/observe.js\n"),
        ]
    )
    monkeypatch.setattr(capabilities, "_command", command)

    assert await capabilities._runtime_is_installed() is False
    assert command.await_count == 4
    assert command.await_args_list[3].args[-2:] == (
        "--porcelain",
        "--untracked-files=no",
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

    official, chat = descriptors
    assert official.ready is True
    assert official.unavailable_reason is None
    assert chat.ready is False
    assert chat.unavailable_reason == "no_authenticated_profile"


@pytest.mark.asyncio
async def test_catalog_omits_a_capability_when_its_real_command_is_not_registered(
    monkeypatch,
):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities, "_runtime_is_installed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        capabilities,
        "_command",
        AsyncMock(return_value=(1, "unknown command: official-site observe")),
    )

    assert await capabilities.probe_capabilities() == []


@pytest.mark.asyncio
async def test_catalog_rejects_opencli_root_help_for_an_unknown_site(monkeypatch):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities, "_runtime_is_installed", AsyncMock(return_value=True)
    )
    command = AsyncMock(return_value=(0, "Usage: opencli [options] [command]"))
    monkeypatch.setattr(capabilities, "_command", command)

    assert await capabilities.probe_capabilities() == []
    assert command.await_count == 2


@pytest.mark.asyncio
async def test_catalog_publishes_registered_chat_ai_capture(monkeypatch):
    from backend.acquisition import capabilities

    monkeypatch.setattr(
        capabilities, "_runtime_is_installed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        capabilities,
        "_command",
        AsyncMock(
            side_effect=[
                (0, "official-site observe help"),
                (1, "CDP not reachable at http://127.0.0.1:9"),
                (0, "Capture one real ChatGPT web answer for chat-ai.capture@1"),
                (1, "CDP not reachable at http://127.0.0.1:9"),
            ]
        ),
    )

    descriptors = await capabilities.probe_capabilities()

    assert [item.capability_id for item in descriptors] == [
        "official-site.observe",
        "chat-ai.capture",
    ]
