"""Subprocess adapter for pi (earendil-works/pi) in ``--mode rpc``.

Protocol research (2026-07-03) — kept in this one section so a schema fix is a
one-place edit. Sources: https://github.com/earendil-works/pi,
https://pi.dev/docs/latest/{usage,rpc,json,sessions,settings}.md (fetched via
WebFetch; the raw docs are not vendored into this repo).

CONFIRMED from packages/coding-agent/docs/rpc.md and usage.md:
  * Invocation: ``pi --mode rpc`` — "RPC mode over stdin/stdout".
  * Framing: strict JSONL, ``\\n``-delimited only (doc explicitly warns
    Node's ``readline`` is non-compliant because it also splits on U+2028/
    U+2029 — irrelevant for our line-based asyncio reader, noted for anyone
    porting this later). Clients should tolerate a trailing ``\\r``.
  * Stdin commands are JSON objects, one per line, with a required ``type``
    and an optional ``id`` for request/response correlation. The one we use:
    ``{"type": "prompt", "id": <task_id>, "message": <text>}``.
  * Stdout events are JSONL, one per line, no ``id`` field. Relevant native
    event types and the fields we read:
      - ``message_update`` with ``assistantMessageEvent.type in
        {"text_delta"}`` carrying ``assistantMessageEvent.delta`` (string) ->
        our ``text`` event.
      - ``tool_execution_start`` with ``toolCallId``, ``toolName``, ``args``
        -> our ``tool_call`` event.
      - ``tool_execution_end`` with ``toolCallId``, ``toolName``, ``result``,
        ``isError`` -> our ``tool_result`` event.
      - ``agent_end`` with ``messages`` (array) -> folded into the final
        ``done`` event's ``result`` once the process exits 0.
      - ``response`` (reply to our ``prompt`` command) with ``success`` and,
        on failure, ``error`` -> if ``success is False`` we surface an
        ``error`` event with that message instead of waiting for the process
        to exit.
    Every other native event type (``agent_start``, ``turn_start``/
    ``turn_end``, ``message_start``/``message_end``, ``queue_update``,
    ``compaction_*``, ``auto_retry_*``, ``extension_*``,
    ``tool_execution_update``, and any ``message_update`` sub-type other than
    ``text_delta`` such as ``thinking_delta``/``toolcall_delta``) is skipped
    with a debug log — NOT invented into a new event type, per the closed
    event-set rule in ``base.py``.
  * ``PI_CODING_AGENT_DIR`` (assumed in the original design as a "provider
    override" env var) DOES NOT EXIST in pi's actual settings/providers docs.
    The real, documented env var for redirecting pi's persistent state is
    ``PI_CODING_AGENT_SESSION_DIR`` (overrides the ``sessionDir`` setting;
    default is under the user's home directory). This adapter's
    ``provider_dir`` config key is kept (design notes's name) but is wired to the
    real ``PI_CODING_AGENT_SESSION_DIR`` env var — see ``_compose_env``. If a
    future pi release adds a true provider/model-config directory override,
    fix the one line in ``_compose_env``.
  * Session resume: pi has NO ``--session-id`` flag (also assumed in the GOAL
    doc). Session identity is file-path based: ``--session <path|id>``
    ("Use a specific session file or partial UUID") on the CLI, or the
    ``switch_session`` RPC command with a ``sessionPath``. ``get_state``
    returns a ``sessionId`` (UUID) once a session exists, but there is no way
    to pre-assign an id and reopen purely by that id over RPC — you need the
    session file path. Because our ``AgentTask.session_id`` is a bare
    launcher-assigned string with no path semantics, we do NOT plumb it into
    ``--session`` (that would silently misbehave the moment the id isn't
    also a valid path/partial UUID pi recognizes). Hence
    ``resume_by_id=False`` in this adapter's capabilities — documented here
    rather than silently defaulted, per the task's design doc callout, this
    is the "otherwise leave False and document why" branch.

UNKNOWN / not fully documented: the exact shape of ``AgentMessage`` and tool
``result.content``/``details`` objects referenced by rpc.md are not spelled
out field-by-field in the fetched docs; we only reach into the specific
fields listed above and leave the rest opaque (passed through as-is inside
our own event payloads where relevant, never parsed further).
"""

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from typing import Any

from backend.agent_runtimes.base import (
    AgentTask,
    RuntimeAdapter,
    RuntimeCapabilities,
    event_done,
    event_error,
    event_started,
    event_text,
    event_tool_call,
    event_tool_result,
)
from backend.agent_runtimes.registry import register_runtime

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 300
_KILL_GRACE_SECONDS = 10
_STDERR_TAIL_BYTES = 2048
_READ_ONLY_PROFILE_MODES = frozenset({"observe_only", "suggest_changes"})
_READ_ONLY_TOOLS = "read,grep,find,ls"


@register_runtime
class PiRuntimeAdapter(RuntimeAdapter):
    """Adapter for pi (earendil-works/pi) run as ``<binary> --mode rpc``."""

    runtime_type = "pi"
    capabilities = RuntimeCapabilities(
        transport="stdio",
        streaming=True,
        resume_by_id=False,  # see module docstring: no --session-id in pi's RPC protocol
        checkpoint="none",
        concurrent_sessions=True,
    )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        permission_mode = config.get("permission_mode")
        if permission_mode in _READ_ONLY_PROFILE_MODES:
            unsupported = sorted(set(config) - {"permission_mode", "timeout_seconds"})
            if unsupported:
                errors.append(
                    "read-only permission modes cannot override Fleet launch config: "
                    + ", ".join(unsupported)
                )
        binary = config.get("binary", "pi")
        if not isinstance(binary, str) or not binary:
            errors.append("'binary' must be a non-empty string")
        if "cwd" in config and config["cwd"] is not None and not isinstance(config["cwd"], str):
            errors.append("'cwd' must be a string when provided")
        if "env" in config and config["env"] is not None and not isinstance(config["env"], dict):
            errors.append("'env' must be a dict when provided")
        if "provider_dir" in config and config["provider_dir"] is not None and not isinstance(
            config["provider_dir"], str
        ):
            errors.append("'provider_dir' must be a string when provided")
        if "args" in config and config["args"] is not None:
            args = config["args"]
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                errors.append("'args' must be a list of strings when provided")
        if permission_mode not in {None, *_READ_ONLY_PROFILE_MODES}:
            errors.append("'permission_mode' must be 'observe_only' or 'suggest_changes'")
        if "timeout_seconds" in config and config["timeout_seconds"] is not None:
            timeout = config["timeout_seconds"]
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
                errors.append("'timeout_seconds' must be a positive number when provided")
        return errors

    async def health(self) -> bool:
        return self.is_available()

    @classmethod
    def is_available(cls, binary: str = "pi") -> bool:
        """Cheap sync check used by ``registry.available_runtimes()``."""
        return shutil.which(binary) is not None

    # ── argv / env composition ───────────────────────────────────────────────
    # Split into small methods (OpenAlice CliAdapter pattern cited in the GOAL
    # doc) rather than one monolithic spawn(): argv, env, and the request
    # payload are each independently testable and independently overridable.

    def _compose_argv(self, config: dict[str, Any]) -> list[str]:
        binary = config.get("binary") or "pi"
        extra_args = config.get("args") or []
        # `args` is inserted BEFORE `--mode rpc` so tests can point `binary`
        # at a bare interpreter (e.g. sys.executable) and supply the script
        # path via `args`: argv becomes
        #   [sys.executable, "<fake_pi.py>", "--mode", "rpc"]
        # which is indistinguishable, from asyncio's point of view, from
        # invoking a real `pi` binary directly. This keeps `binary` a plain
        # str (matching cli_channel.py's config shape) with no special-casing
        # for "binary is actually an argv list".
        argv = [binary, *extra_args]
        if config.get("permission_mode") in _READ_ONLY_PROFILE_MODES:
            argv.extend(
                [
                    "--no-extensions",
                    "--no-skills",
                    "--no-prompt-templates",
                    "--tools",
                    _READ_ONLY_TOOLS,
                ]
            )
        return [*argv, "--mode", "rpc"]

    def _compose_env(self, config: dict[str, Any]) -> dict[str, str] | None:
        import os

        extra_env: dict[str, str] = dict(config.get("env") or {})
        provider_dir = config.get("provider_dir")
        if provider_dir:
            # See module docstring: pi has no PI_CODING_AGENT_DIR; the real
            # override is PI_CODING_AGENT_SESSION_DIR.
            extra_env.setdefault("PI_CODING_AGENT_SESSION_DIR", provider_dir)
        if not extra_env:
            return None
        return {**os.environ, **extra_env}

    def _compose_request(self, task: AgentTask) -> dict[str, Any]:
        message = task.input.get("message") if isinstance(task.input, dict) else None
        if message is None:
            message = task.input.get("prompt") if isinstance(task.input, dict) else None
        if message is None:
            message = ""
        if task.instructions:
            message = f"{task.instructions}\n\n{message}".strip()
        return {"type": "prompt", "id": task.task_id, "message": message}

    # ── invoke ────────────────────────────────────────────────────────────────

    async def invoke(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        config = task.config or {}
        config_errors = self.validate_config(config)
        if config_errors:
            yield event_error(task.task_id, "; ".join(config_errors), error_type="ConfigError")
            return
        argv = self._compose_argv(config)
        env = self._compose_env(config)
        cwd = config.get("cwd")
        timeout_seconds = config.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            yield event_error(task.task_id, f"pi binary not found: {argv[0]!r}", error_type=type(exc).__name__)
            return
        except OSError as exc:
            yield event_error(task.task_id, f"failed to spawn pi: {exc}", error_type=type(exc).__name__)
            return

        yield event_started(task.task_id)

        request = self._compose_request(task)
        assert proc.stdin is not None
        try:
            proc.stdin.write((json.dumps(request) + "\n").encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the process may have already exited; exit-code handling below reports it

        accumulated_text: list[str] = []
        saw_error_response = False
        error_message = ""

        async def _read_events() -> AsyncIterator[dict[str, Any]]:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                stripped = line.decode(errors="replace").strip("\r\n")
                if not stripped:
                    continue
                try:
                    native = json.loads(stripped)
                except json.JSONDecodeError:
                    logger.debug("pi_adapter: skipping non-JSON stdout line: %r", stripped[:200])
                    continue
                translated = self._translate_event(task.task_id, native)
                if translated is not None:
                    yield translated

        try:
            async with asyncio.timeout(timeout_seconds):
                async for event in _read_events():
                    if event["type"] == "text":
                        accumulated_text.append(event["text"])
                    if event["type"] == "error":
                        # A failed RPC `response` (e.g. bad model) is terminal:
                        # stop draining stdout and fall through to the single
                        # post-loop error yield below, so we never emit two
                        # error events for one failed run.
                        saw_error_response = True
                        error_message = event["message"]
                        break
                    yield event
        except (TimeoutError, asyncio.CancelledError) as exc:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            yield event_error(
                task.task_id,
                f"pi run timed out after {timeout_seconds}s",
                error_type="TimeoutError",
            )
            return

        returncode = await proc.wait()

        if saw_error_response:
            yield event_error(task.task_id, error_message, error_type="RuntimeInvocationError")
            return

        if returncode != 0:
            stderr_tail = b""
            if proc.stderr is not None:
                stderr_tail = await proc.stderr.read()
            tail = stderr_tail[-_STDERR_TAIL_BYTES:].decode(errors="replace")
            yield event_error(
                task.task_id,
                f"pi exited with code {returncode}: {tail}",
                error_type="ProcessExitError",
            )
            return

        yield event_done(task.task_id, result={"text": "".join(accumulated_text)})

    # ── native event translation ────────────────────────────────────────────

    def _translate_event(self, task_id: str, native: dict[str, Any]) -> dict[str, Any] | None:
        """Translate one pi native JSONL event/response into our closed event
        set. Unknown native types are skipped (return None) with a debug log
        — see the schema table in the module docstring for what's mapped."""
        native_type = native.get("type")

        if native_type == "response":
            if native.get("success") is False:
                return event_error(task_id, native.get("error") or "pi command failed")
            return None

        if native_type == "message_update":
            delta_event = native.get("assistantMessageEvent") or {}
            if delta_event.get("type") == "text_delta":
                return event_text(task_id, delta_event.get("delta", ""))
            return None

        if native_type == "tool_execution_start":
            return event_tool_call(
                task_id,
                name=native.get("toolName", ""),
                args=native.get("args") or {},
                call_id=native.get("toolCallId"),
            )

        if native_type == "tool_execution_end":
            return event_tool_result(
                task_id,
                name=native.get("toolName", ""),
                result=native.get("result"),
                call_id=native.get("toolCallId"),
                is_error=bool(native.get("isError")),
            )

        logger.debug("pi_adapter: skipping unmapped native event type %r", native_type)
        return None
