import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.manager import AuthManager
from backend.config import get_settings
from backend.control import aggregation
from backend.control.objectives import resolve_objective
from backend.control.service import decide_for_source
from backend.control.system_context import build_system_context
from backend.database import get_db
from backend.schemas.common import ApiResponse, PaginationMeta
from backend.schemas.control import (
    FallbackTrendRead,
    SourceControlStateRead,
    SourceMeasurementRecordRead,
    SuggestedActionRead,
    TrendRead,
)
from backend.schemas.credential import CredentialCreate, CredentialKeyRead
from backend.schemas.source import (
    DataSourceCreate,
    DataSourceDetail,
    DataSourceRead,
    DataSourceUpdate,
    SourceObjectiveOverridePatch,
)
from backend.services import measurement_service, source_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=ApiResponse[list[DataSourceRead]])
async def list_sources(
    enabled: bool | None = None,
    channel_type: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    sources, total = await source_service.list_sources(
        db, enabled=enabled, channel_type=channel_type, page=page, limit=limit
    )
    return ApiResponse.ok(
        data=[DataSourceRead.model_validate(s) for s in sources],
        meta=PaginationMeta(
            total=total, page=page, limit=limit, pages=max(1, -(-total // limit))
        ),
    )


@router.post("", response_model=ApiResponse[DataSourceRead], status_code=201)
async def create_source(
    body: DataSourceCreate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    source = await source_service.create_source(db, body)
    return ApiResponse.ok(DataSourceRead.model_validate(source))


class FeedDiscoveryRequest(BaseModel):
    url: str


class FeedCandidate(BaseModel):
    url: str
    title: str | None = None


@router.post("/discover-feed", response_model=ApiResponse[list[FeedCandidate]])
async def discover_feed(body: FeedDiscoveryRequest) -> ApiResponse:
    """Given a site's homepage, find candidate RSS/Atom feeds — setup-time
    convenience, not a scheduled channel action. Returns every candidate found
    (never auto-picks "the main one"); empty list if none found."""
    candidates = await source_service.discover_feeds(body.url)
    return ApiResponse.ok([FeedCandidate(**c) for c in candidates])


class OpmlImportResult(BaseModel):
    created: list[DataSourceRead]
    skipped_existing: list[str]


class OpmlUrlImportRequest(BaseModel):
    url: str


@router.post("/import-opml", response_model=ApiResponse[OpmlImportResult])
async def import_opml(
    file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Bulk-create channel_type="rss" sources from an OPML export. Created
    rows land disabled (human reviews + enables); already-stored feed_urls and
    duplicates within the same file are skipped, not re-created."""
    raw = await file.read()
    try:
        entries = source_service.parse_opml(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created, skipped = await source_service.bulk_import_rss(db, entries)
    await db.commit()
    return ApiResponse.ok(
        OpmlImportResult(
            created=[DataSourceRead.model_validate(s) for s in created],
            skipped_existing=skipped,
        )
    )


@router.post("/import-opml-url", response_model=ApiResponse[OpmlImportResult])
async def import_opml_url(
    body: OpmlUrlImportRequest, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Import a public OPML catalog directly from GitHub or another public
    catalog URL. Sources land disabled and retain their OPML folder plus
    catalog provenance for review and later grouping."""
    try:
        xml_text = await source_service.fetch_remote_opml(body.url)
        entries = source_service.parse_opml(xml_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created, skipped = await source_service.bulk_import_rss(
        db, entries, catalog_url=body.url
    )
    await db.commit()
    return ApiResponse.ok(
        OpmlImportResult(
            created=[DataSourceRead.model_validate(s) for s in created],
            skipped_existing=skipped,
        )
    )


@router.get("/{source_id}", response_model=ApiResponse[DataSourceDetail])
async def get_source(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return ApiResponse.ok(DataSourceDetail.model_validate(source))


@router.patch("/{source_id}", response_model=ApiResponse[DataSourceRead])
async def update_source(
    source_id: str, body: DataSourceUpdate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    updated = await source_service.update_source(db, source, body)
    return ApiResponse.ok(DataSourceRead.model_validate(updated))


@router.patch("/{source_id}/objective", response_model=ApiResponse[DataSourceRead])
async def set_source_objective(
    source_id: str, body: SourceObjectiveOverridePatch, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Set, update, or clear (``objective_override: null``) a source's
    per-source SourceObjective override (issue 02).

    Field validation happens against ``backend.control.objectives.
    SourceObjectiveOverride`` — unknown field names or wrong types 422
    rather than silently being stored and never applied. The resolved
    objective (override merged over defaults) is what
    ``GET /{source_id}/control-state`` actually classifies against; this
    endpoint returns the raw stored override on the source, not the resolved
    shape (see ``SourceControlStateRead.objective`` for that).
    """
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        updated = await source_service.set_objective_override(
            db, source, body.objective_override
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    return ApiResponse.ok(DataSourceRead.model_validate(updated))


@router.delete("/{source_id}", response_model=ApiResponse[None])
async def delete_source(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await source_service.delete_source(db, source)
    return ApiResponse.ok(None)


@router.post("/{source_id}/test", response_model=ApiResponse[dict])
async def test_source(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    ok, errors = await source_service.test_source_connectivity(source)
    return ApiResponse.ok({"connected": ok, "errors": errors})


@router.get("/{source_id}/credentials", response_model=ApiResponse[list[CredentialKeyRead]])
async def list_source_credentials(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Which credential keys are stored for this source — never the values."""
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    keys = await AuthManager().list_keys(source_id)
    return ApiResponse.ok([CredentialKeyRead(key_name=k) for k in keys])


@router.post(
    "/{source_id}/credentials", response_model=ApiResponse[None], status_code=201
)
async def store_source_credential(
    source_id: str, body: CredentialCreate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Encrypt and store a secret for this source (``AuthManager``-backed).
    Migrates a source off plaintext ``channel_config.auth`` / env indirection —
    channels that read via ``AuthManager`` (e.g. ``api``) prefer this over the
    legacy inline config once a matching key is stored."""
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await AuthManager().store(source_id, body.key_name, body.secret)
    return ApiResponse.ok(None)


@router.delete("/{source_id}/credentials/{key_name}", response_model=ApiResponse[None])
async def delete_source_credential(
    source_id: str, key_name: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await AuthManager().delete(source_id, key_name)
    return ApiResponse.ok(None)


@router.get(
    "/{source_id}/control-state",
    response_model=ApiResponse[SourceControlStateRead],
)
async def get_source_control_state(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Read-only, ADVISORY control view of a source (PR-Control-3).

    Aggregates the source's latest sensor reading (preferring the persisted
    ``source_measurements`` table — see ``backend.control.aggregation``),
    computes a rolling trend, collects the shared ODP system_context, derives
    a full ``SourceControlState`` (``backend.control.evaluator``), and maps
    that state onto advisory ``ControlAction`` suggestions
    (``backend.control.policies``).

    ADVISORY ONLY: this endpoint never mutates the source's ``DataSource``
    row (no pause/resume, no interval change, no config write) and never
    calls the scheduler. Per PR-Control-3.5, a non-empty suggestion list IS
    persisted as evidence to the ``control_actions`` ledger
    (``backend.control.ledger.record_advisory_actions``) — recording that a
    suggestion was made is not the same as acting on it; only a human (or a
    future PR-Control-4 actuator, gated by ``Settings.control_mode``) decides
    whether to act.

    404 if the source itself doesn't exist. Every other failure mode (no run
    evidence yet, ODP fully down) degrades gracefully to nulls/False fields —
    this endpoint never 500s because a downstream signal is unavailable.

    All decision logic (measure -> evaluate -> record) lives in
    ``backend.control.service.decide_for_source`` — this handler only does
    HTTP concerns: 404 lookup and assembling the pinned response schema.
    """
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    settings = get_settings()
    objective = resolve_objective(source.objective_override)
    system_context = await build_system_context(objective)

    decision = await decide_for_source(
        db,
        source_id=source_id,
        objective=objective,
        system_context={
            "odp_backpressured": system_context.odp_backpressured,
            "available": system_context.available,
        },
        mode=settings.control_mode,
        dedup_seconds=settings.control_advisory_dedup_seconds,
    )

    # Issue 06: a run-history fallback trend (pre-measurement source) carries
    # an explicit provenance marker; a measurement-backed trend keeps the
    # original pinned four-field shape (no provenance key).
    trend_read = None
    if decision.trend is not None:
        trend_fields = dict(
            window=decision.trend.window,
            zero_accepted_streak=decision.trend.zero_accepted_streak,
            avg_error_rate=decision.trend.avg_error_rate,
            rate_limited_runs=decision.trend.rate_limited_runs,
        )
        if decision.trend.provenance == aggregation.TREND_PROVENANCE_RUN_HISTORY:
            trend_read = FallbackTrendRead(provenance="run_history", **trend_fields)
        else:
            trend_read = TrendRead(**trend_fields)
    suggestions = [
        SuggestedActionRead(
            action_type=a.action_type, reason=a.reason, payload=a.payload
        )
        for a in decision.suggested_actions
    ]

    return ApiResponse.ok(
        SourceControlStateRead(
            source_id=source_id,
            control_state=decision.control_state,
            confidence=decision.confidence,
            sensor_coverage=decision.coverage,
            missing_signals=decision.missing_signals,
            measurement=decision.measurement,
            objective=objective,
            trend=trend_read,
            system_context=system_context,
            suggested_actions=suggestions,
            control_mode=settings.control_mode,
        )
    )


@router.get(
    "/{source_id}/measurements",
    response_model=ApiResponse[list[SourceMeasurementRecordRead]],
)
async def list_source_measurements(
    source_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Paginated, newest-first listing over a source's raw source_measurements
    time series (Source Control Room trend endpoint).

    Distinct from ``GET /{source_id}/control-state``, which folds only the
    LATEST measurement (plus a rolling trend summary) into one decision
    snapshot: this endpoint is the operator's drill-in view of the full
    per-run sensor history behind that snapshot, paginated like every other
    list endpoint (see ``control_ledger_service.list_control_actions`` for
    the sibling pattern this mirrors).

    404 if the source itself doesn't exist, matching every other
    ``/sources/{id}/*`` endpoint. A source with zero measurement rows
    (pre-measurement) returns 200 with an empty ``data`` list and
    ``meta.total == 0`` — that is a legitimate, honest state, not an error.
    Read-only: nothing here writes to source_measurements.
    """
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    rows, total = await measurement_service.list_measurements(
        db, source_id=source_id, page=page, limit=limit
    )
    return ApiResponse.ok(
        data=[SourceMeasurementRecordRead.model_validate(r) for r in rows],
        meta=PaginationMeta(
            total=total, page=page, limit=limit, pages=max(1, -(-total // limit))
        ),
    )
