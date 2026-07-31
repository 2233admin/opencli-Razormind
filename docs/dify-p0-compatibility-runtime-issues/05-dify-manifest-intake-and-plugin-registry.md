# P0-05: Import Dify manifests into a real plugin registry

Labels: `p0`, `backend`, `plugins`, `security`, `dify`

Parent: `docs/dify-p0-compatibility-runtime-PRD.md`

Blocked by: P0-02

## Outcome

Replace frontend-only “installed” claims with a persisted plugin installation catalog that can safely import Dify manifest metadata without executing package code.

## Files

Create:

- `backend/models/plugin_installation.py`
- `backend/schemas/plugin.py`
- `backend/services/plugin_registry_service.py`
- `backend/plugins/dify_manifest.py`
- `backend/plugins/dify_package.py`
- `backend/api/v1/plugins.py`
- `backend/migrations/versions/<next_revision>_add_plugin_installations.py`
- `tests/fixtures/dify_plugins/tool_manifest.yaml`
- `tests/fixtures/dify_plugins/multi_capability.difypkg`
- `tests/integration/test_plugin_dify_import_api.py`
- `tests/unit/test_dify_package_security.py`

Modify:

- `backend/models/__init__.py`
- `backend/api/v1/__init__.py:5-63`
- `backend/workflow/capability_projection.py`
- `backend/workflow/node_registry.py`

## Persistence

Create `plugin_installations` with:

- `id`
- `provider_key`
- `name`
- `author`
- `version`
- `source_kind`
- `source_digest`
- `manifest_spec_version`
- `signature_state`
- `manifest_json`
- `capabilities_json`
- `permissions_json`
- `runtime_status`
- `blockers_json`
- timestamps

Unique key: `(provider_key, version, source_digest)`.

Do not store package executable bytes in the database in P0.

## Build

- Add list/detail/import/delete endpoints from the PRD.
- Parse standalone manifest YAML and `.difypkg` ZIP.
- Enforce archive limits and ZIP-slip/symlink protections.
- Treat all archive content as untrusted.
- Record `unsigned` or `present_unverified`; do not perform or claim trust verification.
- Project Tool, Model, Datasource, Trigger, Agent Strategy and Endpoint families into normalized capabilities.
- Produce locked node definitions only for executable flow capabilities.
- Keep installations, provider credentials and runtime resources as domain objects.
- Default every imported capability to `BLOCKED`.
- Promote a capability to `READY` only when an existing runtime adapter explicitly claims its compatibility id.
- Seed bundled OpenCLI providers through the same registry read model so the plugin page does not combine two unrelated truth sources.
- Reject uninstall with `409` while a stored workflow draft references the installation/version.

## Acceptance criteria

- [ ] Valid manifest YAML installs and is returned by list/detail.
- [ ] Valid `.difypkg` installs the same normalized metadata as its manifest.
- [ ] Duplicate import is idempotent or returns a stable conflict without duplicate rows.
- [ ] ZIP-slip, symlink, duplicate normalized path, zip bomb ratio and oversize fixtures are rejected.
- [ ] Invalid YAML and missing required manifest fields return stable 422 errors.
- [ ] Imported code is never extracted to an executable directory.
- [ ] Unsupported capabilities are present and `BLOCKED`, not omitted and not marked ready.
- [ ] Locked node definitions include installation id and version provenance.
- [ ] Plugin uninstall protects referenced durable workflow drafts.
- [ ] Migration upgrades and downgrades cleanly on SQLite and PostgreSQL test paths.

## Verification

Run:

    uv run pytest tests/unit/test_dify_package_security.py tests/integration/test_plugin_dify_import_api.py
    uv run alembic upgrade head
    uv run alembic downgrade -1
    uv run alembic upgrade head
    uv run ruff check backend/plugins backend/services/plugin_registry_service.py backend/api/v1/plugins.py
