"""Fail-closed protocol adapter for an administrator-owned isolated runner.

The executable is deliberately selected only by ``AGENT_SESSION_ISOLATED_RUNNER``.
It is a small JSON-stdio boundary; provisioning the OS isolation, credentials,
and governed tool gateway is the runner operator's responsibility.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
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
    event_text,
)
from backend.agent_runtimes.registry import register_runtime

_RUNNER_ENV = "AGENT_SESSION_ISOLATED_RUNNER"
_DEFAULT_TIMEOUT = 120
_MAX_TIMEOUT = 900
_PROBE_TIMEOUT = 5
_MAX_FRAME_BYTES = 64 * 1024
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_TEXT_BYTES = 512 * 1024
_MAX_EVENTS = 10_000
_CLEANUP_TIMEOUT = 2
_SAFE_ENV_KEYS = frozenset(
    {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
)
_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "native_session_id",
        "cli_session_id",
        "session_ref",
        "runner_path",
        "argv",
        "args",
        "env",
        "cwd",
    }
)


def _safe_env() -> dict[str, str]:
    """Do not pass the Agent process's credential-bearing environment onward."""
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _SAFE_ENV_KEYS or key.upper().startswith("LC_")
    }


def _runner_path() -> str:
    raw = os.environ.get(_RUNNER_ENV, "").strip()
    if not raw:
        raise ValueError(f"{_RUNNER_ENV} is required")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{_RUNNER_ENV} must be an absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("isolated session runner is unavailable") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("isolated session runner is not executable")
    return str(resolved)


def _probe_valid(payload: Any) -> bool:
    """Require attestations which a CLI-version check cannot establish."""
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "readiness"
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
    ):
        return False
    isolation = payload.get("isolation")
    authentication = payload.get("authentication")
    resume = payload.get("resume")
    tools = payload.get("tool_policy")
    return (
        isinstance(isolation, dict)
        and isolation.get("os_isolated") is True
        and isinstance(authentication, dict)
        and authentication.get("authenticated") is True
        and isinstance(resume, dict)
        and resume.get("persistent_native_resume") is True
        and resume.get("scope_bound") is True
        and isinstance(tools, dict)
        and tools.get("governed_gateway") is True
    )


def _process_kwargs() -> dict[str, Any]:
    return (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )


def _stop_probe_process(proc: subprocess.Popen[bytes]) -> None:
    """Bound cleanup; the Windows containment supervisor also owns orphan cleanup."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=_CLEANUP_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()
    try:
        proc.wait(timeout=_CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass


async def _stop_process(proc: asyncio.subprocess.Process) -> None:
    """Bound cleanup, targeting the group/tree even after the parent exits."""
    if os.name == "nt" and proc.pid:
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=_CLEANUP_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif proc.pid:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=_CLEANUP_TIMEOUT)
    except TimeoutError:
        pass


@register_runtime
class IsolatedSessionRuntimeAdapter(RuntimeAdapter):
    """JSONL contract only; unavailable until its isolated runner attests readiness."""

    runtime_type = "isolated_session"
    capabilities = RuntimeCapabilities(
        transport="stdio",
        streaming=True,
        resume_by_id=True,
        checkpoint="external",
        concurrent_sessions=True,
        features=frozenset({"isolated_runner", "governed_tools"}),
    )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        if not isinstance(config, dict) or not all(isinstance(key, str) for key in config):
            return ["runtime config must be a string-keyed object"]
        unsupported = sorted(set(config) - {"timeout_seconds"})
        errors = (
            ["controller launch overrides are forbidden: " + ", ".join(unsupported)]
            if unsupported
            else []
        )
        if "timeout_seconds" in config:
            value = config["timeout_seconds"]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 < value <= _MAX_TIMEOUT
            ):
                errors.append(f"'timeout_seconds' must be between 0 and {_MAX_TIMEOUT}")
        return errors

    @classmethod
    def is_available(cls) -> bool:
        return cls._probe_sync()

    @classmethod
    def _probe_sync(cls) -> bool:
        proc: subprocess.Popen[bytes] | None = None
        try:
            deadline = time.monotonic() + _PROBE_TIMEOUT
            runner = _runner_path()
            proc = subprocess.Popen(
                [runner, "--opencli-session-probe"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                env=_safe_env(),
                **_process_kwargs(),
            )
            assert proc.stdout is not None
            output: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)

            def read_limited() -> None:
                try:
                    chunks = bytearray()
                    while len(chunks) <= _MAX_FRAME_BYTES:
                        chunk = proc.stdout.read(min(4096, _MAX_FRAME_BYTES + 1 - len(chunks)))
                        if not chunk:
                            break
                        chunks.extend(chunk)
                    output.put(bytes(chunks))
                except BaseException as exc:  # pipe closure during timeout is expected
                    output.put(exc)

            threading.Thread(target=read_limited, daemon=True).start()
            try:
                stdout_or_error = output.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                return False
            if isinstance(stdout_or_error, BaseException):
                return False
            stdout = stdout_or_error
            remaining = deadline - time.monotonic()
            if (
                remaining <= 0
                or len(stdout) > _MAX_FRAME_BYTES
                or proc.wait(timeout=remaining) != 0
            ):
                return False
            return _probe_valid(json.loads(stdout.decode("utf-8")))
        except (
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return False
        finally:
            if proc is not None:
                _stop_probe_process(proc)

    async def health(self) -> bool:
        return await asyncio.to_thread(self._probe_sync)

    async def readiness(self, config: dict[str, Any] | None = None) -> RuntimeReadiness:
        errors = self.validate_config(config or {})
        ready = not errors and await asyncio.to_thread(self._probe_sync)
        return RuntimeReadiness(
            runtime=self.runtime_type,
            capability_id="runtime.isolated_session",
            status="ready" if ready else "blocked",
            binary_present=bool(ready),
            reason_code=None if ready else ("invalid_config" if errors else "runner_not_attested"),
            reason=None
            if ready
            else (
                "; ".join(errors)
                if errors
                else (
                    "runner is absent or lacks required isolation, authentication, "
                    "resume, scope, or tool-gateway attestation"
                )
            ),
        )

    def _scope(self, task: AgentTask) -> tuple[tuple[str, str, str], bool]:
        if not isinstance(task.input, dict):
            raise ValueError("task input must be an object")
        forbidden = sorted(_FORBIDDEN_INPUT_KEYS & set(task.input))
        if forbidden:
            raise ValueError("browser-controlled native session or launch fields are forbidden")
        values = tuple(
            task.input.get(key) for key in ("workspace_id", "conversation_id", "actor_subject")
        )
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("workspace_id, conversation_id, and actor_subject are required")
        try:
            uuid.UUID(task.session_id or "")
        except (ValueError, AttributeError):
            raise ValueError("AgentTask.session_id must be a server AgentSession UUID")
        scope = (values[0], values[1], values[2])
        continuation = task.input.get("continuation")
        if not isinstance(continuation, str) or continuation not in {"start", "resume"}:
            raise ValueError("continuation must be 'start' or 'resume'")
        return scope, continuation == "resume"

    @staticmethod
    def _message(task: AgentTask) -> str:
        message = task.input.get("message", task.input.get("prompt", ""))
        if not isinstance(message, str):
            raise ValueError("message must be a string")
        if not isinstance(task.instructions, str):
            raise ValueError("instructions must be a string")
        return message

    async def invoke(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        errors = self.validate_config(task.config or {})
        if errors:
            yield event_error(task.task_id, "; ".join(errors), "ConfigError")
            return
        try:
            scope, resumed = self._scope(task)
            runner = _runner_path()
            message = self._message(task)
        except ValueError as exc:
            yield event_error(task.task_id, str(exc), "ProtocolError")
            return
        if not await asyncio.to_thread(self._probe_sync):
            yield event_error(
                task.task_id, "isolated runner is not attested ready", "ReadinessError"
            )
            return
        request = {
            "schema_version": 1,
            "type": "turn",
            "task_id": task.task_id,
            "server_session_id": task.session_id,
            "scope": {
                "workspace_id": scope[0],
                "conversation_id": scope[1],
                "actor_subject": scope[2],
            },
            "resume": {"requested": resumed},
            "instructions": task.instructions,
            "message": message,
            "provider": task.provider,
            "model": task.model,
            "permissions": task.permissions,
            "budget": task.budget,
            "required_capabilities": task.required_capabilities,
            "evidence_requirements": task.evidence_requirements,
        }
        try:
            encoded_request = (
                json.dumps(request, allow_nan=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            if len(encoded_request) > _MAX_REQUEST_BYTES:
                raise ValueError("request exceeds protocol limit")
        except (TypeError, ValueError, UnicodeEncodeError):
            yield event_error(task.task_id, "invalid or oversized runner request", "ProtocolError")
            return
        timeout = float((task.config or {}).get("timeout_seconds", _DEFAULT_TIMEOUT))
        proc: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[None] | None = None
        try:
            async with asyncio.timeout(timeout):
                proc = await asyncio.create_subprocess_exec(
                    runner,
                    "--opencli-session-turn",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_safe_env(),
                    **_process_kwargs(),
                )
                assert proc.stdin and proc.stdout and proc.stderr

                async def drain_stderr() -> None:
                    while await proc.stderr.read(4096):
                        pass  # drain continuously, but never surface untrusted diagnostics

                stderr_task = asyncio.create_task(drain_stderr())
                proc.stdin.write(encoded_request)
                await proc.stdin.drain()
                proc.stdin.close()
                acked = False
                terminal = False
                terminal_kind: str | None = None
                text_parts: list[str] = []
                text_bytes = 0
                event_count = 0
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    if len(line) > _MAX_FRAME_BYTES:
                        raise ValueError("runner emitted an oversized frame")
                    try:
                        frame = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError("runner emitted malformed JSONL") from exc
                    if not isinstance(frame, dict):
                        raise ValueError("runner emitted a non-object frame")
                    if frame.get("task_id") != task.task_id:
                        raise ValueError("runner frame task binding does not match request")
                    event_count += 1
                    if event_count > _MAX_EVENTS:
                        raise ValueError("runner emitted too many frames")
                    kind = frame.get("type")
                    if kind == "started":
                        if (
                            acked
                            or frame.get("task_id") != task.task_id
                            or frame.get("server_session_id") != task.session_id
                            or frame.get("scope") != request["scope"]
                            or frame.get("native_resume_ack") is not True
                            or frame.get("resumed") is not resumed
                        ):
                            raise ValueError("runner did not acknowledge the scoped native resume")
                        acked = True
                        yield event_started(task.task_id)
                    elif kind == "text":
                        if not acked or terminal or not isinstance(frame.get("text"), str):
                            raise ValueError("invalid text frame")
                        text_parts.append(frame["text"])
                        text_bytes += len(frame["text"].encode("utf-8"))
                        if text_bytes > _MAX_TEXT_BYTES:
                            raise ValueError("runner emitted too much text")
                        yield event_text(task.task_id, frame["text"])
                    elif kind in {"done", "error"}:
                        if not acked or terminal:
                            raise ValueError("invalid terminal frame")
                        terminal = True
                        terminal_kind = kind
                    else:
                        raise ValueError("unsupported runner frame")
                if not terminal:
                    yield event_error(
                        task.task_id,
                        "runner stream ended without one terminal frame",
                        "ProtocolError",
                    )
                elif await proc.wait() != 0:
                    # A terminal result is not accepted from a runner whose process failed.
                    yield event_error(
                        task.task_id, "isolated runner exited unsuccessfully", "ProcessExitError"
                    )
                elif terminal_kind == "done":
                    yield event_done(
                        task.task_id, {"runtime": self.runtime_type, "text": "".join(text_parts)}
                    )
                else:
                    yield event_error(
                        task.task_id, "isolated runner reported failure", "RunnerError"
                    )
        except TimeoutError:
            yield event_error(task.task_id, "isolated runner timed out", "TimeoutError")
        except (OSError, ValueError) as exc:
            yield event_error(task.task_id, "isolated runner protocol failed", type(exc).__name__)
        finally:
            if proc is not None:
                await asyncio.shield(_stop_process(proc))
            if stderr_task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(stderr_task), timeout=5)
                except Exception:
                    stderr_task.cancel()
