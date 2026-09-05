from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.workflow.iii_collection_dispatch import (
    IIIBridgeUnavailableError,
    IIITriggerUnsentError,
    _trigger_options,
    invoke_iii_collection,
)


class _Process:
    def __init__(self) -> None:
        self.returncode = 0
        self.killed = False

    async def communicate(self):
        return b"", b""

    def kill(self) -> None:
        self.killed = True


@pytest.mark.parametrize(
    "url",
    (
        "wss://proof-iii:49134",
        "ws://proof-iii:49134/not-root",
        "ws://user@proof-iii:49134",
        "ws://proof-iii:49134/?query=yes",
        "ws://proof-iii:70000",
    ),
)
def test_trigger_options_reject_unsupported_or_ambiguous_iii_urls(url: str):
    with pytest.raises(IIITriggerUnsentError):
        _trigger_options(url)


@pytest.mark.asyncio
async def test_invoke_iii_collection_uses_exact_cli_argv_env_and_json_envelope(monkeypatch):
    captured: dict[str, object] = {}
    process = _Process()

    async def create(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.get_settings",
        lambda: SimpleNamespace(
            iii_lifecycle_url="http://proof-relay:8080/api/v1/iii-collections/lifecycle",
            iii_lifecycle_token="bridge-token",
            iii_url="ws://proof-iii:49134/",
            iii_cli_path="/opt/iii/iii",
            iii_trigger_timeout_seconds=3,
        ),
    )
    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.asyncio.create_subprocess_exec",
        create,
    )

    payload = {"admin_collection": {"command_id": "command-1"}, "task_id": "task-1"}
    await invoke_iii_collection(payload, function_id="odp.collect::opencli_snapshot")

    argv = captured["argv"]
    assert argv[:6] == (
        "/opt/iii/iii", "trigger", "--address", "proof-iii", "--port", "49134",
    )
    assert argv[6:8] == ("odp.collect::opencli_snapshot", "--json")
    outer = json.loads(argv[8])
    assert json.loads(outer["admin_command_json"]) == payload
    environment = captured["kwargs"]["env"]
    assert environment["ADMIN_III_LIFECYCLE_URL"].endswith("/lifecycle")
    assert environment["ADMIN_III_LIFECYCLE_TOKEN"] == "bridge-token"


@pytest.mark.asyncio
async def test_invoke_timeout_kills_process_and_preserves_unknown_outbox_semantics(monkeypatch):
    process = _Process()

    async def create(*_argv, **_kwargs):
        return process

    async def timed_out(awaitable, *_args, **_kwargs):
        awaitable.close()
        raise TimeoutError
    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.get_settings",
        lambda: SimpleNamespace(
            iii_lifecycle_url="http://proof-relay:8080/api/v1/iii-collections/lifecycle",
            iii_lifecycle_token=None,
            iii_url="ws://proof-iii:49134",
            iii_cli_path="/opt/iii/iii",
            iii_trigger_timeout_seconds=1,
        ),
    )
    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.asyncio.create_subprocess_exec",
        create,
    )
    monkeypatch.setattr(
        "backend.workflow.iii_collection_dispatch.asyncio.wait_for", timed_out
    )

    with pytest.raises(IIIBridgeUnavailableError, match="outcome is unknown"):
        await invoke_iii_collection({}, function_id="odp.collect::opencli_snapshot")
    assert process.killed is True
