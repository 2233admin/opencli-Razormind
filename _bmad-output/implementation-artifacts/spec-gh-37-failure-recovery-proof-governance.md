---
title: 'GitHub #37: Failure-Recovery Release Gate and Proof-Bundle Governance'
type: 'chore'
created: '2026-08-30'
baseline_commit: 'dca17f83cfa0e169d594f98faeb4696435ec208a'
status: 'done'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** #36 proves an isolated accepted route but cannot certify fail-closed recovery, cancellation races, or governed redacted evidence when a real vertical dependency fails.

**Approach:** Build release-only orchestration around unchanged #29–#36 contracts. Fresh scenarios use only real containers, controlled fixture/relay/proxy/network seams, and authenticated scoped public APIs; the runner normalizes their facts into a signed redacted result and never becomes an application authority.

## Boundaries & Constraints

**Always:** A scenario has a fresh Compose project; labeled volumes/networks; 0700 secrets; local OIDC issuer/key namespace; workspace/project/workflow/run; proxy state; artifact/signing-key/tombstone namespace. Build immutable-digest images once under content-addressed names; serial or sharded scenarios never share that tuple. Container state is admission/cleanup evidence only, never an authority fact. Bounds: admission 120s; each gate 30s; collection recovery `max(90s, configured lease+30s)`; materialization/graph 60s each; delivery execution 110s (3×30s plus backoff); reconciliation 60s; cleanup 60s; scenario 360s; full matrix 2h. A timeout signs nothing and label-cleans only that scenario. Do not use mock transport, direct DB proof, production flags, external destinations, shared resources, raw secrets/payloads, projection booleans, acknowledgments, ingress receipts, HTTP 2xx, or page traversal as terminal proof.

Materialization/DLQ/exact authority is only authenticated `POST /materialize` or `/recover` plus scoped materialization GET/status; a public manifest carries nonterminal page lineage. The driver never calls Rust `odp-query` or the page-only Admin reconciliation route as evidence.

`ScenarioResultV1` is acceptance-only, with a canonical allowlist before hash/sign/store: scenario/run/fault, bounded correlated IDs/hashes, normalized collection `{blockingStage,recoveryAction,sideEffectUncertainty}`, materialization `{status,blocker,recoveryAction,manifestHash,reconciliationRevision,pageSnapshotAsOf}`, graph `{pin,sequence,readBlocker,mutationStatus}`, delivery `{state,outcome,attemptCount,receiptHash,reconciliation}`, forbidden-fact assertions, redaction profile, timing, and governance reference. Immutable image/Compose admission provenance is a separate unsigned harness-admission report, never an authority or proof-bundle fact. Reject bundle or audit entries containing bearer tokens, private signing/receiver/transport keys, raw ODP payloads, or proxy internals; serialize audit through a separate allowlist.

**Ask First:** No approval gate remains.

**Never:** Reimplement or loosen #29–#36 authority/retry/cancellation semantics; treat governance as an Admin business contract; infer ODP finality from page order, retention timing, container state, or absent receipt; claim no possible late III or receiver effect where the public contract says unknown.

## I/O & Edge-Case Matrix

| Scenario | Actuator and admissible observation | Required boundary and forbidden success |
|---|---|---|
| Admin crash | Async submit; gate primary Admin→III before admission; kill primary. Per-scenario relay upstream switch changes only its three allowlisted callback paths from primary to control, using the same scenario Fleet secret. Identical idempotent POST via control returns `created=false` with same command/attempt/task/trace/hash; wait configured lease; scoped public `/resume` and reads via control. | Same attempt resumes. Relay switch/gate state is excluded; no new Admin intent/direct downstream send; no assertion that III could not later act. |
| III unreachable | Disconnect primary→III; scoped collection GET. Read unchanged scoped graph pin/sequence, no delivery decision/execution, and no terminal materialization contribution. | `bridge_unavailable`, `sideEffectUncertainty=true`, safe retry. Certify only no Admin-created fallback, never absence of a late III effect. |
| No report / zero / crash-after-ingest | Path callback gate drops report; signed-successful-zero fixture; or gate holds report, public collection status first proves ingress-receipt hash, then collector stops. Authenticate `POST /materialize` or `/recover`, then scoped materialization GET. | Missing/crash persists `indeterminate`; zero persists `completed_empty` only at signed-zero boundary. No terminal authority or success certificate: no graph evidence/claim, delivery decision/execution; the governed signed failure-scenario certificate remains required. Gate state is excluded. |
| Ingest/Redis/store/notification loss | Narrow bridge→real `odp-ingest` HTTP `schema_version` mutator preserves IDs/context; independently cut `odp-ingest`→Redis, `odp-store`→Postgres, and RESP-aware `odp-store`→Redis filter armed only after Postgres commit for `odp.record.committed`. Use authenticated materialize/recover plus scoped materialization GET. | Ingress receipt is nonterminal; rejection/loss becomes reject, unknown, or reconciliation. No persistence/completion inference. |
| Duplicate and DLQ | (1) identical Admin replay proves same command/attempt/hash and no second intent. (2) second real disposable command/run shares only stable source/event key, obtains signed `duplicate` receipt and authenticated materialization exact-presence result. Positive retained-DLQ classification consumed by materializer is an admission prerequisite; an absent DLQ/retention key is `unknown` and fail-closed. | Duplicate never inferred from time/order. Positive retained DLQ may produce permitted partial behavior; unknown retention/DLQ never does. No expired-ODP-data claim. |
| Query/page race | Normal collector emits/reports 100 expected keys. Acceptance-only `proof-iii-actuator` (real pinned III client/function caller) adds one unrelated correlated record before materialize through real III→bridge→ingest with its receipt callback path held, so page has 101. PG gate holds materializer page SELECT after `as_of`; actor adds another late correlated record through the same real route, then release. Drive materialize/recover and scoped materialization GET only. | Actor response/control state is excluded. Manifest `pageSnapshotAsOf` is nonterminal lineage; exact-key result determines the 100-key outcome. |
| Graph stale/auth/CAS/retract | Wrong capability and CAS mutation preserve scoped 403/409 and a before/after authenticated graph read with unchanged pin/sequence. Legal stale-manifest/retract uses durable graph read blocker. | 403/409 and unchanged read prove denial; durable blocker is reserved for stale/retract. No projection/stale/retracted pin authorizes. |
| Amendment/gate mismatch/decision conflict | Start terminal graph-eligible manifest N and pin. `proof-iii-actuator` invokes the real III ODP-ingest bridge with supplied valid context, same command/attempt/task/trace/expected key, fresh ingress idempotency, and allowed callback; real ingest returns signed `duplicate` receipt. Authenticated `/recover` must yield terminal N+1; assert both hashes, stale old pin, re-review, and new delivery operation. Apply mismatch gate and changed-decision replay. | Actor control/response excluded; no hand-authored callback/DB write. Gate on prerequisite `recover_evidence_batch` amendment symbol/test; if it proves a different compatible fact, update to that proven actor/sequence before approval; if no N→N+1 terminal amendment is proven, admission blocks. |
| Receiver HMAC/timeout/5xx | CA-trusted TLS proxy is the sole registry endpoint at fixed allowed address before real receiver at a distinct internal address; corrupt MAC, delay, or 5xx; read public DeliveryExecution. | Attempt protocol/transport/http facts and recovery; no 2xx, attempt, or retry is acceptance. |
| Receiver duplicate/unknown/restart | All three real sends reach durable receiver; proxy receives and withholds/drops each valid response until Admin is blocked unknown. Restart receiver/proxy preserving receiver DB; Admin-only reconciliation obtains real signed accepted/rejected receipt. | One durable action/receipt or unknown until signed reconciliation. Missing receipt is unknown, never no effect. |
| Cancel before dispatch | Primary delivery execution's acceptance-only PostgreSQL protocol gate passes durable `reserved` COMMIT then holds next `SELECT FOR UPDATE`; control Admin's direct path uses public delivery list/cancel; release. | Public `state=cancelled|unknown`, `attemptCount=0`, and empty attempt/result list establish no Admin outbound send under the locked boundary; no `_before_send_start` patch. |
| Cancel unknown/in-flight | TLS proxy holds first valid response after receiver commit; public cancel remains pending/unknown. **Release:** public signed accepted/rejected result may settle despite cancellation. **Drop:** remain cancelled/unknown until Admin-only reconcile receives real signed status. | No absence-of-receiver-action claim; cancellation never erases possible effect. |

</frozen-after-approval>

## Code Map

- `Dockerfile` — acceptance target must COPY failure tools/fixture and verify their digest while preserving #36 target behavior.
- `docker-compose.non-bypass-acceptance.yml`, `scripts/run_non_bypass_happy_vertical.py`, `tests/acceptance/non_bypass_vertical.py`, `tests/acceptance/test_non_bypass_happy_vertical.py`, `scripts/non_bypass_proof_contract.py` — preserved #36 base; invocation and contracts unchanged except optional stable-helper import.
- `docker-compose.non-bypass-failure.yml`, `scripts/run_non_bypass_failure_matrix.py`, `scripts/non_bypass_failure_proof_contract.py`, `tests/acceptance/non_bypass_failure_matrix.py`, `tests/acceptance/test_non_bypass_failure_matrix.py` — new overlay, runner, strict result contract, harness, and tests.
- `tests/acceptance/fault_tools/`, `tests/acceptance/fixtures/opencli-failure-proof`, `tests/acceptance/fixtures/opencli-failure-proof.sha256`, `proof-iii-actuator` service — semantic gateways, pinned deterministic fixture, and actuator-only real III bridge caller.
- `backend/api/v1/iii_collections.py`, `backend/schemas/iii_collection.py`, `backend/workflow/iii_collection_dispatch.py` — scoped submit/status/resume/cancel and immutable collection authority.
- `backend/api/v1/odp_reconciliation.py` — page-only delegated route, excluded as exact/DLQ authority evidence. `backend/workflow/evidence_batch_materializer.py`, `backend/schemas/iii_collection.py` — materialization/recovery fields and precedence.
- `backend/api/v1/research_graph_v2_routes.py`, `backend/schemas/research_graph_v2.py`, `backend/workflow/research_graph_v2.py`; `backend/api/v1/delivery_execution_routes.py`, `backend/schemas/delivery_execution.py`, `backend/workflow/delivery_execution.py`, `backend/security/controlled_receiver.py`, `backend/api/v1/controlled_receiver_routes.py` — graph and delivery public authority.
- Admission prerequisites owned elsewhere: `tests/integration/test_iii_collection_vertical.py` (collection/evidence RBAC); `tests/integration/test_evidence_batch_materialization_api.py` (positive DLQ is `retained`, absent is `unknown`, and `recover_evidence_batch` amendment); `odp-rs/crates/odp-query/src/types.rs`, `query.rs` — no `expired` retention state is claimed.

## Tasks & Acceptance

**Execution:**
- [x] `Dockerfile`, `docker-compose.non-bypass-failure.yml`, `tests/acceptance/fault_tools/`, `tests/acceptance/fixtures/opencli-failure-proof`, `tests/acceptance/fixtures/opencli-failure-proof.sha256`, `tests/acceptance/non_bypass_failure_matrix.py` — add acceptance-target COPY/SHA verification and generated no-port overlay; add `proof-iii-actuator`, semantic gateways, deterministic one/zero/100-record fixture, actor-supplied 101st pre-snapshot and 102nd late records, two Admins/scenario PostgreSQL, and III/callback/PostgreSQL/TLS/RESP/PG-page gates. Keep base #36 invocation unchanged; verify catalog image IDs and fixture digests before `up --no-build`.
- [x] `scripts/run_non_bypass_failure_matrix.py`, `scripts/non_bypass_failure_proof_contract.py`, `tests/acceptance/test_non_bypass_failure_matrix.py` — preflight prerequisite tests/contracts and catalog IDs; compose base+overlay; actor drives only permitted real III bridge calls; public APIs supply all certificate facts; validate/hash/sign allowlisted result, assert boundaries/no fallback, timeout cleanup, and no actuator/gate control state in proof. Happy runner/contract remain unchanged except optional stable-helper import.
- [x] `scripts/proof_bundle_governance.py`, `tests/acceptance/test_proof_bundle_governance.py` — no-port `proof-governance` HTTP service and scenario durable store. Local OIDC/JWKS has separate `bundle-writer` and `key-admin`; immutable HTTP bootstrap-active/stage-next/promote/retire/revoke operations permit only active key/version signing, prohibit writer administration, expose trust-root/public fingerprint through authenticated HTTP, and audit each transition. A dedicated immutable audit-root Ed25519 key/fingerprint signs canonical predecessor-linked records with key ID. Each create; successful read/verify/audit-read; bundle-key lifecycle; retention/tombstone; and attributable authenticated denial appends one record. Invalid/unparseable credentials may rec…

**Acceptance Criteria:**
- Given an admitted row completing before its deadline, when its authenticated public boundary settles, then exactly one signed `ScenarioResultV1` passes DTO and audit allowlists and records actuator, IDs/hashes, public facts, forbidden-fact absence, and governance reference.
- Given an uncertain III/receiver/cancellation case, when no signed reconciliation proves finality, then it certifies only explicitly observable absence of Admin-created fallback. Only the locked pre-send row certifies no Admin outbound send from public reserved→`cancelled|unknown`, `attemptCount=0`, and empty attempts/results.
- Given a governance scope, bytes/hash/signature, key lifecycle, audit-continuity, tamper, or expiry failure, when its HTTP endpoint is called, then it denies with no secret; audit read verifies the signed predecessor chain before success; expired retrieval/verification returns `410` and no certificate issues.

## Verification

**Commands:**
- `docker compose -p non-bypass-failure-preflight -f docker-compose.non-bypass-acceptance.yml -f docker-compose.non-bypass-failure.yml config --quiet`
- `uv run --extra dev pytest -o addopts='' tests/integration/test_iii_collection_vertical.py tests/integration/test_evidence_batch_materialization_api.py -q`
- `uv run --extra dev python scripts/run_non_bypass_failure_matrix.py --compose-file docker-compose.non-bypass-acceptance.yml --overlay-file docker-compose.non-bypass-failure.yml --artifact-dir .artifacts/non-bypass-failures`
- `uv run --extra dev pytest -o addopts='' tests/acceptance/test_non_bypass_failure_matrix.py tests/acceptance/test_proof_bundle_governance.py -q`

## Suggested Review Order

**Release orchestration**

- Builds isolated scenario lifecycles, admission, execution, governance, and cleanup from one runner.
  [`run_non_bypass_failure_matrix.py:894`](../../scripts/run_non_bypass_failure_matrix.py#L894)

- Derives collision-free public receiver networking for each Compose project.
  [`run_non_bypass_failure_matrix.py:277`](../../scripts/run_non_bypass_failure_matrix.py#L277)

**Proof boundary and governance**

- Rejects non-allowlisted or secret-bearing facts before a scenario can become evidence.
  [`non_bypass_failure_proof_contract.py:94`](../../scripts/non_bypass_failure_proof_contract.py#L94)

- Stores signed, immutable, scoped bundles with lifecycle-controlled signing material.
  [`proof_bundle_governance.py:166`](../../scripts/proof_bundle_governance.py#L166)

- Verifies retained public bundle and audit evidence without service-private material.
  [`proof_bundle_governance.py:877`](../../scripts/proof_bundle_governance.py#L877)

**Materialization recovery authority**

- Converts cross-workspace materialization read denial into non-enumerable absence.
  [`iii_collections.py:131`](../../backend/api/v1/iii_collections.py#L131)

- Reconciles changed signed receipts into immutable terminal N+1 revisions.
  [`evidence_batch_materializer.py:341`](../../backend/workflow/evidence_batch_materializer.py#L341)

**Failure topology**

- Places Admin behind a TLS proxy while isolating the durable receiver backend.
  [`docker-compose.non-bypass-failure.yml:113`](../../docker-compose.non-bypass-failure.yml#L113)

- Routes only race and amendment scenarios through the permitted real III actuator.
  [`non_bypass_failure_proof_contract.py:18`](../../scripts/non_bypass_failure_proof_contract.py#L18)

**Receiver recovery**

- Bounds each live delivery execution independently from restart and reconciliation.
  [`non_bypass_failure_driver.py:1881`](../../tests/acceptance/non_bypass_failure_driver.py#L1881)

**Verification peripherals**

- Pins phase-local deadline boundaries with deterministic clock-driven assertions.
  [`test_non_bypass_failure_matrix.py:889`](../../tests/acceptance/test_non_bypass_failure_matrix.py#L889)

- Exercises receipt-amendment recovery and immutable prior manifest preservation.
  [`test_evidence_batch_materialization_api.py:1176`](../../tests/integration/test_evidence_batch_materialization_api.py#L1176)
