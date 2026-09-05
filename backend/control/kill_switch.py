"""kill_switch: the global, in-memory runtime override for the Control
Cycle's execution gate (issue 03).

Two halves make up "the kill switch is off": ``Settings.control_kill_switch``
(config, resets on every restart) and this module's in-memory toggle (runtime
API, GET/POST ``/api/v1/control/kill-switch`` — see
``backend.api.v1.control``). Either one being "on" (True) short-circuits ALL
execution in ``run_control_cycle_once``, unconditionally, before any other
gate is evaluated.

Deliberately module-level state, not a DB row or Redis key: a single-operator
fleet (ADR-0005) does not need the toggle to survive a restart or to be
shared across processes — restarting the API is itself an acceptable way to
reset an accidentally-flipped switch back to the safe configured value.
"""

from __future__ import annotations

_runtime_override: bool | None = None


def is_engaged() -> bool:
    """True if the kill switch should block execution right now: either the
    runtime override is explicitly True, or (no override has been set) the
    configured default is True."""
    from backend.config import get_settings

    if _runtime_override is not None:
        return _runtime_override
    return get_settings().control_kill_switch


def set_override(value: bool) -> None:
    """Set the runtime toggle explicitly (True = engaged/blocking, False =
    disengaged/allow). Takes precedence over the config value until
    :func:`reset` is called or the process restarts."""
    global _runtime_override
    _runtime_override = value


def reset() -> None:
    """Clear the runtime override, falling back to ``Settings.control_kill_switch``
    again. Mainly for tests; also what a fresh process naturally starts as."""
    global _runtime_override
    _runtime_override = None


def current_state() -> dict:
    """Snapshot for the API: the effective engaged state plus which half
    (runtime override vs config default) is driving it."""
    from backend.config import get_settings

    return {
        "engaged": is_engaged(),
        "runtime_override": _runtime_override,
        "config_default": get_settings().control_kill_switch,
    }
