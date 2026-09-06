"""Select connected edge runtimes from declared capabilities, never product branches."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import ws_agent_manager
from backend.models.edge_node import EdgeNode
from backend.schemas.operations_agent import AgentContractV2, AgentRuntimeBindingV2


class RuntimeSelectionError(RuntimeError):
    """No connected Fleet runtime satisfies the published Agent contract."""


def _preference_rank(value: str, preferred: list[str]) -> tuple[int, int | str]:
    if value in preferred:
        return (0, preferred.index(value))
    return (1, value)


async def select_agent_runtime(
    db: AsyncSession,
    *,
    contract: AgentContractV2,
    binding: AgentRuntimeBindingV2,
    require_connected: bool = True,
) -> dict[str, Any]:
    """Return a non-secret immutable selection snapshot for one run."""

    required = set(contract.required_capabilities)
    if binding.model_binding is not None:
        required.add("model_selection")
    if contract.tool_policy:
        required.add("tool_policy")
    if contract.budget:
        required.add("budget_control")
    if contract.quality_gates:
        required.add("quality_gates")

    nodes = list(
        (
            await db.execute(
                select(EdgeNode).where(
                    EdgeNode.protocol == "ws",
                    *(
                        (EdgeNode.status == "online",)
                        if require_connected
                        else ()
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    candidates: list[
        tuple[
            tuple[int, int | str],
            tuple[int, int | str],
            str,
            str,
            EdgeNode,
            list[str],
        ]
    ] = []
    for node in nodes:
        if require_connected and not ws_agent_manager.is_connected(node.url):
            continue
        manifests = node.runtime_capabilities or {}
        if not isinstance(manifests, dict):
            continue
        for runtime, advertised in manifests.items():
            if (
                not isinstance(runtime, str)
                or not runtime.strip()
                or runtime != runtime.strip()
                or not isinstance(advertised, list)
            ):
                continue
            capabilities = sorted(
                {item for item in advertised if isinstance(item, str) and item.strip()}
            )
            if not required.issubset(capabilities):
                continue
            candidates.append(
                (
                    _preference_rank(node.url, binding.preferred_agent_urls),
                    _preference_rank(runtime, binding.preferred_runtimes),
                    node.id,
                    runtime,
                    node,
                    capabilities,
                )
            )

    if not candidates:
        names = ", ".join(sorted(required)) or "none"
        raise RuntimeSelectionError(
            f"no connected Agent Runtime satisfies required capabilities: {names}"
        )

    _, _, _, runtime, node, capabilities = min(
        candidates,
        key=lambda item: (item[0], item[1], item[2]),
    )
    model_binding = binding.model_binding
    return {
        "schema_version": "agent.runtime-selection.v1",
        "agent_url": node.url,
        "runtime": runtime,
        "workflow": binding.workflow,
        "capabilities": capabilities,
        "provider": model_binding.provider if model_binding else None,
        "model": model_binding.model if model_binding else None,
        "auth_profile": model_binding.auth_profile if model_binding else None,
    }
