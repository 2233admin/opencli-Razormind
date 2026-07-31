from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent_runtimes.base import RuntimeAdapter

_REGISTRY: dict[str, "RuntimeAdapter"] = {}


def register_runtime(cls: type) -> type:
    """Class decorator to register an agent-runtime adapter implementation."""
    instance = cls()
    _REGISTRY[instance.runtime_type] = instance
    return cls


def get_runtime(runtime_type: str) -> "RuntimeAdapter":
    if runtime_type not in _REGISTRY:
        raise ValueError(
            f"Unknown runtime type: {runtime_type!r}. "
            f"Available: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[runtime_type]


def list_runtime_types() -> list[str]:
    """All registered runtime types, regardless of whether the underlying
    binary/env is actually present on this node."""
    return list(_REGISTRY.keys())


def available_runtimes() -> list[str]:
    """Runtime types whose adapter reports itself actually usable on this
    node (binary on PATH, sidecar reachable, etc.) via the adapter's cheap
    sync ``is_available()`` classmethod. This is what the ws register
    handshake advertises to the center — never the full registry, since a
    node may not have every runtime's binary installed (Docker image
    layering, design notes §6)."""
    available: list[str] = []
    for runtime_type, instance in _REGISTRY.items():
        is_available = getattr(type(instance), "is_available", None)
        if is_available is not None and is_available():
            available.append(runtime_type)
    return available


def _load_all_runtimes() -> None:
    """Import all agent-runtime adapter modules to trigger registration."""
    from backend.agent_runtimes import (  # noqa: F401
        bbx_adapter,
        miniflow_adapter,
        opentabs_adapter,
        pi_adapter,
    )


_load_all_runtimes()
