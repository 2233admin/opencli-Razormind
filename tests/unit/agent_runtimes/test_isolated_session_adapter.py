"""Protocol tests with in-process and benign subprocess fakes, never a real LLM."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import pytest

from backend.agent_runtimes.base import AgentTask
from backend.agent_runtimes.isolated_session_adapter import (
    IsolatedSessionRuntimeAdapter,
    _probe_valid,
)
from backend.agent_runtimes.registry import get_runtime


class _Writer:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        pass


class _Process:
    def __init__(self, frames: list[dict] | None = None, exit_code: int = 0, hang: bool = False):
        self.stdin = _Writer()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode = None
        self.pid = None
        self._exit_code = exit_code
        self._hang = hang
        for frame in frames or []:
            self.stdout.feed_data((json.dumps(frame) + "\n").encode())
        if not hang:
            self.stdout.feed_eof()
            self.stderr.feed_eof()

    async def wait(self):
        if self.returncode is not None:
            return self.returncode
        if self._hang:
            await asyncio.Future()
        self.returncode = self._exit_code
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self._hang = False
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def kill(self):
        self.returncode = -9
        self._hang = False
        self.stdout.feed_eof()
        self.stderr.feed_eof()


def _task(**changes):
    value = {
        "task_id": "turn-1",
        "workflow": "chat",
        "instructions": "be concise",
        "input": {
            "workspace_id": "ws-1",
            "conversation_id": "conv-1",
            "actor_subject": "user-1",
            "message": "hello",
            "continuation": "start",
        },
        "session_id": str(uuid.uuid4()),
        "permissions": {"tools": ["read"]},
        "budget": {"max_steps": 5},
        "required_capabilities": ("streaming",),
        "evidence_requirements": ("runtime_events",),
    }
    value.update(changes)
    return AgentTask(**value)


async def _events(adapter, task):
    return [event async for event in adapter.invoke(task)]


def _frames(request: dict, *tail: dict):
    frames = [
        {
            "type": "started",
            "task_id": request["task_id"],
            "server_session_id": request["server_session_id"],
            "scope": request["scope"],
            "native_resume_ack": True,
            "resumed": request["resume"]["requested"],
        },
        *tail,
    ]
    return [dict(frame, task_id=request["task_id"]) for frame in frames]


@pytest.mark.asyncio
async def test_two_turns_bind_scope_and_require_native_resume_ack(monkeypatch):
    adapter = IsolatedSessionRuntimeAdapter()
    requests = []
    monkeypatch.setattr(IsolatedSessionRuntimeAdapter, "_probe_sync", classmethod(lambda cls: True))

    async def spawn(*argv, **kwargs):
        proc = _Process(hang=True)  # populate after native id has been written
        original_drain = proc.stdin.drain

        async def drain():
            await original_drain()
            request = json.loads(proc.stdin.data)
            requests.append((argv, kwargs, request))
            proc._hang = False
            proc.stdout.feed_data(
                b"".join(
                    (json.dumps(frame) + "\n").encode()
                    for frame in _frames(request, {"type": "text", "text": "ok"}, {"type": "done"})
                )
            )
            proc.stdout.feed_eof()
            proc.stderr.feed_eof()

        proc.stdin.drain = drain
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(
        "backend.agent_runtimes.isolated_session_adapter._runner_path", lambda: "C:/runner.exe"
    )
    task = _task()
    assert [e["type"] for e in await _events(adapter, task)] == ["started", "text", "done"]
    task.task_id = "turn-2"
    task.input["continuation"] = "resume"
    # A new adapter sends resume without depending on adapter-local memory.
    assert [e["type"] for e in await _events(IsolatedSessionRuntimeAdapter(), task)] == [
        "started",
        "text",
        "done",
    ]
    assert [r[0][1] for r in requests] == ["--opencli-session-turn", "--opencli-session-turn"]
    assert "session_id" not in requests[0][2]
    assert (
        requests[0][2]["resume"]["requested"] is False
        and requests[1][2]["resume"]["requested"] is True
    )
    assert requests[0][2]["server_session_id"] == task.session_id
    assert requests[0][2]["message"] == "hello"
    assert requests[0][2]["instructions"] == "be concise"
    assert requests[0][2]["budget"] == {"max_steps": 5}
    assert requests[0][2]["required_capabilities"] == ["streaming"]
    assert requests[0][2]["evidence_requirements"] == ["runtime_events"]
    assert "session_id" not in (await _events(adapter, task))[-1]["result"]


@pytest.mark.asyncio
async def test_rejects_forged_native_ref_and_changed_scope_echo(monkeypatch):
    adapter = IsolatedSessionRuntimeAdapter()
    monkeypatch.setattr(IsolatedSessionRuntimeAdapter, "_probe_sync", classmethod(lambda cls: True))
    with_native = _task(input={**_task().input, "native_session_id": "browser-ref"})
    assert (await _events(adapter, with_native))[-1]["error_type"] == "ProtocolError"
    monkeypatch.setattr(
        "backend.agent_runtimes.isolated_session_adapter._runner_path", lambda: "C:/runner.exe"
    )
    changed = _task()
    proc = _Process(
        [
            {
                "type": "started",
                "task_id": "turn-1",
                "server_session_id": changed.session_id,
                "scope": {
                    "workspace_id": "wrong",
                    "conversation_id": "conv-1",
                    "actor_subject": "user-1",
                },
                "native_resume_ack": True,
                "resumed": False,
            }
        ]
    )

    async def spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    assert (await _events(adapter, changed))[-1]["error_type"] == "ValueError"


_PERSISTENT_RUNNER = r"""
import json
import sys
from pathlib import Path

if sys.argv[1] == "--opencli-session-probe":
    print(json.dumps({
        "type": "readiness", "schema_version": 1,
        "isolation": {"os_isolated": True},
        "authentication": {"authenticated": True},
        "resume": {"persistent_native_resume": True, "scope_bound": True},
        "tool_policy": {"governed_gateway": True},
    }))
    raise SystemExit(0)

sys.stderr.write("fixture diagnostic " * 32_768)
sys.stderr.flush()
request = json.loads(sys.stdin.readline())
state_path = Path(__file__).with_suffix(".state.json")
records = json.loads(state_path.read_text()) if state_path.exists() else {}
session_id = request["server_session_id"]
record = records.get(session_id)
resumed = request["resume"]["requested"]
if resumed != (record is not None) or (record and record["scope"] != request["scope"]):
    raise SystemExit(2)
count = record["count"] + 1 if record else 1
records[session_id] = {"scope": request["scope"], "count": count}
state_path.write_text(json.dumps(records))
for frame in [
    {"type": "started", "server_session_id": session_id, "scope": request["scope"],
     "native_resume_ack": True, "resumed": resumed},
    {"type": "text", "text": "persisted turn " + str(count)},
    {"type": "done"},
]:
    print(json.dumps({**frame, "task_id": request["task_id"]}), flush=True)
"""


async def test_real_child_resume_survives_adapter_restart_and_rejects_scope_change(
    monkeypatch, tmp_path
):
    """Exercise real pipes and disk-backed fake state; attestations remain test fixtures."""
    script = tmp_path / "protocol_fixture.py"
    script.write_text(f"#!{sys.executable}\n{_PERSISTENT_RUNNER}", encoding="utf-8")
    if os.name == "nt":
        runner = tmp_path / "protocol_fixture.cmd"
        runner.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        runner = script
        runner.chmod(0o700)
    monkeypatch.setenv("AGENT_SESSION_ISOLATED_RUNNER", str(runner))
    task = _task()
    # Large input and pre-request stderr must drain concurrently without deadlock.
    task.input["message"] = "x" * 150_000
    task.config["timeout_seconds"] = 5
    first = await _events(IsolatedSessionRuntimeAdapter(), task)
    assert first[-1]["type"] == "done"
    assert first[-1]["result"]["text"] == "persisted turn 1"
    task.task_id = "turn-2"
    task.input["continuation"] = "resume"
    second = await _events(IsolatedSessionRuntimeAdapter(), task)
    assert second[-1]["type"] == "done"
    assert second[-1]["result"]["text"] == "persisted turn 2"
    task.task_id = "turn-3"
    task.input["actor_subject"] = "different-actor"
    rejected = await _events(IsolatedSessionRuntimeAdapter(), task)
    assert rejected[-1]["type"] == "error"
    state = json.loads(script.with_suffix(".state.json").read_text())
    assert state[task.session_id]["count"] == 2
    assert state[task.session_id]["scope"]["actor_subject"] == "user-1"


@pytest.mark.parametrize("close_stream", [False, True])
async def test_cancellation_and_aclose_stop_the_runner(monkeypatch, close_stream):
    monkeypatch.setattr(IsolatedSessionRuntimeAdapter, "_probe_sync", classmethod(lambda cls: True))
    monkeypatch.setattr(
        "backend.agent_runtimes.isolated_session_adapter._runner_path", lambda: "C:/runner.exe"
    )
    process = _Process(hang=True)
    started = asyncio.Event()

    async def drain():
        request = json.loads(process.stdin.data)
        process.stdout.feed_data((json.dumps(_frames(request)[0]) + "\n").encode())

    process.stdin.drain = drain

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    stream = IsolatedSessionRuntimeAdapter().invoke(_task())
    if close_stream:
        assert (await anext(stream))["type"] == "started"
        await stream.aclose()
    else:

        async def consume():
            async for event in stream:
                if event["type"] == "started":
                    started.set()

        execution = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=1)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
    assert process.returncode == -9


@pytest.mark.asyncio
async def test_continuation_is_required(monkeypatch):
    monkeypatch.setattr(IsolatedSessionRuntimeAdapter, "_probe_sync", classmethod(lambda cls: True))
    task = _task(
        input={key: value for key, value in _task().input.items() if key != "continuation"}
    )
    event = (await _events(IsolatedSessionRuntimeAdapter(), task))[-1]
    assert event["error_type"] == "ProtocolError"


@pytest.mark.asyncio
async def test_malformed_and_incomplete_streams_fail_closed(monkeypatch):
    adapter = IsolatedSessionRuntimeAdapter()
    monkeypatch.setattr(IsolatedSessionRuntimeAdapter, "_probe_sync", classmethod(lambda cls: True))
    monkeypatch.setattr(
        "backend.agent_runtimes.isolated_session_adapter._runner_path", lambda: "C:/runner.exe"
    )
    proc = _Process(hang=True)
    proc._hang = False
    proc.stdout.feed_data(b"not-json\n")
    proc.stdout.feed_eof()
    proc.stderr.feed_eof()

    async def spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    events = await _events(adapter, _task())
    assert [event["type"] for event in events] == ["error"] and events[-1][
        "error_type"
    ] == "ValueError"

    adapter = IsolatedSessionRuntimeAdapter()
    proc = _Process([])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    events = await _events(adapter, _task())
    assert events[-1]["error_type"] == "ProtocolError"


@pytest.mark.asyncio
async def test_timeout_and_launch_overrides_and_secret_environment(monkeypatch):
    adapter = IsolatedSessionRuntimeAdapter()
    monkeypatch.setattr(IsolatedSessionRuntimeAdapter, "_probe_sync", classmethod(lambda cls: True))
    monkeypatch.setattr(
        "backend.agent_runtimes.isolated_session_adapter._runner_path", lambda: "C:/runner.exe"
    )
    captured = {}

    async def spawn(*args, **kwargs):
        captured.update(kwargs)
        return _Process(hang=True)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("PRIVATE_AGENT_TOKEN", "must-not-reach-runner")
    timed = await _events(adapter, _task(config={"timeout_seconds": 0.01}))
    assert timed[-1]["error_type"] == "TimeoutError"
    assert "PRIVATE_AGENT_TOKEN" not in captured["env"]
    rejected = await _events(
        adapter, _task(config={"binary": "evil", "args": ["x"], "env": {"X": "Y"}, "cwd": "C:/tmp"})
    )
    assert rejected[-1]["error_type"] == "ConfigError"


def test_registry_and_probe_evidence_are_fail_closed(monkeypatch):
    assert get_runtime("isolated_session").runtime_type == "isolated_session"
    monkeypatch.delenv("AGENT_SESSION_ISOLATED_RUNNER", raising=False)
    assert IsolatedSessionRuntimeAdapter.is_available() is False
    assert IsolatedSessionRuntimeAdapter().validate_config({"args": []})
    valid = {
        "type": "readiness",
        "schema_version": 1,
        "isolation": {"os_isolated": True},
        "authentication": {"authenticated": True},
        "resume": {"persistent_native_resume": True, "scope_bound": True},
        "tool_policy": {"governed_gateway": True},
    }
    assert _probe_valid(valid)
    assert not _probe_valid({**valid, "schema_version": True})


@pytest.mark.parametrize("permissions", [{"unexpected": object()}, {"bulk": "x" * 262_144}])
async def test_invalid_or_oversized_request_never_launches_turn(monkeypatch, permissions):
    monkeypatch.setattr(IsolatedSessionRuntimeAdapter, "_probe_sync", classmethod(lambda cls: True))
    monkeypatch.setattr(
        "backend.agent_runtimes.isolated_session_adapter._runner_path", lambda: "C:/runner.exe"
    )

    async def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("invalid request reached process launch")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    events = await _events(IsolatedSessionRuntimeAdapter(), _task(permissions=permissions))
    assert len(events) == 1
    assert events[0]["error_type"] == "ProtocolError"


@pytest.mark.skipif(os.name != "nt", reason="uses a Windows executable .cmd runner")
@pytest.mark.parametrize("prefix", ["", 'echo {"type":"readiness"}\r\n'])
def test_silent_or_nonterminating_probe_has_a_real_deadline(monkeypatch, tmp_path, prefix):
    """A real child neither exits nor closes stdout; registration must return."""
    runner = tmp_path / "hanging-runner.cmd"
    runner.write_text(f"@echo off\r\n{prefix}ping -n 30 127.0.0.1 >nul\r\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_SESSION_ISOLATED_RUNNER", str(runner))
    monkeypatch.setattr("backend.agent_runtimes.isolated_session_adapter._PROBE_TIMEOUT", 0.2)
    started = time.monotonic()
    assert IsolatedSessionRuntimeAdapter.is_available() is False
    assert time.monotonic() - started < 2
