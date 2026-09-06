"""Registered-Agent adapter for the local Claude Code CLI.

Claude Code is launched only on the edge machine that owns the operator's
local login and browser tools.  The control plane receives the normalized
runtime event stream; it never receives provider credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from backend.agent_runtimes.base import (
    AgentTask,
    RuntimeAdapter,
    RuntimeCapabilities,
    RuntimeReadiness,
    event_done,
    event_error,
    event_started,
    event_state,
    event_text,
    event_tool_call,
    event_tool_result,
)
from backend.agent_runtimes.registry import register_runtime

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 1800
_MAX_TIMEOUT_SECONDS = 3600
_VERSION_TIMEOUT_SECONDS = 5
_KILL_GRACE_SECONDS = 10
_STDERR_TAIL_BYTES = 2048
_CAPABILITY_ID = "runtime.claude-code"
_PERMISSION_MODES = frozenset({"observe_only", "suggest_changes", "approval_required", "full_auto"})
_CLAUDE_PERMISSION_MODES = {
    "observe_only": "plan",
    "suggest_changes": "plan",
    "approval_required": "manual",
    "full_auto": "auto",
}
_VERSION_RE = re.compile(r"(?:Claude Code\s+)?([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)")


@register_runtime
class ClaudeCodeRuntimeAdapter(RuntimeAdapter):
    """Run ``claude -p --output-format stream-json`` on a local Agent node."""

    runtime_type = "claude-code"
    capabilities = RuntimeCapabilities(
        transport="stdio",
        streaming=True,
        resume_by_id=False,
        checkpoint="none",
        concurrent_sessions=True,
        features=frozenset({"tool_events", "model_selection", "workspace_read", "workspace_write"}),
    )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        binary = config.get("binary", "claude")
        if not isinstance(binary, str) or not binary.strip():
            errors.append("'binary' must be a non-empty string")
        elif "\x00" in binary:
            errors.append("'binary' must not contain NUL bytes")

        for key in ("cwd", "project_root"):
            if key in config and config[key] is not None:
                value = config[key]
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"'{key}' must be a non-empty string when provided")
                elif "\x00" in value:
                    errors.append(f"'{key}' must not contain NUL bytes")

        if "args" in config and config["args"] is not None:
            args = config["args"]
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                errors.append("'args' must be a list of strings when provided")
        if "chrome" in config and not isinstance(config["chrome"], bool):
            errors.append("'chrome' must be a boolean when provided")
        permission_mode = config.get("permission_mode")
        if permission_mode is not None and permission_mode not in _PERMISSION_MODES:
            errors.append(
                "'permission_mode' must be one of " + ", ".join(sorted(_PERMISSION_MODES))
            )
        if "timeout_seconds" in config and config["timeout_seconds"] is not None:
            timeout = config["timeout_seconds"]
            if (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or not 0 < timeout <= _MAX_TIMEOUT_SECONDS
            ):
                errors.append(
                    f"'timeout_seconds' must be between 0 and {_MAX_TIMEOUT_SECONDS} when provided"
                )
        return errors

    async def health(self) -> bool:
        return self.is_available()

    @classmethod
    def is_available(cls, binary: str = "claude") -> bool:
        if not isinstance(binary, str) or not binary or "\x00" in binary:
            return False
        return shutil.which(binary) is not None

    async def readiness(self, config: dict[str, Any] | None = None) -> RuntimeReadiness:
        config = config or {}
        errors = self.validate_config(config)
        if errors:
            return RuntimeReadiness(
                runtime=self.runtime_type,
                capability_id=_CAPABILITY_ID,
                status="blocked",
                binary_present=False,
                reason_code="invalid_config",
                reason="; ".join(errors),
            )

        binary = config.get("binary") or "claude"
        resolved_binary = shutil.which(binary)
        if resolved_binary is None:
            return RuntimeReadiness(
                runtime=self.runtime_type,
                capability_id=_CAPABILITY_ID,
                status="blocked",
                binary_present=False,
                reason_code="missing_binary",
                reason=f"Claude Code binary not found: {binary!r}",
            )
        try:
            project_root, cwd = self._resolve_paths(config)
        except ValueError as exc:
            return RuntimeReadiness(
                runtime=self.runtime_type,
                capability_id=_CAPABILITY_ID,
                status="blocked",
                binary_present=True,
                permitted_project_root=self._display_path(config.get("project_root")),
                working_directory=self._display_path(config.get("cwd")),
                reason_code="invalid_path",
                reason=str(exc),
            )
        version = await self._detect_version(resolved_binary, config.get("args") or [])
        if version is None:
            return RuntimeReadiness(
                runtime=self.runtime_type,
                capability_id=_CAPABILITY_ID,
                status="blocked",
                binary_present=True,
                permitted_project_root=str(project_root),
                working_directory=str(cwd),
                reason_code="version_probe_failed",
                reason="Claude Code binary did not complete a compatible version probe",
            )
        return RuntimeReadiness(
            runtime=self.runtime_type,
            capability_id=_CAPABILITY_ID,
            status="ready",
            binary_present=True,
            version=version,
            permitted_project_root=str(project_root),
            working_directory=str(cwd),
        )

    def _compose_prompt(self, task: AgentTask) -> str:
        payload = task.input if isinstance(task.input, dict) else {}
        message = payload.get("message") or payload.get("prompt") or ""
        if not isinstance(message, str):
            message = str(message)
        return f"{task.instructions}\n\n{message}".strip() if task.instructions else message

    def _compose_argv(
        self,
        config: dict[str, Any],
        prompt: str = "",
        *,
        model: str | None = None,
    ) -> list[str]:
        binary = config.get("binary") or "claude"
        argv = [
            binary,
            *(config.get("args") or []),
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
        ]
        permission_mode = config.get("permission_mode")
        if permission_mode in _CLAUDE_PERMISSION_MODES:
            argv.extend(("--permission-mode", _CLAUDE_PERMISSION_MODES[permission_mode]))
        if config.get("chrome") is True:
            argv.append("--chrome")
        if model:
            argv.extend(("--model", model))
        argv.append(prompt)
        return argv

    async def invoke(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        config = task.config or {}
        errors = self.validate_config(config)
        if errors:
            yield event_error(task.task_id, "; ".join(errors), error_type="ConfigError")
            return

        binary = config.get("binary") or "claude"
        resolved_binary = shutil.which(binary)
        if resolved_binary is None:
            yield event_error(
                task.task_id,
                f"Claude Code binary not found: {binary!r}",
                error_type="FileNotFoundError",
            )
            return
        if task.provider not in {None, "anthropic", "claude", "claude-code"}:
            yield event_error(
                task.task_id,
                f"Claude Code runtime does not support provider {task.provider!r}",
                error_type="ConfigError",
            )
            return
        try:
            _project_root, cwd = self._resolve_paths(config)
        except ValueError as exc:
            yield event_error(task.task_id, str(exc), error_type="PathError")
            return

        timeout_seconds = config.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS
        version = await self._detect_version(
            resolved_binary,
            config.get("args") or [],
            timeout_seconds=min(timeout_seconds, _VERSION_TIMEOUT_SECONDS),
        )
        argv = self._compose_argv(config, self._compose_prompt(task), model=task.model)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
        except FileNotFoundError as exc:
            yield event_error(
                task.task_id, f"Claude Code binary not found: {binary!r}", type(exc).__name__
            )
            return
        except OSError as exc:
            yield event_error(
                task.task_id, f"failed to spawn Claude Code: {exc}", type(exc).__name__
            )
            return

        yield event_started(task.task_id)
        yield event_state(
            task.task_id,
            {
                "runtime": self.runtime_type,
                "claude_code_version": version,
                "working_directory": str(cwd),
            },
        )
        accumulated_text: list[str] = []
        native_error: str | None = None

        try:
            assert proc.stdout is not None
            async with asyncio.timeout(timeout_seconds):
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
                        logger.debug(
                            "claude_code_adapter: skipping non-JSON stdout line: %r", stripped[:200]
                        )
                        continue
                    if not isinstance(native, dict):
                        continue
                    for event in self._translate_event(task.task_id, native, accumulated_text):
                        if event["type"] == "error":
                            native_error = str(
                                event.get("message") or "Claude Code reported an error"
                            )
                            break
                        yield event
                    if native_error is not None:
                        break
        except (TimeoutError, asyncio.CancelledError) as exc:
            await self._stop_process(proc)
            if isinstance(exc, asyncio.CancelledError):
                raise
            yield event_error(
                task.task_id,
                f"Claude Code run timed out after {timeout_seconds}s",
                error_type="TimeoutError",
            )
            return

        returncode = await proc.wait()
        if native_error is not None:
            yield event_error(task.task_id, native_error, error_type="RuntimeInvocationError")
            return
        if returncode != 0:
            stderr_tail = b""
            if proc.stderr is not None:
                stderr_tail = await proc.stderr.read()
            tail = stderr_tail[-_STDERR_TAIL_BYTES:].decode(errors="replace")
            detail = f": {tail}" if tail else ""
            yield event_error(
                task.task_id,
                f"Claude Code exited with code {returncode}{detail}",
                error_type="ProcessExitError",
            )
            return
        yield event_done(
            task.task_id,
            result={
                "runtime": self.runtime_type,
                "claude_code_version": version,
                "exit_code": returncode,
                "text": "".join(accumulated_text),
            },
        )

    def _translate_event(
        self,
        task_id: str,
        native: dict[str, Any],
        accumulated_text: list[str],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        native_type = native.get("type")
        if native_type == "system":
            subtype = native.get("subtype")
            if subtype == "init":
                state = {
                    key: native[key]
                    for key in ("model", "cwd", "session_id", "permissionMode")
                    if key in native
                }
                if state:
                    events.append(event_state(task_id, state))
            return events

        if native_type == "assistant":
            message = native.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                return events
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "text" and isinstance(item.get("text"), str):
                    text = item["text"]
                    accumulated_text.append(text)
                    events.append(event_text(task_id, text))
                elif item_type == "tool_use":
                    events.append(
                        event_tool_call(
                            task_id,
                            name=str(item.get("name") or "claude_tool"),
                            args=item.get("input") if isinstance(item.get("input"), dict) else {},
                            call_id=item.get("id"),
                        )
                    )
            return events

        if native_type == "user":
            message = native.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                return events
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool_result":
                    continue
                result = item.get("content")
                events.append(
                    event_tool_result(
                        task_id,
                        name=str(item.get("tool_name") or "claude_tool"),
                        result=result,
                        call_id=item.get("tool_use_id"),
                        is_error=bool(item.get("is_error")),
                    )
                )
            return events

        if native_type == "result" and native.get("is_error"):
            events.append(
                event_error(
                    task_id,
                    str(native.get("result") or native.get("subtype") or "Claude Code failed"),
                    error_type="RuntimeInvocationError",
                )
            )
        return events

    async def _detect_version(
        self,
        binary: str,
        args: list[str] | None = None,
        timeout_seconds: float = _VERSION_TIMEOUT_SECONDS,
    ) -> str | None:
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                *(args or []),
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            return None
        except OSError:
            return None
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        if proc.returncode != 0:
            return None
        text = stdout.decode(errors="replace") if stdout else ""
        match = _VERSION_RE.search(text)
        return match.group(1) if match else None

    async def _stop_process(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    def _resolve_paths(self, config: dict[str, Any]) -> tuple[Path, Path]:
        project_root_raw = config.get("project_root") or config.get("cwd") or str(Path.cwd())
        cwd_raw = config.get("cwd") or project_root_raw
        project_root = Path(project_root_raw).expanduser().resolve()
        cwd = Path(cwd_raw).expanduser().resolve()
        if not project_root.is_dir():
            raise ValueError(f"permitted project root is not a directory: {project_root}")
        if not cwd.is_dir():
            raise ValueError(f"working directory is not a directory: {cwd}")
        try:
            cwd.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(
                f"working directory {cwd} is outside permitted project root {project_root}"
            ) from exc
        return project_root, cwd

    @staticmethod
    def _display_path(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return str(Path(value).expanduser().resolve())


__all__ = ["ClaudeCodeRuntimeAdapter"]
