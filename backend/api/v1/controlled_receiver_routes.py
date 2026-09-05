"""Independently authenticated durable controlled-receiver v2 surface."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.delivery_execution import ControlledReceiverDelivery, ControlledReceiverNonce
from backend.schemas.delivery_execution import ControlledReceiverDeliveryV2
from backend.security.controlled_receiver import (
    ControlledReceiverSecurityError,
    canonical_hash,
    canonical_json,
    receipt_payload,
    resolve_receiver_identity,
    sign_receipt,
    verify_request,
)

router = APIRouter(prefix="/controlled-receiver/v2", tags=["controlled-receiver-v2"])
MAX_CONTROLLED_RECEIVER_BODY_BYTES = 64 * 1024



def _receipt(row: ControlledReceiverDelivery) -> dict[str, str]:
    payload = receipt_payload(
        receiver_identity=row.receiver_identity,
        operation_id=row.operation_id,
        decision_hash=row.decision_hash,
        payload_hash=row.payload_hash,
        durable_status=row.durable_status,
        receipt_id=row.receipt_id,
        issued_at=row.receipt_timestamp,
    )
    return {**payload, "keyId": row.receipt_key_id, "signature": row.receipt_signature}


async def _parse(request: Request) -> tuple[ControlledReceiverDeliveryV2, bytes]:
    declared_length = request.headers.get("content-length")
    try:
        if (
            declared_length is not None
            and int(declared_length) > MAX_CONTROLLED_RECEIVER_BODY_BYTES
        ):
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "Controlled receiver v2 body is too large",
            )
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid controlled receiver content length",
        ) from exc
    raw = await request.body()
    if len(raw) > MAX_CONTROLLED_RECEIVER_BODY_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Controlled receiver v2 body is too large",
        )
    try:
        value = ControlledReceiverDeliveryV2.model_validate_json(raw)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid controlled receiver v2 body",
        ) from exc
    if raw != canonical_json(value.model_dump(by_alias=True)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Controlled receiver body is not canonical",
        )
    if canonical_hash(value.payload.model_dump(by_alias=True)) != value.payload_hash:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Controlled receiver payload hash mismatch",
        )
    return value, raw


async def _authenticated(
    request: Request,
) -> tuple[ControlledReceiverDeliveryV2, bytes, object, str, str]:
    value, raw = await _parse(request)
    try:
        endpoint = resolve_receiver_identity(value.receiver_identity)
        key_id, nonce = verify_request(
            body=raw,
            headers=request.headers,
            receiver_identity=value.receiver_identity,
            operation_id=value.operation_id,
            decision_hash=value.decision_hash,
            payload_hash=value.payload_hash,
        )
        # verify_request resolves the same registry entry and binds its key.
    except ControlledReceiverSecurityError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid controlled receiver authentication",
        ) from exc
    return value, raw, endpoint, key_id, nonce


async def _existing(
    db: AsyncSession,
    value: ControlledReceiverDeliveryV2,
) -> ControlledReceiverDelivery | None:
    return await db.scalar(
        select(ControlledReceiverDelivery)
        .where(
            ControlledReceiverDelivery.operation_id == value.operation_id,
            ControlledReceiverDelivery.decision_hash == value.decision_hash,
        )
        .with_for_update()
    )


@router.post("/deliver")
async def deliver(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, dict[str, str]]:
    value, raw, endpoint, key_id, nonce = await _authenticated(request)
    request_hash = canonical_hash(raw)
    row = await _existing(db, value)
    if row is not None:
        if row.request_hash != request_hash or row.payload_hash != value.payload_hash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Controlled receiver durable delivery conflicts",
            )
        return {"receipt": _receipt(row)}
    nonce_row = await db.scalar(
        select(ControlledReceiverNonce)
        .where(
            ControlledReceiverNonce.receiver_identity == value.receiver_identity,
            ControlledReceiverNonce.key_id == key_id,
            ControlledReceiverNonce.nonce == nonce,
        )
        .with_for_update()
    )
    if nonce_row is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Controlled receiver nonce was already used",
        )
    now = datetime.now(UTC)
    receipt_id = f"crv2-{request_hash[:24]}"
    payload = receipt_payload(
        receiver_identity=value.receiver_identity,
        operation_id=value.operation_id,
        decision_hash=value.decision_hash,
        payload_hash=value.payload_hash,
        durable_status=endpoint.durable_status,
        receipt_id=receipt_id,
        issued_at=now,
    )
    row = ControlledReceiverDelivery(
        receiver_identity=value.receiver_identity,
        operation_id=value.operation_id,
        decision_hash=value.decision_hash,
        payload_hash=value.payload_hash,
        request_hash=request_hash,
        durable_status=endpoint.durable_status,
        receipt_id=receipt_id,
        receipt_timestamp=now,
        receipt_key_id=endpoint.receipt_key_id,
        receipt_signature=sign_receipt(payload, endpoint.receipt_key_id),
    )
    db.add_all(
        (
            row,
            ControlledReceiverNonce(
                receiver_identity=value.receiver_identity,
                key_id=key_id,
                nonce=nonce,
                request_hash=request_hash,
            ),
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        replay = await _existing(db, value)
        if replay is not None and replay.request_hash == request_hash:
            return {"receipt": _receipt(replay)}
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Controlled receiver durable delivery conflicts",
        ) from exc
    return {"receipt": _receipt(row)}

@router.post("/status")
async def delivery_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, dict[str, str]]:
    value, raw, _, _, _ = await _authenticated(request)
    row = await _existing(db, value)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Controlled receiver delivery not found",
        )
    if row.payload_hash != value.payload_hash or row.request_hash != canonical_hash(raw):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Controlled receiver durable delivery conflicts",
        )
    return {"receipt": _receipt(row)}
