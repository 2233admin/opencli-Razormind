"""CRUD endpoints for model providers, plus model-provider runtime PR-C's provider-scoped
API: test-connection, model-catalog sync, and model catalog CRUD (decision
#10). DB logic for the PR-C additions lives in
``backend.services.provider_model_service`` (thin-endpoint convention); this
module only does HTTP concerns (404 lookups, response shaping)."""

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.crypto import CredentialCryptoError
from backend.database import get_db
from backend.llm.base import LlmAdapterError
from backend.llm.factory import get_adapter
from backend.models.feed_provider import FeedProvider
from backend.models.provider import ModelProvider
from backend.schemas.common import ApiResponse
from backend.schemas.feed_provider import (
    FeedProviderConnectionTest,
    FeedProviderCreate,
    FeedProviderRead,
    FeedProviderUpdate,
    FeedProviderWorkflowNodeRequest,
)
from backend.schemas.provider import (
    ModelProviderCreate,
    ModelProviderRead,
    ModelProviderUpdate,
    ProviderModelDiscoveryRequest,
)
from backend.schemas.provider_model import (
    ProviderModelManualCreate,
    ProviderModelRead,
    ProviderModelUpdate,
)
from backend.services import feed_provider_service, provider_model_service

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/feed-generators", response_model=ApiResponse[list[FeedProviderRead]])
async def list_feed_generators(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    rows = await feed_provider_service.list_feed_providers(db)
    return ApiResponse.ok([FeedProviderRead.from_model(row) for row in rows])


@router.post(
    "/feed-generators",
    response_model=ApiResponse[FeedProviderRead],
    status_code=201,
)
async def create_feed_generator(
    body: FeedProviderCreate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    payload = body.model_dump(exclude={"access_token", "config"})
    row = FeedProvider(
        **payload,
        access_token=body.access_token,
        config=body.config.model_dump(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ApiResponse.ok(FeedProviderRead.from_model(row))


@router.patch(
    "/feed-generators/{feed_provider_id}",
    response_model=ApiResponse[FeedProviderRead],
)
async def update_feed_generator(
    feed_provider_id: str,
    body: FeedProviderUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    row = await feed_provider_service.get_feed_provider(db, feed_provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Feed provider not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "config" and value is not None:
            row.config = value
        else:
            setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return ApiResponse.ok(FeedProviderRead.from_model(row))


@router.delete("/feed-generators/{feed_provider_id}", response_model=ApiResponse[None])
async def delete_feed_generator(
    feed_provider_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    row = await feed_provider_service.get_feed_provider(db, feed_provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Feed provider not found")
    await db.delete(row)
    await db.commit()
    return ApiResponse.ok(None)


@router.post(
    "/feed-generators/{feed_provider_id}/test",
    response_model=ApiResponse[FeedProviderConnectionTest],
)
async def test_feed_generator(
    feed_provider_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    row = await feed_provider_service.get_feed_provider(db, feed_provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Feed provider not found")
    return ApiResponse.ok(await feed_provider_service.probe_feed_provider(row))


@router.get("/feed-generators/{feed_provider_id}/catalog", response_model=ApiResponse[dict])
async def get_feed_generator_catalog(
    feed_provider_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    row = await feed_provider_service.get_feed_provider(db, feed_provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Feed provider not found")
    try:
        catalog = await feed_provider_service.discover_feed_provider_catalog(row)
    except feed_provider_service.FeedProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"message": str(exc), "error_kind": exc.kind},
        ) from exc
    return ApiResponse.ok(catalog)


@router.post(
    "/feed-generators/{feed_provider_id}/workflow-node",
    response_model=ApiResponse[dict],
)
async def build_feed_generator_workflow_node(
    feed_provider_id: str,
    body: FeedProviderWorkflowNodeRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    row = await feed_provider_service.get_feed_provider(db, feed_provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Feed provider not found")
    try:
        node = feed_provider_service.build_workflow_node(row, body)
    except feed_provider_service.FeedProviderError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "error_kind": exc.kind},
        ) from exc
    return ApiResponse.ok(node)


@router.post("/discover-models", response_model=ApiResponse[list[str]])
async def discover_provider_models(body: ProviderModelDiscoveryRequest) -> ApiResponse:
    """Discover model IDs from provider settings before the provider is saved.

    This is the progressive-setup counterpart to provider-scoped catalog
    sync. It deliberately uses the same adapter and URL guard as saved
    providers, while keeping the supplied credential request-local.
    """
    provider = SimpleNamespace(
        provider_type=body.provider_type,
        base_url=body.base_url,
        api_key=body.api_key or "",
        default_model=None,
    )
    try:
        models = await get_adapter(provider).list_models()
    except LlmAdapterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ApiResponse.ok(models)


@router.get("", response_model=ApiResponse[list[ModelProviderRead]])
async def list_providers(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    result = await db.execute(select(ModelProvider).order_by(ModelProvider.created_at.desc()))
    # ModelProviderRead.from_model masks api_key (has_api_key/api_key_preview
    # only) — see AUDIT item B3. Built explicitly rather than relying on
    # response_model's from_attributes, since api_key -> has_api_key/
    # api_key_preview isn't a 1:1 attribute mapping.
    providers = [ModelProviderRead.from_model(p) for p in result.scalars().all()]
    return ApiResponse.ok(providers)


@router.post("", response_model=ApiResponse[ModelProviderRead], status_code=201)
async def create_provider(
    body: ModelProviderCreate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    try:
        provider = ModelProvider(**body.model_dump())
    except CredentialCryptoError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Provider credential encryption is not configured. Set "
                "CREDENTIAL_ENCRYPTION_KEY before saving an API key."
            ),
        ) from exc
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return ApiResponse.ok(ModelProviderRead.from_model(provider))


@router.patch("/{provider_id}", response_model=ApiResponse[ModelProviderRead])
async def update_provider(
    provider_id: str, body: ModelProviderUpdate, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    result = await db.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    try:
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(provider, field, value)
    except CredentialCryptoError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail=(
                "Provider credential encryption is not configured. Set "
                "CREDENTIAL_ENCRYPTION_KEY before saving an API key."
            ),
        ) from exc
    await db.commit()
    await db.refresh(provider)
    return ApiResponse.ok(ModelProviderRead.from_model(provider))


@router.delete("/{provider_id}", response_model=ApiResponse[None])
async def delete_provider(provider_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    result = await db.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    # model-provider runtime PR-C (decision #3 / PR-A note): sqlite here never runs with
    # PRAGMA foreign_keys=ON, so provider_models' ondelete=CASCADE never
    # fires at runtime -- clean up the catalog explicitly or it orphans.
    await provider_model_service.delete_provider_models(db, provider_id)
    await db.delete(provider)
    await db.commit()
    return ApiResponse.ok(None)


# ---------------------------------------------------------------------------
# model-provider runtime PR-C: test connection / model catalog sync + CRUD (decision #10)
# ---------------------------------------------------------------------------


@router.post("/{provider_id}/test", response_model=ApiResponse[dict])
async def test_provider_connection(
    provider_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Probe the provider via its adapter (PR-B factory). Never echoes
    ``api_key`` — ``ConnectionTestResult``/adapters guarantee that, not this
    endpoint (see ``backend.llm.base.redact_secret``)."""
    result = await provider_model_service.test_connection(db, provider_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ApiResponse.ok(result)


@router.post("/{provider_id}/models/sync", response_model=ApiResponse[dict])
async def sync_provider_models(
    provider_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Discover this provider's models and upsert them into its catalog
    (decision #3: manual rows are never touched; stale discovered rows are
    pruned — see ``provider_model_service.sync_models`` for the full policy).
    A genuine discovery failure (connection error, bad key, ...) surfaces as
    502, not 500 — the adapter already sanitized the message."""
    try:
        result = await provider_model_service.sync_models(db, provider_id)
    except LlmAdapterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ApiResponse.ok(dict(result))


@router.get("/{provider_id}/models", response_model=ApiResponse[list[ProviderModelRead]])
async def list_provider_models(
    provider_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    provider = await provider_model_service.get_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    rows = await provider_model_service.list_models(db, provider_id)
    return ApiResponse.ok([ProviderModelRead.model_validate(r) for r in rows])


@router.post(
    "/{provider_id}/models", response_model=ApiResponse[ProviderModelRead], status_code=201
)
async def add_provider_model(
    provider_id: str,
    body: ProviderModelManualCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Hand-register a catalog entry (``source="manual"`` — decision #3;
    the request body has no ``source`` field, it's always forced manual
    server-side)."""
    provider = await provider_model_service.get_provider(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    row = await provider_model_service.add_manual_model(db, provider_id, body)
    return ApiResponse.ok(ProviderModelRead.model_validate(row))


@router.patch(
    "/{provider_id}/models/{model_row_id}", response_model=ApiResponse[ProviderModelRead]
)
async def update_provider_model(
    provider_id: str,
    model_row_id: str,
    body: ProviderModelUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    existing = await provider_model_service.get_model(db, model_row_id)
    if existing is None or existing.provider_id != provider_id:
        raise HTTPException(status_code=404, detail="Model not found")
    row = await provider_model_service.update_model(db, model_row_id, body)
    return ApiResponse.ok(ProviderModelRead.model_validate(row))


@router.delete("/{provider_id}/models/{model_row_id}", response_model=ApiResponse[None])
async def delete_provider_model(
    provider_id: str, model_row_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    existing = await provider_model_service.get_model(db, model_row_id)
    if existing is None or existing.provider_id != provider_id:
        raise HTTPException(status_code=404, detail="Model not found")
    await provider_model_service.delete_model(db, model_row_id)
    return ApiResponse.ok(None)
