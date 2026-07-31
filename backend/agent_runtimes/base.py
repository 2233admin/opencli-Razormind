"""Contract for pluggable agent runtimes (LangGraph, VoltAgent, pi, ...).

Mirrors ``backend/channels/base.py``'s split: a frozen ``Capabilities`` dataclass
the caller branches on (never ``isinstance``), a small task/result value-object
pair, and an ``ABC`` adapters implement. The three target frameworks
(LangGraph, VoltAgent, pi) span two languages and three transport shapes, so
the abstraction has to be a *process/protocol* contract, not a Python import.
Each adapter owns exactly one framework's native stream and translates it into
the closed event set below; nothing outside the adapter ever sees a
framework-native shape.

Event design note: normalize the *protocol*, not the output. The event set is
intentionally tiny and closed —
``EVENT_TYPES`` — so adapters cannot invent new shapes ad hoc. Use the
``event_*`` helper constructors below rather than hand-building event dicts;
that is what prevents typos in ``type`` strings and missing ``task_id`` fields
from ever reaching a caller.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

#: Closed tagged-union of runtime event types. Adapters MUST NOT emit any
#: `type` outside this set — an unrecognized native event from the underlying
#: framework is either mapped onto one of these or dropped (with a debug log),
#: never passed through verbatim.
EVENT_TYPES: frozenset[str] = frozenset(
    {"started", "text", "tool_call", "tool_result", "state", "done", "error"}
)


@dataclass(frozen=True)
class RuntimeCapabilities:
    """What an agent runtime adapter can do; callers branch on this
    declaration, exactly like ``channels.base.Capabilities``."""

    transport: str  # "stdio" | "http" | "inprocess"
    streaming: bool = True
    resume_by_id: bool = False  # can reopen a session by launcher-assigned id
    checkpoint: str = "none"  # none | memory | sqlite | postgres
    concurrent_sessions: bool = True


@dataclass
class AgentTask:
    """One agent run request handed to a ``RuntimeAdapter.invoke()``."""

    task_id: str
    workflow: str  # runtime-native workflow/agent identifier
    instructions: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    # Runtime-specific settings such as model, tools, cwd, or sidecar config.
    config: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None  # resume handle


# ── Event constructors ───────────────────────────────────────────────────────
# Plain dicts (not a dataclass) so events serialize to JSON for the ws wire
# protocol with zero translation step — but callers should always go through
# these constructors, never build the dict literal by hand, so a typo'd key
# or a `type` outside EVENT_TYPES is impossible.


def event_started(task_id: str) -> dict[str, Any]:
    return {"type": "started", "task_id": task_id}


def event_text(task_id: str, text: str) -> dict[str, Any]:
    return {"type": "text", "task_id": task_id, "text": text}


def event_tool_call(
    task_id: str, name: str, args: dict[str, Any] | None = None, call_id: str | None = None
) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "task_id": task_id,
        "name": name,
        "args": args or {},
        "call_id": call_id,
    }


def event_tool_result(
    task_id: str,
    name: str,
    result: Any = None,
    call_id: str | None = None,
    is_error: bool = False,
) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "task_id": task_id,
        "name": name,
        "result": result,
        "call_id": call_id,
        "is_error": is_error,
    }


def event_state(task_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {"type": "state", "task_id": task_id, "state": state}


def event_done(task_id: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "done", "task_id": task_id, "result": result or {}}


def event_error(task_id: str, message: str, error_type: str | None = None) -> dict[str, Any]:
    """``error_type`` mirrors ``channels.base.ChannelResult.error_type``: the
    failing exception's class name (e.g. "TimeoutError"), letting callers
    build a retryable-vs-permanent taxonomy without re-parsing free text."""
    return {"type": "error", "task_id": task_id, "message": message, "error_type": error_type}


class RuntimeInvocationError(Exception):
    """Raised by an adapter when a run cannot be started or fails outside the
    normal event stream (mirrors ``channels.base.ChannelFetchError``).

    ``error_type`` carries an explicit retry-classification hint for callers
    that already know the fault category but don't want to lose it behind a
    generic wrapper exception."""

    def __init__(self, message: str, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


class RuntimeAdapter(ABC):
    """Base class for all agent-runtime adapters."""

    runtime_type: str
    #: What this adapter can do. A subclass overrides this with its own
    #: RuntimeCapabilities(...); there is no safe default transport, so
    #: unlike Capabilities this has no class-level fallback value.
    capabilities: RuntimeCapabilities

    @abstractmethod
    async def invoke(self, task: AgentTask) -> AsyncIterator[dict[str, Any]]:
        """Run one agent task, yielding RuntimeEvents (see EVENT_TYPES) as
        they occur. MUST yield exactly one terminal event — either a single
        ``done`` or a single ``error`` — as the last item, and MUST NOT yield
        anything after it."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async generator for type checkers

    @abstractmethod
    async def health(self) -> bool:
        """Cheap liveness check for this runtime (binary present, sidecar
        reachable, ...). Does not run a task."""

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate an AgentTask.config dict; return list of error strings
        (empty = valid)."""

    async def bootstrap(self) -> None:
        """One-time env/config setup (e.g. writing skill files, provisioning
        a session directory). Default is a no-op; adapters override as
        needed. Mirrors OpenAlice's ``bootstrap()`` pattern."""
        return None
