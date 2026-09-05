"""Integration tests for /api/v1/skills endpoints (2026-07-01 additions):
GET /{id} detail, POST /{id}/dismiss-correction, POST /{id}/rollback.

The pre-existing list + redistill endpoints have no dedicated test file; these
cover only the new routes (plus the concurrency-lock addition on redistill,
exercised indirectly — sqlite no-ops FOR UPDATE, so this proves the query still
works, not the lock itself).
"""

import pytest

from backend.models.skill import Skill


async def _seed_skill(db_session, **overrides) -> Skill:
    defaults = dict(
        domain="d", capability="c", scope="s",
        skill_md="OLD MD", elements={"terminal_conditions": ["done"]},
        evidence=[], version=1, status="active", enabled=True,
    )
    defaults.update(overrides)
    skill = Skill(**defaults)
    db_session.add(skill)
    await db_session.flush()
    await db_session.commit()
    return skill


@pytest.mark.asyncio
async def test_get_skill_detail_not_found(client):
    response = await client.get("/api/v1/skills/nope-404")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_skill_detail_returns_full_body_and_evidence(client, db_session):
    skill = await _seed_skill(
        db_session,
        evidence=[{"event": "distilled", "at": "2026-01-01T00:00:00+00:00"}],
    )
    response = await client.get(f"/api/v1/skills/{skill.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill_md"] == "OLD MD"
    assert data["elements"] == {"terminal_conditions": ["done"]}
    assert len(data["evidence"]) == 1
    assert data["has_open_proposal"] is False


@pytest.mark.asyncio
async def test_get_skill_detail_flags_open_proposal(client, db_session):
    skill = await _seed_skill(
        db_session,
        evidence=[
            {"event": "executed", "passed": False, "loop_outcome": "done_failed"},
            {"event": "correction_proposed", "trace_ids": [], "at": "x"},
        ],
    )
    response = await client.get(f"/api/v1/skills/{skill.id}")
    assert response.json()["data"]["has_open_proposal"] is True


@pytest.mark.asyncio
async def test_dismiss_correction_appends_event_and_clears_flag(client, db_session):
    skill = await _seed_skill(
        db_session,
        evidence=[{"event": "correction_proposed", "trace_ids": [], "at": "x"}],
    )
    response = await client.post(f"/api/v1/skills/{skill.id}/dismiss-correction")
    assert response.status_code == 200

    reloaded = await db_session.get(Skill, skill.id)
    events = [e["event"] for e in reloaded.evidence]
    assert events[-1] == "correction_dismissed"

    detail = await client.get(f"/api/v1/skills/{skill.id}")
    assert detail.json()["data"]["has_open_proposal"] is False


@pytest.mark.asyncio
async def test_dismiss_correction_not_found(client):
    response = await client.post("/api/v1/skills/nope-404/dismiss-correction")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rollback_restores_previous_body(client, db_session):
    skill = await _seed_skill(
        db_session,
        skill_md="NEW BAD MD",
        elements={"procedure": ["new"]},
        version=2,
        evidence=[
            {
                "event": "corrected",
                "from_version": 1,
                "to_version": 2,
                "trace_id": "t1",
                "at": "2026-01-01T00:00:00+00:00",
                "prev_skill_md": "OLD GOOD MD",
                "prev_elements": {"procedure": ["old"]},
                "prev_distill_model": "m1",
                "prev_source_trace": "t0",
            }
        ],
    )

    response = await client.post(f"/api/v1/skills/{skill.id}/rollback")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["skill_md"] == "OLD GOOD MD"
    assert data["elements"] == {"procedure": ["old"]}
    assert data["version"] == 1

    reloaded = await db_session.get(Skill, skill.id)
    assert reloaded.version == 1
    assert reloaded.skill_md == "OLD GOOD MD"
    assert reloaded.evidence[-1]["event"] == "rolled_back"


@pytest.mark.asyncio
async def test_rollback_not_found(client):
    response = await client.post("/api/v1/skills/nope-404/rollback")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rollback_without_prior_correction_is_a_clean_400(client, db_session):
    skill = await _seed_skill(db_session, evidence=[])
    response = await client.post(f"/api/v1/skills/{skill.id}/rollback")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_skill_detail_includes_last_failing_trace(client, db_session):
    trace = {"schema": "journey_trace_v1", "trace_id": "t1", "steps": []}
    skill = await _seed_skill(db_session, last_failing_trace=trace)
    response = await client.get(f"/api/v1/skills/{skill.id}")
    assert response.json()["data"]["last_failing_trace"] == trace


@pytest.mark.asyncio
async def test_redistill_requires_trace_or_fallback(client, db_session):
    skill = await _seed_skill(db_session)  # no last_failing_trace, no body
    response = await client.post(f"/api/v1/skills/{skill.id}/redistill")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_redistill_falls_back_to_last_failing_trace(client, db_session, monkeypatch):
    from backend.skills import correction

    async def fake_distill(trace, provider=None):
        return {
            "skill_name": "x", "scope": "s", "skill_md": "NEW MD",
            "preconditions": [], "procedure": [], "milestones": [],
            "terminal_conditions": [], "false_terminal_states": [],
            "recovery_policies": [], "anti_drift_boundaries": [], "red_lines": [],
            "domain": "d", "capability": "c",
            "source_trace": trace.get("trace_id"), "distill_model": "m",
        }

    monkeypatch.setattr(correction, "distill_trace", fake_distill)

    trace = {"schema": "journey_trace_v1", "trace_id": "fallback-t1", "steps": []}
    skill = await _seed_skill(db_session, last_failing_trace=trace)

    # no body at all — the endpoint must fall back to skill.last_failing_trace.
    response = await client.post(f"/api/v1/skills/{skill.id}/redistill")
    assert response.status_code == 200
    assert response.json()["data"]["version"] == 2

    reloaded = await db_session.get(Skill, skill.id)
    assert reloaded.skill_md == "NEW MD"
    assert reloaded.evidence[-1]["trace_id"] == "fallback-t1"


@pytest.mark.asyncio
async def test_rollback_twice_is_a_clean_400(client, db_session):
    skill = await _seed_skill(
        db_session,
        skill_md="NEW BAD MD",
        version=2,
        evidence=[
            {
                "event": "corrected", "from_version": 1, "to_version": 2,
                "trace_id": "t1", "at": "x",
                "prev_skill_md": "OLD GOOD MD", "prev_elements": {},
                "prev_distill_model": None, "prev_source_trace": None,
            }
        ],
    )
    first = await client.post(f"/api/v1/skills/{skill.id}/rollback")
    assert first.status_code == 200
    second = await client.post(f"/api/v1/skills/{skill.id}/rollback")
    assert second.status_code == 400
