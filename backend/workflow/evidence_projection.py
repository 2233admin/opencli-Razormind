"""Read-only EvidenceBatch projections derived from replayable workflow runs."""


import uuid
from collections.abc import Iterable

from backend.schemas.workflow_evidence import (
    EvidenceBatchSummary,
    WorkflowEvidenceBatchDetail,
    WorkflowEvidenceBatchListResponse,
    WorkflowEvidenceProjection,
    WorkflowEvidenceSummary,
    WorkflowMissingSource,
    WorkflowProjectionArtifact,
    WorkflowSourceCoverage,
)
from backend.schemas.workflow_runtime import (
    WorkflowRunNodeState,
    WorkflowRunProjection,
    WorkflowRunStatus,
)

EVIDENCE_PROJECTION_INCLUDES = frozenset(
    {"clusters", "missingSources", "summaries", "conflicts"}
)

_STATUS_PRIORITY: dict[WorkflowRunStatus, int] = {
    "failed": 7,
    "blocked": 6,
    "partial_success": 5,
    "partial": 4,
    "running": 3,
    "queued": 2,
    "completed": 1,
}


def list_evidence_batches(
    projection: WorkflowRunProjection,
    *,
    node_id: str | None = None,
    source_group: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> WorkflowEvidenceBatchListResponse:
    batches = _batch_summaries(
        projection,
        node_id=node_id,
        source_group=source_group,
    )
    start = 0
    if cursor is not None:
        cursor_positions = [
            index for index, batch in enumerate(batches) if batch.batchId == cursor
        ]
        if not cursor_positions:
            raise ValueError("Unsupported evidence batch cursor")
        start = cursor_positions[0] + 1

    page = batches[start : start + limit]
    next_cursor = page[-1].batchId if start + limit < len(batches) and page else None
    return WorkflowEvidenceBatchListResponse(
        runId=projection.runId,
        batches=page,
        nextCursor=next_cursor,
    )


def get_evidence_batch(
    projection: WorkflowRunProjection,
    batch_id: str,
) -> WorkflowEvidenceBatchDetail | None:
    batches = _batch_summaries(projection)
    batch = next((entry for entry in batches if entry.batchId == batch_id), None)
    if batch is None:
        return None
    related = [entry for entry in batches if entry.sourceGroup == batch.sourceGroup]
    coverage = WorkflowSourceCoverage(
        sourceGroup=batch.sourceGroup,
        status=_aggregate_status(entry.status for entry in related),
        batchCount=len(related),
        itemCount=sum(entry.itemCount for entry in related),
        recordCount=sum(entry.recordCount for entry in related),
    )
    return WorkflowEvidenceBatchDetail(
        runId=projection.runId,
        batch=batch,
        manifestUri=batch.manifestUri,
        odpRef=batch.odpRef,
        recordCount=batch.recordCount,
        itemCount=batch.itemCount,
        sourceCoverage=coverage,
    )


def build_evidence_projection(
    projection: WorkflowRunProjection,
    *,
    node_id: str | None = None,
    source_group: str | None = None,
    includes: frozenset[str] = EVIDENCE_PROJECTION_INCLUDES,
) -> WorkflowEvidenceProjection:
    nodes = _filter_nodes(
        projection.nodeStates,
        node_id=node_id,
        source_group=source_group,
    )
    batches = _batch_summaries(
        projection,
        node_id=node_id,
        source_group=source_group,
    )

    missing_sources: list[WorkflowMissingSource] = []
    if "missingSources" in includes:
        for node in nodes:
            if node.status not in {"partial", "blocked", "failed"}:
                continue
            groups: list[str | None] = list(node.sourceGroups) or [None]
            missing_sources.extend(
                WorkflowMissingSource(
                    nodeId=node.nodeId,
                    sourceGroup=group,
                    status=node.status,
                    reasons=node.blockReasons,
                )
                for group in groups
            )

    summaries: list[WorkflowEvidenceSummary] = []
    if "summaries" in includes:
        grouped: dict[str | None, list[EvidenceBatchSummary]] = {}
        for batch in batches:
            grouped.setdefault(batch.sourceGroup, []).append(batch)
        for group, group_batches in grouped.items():
            summary_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"opencli-admin/workflow-run/{projection.runId}/summary/{group or 'ungrouped'}",
                )
            )
            summaries.append(
                WorkflowEvidenceSummary(
                    summaryId=summary_id,
                    sourceGroup=group,
                    status=_aggregate_status(batch.status for batch in group_batches),
                    batchIds=[batch.batchId for batch in group_batches],
                    itemCount=sum(batch.itemCount for batch in group_batches),
                    recordCount=sum(batch.recordCount for batch in group_batches),
                )
            )

    artifacts = [
        WorkflowProjectionArtifact(
            artifactId=f"evidence-batch:{batch.batchId}",
            batchId=batch.batchId,
            nodeId=batch.nodeId,
            manifestUri=batch.manifestUri,
            odpRef=batch.odpRef,
        )
        for batch in batches
    ]
    return WorkflowEvidenceProjection(
        runId=projection.runId,
        traceId=projection.traceId,
        status=projection.status,
        nodes=nodes,
        clusters=[] if "clusters" in includes else [],
        missingSources=missing_sources,
        summaries=summaries,
        conflicts=[] if "conflicts" in includes else [],
        artifacts=artifacts,
    )


def parse_projection_includes(value: str | None) -> frozenset[str]:
    if value is None or not value.strip():
        return EVIDENCE_PROJECTION_INCLUDES
    includes = frozenset(part.strip() for part in value.split(",") if part.strip())
    unsupported = includes - EVIDENCE_PROJECTION_INCLUDES
    if unsupported:
        raise ValueError(f"Unsupported projection include: {', '.join(sorted(unsupported))}")
    return includes


def _batch_summaries(
    projection: WorkflowRunProjection,
    *,
    node_id: str | None = None,
    source_group: str | None = None,
) -> list[EvidenceBatchSummary]:
    summaries: list[EvidenceBatchSummary] = []
    seen_batch_ids: set[str] = set()
    for node in projection.nodeStates:
        if node_id is not None and node.nodeId != node_id:
            continue
        for batch in node.batches:
            if source_group is not None and batch.sourceGroup != source_group:
                continue
            if batch.batchId in seen_batch_ids:
                continue
            seen_batch_ids.add(batch.batchId)
            summaries.append(
                EvidenceBatchSummary(
                    runId=projection.runId,
                    nodeId=node.nodeId,
                    nodePath=node.nodePath,
                    packageNodeId=node.packageNodeId,
                    internalNodeId=node.internalNodeId,
                    sourceGroup=batch.sourceGroup,
                    adapterTaskId=batch.adapterTaskId,
                    traceId=projection.traceId,
                    batchId=batch.batchId,
                    manifestUri=batch.manifestUri,
                    odpRef=batch.odpRef,
                    itemCount=batch.itemCount,
                    recordCount=batch.recordCount,
                    status=node.status,
                )
            )
    return summaries


def _filter_nodes(
    nodes: list[WorkflowRunNodeState],
    *,
    node_id: str | None,
    source_group: str | None,
) -> list[WorkflowRunNodeState]:
    return [
        node
        for node in nodes
        if (node_id is None or node.nodeId == node_id)
        and (
            source_group is None
            or source_group in node.sourceGroups
            or any(batch.sourceGroup == source_group for batch in node.batches)
        )
    ]


def _aggregate_status(statuses: Iterable[WorkflowRunStatus]) -> WorkflowRunStatus:
    return max(statuses, key=_STATUS_PRIORITY.__getitem__, default="queued")
