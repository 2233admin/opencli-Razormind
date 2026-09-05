# Non-Bypass III Vertical Specification

**Status:** Published — https://github.com/1012839419a-alt/opencli-Razormind-gjx/issues/28

## Problem Statement

Operators cannot currently prove that a workflow collection travelled through the intended Admin → III → collector → ODP → EvidenceBatch → ResearchGraph → Delivery route, nor can they distinguish durable evidence, delivery execution, and outcome from weaker transport observations. A response from a bridge, ingest endpoint, Redis notification, graph projection, or webhook 2xx can look successful while the required downstream fact is missing, duplicated, stale, rejected, or unknown.

The vertical needs one authoritative, recoverable route with user-visible state and a governed isolated acceptance proof. It must fail closed rather than allow Admin direct fallback, raw ODP access, unpinned graph state, or transport-only delivery to become evidence of success.

## Solution

Deliver one bounded vertical with Admin as the command, authority, lifecycle, materialization, and delivery-decision owner; III as routed execution; ODP PostgreSQL as Record truth behind an independent read service; ResearchGraph as a non-authoritative, actor-governed projection; and one controlled durable receiver as the acceptance destination.

The route is accepted only when its immutable evidence chain is complete: committed Admin intent; III lifecycle; collector expected-key report; nonterminal ingress receipt; bounded ODP snapshot; final EvidenceBatch manifest; authorized/pinned ResearchGraph fold; signed receiver evidence; separate Delivery Execution Result; and policy-classified controlled acceptance Business Outcome. The highest test seam is one isolated real non-bypass vertical acceptance run. Narrow changed-contract tests defend the boundary contracts beneath it.

## User Stories

1. As an operator, I want a single run state that identifies the blocking stage, so that I can distinguish dispatch, evidence, graph, delivery, and outcome failures.
2. As an operator, I want recovery actions to preserve the same command/attempt or allocate an explicit new attempt, so that retries never silently duplicate business intent.
3. As an operator, I want cancellation to remain pending when a side effect might be in flight, so that absence of a receipt is never misreported as no effect.
4. As an operator, I want late records and amended manifests surfaced as revisions, so that historical and current evidence remain auditable.
5. As an Admin service, I want to commit immutable collection intent before any III send, so that a crash can recover safely without inventing work.
6. As an Admin service, I want durable command, attempt, task, trace, payload, and policy identities, so that every downstream observation can be correlated and validated.
7. As an Admin service, I want replay to deduplicate identical lifecycle events and reject changed content, so that at-least-once transport is safe.
8. As an Admin service, I want no direct collector, ODP, graph, or receiver fallback, so that the governed vertical cannot be bypassed.
9. As an III bridge, I want to receive immutable Admin-supplied IDs and payload hash, so that bridge acceptance/start/return events cannot be misattributed.
10. As a collector, I want to emit a bounded final expected-key report, so that item count, zero result, rejections, and exact source/event keys have a trustworthy completion boundary.
11. As a collector, I want records sent before terminal return to remain discoverable after a crash, so that they become late/unmatched evidence rather than disappearing.
12. As an ODP ingress service, I want accepted, duplicate, and rejected counts recorded as nonterminal observations, so that ingress does not impersonate persistence.
13. As an ODP store owner, I want PostgreSQL persistence to remain the Record truth, so that Redis acknowledgement or notification loss cannot corrupt completion semantics.
14. As an Admin reader, I want a scoped independent ODP read service, so that record queries have service trust, delegated workspace/run scope, redaction, and no arbitrary predicate.
15. As a materializer, I want exact expected-key reconciliation and stable attempt-page snapshots, so that pagination does not become a watermark and duplicates are not guessed from time.
16. As a materializer, I want explicit zero, partial, failed, and indeterminate precedence, so that unknown or pending evidence always fails closed.
17. As a researcher, I want only terminal, known EvidenceBatch manifests to create visible source/evidence/claim lineage, so that ResearchGraph never receives speculative ODP evidence.
18. As a researcher, I want partial batches to contribute only record-present evidence and only where the candidate revision explicitly excludes rejected/DLQ items, so that claims cannot hide missing evidence.
19. As a reviewer, I want graph propose/verify/reject/retract actions bound to authenticated scoped capabilities, so that a projection boolean cannot grant approval.
20. As a reviewer, I want independent review policy and no self-approval where required, so that verification has auditable actor authority.
21. As a reviewer, I want transactional sequence/revision CAS and append-only replay, so that stale graph state or later retraction cannot authorize delivery.
22. As a delivery authorizer, I want the existing content publish gate unchanged but advisory until the authority overlay passes, so that current research semantics remain compatible.
23. As a delivery authorizer, I want one immutable side-effect decision that pins operation, target, policy, graph sequence, research revision, manifests, payload, and approvals, so that execution cannot drift after authorization.
24. As an acceptance receiver, I want a server-resolved fixed service binding, so that clients cannot select an arbitrary private, loopback, redirected, or rebinding target.
25. As an acceptance receiver, I want a signed v2 payload with MAC version, key ID, timestamp, nonce, operation ID, decision hash, payload hash, and canonical body, so that replay and tampering are rejected.
26. As an acceptance receiver, I want durable idempotency by operation ID and decision hash, so that retries return the same receipt and perform one receiver action.
27. As a delivery executor, I want every HTTP attempt recorded as an Execution Result, so that 4xx, 5xx, timeout, duplicate, and no-response facts remain distinct.
28. As a delivery policy owner, I want Business Outcome written only from signed receiver status/receipt/callback evidence, so that 2xx is never called customer success.
29. As a security operator, I want proof bundles redacted, signed, retained, access-controlled, and key-verifiable, so that evidence remains auditable without exposing secrets.
30. As a release owner, I want an isolated real-stack acceptance gate with mandatory fail-closed injections, so that mocks and happy-path CI cannot certify the route.

## Implementation Decisions

1. **In-scope deliverables.** This specification delivers the Admin schemas, migrations, lifecycle/status APIs, and replay; III lifecycle ingress and collector final-report contract; independent ODP query service, indexes, DLQ/replay/retention contract; materializer manifests/events; actor-governed ResearchGraph V2; controlled receiver and receipt-capable v2 executor; scoped Studio vertical-status view; and real isolated acceptance harness/tests.
2. Admin owns desired operation, attempt allocation, cancellation intent, lifecycle ledger, EvidenceBatch materialization projection, DeliveryAuthorizationDecision, Execution Results, and Business Outcomes. It commits immutable intent before sending to III and exposes no direct collector, ODP, graph, or receiver fallback.
3. The following versioned contracts are mandatory and append-only unless a later revision is explicitly linked:

| Contract | Required fields and invariants |
| --- | --- |
| `IIICollectionCommandV1` | command/run/workflow-version/node identity; canonical collector payload/hash; raw-trimmed source identity inputs; trace; policy snapshot; committed-before-send reference. |
| `IIICollectionAttemptV1` | attempt number and identity; command/task/trace; immutable submitted/accepted/terminal lifecycle references; retry/replay identity. |
| `CollectorFinalExpectedKeyReportV1` | command/attempt/task/trace; bounded expected source/event set and hash; item/zero/reject counts; report sequence, timestamp, payload hash. It may follow ingress; its absence blocks finalization, not possible ODP side effects. |
| `ODPIngressOutcomeReceiptV1` | **Authoritative producer: `odp-ingest`** for validation/enqueue outcomes; authenticated III bridge writes it to the Admin lifecycle ledger and Admin persists it under ledger retention policy. Required fields: receipt ID, schema version, producer identity, command/attempt/task/trace, expected-key-set hash, per-key source/event outcome (`accepted`, `duplicate`, or `rejected`) and reason, received time, receipt hash, idempotency identity, and signature. It proves ingress outcome only, never PostgreSQL persistence; it is not an ODP query/store authority. |
| `EvidenceBatchMaterializationManifestV1` and event | schema/reconciliation revision; dispatch-task batch derivation; report/query/snapshot hashes; ingress-receipt references; counts, terminal materialization state, record refs, retention/redaction facts, and immutable manifest hash. |
| `AuthorizedResearchGraphEventV2` | graph event/revision/expected sequence; actor/principal/capability/policy envelope; final-manifest tuple; authorization and CAS binding; replay-safe idempotency identity. |
| `DeliveryAuthorizationDecisionV1` | immutable operation; binding/revision; server-resolved target/scope; destination policy snapshot/version; pinned graph sequence/revision/manifests; sanitized payload manifest/hash; approval/confirmation actor evidence. |
| `DeliveryExecutionResultV1` | every attempt’s timestamps, target/binding, request/payload hash, retry/timeout/transport facts, external IDs, and signed receipt reference or absence. |
| `ControlledAcceptanceBusinessOutcomeV1` | immutable policy-classified accepted/rejected/unknown state, evidence reference, decision/operation identity, and outcome time; receiver acceptance is not customer outcome. |
| `ReceiverReceiptV1`, status, and callback | key ID/MAC version, operation/decision/payload hash, receiver sequence/state, signed receipt/status/callback evidence, replay-safe idempotency and confirmation reference. |
| Scoped `VerticalStatusV1` | `verticalState`, `blockingStage`, `evidenceRefs`, `recoveryAction`, and `sideEffectUnknown`; it is scope-authorized, read-only, redacted, and never exposes secrets/raw ODP data. |

4. III is execution routing only. It receives immutable supplied IDs and reports idempotent acceptance/start/terminal lifecycle observations; it is not a Run or attempt authority. Collector ingress can precede terminal return and collector crash-after-ingest becomes late/unmatched indeterminate evidence until credible final report/attempt evidence arrives.
5. ODP ingest counts are nonterminal. ODP PostgreSQL is the Record truth. The committed notification is only acceleration and notification loss must reconcile through query rather than become a failure or success fact.
6. ODP reads are served by a separate read-only Rust query service; the store remains writer/schema owner. Admin delegates a machine-trusted workspace/project/workflow/run/batch scope. Fixed exact-key, attempt-page, and DLQ modes reject arbitrary predicates and redact JSONB server-side. Ingress outcome receipts are deliberately not an ODP query mode.
7. Exact source/event reconciliation is independent from page scan. Attempt pages capture the first-page `as_of`, use a query-bound opaque cursor ordered by `(committed_at,id)`, and cannot establish completion. Exact presence proves only that the PostgreSQL row exists; `ODPIngressOutcomeReceiptV1` independently classifies the ingress outcome. Materialization joins those independent facts, and missing proof remains unknown. Required migration/index work supports task/trace/source/snapshot reads, exact source/event lookup, and durable DLQ classification/retention.
8. Materialization has a versioned `materializationStatus` separate from the existing `WorkflowRunStatus` enum. Compatibility maps `awaiting_final_report`/`reconciling` to `running`, `completed`/`completed_empty` to `completed`, `partial` to `partial`, `failed_definitive` to `failed`, and `indeterminate` to `blocked`. New materialization values MUST NOT be written into the old enum.
9. ResearchGraph remains deterministic and non-authoritative. V2 mutation authorization, independent-review policy, transactionally bound current sequence/revision CAS, final-manifest eligibility, and append-only replay are required. The existing content publish gate remains unchanged and advisory until the authority overlay passes.
10. The receiver is a required isolated acceptance fixture/service with server-resolved target binding and fixed service/network/IP identity. The new v2 adapter, not the existing transport notifier, validates signed receipts and status. Receiver secrets are secure-injected and never emitted in payloads, logs, events, or proof bundles.
11. The single state/authority transition invariant is:

| Condition | Only allowed materialization/authority transition |
| --- | --- |
| Every nonempty expected key is record-present; no reject/DLQ/unknown/pending | `completed` manifest; only then eligible graph contribution. |
| Expected set exactly empty with successful declared zero and final reconciliation | `completed_empty`; no evidence/claim contribution. |
| At least one durable reject/DLQ; all others present/reject/DLQ; no unknown/pending; candidate revision explicitly excludes rejected/DLQ items | `partial`; only complete record-present claim refs can proceed. |
| Any unknown, pending, absent-with-unknown-retention, unavailable query, or missing credible final report | `indeterminate`, regardless of otherwise-terminal evidence. |
| Ingest before collector return or post-commit notification loss | Preserve ingress/record fact and reconcile; missing return/notification never proves no side effect or finality. |
| Duplicate without an `ODPIngressOutcomeReceiptV1` joined to exact record presence | Classification is `unknown`; never infer new/duplicate from timestamp, ID order, or query timing. |
| Late record or manifest amendment | Append a reconciliation revision; re-evaluate graph/delivery candidate; never rewrite a submitted operation. |
| Graph stale/auth/CAS/retract or gate mismatch | Block authority decision; no delivery override. |
| Delivery decision conflict | Reject overwrite; require a newly authorized operation. |
| Receiver cancellation race | Remain pending/unknown until signed not-accepted status or cooperative cancel acknowledgement; no receipt is not proof. |

12. Migration/version/replay rules are mandatory: legacy projections remain readable but are marked unmaterialized; new contracts carry schema version and immutable hash; replays use stored versioned inputs and never recompute ODP/materialization; every mutation, materialization amendment, authorization, execution attempt, outcome, and cancellation appends history rather than rewriting it.
13. Proof-bundle retention is policy-versioned and includes created/expiry/retention class, signed bundle manifest, scoped authenticated/audited access, redaction profile, verification key ID, and rotation/revocation state. Missing/unverifiable key or expired authoritative evidence fails closed rather than becoming accepted.

## Testing Decisions

1. Development follows TDD: add or adjust a changed-contract test before implementing each observable contract, make it fail for the intended missing/broken behavior, then implement the smallest behavior that passes.
2. The highest and required seam is one isolated `non-bypass-vertical` E2E profile. Fresh proof-runs exercise both happy and mandatory failure scenarios across Admin, III, collector, ODP ingest/store/query, projection, graph authority, receiver, and status/outcome records.
3. The real acceptance gate uses actual container-network HTTP and durable receiver persistence. Mock transport, public ephemeral receivers, webhook 2xx, and client booleans are prohibited as E2E proof.
4. The seven changed-contract seams are: (a) Admin–III command/lifecycle/replay/no-fallback; (b) ODP query authorization, exact/snapshot/DLQ/retention; (c) materializer manifest/status and scoped Studio vertical status; (d) ResearchGraph actor/CAS/retract/pinned fold; (e) delivery authority decision and frozen policy/payload; (f) receiver v2 target/MAC/idempotency/receipt; and (g) execution/outcome/proof-bundle retention/redaction/key lifecycle.
5. The mandatory fail-closed matrix injects Admin crash; III unreachable; collector no report, zero, and crash-after-ingest; ingest rejection; Redis/store/committed-notification loss; duplicate/DLQ/query/retention/page race; graph stale/auth/retract; manifest amendment; gate mismatch; decision conflict; receiver HMAC, timeout, 5xx, duplicate, unknown, restart, plus cancellation before dispatch and after unknown/in-flight send.
6. Proof-bundle tests verify signed manifest, retention policy/version/expiry, authenticated scoped access, verification-key lifecycle, and redaction. These are governed acceptance/release tests, not ordinary fast CI.

## Out of Scope

- A generalized delivery platform, additional provider marketplace, customer-specific outcome semantics, or third-party chat/email credential setup.
- Replacing all legacy webhook callers or migrating every existing workflow/source.
- Treating Admin direct ODP/collector/receiver paths, graph projection state, ingest acknowledgement, Redis stream facts, raw response bodies, mock transport, or HTTP 2xx as authoritative evidence.
- Using normal/shared/staging/production databases, external ephemeral destinations, or user credentials for the isolated acceptance proof.

## Further Notes

- The spec is intentionally implementation-prerequisite-first: current observability gaps are not silently satisfied by existing transport seams.
- Recovery must preserve immutable history. Late facts, cancellation races, and submitted external attempts are amended or reconciled, never erased.
- Published specification: https://github.com/1012839419a-alt/opencli-Razormind-gjx/issues/28. Implementation planning proceeds through the repository issue workflow.
