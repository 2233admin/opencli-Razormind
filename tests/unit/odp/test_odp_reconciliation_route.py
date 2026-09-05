from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from fastapi import HTTPException

from backend.api.v1 import odp_reconciliation
from backend.odp.query_client import OdpReconciliationDelegation

SOURCE_ID = UUID("00000000-0000-0000-0000-000000000001")
TASK_ID = UUID("00000000-0000-0000-0000-000000000002")
TRACE_ID = UUID("00000000-0000-0000-0000-000000000003")


def delegation(mode="exact"):
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
        allowed_modes=(mode,),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_ledger_delegation_derives_task_batch_and_scope(monkeypatch):
    command = SimpleNamespace(
        id="command",
        trace_id=str(TRACE_ID),
        odp_source_id=str(SOURCE_ID),
    )
    attempt = SimpleNamespace(id="attempt", task_id=str(TASK_ID), trace_id=str(TRACE_ID))
    result = SimpleNamespace(scalar_one_or_none=lambda: attempt)
    database = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(
        odp_reconciliation,
        "_scoped_run",
        AsyncMock(return_value=(SimpleNamespace(), None, None)),
    )
    monkeypatch.setattr(
        odp_reconciliation,
        "get_scoped_command",
        AsyncMock(return_value=command),
    )

    scope = await odp_reconciliation._ledger_delegation(
        database,
        workspace_id="workspace",
        project_id="project",
        workflow_id="workflow",
        run_id="run",
        command_id="command",
        mode="attempt_page",
    )

    assert scope.task_id == TASK_ID
    assert scope.trace_id == TRACE_ID
    assert scope.allowed_source_ids == (SOURCE_ID,)
    assert scope.allowed_modes == ("attempt_page",)
    assert scope.batch_id == str(
        uuid5(NAMESPACE_URL, "opencli-admin/workflow/workflow/run/run/batch/" + str(TASK_ID))
    )


@pytest.mark.asyncio
async def test_proxy_fails_closed_for_browser_supplied_exact_keys(monkeypatch):
    delegated = delegation()
    forwarded = AsyncMock()
    ledger = AsyncMock(return_value=delegated)
    monkeypatch.setattr(odp_reconciliation, "_ledger_delegation", ledger)
    monkeypatch.setattr(odp_reconciliation, "post_reconciliation_query", forwarded)

    with pytest.raises(HTTPException) as exc:
        await odp_reconciliation.reconcile_iii_collection_odp(
            "workspace",
            "project",
            "workflow",
            "run",
            "command",
            "exact",
            event_id=["browser-event"],
            cursor=None,
            page_size=None,
            db=object(),
        )

    assert exc.value.status_code == 409
    ledger.assert_not_awaited()
    forwarded.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxy_rejects_cross_mode_predicates_before_query(monkeypatch):
    delegated = delegation("attempt_page")
    forwarded = AsyncMock()
    monkeypatch.setattr(odp_reconciliation, "_ledger_delegation", AsyncMock(return_value=delegated))
    monkeypatch.setattr(odp_reconciliation, "post_reconciliation_query", forwarded)

    with pytest.raises(HTTPException) as exc_info:
        await odp_reconciliation.reconcile_iii_collection_odp(
            "workspace",
            "project",
            "workflow",
            "run",
            "command",
            "attempt_page",
            event_id=["browser-predicate"],
            db=object(),
        )

    assert exc_info.value.status_code == 400
    forwarded.assert_not_awaited()


@pytest.mark.asyncio
async def test_ledger_trace_mismatch_fails_closed(monkeypatch):
    command = SimpleNamespace(id="command", trace_id=str(TRACE_ID), odp_source_id=str(SOURCE_ID))
    attempt = SimpleNamespace(id="attempt", task_id=str(TASK_ID), trace_id=str(UUID(int=99)))
    result = SimpleNamespace(scalar_one_or_none=lambda: attempt)
    database = SimpleNamespace(execute=AsyncMock(return_value=result))
    monkeypatch.setattr(
        odp_reconciliation,
        "_scoped_run",
        AsyncMock(return_value=(SimpleNamespace(), None, None)),
    )
    monkeypatch.setattr(
        odp_reconciliation,
        "get_scoped_command",
        AsyncMock(return_value=command),
    )

    with pytest.raises(HTTPException) as exc_info:
        await odp_reconciliation._ledger_delegation(
            database,
            workspace_id="workspace",
            project_id="project",
            workflow_id="workflow",
            run_id="run",
            command_id="command",
            mode="exact",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "ODP reconciliation is unavailable"
