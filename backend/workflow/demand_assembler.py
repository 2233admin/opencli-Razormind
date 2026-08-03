"""Assemble collection needs into reviewable WorkflowProject patches."""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.schemas.workflow import (
    WorkflowAdapterBinding,
    WorkflowDemandDraftRequest,
    WorkflowPatchOperation,
    WorkflowPatchResponse,
    WorkflowProject,
    WorkflowProjectEdge,
    WorkflowProjectNode,
)
from backend.workflow.opencli_adapter_nodes import list_opencli_adapter_nodes
from backend.workflow.patcher import preview_workflow_patch

logger = logging.getLogger(__name__)


def draft_workflow_demand(body: WorkflowDemandDraftRequest) -> WorkflowPatchResponse:
    """Translate a user collection need into reviewable native-node patches.

    This is intentionally deterministic and conservative. It never emits raw
    executors or OpenCLI payloads; it only assembles packaged OpenCLI Admin
    capabilities that the Canvas can review before materialization.
    """

    sources = _source_slots_for_need(body.text)
    if not sources:
        return preview_workflow_patch(
            body.project,
            [
                WorkflowPatchOperation(
                    op="request_missing_capability",
                    capability="collection.source.intent_mapping",
                    reason=(
                        "No existing Canvas source capability matched this collection need. "
                        "Add a real source/channel mapping before assembling runnable nodes."
                    ),
                )
            ],
        )

    operations = _native_first_loop_operations(
        body.project,
        sources,
        body.text,
        body.locale,
        data_operators=_data_operators_for_need(body.text),
    )
    return preview_workflow_patch(body.project, operations)


def _native_first_loop_operations(
    project: WorkflowProject,
    sources: list[dict[str, Any]],
    demand_text: str,
    locale: str | None,
    *,
    data_operators: list[dict[str, Any]],
) -> list[WorkflowPatchOperation]:
    operations: list[WorkflowPatchOperation] = []
    used_node_ids = {node.id for node in project.nodes}
    used_edge_ids = {edge.id for edge in project.edges}
    used_adapter_ids = {adapter.id for adapter in project.adapters}
    normalize_ids: list[str] = []

    for index, source in enumerate(sources):
        source_slug = _read_string(source.get("id")) or f"source-{index + 1}"
        adapter_id = _unique_id(used_adapter_ids, f"opencli-{source_slug}")
        if adapter_id not in {adapter.id for adapter in project.adapters}:
            operations.append(
                WorkflowPatchOperation(
                    op="add_adapter",
                    adapter=WorkflowAdapterBinding(
                        id=adapter_id,
                        type="source",
                        provider="opencli",
                        mode="live",
                        config={"channel": "opencli"},
                    ),
                )
            )

        source_id = _unique_id(used_node_ids, f"source-{source_slug}")
        normalize_id = _unique_id(used_node_ids, f"normalize-{source_slug}")
        normalize_ids.append(normalize_id)
        operations.extend(
            [
                WorkflowPatchOperation(
                    op="add_node",
                    node=WorkflowProjectNode(
                        id=source_id,
                        kind="source",
                        capability="fetch",
                        adapter=adapter_id,
                        params={
                            "site": source["site"],
                            "command": source["command"],
                            "args": source.get("args", {}),
                            "sourceGroup": source.get("sourceGroup"),
                            "demand": {
                                "text": demand_text,
                                "locale": locale,
                                "source": "ai_plan_draft",
                            },
                        },
                        ui={
                            "catalogId": "intelligence.source.opencli-slot",
                            "label": source.get("label", source_slug),
                            "position": {"x": 180, "y": 180 + index * 120},
                        },
                    ),
                ),
                WorkflowPatchOperation(
                    op="add_node",
                    node=WorkflowProjectNode(
                        id=normalize_id,
                        kind="agent",
                        capability="normalize",
                        params={"language": locale or "zh-CN", "preserveSourceRefs": True},
                        ui={
                            "catalogId": "intelligence.processing.normalize",
                            "label": "Normalize",
                            "position": {"x": 440, "y": 180 + index * 120},
                        },
                    ),
                ),
                WorkflowPatchOperation(
                    op="connect_nodes",
                    edge=WorkflowProjectEdge(
                        id=_unique_id(used_edge_ids, f"e-{source_id}-{normalize_id}"),
                        source=source_id,
                        target=normalize_id,
                        sourcePort="out",
                        targetPort="in",
                    ),
                ),
            ]
        )

    merge_id = _unique_id(used_node_ids, "merge-candidates")
    operator_node_ids = [
        _unique_id(used_node_ids, f"{operator['id']}-data")
        for operator in data_operators
    ]
    accept_id = _unique_id(used_node_ids, "accept-records")
    sink_id = _unique_id(used_node_ids, "record-sink")
    accept_x = 960 + len(operator_node_ids) * 260
    operations.extend(
        [
            WorkflowPatchOperation(
                op="add_node",
                node=WorkflowProjectNode(
                    id=merge_id,
                    kind="flow",
                    capability="merge",
                    params={
                        "strategy": "concat",
                        "preserveLineage": True,
                        "inputType": "recordCandidate[]",
                        "outputType": "recordCandidate[]",
                    },
                    ui={
                        "catalogId": "intelligence.flow.merge",
                        "label": "Merge Candidates",
                        "position": {"x": 700, "y": 240},
                    },
                ),
            ),
            WorkflowPatchOperation(
                op="add_node",
                node=WorkflowProjectNode(
                    id=accept_id,
                    kind="control",
                    capability="accept",
                    params={
                        "mode": "automatic_with_review",
                        "schema": "record.v1",
                        "dedupe": "required",
                        "lineageRequired": True,
                        "minQuality": 0,
                    },
                    ui={
                        "catalogId": "intelligence.control.record-acceptance",
                        "label": "Record Acceptance",
                        "position": {"x": accept_x, "y": 240},
                    },
                ),
            ),
            WorkflowPatchOperation(
                op="add_node",
                node=WorkflowProjectNode(
                    id=sink_id,
                    kind="sink",
                    capability="store",
                    params={
                        "target": "records",
                        "writeMode": "append",
                        "preserveLineage": True,
                    },
                    ui={
                        "catalogId": "intelligence.sink.records",
                        "label": "Records",
                        "position": {"x": accept_x + 260, "y": 240},
                    },
                ),
            ),
        ]
    )
    for index, (operator, operator_node_id) in enumerate(
        zip(data_operators, operator_node_ids, strict=True)
    ):
        operations.append(
            WorkflowPatchOperation(
                op="add_node",
                node=WorkflowProjectNode(
                    id=operator_node_id,
                    kind="agent",
                    capability="normalize",
                    params={
                        "operatorId": operator["operatorId"],
                        "packVersion": operator["packVersion"],
                        "config": operator.get("config", {}),
                    },
                    ui={
                        "catalogId": operator["catalogId"],
                        "label": operator["label"],
                        "position": {"x": 960 + index * 260, "y": 240},
                    },
                ),
            )
        )
    for index, normalize_id in enumerate(normalize_ids, start=1):
        operations.append(
            WorkflowPatchOperation(
                op="connect_nodes",
                edge=WorkflowProjectEdge(
                    id=_unique_id(used_edge_ids, f"e-{normalize_id}-{merge_id}"),
                    source=normalize_id,
                    target=merge_id,
                    sourcePort="out",
                    targetPort=f"in{index}",
                ),
            )
        )
    terminal_id = operator_node_ids[-1] if operator_node_ids else merge_id
    if operator_node_ids:
        operations.append(
            WorkflowPatchOperation(
                op="connect_nodes",
                edge=WorkflowProjectEdge(
                    id=_unique_id(
                        used_edge_ids,
                        f"e-{merge_id}-{operator_node_ids[0]}",
                    ),
                    source=merge_id,
                    target=operator_node_ids[0],
                    sourcePort="out",
                    targetPort="in",
                ),
            )
        )
        for source_id, target_id in zip(
            operator_node_ids,
            operator_node_ids[1:],
        ):
            operations.append(
                WorkflowPatchOperation(
                    op="connect_nodes",
                    edge=WorkflowProjectEdge(
                        id=_unique_id(used_edge_ids, f"e-{source_id}-{target_id}"),
                        source=source_id,
                        target=target_id,
                        sourcePort="out",
                        targetPort="in",
                    ),
                )
            )
    operations.extend(
        [
            WorkflowPatchOperation(
                op="connect_nodes",
                edge=WorkflowProjectEdge(
                    id=_unique_id(used_edge_ids, f"e-{terminal_id}-{accept_id}"),
                    source=terminal_id,
                    target=accept_id,
                    sourcePort="out",
                    targetPort="candidates",
                ),
            ),
            WorkflowPatchOperation(
                op="connect_nodes",
                edge=WorkflowProjectEdge(
                    id=_unique_id(used_edge_ids, f"e-{accept_id}-{sink_id}"),
                    source=accept_id,
                    target=sink_id,
                    sourcePort="records",
                    targetPort="records",
                ),
            ),
        ]
    )
    if _requires_popularity_ranking(demand_text) and _keyword_from_need(demand_text) != "热门":
        operations.append(
            WorkflowPatchOperation(
                op="request_missing_capability",
                capability="collection.rank.popularity",
                reason=(
                    "The matched OpenCLI search adapters accept a query and limit but expose no "
                    "popularity sort or threshold. Keep the topic search runnable, but require a "
                    "governed popularity-ranking capability before claiming hot-post fidelity."
                ),
            )
        )
    return operations


def _data_operators_for_need(text: str) -> list[dict[str, Any]]:
    normalized = text.lower()
    dataflow_compat = "dataflow" in normalized
    research_work = any(
        token in normalized
        for token in ("deep research", "deep-research", "深度调研", "深度研究", "投研")
    )

    training_data = any(
        token in normalized
        for token in ("训练数据", "sft", "instruction", "instruction data", "微调数据")
    )
    quality_work = training_data or any(
        token in normalized
        for token in (
            "dataflow",
            "数据准备",
            "数据清洗",
            "清洗",
            "quality",
            "filter",
            "evaluate",
            "refine",
            "质量",
            "过滤",
            "评估",
            "筛选",
        )
    )
    if not (quality_work or research_work):
        return []

    operators = [
        {
            "id": "chunk",
            "catalogId": "intelligence.data.generate",
            "operatorId": "data.chunk",
            "packVersion": "1.0.0",
            "label": "Chunk Data",
        },
        {
            "id": "clean",
            "catalogId": "intelligence.data.refine",
            "operatorId": "text.clean",
            "packVersion": "1.1.0" if dataflow_compat else "1.0.0",
            "config": (
                {
                    "fields": ["content"],
                    "operations": [
                        "removeEmoji",
                        "htmlUrlRemover",
                        "removeExtraSpaces",
                    ],
                }
                if dataflow_compat
                else {}
            ),
            "label": "Clean Text",
        },
        {
            "id": "deduplicate",
            "catalogId": "intelligence.data.filter",
            "operatorId": "text.deduplicate",
            "packVersion": "1.1.0" if dataflow_compat else "1.0.0",
            "config": (
                {
                    "fields": ["content"],
                    "mode": "exact",
                    "hashFunction": "md5",
                }
                if dataflow_compat
                else {}
            ),
            "label": "Deduplicate Text",
        },
        {
            "id": "rule-filter",
            "catalogId": "intelligence.data.filter",
            "operatorId": "text.rule-filter",
            "packVersion": "1.1.0" if dataflow_compat else "1.0.0",
            "config": (
                {
                    "fields": ["content"],
                    "rules": [
                        {
                            "type": "contentNull",
                            "outputKey": "content_null_filter_label",
                        }
                    ],
                }
                if dataflow_compat
                else {}
            ),
            "label": "Filter Text Rules",
        },
        {
            "id": "statistics",
            "catalogId": "intelligence.data.evaluate",
            "operatorId": "text.statistics",
            "packVersion": "1.0.0",
            "label": "Text Statistics",
        },
    ]
    if training_data:
        operators.append(
            {
                "id": "generate",
                "catalogId": "intelligence.data.generate",
                "operatorId": "core.generate.instruction-pairs",
                "packVersion": "1.0.0",
                "label": "Generate Instruction Pairs",
            }
        )
    if research_work:
        operators.extend(_research_operators_for_need(text))

    return operators


def _research_operators_for_need(text: str) -> list[dict[str, Any]]:
    dimensions = _research_dimensions_for_need(text)
    operators = [
        {
            "id": "research-claim",
            "catalogId": "intelligence.data.refine",
            "operatorId": "research.claim-project",
            "packVersion": "1.0.0",
            "label": "Project Research Claims",
        },
        {
            "id": "research-coverage",
            "catalogId": "intelligence.data.evaluate",
            "operatorId": "research.coverage-audit",
            "packVersion": "1.0.0",
            "config": {
                "requiredDimensions": dimensions,
                "iteration": 1,
                "maxIterations": 2,
                "additionalCollectionCount": 0,
                "maxAdditionalCollections": 1,
            },
            "label": "Audit Research Coverage",
        },
        {
            "id": "research-counter",
            "catalogId": "intelligence.data.generate",
            "operatorId": "research.counter-thesis",
            "packVersion": "1.0.0",
            "label": "Generate Counter Thesis",
        },
    ]
    if dimensions:
        operators.append(
            {
                "id": "research-scenario",
                "catalogId": "intelligence.data.generate",
                "operatorId": "research.scenario-simulate",
                "packVersion": "1.0.0",
                "config": {"scenarios": _research_scenarios_for_dimensions(dimensions)},
                "label": "Simulate Research Scenarios",
            }
        )
    operators.extend(
        [
        {
            "id": "research-revision",
            "catalogId": "intelligence.data.evaluate",
            "operatorId": "research.revision-diff",
            "packVersion": "1.0.0",
            "config": {"previousClaims": [], "previousScenarios": []},
            "label": "Record Research Revision",
        },
        {
            "id": "research-publish-gate",
            "catalogId": "intelligence.data.filter",
            "operatorId": "research.publish-gate",
            "packVersion": "1.0.0",
            "label": "Gate Research Publication",
        },
        ]
    )
    return operators


def _research_scenarios_for_dimensions(dimensions: list[str]) -> list[dict[str, Any]]:
    magnitude = min(0.3 / len(dimensions), 0.15)
    return [
        {
            "scenarioId": "upside",
            "label": "Evidence-aligned upside",
            "priorScore": 0.5,
            "drivers": [
                {"dimension": dimension, "weight": magnitude}
                for dimension in dimensions
            ],
            "assumptions": ["Verified evidence remains valid during the decision window."],
            "invalidationSignals": ["A required dimension loses verified evidence."],
        },
        {
            "scenarioId": "downside",
            "label": "Evidence-challenged downside",
            "priorScore": 0.5,
            "drivers": [
                {"dimension": dimension, "weight": -magnitude}
                for dimension in dimensions
            ],
            "assumptions": ["Contradicting evidence dominates the decision window."],
            "invalidationSignals": ["All required dimensions remain supported."],
        },
    ]


def _research_dimensions_for_need(text: str) -> list[str]:
    match = re.search(
        r"(?:维度|dimensions?)\s*[:：]\s*([^\n;；。]+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    values = re.split(r"[,，、\s]+", match.group(1).strip())
    return list(dict.fromkeys(value for value in values if value))[:12]


def _source_slots_for_need(text: str) -> list[dict[str, Any]]:
    """Resolve a collection need to native OpenCLI source slots.

    Stable aliases win when they are explicit in the request. Otherwise the
    OpenCLI adapter catalog can match any catalog-known site. Catalog access
    is best-effort and never raises.
    """
    return _legacy_keyword_slots_for_need(text) or _catalog_slots_for_need(text)


def _legacy_keyword_slots_for_need(text: str) -> list[dict[str, Any]]:
    normalized = text.lower()
    slots: list[dict[str, Any]] = []
    keyword = _keyword_from_need(text)

    if any(token in normalized for token in ("小红书", "xiaohongshu", "xhs")):
        slots.append(
            {
                "id": "xiaohongshu",
                "label": "Xiaohongshu Search",
                "sourceGroup": "social",
                "site": "xiaohongshu",
                "command": "search",
                "args": {"keyword": keyword},
            }
        )

    if any(token in normalized for token in ("哔哩", "bilibili", "b站", "bili")):
        slots.append(
            {
                "id": "bilibili",
                "label": "Bilibili Search",
                "sourceGroup": "video",
                "site": "bilibili",
                "command": "search",
                "args": {"keyword": keyword},
            }
        )

    return slots


def _keyword_from_need(text: str) -> str:
    value = text.strip()
    value = re.sub(
        r"[,，;；。]\s*(?:(?:再|然后|并|以及)\s*)?"
        r"(?:清洗|去重|保存|存储|入库|发送|通知|汇总|分析).*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    for pattern in (
        r"^(?:请|帮我)?\s*(?:抓取?|采集|收集|监控|找|搜索|搜|看(?:下|一下)?)\s*",
        r"(小红书|xiaohongshu|xhs|哔哩哔哩|哔哩|bilibili|b站|bili)",
        r"(相关的?|有关的?)",
        r"(热帖|热门帖子|热门内容|hot posts?)",
    ):
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*(?:(?:和|与|及|、|的)\s*)+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ，,。")
    return value or "热门"


def _requires_popularity_ranking(text: str) -> bool:
    return bool(
        re.search(
            r"(热帖|热门帖子|热门内容|爆款|高热度|hot posts?|trending)",
            text,
            flags=re.IGNORECASE,
        )
    )


# --- OpenCLI adapter catalog matching --------------------------------------
#
# Chinese aliases for catalog sites that are commonly typed in Chinese rather
# than by their OpenCLI site slug. An alias for a site that isn't present in
# the loaded catalog is harmless -- it simply never matches anything.
_CATALOG_SITE_ALIASES: dict[str, tuple[str, ...]] = {
    "xiaohongshu": ("小红书", "xiaohongshu", "xhs"),
    "bilibili": ("哔哩", "bilibili", "b站", "bili"),
}

# The native merge capability exposes in1/in2.
_CATALOG_SLOT_CAP = 2
_CATALOG_TOKEN_PATTERN = re.compile(r"[a-zA-Z一-鿿]+")


def _catalog_slots_for_need(text: str) -> list[dict[str, Any]]:
    """Match a need against the OpenCLI adapter catalog.

    Deterministic, no LLM. Scores each read-access catalog site against the
    need text by (a) exact site id/name token, (b) Chinese alias substring,
    (c) description/domain/strategy keyword -- in that priority -- and keeps
    only the best-scoring tier per site. Never raises: any catalog access
    failure (missing binary, subprocess error, decode error, ...) is caught
    and treated as "no catalog available", returning an empty list so the
    caller can fall back to the legacy keyword floor.
    """
    try:
        nodes = list_opencli_adapter_nodes(include_write=False).nodes
    except Exception:
        logger.debug(
            "opencli adapter catalog unavailable for demand matching", exc_info=True
        )
        return []

    normalized = text.strip().lower()
    if not normalized or not nodes:
        return []

    keyword = _keyword_from_need(text)
    best: dict[str, tuple[int, int, int, str]] = {}
    for index, node in enumerate(nodes):
        match = _catalog_match_tier(normalized, node)
        if match is None:
            continue
        tier, mention_pos = match
        current = best.get(node.site)
        if current is None or (tier, mention_pos) < (current[0], current[1]):
            best[node.site] = (tier, mention_pos, index, node.command)

    ordered_sites = sorted(
        best.items(), key=lambda item: (item[1][0], item[1][1], item[1][2])
    )
    slots: list[dict[str, Any]] = []
    for site, (_tier, _mention, _index, command) in ordered_sites[:_CATALOG_SLOT_CAP]:
        slots.append(
            {
                "id": _catalog_slot_id(site),
                "label": f"{site.title()} {command.title()}".strip(),
                "sourceGroup": "opencli",
                "site": site,
                "command": command,
                "args": {"keyword": keyword},
            }
        )
    return slots


def _catalog_match_tier(normalized: str, node: Any) -> tuple[int, int] | None:
    """Return (tier, first mention position) or None.

    The mention position keeps multi-source needs ordered the way the
    operator wrote them ("抓小红书和B站…" puts xiaohongshu before bilibili)
    instead of by adapter-catalog listing order, which is not stable across
    catalog updates."""
    # Command names (node.command) repeat across unrelated sites, so a
    # command-only hit must never claim the exact-site tier.
    site = node.site.lower()
    if len(site) >= 2 and site in normalized:
        return 0, normalized.find(site)
    alias_positions = [
        normalized.find(alias.lower())
        for alias in _CATALOG_SITE_ALIASES.get(site, ())
        if alias.lower() in normalized
    ]
    if alias_positions:
        return 1, min(alias_positions)
    token_positions = [
        normalized.find(token)
        for token in _catalog_description_tokens(node)
        if token in normalized
    ]
    if token_positions:
        return 2, min(token_positions)
    return None


def _catalog_mention_position(normalized: str, site: str) -> int:
    candidates = (site, *_CATALOG_SITE_ALIASES.get(site, ()))
    positions = (
        normalized.find(candidate.lower())
        for candidate in candidates
    )
    return min((position for position in positions if position >= 0), default=len(normalized))


def _catalog_description_tokens(node: Any) -> list[str]:
    haystack = " ".join(
        value for value in (node.description, node.domain, node.strategy) if value
    )
    return [
        token.lower()
        for token in _CATALOG_TOKEN_PATTERN.findall(haystack)
        if len(token) >= 2
    ]


def _catalog_slot_id(site: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", site.strip().lower()).strip("-") or "source"


def _unique_node_id(project: WorkflowProject, base: str) -> str:
    return _unique_id({node.id for node in project.nodes}, base)


def _unique_id(used: set[str], base: str) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _read_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
