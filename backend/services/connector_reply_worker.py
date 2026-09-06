"""Durable connector receipt worker lifecycle and recovery."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.agent_conversation import AgentConversationTurn
from backend.models.connector_reply import (
    ConnectorArtifactGrant,
    ConnectorInboundReceipt,
    ConnectorOutboundDelivery,
    ConnectorReplyGrant,
)

LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 10
RECOVERY_SECONDS = 60
_STABLE_ERROR_RE = re.compile(r"^[a-z][a-z0-9_]{0,95}$")


@dataclass(frozen=True)
class ConnectorReplyWorkerReadiness:
    configured: bool = False
    started: bool = False
    accepting_claims: bool = False
    outbound_started: bool = False
    artifact_configured: bool = False

    @property
    def reply_execution_ready(self) -> bool:
        return self.configured and self.started and self.accepting_claims and self.outbound_started

    @property
    def artifact_delivery_ready(self) -> bool:
        return self.reply_execution_ready and self.artifact_configured


_session_factory: async_sessionmaker[AsyncSession] | None = None
_chat_runner: Callable[..., Any] | None = None
_tasks: dict[str, asyncio.Task[None]] = {}
_recovery_task: asyncio.Task[None] | None = None
_receipt_recovery_cursor: tuple[datetime, str] | None = None
_delivery_recovery_cursor: tuple[datetime, str] | None = None
_readiness = ConnectorReplyWorkerReadiness()


async def start_connector_reply_worker(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    reply_enabled: bool,
    artifact_enabled: bool,
    chat_runner: Callable[..., Any] | None = None,
) -> ConnectorReplyWorkerReadiness:
    """Configure this process to accept durable connector receipt claims."""

    global _session_factory, _chat_runner, _readiness, _recovery_task
    global _receipt_recovery_cursor, _delivery_recovery_cursor
    if _readiness.started:
        return _readiness
    _session_factory = session_factory
    _chat_runner = chat_runner
    _receipt_recovery_cursor = None
    _delivery_recovery_cursor = None
    _readiness = ConnectorReplyWorkerReadiness(
        configured=reply_enabled,
        started=reply_enabled,
        accepting_claims=reply_enabled,
        outbound_started=reply_enabled,
        artifact_configured=reply_enabled and artifact_enabled,
    )
    if reply_enabled:
        try:
            _recovery_task = asyncio.create_task(
                _recovery_supervisor(), name="connector-reply-recovery"
            )
            _recovery_task.add_done_callback(_recovery_done)
        except Exception:
            _session_factory = None
            _chat_runner = None
            _readiness = ConnectorReplyWorkerReadiness(configured=reply_enabled)
            raise
    return _readiness


async def recover_connector_receipts(*, limit: int = 100) -> int:
    """Schedule committed receipts and recoverable outbound deliveries."""

    if not _readiness.reply_execution_ready or _session_factory is None:
        return 0
    if not 1 <= limit <= 1_000:
        raise ValueError("connector recovery limit must be 1..1000")
    global _receipt_recovery_cursor, _delivery_recovery_cursor
    now = datetime.now(UTC)
    scheduled = 0
    async with _session_factory() as db:
        # A crashed send may have reached the provider. Never auto-retry it.
        await db.execute(
            update(ConnectorOutboundDelivery)
            .where(
                ConnectorOutboundDelivery.status == "sending",
                ConnectorOutboundDelivery.lease_expires_at <= now,
            )
            .values(
                status="indeterminate",
                error_code="send_result_unknown",
                lease_owner=None,
                lease_expires_at=None,
                lease_generation=ConnectorOutboundDelivery.lease_generation + 1,
            )
        )
        await db.execute(
            update(ConnectorOutboundDelivery)
            .where(
                ConnectorOutboundDelivery.status == "connecting",
                ConnectorOutboundDelivery.lease_expires_at <= now,
            )
            .values(
                status="retryable_failed",
                error_code="connect_interrupted",
                lease_owner=None,
                lease_expires_at=None,
                lease_generation=ConnectorOutboundDelivery.lease_generation + 1,
            )
        )
        receipt_condition = or_(
            ConnectorInboundReceipt.status.in_(("received", "retryable_failed")),
            (
                (ConnectorInboundReceipt.status == "processing")
                & (ConnectorInboundReceipt.lease_expires_at <= now)
            ),
        )
        receipt_query = select(ConnectorInboundReceipt).where(receipt_condition)
        if _receipt_recovery_cursor is not None:
            cursor_time, cursor_id = _receipt_recovery_cursor
            receipt_query = receipt_query.where(
                or_(
                    ConnectorInboundReceipt.created_at > cursor_time,
                    (
                        (ConnectorInboundReceipt.created_at == cursor_time)
                        & (ConnectorInboundReceipt.id > cursor_id)
                    ),
                )
            )
        receipt_query = receipt_query.order_by(
            ConnectorInboundReceipt.created_at, ConnectorInboundReceipt.id
        ).limit(limit)
        receipts = list(await db.scalars(receipt_query))
        if not receipts and _receipt_recovery_cursor is not None:
            receipts = list(
                await db.scalars(
                    select(ConnectorInboundReceipt)
                    .where(receipt_condition)
                    .order_by(ConnectorInboundReceipt.created_at, ConnectorInboundReceipt.id)
                    .limit(limit)
                )
            )
        delivery_condition = ConnectorOutboundDelivery.status.in_(("pending", "retryable_failed"))
        delivery_query = select(ConnectorOutboundDelivery).where(delivery_condition)
        if _delivery_recovery_cursor is not None:
            cursor_time, cursor_id = _delivery_recovery_cursor
            delivery_query = delivery_query.where(
                or_(
                    ConnectorOutboundDelivery.created_at > cursor_time,
                    (
                        (ConnectorOutboundDelivery.created_at == cursor_time)
                        & (ConnectorOutboundDelivery.id > cursor_id)
                    ),
                )
            )
        delivery_query = delivery_query.order_by(
            ConnectorOutboundDelivery.created_at, ConnectorOutboundDelivery.id
        ).limit(limit)
        deliveries = list(await db.scalars(delivery_query))
        if not deliveries and _delivery_recovery_cursor is not None:
            deliveries = list(
                await db.scalars(
                    select(ConnectorOutboundDelivery)
                    .where(delivery_condition)
                    .order_by(ConnectorOutboundDelivery.created_at, ConnectorOutboundDelivery.id)
                    .limit(limit)
                )
            )
        await db.commit()
    _receipt_recovery_cursor = (
        (receipts[-1].created_at, receipts[-1].id) if len(receipts) == limit else None
    )
    _delivery_recovery_cursor = (
        (deliveries[-1].created_at, deliveries[-1].id) if len(deliveries) == limit else None
    )
    for receipt in receipts:
        schedule_connector_receipt(receipt.id)
        scheduled += 1
    for delivery in deliveries:
        schedule_connector_outbound(delivery.id)
        scheduled += 1
    return scheduled


async def stop_connector_reply_worker() -> None:
    """Stop accepting claims and await all process-local wake-up tasks."""

    global _session_factory, _chat_runner, _readiness, _recovery_task
    _readiness = ConnectorReplyWorkerReadiness(
        configured=_readiness.configured,
        started=False,
        accepting_claims=False,
        outbound_started=False,
        artifact_configured=_readiness.artifact_configured,
    )
    pending = list(_tasks.values())
    if _recovery_task is not None:
        pending.append(_recovery_task)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _tasks.clear()
    _recovery_task = None
    _session_factory = None
    _chat_runner = None


def get_connector_reply_worker_readiness() -> ConnectorReplyWorkerReadiness:
    """Return the current process-local capability/lifecycle snapshot."""

    return _readiness


async def _recovery_supervisor() -> None:
    while True:
        await asyncio.sleep(RECOVERY_SECONDS)
        await recover_connector_receipts()


def _recovery_done(task: asyncio.Task[None]) -> None:
    global _readiness
    if task.cancelled():
        return
    try:
        failure = task.exception()
    except asyncio.CancelledError:
        return
    if failure is not None:
        _readiness = ConnectorReplyWorkerReadiness(
            configured=_readiness.configured,
            started=False,
            accepting_claims=False,
            outbound_started=False,
            artifact_configured=_readiness.artifact_configured,
        )


def schedule_connector_receipt(receipt_id: str) -> None:
    """Wake the worker after the receipt transaction has committed."""

    if not receipt_id or not _readiness.reply_execution_ready or _session_factory is None:
        return
    existing = _tasks.get(receipt_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_process_scheduled(receipt_id), name=f"connector:{receipt_id}")
    _tasks[receipt_id] = task
    task.add_done_callback(_receipt_done_callback(receipt_id))


def schedule_connector_outbound(delivery_id: str) -> None:
    """Wake one immutable delivery after its transaction commits."""

    key = f"outbound:{delivery_id}"
    if not delivery_id or not _readiness.reply_execution_ready or _session_factory is None:
        return
    existing = _tasks.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_process_outbound(delivery_id), name=f"connector-out:{delivery_id}")
    _tasks[key] = task
    task.add_done_callback(_receipt_done_callback(key))


async def _process_scheduled(receipt_id: str) -> None:
    if _session_factory is None:
        return
    await process_connector_receipt(_session_factory, receipt_id, chat_runner=_chat_runner)


async def _process_outbound(delivery_id: str) -> None:
    if _session_factory is None:
        return
    from backend.services.connector_outbound_service import deliver_outbound

    await deliver_outbound(_session_factory, delivery_id)


def _observe_task(receipt_id: str, task: asyncio.Task[None]) -> None:
    _tasks.pop(receipt_id, None)
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        # Durable receipt state is the retry signal; never expose body/claim in logs here.
        return


def _receipt_done_callback(key: str) -> Callable[[asyncio.Task[None]], None]:
    def done(task: asyncio.Task[None]) -> None:
        _observe_task(key, task)

    return done


def _turn_request_id(installation_id: str, provider_message_id: str) -> str:
    digest = hashlib.sha256(
        f"connector-turn-v1\0{installation_id}\0{provider_message_id}".encode()
    ).hexdigest()[:56]
    return f"feishu:{digest}"


async def _claim_receipt(
    session_factory: async_sessionmaker[AsyncSession], receipt_id: str
) -> tuple[str, int, int] | None:
    owner = uuid.uuid4().hex
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=LEASE_SECONDS)
    async with session_factory() as db:
        receipt_claim = await db.execute(
            update(ConnectorInboundReceipt)
            .where(
                ConnectorInboundReceipt.id == receipt_id,
                ConnectorInboundReceipt.reply_grant_id.is_not(None),
                or_(
                    ConnectorInboundReceipt.status.in_(("received", "retryable_failed")),
                    (
                        (ConnectorInboundReceipt.status == "processing")
                        & (ConnectorInboundReceipt.lease_expires_at <= now)
                    ),
                ),
            )
            .values(
                status="processing",
                lease_owner=owner,
                lease_generation=ConnectorInboundReceipt.lease_generation + 1,
                lease_expires_at=expires,
                processing_started_at=func.coalesce(
                    ConnectorInboundReceipt.processing_started_at, now
                ),
                attempt_count=ConnectorInboundReceipt.attempt_count + 1,
            )
            .returning(ConnectorInboundReceipt.lease_generation)
        )
        receipt_generation = receipt_claim.scalar_one_or_none()
        if receipt_generation is None:
            await db.rollback()
            return None
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        if receipt is None or receipt.reply_grant_id is None:
            await db.rollback()
            return None
        request_id = _turn_request_id(receipt.installation_id, receipt.provider_message_id)
        prior_turn = (
            await db.scalar(
                select(AgentConversationTurn).where(
                    AgentConversationTurn.conversation_id == receipt.conversation_id,
                    AgentConversationTurn.request_id == request_id,
                )
            )
            if receipt.intent == "reply" and receipt.conversation_id is not None
            else None
        )
        if prior_turn is not None and prior_turn.status == "running":
            stale_grant_generation = receipt.grant_lease_generation
            receipt.status = "permanent_failed"
            receipt.stable_error_code = "agent_execution_indeterminate"
            receipt.lease_owner = None
            receipt.lease_expires_at = None
            prior_turn.status = "failed"
            prior_turn.error_code = "agent_execution_indeterminate"
            prior_turn.error_message = "Connector execution result is indeterminate"
            await db.execute(
                update(ConnectorReplyGrant)
                .where(
                    ConnectorReplyGrant.id == receipt.reply_grant_id,
                    ConnectorReplyGrant.execution_receipt_id == receipt.id,
                    ConnectorReplyGrant.execution_lease_generation == stale_grant_generation,
                )
                .values(
                    execution_receipt_id=None,
                    execution_lease_owner=None,
                    execution_lease_expires_at=None,
                    execution_lease_generation=ConnectorReplyGrant.execution_lease_generation + 1,
                )
            )
            await db.commit()
            return None
        grant_claim = await db.execute(
            update(ConnectorReplyGrant)
            .where(
                ConnectorReplyGrant.id == receipt.reply_grant_id,
                or_(
                    ConnectorReplyGrant.execution_receipt_id.is_(None),
                    (
                        (ConnectorReplyGrant.execution_receipt_id == receipt.id)
                        & (
                            ConnectorReplyGrant.execution_lease_expires_at.is_(None)
                            | (ConnectorReplyGrant.execution_lease_expires_at <= now)
                        )
                    ),
                ),
            )
            .values(
                execution_receipt_id=receipt.id,
                execution_lease_owner=owner,
                execution_lease_generation=ConnectorReplyGrant.execution_lease_generation + 1,
                execution_lease_expires_at=expires,
            )
            .returning(ConnectorReplyGrant.execution_lease_generation)
        )
        grant_generation = grant_claim.scalar_one_or_none()
        if grant_generation is None:
            grant_exists = await db.scalar(
                select(ConnectorReplyGrant.id).where(
                    ConnectorReplyGrant.id == receipt.reply_grant_id
                )
            )
            receipt.status = "retryable_failed" if grant_exists is not None else "permanent_failed"
            receipt.stable_error_code = (
                "reply_grant_busy" if grant_exists is not None else "reply_grant_unavailable"
            )
            receipt.lease_owner = None
            receipt.lease_expires_at = None
            await db.commit()
            return None
        receipt.grant_lease_generation = int(grant_generation)
        receipt.stable_error_code = None
        await db.commit()
        return owner, int(receipt_generation), int(grant_generation)


async def renew_receipt_lease(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    receipt_id: str,
    owner: str,
    receipt_generation: int,
    grant_generation: int,
) -> bool:
    expires = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    async with session_factory() as db:
        receipt_result = await db.execute(
            update(ConnectorInboundReceipt)
            .where(
                ConnectorInboundReceipt.id == receipt_id,
                ConnectorInboundReceipt.status == "processing",
                ConnectorInboundReceipt.lease_owner == owner,
                ConnectorInboundReceipt.lease_generation == receipt_generation,
            )
            .values(lease_expires_at=expires)
        )
        grant_result = await db.execute(
            update(ConnectorReplyGrant)
            .where(
                ConnectorReplyGrant.execution_receipt_id == receipt_id,
                ConnectorReplyGrant.execution_lease_owner == owner,
                ConnectorReplyGrant.execution_lease_generation == grant_generation,
            )
            .values(execution_lease_expires_at=expires)
        )
        if not receipt_result.rowcount or not grant_result.rowcount:
            await db.rollback()
            return False
        await db.commit()
        return True


async def _heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    receipt_id: str,
    owner: str,
    receipt_generation: int,
    grant_generation: int,
    lost: asyncio.Event,
) -> None:
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not await renew_receipt_lease(
                session_factory,
                receipt_id=receipt_id,
                owner=owner,
                receipt_generation=receipt_generation,
                grant_generation=grant_generation,
            ):
                lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        lost.set()


async def _finalize_receipt_with_delivery(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    receipt_id: str,
    owner: str,
    receipt_generation: int,
    grant_generation: int,
    turn: AgentConversationTurn | None,
) -> str | None:
    from backend.services.connector_artifact_grant_service import (
        create_artifact_delivery_for_receipt,
    )
    from backend.services.connector_outbound_service import create_text_delivery

    async with session_factory() as db:
        fence = await db.execute(
            update(ConnectorInboundReceipt)
            .where(
                ConnectorInboundReceipt.id == receipt_id,
                ConnectorInboundReceipt.status == "processing",
                ConnectorInboundReceipt.lease_owner == owner,
                ConnectorInboundReceipt.lease_generation == receipt_generation,
                ConnectorInboundReceipt.grant_lease_generation == grant_generation,
            )
            .values(lease_expires_at=ConnectorInboundReceipt.lease_expires_at)
            .returning(ConnectorInboundReceipt.id)
        )
        if fence.scalar_one_or_none() is None:
            await db.rollback()
            return None
        receipt = await db.get(ConnectorInboundReceipt, receipt_id)
        if receipt is None or receipt.reply_grant_id is None:
            await db.rollback()
            return None
        reply = await db.scalar(
            select(ConnectorReplyGrant)
            .where(
                ConnectorReplyGrant.id == receipt.reply_grant_id,
                ConnectorReplyGrant.execution_receipt_id == receipt.id,
                ConnectorReplyGrant.execution_lease_owner == owner,
                ConnectorReplyGrant.execution_lease_generation == grant_generation,
            )
            .with_for_update()
        )
        if reply is None:
            raise ValueError("reply_grant_unavailable")
        existing = (
            await db.get(ConnectorOutboundDelivery, receipt.outbound_delivery_id)
            if receipt.outbound_delivery_id is not None
            else None
        )
        if existing is None:
            if receipt.intent == "artifact_claim":
                if receipt.artifact_grant_id is None:
                    raise ValueError("artifact_grant_unavailable")
                artifact = await db.get(ConnectorArtifactGrant, receipt.artifact_grant_id)
                if artifact is None or artifact.status != "active":
                    raise ValueError("artifact_grant_unavailable")
                delivery = await create_artifact_delivery_for_receipt(
                    db, receipt=receipt, grant=artifact, reply=reply
                )
            else:
                if turn is None or not isinstance(turn.response, dict):
                    raise ValueError("connector_turn_unavailable")
                if turn.response.get("type") == "proposal":
                    proposal = turn.response.get("proposal")
                    summary = proposal.get("summary") if isinstance(proposal, dict) else None
                    text = (
                        summary
                        if isinstance(summary, str)
                        else "Agent 已生成待审提案，请在平台中查看。"
                    )
                    purpose = "proposal_notice"
                else:
                    content = turn.response.get("content")
                    text = content if isinstance(content, str) else "Agent 已完成处理。"
                    purpose = "agent_reply"
                delivery = create_text_delivery(
                    db,
                    installation_id=receipt.installation_id,
                    reply_grant_id=reply.id,
                    purpose=purpose,
                    chat_id=reply.p2p_chat_id,
                    text=text,
                    dedupe_owner_id=receipt.id,
                    reply_to_message_id=receipt.provider_message_id,
                )
            await db.flush()
        else:
            delivery = existing
        next_status = "proposal" if turn is not None and turn.status == "proposal" else "completed"
        receipt_result = await db.execute(
            update(ConnectorInboundReceipt)
            .where(
                ConnectorInboundReceipt.id == receipt_id,
                ConnectorInboundReceipt.status == "processing",
                ConnectorInboundReceipt.lease_owner == owner,
                ConnectorInboundReceipt.lease_generation == receipt_generation,
                ConnectorInboundReceipt.grant_lease_generation == grant_generation,
            )
            .values(
                outbound_delivery_id=delivery.id,
                turn_id=turn.id if turn is not None else receipt.turn_id,
                status=next_status,
                processed_at=datetime.now(UTC),
                stable_error_code=None,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        reply_result = await db.execute(
            update(ConnectorReplyGrant)
            .where(
                ConnectorReplyGrant.id == reply.id,
                ConnectorReplyGrant.execution_receipt_id == receipt_id,
                ConnectorReplyGrant.execution_lease_owner == owner,
                ConnectorReplyGrant.execution_lease_generation == grant_generation,
            )
            .values(
                execution_receipt_id=None,
                execution_lease_owner=None,
                execution_lease_expires_at=None,
            )
        )
        if not receipt_result.rowcount or not reply_result.rowcount:
            await db.rollback()
            return None
        await db.commit()
        return delivery.id


async def _fail_receipt(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    receipt_id: str,
    owner: str,
    receipt_generation: int,
    grant_generation: int,
    code: str,
) -> None:
    async with session_factory() as db:
        receipt_result = await db.execute(
            update(ConnectorInboundReceipt)
            .where(
                ConnectorInboundReceipt.id == receipt_id,
                ConnectorInboundReceipt.status == "processing",
                ConnectorInboundReceipt.lease_owner == owner,
                ConnectorInboundReceipt.lease_generation == receipt_generation,
                ConnectorInboundReceipt.grant_lease_generation == grant_generation,
            )
            .values(
                status="permanent_failed",
                stable_error_code=code,
                processed_at=datetime.now(UTC),
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        if not receipt_result.rowcount:
            await db.rollback()
            return
        grant_result = await db.execute(
            update(ConnectorReplyGrant)
            .where(
                ConnectorReplyGrant.execution_receipt_id == receipt_id,
                ConnectorReplyGrant.execution_lease_owner == owner,
                ConnectorReplyGrant.execution_lease_generation == grant_generation,
            )
            .values(
                execution_receipt_id=None,
                execution_lease_owner=None,
                execution_lease_expires_at=None,
            )
        )
        if not grant_result.rowcount:
            await db.rollback()
            return
        await db.commit()


async def process_connector_receipt(
    session_factory: async_sessionmaker[AsyncSession],
    receipt_id: str,
    *,
    chat_runner: Callable[..., Any] | None = None,
) -> None:
    claim = await _claim_receipt(session_factory, receipt_id)
    if claim is None:
        return
    owner, receipt_generation, grant_generation = claim
    lost = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat(
            session_factory,
            receipt_id,
            owner,
            receipt_generation,
            grant_generation,
            lost,
        ),
        name=f"connector-heartbeat:{receipt_id}",
    )
    delivery_id: str | None = None
    try:
        async with session_factory() as db:
            receipt = await db.get(ConnectorInboundReceipt, receipt_id)
            if receipt is None:
                return
            if receipt.intent == "artifact_claim":
                from backend.services.connector_reply_grant_service import (
                    authorize_receipt_for_agent,
                )

                await authorize_receipt_for_agent(
                    db,
                    receipt_id=receipt_id,
                    lease_owner=owner,
                    receipt_lease_generation=receipt_generation,
                    grant_lease_generation=grant_generation,
                )
                delivery_id = await _finalize_receipt_with_delivery(
                    session_factory,
                    receipt_id=receipt_id,
                    owner=owner,
                    receipt_generation=receipt_generation,
                    grant_generation=grant_generation,
                    turn=None,
                )
            else:
                from backend.services.agent_conversation_service import send_connector_message
                from backend.services.connector_reply_grant_service import (
                    authorize_receipt_for_agent,
                )

                access = await authorize_receipt_for_agent(
                    db,
                    receipt_id=receipt_id,
                    lease_owner=owner,
                    receipt_lease_generation=receipt_generation,
                    grant_lease_generation=grant_generation,
                )
                request_id = _turn_request_id(receipt.installation_id, receipt.provider_message_id)
                _, turn = await send_connector_message(
                    db,
                    access,
                    request_id=request_id,
                    content=receipt.safe_content_text,
                    chat_runner=chat_runner,
                    execution_fence_lost=lost.is_set,
                )
                if lost.is_set():
                    return
                delivery_id = await _finalize_receipt_with_delivery(
                    session_factory,
                    receipt_id=receipt_id,
                    owner=owner,
                    receipt_generation=receipt_generation,
                    grant_generation=grant_generation,
                    turn=turn,
                )
    except Exception as exc:
        if not lost.is_set():
            code = getattr(exc, "detail", None)
            if code is None and exc.args and isinstance(exc.args[0], str):
                code = exc.args[0]
            stable_code = (
                code if isinstance(code, str) and _STABLE_ERROR_RE.fullmatch(code) else None
            )
            await _fail_receipt(
                session_factory,
                receipt_id=receipt_id,
                owner=owner,
                receipt_generation=receipt_generation,
                grant_generation=grant_generation,
                code=(stable_code or "connector_processing_failed"),
            )
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
    if delivery_id is not None:
        schedule_connector_outbound(delivery_id)


__all__ = [
    "ConnectorReplyWorkerReadiness",
    "get_connector_reply_worker_readiness",
    "recover_connector_receipts",
    "renew_receipt_lease",
    "schedule_connector_receipt",
    "schedule_connector_outbound",
    "process_connector_receipt",
    "start_connector_reply_worker",
    "stop_connector_reply_worker",
]
