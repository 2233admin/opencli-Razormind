## 1. Guardrails and Failing Evidence

- [x] 1.1 Start a Sentrux coding session and record the existing Code Intel `doctor`/manifest and baseline-format debts without repairing, repinning, or re-baselining them.
- [x] 1.2 Add the exact ten-node acceptance fixture defined in `design.md`, including the specified authored order, three edges, four unknown bindings, and two valid parked nodes.
- [x] 1.3 Add failing tests proving the current full-graph compile blocks Run before trigger scope selection and that zero batches/items are not runtime execution evidence.

## 2. Trigger Scope Domain Contract

- [x] 2.1 Implement `backend/workflow/trigger_scope.py` using `resolve_node_origin` plus `resolve_runtime_metadata`, exact binding ids, authored ordering, downstream reachability, and the specified empty-dictionary `externalWorkflow` exception.
- [x] 2.2 Cover bounded manual/schedule/webhook recognition, `ai` normalization, explicit-id precedence, one-match id omission, zero-match, kind mismatch, same-kind ambiguity, shared downstream, ordinary disconnected, and the exact `params.externalWorkflow` dictionary exception.
- [x] 2.3 Ensure the selector returns deterministic active and parked node ids and a scoped graph whose edges have both endpoints in scope.

## 3. Run Ordering

- [x] 3.1 Change workflow Run startup to select the source graph before authoritative compilation.
- [x] 3.2 Preserve all active-chain compiler, permission, adapter, runtime-binding, idempotency, and no-dispatch safety gates.
- [x] 3.3 Add integration coverage proving the four-node active chain can run beside invalid parked nodes and that parked nodes emit no events, states, dispatches, batches, or items.

## 4. Studio Validation and Publication

- [x] 4.1 Discover the union of supported trigger-reachable components while preserving the existing full-graph path for graphs with no supported trigger.
- [x] 4.2 Compile the active union; keep active diagnostics in `errors`, emit exactly one `parked_node` membership warning per parked id, then append original parked configuration diagnostics in authored order.
- [x] 4.3 Persist only the validated active graph in `resolved_graph` while leaving the editable draft graph unchanged.
- [x] 4.4 Add lifecycle tests for parked-invalid-valid-active, active-invalid, no-trigger, multiple-trigger, publish, reopen-draft, and reconnect-parked cases. *(Coverage: reconnect-parked = active-error, publish, no-trigger, multiple-trigger, validation; reopen-draft and per-case parked-invalid/parked-valid boundary tests merged into integration test.)*

## 5. Canvas Feedback

- [x] 5.1 Reuse existing validation feedback and add only `data-testid="workflow-validation-scope-summary"` to show separate active and parked counts plus node-anchored parked diagnostics.
- [x] 5.2 Keep Run Trace counts scoped to real runtime projection facts and ensure parked compile diagnostics are never labeled as execution.
- [x] 5.3 Add focused frontend regression assertions without redesigning the canvas or adding dependencies.

## 6. Verification and Handoff

- [x] 6.1 Run focused backend unit/integration tests and the existing workflow compile/runtime regression suites selected by the changed paths.
- [x] 6.2 Run the relevant frontend regression script, `npm run typecheck:frontend`, and `git diff --check`.
- [x] 6.3 Run `openspec validate trigger-scoped-workflow-execution --strict` and leave all OpenSpec task checkboxes truthful.
- [x] 6.4 Run Sentrux `session_end`; report existing baseline/tool debt separately and do not save a new baseline.
- [x] 6.5 Run or hand off the exact Orca evaluation in `design.md`, then corroborate it with Run/trace JSON for active ids, dispatch, batches, items, and parked-id absence.
- [x] 6.6 Verify rollback compatibility by loading a pre-change published version and a newly scoped version through existing read/Run APIs with no schema failure.
- [x] 6.7 Send `worker_done` with exact files changed, commands/results, remaining risks, and no completion claim unless dispatch and nonzero-output evidence are genuinely observed.
