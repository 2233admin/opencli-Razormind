"""Tests for backend/agent_runtimes/hermes_adapter.py using a FAKE hermes binary.

The fake is a small Python script written to tmp_path that emulates
``hermes -z <prompt>`` one-shot semantics: prints the final response text to
stdout and exits 0. We point the adapter's `binary` config at
`sys.executable` and prepend the fake script path via `args` — see
HermesRuntimeAdapter._compose_argv for why that composition was chosen.
"""

import asyncio
import sys

import pytest

from backend.agent_runtimes.base import AgentTask
from backend.agent_runtimes.hermes_adapter import HermesRuntimeAdapter

_FAKE_HERMES_HAPPY = r'''
import sys

# One-shot mode: reply text on stdout, exit 0. Echo the prompt marker so the
# test can assert the composed message reached the fake.
args = sys.argv
assert "-z" in args, f"expected -z flag, got argv={args}"
idx = args.index("-z")
prompt = args[idx + 1]
print(f"REPLY_TO: {prompt}")
'''

_FAKE_HERMES_FAIL = r'''
import sys

sys.stderr.write("model provider auth failed\n")
sys.exit(1)
'''

_FAKE_HERMES_SLOW = r'''
import time

time.sleep(30)
'''

_FAKE_HERMES_EMPTY = r'''
import sys
# exit 0 with no stdout
'''

_FAKE_HERMES_USAGE = r'''
import json
import sys

idx = sys.argv.index("--usage-file")
with open(sys.argv[idx + 1], "w") as fh:
    json.dump({"cost": 0.01, "model": "test"}, fh)
print("done")
'''


def _adapter(binary_script: str, tmp_path, **config) -> HermesRuntimeAdapter:
    script = tmp_path / "fake_hermes.py"
    script.write_text(binary_script, encoding="utf-8")
    return HermesRuntimeAdapter()


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
async def test_happy_path_emits_text_and_done(tmp_path):
    script = tmp_path / "fake_hermes.py"
    script.write_text(_FAKE_HERMES_HAPPY, encoding="utf-8")
    adapter = HermesRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(config={"binary": sys.executable, "args": [str(script)]}),
    )

    types = [e["type"] for e in events]
    assert types == ["started", "text", "done"]
    text = events[1]
    assert text["text"].startswith("REPLY_TO:")
    assert "hello" in text["text"]
    assert events[2]["result"] == {"text": text["text"]}


@pytest.mark.asyncio
async def test_instructions_prepended_to_message(tmp_path):
    script = tmp_path / "fake_hermes.py"
    script.write_text(_FAKE_HERMES_HAPPY, encoding="utf-8")
    adapter = HermesRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(
            instructions="Follow the contract.",
            input={"message": "do the thing"},
            config={"binary": sys.executable, "args": [str(script)]},
        ),
    )

    text = next(e for e in events if e["type"] == "text")
    assert "Follow the contract." in text["text"]
    assert "do the thing" in text["text"]


@pytest.mark.asyncio
async def test_nonzero_exit_emits_error(tmp_path):
    script = tmp_path / "fake_hermes.py"
    script.write_text(_FAKE_HERMES_FAIL, encoding="utf-8")
    adapter = HermesRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(config={"binary": sys.executable, "args": [str(script)]}),
    )

    assert events[0]["type"] == "started"
    assert events[-1]["type"] == "error"
    err = events[-1]
    assert "exited with code 1" in err["message"]
    assert err["error_type"] == "ProcessExitError"
    assert "model provider auth failed" in err["message"]


@pytest.mark.asyncio
async def test_timeout_emits_error(tmp_path):
    script = tmp_path / "fake_hermes.py"
    script.write_text(_FAKE_HERMES_SLOW, encoding="utf-8")
    adapter = HermesRuntimeAdapter()
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
    assert "timed out" in events[-1]["message"]


@pytest.mark.asyncio
async def test_empty_stdout_still_done(tmp_path):
    script = tmp_path / "fake_hermes.py"
    script.write_text(_FAKE_HERMES_EMPTY, encoding="utf-8")
    adapter = HermesRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(config={"binary": sys.executable, "args": [str(script)]}),
    )

    assert events[-1]["type"] == "done"
    assert events[-1]["result"] == {"text": ""}


@pytest.mark.asyncio
async def test_usage_file_config_passed_through(tmp_path):
    script = tmp_path / "fake_hermes.py"
    script.write_text(_FAKE_HERMES_USAGE, encoding="utf-8")
    usage_path = tmp_path / "usage.json"
    adapter = HermesRuntimeAdapter()
    events = await _collect(
        adapter,
        _task(
            config={
                "binary": sys.executable,
                "args": [str(script)],
                "usage_file": str(usage_path),
            }
        ),
    )

    assert events[-1]["type"] == "done"
    assert usage_path.exists()
    import json

    payload = json.loads(usage_path.read_text(encoding="utf-8"))
    assert payload["model"] == "test"


def test_validate_config_rejects_bad_values():
    adapter = HermesRuntimeAdapter()
    errors = adapter.validate_config({"model": 123, "timeout_seconds": -1})
    assert any("'model' must be a string" in e for e in errors)
    assert any("'timeout_seconds' must be a positive number" in e for e in errors)
    assert adapter.validate_config({}) == []


def test_is_available_checks_binary():
    assert HermesRuntimeAdapter.is_available(binary="definitely-not-a-real-binary-xyz") is False


def test_runtime_type_and_capabilities():
    adapter = HermesRuntimeAdapter()
    assert adapter.runtime_type == "hermes"
    assert adapter.capabilities.transport == "stdio"
    assert adapter.capabilities.streaming is False
