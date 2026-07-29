import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.acquisition.capabilities import probe_capabilities
from backend.database import get_db
from backend.executor import get_executor
from backend.models.acquisition import AcquisitionExecutionStatus
from backend.schemas.acquisition import (
    AcquisitionExecutionRead,
    AcquisitionSubmission,
    CapabilityDescriptor,
    CapabilityList,
)
from backend.services import acquisition_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/geo-acquisition", tags=["internal-geo-acquisition"]
)

async def _validate_capability(body: AcquisitionSubmission) -> CapabilityDescriptor:
    capabilities = await probe_capabilities()
    same_id = [c for c in capabilities if c.capability_id == body.capability.id]
    if not same_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "unsupported_capability", "message": body.capability.id},
        )
    same_version = [
        c for c in same_id if c.capability_version == body.capability.version
    ]
    if not same_version:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_capability_version",
                "message": body.capability.version,
            },
        )
    matching_schema = [
        c for c in same_version if c.output_schema_version == body.output_schema_version
    ]
    if not matching_schema:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unsupported_output_schema_version",
                "message": body.output_schema_version,
            },
        )
    requested_target = body.input.get("target")
    matching_target = [
        c for c in matching_schema if c.target == requested_target
    ]
    if not matching_target:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "target_not_registered",
                "message": str(requested_target or ""),
            },
        )
    capability = matching_target[0]
    if not capability.ready:
        raise HTTPException(
            status_code=409,
            detail={
                "code": capability.unavailable_reason or "capability_not_usable",
                "message": "Capability is not usable",
            },
        )
    return capability


@router.get("/capabilities", response_model=CapabilityList)
async def list_capabilities() -> CapabilityList:
    return CapabilityList(capabilities=await probe_capabilities())


@router.post(
    "/executions", response_model=AcquisitionExecutionRead, status_code=202
)
async def submit_execution(
    body: AcquisitionSubmission,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AcquisitionExecutionRead:
    await _validate_capability(body)
    outcome = await acquisition_service.submit_execution(db, body)
    if not outcome.idempotency_match:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": "The idempotency key already identifies different work",
            },
        )
    if outcome.created:
        await acquisition_service.queue_execution(db, outcome.execution)
    else:
        response.status_code = 200
    if outcome.execution.status == AcquisitionExecutionStatus.QUEUED:
        try:
            await get_executor().dispatch_acquisition(outcome.execution.id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "acquisition_dispatch_failed",
                    "message": str(exc),
                },
            ) from exc
    return AcquisitionExecutionRead.from_execution(outcome.execution)


@router.get(
    "/executions/{execution_id}", response_model=AcquisitionExecutionRead
)
async def get_execution(
    execution_id: str, db: AsyncSession = Depends(get_db)
) -> AcquisitionExecutionRead:
    execution = await acquisition_service.get_execution(db, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Acquisition execution not found")
    return AcquisitionExecutionRead.from_execution(execution)


@router.post(
    "/executions/{execution_id}/cancel", response_model=AcquisitionExecutionRead
)
async def cancel_execution(
    execution_id: str, db: AsyncSession = Depends(get_db)
) -> AcquisitionExecutionRead:
    execution = await acquisition_service.get_execution(db, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Acquisition execution not found")
    if not execution.status.accepts_cancel_request:
        raise HTTPException(
            status_code=409,
            detail={"code": "execution_not_cancellable", "message": execution.status},
        )
    cancelled = await acquisition_service.cancel_execution(db, execution)
    try:
        await get_executor().cancel_acquisition(execution_id)
    except Exception as exc:
        # Cancellation is an externally idempotent state transition.  Once the
        # durable state is CANCELLED a best-effort broker revoke must not turn a
        # successful API operation into a misleading 503.
        logger.warning("Cancellation persisted but executor revoke failed: %s", exc)
    return AcquisitionExecutionRead.from_execution(cancelled)
