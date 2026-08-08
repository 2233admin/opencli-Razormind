"""Tests for backend/agent_runtimes/openclaw_adapter.py using a FAKE openclaw binary.

The fake emulates ``openclaw agent --agent <id> -m <msg> --json``:
prints JSON (or, for failure cases, diagnostic text) to stdout and exits
with a chosen code. `binary` is pointed at `sys.executable` with the fake
script path prepended via `args`.
"""

import json
import sys

import pytest

from backend.agent_runtimes.base import AgentTask
from backend.agent_runtimes.openclaw_adapter import _extract_reply_text
from backend.agent_runtimes.openclaw_adapter import OpenClawRuntimeAdapter

_FAKE_OPENCLAW_JSON = r'''
import json
import sys

args = sys.argv
assert "--agent" in args
assert "--json" in args
idx = args.index("-m")
message = args[idx + 1]
print("[openclaw] startup notice")  # noise before the payload
print(json.dumps({"text": f"ECHO: {message}", "run_id": "r1"}))
'''

_FAKE_OPENCLAW_NESTED = r'''
import json
import sys

print(json.dumps({"response": {"content": "nested reply"}}))
'''

_FAKE_OPENCLAW_NON_JSON = r'''
import sys

print("plain diagnostic output, no json at all")
'''

_FAKE_OPENCLAW_FAIL = r'''
import sys

sys.stderr.write("billing error: no valid subscription\n")
sys.exit(1)
'''

_FAKE_OPENCLAW_SLOW = r'''
import time

time.sleep(30)
'''


def _task(**overrides) -> AgentTask:
    base = dict(
        task_id="t1",
        workflow="default",
        instructions="",
        input={"message": "hello"},
        config={},
    )
    base.update(overrides)
    return AgentTask(**base)


async def _collect(adapter, task):
    events = []
    async for event in adapter.invoke(task):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_json_reply_emits_text_and_done(tmp_path):
    script = tmp_path / "fake_openclaw.py"
    script.write_text(_FAKE_OPENCLAW_JSON, encoding="utf-8")
    adapter = OpenClawRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(config={"binary": sys.executable, "args": [str(script)]}),
    )

    types = [e["type"] for e in events]
    assert types == ["started", "text", "done"]
    text = events[1]
    assert "ECHO: hello" in text["text"]
    assert events[2]["result"] == {"text": text["text"]}


@pytest.mark.asyncio
async def test_nested_json_reply_extracted(tmp_path):
    script = tmp_path / "fake_openclaw.py"
    script.write_text(_FAKE_OPENCLAW_NESTED, encoding="utf-8")
    adapter = OpenClawRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(config={"binary": sys.executable, "args": [str(script)]}),
    )

    assert events[-1]["type"] == "done"
    assert events[-1]["result"] == {"text": "nested reply"}


@pytest.mark.asyncio
async def test_non_json_stdout_falls_back_to_text(tmp_path):
    script = tmp_path / "fake_openclaw.py"
    script.write_text(_FAKE_OPENCLAW_NON_JSON, encoding="utf-8")
    adapter = OpenClawRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(config={"binary": sys.executable, "args": [str(script)]}),
    )

    assert events[-1]["type"] == "done"
    assert "plain diagnostic output" in events[-1]["result"]["text"]


@pytest.mark.asyncio
async def test_nonzero_exit_emits_error_with_stderr(tmp_path):
    script = tmp_path / "fake_openclaw.py"
    script.write_text(_FAKE_OPENCLAW_FAIL, encoding="utf-8")
    adapter = OpenClawRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(config={"binary": sys.executable, "args": [str(script)]}),
    )

    assert events[0]["type"] == "started"
    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "ProcessExitError"
    assert "billing error" in events[-1]["message"]


@pytest.mark.asyncio
async def test_timeout_emits_error(tmp_path):
    script = tmp_path / "fake_openclaw.py"
    script.write_text(_FAKE_OPENCLAW_SLOW, encoding="utf-8")
    adapter = OpenClawRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(
            config={
                "binary": sys.executable,
                "args": [str(script)],
                "timeout_seconds": 1,
            }
        ),
    )

    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_instructions_prepended_to_message(tmp_path):
    script = tmp_path / "fake_openclaw.py"
    script.write_text(_FAKE_OPENCLAW_JSON, encoding="utf-8")
    adapter = OpenClawRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(
            instructions="Be brief.",
            input={"message": "status?"},
            config={"binary": sys.executable, "args": [str(script)]},
        ),
    )

    text = next(e for e in events if e["type"] == "text")
    assert "Be brief." in text["text"]
    assert "status?" in text["text"]


def test_extract_reply_text_probing():
    assert _extract_reply_text("plain") == "plain"
    assert _extract_reply_text({"text": "hi"}) == "hi"
    assert _extract_reply_text({"response": {"content": "nested"}}) == "nested"
    assert _extract_reply_text({"run_id": "r1"}) is None
    assert _extract_reply_text(["not", "a", "dict"]) is None


def test_validate_config_rejects_bad_values():
    adapter = OpenClawRuntimeAdapter()
    errors = adapter.validate_config({"local": "yes", "timeout_seconds": 0})
    assert any("'local' must be a boolean" in e for e in errors)
    assert any("'timeout_seconds' must be a positive number" in e for e in errors)
    assert adapter.validate_config({}) == []


def test_is_available_checks_binary():
    assert OpenClawRuntimeAdapter.is_available(binary="definitely-not-a-real-binary-xyz") is False


def test_runtime_type_and_capabilities():
    adapter = OpenClawRuntimeAdapter()
    assert adapter.runtime_type == "openclaw"
    assert adapter.capabilities.transport == "stdio"
