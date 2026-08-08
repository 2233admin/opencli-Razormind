"""Subprocess adapter for OpenClaw (openclaw) via ``agent`` subcommand.

Transport: ``<binary> agent --agent <id> -m <message> --json`` — a single
agent turn routed through the OpenClaw Gateway (or ``--local`` for the
embedded agent), returning the reply as JSON on stdout.

Protocol notes (verified 2026-08-08 against OpenClaw 2026.7.1-2):
  * Invocation: ``openclaw agent --agent <id> -m "<message>" --json``. A
    session must be selected (``--agent``, ``--session-key``,
    ``--session-id``, or ``--to <E.164>``) — without one the CLI exits with
    "Pass --to <E.164>, --session-key, --session-id, or --agent to choose a
    session". ``--agent`` is the stable, id-based choice; the adapter
    defaults to ``main`` (the default agent) and lets config override it.
  * ``--local`` runs the embedded agent without the Gateway; requires model
    provider API keys in the shell. Defaults OFF so a configured Gateway is
    used when present (the adapter surfaces whatever error the CLI reports).
  * Output: ``--json`` requests JSON, but the CLI also prints startup /
    state-migration / plugin notices to stdout before the payload, and on
    failure (billing, auth, routing) prints diagnostic text instead of JSON.
    The adapter therefore: (1) tries to parse the LAST JSON-looking line of
    stdout; (2) on parse failure, treats stdout as plain text; (3) folds the
    exit code and stderr tail into an ``error`` event when non-zero.
  * The JSON result schema is not documented field-by-field; the adapter
    probes a small set of common reply fields (``text``, ``reply``,
    ``content``, ``message``, ``result``) rather than assuming one shape.

UNKNOWN: exact JSON result shape across OpenClaw versions and whether
``--local`` vs Gateway routing changes it; kept to field probing plus
pass-through so a schema change degrades to plain-text, never a crash.
"""

from __future__ import annotations

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
)
from backend.agent_runtimes.registry import register_runtime

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 300
_KILL_GRACE_SECONDS = 10
_STDERR_TAIL_BYTES = 2048

#: Probing order for extracting the reply text from OpenClaw's JSON output.
_REPLY_FIELDS = ("text", "reply", "content", "message", "result", "response")


def _extract_reply_text(payload: Any) -> str | None:
    """Pull the reply text out of an OpenClaw JSON payload (best-effort).

    Known reply keys are probed first; a dict without any of them is
    recursed into (first dict value that yields text wins), so nested shapes
    like ``{"response": {"content": "..."}}`` still resolve.
    """
    if isinstance(payload, str):
        return payload if payload.strip() else None
    if not isinstance(payload, dict):
        return None
    for key in _REPLY_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = _extract_reply_text(value)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, dict):
            nested = _extract_reply_text(value)
            if nested:
                return nested
    return None


@register_runtime
class OpenClawRuntimeAdapter(RuntimeAdapter):
    """Adapter for OpenClaw run as ``<binary> agent --agent <id> -m <msg>``."""

    runtime_type = "openclaw"
    capabilities = RuntimeCapabilities(
        transport="stdio",
        streaming=False,  # agent subcommand returns the reply, not an event stream
        resume_by_id=False,  # sessions are named/selected via --agent/--session-key, not opaque ids
        checkpoint="none",
        concurrent_sessions=True,
    )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        binary = config.get("binary", "openclaw")
        if not isinstance(binary, str) or not binary:
            errors.append("'binary' must be a non-empty string")
        if "agent_id" in config and config["agent_id"] is not None and not isinstance(
            config["agent_id"], str
        ):
            errors.append("'agent_id' must be a string when provided")
        if "model" in config and config["model"] is not None and not isinstance(config["model"], str):
            errors.append("'model' must be a string when provided")
        if "local" in config and config["local"] is not None and not isinstance(
            config["local"], bool
        ):
            errors.append("'local' must be a boolean when provided")
        if "cwd" in config and config["cwd"] is not None and not isinstance(config["cwd"], str):
            errors.append("'cwd' must be a string when provided")
        if "env" in config and config["env"] is not None and not isinstance(config["env"], dict):
            errors.append("'env' must be a dict when provided")
        if "args" in config and config["args"] is not None:
            args = config["args"]
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                errors.append("'args' must be a list of strings when provided")
        if "timeout_seconds" in config and config["timeout_seconds"] is not None:
            timeout = config["timeout_seconds"]
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
                errors.append("'timeout_seconds' must be a positive number when provided")
        return errors

    async def health(self) -> bool:
        return self.is_available()

    @classmethod
    def is_available(cls, binary: str = "openclaw") -> bool:
        """Cheap sync check used by ``registry.available_runtimes()``."""
        return shutil.which(binary) is not None

    # ── argv / env / request composition ─────────────────────────────────────

    def _compose_argv(self, config: dict[str, Any], message: str) -> list[str]:
        binary = config.get("binary") or "openclaw"
        # `args` inserted right after binary so tests can point `binary` at a
        # bare interpreter and supply a fake-script path via `args`:
        #   [sys.executable, "<fake_openclaw.py>", "agent", "--agent", ...]
        # (subcommand comes after args; a python binary would otherwise treat
        # the subcommand as the module/script to run).
        argv = [binary]
        argv.extend(config.get("args") or [])
        argv.append("agent")
        agent_id = config.get("agent_id") or "main"
        argv.extend(["--agent", agent_id])
        if config.get("local"):
            argv.append("--local")
        model = config.get("model")
        if model:
            argv.extend(["--model", model])
        argv.extend(["-m", message, "--json"])
        return argv

    def _compose_env(self, config: dict[str, Any]) -> dict[str, str] | None:
        import os

        extra_env: dict[str, str] = dict(config.get("env") or {})
        if not extra_env:
            return None
        return {**os.environ, **extra_env}

    def _compose_message(self, task: AgentTask) -> str:
        message = task.input.get("message") if isinstance(task.input, dict) else None
        if message is None:
            message = task.input.get("prompt") if isinstance(task.input, dict) else None
        if message is None:
            message = ""
        if task.instructions:
            message = f"{task.instructions}\n\n{message}".strip()
        return message

    # ── output parsing ────────────────────────────────────────────────────────

    def _parse_stdout(self, stdout: str) -> tuple[str | None, str | None]:
        """Return (reply_text, json_error). JSON best-effort, fallback text."""
        # OpenClaw prints notices before the payload; try the last JSON-looking
        # line first, then a full-document parse.
        candidates: list[str] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                candidates.append(stripped)
        if candidates:
            for candidate in reversed(candidates):
                try:
                    payload = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                text = _extract_reply_text(payload)
                if text:
                    return text, None
                return None, "OpenClaw JSON reply contained no recognized text field"
        return None, None  # non-JSON stdout handled as plain text by caller

    # ── invoke ────────────────────────────────────────────────────────────────

    async def invoke(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        config = task.config or {}
        config_errors = self.validate_config(config)
        if config_errors:
            yield event_error(task.task_id, "; ".join(config_errors), error_type="ConfigError")
            return

        message = self._compose_message(task)
        argv = self._compose_argv(config, message)
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
            yield event_error(
                task.task_id,
                f"openclaw binary not found: {argv[0]!r}",
                error_type=type(exc).__name__,
            )
            return
        except OSError as exc:
            yield event_error(
                task.task_id, f"failed to spawn openclaw: {exc}", error_type=type(exc).__name__
            )
            return

        yield event_started(task.task_id)

        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:  # pragma: no cover - child may have exited already
                pass

        try:
            async with asyncio.timeout(timeout_seconds):
                stdout_bytes = await proc.stdout.read() if proc.stdout is not None else b""
                returncode = await proc.wait()
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
                f"openclaw run timed out after {timeout_seconds}s",
                error_type="TimeoutError",
            )
            return

        stdout_text = stdout_bytes.decode(errors="replace")
        reply, json_error = self._parse_stdout(stdout_text)

        if returncode != 0:
            stderr_tail = b""
            if proc.stderr is not None:
                stderr_tail = await proc.stderr.read()
            tail = stderr_tail[-_STDERR_TAIL_BYTES:].decode(errors="replace")
            detail = reply or tail or stdout_text.strip()
            yield event_error(
                task.task_id,
                f"openclaw exited with code {returncode}: {detail[:500]}",
                error_type="ProcessExitError",
            )
            return

        if reply is None:
            # Non-JSON stdout on a clean exit: surface it as plain text. A
            # json_error means we saw JSON but couldn't extract a reply — that
            # is still a useful diagnostic, so it prefixes the raw stdout.
            body = stdout_text.strip()
            if json_error:
                body = f"{json_error}\n{body}"
            if not body:
                yield event_done(task.task_id, result={"text": ""})
                return
            yield event_text(task.task_id, body)
            yield event_done(task.task_id, result={"text": body})
            return

        yield event_text(task.task_id, reply)
        yield event_done(task.task_id, result={"text": reply})
