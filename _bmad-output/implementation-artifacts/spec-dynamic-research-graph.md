---
title: 'ResearchGraph Event Projection'
type: 'feature'
created: '2026-08-29'
baseline_commit: 'b9ec317efda601322f6c314af8d18b5700b7aeb0'
status: 'in-progress'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Workflow runs retain ordered node events and evidence projections, but users cannot obtain a deterministic, provenance-preserving graph of research claims, evidence, sources, and relations from the authoritative event transcript. This blocks trustworthy recovery and backend graph consumers.

**Approach:** Define a clean-room, versioned ResearchGraph semantic envelope inside the existing `WorkflowRunEvent` contract and fold accepted persisted events into an ownership-scoped, read-only graph projection. The projection is rebuilt from event history; it does not introduce graph-fact persistence or a second write authority.

## Boundaries & Constraints

**Always:** `WorkflowRunEvent` is the sole write history. ResearchGraph events include schema version, stable event/idempotency key, run/trace/node/revision/lineage references, and validated payload. Folding is deterministic for the same accepted ordered list, including prefix replay and recovery. Every entity retains run, trace, node, source, and evidence provenance. Existing workspace/run ownership protects graph queries. Reuse current transaction, event-ID, sequence-allocation, event-listing, and evidence-projection conventions.

**Ask First:** Adding a graph-fact database table or cache treated as authority; changing legacy event meanings or public trace contracts; broadening authorization policy; adding a dependency; or importing/copying external source code.

**Never:** Create a second authoritative store; mutate/delete historical events; bypass event sequence/idempotency allocation; infer absent provenance; expose graph facts across workspace/run boundaries; treat model or human suggestions as verified facts; or change user-owned `.agents/`, `_bmad-output/`, or other pre-existing dirty/untracked work beyond this approved spec output.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Valid semantic event | Versioned envelope for a persisted run/node with claim, evidence, source, or relation payload | Existing append path accepts it and folding exposes ordered, provenance-linked graph entities | Reject malformed IDs/references, unsupported version/type, invalid relation shape, missing required evidence, or cross-run reference before append |
| Retry, reordering, recovery | Duplicate event/idempotency key, out-of-order client input, or a run reloaded after restart | Existing allocator establishes authoritative order; repeated replay produces the same projection without duplicate entities | Return existing accepted event or deterministic conflict and never advance sequence for rejected input |
| Graph query | Authorised caller requests a run and an optional sequence/revision/local-scope bound | Returns deterministic, bounded projection including source/evidence/run/trace/node lineage | Reject inaccessible/missing run; validate bounds and return existing API-style errors |
| Legacy run | Persisted run has no ResearchGraph envelope | Empty graph projection while trace, evidence projection, continuation, and canvas contracts remain unchanged | No fallback inference or legacy payload mutation |

</frozen-after-approval>

## Code Map

- `backend/schemas/workflow.py:650-706, 873-1038` -- Node-event, projection, evidence, trace, and checkpoint contracts; add versioned ResearchGraph envelope and query-response schemas.
- `backend/models/workflow_run.py:8-77` -- `WorkflowRunEvent` is durable authority with globally unique event IDs and `(run_id, sequence)` uniqueness; no graph persistence model is permitted.
- `backend/workflow/workflow_run_events.py:49-160, 232-305` -- Canonical append, idempotent suffix classification, counter reconciliation, nested transaction, and locked contiguous sequence reservation.
- `backend/workflow/opencli_hda_tracer.py:1729-1778, 1849-1931` -- Ordered persisted-event listing and write/cache seam; graph recovery reads the former and never makes cache authoritative.
- `backend/workflow/evidence_projection.py:69-188` -- Existing run/node/trace/batch/manifest/ODP provenance to reuse when validating and displaying evidence references.
- `backend/api/v1/workflows.py:518-780` -- Workspace-scoped evidence, trace, checkpoint, and raw-event API conventions for the graph query endpoint.
- `backend/workflow/research_operators.py:17-150, 520-686` -- Read-only evidence-linked output contract informing envelope validation; automatic operator emission is intentionally deferred.
- `tests/unit/test_workflow_run_events.py:115-335`, `tests/integration/test_workflow_opencli_hda_trace_api.py`, and `tests/integration/test_workflow_deep_research_api.py` -- Existing idempotency, trace, and evidence-provenance behavior to preserve and extend.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/schemas/workflow.py` and `backend/workflow/research_graph.py` -- define the versioned semantic envelope, immutable graph records, canonical validation, and pure fold/projection over persisted `WorkflowRunEvent` data.
- [ ] `backend/workflow/workflow_run_events.py` and the established persisted-event reader seam -- admit validated ResearchGraph envelopes through the existing allocator and make replay/recovery use accepted ordered rows only.
- [ ] `backend/api/v1/workflows.py` -- add workspace/run ownership-scoped ResearchGraph projection queries with bounded deterministic filtering and existing API error conventions.
- [ ] `tests/unit/test_research_graph.py` plus focused existing event/API tests -- test fold ordering, prefix replay, recovery, duplicate delivery, invalid envelope/reference/version rejection, ownership, provenance, and no-envelope legacy compatibility.

**Acceptance Criteria:**
- Given accepted research semantic events, when folding any persisted sequence prefix or reloading all event rows after restart, then the same ordered graph projection is returned without a graph-fact table.
- Given a graph claim, source, evidence, or relation, when queried, then it carries its originating run, trace, node, source/evidence, and revision/lineage identifiers.
- Given duplicate, malformed, unsupported-version, out-of-order, or cross-run input, when it reaches append or query validation, then no invalid graph entity or incorrect event sequence becomes authoritative.
- Given an unauthorised/missing run or bounded query, when the graph API is called, then existing workspace ownership and deterministic error/limit behavior apply.
- Given an existing run with no semantic envelopes, when trace/evidence/continuation endpoints and graph query are called, then legacy behavior is unchanged and graph output is empty.

## Spec Change Log

## Design Notes

The event row supplies order and idempotency. Folding is pure over semantic envelopes, so recovery relists persisted events; caches are disposable. Proposal/verification/retraction writes, automatic operator/evidence emission, and frontend visualisation are deferred until graph identity and query contracts are stable.

## Verification

**Commands:**
- `uv run pytest tests/unit/test_research_graph.py tests/unit/test_workflow_run_events.py` -- expected: fold, validation, ordering, retry, and recovery contracts pass.
- `uv run pytest tests/integration/test_workflow_deep_research_api.py tests/integration/test_workflow_opencli_hda_trace_api.py` -- expected: provenance, graph query ownership, and legacy trace compatibility pass.
- `pwsh -NoProfile -Command '& (Join-Path $env:CODE_INTEL_HOME "legacy/Invoke-SentruxAgentTool.ps1") session_end "C:/Users/Administrator/orca/workspaces/opencli-Razormind/集合新功能"'` -- expected: no structural regression from the baseline.
