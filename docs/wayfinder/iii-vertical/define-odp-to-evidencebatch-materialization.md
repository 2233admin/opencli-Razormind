# ODP-to-Workflow EvidenceBatch Materialization

**Wayfinder research for [#24](https://github.com/1012839419a-alt/opencli-Razormind-gjx/issues/24)**  
**Status:** adopted Wayfinder decision; not implemented.

## Decision summary

The first non-bypass vertical materializes EvidenceBatch data through an independent Rust **`odp-query`** read-only service. `odp-store` remains the ODP Postgres writer and schema owner; `odp-ingest` remains ingress/health only. Admin MUST NOT read ODP tables directly, and neither Redis acknowledgements/notifications, browser access, nor Admin's unrelated `/records` store is ODP record truth.

Admin remains the public, scoped orchestration surface: it authenticates and authorizes the workspace/project/workflow/run/batch request, derives fixed bounded ODP query inputs from its durable command/attempt correlation ledger, and delegates the read to `odp-query`. The mandatory boundary is authenticated Admin-to-`odp-query` machine trust plus a workspace/run/batch delegated scope. A signed, short-lived grant is a candidate implementation, not a prerequisite. In all cases, `odp-query` rejects arbitrary task, trace, source, SQL, JSONB-path, or cursor predicates and returns only field-whitelisted, server-redacted Record projections/references.

A collector final expected-key report is a **new, unimplemented prerequisite contract**, not an observed capability. Once implemented, it creates an immutable expected `(source_id,event_id)` set against which `odp-query` performs exact reconciliation. **Ingest acceptance, empty query result, Redis XACK, Redis stream ID, and `odp.record.committed` notification are not a completion watermark.** An exact ODP lookup establishes only that a record exists; it cannot infer whether this attempt inserted the record or encountered a duplicate. New-versus-duplicate classification requires a durable ingest/attempt receipt or equally rigorous immutable attempt-boundary evidence; without it the classification remains `unknown`.

The proposal establishes B0–B3 as to-spec implementation preconditions and strengthens #23's correlation constraints: task/trace must reach Record v2, the complete Admin envelope stays outside Record v2, and no EvidenceBatch/Delivery decision may treat these contracts as already implemented.

## Observed facts

| Fact | Primary evidence | Consequence |
| --- | --- | --- |
| `odp-ingest` accepts records and publishes to the bus; its HTTP surface is ingestion/health, not an ODP record read model. `odp-store` owns `odp_records`, applies its schema, commits PostgreSQL, then attempts the committed notification. The ODP enterprise plan assigns cursor read APIs to a separate Rust `odp-query` service and prohibits Admin/`odp-store` in-process DB sharing. | [`odp-rs/crates/odp-ingest/src/handlers.rs:15-123`](../../../odp-rs/crates/odp-ingest/src/handlers.rs); [`odp-rs/crates/odp-store/src/main.rs:1-92`](../../../odp-rs/crates/odp-store/src/main.rs); [`odp-rs/crates/odp-store/src/writer.rs:11-123`](../../../odp-rs/crates/odp-store/src/writer.rs); [`docs/PLAN_odp_enterprise.md:60-77`](../../PLAN_odp_enterprise.md#L60-L77). | The read contract belongs to a separate `odp-query` service. `odp-store` owns writer/schema truth; `odp-ingest` is not a reader; Admin must delegate rather than share the ODP database session. |
| ODP persistence is unique only on `(source_id,event_id)`. `odp_records` contains `task_id`, `trace_id`, and `committed_at`, but the current migration indexes only `(source_id, source_ts DESC)`. | [`odp-rs/crates/odp-store/src/writer.rs:11-40`](../../../odp-rs/crates/odp-store/src/writer.rs); [`odp-rs/crates/odp-contracts/src/lib.rs:16-85`](../../../odp-rs/crates/odp-contracts/src/lib.rs). | Task/trace materialization queries need an explicit index plan; their columns are not an existing indexed/read API. |
| The store publishes `odp.record.committed` only after its SQL transaction commits, but logs and continues when publication fails; stream acknowledgement occurs separately. | [`odp-rs/crates/odp-store/src/main.rs:74-92`](../../../odp-rs/crates/odp-store/src/main.rs); [`odp-rs/crates/odp-store/src/writer.rs:79-123`](../../../odp-rs/crates/odp-store/src/writer.rs); [`odp-rs/crates/odp-bus/src/redis_streams.rs:94-108`](../../../odp-rs/crates/odp-bus/src/redis_streams.rs). | The committed stream is best-effort post-commit acceleration, not a complete watermark. XACK and stream ID are transport facts, not record facts. |
| DLQ is a distinct ODP table with source/event/error/delivery count/payload, but current code has no read API, replay API, or retention contract for it. | [`odp-rs/crates/odp-store/src/writer.rs:44-63,125-182`](../../../odp-rs/crates/odp-store/src/writer.rs). | A missing record cannot be called rejected/DLQ without an explicit read/result contract. Unknown DLQ/retention stays indeterminate. |
| Existing Admin ODP access is limited to control metrics through a dedicated read-only engine; it does not expose records. Admin `/records` is a separate Admin store. | [`backend/control/collectors/odp_metrics.py:77-83,249-260`](../../../backend/control/collectors/odp_metrics.py); [`backend/api/v1/records.py:20-35`](../../../backend/api/v1/records.py). | #24 must create a bounded ODP read-service delegation, not expose the existing engine as a generic record browser or reuse Admin `/records`. |
| Studio EvidenceBatch endpoints authorize the requested workspace/project/workflow/run scope, then derive metadata/detail from the persisted WorkflowRun projection. They do not query ODP. | [`backend/api/v1/studio_workflows.py:469-585`](../../../backend/api/v1/studio_workflows.py); [`backend/workflow/evidence_projection.py:1-93,190-220`](../../../backend/workflow/evidence_projection.py). | The current UI path can be retained as the public presentation seam only after its Run projection receives an authoritative materialization manifest. |
| Current batch references are deterministic UUID5 projection IDs with two different derivation branches; `odpRef` is a formatted optional string. | [`backend/workflow/opencli_hda_tracer.py:2259-2311`](../../../backend/workflow/opencli_hda_tracer.py); [`backend/schemas/workflow_runtime.py:99-127`](../../../backend/schemas/workflow_runtime.py). | The first ODP-backed vertical must choose the dispatch/task branch and must not treat `odpRef` as a resolver. |
| ResearchGraph lineage only contributes when evidence refs are complete; it carries source/evidence/item/batch/run/node/manifest fields and can include `odpRef`, but it does not query ODP. | [`backend/workflow/opencli_hda_tracer.py:3192-3242`](../../../backend/workflow/opencli_hda_tracer.py); [`backend/workflow/research_graph_lineage.py:19-128`](../../../backend/workflow/research_graph_lineage.py). | ResearchGraph must consume a finalized Admin EvidenceBatch manifest with explicit record references; it cannot be the materializer. |

## B0–B3 prerequisite contracts and ownership

### B0 — independent `odp-query` Rust read service

The repository plan requires a separately deployed Rust `odp-query` service for ODP record reads, cursor pagination, and export. It is read-only against ODP's Postgres truth. `odp-store` remains writer and schema owner; it is not the HTTP read router. `odp-ingest` remains health/ingest only.

Admin is the only public EvidenceBatch surface. Its existing workspace/project/workflow/run endpoints may present an ODP-backed batch, but the browser never receives ODP database access and Admin never uses its control metrics engine or `/records` store as a substitute for an `odp-query` result.

### B1 — collector final expected-key report is a new contract

No current collector final expected-key report exists. Before a batch can claim `completed`, `completed_empty`, or `partial`, a new versioned **`CollectorFinalExpectedKeyReportV1`** contract MUST be implemented and durably associated with the Admin command/attempt:

```text
command_id, attempt_id, task_id, trace_id
workspace_id, project_id, workflow_id, run_id, node_id
allowed_source_ids
collector_item_count
expected_record_key_set_hash
bounded expected `(source_id,event_id)` pages or immutable equivalent
normalized collector outcome and declared rejected keys/reasons
report_sequence, emitted_at, payload_hash
```

This is bridge/Admin lifecycle metadata, not Record v2. It creates the expected-key-set completion boundary only after it is durably recorded. Before that contract exists, an ODP-backed batch is not materializable as complete/empty/partial; its materialization fact is unavailable.

### B2 — task/trace continuity and classification evidence

Admin allocates `attempt_id`, `task_id`, and `trace_id` before dispatch. The III bridge passes task/trace to the collector. Record v2 retains only approved data-plane correlation:

```text
source_id
event_id
task_id
trace_id
```

The complete Admin correlation envelope—workspace/project/workflow/version/run/node/command/attempt/source-binding revision/collector payload hash—remains in Admin's durable ledger and authenticated bridge lifecycle metadata. It MUST NOT be copied into Record v2 `payload` or `raw_data`.

An exact `(source_id,event_id)` query proves that an ODP record is present. It cannot prove that the current attempt inserted it rather than hit the existing unique key, and it MUST NOT infer that answer from `committed_at`, record ID order, stream arrival, or a time window. Separate `inserted` and `duplicate_existing` counts require one of these unimplemented prerequisite evidences:

1. a durable, attempt-bound ingest outcome receipt keyed by `attempt_id`, `task_id`, and exact source/event key, queryable through `odp-query`; or
2. a rigorously specified immutable attempt boundary that proves the record's insertion outcome.

Until such evidence exists, the manifest reports `record_present_count` and `new_or_duplicate_unknown_count`; it does not fabricate `new` or `duplicate_existing` counts. A durable receipt is also required to classify a key as rejected; a matching durable DLQ result is required to classify it as DLQ.

### B3 — stable page snapshot, exact reconciliation, and retention

The attempt page query is fixed at its first page. `odp-query` records an `as_of` bound and a query fingerprint, then all subsequent pages use the fixed predicate:

```sql
task_id = :task_id
AND trace_id = :trace_id
AND source_id = ANY(:allowed_source_ids)
AND committed_at <= :as_of
AND (committed_at, id) > (:last_committed_at, :last_id)
ORDER BY committed_at ASC, id ASC
LIMIT :bounded_limit
```

The opaque cursor contains version, query fingerprint, `as_of`, and last `(committed_at,id)`. It is invalid outside the original scope/fingerprint. `(committed_at,id)` is a stable page order only; it is not a completion watermark.

Exact expected-key reconciliation is a separate bounded query from the page scan. It is what proves presence for each declared key. The read contract MUST expose retention/deletion state where known. If a key is not found and the service cannot establish whether retention/deletion occurred, its state is `unknown`; it is neither absent evidence nor a zero result.

## Service-authentication and query contract

The mandatory trust contract is:

1. Admin authenticates the public caller and validates the full workspace/project/workflow/run/batch relationship.
2. Admin obtains the immutable attempt correlation and report references from its own ledger. The caller never provides the ODP predicate.
3. Admin authenticates to `odp-query` over an explicit machine trust boundary and delegates `{workspace, project, workflow, run, batch, attempt, task, trace, allowed_source_ids, query_fingerprint, allowed_fields, expiry}` for audit and enforcement.
4. `odp-query` authorizes only this Admin machine caller/scope and executes one of the fixed query modes below. It accepts no general ODP browser, arbitrary JSONB path, raw SQL, unbounded source set, or caller-selected cursor predicate.
5. `odp-query` applies server-side JSONB redaction and field/size/page limits before returning data. `payload`/`raw_data` must not expose credentials, authorization headers, cookies, Chrome endpoints, bridge arguments, or any out-of-scope data.

A signed short-lived delegation grant is a recommended future hardening option for independently verifiable scope. It is not required for this design to proceed; the service-machine trust boundary and delegated workspace/run scope are.

`odp-query` exposes a versioned internal operation, conceptually:

```text
POST /internal/v1/evidence-records:query
```

| Mode | Fixed Admin-derived input | Allowed conclusion | Required index/contract |
| --- | --- | --- | --- |
| **Exact reconciliation** | Bounded expected `(source_id,event_id)` keys and the attempt/report query fingerprint. | `present`, or explicit `unknown`/retention state; never new-versus-duplicate by lookup alone. | Existing unique `(source_id,event_id)` supports lookup. |
| **Attempt page** | Exact task, trace, bounded allowed sources, fixed `as_of`, opaque cursor. | Stable list of records visible inside that immutable page snapshot; unexpected records remain diagnostic. | Add `odp_records(task_id, trace_id, source_id, committed_at, id)`. |
| **Attempt outcome receipt** | Exact attempt/task and expected keys, only when the new durable receipt contract exists. | `inserted`, `duplicate_existing`, or `rejected` for that exact attempt/key. | Define receipt storage, retention, and a query index as part of its implementation. |
| **DLQ reconciliation** | Expected exact source/event keys and bounded attempt/source context. | `dlq` only for a matching durable DLQ result; otherwise `unknown`. | Add `odp_dlq(source_id, event_id)` and define DLQ read, replay, and retention semantics. |

The sanitized response contains only a query fingerprint, snapshot `as_of`, next cursor, retention state, redaction profile version, and record references `{source_id,event_id,odp_record_id,committed_at,provider,source_ts}`. Redis stream IDs are never record IDs.

## Completion boundary and materialization outcome

After B1 exists, Admin performs exact expected-key reconciliation through `odp-query`, independently from B3's page scan. An expected key may be counted only as:

- `record_present` when exact ODP lookup finds it;
- `inserted`, `duplicate_existing`, or `rejected` only when B2's durable outcome evidence says so;
- `dlq` only when the future durable DLQ read contract says so;
- `unknown` when it is missing, retention/deletion is unknown, the result is unavailable, or no requisite outcome evidence exists.

The status precedence is deliberately ordered and mutually exclusive:

1. **`indeterminate` wins first:** if any expected key is `unknown` or `pending`, if B1 is absent, if `odp-query` is unavailable, or if retention/deletion/DLQ state cannot be classified, the status is `indeterminate` regardless of any otherwise-terminal evidence.
2. **`failed_definitive` (only if adopted by the collector policy):** a collector-declared definitive failure with no usable records is `failed_definitive`, provided no `unknown`/`pending` state exists. It is not an empty success.
3. **`completed_empty`:** the B1 report declares a successful zero-item outcome, the expected key set is exactly empty, and final exact reconciliation finds no unresolved state. An empty page/query alone is never zero.
4. **`partial`:** at least one expected key has a durable `rejected` or `dlq` outcome; every other expected key is `record_present`, `rejected`, or `dlq`; and none is `unknown` or `pending`.
5. **`completed`:** the expected key set is nonempty and every expected key is `record_present`; there are no rejected/DLQ/unknown/pending keys. It may still report new-versus-duplicate counts as unknown if B2 outcome receipts are absent.

`odp.record.committed` may trigger faster reconciliation only. Ingest acceptance, Redis XACK, Redis stream ID, and notification delivery never satisfy the boundary.

## N6/N2 — new Admin manifest/event schema and status migration

This proposal introduces a **new Admin schema**, not a claim about the current projection:

```text
EvidenceBatchMaterializationManifestV1
EvidenceBatchMaterializationEventV1
```

The manifest is immutable per reconciliation revision and contains:

```text
schema_version = 1
batch_id, derivation = "dispatch-task-v1", reconciliation_revision
workspace_id, project_id, workflow_id, studio_workflow_version_id, run_id, node_id
command_id, attempt_id, task_id, trace_id
source_binding_id, source_binding_revision_id
collector_final_expected_key_report_id, expected_record_key_set_hash
query_fingerprint, page_snapshot_as_of, redaction_profile_version
item_count
counts: expected, record_present, inserted, duplicate_existing, rejected, dlq, unknown
materialization_status
record_ref_pages: `{source_id,event_id,odp_record_id,committed_at}` only
retention_state, finalization_reason, finalized_at or reconciliation_deadline
```

The new manifest status is versioned and is deliberately **not** the existing `WorkflowRunStatus` enum:

```text
awaiting_final_report | reconciling | completed | completed_empty |
partial | failed_definitive | indeterminate
```

`EvidenceBatchSummary.status` currently accepts only `queued`, `running`, `partial`, `partial_success`, `waiting`, `blocked`, `completed`, and `failed`. The migration therefore MUST preserve its existing contract and project the new status explicitly:

| Manifest status | Existing summary status | Required additional field |
| --- | --- | --- |
| `awaiting_final_report`, `reconciling` | `running` | `materializationStatus` |
| `completed`, `completed_empty` | `completed` | `materializationStatus` |
| `partial` | `partial` | `materializationStatus` and durable outcome counts |
| `failed_definitive` | `failed` | `materializationStatus` and reason |
| `indeterminate` | `blocked` | `materializationStatus`, unresolved reason, and retry/reconciliation guidance |

Migration sequence: add the new versioned manifest/event and projection reader; dual-write the old summary projection plus its defined mapping; make Studio and ResearchGraph consume `materializationStatus`/manifest version when present; retain legacy summaries without a manifest as legacy/unmaterialized rather than backfilling invented ODP facts. No producer may emit `materializing` or `indeterminate` into the existing `WorkflowRunStatus` field.

For this vertical, choose the dispatch/task UUID5 branch:

```text
batch_id = UUID5(NAMESPACE_URL,
  "opencli-admin/workflow/{workflow_id}/run/{run_id}/batch/{task_id}")
```

The node-derived branch remains separate and MUST NOT claim ODP-backed attempt materialization. A retry creates a new attempt/task and batch; transport redelivery for the same attempt reuses the batch ID and appends only idempotent event revisions.

`item_count` is the B1 collector report count. `record_count` in the existing summary maps only to `record_present_count`; it MUST NOT imply accepted, inserted, or duplicate counts. `odpRef` remains opaque display/provenance data, never a resolver or access-control token.

## Run-event, Studio, and ResearchGraph consumption

1. **Start:** after a B1 report is stored, append `EvidenceBatchMaterializationEventV1` with `reconciling`, batch derivation, report/hash references, and no raw ODP JSONB.
2. **Finalize:** append a new reconciliation revision only after B1–B3 reconciliation yields the versioned terminal materialization status. Its idempotency identity is `(command_id, attempt_id, query_fingerprint, reconciliation_revision)`.
3. **Late facts:** a late expected record or durable DLQ receipt appends a later revision for the same batch/attempt after scope/hash validation. Unexpected attempt-page records are diagnostic/unmatched, never silently added.
4. **Studio:** existing scoped endpoints remain presentation seams but must read the new Admin materialization projection. A new versioned detail/records representation exposes the manifest status and sanitized pages; no browser talks to `odp-query`.
5. **ResearchGraph:** create source→evidence→claim lineage only from a terminal materialization manifest with `record_present` references and no `unknown` expected keys. Graph IDs stay independent of ODP event IDs.
6. **Replay:** replays immutable B1 report, manifest, and reconciliation events for the same attempt/query fingerprint. It may call a fresh bounded reconciliation only for a nonterminal manifest or appended late fact; it cannot reclassify new/duplicate without B2 evidence.

## Failure, retention, and anti-pattern rules

| Situation | Required treatment |
| --- | --- |
| Ingest `accepted` / `duplicates` / `rejected` response | A non-durable ingress observation only. Retain it for diagnostics, but do not use it to classify a record as inserted/duplicate/rejected or to complete a batch. |
| `odp.record.committed` received | Accelerate a later reconciliation; do not mark complete until B1 exact-key reconciliation and B2 outcome evidence allow it. |
| Redis XACK / stream ID | Transport diagnostic only; never record existence, completeness, page cursor, or watermark. |
| Query returns no rows | Never infer zero. `completed_empty` requires the B1 declared empty set and final exact reconciliation. |
| Exact query finds a row | Count it as `record_present`; do not count it as `inserted` or `duplicate_existing` without B2 durable outcome evidence. |
| Existing duplicate row from another attempt | It may satisfy `record_present` for an expected exact key, but its relation to this attempt remains unknown without a durable receipt/boundary. |
| Explicit ODP rejection | Count as rejected only from the new durable attempt outcome receipt or rigorous equivalent, never merely from timing or an undurable response. |
| DLQ row | Count as DLQ only after `odp-query` returns a matching durable result under a defined read/replay/retention contract. Otherwise it is `unknown`. |
| Later expected record / receipt | Append a reconciliation revision for the same batch/attempt after scope/hash validation. Never alter another attempt or fabricate a batch. |
| Record retention/deletion unknown | Preserve it as `unknown`; do not turn it into absent, zero, inserted, or duplicate. |
| ODP read service unavailable | Project manifest `indeterminate` to existing summary `blocked`; Studio exposes the reason and ResearchGraph/Delivery cannot consume it as final evidence. |
| Changed data during pagination | Freeze `as_of` on the first page and use the B3 predicate. Never advance the page snapshot from later pages. |
| Admin `/records` response | Never substitute for ODP evidence. It is a separate store. |
| Raw JSONB / raw_data | Redact server-side in `odp-query`; do not send generic raw payloads to Admin, Studio, or ResearchGraph. |

## Independent-audit checklist

Before adoption, an independent audit must confirm that the resulting specification:

- makes B0 an independent Rust `odp-query` read-only service; keeps `odp-store` as writer/schema owner and `odp-ingest` ingress-only; and forbids direct Admin ODP DB access;
- labels B1's collector final expected-key report and B2's durable outcome receipt/boundary as new prerequisites rather than observed capabilities;
- permits exact lookup to establish only record presence, never new-versus-duplicate classification from record order, timestamp, acceptance, or stream facts;
- uses B3's first-page fixed `as_of`, query fingerprint, and `(committed_at,id)` predicate, while keeping exact reconciliation independent of the page scan;
- defines machine trust plus delegated workspace/project/workflow/run/batch scope without requiring a signed grant, forbids arbitrary predicates, and enforces `odp-query` JSONB redaction;
- defines DLQ/replay/retention/deletion unknown states and does not turn unknown or no-row results into zero/success;
- introduces the new versioned Admin manifest/event schema and the explicit mapping/migration from materialization statuses to the existing `WorkflowRunStatus` enum;
- uses only terminal manifests with no unknown expected keys for Studio EvidenceBatch finality and ResearchGraph contribution, without making `odpRef` dereferenceable.

## Primary sources

1. [`odp-rs/crates/odp-ingest/src/handlers.rs`](../../../odp-rs/crates/odp-ingest/src/handlers.rs#L15-L123)
2. [`odp-rs/crates/odp-store/src/main.rs`](../../../odp-rs/crates/odp-store/src/main.rs#L1-L92)
3. [`odp-rs/crates/odp-store/src/writer.rs`](../../../odp-rs/crates/odp-store/src/writer.rs#L11-L182)
4. [`odp-rs/crates/odp-bus/src/redis_streams.rs`](../../../odp-rs/crates/odp-bus/src/redis_streams.rs#L94-L108)
5. [`backend/control/collectors/odp_metrics.py`](../../../backend/control/collectors/odp_metrics.py#L77-L83)
6. [`backend/api/v1/records.py`](../../../backend/api/v1/records.py#L20-L35)
7. [`backend/api/v1/studio_workflows.py`](../../../backend/api/v1/studio_workflows.py#L469-L585)
8. [`backend/workflow/evidence_projection.py`](../../../backend/workflow/evidence_projection.py#L1-L93)
9. [`backend/workflow/opencli_hda_tracer.py`](../../../backend/workflow/opencli_hda_tracer.py#L2259-L2311)
10. [`backend/workflow/research_graph_lineage.py`](../../../backend/workflow/research_graph_lineage.py#L19-L128)
11. [`backend/workflow/opencli_hda_tracer.py`](../../../backend/workflow/opencli_hda_tracer.py#L3192-L3242)
12. [`backend/schemas/workflow_runtime.py`](../../../backend/schemas/workflow_runtime.py#L40-L49)
13. [`backend/schemas/workflow_evidence.py`](../../../backend/schemas/workflow_evidence.py#L19-L70)
14. [`docs/PLAN_odp_enterprise.md`](../../PLAN_odp_enterprise.md#L60-L77)
