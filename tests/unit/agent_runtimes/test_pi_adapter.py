"""Tests for backend/agent_runtimes/pi_adapter.py using a FAKE pi binary.

The fake is a small Python script written to tmp_path that speaks the
documented pi --mode rpc JSONL protocol (see pi_adapter.py's module
docstring for the schema this mirrors) on stdin/stdout. We point the
adapter's `binary` config at `sys.executable` and prepend the fake script
path via `args` — see PiRuntimeAdapter._compose_argv's docstring for why
that composition was chosen (keeps `binary` a plain str, no argv-as-list
special case).
"""

import asyncio
import sys
from typing import Any

import pytest

from backend.agent_runtimes.base import AgentTask
from backend.agent_runtimes.pi_adapter import PiRuntimeAdapter

_FAKE_PI_HAPPY = r'''
import json
import sys

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

line = sys.stdin.readline()
req = json.loads(line)

emit({"type": "response", "command": "prompt", "success": True})
emit({
    "type": "message_update",
    "message": {"id": "m1"},
    "assistantMessageEvent": {"type": "text_start", "contentIndex": 0},
})
emit({
    "type": "message_update",
    "message": {"id": "m1"},
    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "Hello "},
})
emit({
    "type": "message_update",
    "message": {"id": "m1"},
    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "world"},
})
emit({
    "type": "tool_execution_start",
    "toolCallId": "c1",
    "toolName": "search",
    "args": {"q": "foo"},
})
emit({
    "type": "tool_execution_end",
    "toolCallId": "c1",
    "toolName": "search",
    "result": {"content": ["bar"]},
    "isError": False,
})
emit({"type": "queue_update", "steering": [], "followUp": []})  # unmapped, must be skipped
emit({"type": "agent_end", "messages": [{"id": "m1"}]})
sys.exit(0)
'''

_FAKE_PI_ERROR_EXIT = r'''
import sys

sys.stdin.readline()
sys.stderr.write("fatal: something exploded\n")
sys.exit(1)
'''

_FAKE_PI_RPC_ERROR_RESPONSE = r'''
import json
import sys

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

sys.stdin.readline()
emit(
    {
        "type": "response",
        "command": "prompt",
        "success": False,
        "error": "Model not found: bogus/model",
    }
)
sys.exit(1)
'''

_FAKE_PI_HANGS = r'''
import sys
import time

sys.stdin.readline()
while True:
    time.sleep(1)
'''

_FAKE_PI_MALFORMED_LINES = r'''
import json
import sys

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

sys.stdin.readline()
sys.stdout.write("\n")  # blank line: must be skipped, not treated as JSON
sys.stdout.flush()
sys.stdout.write("not json at all\n")
sys.stdout.flush()
emit(
    {
        "type": "message_update",
        "message": {},
        "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "ok"},
    }
)
emit({"type": "agent_end", "messages": []})
sys.exit(0)
'''



def _write_fake(tmp_path, name: str, source: str) -> str:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


def _adapter_config(script_path: str, **overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {"binary": sys.executable, "args": [script_path]}
    config.update(overrides)
    return config


# ── happy path ────────────────────────────────────────────────────────────


async def test_invoke_translates_full_event_sequence(tmp_path):
    script = _write_fake(tmp_path, "fake_pi_happy.py", _FAKE_PI_HAPPY)
    adapter = PiRuntimeAdapter()
    task = AgentTask(
        task_id="t1",
        workflow="wf",
        input={"message": "hi"},
        config=_adapter_config(script),
    )

    events = [ev async for ev in adapter.invoke(task)]

    assert events[0] == {"type": "started", "task_id": "t1"}
    assert events[-1]["type"] == "done"
    assert sum(1 for e in events if e["type"] in ("done", "error")) == 1

    text_events = [e for e in events if e["type"] == "text"]
    assert [e["text"] for e in text_events] == ["Hello ", "world"]

    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert tool_calls == [
        {
            "type": "tool_call",
            "task_id": "t1",
            "name": "search",
            "args": {"q": "foo"},
            "call_id": "c1",
        }
    ]

    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert tool_results == [
        {
            "type": "tool_result",
            "task_id": "t1",
            "name": "search",
            "result": {"content": ["bar"]},
            "call_id": "c1",
            "is_error": False,
        }
    ]

    done_event = events[-1]
    assert done_event["result"]["text"] == "Hello world"

    # queue_update must NOT leak through as an invented event type
    assert all(e["type"] != "queue_update" for e in events)


async def test_invoke_skips_malformed_json_lines_without_crashing(tmp_path):
    script = _write_fake(tmp_path, "fake_pi_malformed.py", _FAKE_PI_MALFORMED_LINES)
    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t2", workflow="wf", config=_adapter_config(script))

    events = [ev async for ev in adapter.invoke(task)]

    assert events[-1]["type"] == "done"
    text_events = [e for e in events if e["type"] == "text"]
    assert [e["text"] for e in text_events] == ["ok"]


# ── error paths ───────────────────────────────────────────────────────────


async def test_invoke_nonzero_exit_yields_error_with_stderr_tail(tmp_path):
    script = _write_fake(tmp_path, "fake_pi_error.py", _FAKE_PI_ERROR_EXIT)
    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t3", workflow="wf", config=_adapter_config(script))

    events = [ev async for ev in adapter.invoke(task)]

    assert events[0]["type"] == "started"
    assert len(events) == 2
    error_event = events[-1]
    assert error_event["type"] == "error"
    assert error_event["task_id"] == "t3"
    assert "something exploded" in error_event["message"]
    assert error_event["error_type"] == "ProcessExitError"


async def test_invoke_rpc_level_error_response_yields_error(tmp_path):
    script = _write_fake(tmp_path, "fake_pi_rpc_error.py", _FAKE_PI_RPC_ERROR_RESPONSE)
    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t4", workflow="wf", config=_adapter_config(script))

    events = [ev async for ev in adapter.invoke(task)]

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert "Model not found" in error_events[0]["message"]
    # exactly one terminal event even though process also exits nonzero
    assert sum(1 for e in events if e["type"] in ("done", "error")) == 1


async def test_invoke_timeout_kills_process_and_yields_timeout_error(tmp_path):
    script = _write_fake(tmp_path, "fake_pi_hangs.py", _FAKE_PI_HANGS)
    adapter = PiRuntimeAdapter()
    task = AgentTask(
        task_id="t5",
        workflow="wf",
        config=_adapter_config(script, timeout_seconds=0.2),
    )

    events = [ev async for ev in adapter.invoke(task)]

    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "TimeoutError"
    assert sum(1 for e in events if e["type"] in ("done", "error")) == 1


async def test_invoke_cancellation_stops_hanging_process(tmp_path):
    script = _write_fake(tmp_path, "fake_pi_cancel.py", _FAKE_PI_HANGS)
    adapter = PiRuntimeAdapter()
    task = AgentTask(
        task_id="t-cancel",
        workflow="wf",
        config=_adapter_config(script, timeout_seconds=30),
    )
    started = asyncio.Event()

    async def consume():
        async for event in adapter.invoke(task):
            if event["type"] == "started":
                started.set()

    consumer = asyncio.create_task(consume())
    await started.wait()
    consumer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consumer, timeout=1)


async def test_invoke_missing_binary_yields_error_without_raising():
    adapter = PiRuntimeAdapter()
    task = AgentTask(
        task_id="t6",
        workflow="wf",
        config={"binary": "definitely-not-a-real-binary-xyz"},
    )

    events = [ev async for ev in adapter.invoke(task)]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error_type"] == "FileNotFoundError"


async def test_invoke_os_error_on_spawn_yields_error(monkeypatch):
    async def _raise_os_error(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_os_error)

    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t7", workflow="wf", config={"binary": "pi"})

    events = [ev async for ev in adapter.invoke(task)]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error_type"] == "OSError"
    assert "permission denied" in events[0]["message"]


async def test_invoke_survives_stdin_write_after_process_exits(tmp_path):
    """A pi process that exits immediately (before we finish writing the
    request) must not crash the adapter — BrokenPipeError/ConnectionResetError
    on the stdin write are swallowed and the exit-code path reports the
    failure instead."""
    script = _write_fake(tmp_path, "fake_pi_error.py", _FAKE_PI_ERROR_EXIT)
    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t8", workflow="wf", config=_adapter_config(script))

    events = [ev async for ev in adapter.invoke(task)]

    assert events[0]["type"] == "started"
    assert events[-1]["type"] == "error"


# ── validate_config ───────────────────────────────────────────────────────


def test_validate_config_accepts_minimal_config():
    adapter = PiRuntimeAdapter()
    assert adapter.validate_config({}) == []


def test_validate_config_accepts_full_config(tmp_path):
    adapter = PiRuntimeAdapter()
    errors = adapter.validate_config(
        {
            "binary": "pi",
            "cwd": str(tmp_path),
            "env": {"FOO": "bar"},
            "provider_dir": str(tmp_path),
            "args": ["--verbose"],
            "timeout_seconds": 30,
        }
    )
    assert errors == []


@pytest.mark.parametrize(
    "config,expected_substr",
    [
        ({"binary": ""}, "binary"),
        ({"binary": 123}, "binary"),
        ({"cwd": 123}, "cwd"),
        ({"env": "not-a-dict"}, "env"),
        ({"provider_dir": 123}, "provider_dir"),
        ({"args": "not-a-list"}, "args"),
        ({"args": [1, 2]}, "args"),
        ({"timeout_seconds": -1}, "timeout_seconds"),
        ({"timeout_seconds": "soon"}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
    ],
)
def test_validate_config_rejects_bad_shapes(config, expected_substr):
    adapter = PiRuntimeAdapter()
    errors = adapter.validate_config(config)
    assert errors
    assert any(expected_substr in e for e in errors)


# ── capabilities / is_available / health ─────────────────────────────────


def test_capabilities_declares_stdio_transport_and_no_resume():
    adapter = PiRuntimeAdapter()
    assert adapter.capabilities.transport == "stdio"
    assert adapter.capabilities.streaming is True
    assert adapter.capabilities.resume_by_id is False
    assert adapter.capabilities.checkpoint == "none"


def test_is_available_true_for_python_executable():
    assert PiRuntimeAdapter.is_available(sys.executable) is True


def test_is_available_false_for_bogus_binary():
    assert PiRuntimeAdapter.is_available("definitely-not-a-real-binary-xyz") is False


async def test_health_reflects_is_available(monkeypatch):
    adapter = PiRuntimeAdapter()
    monkeypatch.setattr(
        PiRuntimeAdapter, "is_available", classmethod(lambda cls, binary="pi": True)
    )
    assert await adapter.health() is True
    monkeypatch.setattr(
        PiRuntimeAdapter, "is_available", classmethod(lambda cls, binary="pi": False)
    )
    assert await adapter.health() is False


# ── argv / env composition ────────────────────────────────────────────────


def test_compose_argv_inserts_args_before_mode_rpc():
    adapter = PiRuntimeAdapter()
    argv = adapter._compose_argv({"binary": "pi-bin", "args": ["--foo", "bar"]})
    assert argv == ["pi-bin", "--foo", "bar", "--mode", "rpc"]


def test_compose_argv_defaults_binary_to_pi():
    adapter = PiRuntimeAdapter()
    assert adapter._compose_argv({}) == ["pi", "--mode", "rpc"]


def test_read_only_permission_mode_forces_builtin_read_tools():
    adapter = PiRuntimeAdapter()
    argv = adapter._compose_argv(
        {
            "binary": "pi-bin",
            "args": ["--provider", "openai"],
            "permission_mode": "suggest_changes",
        }
    )
    assert argv == [
        "pi-bin",
        "--provider",
        "openai",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--tools",
        "read,grep,find,ls",
        "--mode",
        "rpc",
    ]


def test_read_only_permission_mode_rejects_explicit_extensions():
    adapter = PiRuntimeAdapter()
    errors = adapter.validate_config(
        {
            "permission_mode": "observe_only",
            "args": ["--extension=unsafe.ts"],
        }
    )
    assert errors == [
        "read-only permission modes cannot override Fleet launch config: args"
    ]


async def test_read_only_permission_mode_never_spawns_explicit_extension(monkeypatch):
    async def spawn(*args, **kwargs):
        raise AssertionError("invalid runtime config must not spawn a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = PiRuntimeAdapter()
    task = AgentTask(
        task_id="restricted",
        workflow="operations-agent",
        config={
            "permission_mode": "observe_only",
            "args": ["--extension=unsafe.ts"],
        },
    )

    events = [event async for event in adapter.invoke(task)]

    assert events == [
        {
            "type": "error",
            "task_id": "restricted",
            "message": "read-only permission modes cannot override Fleet launch config: args",
            "error_type": "ConfigError",
        }
    ]


async def test_read_only_permission_mode_never_spawns_binary_override(monkeypatch):
    async def spawn(*args, **kwargs):
        raise AssertionError("Fleet-owned binary must not be overridden")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    adapter = PiRuntimeAdapter()
    task = AgentTask(
        task_id="restricted-binary",
        workflow="operations-agent",
        config={
            "permission_mode": "suggest_changes",
            "binary": sys.executable,
            "args": ["-c", "print('BINARY_OVERRIDE_EXECUTED')"],
        },
    )

    events = [event async for event in adapter.invoke(task)]

    assert events[0]["type"] == "error"
    assert events[0]["error_type"] == "ConfigError"
    assert "binary" in events[0]["message"]
    assert "args" in events[0]["message"]


def test_compose_env_none_when_no_overrides():
    adapter = PiRuntimeAdapter()
    assert adapter._compose_env({}) is None


def test_compose_env_maps_provider_dir_to_session_dir_var(tmp_path):
    adapter = PiRuntimeAdapter()
    env = adapter._compose_env({"provider_dir": str(tmp_path)})
    assert env is not None
    assert env["PI_CODING_AGENT_SESSION_DIR"] == str(tmp_path)


def test_compose_env_merges_explicit_env():
    adapter = PiRuntimeAdapter()
    env = adapter._compose_env({"env": {"FOO": "bar"}})
    assert env is not None
    assert env["FOO"] == "bar"


def test_compose_request_reads_message_key():
    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t1", workflow="wf", input={"message": "hi there"})
    assert adapter._compose_request(task) == {"type": "prompt", "id": "t1", "message": "hi there"}


def test_compose_request_falls_back_to_prompt_key():
    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t1", workflow="wf", input={"prompt": "hi there"})
    assert adapter._compose_request(task)["message"] == "hi there"


def test_compose_request_defaults_to_empty_message():
    adapter = PiRuntimeAdapter()
    task = AgentTask(task_id="t1", workflow="wf")
    assert adapter._compose_request(task)["message"] == ""


def test_compose_request_prepends_operations_agent_instructions():
    adapter = PiRuntimeAdapter()
    task = AgentTask(
        task_id="t1",
        workflow="wf",
        instructions="Follow the published behavior.",
        input={"message": "Inspect target 42."},
    )

    assert adapter._compose_request(task)["message"] == (
        "Follow the published behavior.\n\nInspect target 42."
    )
