"""GET/PUT endpoints for ``model_defaults`` (model-provider runtime PR-C, decision #10).

A top-level resource, not nested under ``/providers/{id}`` — a role's
candidate list can reference any provider, so it doesn't belong under one
provider's URL subtree. DB/validation logic lives in
``backend.services.provider_model_service`` (thin-endpoint convention).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.llm import VALID_ROLES
from backend.schemas.common import ApiResponse
from backend.schemas.model_default import ModelDefaultCandidatesBody, ModelDefaultRead
from backend.services import provider_model_service

router = APIRouter(prefix="/model-defaults", tags=["model-defaults"])


@router.get("", response_model=ApiResponse[list[ModelDefaultRead]])
async def list_model_defaults(db: AsyncSession = Depends(get_db)) -> ApiResponse:
    rows = await provider_model_service.get_defaults(db)
    return ApiResponse.ok([ModelDefaultRead.model_validate(r) for r in rows])


@router.put("/{role}", response_model=ApiResponse[ModelDefaultRead])
async def put_model_default(
    role: str, body: ModelDefaultCandidatesBody, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Set (upsert) the ordered candidate list for ``role`` (index 0 =
    primary, the rest are PR-D's failover order).

    ``role`` fails fast here (before any DB work) if it's outside the closed
    set; each candidate's ``(provider_id, model_id)`` is validated against
    real rows by ``provider_model_service.put_default`` — a candidate naming
    a nonexistent provider or a model never registered in that provider's
    catalog is rejected with a 400 naming exactly which candidate is bad.
    """
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"invalid role: {role!r}")
    try:
        row = await provider_model_service.put_default(db, role, body.candidates)
    except provider_model_service.ModelDefaultsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse.ok(ModelDefaultRead.model_validate(row))
