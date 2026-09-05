"""Skills API — the human-triggered **correct** leg (ADR-0003 D7, D8).

The dock's ``重蒸技能`` action POSTs a failing ``journey_trace_v1`` trace here;
this endpoint re-distills the skill into version *n+1* via
:func:`backend.skills.correction.re_distill` (re-distillation, never a
hand-patch). Per **D8**, re-distill is **human-triggered only** in v1 — there is
no automatic "N fails → re-distill" path anywhere; this router is the sole entry.

Auth: this router mirrors the other v1 write endpoints (e.g. ``/chat/confirm``),
which take ``Depends(get_db)`` and rely on the same app-level protection — so the
redistill endpoint is no less protected than they are.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.skill import Skill
from backend.schemas.common import ApiResponse, PaginationMeta
from backend.skills import correction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _skill_brief(s: Skill) -> dict[str, Any]:
    """Compact skill projection for the dock (no full skill_md body)."""
    return {
        "id": s.id,
        "domain": s.domain,
        "capability": s.capability,
        "name": s.capability,
        "version": s.version,
        "status": s.status,
        "enabled": s.enabled,
        "evidence_count": len(s.evidence or []),
        "has_open_proposal": _has_open_proposal(s.evidence or []),
    }


def _has_open_proposal(evidence: list[dict[str, Any]]) -> bool:
    """Best-effort: an unresolved `correction_proposed` sits past the last
    `corrected`/`correction_dismissed` boundary — same rule
    ``correction.maybe_propose_correction`` uses to avoid duplicate proposals.
    Lets the dock's list view flag "needs attention" without a per-row detail
    fetch."""
    boundary = 0
    for i, ev in enumerate(evidence):
        if ev.get("event") in ("corrected", "correction_dismissed"):
            boundary = i + 1
    return any(ev.get("event") == "correction_proposed" for ev in evidence[boundary:])


def _skill_detail(s: Skill) -> dict[str, Any]:
    """Full skill projection (list's brief + body + evidence) for the dock's
    single-skill review view — the previously-missing GET.../{id} leg."""
    return {
        **_skill_brief(s),
        "scope": s.scope,
        "skill_md": s.skill_md,
        "elements": s.elements,
        "source_trace": s.source_trace,
        "distill_model": s.distill_model,
        "evidence": s.evidence or [],
        "last_failing_trace": s.last_failing_trace,
    }


@router.get("", response_model=ApiResponse[list[dict]])
async def list_skills(
    domain: str | None = None,
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """List distilled skills (compact). Read-only; used by the dock to pick a
    skill to re-distill."""
    stmt = select(Skill)
    count_stmt = select(Skill)
    if domain is not None:
        stmt = stmt.where(Skill.domain == domain)
        count_stmt = count_stmt.where(Skill.domain == domain)
    if enabled is not None:
        stmt = stmt.where(Skill.enabled.is_(enabled))
        count_stmt = count_stmt.where(Skill.enabled.is_(enabled))

    total = len((await db.execute(count_stmt)).scalars().all())
    stmt = stmt.order_by(Skill.updated_at.desc()).offset((page - 1) * limit).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return ApiResponse.ok(
        data=[_skill_brief(s) for s in rows],
        meta=PaginationMeta(total=total, page=page, limit=limit, pages=max(1, -(-total // limit))),
    )


@router.get("/{skill_id}", response_model=ApiResponse[dict])
async def get_skill(skill_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    """Full detail for one skill — body + elements + full evidence log. The
    dock needs this to actually review a `correction_proposed` entry (the list
    endpoint only carries `evidence_count` / `has_open_proposal`, not content)."""
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    return ApiResponse.ok(_skill_detail(skill))


@router.post("/{skill_id}/dismiss-correction", response_model=ApiResponse[dict])
async def dismiss_correction(
    skill_id: str, db: AsyncSession = Depends(get_db)
) -> ApiResponse:
    """Human says "this fail streak isn't a real problem" — append
    `correction_dismissed` (ADR-0003 D7 v2 addendum). Resets
    ``maybe_propose_correction``'s streak/duplicate-guard boundary so the skill
    gets a fresh N chances and stops showing as needing attention; never touches
    ``skill_md`` / ``version`` (that's `redistill`'s job, not this one's)."""
    skill = await db.get(Skill, skill_id, with_for_update=True)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    evidence = list(skill.evidence or [])
    evidence.append({"event": "correction_dismissed", "at": _now_iso()})
    skill.evidence = evidence
    await db.commit()
    return ApiResponse.ok(_skill_brief(skill))


@router.post("/{skill_id}/rollback", response_model=ApiResponse[dict])
async def rollback_skill(skill_id: str, db: AsyncSession = Depends(get_db)) -> ApiResponse:
    """Undo the most recent re-distill — restore v(n)'s body from the `corrected`
    evidence entry's stashed `prev_*` fields (ADR-0003 D7 v2 addendum: re-distill
    used to overwrite in place with no way back if v(n+1) is worse)."""
    skill = await db.get(Skill, skill_id, with_for_update=True)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")
    try:
        skill = await correction.rollback_correction(db, skill)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse.ok(_skill_detail(skill))


@router.post("/{skill_id}/redistill", response_model=ApiResponse[dict])
async def redistill_skill(
    skill_id: str,
    body: dict[str, Any] | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Re-distill a failing skill from its trace → version *n+1* (D7).

    Body: ``{"trace": <journey_trace_v1 dict>}`` (or ``{"traces": [...]}``),
    optional. When omitted, falls back to ``skill.last_failing_trace``
    (2026-07-01 addendum) — the dock's skill detail page can trigger a redistill
    without carrying the trace payload itself; the live chat-driven flow
    (``AgentDock.tsx``) keeps passing an explicit trace, unaffected. The failing
    trace is fed back through the distiller; ``version`` bumps by 1, ``evidence``
    gains one ``"corrected"`` entry, and ``skill_md`` / ``elements`` are replaced
    from the fresh distillation. Returns the new version.
    """
    skill = await db.get(Skill, skill_id, with_for_update=True)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 {skill_id} 不存在")

    body = body or {}
    traces = body.get("trace") or body.get("traces") or skill.last_failing_trace
    if not traces:
        raise HTTPException(
            status_code=400,
            detail=(
                "redistill 需要 body.trace (journey_trace_v1),"
                "且该技能没有 last_failing_trace 可兜底"
            ),
        )

    try:
        skill = await correction.re_distill(db, skill, traces)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # distiller / provider failure
        logger.error("redistill failed | skill=%s err=%s", skill_id, exc)
        raise HTTPException(status_code=502, detail=f"重蒸馏失败: {exc}") from exc

    return ApiResponse.ok(
        {
            "skill_id": skill.id,
            "version": skill.version,
            "domain": skill.domain,
            "capability": skill.capability,
        }
    )
