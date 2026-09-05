from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.odp.query_client import (
    OdpQueryRejected,
    OdpQueryUnavailable,
    OdpReconciliationDelegation,
    OdpRecordKey,
    build_attempt_page_request,
    build_dlq_request,
    build_exact_request,
    post_reconciliation_query,
    sanitize_query_response,
)

SOURCE_ID = UUID("00000000-0000-0000-0000-000000000001")
TASK_ID = UUID("00000000-0000-0000-0000-000000000002")
TRACE_ID = UUID("00000000-0000-0000-0000-000000000003")


def scope(*, modes=("exact", "attempt_page", "dlq")):
    return OdpReconciliationDelegation(
        workspace_id="workspace",
        project_id="project",
        workflow_id="workflow",
        run_id="run",
        batch_id="batch",
        attempt_id="attempt",
        task_id=TASK_ID,
        trace_id=TRACE_ID,
        allowed_source_ids=(SOURCE_ID,),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        allowed_modes=modes,
    )


def safe_reference():
    return {
        "source_id": str(SOURCE_ID),
        "event_id": "event",
        "odp_record_id": 9,
        "committed_at": "2026-08-29T00:00:00Z",
        "provider": "rss",
        "source_ts": "2026-08-29T00:00:00Z",
    }


def test_admin_transport_builds_only_a_bounded_delegated_exact_request():
    request = build_exact_request(scope(), [OdpRecordKey(SOURCE_ID, "event")])

    assert request["mode"] == "exact"
    assert request["keys"] == [{"source_id": str(SOURCE_ID), "event_id": "event"}]
    delegation = request["delegation"]
    assert delegation["task_id"] == str(TASK_ID)
    assert delegation["trace_id"] == str(TRACE_ID)
    assert len(delegation["query_fingerprint"]) == 64
    assert "payload" not in request
    assert "raw_data" not in request


def test_admin_transport_rejects_browser_like_predicates_and_unsafe_scope():
    with pytest.raises(OdpQueryRejected):
        build_exact_request(scope(), [OdpRecordKey(UUID(int=99), "not-authorized")])
    with pytest.raises(OdpQueryRejected):
        build_attempt_page_request(scope(modes=("exact",)), page_size=1)
    with pytest.raises(OdpQueryRejected):
        build_attempt_page_request(scope(), page_size=101)


def test_attempt_page_and_dlq_are_fixed_bounded_modes():
    page = build_attempt_page_request(scope(), cursor="opaque", page_size=25)
    dlq = build_dlq_request(scope(), [OdpRecordKey(SOURCE_ID, "event")])

    assert page["mode"] == "attempt_page"
    assert page["cursor"] == "opaque"
    assert page["page_size"] == 25
    assert dlq["mode"] == "dlq"


def test_admin_client_strips_unredacted_service_fields():
    request = build_attempt_page_request(scope(), page_size=25)
    response = sanitize_query_response(
        {
            "mode": "attempt_page",
            "query_fingerprint": request["delegation"]["query_fingerprint"],
            "retention_state": "unknown",
            "redaction_profile_version": "odp-query-reference-v1",
            "records": [{**safe_reference(), "payload": {"cookie": "secret"}, "raw_data": {}}],
            "results": [],
            "diagnostic": "must not escape",
        },
        request,
    )

    assert response["records"] == [safe_reference()]
    assert "diagnostic" not in response
    assert "payload" not in response["records"][0]
    assert "raw_data" not in response["records"][0]



def test_admin_client_accepts_durable_dlq_retention_without_payload():
    request = build_dlq_request(scope(), [OdpRecordKey(SOURCE_ID, "event")])
    response = sanitize_query_response(
        {
            "mode": "dlq",
            "query_fingerprint": request["delegation"]["query_fingerprint"],
            "retention_state": "retained",
            "redaction_profile_version": "odp-query-reference-v1",
            "records": [],
            "results": [
                {
                    "key": {"source_id": str(SOURCE_ID), "event_id": "event"},
                    "classification": "dlq",
                    "retention_state": "retained",
                    "payload": {"cookie": "secret"},
                }
            ],
        },
        request,
    )

    assert response["retention_state"] == "retained"
    assert response["results"] == [
        {
            "key": {"source_id": str(SOURCE_ID), "event_id": "event"},
            "classification": "dlq",
            "retention_state": "retained",
        }
    ]

def test_fingerprint_is_stable_across_delegation_renewal_and_mode_order():
    original = scope(modes=("dlq", "exact", "attempt_page"))
    renewed = OdpReconciliationDelegation(
        **{**original.__dict__, "expires_at": original.expires_at + timedelta(minutes=5)}
    )

    assert original.to_wire()["query_fingerprint"] == renewed.to_wire()["query_fingerprint"]
    assert original.to_wire()["allowed_modes"] == ["exact", "attempt_page", "dlq"]


@pytest.mark.asyncio
async def test_query_outage_is_redacted_when_admin_is_not_configured(monkeypatch):
    monkeypatch.delenv("ODP_QUERY_URL", raising=False)
    monkeypatch.delenv("ODP_QUERY_ADMIN_CREDENTIAL", raising=False)

    with pytest.raises(OdpQueryUnavailable, match="ODP reconciliation is unavailable"):
        await post_reconciliation_query({"not": "a browser predicate"})
