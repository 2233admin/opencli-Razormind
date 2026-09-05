# ResearchGraph Delivery Authority

**Wayfinder research for #25**  
**Status:** **adopted Wayfinder decision; not implemented.**

## Decision

ResearchGraph remains a deterministic, **non-authoritative** projection of the
append-only `WorkflowRunEvent` transcript. It is necessary evidence for a
research delivery decision, but it is never the authority that materializes
ODP evidence, authorizes an actor, decides a Delivery, or performs an external
write. Admin owns the proposed, durable `DeliveryAuthorizationDecisionV1`; a
Delivery executor may act only on that decision and its frozen inputs.

This proposal extends—not replaces—the adopted #24 materialization boundary:
only terminal EvidenceBatch materialization manifests with no unknown expected
keys are final research input. `odpRef`, a graph entity's `authoritative` flag,
or a successful `research.publish-gate` metric alone MUST NOT authorize a
Delivery.

> **Proposal, not current behavior.** Every MUST in “Proposed contracts” is a
> required implementation contract. It does not describe an existing endpoint,
> schema, permission, manifest, or Delivery behavior.

## Observed facts

| Observed fact | Primary source | Consequence for this decision |
| --- | --- | --- |
| ResearchGraph folds persisted run events in sequence order into a deterministic, explicitly non-authoritative projection. It validates run/trace context and duplicate sequence numbers while folding. | [`backend/workflow/research_graph.py:27-79`](../../../backend/workflow/research_graph.py#L27-L79) | Graph output is a read model over the run transcript, not an authority store. |
| `record` and `propose` create entities; `verify`/`reject` resolve only a proposed target; `retract` may retract a proposed or verified target. Projection `authoritative` is `true` for `record` and becomes `true` when a proposal is verified; retraction recursively downgrades dependents. | [`backend/workflow/research_graph.py:94-139`](../../../backend/workflow/research_graph.py#L94-L139); [`backend/schemas/research_graph.py:9-23,139-140`](../../../backend/schemas/research_graph.py#L139-L140) | The projection boolean is a reducer result, not a Delivery approval, and a later state transition can invalidate a graph assertion. |
| Automatic lineage emits source → evidence → claim rows only when every claim evidence reference has `sourceId`, `evidenceId`, `itemKey`, `batchId`, `runId`, `nodeId`, and `manifestUri`; evidence currently carries an optional opaque `odpRef`. | [`backend/workflow/research_graph_lineage.py:19-128`](../../../backend/workflow/research_graph_lineage.py#L19-L128) | Current completeness is syntactic provenance only. It does not prove that the referenced manifest is terminal, final, or unchanged. |
| Current mutation requests carry an idempotency key, expected revision, expected sequence, action, trace/node, lineage, and entity/target shape. The shared appender locks the per-run allocator and preserves a same-key/same-content retry, but neither route derives an actor/principal/role. | [`backend/schemas/research_graph.py:104-128`](../../../backend/schemas/research_graph.py#L104-L128); [`backend/workflow/research_graph_mutations.py:69-147`](../../../backend/workflow/research_graph_mutations.py#L69-L147); [`backend/api/v1/research_graph_routes.py:28-90`](../../../backend/api/v1/research_graph_routes.py#L28-L90) | Current mutation idempotency and sequencing are useful seams, but do not provide authorization or a transactional authority decision. |
| `expectedSequence` and `expectedRevision` are request/allocator inputs, while the fold simply updates `currentRevision` as it encounters an envelope carrying `revisionId`; it does not define monotonic revision CAS or bind a final-manifest identity. | [`backend/schemas/research_graph.py:104-128`](../../../backend/schemas/research_graph.py#L104-L128); [`backend/workflow/research_graph.py:44-95`](../../../backend/workflow/research_graph.py#L44-L95) | A Delivery decision cannot infer a revision CAS or final evidence identity from the current fold alone; the required current-sequence/revision CAS must be made transactional at append time. |
| The shared WorkflowRun event writer is append-only, allocates sequences itself under the run allocator, and validates plugin events before it reserves/appends a suffix. | [`backend/workflow/workflow_run_events.py:53-172`](../../../backend/workflow/workflow_run_events.py#L53-L172) | The proposed CAS and authority record must be bound inside this same Admin transaction/locking boundary. |
| `research.publish-gate` currently reads normalized claims, coverage reports, revisions, counters, and scenarios. It admits only verified claims with evidence references, final satisfied coverage whose claim-set hash matches, evidence-backed counter/scenario, and a matching revision hash; failure returns no items and reason metrics. It does not read ResearchGraph or EvidenceBatch manifests. | [`backend/workflow/research_operators.py:506-591`](../../../backend/workflow/research_operators.py#L506-L591) | `publishAllowed` is a valuable content gate, but is advisory for Delivery until the proposed authority inputs exist. |
| The research ledger currently maps the latest `publishAllowed == true` to `researchStatus = final` from event metrics, coverage, and revision, then copies evidence refs; it does not inspect ResearchGraph state or EvidenceBatch materialization finality. | [`backend/workflow/research_continuation.py:331-400`](../../../backend/workflow/research_continuation.py#L331-L400) | Existing `final` presentation remains compatible, but it MUST NOT be read as an authorization to deliver. |
| The current webhook payload has only schema, workflow/run/node identity, target, item count, and sanitized items. | [`backend/workflow/webhook_delivery.py:13-47`](../../../backend/workflow/webhook_delivery.py#L13-L47) | There is no current frozen research revision or EvidenceBatch identity in a Delivery payload. |
| #24 defines immutable manifests per reconciliation revision and requires ResearchGraph contribution only from a terminal manifest with `record_present` references and no unknown expected keys. It also says replays preserve immutable manifest/reconciliation events. | [`docs/wayfinder/iii-vertical/define-odp-to-evidencebatch-materialization.md:138-201`](define-odp-to-evidencebatch-materialization.md#L138-L201) | This proposal consumes #24 finality; it does not relax, duplicate, or materialize it. |

## Proposed contracts

### 1. EvidenceBatch eligibility and graph visibility

**New contract — `FinalResearchEvidenceInputV1`.** A ResearchGraph contributor
MUST accept an evidence reference only after Admin resolves it to one immutable
#24 `EvidenceBatchMaterializationManifestV1` reconciliation revision whose
`unknown` expected-key count is zero and whose status is terminal:

| Terminal materialization result | May create source/evidence graph entities | May make a claim visible to the delivery candidate |
| --- | --- | --- |
| `completed` | Yes, for each explicitly listed `record_present` reference. | Yes, only when **every** evidence reference of that claim resolves to an eligible final manifest. |
| `partial` | Yes, but only for its explicitly listed `record_present` references. Rejected/DLQ entries remain manifest audit outcomes; they are not evidence entities. | Yes only when the candidate revision explicitly excludes every rejected/DLQ item and every delivered claim's complete reference set is `record_present`. It may not silently omit a rejected/DLQ/absent expected item. |
| `completed_empty` | No source, evidence, or claim entity is created from the empty batch. | No. A zero-item finalization is not evidence. |
| `failed_definitive` | No source, evidence, or claim entity is created. | No. |
| `awaiting_final_report`, `reconciling`, or `indeterminate` (including any unknown/pending expected key) | No. | No. |

A visible source is therefore created only as the parent of an eligible
`record_present` evidence entity; it is not an assertion that the source as a
whole is complete. An evidence entity MUST include the stable source ID,
evidence ID/item key, batch ID, run/node scope, manifest identity, and record
reference identity. A claim is visible only after all of its evidence entities
are visible and are active. Graph IDs remain independent of ODP IDs, and
`odpRef` remains display/provenance data only—never a resolver, access token,
or substitute for a manifest record reference.

**New data requirement.** `manifestUri` alone is insufficient because a URI
can name a later reconciliation revision. Each eligible evidence reference
MUST carry `manifestSchemaVersion`, `batchId`, `derivation`,
`reconciliationRevision`, and an immutable `manifestHash` (plus its referenced
record identity). Admin verifies this tuple against its scoped materialization
projection before the contributor appends a graph event.

### 2. Claim state and publication gate

The existing `research.publish-gate` v1 contract remains unchanged for its
current normalized inputs and metrics. It continues to return its input items
when its current checks pass, and an empty item list with `gateReasons` when
they do not. It MUST NOT be silently redefined to dereference ODP, infer graph
state, or treat a missing final-manifest implementation as passing.

**New contract — delivery authority overlay.** Admin evaluates the following
additional state gate against the same frozen revision after v1 produces
`publishAllowed = true`:

| State of a claim included in the candidate revision | Proposed gate outcome |
| --- | --- |
| `proposed` | Block the whole candidate with `proposed_claims`; `publishAllowed` remains an advisory v1 metric only. |
| `verified` | Eligible only if the claim has the complete eligible evidence set above and all existing v1 coverage/revision checks still match. Verification alone never delivers. |
| `rejected` | Block with `rejected_claims` if retained in the candidate revision; a later revision may omit it. It MUST NOT enter a Delivery payload. |
| `retracted` | Block with `retracted_claims` if retained in the candidate revision; it and recursively retracted dependent assertions MUST NOT enter a Delivery payload. |

The overlay also fails closed for an unknown graph entity, a missing/deactivated
source or evidence reference, a graph revision/sequence mismatch, a
non-terminal or unknown-key manifest, a manifest tuple mismatch, or changed
coverage/claim/scenario hashes. Its result is a new durable authority decision,
not a new interpretation of v1's `publishAllowed` field. Existing
continuation/ledger views may continue to show their current `final` state for
compatibility, but MUST label it as **not delivery-authorized** until this
contract is implemented.

### 3. Mutation actor, authorization, and optimistic concurrency

**New contract — `ResearchGraphMutationAuthorizationV1`.** Every human or
service mutation appends an immutable authorization envelope containing:
`actorType`, immutable `actorId`, authenticated principal/session or workload
identity, evaluated workspace/project/workflow/run scope, granted action
capability, policy version, authorization time, and request id. The envelope
is audit data; it is never taken from client-controlled graph attributes.

* A workflow contributor may append only machine `record` lineage facts for its
  own run/node and only after final-manifest validation.
* A scoped principal with `research.graph.propose` may propose within the
  project/run scope.
* `verify`, `reject`, and `retract` require the corresponding separately
  evaluated scoped capability. `verify` additionally requires an approved
  reviewer policy and MUST record the reviewer identity; a verifier MUST NOT
  satisfy a policy requiring independent review with the same identity that
  proposed the assertion.
* No unscoped endpoint, graph attribute, `authoritative = true` projection
  field, or client-supplied actor string grants any of these capabilities.

For a new mutation, Admin MUST, under the locked run transaction, load the
actual tail sequence and folded graph revision; compare both to
`expectedSequence` and `expectedRevision`; validate actor authorization and
final-manifest eligibility; append exactly one event; and persist the
authorization envelope with the append. A mismatch returns a conflict and
appends nothing. A retry with the same idempotency key is accepted only when
canonical mutation content **and authorization binding** are identical; a key
reused with changed content or authority is a conflict. This is the required
transactional CAS, rather than a client-side fold followed by a later append.

### 4. Append-only replay and revision evolution

**New contract — schema-versioned reducers.** Existing v1 graph events replay
with their present semantics; they are not retroactively claimed to contain an
actor, manifest hash, or Delivery authority. New authorized events use a new
versioned envelope/reducer that preserves the v1 graph shape while retaining
its authorization and final-manifest binding. Replay MUST use the event's
recorded schema and immutable inputs; it MUST NOT call ODP, recompute
materialization, or reinterpret a historical event using a later manifest.

Every proposal, verification, rejection, retraction, or final-manifest change
that affects a delivery candidate creates a later append-only event and, when
claim membership/content changes, a new immutable research revision with new
claim-set/scenario-set hashes. Revisions MUST NOT be rewritten in place or
made implicitly current by an arbitrary replay order. A later materialization
revision can make a new candidate eligible only through a new graph append and
new research revision; it cannot alter an already frozen Delivery decision.

A not-yet-submitted authority decision is revoked when a later authorized
mutation or final-manifest revision changes its pinned graph/revision/manifest
tuple. A Delivery that has already attempted an external write remains an
immutable historical attempt; any new external action requires a distinct
Delivery operation and fresh authority decision.

### 5. Delivery authority and frozen payload identity

**New contract — Admin `DeliveryAuthorizationDecisionV1`.** This decision is
only a durable **side-effect authorization**: it authorizes one immutable
Delivery operation to be attempted. It is not an Execution Result, an external
receipt, a Business Outcome, or proof that the destination accepted work.
After v1 `research.publish-gate` succeeds and the overlay passes, Admin
persists one scoped, idempotent decision before invoking a Delivery executor.
The executor accepts only this frozen decision and MUST NOT accept a graph
boolean, a bare `publishAllowed`, or an unpinned revision as authority.

The decision MUST freeze or reference these immutable inputs:

```text
schemaVersion, decisionId, decisionHash, decisionedAt
workspaceId, projectId, workflowId, workflowVersionId, runId, nodeId

deliveryOperationId                 # immutable operation, not a future generated ID
destinationBindingId, destinationBindingRevision
resolvedTarget, resolvedTargetScope # exact allowed destination, tenant/project/run scope
destinationPolicySnapshot, destinationPolicyVersion:
  timeout, retry policy, confirmation policy, unknown-result policy,
  continuation policy, compensation policy

runEventSequence                    # exact graph fold tail used
researchRevisionId
claimSetHash, scenarioSetHash, coverageReportHash
researchPublishGateVersion, publishGateInputHash
claims[]                            # sorted verified claim IDs/content hashes
manifestSet[]                       # sorted, complete evidence-manifest set:
  batchId, derivation, reconciliationRevision,
  manifestSchemaVersion, manifestHash,
  expectedRecordKeySetHash, recordRefSetHash, materializationStatus

sanitizedPayloadManifest:
  payloadSchemaVersion, payloadReference, payloadHash,
  sorted sanctioned item/reference hashes, redactionProfileVersion
approvalEvidence[]:
  policyDecisionId, approval/confirmation evidence reference,
  actorId, actorType, authenticated principal/workload identity,
  action capability, policyVersion, decidedAt
```

`manifestSet` contains **every and only** terminal manifest referenced by the
frozen revision's delivered claims and scenarios; each tuple is the #24
`dispatch-task-v1` batch derivation and exact reconciliation revision resolved
by Admin, not merely a batch ID or `manifestUri`. The frozen research revision
is the single revision whose `claimSetHash` and `scenarioSetHash` matched the
v1 gate, and the frozen evidence is the complete sorted set of that revision's
manifest tuples. The sanitized payload is equally frozen by manifest/reference
and hash; raw ODP JSONB, unredacted credentials, and recomputed mutable item
lists are prohibited. Any mismatch among the decision, operation, target
scope, policy snapshot, approval evidence, revision hashes, graph fold tail,
claim reference, manifest hash, or payload hash fails closed.

The executor MUST append a distinct immutable **Delivery Execution Result**
record for each attempt. That execution record, not the authorization
decision, carries attempt/submission timestamps, retry and timeout facts,
external request/receipt identifiers, response classification, confirmation
evidence, and transport errors. A 2xx response is only a transport result; it
is not a Business Outcome. Admin MUST append a separate **Business Outcome**
record only when the frozen destination policy's confirmation/unknown/
continuation/compensation rules classify the outcome from appropriate
evidence. Unknown outcomes remain unknown and are handled by the frozen policy,
not rewritten as successful decisions.

For webhooks, this requires a new payload schema (for example,
`workflow.webhook.evidence_batch.v2`) that embeds the immutable decision ID
and hash, operation/target/policy tuple, sanitized payload manifest/hash,
approval evidence references, and frozen authority tuple above. The current v1
payload remains supported only as a non-research-authorized compatibility
payload; it MUST NOT be relabeled as v2 or be assumed to have frozen research
evidence. Delivery payloads continue to contain only sanctioned/sanitized
items and projection references.

## Explicit non-goals

This decision does not implement #24 materialization prerequisites, an ODP
read API, graph mutation authorization, a new graph schema, a Delivery
operation store, a destination policy, or a webhook v2 payload. It does not
make ResearchGraph authoritative, infer inserted-versus-duplicate facts, or
turn an existing `publishAllowed` metric/ledger `final` state into an external
side-effect permission.

## Implementation acceptance boundary

The proposal is implementable only when evidence shows all of the following:

1. Admin rejects all non-final, unknown-key, mismatched, or URI-only evidence
   references before they enter ResearchGraph or Delivery authority.
2. Graph state transitions are actor-authorized, transactionally CAS-bound to
   the current run sequence/revision, append-only, and replay deterministically.
3. The v1 publish gate and existing revision/ledger semantics retain their
   documented behavior while the new overlay fails closed.
4. A Delivery executor receives a persisted decision with the exact frozen
   graph sequence, revision hashes, and complete terminal manifest set, and
   refuses graph projection booleans or unpinned `publishAllowed` as authority.
