---
title: 'ResearchGraph Mutations and Automatic Lineage'
type: 'feature'
created: '2026-08-29'
baseline_commit: 'b9ec317efda601322f6c314af8d18b5700b7aeb0'
status: 'in-progress'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The first ResearchGraph delivery exposes a deterministic, read-only projection, but a researcher cannot record a tentative claim, verify or reject it, or retract an accepted graph fact through the authoritative workflow-event history. Research-operator output already carries evidence references, yet no complete semantic lineage is persisted automatically.

**Approach:** Add versioned mutation requests and workspace-scoped plus generic run mutation endpoints that transform permitted actions into validated semantic `WorkflowRunEvent` rows through the existing allocator. Expand the replay fold to model tentative, accepted, rejected, and retracted states, and have valid research-operator partial output emit source/evidence/claim semantic rows without inventing any unavailable provenance.

## Boundaries & Constraints

**Always:** `WorkflowRunEvent` remains the unique append-only authority and existing sequence allocation, canonical payload comparison, nested transaction, and event mirroring paths are reused. Mutation requests carry `schemaVersion`, `idempotencyKey`, `expectedRevision`, and `expectedSequence`; failed validation appends nothing. A proposal is non-authoritative until a verification promotes it; rejection never promotes it. Retraction preserves its original entity/event history and makes its dependencies non-authoritative. Every emitted semantic record retains run, trace, node, revision, action lineage, and actual source/evidence references. Requests and routes must enforce run/workspace ownership exactly as their read counterparts.

**Ask First:** Introducing a graph-fact table, cache as authority, a public authorization-policy change, a dependency, an external implementation, mutation of legacy event meanings, or a cross-run relation.

**Never:** Bypass `append_workflow_run_events`; allocate a second event sequence; mutate/delete historical events; treat proposed/rejected/retracted claims as verified; infer a source, evidence batch, manifest URI, node, run, or provenance value that the operator output does not contain; expose workspace-scoped runs through generic mutation routes; or change unrelated user-owned files.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Propose | Version 1 request with unique key, current revision/sequence, and evidence-linked claim payload | Appends a semantic proposal row; projection exposes it as non-authoritative | Reject unknown/retracted evidence, duplicate entity, wrong run/trace/node, stale revision/sequence, or malformed version before append |
| Verify | Version 1 promote or reject request referencing an existing active proposal at the current revision | Appends a verification row; promote makes the proposal authoritative and reject leaves it non-authoritative | Reject unknown, cross-run, already resolved, stale, or retracted proposal; idempotent replay returns the accepted row |
| Retract | Version 1 request targets an active proposed or promoted entity | Appends a retraction row; target stays in history and all dependent claims/relations are downgraded | Reject unknown, cross-run, repeated retraction, stale revision/sequence, or incompatible action without advancing sequence |
| Automatic lineage | Research operator produces partial items with complete source/evidence references | Appends deterministic semantic source, evidence, and claim rows in the same run transcript through the allocator | Skip incomplete provenance rather than fabricate it; duplicate replay does not duplicate rows |
| Route isolation | Workspace route targets its workflow-owned run; generic route targets an unscoped run | Both append only to their allowed run and return existing API envelope conventions | Return existing 404 ownership/not-found behavior and no graph mutation on unauthorized/mismatched routes |

</frozen-after-approval>

## Code Map

- `backend/schemas/workflow.py:704-924` -- Existing v1 semantic envelope and event-row validation; add mutation request/action/result contracts and state/provenance fields while retaining backwards-compatible first-delivery event validation.
- `backend/workflow/research_graph.py:17-211` -- Pure transcript fold and reference validation; derive revision/state authority, proposal verification, rejection, retraction, dependent downgrade, and append-ready semantic event construction without storing facts.
- `backend/workflow/workflow_run_events.py:53-168,212-275` -- Locked shared allocator, suffix idempotency and candidate-transcript validation; mutation append must enter only through this seam.
- `backend/workflow/opencli_hda_tracer.py:1847-1921,2036-2124,3154-3310` -- Durable run/event persistence, event construction, research evidence binding and operator partial details; attach automatic emission at a point that has actual run/trace/node/evidence values.
- `backend/api/v1/workflows.py:63-68,589-623` -- Generic unscoped read/ownership conventions; add non-scoped mutation route and map invalid mutation requests to established errors.
- `backend/api/v1/studio_workflows.py:448-489` -- Project/workspace run ownership convention; add the scoped mutation twin.
- `tests/unit/test_research_graph.py`, `tests/unit/test_workflow_run_events.py`, `tests/unit/test_research_operators.py`, and integration workflow route tests -- extend transcript state, allocator idempotency/concurrency guard, automatic provenance, and generic/scoped ownership coverage.

## Tasks & Acceptance

**Execution:**
- [x] `backend/schemas/workflow.py` -- define versioned mutation requests, actions, expected transcript guard values, and response/state fields that forbid unknown inputs.
- [x] `backend/workflow/research_graph.py` -- validate/construct mutation events and fold the action history into deterministic authority states, including dependency downgrade after retraction.
- [x] `backend/workflow/workflow_run_events.py` and `backend/workflow/opencli_hda_tracer.py` -- append mutations and automatic research-operator lineage only via the existing allocator, preserving idempotency and actual provenance.
- [x] `backend/api/v1/workflows.py` and `backend/api/v1/studio_workflows.py` -- add matching generic and workspace-scoped write APIs with existing ownership and response conventions.
- [x] Focused unit and integration tests -- cover each matrix case, no-event-on-rejection, replay, stale concurrent guards, and automatic lineage omissions.

**Acceptance Criteria:**
- Given a valid mutation request, when the current revision and sequence match the authoritative transcript, then exactly its accepted semantic event(s) are appended by the shared allocator and replay returns the original accepted event(s).
- Given proposals, verification actions, and a retraction in a transcript, when any prefix or recovered full history is folded, then authority state and dependent downgrade are deterministic while every historical record remains visible with lineage.
- Given a stale request, unknown/cross-run/retracted target, duplicated semantic identity, invalid version, or incomplete automatic provenance, when it is processed, then it is rejected or skipped as appropriate without creating invalid authority or advancing the run transcript.
- Given a research operator partial result with complete source/evidence references, when it is persisted, then its generated records establish source-to-evidence-to-claim lineage; when any required provenance field is absent, no fabricated record is emitted.
- Given generic and workspace mutation callers, when they target a run outside their allowed scope, then existing ownership semantics prevent every write.

## Spec Change Log

## Design Notes

The event row sequence is both ordering and optimistic-concurrency frontier. `expectedSequence` guards against a stale client transcript, while `expectedRevision` guards the active semantic revision. Retract does not erase records: the fold marks the target and descendants non-authoritative so recovery never needs a compensating store. Automatic records use stable identities derived only from the operator's output and its bound evidence reference; a partial item missing a required lineage field is intentionally omitted.

## Verification

**Commands:**
- `uv run pytest --no-cov tests/unit/test_research_graph.py tests/unit/test_workflow_run_events.py tests/unit/test_research_operators.py` -- expected: state transition, idempotency, transcript validation, and provenance omissions pass.
- `uv run pytest --no-cov tests/integration/test_workflow_deep_research_api.py tests/integration/test_trigger_scoped_workflow_execution.py` -- expected: automatic lineage and generic/scoped mutation API ownership/guard coverage pass.
- `pwsh -NoProfile -Command '& (Join-Path $env:CODE_INTEL_HOME "legacy/Invoke-SentruxAgentTool.ps1") session_end "C:/Users/Administrator/orca/workspaces/opencli-Razormind/集合新功能"'` -- expected: structural gate detects no regression from the session baseline.
