# Cross-Plane Correlation and Idempotency Facts

**Wayfinder research for [#23](https://github.com/1012839419a-alt/opencli-Razormind-gjx/issues/23)**  
**Status:** adopted Wayfinder decision; not implemented.

## Decision summary

The first non-bypass vertical MUST carry an explicit **cross-plane correlation envelope**. It may not infer identity from a collector return dictionary, reuse an ODP `event_id` as an Admin lifecycle event, or treat an `odp://` string as a record resolver.

The authoritative identities are deliberately separate:

- **Admin scope and operation:** workspace, project, workflow/version, run, node, command, and attempt.
- **III execution correlation:** task and trace values that are supplied by Admin; III must not allocate them for a governed Admin attempt.
- **ODP record identity:** `(source_id, event_id)` before persistence and `odp_records.id` after persistence.
- **Workflow projection identity:** EvidenceBatch and ResearchGraph IDs, each scoped to a Run and never promoted to an ODP record key.

The required join is a persisted, immutable **cross-plane correlation record** for each Admin attempt. The full record lives only in Admin's ledger and bridge lifecycle metadata; it binds the Admin command/attempt and scoped node/source-binding revision to the ODP task/trace/source identity and resulting record references. It is never copied wholesale into Record v2. This does not change the authority decision in #22: Admin owns operations and lifecycle, III owns execution observations, and ODP Postgres owns persisted records.

## Observed facts

| Fact | Primary evidence | Consequence |
| --- | --- | --- |
| The models supply UUID4 defaults in application code; the database columns do not enforce UUID format. Workspace/Project/Workflow/Version have relational parent scopes and natural uniqueness constraints; Studio workflow versions are unique by `(workflow_id, version)`. | [`backend/models/base.py:14-33`](../../../backend/models/base.py); [`backend/models/identity.py:26-41`](../../../backend/models/identity.py); [`backend/models/workflow.py:7-58`](../../../backend/models/workflow.py); [`backend/models/studio.py:56-114`](../../../backend/models/studio.py). | The protocol cannot rely on a database UUID-format constraint. A numeric version or slug alone is not a cross-plane identity. |
| A scoped Studio run derives UUID5 only when a caller supplies an idempotency key. Its namespace contains workspace, project, workflow, published version ID, and that key. The key is not separately persisted as a column; `requestId` is a distinct request correlation field. Without an idempotency key, the runtime allocates UUID4. A Run persists version reference, trace ID, request/projection JSON, and a monotonic event sequence. | [`backend/api/v1/studio_workflows.py:324-403`](../../../backend/api/v1/studio_workflows.py); [`backend/workflow/opencli_hda_tracer.py:299-313`](../../../backend/workflow/opencli_hda_tracer.py); [`backend/models/workflow_run.py:7-43`](../../../backend/models/workflow_run.py). | A retry must retain the resolved run ID. The client idempotency key and request ID are distinct inputs and neither is an independently persisted Run key today. |
| Workflow run events have both unique `(run_id, sequence)` and globally unique `event_id`. | [`backend/models/workflow_run.py:46-71`](../../../backend/models/workflow_run.py). | ODP `event_id` cannot be reused as a lifecycle `event_id`; it would collide across the two unrelated models and loses attempt semantics. |
| SourceBinding is Project-owned; semantic revisions are immutable and pin a SourceRevision. HDA envelopes currently copy binding/revision IDs and revision number as opaque fields. | [`backend/models/source_binding.py:1-118`](../../../backend/models/source_binding.py); [`backend/api/v1/project_source_bindings.py:103-137,196-226`](../../../backend/api/v1/project_source_bindings.py); [`backend/workflow/opencli_hda_tracer.py:4761-4812`](../../../backend/workflow/opencli_hda_tracer.py). | A binding revision is Admin authorization/configuration context, not the external/ODP source identity. |
| The OpenCLI III collector defaults omitted `task_id` and `trace_id` to random UUID4 values. Its ODP source ID defaults to UUID5 of the raw `site.strip()` and `command.strip()` values in `opencli-admin/opencli/{site}/{command}`, unless explicitly supplied. No source-string canonicalization policy is defined. | [`iii/workers/collector-opencli/src/main.py:35-72`](../../../iii/workers/collector-opencli/src/main.py); [`iii/lib/odp_record.py:32-36,159-184`](../../../iii/lib/odp_record.py). | Retries drift if Admin omits task/trace. This is blocker B2. Case, aliases, or other transformations of site/command must not be treated as equivalent source inputs without a later canonicalization decision. |
| Record v2 uses UUID `source_id`, string `event_id`, optional UUID `trace_id`/`task_id`; its store idempotency key is exactly `(source_id, event_id)`. OpenCLI event IDs use upstream `id`/`msg_id`/`eid`/`url`/`link`, otherwise `json.dumps(item, sort_keys=True, ensure_ascii=False)` hashed as bytes. | [`odp-rs/crates/odp-contracts/src/lib.rs:16-85`](../../../odp-rs/crates/odp-contracts/src/lib.rs); [`iii/lib/odp_record.py:148-184`](../../../iii/lib/odp_record.py). | The fallback is stable only for the same canonicalized JSON bytes and values; it is not semantic identity normalization. Admin command, attempt, task, trace, and ODP record id are not interchangeable with `event_id`. |
| ODP Postgres assigns `odp_records.id BIGSERIAL`, retains source/event/task/trace columns, and enforces unique `(source_id,event_id)`. The current migration adds only a source/time index; it has no task/trace index and Admin has no records query API for ODP rows. | [`odp-rs/crates/odp-store/src/writer.rs:11-40`](../../../odp-rs/crates/odp-store/src/writer.rs); [`backend/api/v1/records.py:20-35`](../../../backend/api/v1/records.py). | Task/trace are columns and future query candidates, not an existing indexed/public read contract. #24 must define its query and index plan. B1 remains blocking. |
| EvidenceBatch is a Run projection with **two UUID5 construction branches**: dispatch batches namespace workflow/run/task ID, while node batches namespace workflow/run/node ID. The projection deduplicates only within its response. Summary ID is UUID5 of `(runId, sourceGroup)` and artifact ID is `evidence-batch:{batchId}`. | [`backend/workflow/opencli_hda_tracer.py:2259-2311`](../../../backend/workflow/opencli_hda_tracer.py); [`backend/workflow/evidence_projection.py:130-165,190-220`](../../../backend/workflow/evidence_projection.py). | The branches are not one formal global batch identity contract. No database/global uniqueness or ODP linkage makes `batchId`, `summaryId`, or `artifactId` an ODP record identity. |
| `odpRef` is merely an optional string assembled as `odp://workflow-runs/...`; the current repository has no ODP resolver/parser and no Admin-accessible ODP-record endpoint. | [`backend/workflow/opencli_hda_tracer.py:2270-2280,2300-2310`](../../../backend/workflow/opencli_hda_tracer.py); existing public run/Studio routes only expose Workflow EvidenceBatch projections, e.g. [`backend/api/v1/studio_workflows.py:470-593`](../../../backend/api/v1/studio_workflows.py). | Existing `odpRef` cannot be dereferenced or used as authorization evidence. This is blocker B1. |
| ResearchGraph is folded from Admin WorkflowRunEvents. Its envelopes bind run/trace/node and force `idempotencyKey == eventId`; mutation replay detects cross-run and different-payload conflicts. Automatic lineage emits IDs such as `source:{source_id}`, `evidence:{evidence_id}`, `claim:{claim_id}`, with a run-prefixed graph event ID. | [`backend/schemas/research_graph.py:26-101`](../../../backend/schemas/research_graph.py); [`backend/workflow/research_graph.py:27-168,236-316`](../../../backend/workflow/research_graph.py); [`backend/workflow/research_graph_lineage.py:55-167`](../../../backend/workflow/research_graph_lineage.py); [`backend/workflow/research_graph_mutations.py:69-138`](../../../backend/workflow/research_graph_mutations.py). | ResearchGraph already has internal transcript idempotency, but no mapping to ODP records. Its entity IDs are Run-scoped projection identifiers, not ODP primary keys. |

## Scope and identity decision

The target vertical is the scoped Studio path. The following table defines every identity's generation authority, stable boundary, use, and retry/replay rule.

| Identity | Generation authority and canonical form | Stable boundary | Required index/query boundary | Retry, late event, replay, conflict rule |
| --- | --- | --- | --- | --- |
| **Workspace** | Current model default is Admin UUID4; no database UUID-format constraint. | Global Admin scope; slug is only a natural label. | Resolve first from authenticated workspace route/context. | Never inferred from III/ODP data. Scope mismatch rejects a bridge lifecycle observation or query result. |
| **Project** | Current model default is Admin UUID4; parent workspace is relationally enforced. | One workspace; `(workspace_id, slug)` natural uniqueness. | Query under `(workspace_id, project_id)`. | Never encoded into ODP source/event identity. A mismatched workspace is a conflict. |
| **Workflow** | Current Studio model default is Admin UUID4. | One project; name only unique within project. | Query under `(workspace_id, project_id, workflow_id)`. | A Run records this exact workflow UUID; do not resolve by name during replay. |
| **Workflow version** | Current `StudioWorkflowVersion.id` model default is UUID4, plus immutable `(workflow_id, version)`. | Published graph snapshot; integer version is scoped, not global. | Run stores `studio_workflow_version_id`; query validates both parent workflow and published version. | Retries retain the exact version UUID and graph snapshot. A same integer under a different workflow is a conflict. |
| **Run** | UUID5 only for a supplied caller idempotency key under namespace `workspace:project:workflow:version:key`; otherwise current runtime allocates UUID4. The key is not separately stored; `requestId` is separate. | One immutable execution of one resolved version. | `workflow_runs.id`; verify `workflow_id` and stored Studio version. | Same supplied key returns the same compatible Run; version/workflow mismatch is a conflict. Never create a new Run for a transmission retry. |
| **Node** | Node ID inside the immutable version graph. | Only stable with `(studio_workflow_version_id, node_id)`; package/internal IDs are additionally qualified by their package node. | Read from the stored version graph; event rows retain node ID. | No cross-version inference. Unknown node or parent/package mismatch rejects an event. |
| **Command** | Proposed #22 Admin UUID4 `command_id`, persisted with immutable payload hash. | One desired collection operation within a Run/node/version/source-binding revision. | Future Admin operation table/ledger; no III or ODP substitute. | Never mutate; transport redelivery retains the same command. Payload-hash mismatch is a conflict. |
| **Attempt** | Proposed #22 Admin UUID4 `attempt_id`, allocated under one command. | One admitted/executed try of a command. | Future Admin attempt ledger keyed by `(command_id, attempt_number)` and `attempt_id`. | Pre-acceptance re-send is the same attempt. A post-acceptance retry is a new attempt. Existing Admin TaskRun/async-adapter identities MUST NOT be presented as an III attempt. |
| **Task** | Admin allocates a UUID `task_id` for each attempt and includes it in the immutable III command. | One ODP correlation cohort for one attempt. | #24 must add an authenticated query and task/trace index plan; current ODP migration has neither. | Collector MUST receive it; it must never fall back to random generation. Late data is attributed to its original attempt. |
| **Trace** | Admin Run allocates/retains one UUID `trace_id`; each command/attempt copies it. | One Run trace across attempts. | Existing Run/event columns plus #24's ODP query/index plan. | It is correlation only, never idempotency. Do not regenerate on retry. |
| **Source** | Current ODP `source_id` is the collector's explicit UUID or UUID5 of raw trimmed `(site, command)`; canonicalization is not defined. | External data origin, potentially shared by many Admin bindings/projects. | ODP record key/query side; UUID only. | Store the actual accepted source UUID in the Admin correlation ledger; do not recompute it from transformed strings on retry. |
| **Source binding** | Admin `SourceBinding.id` model-default UUID4 and immutable `SourceBindingRevision.id`/revision number. | Project authorization/configuration; it pins SourceRevision and scope. | Admin ledger and scoped source-binding routes only; never Record v2. | Carry binding/revision only in Admin command and bridge lifecycle metadata. Many bindings may map to one ODP source. Revision mismatch is a conflict, not a new ODP source. |
| **Record `(source_id,event_id)`** | Collector maps upstream stable item identifier; fallback is stable only for exact canonical JSON bytes/value equality. | ODP idempotency fact before/after persistence. | Unique ODP store key. | Repeating it is a duplicate Record, not a duplicate lifecycle event. Never use `event_id` as Admin/ResearchGraph event ID. |
| **ODP record** | ODP store assigns `odp_records.id BIGSERIAL` only after persistence. Canonical portable reference remains `(source_id,event_id)`. | ODP data plane. | **B1/N5:** no current Admin query/resolver and no task/trace index. #24 must define authenticated query, indexes, and returned record references. | Late/pending/DLQ rows stay ODP facts. Admin reconciles them; it does not invent a record ID from ingest acceptance. |
| **EvidenceBatch** | Current projection uses two deterministic UUID5 branches: workflow/run/task for dispatch and workflow/run/node for node batches. Formal scope is `(run_id, batch_id)` plus its derivation branch. | Workflow Run projection, not ODP store. | Existing scoped Studio EvidenceBatch endpoints query the Run projection, not ODP. | Batch replay deduplicates in projection only. No global uniqueness/persistence assumption. Re-materialization must validate run/version/node/attempt correlation and derivation branch before reusing a batch ID. |
| **ResearchGraph entity/event** | Current entity IDs are graph semantic IDs (`source:*`, `evidence:*`, `claim:*`, `relation:*`); automatic event ID is `run_id:research-graph:{entity_id}` and manual mutation uses its own idempotency key. | One Run transcript, including `(run_id, entity_id)` and event `(run_id, sequence, event_id)`. | Existing graph API folds Admin run events only. | Existing replay rules stay: idempotency-key collision with a different mutation is conflict. Future ODP-backed evidence carries concrete ODP record reference in Admin metadata/graph attributes; it never substitutes ODP `event_id` for graph event identity. |

## Correlation ledger and bridge metadata

The full cross-plane correlation envelope is stored in **Admin's durable operation/attempt ledger** and is carried by authenticated **bridge lifecycle metadata**. It is not an ODP Record v2 payload.

```text
workspace_id
project_id
workflow_id
studio_workflow_version_id
run_id
node_id
command_id
attempt_id
attempt_number
task_id
trace_id
source_binding_id
source_binding_revision_id
source_binding_revision_number
odp_source_id
collector_function_id
payload_sha256
```

For Record v2, the approved cross-plane correlation fields are **only**:

```text
source_id
event_id
task_id
trace_id
```

`workspace_id`, `project_id`, workflow/version/node identifiers, command/attempt identities, source-binding data, collector arguments, credentials, Chrome endpoint, request headers, or the full command payload MUST NOT be copied into Record v2 `payload` or `raw_data` as correlation metadata. `raw_data` may retain only the minimum original collector item required by the approved data contract; redaction rules, credential/header exclusion enforcement, and maximum field/record sizes are specification work, not a hidden inference from this research.

A persisted ODP reconciliation result adds zero or more record references to the Admin ledger:

```text
source_id
event_id
odp_record_id        # only after Postgres persistence
odp_stream_id        # optional transport diagnostic, never identity
committed_at
```

This resolves the III UUID5 source / Workflow source ID ambiguity without inventing source canonicalization:

1. `odp_source_id` is the UUID actually supplied to or returned by the collector; current fallback generation uses raw trimmed `site` and `command` strings.
2. A retry reuses the stored accepted source UUID and does not recompute it from case-folded, aliased, translated, or otherwise transformed input.
3. `source_binding_id` and its immutable revision describe Project-scoped authorization/configuration only and remain in Admin/bridge metadata.
4. The correlation ledger records their relationship explicitly; neither identifier aliases the other.
5. A future graph source entity may use a prefixed ODP source reference (for example `source:odp:{odp_source_id}`) while retaining binding revision in its Admin-backed lineage. It must not use a bare, ambiguous Workflow `sourceId` value.

## Formal `odp://` decision

`odp://` is formally an **opaque provenance key, not a dereferenceable URI** in the current product.

- The currently emitted `odp://workflow-runs/{run}/nodes/{node}/sources/{group}/batches/{batch}` identifies only the Admin workflow projection that minted the EvidenceBatch reference.
- There is no resolver/parser, no Admin-accessible ODP record query endpoint, and no authorization contract that turns the string into ODP data access.
- UI, ResearchGraph, and Delivery logic MUST NOT fetch, authorize, or infer record existence from `odp://`.
- The only record-level provenance keys valid before a future resolver are `(source_id,event_id)` and, after ODP persistence, `odp_record_id`; they are recorded in the Admin correlation ledger, not inferred from or embedded as a full envelope in Record v2.

A future read API may expose a dereferenceable, authenticated resource, but it must receive a new explicitly versioned API identifier. It MUST NOT retroactively declare arbitrary historical `odp://` strings resolvable. Endpoint, authorization, query shape, retention, and EvidenceBatch materialization belong to #24 after blocker B1 is resolved.

## Idempotency, retry, late-event, and conflict rules

1. **Transport retry:** re-send the same immutable command and attempt only before `bridge_accepted`; preserve run, command, attempt, task, trace, binding revision, source UUID, and payload hash.
2. **Execution retry:** after acceptance but missing/failed terminal processing, allocate a new Admin attempt and task UUID; retain the parent command, run, trace, workflow version, node, binding revision, and ODP source UUID.
3. **Record deduplication:** ODP resolves duplicated collected items solely at `(source_id,event_id)`. This protects data rows, not Admin attempt/event deduplication.
4. **Lifecycle deduplication:** Admin accepts bridge lifecycle events only under `(command_id, attempt_id, sequence)` plus matching command payload hash. It never accepts ODP `event_id` as its lifecycle event ID.
5. **Late event:** a late collector/ODP fact retains its original attempt/task identity. It may enrich reconciliation but cannot change a newer attempt's state or create a new Run.
6. **Replay:** reconstruct Studio/EvidenceBatch/ResearchGraph from the persisted Admin Run event transcript and immutable correlation records. Replay cannot resolve an ODP record by `odpRef`; it uses the defined ODP record key/query once B1 is closed.
7. **Conflict:** reject any callback or query result whose Admin scope, workflow version, node/package location, binding revision, source UUID, payload hash, or attempt identity differs from the stored envelope. Preserve it as diagnosable unmatched evidence rather than silently rebinding it.

## Blocking findings

### B1 — No Admin-accessible ODP record query or `odp://` resolver

**Observed:** ODP records exist in ODP Postgres, but Admin exposes no primary-source-backed endpoint/auth/query contract for those rows. Current evidence endpoints read WorkflowRun projections; `odpRef` is optional display/provenance text only.

**Blocks:** #24 cannot specify evidence materialization, authoritative record counts, empty-result proof, query-based reconciliation, or a real record-level EvidenceBatch manifest. #25 and #26 cannot rely on record-level provenance until #24 defines the bounded read contract.

**Required decision boundary:** authenticated read authority, query keys and indexes, pagination/watermark, retention/deletion semantics, treatment of missing/DLQ/late rows, and a response that returns concrete `(source_id,event_id,odp_record_id)` references.

### B2 — Task/attempt correlation drifts when collector defaults IDs

**Observed:** the collector generates random task/trace UUIDs when omitted; current Admin, III, ODP, and workflow projection lifecycles have no existing cross-plane attempt mapping.

**Blocks:** a durable Admin→III handoff cannot distinguish retransmission, retry, late result, and duplicate collector execution. EvidenceBatch and ResearchGraph cannot attribute an ODP record to the correct command/attempt.

**Required decision boundary:** Admin-generated command/attempt/task/trace fields are mandatory on every III command; bridge lifecycle metadata validates the complete correlation envelope, while Record v2 carries only approved `source_id`, `event_id`, `task_id`, and `trace_id`. No legacy Admin TaskRun/async-adapter ID is an III attempt alias.

## Rejected identity shortcuts

| Shortcut | Why rejected |
| --- | --- |
| Use `task_id` as Admin command or attempt ID | It is an optional ODP correlation field and currently defaults randomly in the collector. It has no Admin lifecycle or retry meaning. |
| Use `trace_id` as idempotency | One Run may have several attempts; trace is cross-attempt correlation, not a unique operation key. |
| Use `sourceBinding.id` as Record `source_id` | Binding is Project-scoped authorization; ODP source is a UUID identity for the external source and may be shared by bindings. |
| Use ODP `event_id` as WorkflowRunEvent/ResearchGraph event ID | ODP uniqueness is only paired with source; Admin graph/lifecycle events use different global and run-sequence constraints. |
| Treat `batchId`, `summaryId`, or `artifactId` as global ODP identifiers | They are deterministic Run projections without ODP store persistence or a cross-plane uniqueness contract. |
| Treat `odp://` as a URL | There is no resolver/parser/authenticated record API. Calling it a URI would promise a capability the product does not have. |
| Reuse existing Admin TaskRun/async-adapter identity as III attempt identity | The current systems are separate; doing so would fabricate a mapping rather than record one. |

## Independent-audit checklist

Before adoption, an independent audit must confirm that the eventual specification:

- keeps the full correlation envelope in Admin ledger/bridge lifecycle metadata, limits Record v2 correlation to `source_id`, `event_id`, `task_id`, and `trace_id`, and specifies redaction/header-credential exclusion and size bounds before implementation;
- preserves the authority split adopted in #22 and the distinct idempotency domains defined here;
- leaves `odp://` opaque until #24 delivers an explicit authenticated read contract;
- enforces B2 by prohibiting collector-side default task/trace generation for a governed Admin attempt;
- maps one or more persisted ODP record keys to an EvidenceBatch without declaring projection IDs to be ODP identities;
- rejects scope/version/node/binding/payload/attempt conflicts and keeps late/unmatched evidence diagnosable;
- includes B1 and B2 as hard preconditions for implementation planning.

## Primary sources

1. [`backend/models/base.py`](../../../backend/models/base.py#L14-L33)
2. [`backend/models/identity.py`](../../../backend/models/identity.py#L26-L41)
3. [`backend/models/workflow.py`](../../../backend/models/workflow.py#L7-L58)
4. [`backend/models/studio.py`](../../../backend/models/studio.py#L56-L114)
5. [`backend/models/workflow_run.py`](../../../backend/models/workflow_run.py#L7-L71)
6. [`backend/api/v1/studio_workflows.py`](../../../backend/api/v1/studio_workflows.py#L324-L403)
7. [`backend/models/source_binding.py`](../../../backend/models/source_binding.py#L1-L118)
8. [`backend/workflow/opencli_hda_tracer.py`](../../../backend/workflow/opencli_hda_tracer.py#L2259-L2311)
9. [`backend/workflow/evidence_projection.py`](../../../backend/workflow/evidence_projection.py#L130-L165)
10. [`backend/schemas/research_graph.py`](../../../backend/schemas/research_graph.py#L26-L101)
11. [`backend/workflow/research_graph.py`](../../../backend/workflow/research_graph.py#L27-L168)
12. [`backend/workflow/research_graph_mutations.py`](../../../backend/workflow/research_graph_mutations.py#L69-L138)
13. [`iii/workers/collector-opencli/src/main.py`](../../../iii/workers/collector-opencli/src/main.py#L35-L72)
14. [`iii/lib/odp_record.py`](../../../iii/lib/odp_record.py#L25-L37)
15. [`odp-rs/crates/odp-contracts/src/lib.rs`](../../../odp-rs/crates/odp-contracts/src/lib.rs#L16-L85)
16. [`odp-rs/crates/odp-store/src/writer.rs`](../../../odp-rs/crates/odp-store/src/writer.rs#L11-L40)
17. [`backend/api/v1/records.py`](../../../backend/api/v1/records.py#L20-L35)
