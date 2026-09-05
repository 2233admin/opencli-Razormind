"""Public RBAC contracts for scoped III collection and evidence routes."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from backend.main import app
from backend.models.identity import User, Workspace, WorkspaceMembership, WorkspaceRole
from backend.models.iii_collection import IIICollectionAttemptV1, IIICollectionCommandV1
from backend.security.identity import RequestIdentity, get_request_identity
from backend.workflow.iii_collection_store import _attempt_and_outbound
from tests.integration.iii_collection_test_support import (
    create_scoped_run,
    report_body,
    route,
    submit_body,
)


@pytest.mark.asyncio
async def test_workspace_scoped_collection_routes_require_membership_before_lookup(
    client, db_session, monkeypatch
):
    scope = await create_scoped_run(db_session)
    scope["run"].trace_id = str(uuid.uuid4())
    await db_session.commit()
    workspace_id = scope["workspace"].id
    operator = User(id="iii-operator", subject="iii-operator")
    other_workspace = Workspace(
        id="iii-other-workspace", name="Other III", slug="other-iii"
    )
    viewer = User(id="iii-viewer", subject="iii-viewer")
    disabled = User(id="iii-disabled", subject="iii-disabled", disabled=True)
    nonmember = User(id="iii-nonmember", subject="iii-nonmember")
    db_session.add_all(
        [
            operator,
            other_workspace,
            viewer,
            disabled,
            nonmember,
            WorkspaceMembership(
                workspace_id=workspace_id, user_id=operator.id, role=WorkspaceRole.OPERATOR
            ),
            WorkspaceMembership(
                workspace_id=workspace_id, user_id=viewer.id, role=WorkspaceRole.VIEWER
            ),
            WorkspaceMembership(
                workspace_id=other_workspace.id,
                user_id=nonmember.id,
                role=WorkspaceRole.OPERATOR,
            ),
            WorkspaceMembership(
                workspace_id=workspace_id, user_id=disabled.id, role=WorkspaceRole.OPERATOR
            ),
        ]
    )
    await db_session.commit()

    current_identity = RequestIdentity(subject=operator.subject)

    async def override_identity():
        return current_identity

    async def no_dispatch(db, *, command):
        _, outbound = await _attempt_and_outbound(db, command.id)
        return outbound

    async def page_query(request):
        assert request["mode"] == "attempt_page"
        return {
            "mode": "attempt_page",
            "query_fingerprint": request["delegation"]["query_fingerprint"],
            "retention_state": "unknown",
            "redaction_profile_version": "odp-query-reference-v1",
            "records": [],
            "results": [],
        }

    monkeypatch.setattr("backend.api.v1.iii_collections.dispatch_collection_attempt", no_dispatch)
    monkeypatch.setattr(
        "backend.api.v1.iii_collections.get_settings",
        lambda: SimpleNamespace(iii_lifecycle_token="bridge-token"),
    )
    monkeypatch.setattr(
        "backend.api.v1.odp_reconciliation.post_reconciliation_query", page_query
    )
    app.dependency_overrides[get_request_identity] = override_identity
    collection_route = route(scope)
    studio_run_route = collection_route.removesuffix("/iii-collections")
    try:
        submitted = await client.post(collection_route, json=submit_body())
        assert submitted.status_code == 202
        command_id = submitted.json()["data"]["commandId"]

        current_identity = RequestIdentity(subject=viewer.subject)
        assert (await client.get(f"{collection_route}/{command_id}")).status_code == 200
        assert (await client.get(f"{studio_run_route}/evidence-batches/v1")).status_code == 200
        reconciliation_route = f"{collection_route}/{command_id}/odp-reconciliation/attempt_page"
        assert (await client.get(reconciliation_route)).status_code == 200
        for action in ("resume", "cancel", "materialize", "recover"):
            assert (await client.post(f"{collection_route}/{command_id}/{action}")).status_code == 403

        async def ledger_must_not_be_read(*args, **kwargs):
            raise AssertionError("authorization must precede workspace or ledger lookup")

        monkeypatch.setattr(
            "backend.api.v1.iii_collections._scoped_run", ledger_must_not_be_read
        )
        monkeypatch.setattr(
            "backend.api.v1.odp_reconciliation._ledger_delegation", ledger_must_not_be_read
        )

        current_identity = RequestIdentity(subject=nonmember.subject)
        assert (await client.get(f"{collection_route}/{command_id}")).status_code == 403
        assert (await client.get(reconciliation_route)).status_code == 403

        current_identity = RequestIdentity(subject=disabled.subject)
        assert (await client.get(f"{collection_route}/{command_id}")).status_code == 403

        current_identity = RequestIdentity(subject=operator.subject)
        command = await db_session.get(IIICollectionCommandV1, command_id)
        attempt = await db_session.get(IIICollectionAttemptV1, submitted.json()["data"]["attemptId"])
        assert command is not None and attempt is not None

        current_identity = RequestIdentity(subject=nonmember.subject)
        callback = await client.post(
            "/api/v1/iii-collections/expected-key-reports",
            json=report_body(command, attempt),
            headers={"x-iii-bridge-token": "bridge-token"},
        )
        assert callback.status_code == 200
    finally:
        app.dependency_overrides.pop(get_request_identity, None)
