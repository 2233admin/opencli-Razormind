# Define the Non-Bypass Vertical Proof and Failure Contract

**Wayfinder research for [#27](https://github.com/1012839419a-alt/opencli-Razormind-gjx/issues/27)**  
**Status:** adopted Wayfinder decision; not implemented.

## Decision

The first vertical is proven only by an isolated, real-stack run whose immutable
evidence chain crosses every adopted boundary and whose injected counterexamples
show that neither Admin nor a projection can bypass a failed boundary. The
proof is not a happy-path HTTP test and is not a claim that the required
observability exists today.

All observations, ledgers, receiver fixtures, state projections, query APIs,
and denial instrumentation named below are **implementation prerequisites**.
Current code provides partial execution/projection seams, not the complete
proof contract.

## Observed facts

| Fact | Primary source | Consequence |
| --- | --- | --- |
| Workflow run events are append-only, allocated under a per-run lock, and flushed by the shared writer; the caller owns the surrounding transaction. | [`backend/workflow/workflow_run_events.py:53-172`](../../../backend/workflow/workflow_run_events.py#L53-L172) | `Admin commit-before-send` is a new proof requirement. A flush or sequence allocation alone is not proof that dispatch was committed. |
| The current III ODP bridge offers `odp.ingest::batch`, `single`, and `health`; batch logs/counts then posts Record v2 events to `odp-ingest`. | [`iii/workers/odp-ingest-bridge/src/main.py:45-85`](../../../iii/workers/odp-ingest-bridge/src/main.py#L45-L85); [`iii/lib/odp_record.py:148-184`](../../../iii/lib/odp_record.py#L148-L184) | A bridge call/result is an ingress observation, not ODP persistence, report finality, or an Admin-owned attempt receipt. |
| `odp-store` owns the ODP PostgreSQL writer, uniqueness on `(source_id,event_id)`, and post-commit publication; committed publication failure is warning-only. | [`odp-rs/crates/odp-store/src/writer.rs:11-123`](../../../odp-rs/crates/odp-store/src/writer.rs#L11-L123); [`odp-rs/crates/odp-store/src/main.rs:74-92`](../../../odp-rs/crates/odp-store/src/main.rs#L74-L92) | Redis committed notification/XACK cannot be the persistence or completion proof. |
| Existing Studio EvidenceBatch views read the Admin run projection, while current Admin ODP access is a metrics-only read engine. | [`backend/api/v1/studio_workflows.py:469-585`](../../../backend/api/v1/studio_workflows.py#L469-L585); [`backend/control/collectors/odp_metrics.py:77-83,249-260`](../../../backend/control/collectors/odp_metrics.py#L77-L83) | `odp-query`, the materialization manifest, and a scoped Studio materialization view remain prerequisites. |
| ResearchGraph deterministically folds workflow events but is explicitly non-authoritative. Mutation endpoints do not derive an actor/principal/role decision. | [`backend/workflow/research_graph.py:27-139`](../../../backend/workflow/research_graph.py#L27-L139); [`backend/api/v1/research_graph_routes.py:62-90`](../../../backend/api/v1/research_graph_routes.py#L62-L90) | Actor authorization, transactional CAS, and pinned graph fold are required proof stages; `graph.authoritative` cannot deliver. |
| The generic webhook notifier DNS-pins through the SSRF guard, optionally HMAC-signs, and treats an HTTP success response as transport success. | [`backend/notifiers/webhook_notifier.py:18-62`](../../../backend/notifiers/webhook_notifier.py#L18-L62); [`backend/workflow/webhook_delivery.py:25-93`](../../../backend/workflow/webhook_delivery.py#L25-L93) | The existing notifier is transport-only; the controlled receiver and receipt-capable v2 adapter of #26 are prerequisites for Execution/Outcome proof. |
| The live webhook test uses external ephemeral `webhook.site`; conformance webhook paths use `httpx.MockTransport`. | [`tests/integration/test_generic_webhook_live.py:20-101`](../../../tests/integration/test_generic_webhook_live.py#L20-L101); [`tests/integration/test_workflow_conformance.py:285-400`](../../../tests/integration/test_workflow_conformance.py#L285-L400) | Neither current test is proof of a controlled durable receiver outcome. |
| #22–#26 adopt the required handoff, correlation, ODP materialization, graph authority, and controlled receiver decisions, all as not implemented. | [`define-admin-iii-run-handoff.md`](define-admin-iii-run-handoff.md); [`define-cross-plane-correlation-and-idempotency.md`](define-cross-plane-correlation-and-idempotency.md); [`define-odp-to-evidencebatch-materialization.md`](define-odp-to-evidencebatch-materialization.md); [`define-researchgraph-delivery-authority.md`](define-researchgraph-delivery-authority.md); [`select-first-real-delivery-vertical.md`](select-first-real-delivery-vertical.md) | #27 is the joint gate over those contracts; it does not declare any prerequisite implemented. |

## Immutable evidence chain and authority table

Every proof run has a generated `verticalProofRunId`, Admin run/trace/attempt/
task IDs, and a cryptographic `evidenceChainHash` over ordered immutable
observations. The following eight stages are mandatory; Execution Result and
Business Outcome are separate, final records after stage eight.

| Stage | Authoritative owner | Required immutable observation | Automatic verifier and counterexample | Prohibited fallback |
| --- | --- | --- | --- | --- |
| 1. Admin submit / commit-before-send | Admin command/attempt ledger | `admin_dispatch_committed` with command/attempt/task/trace, payload hash, correlation envelope hash, transaction/sequence reference, and dispatch authorization. | Verifier proves the record committed before the first III outbound attempt. Inject Admin crash after commit/before send: recovery may resend the same attempt idempotently. Inject crash before commit: no outbound bridge request is allowed. | No direct collector, `odp-ingest`, ODP DB, EvidenceBatch, RG, or receiver call from Admin before the committed intent. |
| 2. III supplied IDs / accept / start / return | III execution transcript | Bridge request and accept/start/terminal-return facts carry the Admin-supplied run, attempt, task, trace, command, and idempotency IDs unchanged. Collector ingestion may occur before the terminal return. | Verifier correlates all fields to stage 1 and injects unreachable III. A bridge-health or HTTP acknowledgment alone fails the stage. | Missing terminal return blocks manifest finalization and empty inference; it does **not** assert that no ODP side effect occurred. Admin cannot synthesize a collector result or bypass III. |
| 3. Collector final expected-key report | Collector/bridge report persisted by Admin | `CollectorFinalExpectedKeyReportV1`: bounded exact source/event key set/hash, item count, zero declaration, reject declarations, attempt/task/trace, sequence, payload hash. | Verifier recomputes key-set hash and tests no-report, nonzero, declared-zero, and collector-crash-after-ingest cases. A successful collector process without this report fails finality. | No `zero`, `completed`, or `partial` inference from an empty query, empty output, timeout, or accepted count. |
| 4. Ingest observations are nonterminal | ODP ingest/bridge receipt | Versioned ingress receipt records accepted/duplicate/rejected counts and key references, correlated first to stage-1 attempt/task/trace and reconciled to stage 3 only when a credible final report arrives. | Verifier compares receipt totals to expected keys when available but labels it nonterminal; inject reject, duplicate, Redis disruption, and store error. | No terminal EvidenceBatch, record count, or Business Outcome from ingest HTTP response, Redis XACK, stream ID, or committed notification. |
| 5. PostgreSQL truth / `odp-query` snapshot | `odp-store` writer/schema plus independent Rust `odp-query` | Exact-key reconciliation plus attempt page snapshot: query fingerprint, fixed `as_of`, `(committed_at,id)` cursor, record refs, retention/DLQ state, redaction profile. | Verifier checks exact expected keys independently of page scan, restarts page reads, and injects missing notification, duplicate, DLQ, retention uncertainty, and page race. | No Admin direct table/DB fallback, no `odpRef` resolver, no newer moving cursor, and no stream notification as a query result. |
| 6. EvidenceBatch manifest finality | Admin materialization manifest/event projection | Immutable `EvidenceBatchMaterializationManifestV1` revision with B1 report hash, query fingerprint, counts, status precedence, record refs, and manifest hash. | Verifier accepts only #24 terminal/known manifests and tests late record/amendment. It proves Studio reads the persisted manifest projection. | No raw ODP row, URI-only `odpRef`, or an unfinalized/unknown/retention-unknown batch enters Studio/RG/Delivery. |
| 7. ResearchGraph authorized CAS / pinned fold | Admin run-event ledger and Delivery authority | Authorized graph event includes actor/policy envelope, locked current sequence/revision CAS, final-manifest tuple, and pinned fold hash/sequence. | Verifier folds at the pinned sequence, checks actor capability/independence, and injects stale revision, unauthorized mutation, retraction, gate mismatch, and amendment. | No projection `authoritative=true`, bare `publishAllowed`, stale graph, or current/unpinned replay authorizes Delivery. |
| 8. Controlled receiver real HTTP receipt | Isolated `delivery-receiver` and Admin executor | Real network v2 request plus signed durable receiver receipt/status: operation/decision/payload hashes, HMAC MAC version/key ID/timestamp/nonce, receiver sequence/state. | Verifier validates isolated target binding, network proof, signature, idempotency, durable status across restart, and callback/status evidence. | No mock transport, webhook.site, HTTP 2xx, `received` state, or client-only boolean is an outcome. |

**Ordering rule:** a collector can send Record v2 events to ingest before its
III terminal return or final expected-key report. If it crashes after ingress,
stage 5 may find matching task/trace or exact-key records. Those records are
preserved as `late_or_unmatched_evidence` and the vertical remains
`evidence_indeterminate`; they cannot become an empty/completed/partial
manifest until a trustworthy stage-3 report and, where required, durable
attempt receipt establish the expected set and classification.

From the first receiver request onward, Admin writes two distinct append-only
record families:

1. **Delivery Execution Result** for **every** attempt, whether stage 8 returns
   a receipt, a 4xx/5xx, a timeout, a transport failure, or no response:
   timestamps, target/binding, payload hash, retry/timeout classification,
   HTTP/transport evidence, and signed receipt reference or absence.
2. **Controlled acceptance Business Outcome** only under the frozen policy
   after verified signed receiver evidence classifies the operation as
   `accepted`, `rejected`, or `unknown`. Receiver `accepted` proves only the
   controlled acceptance outcome, never a customer-specific outcome. A
   decision, a 2xx, or an execution result is never itself the outcome.

## User-visible state and recovery contract

The implementation must add a scoped, read-only vertical status projection. It
may map to existing workflow statuses for compatibility, but exposes a stable
`verticalState`, `blockingStage`, immutable evidence references, safe recovery
action, and whether an external side effect may still be unknown. It must not
expose credentials, raw ODP JSONB, receiver secret, or arbitrary endpoints.

| Fault injection | User-visible state | Required immutable observation | Safe recovery |
| --- | --- | --- | --- |
| Admin crash before commit | `not_submitted` | No stage-1 dispatch record and no outbound audit event. | User may submit anew; verifier rejects any observed downstream work. |
| Admin crash after commit/before send | `dispatch_pending` | Stage-1 committed intent, no terminal stage-2 fact. | Resume/retry the exact idempotent attempt; do not create a new task/operation. |
| III unreachable/unavailable | `bridge_unavailable` | Stage-2 transport failure bound to committed attempt. | Bounded retry after health/recovery; no collector/ODP fallback. |
| Collector fail or no final report | `awaiting_collector_report`, `collector_failed`, or `evidence_indeterminate` | Terminal III/collector failure or deadline without stage 3; any stage-5 records found first are retained as late/unmatched evidence. | Collect anew only under explicit retry policy/new attempt; do not finalize a manifest or infer no ODP side effect. |
| Collector crash after ingest / missing terminal return | `evidence_indeterminate` with `late_or_unmatched_evidence` | Exact-key or attempt-bound ODP query finds records without a credible stage-3 report/receipt. | Preserve and reconcile later; do not erase, declare zero, or materialize until the final report/receipt exists. |
| Collector-declared zero | `reconciling_empty` | Stage-3 successful zero report, empty expected-key hash. | Perform stage-5 exact reconciliation; only then terminal empty. |
| Ingest reject | `ingest_rejected_pending_reconciliation` | Stage-4 durable rejected-key evidence. | Reconcile expected set; can become `partial` only under #24 terminal rules. |
| Redis unavailable, XACK loss, or committed notification loss | `reconciling` | Ingress/store audit plus absent/late notification diagnostic. | Poll/reconcile PostgreSQL through `odp-query`; notification loss never fails persisted truth by itself. |
| Store failure | `ingest_store_failed` | ODP durable failure classification, not merely bridge error. | Retry per ingress policy; no record/manifest until exact truth exists. |
| Duplicate source/event | `reconciling_duplicate` | Exact record presence plus durable attempt receipt/boundary if inserted-vs-duplicate is displayed. | Reuse expected key; do not guess new/duplicate from timestamp. |
| DLQ | `dlq_unresolved` or `partial` | Matching durable DLQ read result and retention/replay policy. | Replay/reconcile only under declared policy; no DLQ read/retention proof remains indeterminate. |
| ODP query unavailable, retention/deletion unknown, or page race | `evidence_indeterminate` | Query failure/retention state/fixed-snapshot verification evidence. | Retry a fixed fingerprint/as-of query; do not advance cursor or call empty=zero. |
| Late record or manifest amendment | `evidence_amended` | New manifest reconciliation revision linked to the prior immutable version. | Re-evaluate RG/Delivery candidate; prior submitted external operation remains historical. |
| RG stale CAS, actor/auth failure, or retraction | `graph_blocked` | CAS conflict/authorization denial/retraction chain at pinned sequence. | Refresh/fold and submit a new authorized revision if policy permits; no direct Delivery override. |
| Publish/coverage/manifest gate mismatch | `delivery_authorization_blocked` | Gate reasons plus mismatched frozen hashes/refs. | Repair evidence/revision then issue a new authorization decision. |
| Delivery decision conflict | `delivery_decision_conflict` | Same operation with changed decision/payload/target/policy hashes. | Do not overwrite; create a newly authorized operation only after policy permits. |
| Receiver HMAC/MAC, timestamp, nonce, schema, or target rejection (4xx) | `delivery_rejected` | Signed receiver rejection or deterministic sanitized 4xx evidence. | Terminal for that operation; no altered-payload retry. |
| Receiver 5xx/retryable failure | `delivery_retrying` | Execution Result with attempt/deadline/retry evidence. | Retry identical operation/decision/payload within frozen policy. |
| Receiver timeout or connection loss | `delivery_unknown` | Unknown Execution Result; no valid receipt. | Query signed status before resend; retry same key only if status absent and policy allows. |
| Receiver duplicate | `delivery_receipt_reused` | Original receipt returned for exact `(operation, decisionHash)` key. | Stop resend; one receiver action only. |
| Receiver restart | `delivery_unknown` until status proven | Receiver durable store/restart marker plus status response. | Resolve via signed status; absence after deadline stays unknown. |
| Cancel requested with no proven receiver outcome | `cancellation_pending_outcome` | Cancellation decision plus all Execution Results/status attempts; absence of a receipt proves nothing about an in-flight side effect. | Query signed receiver status; current receiver lacks a cooperative cancel API, so such an API/ack is a prerequisite. Only signed `not_accepted` status or explicit cooperative cancel acknowledgment can prove no acceptance and transition to `cancelled`. |
| Cancel after send/unknown | `cancellation_pending_outcome` | Cancellation plus all Execution Results/status attempts, including in-flight race evidence. | Never erase history or infer no side effect; resolve signed status, then apply frozen continuation/compensation policy. |

## Prohibited false positives

The automatic gate MUST fail if any conclusion is based solely on one of these:

- Admin submit API 2xx, event flush, queue acceptance, worker health, or a
  planned dispatch rather than committed-before-send evidence.
- III request acceptance/start, collector process exit, collector item count,
  or absence of a final expected-key report.
- Ingest accepted/duplicate count, Redis XACK, Redis stream ID, a committed
  stream message, or a missing committed message.
- Empty ODP page, timestamp ordering, current cursor, `odpRef`, Admin
  `/records`, control metrics, or an Admin direct ODP query.
- A batch URI without immutable manifest revision/hash, an unfinalized batch,
  unknown/retention-unknown data, or `partial` evidence with omitted rejected/
  DLQ items.
- `research.publish-gate` v1 alone, ResearchGraph projection boolean, current
  graph head without pinned sequence/revision, actor-free mutation, or stale
  CAS/retracted assertion.
- Delivery authorization alone, webhook 2xx, `delivered=true`, mock transport,
  webhook.site observation, receiver `received`, unsigned receipt, or response
  body text without verified receiver status.
- Absence of a receiver receipt or callback after cancellation as proof that no
  side effect occurred; only signed `not_accepted` status or an explicit
  cooperative cancel acknowledgment can establish `cancelled`.

## Isolated real-stack gate

**New contract — `non-bypass-vertical` acceptance profile.** The gate runs a
per-proof-run isolated Admin, III bridge/collector, ODP ingest/store/query,
PostgreSQL, Redis, Studio-facing projection, controlled receiver, and their
network policy. It uses dedicated database names/volumes and a disposable
Docker network. It MUST NOT connect to normal developer, shared test, staging,
or production databases; it must not use public webhook.site or user-provided
credentials/URLs.

The profile produces a machine-verifiable evidence bundle containing the chain
records, sanitized logs, receiver durable ledger export, status-query/callback
receipts, manifest/graph/decision/revision hashes, verification-key/key-ID
bindings, and negative-injection results. The bundle has a signed manifest and
defined retention period; read access is authenticated, scope-limited to the
proof run, and audit logged. It is redacted before storage/export: secrets,
authorization headers, receiver MAC material, raw ODP JSONB, credentials,
private endpoint details, and unbounded response bodies are excluded.

Receiver signing/verification keys have an explicit lifecycle: the acceptance
profile securely injects active private material, pins the key ID into each
receipt/proof manifest, retains the corresponding verification key for the
bundle retention window, audits rotation/revocation, and never stores secret
material in events, payloads, logs, or bundles. A missing, revoked, or
unverifiable key leaves the proof outcome unknown/failed rather than accepted.

Success requires all eight stages, distinct Execution/Outcome records, and
each mandatory counterexample to fail closed at its named stage. These
counterexamples are a governed isolated acceptance/release gate—not ordinary
fast CI—and may not be replaced by mocks, public endpoints, or a happy-path
only test.

Cleanup runs only after the evidence bundle and terminal/unknown/cancel outcome
records are durably captured: stop isolated processes, remove the proof-run
network/volumes, revoke injected receiver secrets, and retain only the
sanitized, access-controlled bundle under the proof-run identifier. Cleanup
MUST NOT touch normal databases, external destinations, user credentials, or
unrelated containers.

## Implementation prerequisites and non-goals

This research does not implement any prerequisite. Required later work includes
Admin commit-before-send/audit records; III lifecycle correlation; collector
final reports and durable attempt receipts; `odp-query`, indexes, DLQ/retention
semantics; versioned manifests/Studio views; RG actor authorization and
transactional CAS; Delivery authority/execution/outcome storage; the controlled
receiver/binding/v2 adapter; injection harness; and isolated-stack lifecycle.

It does not authorize a direct Admin fallback, turn ResearchGraph into an
authority, add a generalized delivery platform, or permit a normal database to
be used as an acceptance fixture.

## Primary sources

1. [`backend/workflow/workflow_run_events.py`](../../../backend/workflow/workflow_run_events.py#L53-L172)
2. [`iii/workers/odp-ingest-bridge/src/main.py`](../../../iii/workers/odp-ingest-bridge/src/main.py#L45-L85)
3. [`iii/lib/odp_record.py`](../../../iii/lib/odp_record.py#L148-L184)
4. [`odp-rs/crates/odp-store/src/writer.rs`](../../../odp-rs/crates/odp-store/src/writer.rs#L11-L123)
5. [`odp-rs/crates/odp-store/src/main.rs`](../../../odp-rs/crates/odp-store/src/main.rs#L74-L92)
6. [`backend/api/v1/studio_workflows.py`](../../../backend/api/v1/studio_workflows.py#L469-L585)
7. [`backend/control/collectors/odp_metrics.py`](../../../backend/control/collectors/odp_metrics.py#L77-L83)
8. [`backend/workflow/research_graph.py`](../../../backend/workflow/research_graph.py#L27-L139)
9. [`backend/api/v1/research_graph_routes.py`](../../../backend/api/v1/research_graph_routes.py#L62-L90)
10. [`backend/notifiers/webhook_notifier.py`](../../../backend/notifiers/webhook_notifier.py#L18-L62)
11. [`backend/workflow/webhook_delivery.py`](../../../backend/workflow/webhook_delivery.py#L25-L93)
12. [`tests/integration/test_generic_webhook_live.py`](../../../tests/integration/test_generic_webhook_live.py#L20-L101)
13. [`tests/integration/test_workflow_conformance.py`](../../../tests/integration/test_workflow_conformance.py#L285-L400)
14. [`docs/wayfinder/iii-vertical/define-admin-iii-run-handoff.md`](define-admin-iii-run-handoff.md)
15. [`docs/wayfinder/iii-vertical/define-cross-plane-correlation-and-idempotency.md`](define-cross-plane-correlation-and-idempotency.md)
16. [`docs/wayfinder/iii-vertical/define-odp-to-evidencebatch-materialization.md`](define-odp-to-evidencebatch-materialization.md)
17. [`docs/wayfinder/iii-vertical/define-researchgraph-delivery-authority.md`](define-researchgraph-delivery-authority.md)
18. [`docs/wayfinder/iii-vertical/select-first-real-delivery-vertical.md`](select-first-real-delivery-vertical.md)
