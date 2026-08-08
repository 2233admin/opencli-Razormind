"""Subprocess adapter for Hermes Agent (hermes-agent) in one-shot mode.

Transport: ``<binary> -z <prompt>`` (``--oneshot``) — a single prompt whose
final response text is printed to stdout and nothing else. No banner, no
spinner, no session_id line (see ``hermes --help``). This makes Hermes a
drop-in stdio runtime like pi's ``--mode rpc``, but with a simpler contract:
one prompt in, final text out.

Protocol notes (verified 2026-08-08 against Hermes Agent v0.20.0):
  * Invocation: ``hermes -z "<message>" [--safe-mode] [-m <model>]
    [--provider <provider>]``. ``-z`` prints ONLY the final response text to
    stdout (tools/memory still run inside the agent; only the reply is
    emitted). Exit code 0 on success.
  * Streaming: Hermes one-shot mode does not emit intermediate events to
    stdout (no JSONL event stream like pi's RPC mode). The adapter therefore
    accumulates the full stdout as a single ``text`` event and folds it into
    the terminal ``done`` event's ``result`` — matching how pi_adapter
    accumulates ``text_delta`` events. ``capabilities.streaming`` is False
    because the underlying transport cannot surface partial output, not
    because we chose not to.
  * Resume: ``hermes --resume <session>`` / ``-c`` resume a *named* Hermes
    session, not a launcher-assigned ``AgentTask.session_id``. Mapping our
    opaque id onto a session name would silently create unexpected
    continuations, so ``resume_by_id=False`` (same documented-opt-out as
    pi_adapter) — a fresh one-shot per task.
  * ``--usage-file`` writes a JSON usage report (cost/tokens/model) after the
    run; wired as an optional ``usage_file`` config key so pipelines can
    account for spend without parsing stdout.

UNKNOWN: the exact JSON shape of ``--usage-file`` (v0.20.0 writes it but the
schema is not documented field-by-field); the adapter passes the file through
unparsed and leaves it on disk for callers.
"""

from __future__ import annotations

import asyncio
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


@register_runtime
class HermesRuntimeAdapter(RuntimeAdapter):
    """Adapter for Hermes Agent run as ``<binary> -z <prompt>``."""

    runtime_type = "hermes"
    capabilities = RuntimeCapabilities(
        transport="stdio",
        streaming=False,  # one-shot prints final text only; no partial events
        resume_by_id=False,  # hermes --resume takes a named session, not our opaque id
        checkpoint="none",
        concurrent_sessions=True,
    )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        binary = config.get("binary", "hermes")
        if not isinstance(binary, str) or not binary:
            errors.append("'binary' must be a non-empty string")
        if "cwd" in config and config["cwd"] is not None and not isinstance(config["cwd"], str):
            errors.append("'cwd' must be a string when provided")
        if "env" in config and config["env"] is not None and not isinstance(config["env"], dict):
            errors.append("'env' must be a dict when provided")
        if "model" in config and config["model"] is not None and not isinstance(config["model"], str):
            errors.append("'model' must be a string when provided")
        if "provider" in config and config["provider"] is not None and not isinstance(
            config["provider"], str
        ):
            errors.append("'provider' must be a string when provided")
        if "usage_file" in config and config["usage_file"] is not None and not isinstance(
            config["usage_file"], str
        ):
            errors.append("'usage_file' must be a string when provided")
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
    def is_available(cls, binary: str = "hermes") -> bool:
        """Cheap sync check used by ``registry.available_runtimes()``."""
        return shutil.which(binary) is not None

    # ── argv / env / request composition ─────────────────────────────────────

    def _compose_argv(self, config: dict[str, Any], message: str) -> list[str]:
        binary = config.get("binary") or "hermes"
        argv = [binary]
        model = config.get("model")
        if model:
            argv.extend(["-m", model])
        provider = config.get("provider")
        if provider:
            argv.extend(["--provider", provider])
        # `args` inserted before the prompt so tests can point `binary` at a
        # bare interpreter and supply a fake-script path via `args`:
        #   [sys.executable, "<fake_hermes.py>", "-z", "<prompt>"]
        argv.extend(config.get("args") or [])
        usage_file = config.get("usage_file")
        if usage_file:
            argv.extend(["--usage-file", usage_file])
        argv.extend(["-z", message])
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
                task.task_id, f"hermes binary not found: {argv[0]!r}", error_type=type(exc).__name__
            )
            return
        except OSError as exc:
            yield event_error(
                task.task_id, f"failed to spawn hermes: {exc}", error_type=type(exc).__name__
            )
            return

        yield event_started(task.task_id)

        # One-shot mode reads nothing from stdin; close it so the child never
        # waits on us.
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
                f"hermes run timed out after {timeout_seconds}s",
                error_type="TimeoutError",
            )
            return

        text = stdout_bytes.decode(errors="replace").strip()

        if returncode != 0:
            stderr_tail = b""
            if proc.stderr is not None:
                stderr_tail = await proc.stderr.read()
            tail = stderr_tail[-_STDERR_TAIL_BYTES:].decode(errors="replace")
            yield event_error(
                task.task_id,
                f"hermes exited with code {returncode}: {tail}",
                error_type="ProcessExitError",
            )
            return

        if text:
            yield event_text(task.task_id, text)
        yield event_done(task.task_id, result={"text": text})
