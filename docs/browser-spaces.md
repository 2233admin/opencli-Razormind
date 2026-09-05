# Browser Spaces

A Space reserves one existing managed browser runtime slot for a Workspace and
owner. A partial unique index prevents another active Space from reserving the
same instance. Another partial unique index permits only one queued/running task
per Space. Parallel Spaces need distinct instances.

## Authorization and allocation

Every HTTP operation requires an authenticated identity and Workspace membership.
Creating a Space requires Workspace configuration permission **and platform-admin
authority**: BrowserInstance and BrowserBinding are global resources, with no
Workspace assignment in their existing models. Workspace membership alone does
not authorize taking a global authenticated browser session.

The requested owner must match the caller's authorized identity. Workspace
administrators and maintainers may manage existing Spaces in their Workspace;
other members can only read or run their own Spaces, subject to Workspace role
permissions. A foreign Workspace cannot read or mutate a Space.

`GET /api/v1/workspaces/{workspace_id}/browser-spaces/instances` supplies eligible
instance IDs and manifest capability names for the existing browser panel. It
requires the same allocation permissions, omits already reserved instances, and
never supplies endpoints, labels, profile paths or connection credentials. Only
instances with a runtime bundle and a supported Agent route on the registered
endpoint host are eligible. The create operation validates again and atomically
reserves the selected instance; stale selections return `browser_instance_in_use`.

## Lifecycle contract

All routes below are relative to
`/api/v1/workspaces/{workspace_id}/browser-spaces` and wrap results in `data`.

| Method and path | Result |
| --- | --- |
| `GET ?limit=20` | Array of accessible Spaces, newest first; maximum 100 |
| `POST` | Created Space, HTTP 201 |
| `GET /{space_id}` | Space plus `active_task` and `latest_task` |
| `POST /{space_id}/tasks` | HTTP 202 for new work, 200 for identical request replay |
| `POST /{space_id}/cancel` | Current task, or its existing terminal result |
| `POST /{space_id}/close` | Closed Space; 409 while cancellation remains pending |
| `GET /{space_id}/events?after_sequence=0&limit=100` | Ascending events after the cursor |

`active_task` contains only queued/running work. `latest_task` retains the latest
terminal outcome so the panel can display results after polling or reloading.
The frontend drains event pages using the sequence cursor.

Tasks invoke one granted capability from the selected bundle. JSON schema and
host validation reuse the capability service and run before inserting a task.
Unknown/ungranted capabilities, invalid args and invalid timeouts return 422 with
no dispatch. Argument input is limited to 64 KiB and timeout to 1–600 seconds.
An optional `gate` follows the existing platform-admin authorization rule; a
Workspace admin role alone cannot authorize a runtime gate.

Task and queued-event persistence commits before execution. The capability
adapter commits its audit start before runtime I/O, so no database transaction is
held during dispatch. Runtime readiness/component/gate failures persist a failed
task and stable error code. Readiness/allocation failures mark the Space `error`.

Request IDs are unique within a Space. Repeating the same request returns its
existing task; changing capability, args, timeout or gate returns an idempotency
conflict. Cancellation records one `cancel_requested` event and remains
non-terminal until execution unwinds. Closing releases a reservation only after
the active task has finished. Closed Spaces reject new tasks.

The current remote Agent transports do not acknowledge cancellation cleanup.
Consequently a remote cancel request waits for the original runtime response;
it does not abandon the HTTP request and falsely free the browser. A transport
timeout, disconnect or interrupted remote call quarantines the allocation as
`runtime_cleanup_unconfirmed`: new tasks and close return 409, and the instance
remains reserved. An operator must retire/reconcile that runtime allocation;
there is deliberately no force-release API without cleanup evidence.

New task rows retain only an argument-count projection. Results and event
payloads are redacted and bounded to 64 KiB; oversized output is replaced with
`{"truncated":true,"reason":"result_too_large"}`. Validation errors never echo
rejected input. Existing capability audit rows retain their separate audit
contract.

## Verification

Run from the repository root, using the project's Python environment:

```powershell
python -m pytest -o addopts='' tests/unit/test_browser_space_service.py tests/unit/api/test_browser_spaces.py tests/integration/test_browser_space_service.py tests/integration/test_browser_space_contract.py -m 'not live'
python -m pytest -o addopts='' tests/unit/test_browser_runtime_bundle.py tests/unit/test_browser_service.py tests/unit/test_migration_heads.py tests/unit/test_restart_api.py
```

For the real-browser integration test, set `BROWSER_SPACE_TEST_CHROMIUM` to an
installed Chromium executable and run:

```powershell
python -m pytest -o addopts='' tests/integration/test_browser_space_contract.py::test_real_browser_create_snapshot_close
```

This starts a fresh Chromium process and exercises the production Space routes
and capability dispatcher through an in-process test Agent HTTP transport. It
does not attach to operator tabs or certify a deployed Docker Agent installation.
The frontend `browser-space.spec.mjs` tests separately exercise the operator UI
with mocked API responses.
