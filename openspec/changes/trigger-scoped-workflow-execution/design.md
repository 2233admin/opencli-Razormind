## Context

See `proposal.md` for motivation and `specs/trigger-scoped-workflow-execution/spec.md` for normative behavior.

The current Run path calls `compile_workflow_project(body.project)` before `_select_runtime_nodes_for_trigger(...)`. Compilation validates every authored node, so selection never runs when an unrelated parked node has an unknown binding. Studio draft validation also compiles the complete graph and persists the complete `resolved_graph` when valid. The runtime selector already performs downstream reachability and explicitly excludes ordinary disconnected nodes, so the defect is ordering and scope authority rather than missing graph traversal.

The observed revision-84 graph provides a reproducible shape, not a fixture dependency: ten nodes, three edges, a four-node collection component, six parked nodes, and four parked unknown-binding diagnostics. Local read-only comparison proved the complete graph invalid and the four-node component valid.

## Goals / Non-Goals

**Goals:**

- Establish one reusable source-level trigger-scope result before compilation.
- Use the same scope semantics for Run, Studio validation, and immutable publication.
- Preserve parked authoring work in drafts while removing it from executable authority.
- Keep active-chain validation and all existing runtime safety gates strict.
- Produce node-anchored warnings and transparent active/parked UI counts.

**Non-Goals:**

- Do not connect, delete, repair, enable, or execute parked nodes automatically.
- Do not add or alias node-library bindings for `primitive.ai.llm`, `primitive.plugin.trigger`, `primitive.document.extract`, or any other catalog id.
- Do not change OpenCLI adapter commands, browser behavior, concurrency, data schemas, authentication tokens, or provider configuration.
- Do not introduce a database migration unless an existing persisted field cannot carry the required warnings; the existing validation `warnings` JSON field is the default contract.
- Do not refactor the general compiler, replace React Flow, redesign the canvas, or alter unrelated evidence/Galaxy pages.
- Do not make validation execute nodes, and do not claim data collection success from compile success.

## Decisions

### Decision 1: Select a source-level scope before compile

Add a small workflow-domain selector that accepts a `WorkflowProject`, trigger kind, and optional trigger node id and returns a scoped project plus deterministic active and parked node ids. It SHALL reuse canonical runtime-origin/binding semantics; it SHALL NOT infer triggers from display labels or broad `kind` guesses.

Run shall materialize any existing templates required for canonical node identity, select the scope, then compile only the scoped project. Trigger ambiguity/mismatch errors remain unchanged. The compiled-runtime selector may remain as a defensive assertion, but it is no longer the first scope boundary.

Supported kinds and precedence are intentionally identical to the current registry and compiled selector:

1. Normalize request kind `ai` to `manual`.
2. Call `resolve_node_origin(node)` first. A node with `origin.kind == "legacy"` and non-empty `origin.notes` is not a supported trigger and remains parked unless reached from another supported trigger.
3. Call `resolve_runtime_metadata(node, adapter)` and inspect only `metadata.binding.binding_id`; do not reimplement catalog matching. Binding `workflow.trigger.webhook_input` is `webhook`. Binding `workflow.trigger.schedule_tick` is `manual` when `params.builder.nodeType == "manual-trigger"` or `params.mode == "manual"`; otherwise it is `schedule`.
4. Registry output is single-valued. If an authored node could satisfy both schedule and webhook predicates, `resolve_runtime_metadata` checks webhook first, so `workflow.trigger.webhook_input` wins. Native or other earlier resolver branches win by returning a different binding and therefore are not trigger entries.
5. When `triggerNodeId` is supplied, it wins and must exist, be a supported trigger, and match the normalized request kind.
6. Without an id, select exactly one matching trigger; zero is `workflow_trigger_kind_mismatch`, more than one is `workflow_trigger_ambiguous`.
7. Trigger-scoped selection applies only when the authored graph contains at least one supported trigger entry. A graph with no supported trigger preserves the existing full-graph validation and Run behavior for legacy, media-canvas, and other non-trigger workflows; it does not classify the entire graph as parked. Once any supported trigger exists, the old compile-then-select fallback is not used.

Alternative rejected: compile the complete graph and filter compile errors afterward. That cannot produce a trustworthy plan when full compilation fails and risks accidentally suppressing active structural errors.

### Decision 2: Validation discovers active trigger components and demotes only parked diagnostics

Studio validation shall discover every supported trigger entry, compute the union of their downstream components, and compile that active union. If the graph has no supported trigger entry, validation shall preserve the existing full-graph path and diagnostics. Errors attached to active nodes/edges remain errors.

The validator shall emit exactly one membership warning per parked node using `WorkflowCompileError(code="parked_node", node_id=<id>, path=["nodes", <id>])`, then preserve any parked-node configuration diagnostics as warnings with their original code and node id. Warning ordering shall be stable by authored node order and diagnostic order. Existing `warnings: list[WorkflowCompileError]` storage and response shape shall be reused. The canvas derives `parkedCount` from unique membership warnings and `activeCount` from authored node count minus `parkedCount`; no API schema expansion is authorized unless this is proven impossible.

Alternative rejected: make all full-graph errors warnings whenever any valid chain exists. That could demote an error on a second real trigger component and publish unsafe executable authority.

### Decision 3: Published authority is the validated active graph

The validation row's `resolved_graph` shall contain the active union, not the complete draft. Publishing continues to copy the already validated immutable graph. The draft row remains untouched and retains parked nodes.

Alternative rejected: publish the full draft but rely on Run-time filtering. Schedules, API/MCP callers, inspection views, and future runtimes would then disagree about which graph is authoritative.

### Decision 4: Preserve the external-workflow exception narrowly

After normal downstream reachability is complete, include every node for which `isinstance(node.params.get("externalWorkflow"), dict)` is true; an empty dictionary is included. Include the external node itself even when disconnected, retain only its dependencies whose ids are already active, and do not recursively include its downstream nodes unless they were otherwise reachable from the selected trigger. This is the exact current selector behavior. No other parameter shape, UI label, node kind, or catalog id activates the exception.

### Decision 5: Keep the UI change additive and small

The validation feedback in `workflow-editor-session.tsx` shall present active and parked counts derived from the validation response. `run-trace-panel.tsx` shall continue showing runtime projection facts and must not merge parked warnings into Run event counts. Reuse existing warning/error components and node-id-to-label mapping.

No new global state store, visualization library, route, modal framework, or canvas layout algorithm is authorized.

## API and Compatibility

- Existing endpoints, authentication, request bodies, status codes, idempotency behavior, and `valid/errors/warnings` fields remain.
- New parked diagnostics use `WorkflowCompileError` entries in `warnings`; clients that ignore warnings remain compatible.
- The canonical warning code `parked_node` identifies membership. Additional parked configuration warnings retain their original diagnostic code and node id.
- Version and Run projections remain source compatible. Counts change only by correctly excluding parked nodes.

## File Boundaries for the Cloud Worker

The worker MAY modify only these exact paths:

- `backend/workflow/opencli_hda_tracer.py`
- `backend/workflow/trigger_scope.py` (new)
- `backend/api/v1/studio_lifecycle.py`
- `backend/api/v1/studio_helpers.py`
- `frontend/components/flow/workflow-editor-session.tsx`
- `tests/integration/test_workflow_compile_api.py`
- `tests/integration/test_trigger_scoped_workflow_execution.py` (new)
- `frontend/scripts/check-workflow-regressions.mjs`
- this OpenSpec change's `tasks.md` checkboxes after evidence passes

Any required edit outside this list is an escalation: stop, report the exact dependency, and wait for coordinator approval. In particular, `backend/api/v1/studio_schemas.py` and `frontend/components/flow/run-trace-panel.tsx` are not authorized because the existing warnings and projection contracts are sufficient. The worker SHALL NOT modify `.env*`, migrations, lockfiles, package manifests, node catalogs, unrelated OpenSpec changes, root documentation, evidence/Galaxy files, or user-owned dirty files.

## Risks / Trade-offs

- [Risk] Source-level trigger recognition diverges from compiled-runtime recognition. → Reuse canonical origin/binding resolution and add parity tests for manual, schedule, webhook, ambiguous, mismatch, and external-workflow cases.
- [Risk] Parked diagnostics hide a component the author expected to publish. → Report every parked node deterministically and show active/parked counts before publication.
- [Risk] Shared downstream nodes reachable from multiple triggers are duplicated or lose dependencies. → Build an ordered union by authored node order and retain only edges whose endpoints are active; keep per-Run dependency filtering tests.
- [Risk] Publishing a scoped graph surprises clients reading the full draft. → Preserve the full draft and document that published versions are executable authority, not authoring scratch space.
- [Risk] A broad refactor of the large tracer increases regression risk. → Prefer a small pure helper and targeted call-order change; no general compiler rewrite.

## Migration Plan

1. Add failing regression tests that reproduce a valid trigger component beside invalid parked nodes.
2. Add the pure source-level scope selector and parity tests.
3. Change Run ordering to select then compile, preserving trigger errors and the external-workflow exception.
4. Change Studio validation to compile the active union and emit parked warnings; persist only the active resolved graph.
5. Add minimal validation UI feedback and focused frontend regression checks.
6. Run focused tests, frontend typecheck, OpenSpec strict validation, diff check, Sentrux session gate, and Orca browser acceptance.

Rollback is a normal revert of the implementation commit. No schema or data migration is expected. Existing drafts and versions remain readable; newly published scoped versions remain valid workflow graphs.

Rollback verification must load one pre-change published version and one newly scoped published version through the existing read/Run APIs after the revert. Both must remain schema-readable; the pre-change version retains its previous behavior, and the scoped version remains a self-contained executable graph without requiring parked draft nodes.

## Acceptance Fixture

Use a repository fixture, never the user's database row, with nodes in this authored order: `trigger`, `source`, `hygiene`, `records`, `llm-a`, `llm-b`, `plugin`, `review`, `document`, `notify`. The only edges are `trigger -> source`, `source -> hygiene`, and `hygiene -> records`. `llm-a`, `llm-b`, `plugin`, and `document` use deterministic unknown bindings; `review` and `notify` are valid but parked.

Required assertions:

- Full-graph compile reproduces four `unknown_node_library_binding` diagnostics.
- Trigger-scoped compile is valid with four nodes and three edges.
- Run projection contains states/events only for `trigger`, `source`, `hygiene`, and `records`; parked ids have zero dispatch/event/batch/item presence.
- Draft validation is `valid=true`, has six unique `parked_node` warnings plus four parked configuration warnings, and stores a four-node `resolved_graph`.
- Publishing copies the four-node graph while a subsequent draft GET still returns ten nodes.
- Connecting `llm-a` downstream makes its unknown binding an active error and validation `valid=false`.
- Browser acceptance checks visible `活动节点 4`, `未接入节点 6`, a passing active-chain validation state, and a Run Trace with four nodes and no parked node labels.

## Exact API and Browser Acceptance

The integration test seeds its own workspace/project/workflow/draft rows through existing test fixtures; it must not read or mutate the developer database. It then exercises:

1. `POST /api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/draft/validation-runs` with no request body. Assert `response.data.valid == true`, `response.data.errors == []`, six unique `response.data.warnings[?code=="parked_node"].node_id` values, and four `unknown_node_library_binding` warnings.
2. `POST /api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/versions` with `{"reason":"trigger scope acceptance","expectedRevision":<draft revision>,"validationRunId":<validation id>}`. Assert `response.data.graph.nodes` has four ids and `response.data.graph.edges` has three ids.
3. `GET /api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/draft`. Assert `response.data.graph.nodes` still has ten ids.
4. `POST /api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs` with `{"inputs":{},"user":"trigger-scope-acceptance"}` and a unique `Idempotency-Key`. Assert the projection and later trace contain no parked ids. Existing deterministic source-output fixtures may be injected so the test does not call external services.
5. `GET /api/v1/workspaces/{workspace_id}/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/trace`. Assert projected node ids equal the four active ids and batches/items follow the existing deterministic fixture expectations.

The frontend adds only `data-testid="workflow-validation-scope-summary"` to the existing validation feedback container and renders the exact text `活动节点 4 · 未接入节点 6` for the acceptance fixture. The coordinator's Orca acceptance evaluates the current page after Validate and Run and asserts:

```javascript
const body = document.body.innerText;
const summary = document.querySelector('[data-testid="workflow-validation-scope-summary"]')?.textContent || '';
({
  summary,
  activeOk: summary.includes('活动节点 4'),
  parkedOk: summary.includes('未接入节点 6'),
  runHasFour: body.includes('Nodes') && body.includes('4'),
  parkedAbsentFromTrace: !['llm-a', 'llm-b', 'plugin', 'review', 'document', 'notify'].some((id) => body.includes(id)),
});
```

The coordinator must additionally inspect the Run API/trace JSON because DOM text alone is insufficient evidence of dispatch or nonzero output.

## Concrete Rollback Check

The integration test creates version 1 from the existing four-node valid fixture before applying parked-node scope logic, then creates version 2 from the ten-node draft after scoped validation. Record both version ids in the test. The rollback compatibility test model-validates `version1.graph` and `version2.graph` as `WorkflowProject`, invokes the existing published-Run seam once for each graph with deterministic source outputs, and asserts neither produces `invalid_workflow_project` or schema errors. No production row, migration, downgrade command, or mutable database snapshot is involved.
