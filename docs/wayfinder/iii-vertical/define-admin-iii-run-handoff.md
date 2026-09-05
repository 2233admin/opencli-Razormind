# Admin–III Run Handoff and Return Contract

**Wayfinder research for [#22](https://github.com/1012839419a-alt/opencli-Razormind-gjx/issues/22)**  
**Status:** adopted Wayfinder decision; not implemented.

## Conclusion

For the first non-bypass vertical, **Admin owns the durable Workflow Run, the desired operation, attempt allocation, and the canonical lifecycle ledger; III owns only execution routing and collector invocation; ODP owns persisted Record facts.**

Admin MUST issue an immutable, persisted command before it attempts III delivery. A dedicated III execution bridge MUST emit idempotent lifecycle observations back to Admin's durable ledger. A collector's synchronous result and `odp-ingest`'s acceptance response are evidence of work attempted or ingress, respectively; neither is a Run terminal result. A successful data-bearing attempt requires reconciliation against ODP persistence. The `odp.record.committed` stream may accelerate that reconciliation but cannot be its sole evidence because publication may fail after the database transaction commits.

This conclusion deliberately does **not** describe a production implementation. It establishes the contract that the subsequent specification must require.

## Observed facts

| Fact | Primary evidence | Consequence |
| --- | --- | --- |
| Current III direct invocation is `iii trigger` or SDK `worker.trigger`; under the **official III 0.19 contract**, the default call waits for the function result while void is fire-and-forget. The repository pin is not a runtime acceptance test, so this behavior still requires acceptance against the deployed repository SDK/runtime before implementation. | [III 0.19 Functions — Triggering functions](https://iii.dev/docs/0-19-0/using-iii/functions.md); the repository pins `iii-sdk==0.19.4` in [`iii/workers/collector-opencli/pyproject.toml:1-12`](../../../iii/workers/collector-opencli/pyproject.toml). | Direct invocation is not, by itself, a durable Admin operation protocol or a lifecycle stream. |
| Admin's HDA trace endpoint constructs an III trigger envelope, but it does not trigger III. The current packaged HDA execution path can preserve the asynchronous-envelope contract by returning no items; other paths collect locally or through fleet routing. | [`backend/api/v1/workflows.py:215-231`](../../../backend/api/v1/workflows.py); [`backend/workflow/opencli_hda_tracer.py:2348-2406`](../../../backend/workflow/opencli_hda_tracer.py). | There is no existing Admin III client, durable trigger acknowledgement, observer, or unified run-attempt protocol to reuse. |
| The OpenCLI collector is synchronous: it runs OpenCLI, converts items to Record v2, calls `odp.ingest::batch`, and returns a collector-specific dictionary. | [`iii/workers/collector-opencli/src/main.py:35-98`](../../../iii/workers/collector-opencli/src/main.py); [`iii/workers/odp-ingest-bridge/src/main.py:32-83`](../../../iii/workers/odp-ingest-bridge/src/main.py). | A collector result is an execution observation, not a uniform Run/Attempt/Event model and not proof that ODP storage completed. |
| Record v2 carries `task_id` and `trace_id`, while ODP's primary uniqueness is `(source_id, event_id)`. | [`iii/lib/odp_record.py:83-112`](../../../iii/lib/odp_record.py); [`odp-rs/crates/odp-store/src/writer.rs:14-29`](../../../odp-rs/crates/odp-store/src/writer.rs). | `task_id` and `trace_id` are correlation fields. They cannot replace an Admin operation identity or an attempt identity. |
| `odp-ingest` reports `accepted`, `duplicates`, `rejected`, and errors for bus publication. Without a bus it refuses normal startup; explicit no-bus development mode rejects events as unpersisted. | [`odp-rs/crates/odp-ingest/src/handlers.rs:65-123`](../../../odp-rs/crates/odp-ingest/src/handlers.rs); [`odp-rs/crates/odp-ingest/src/main.rs:24-43`](../../../odp-rs/crates/odp-ingest/src/main.rs). | Ingest acceptance is not Postgres durability; no-bus mode is never a valid production success state. |
| `odp-store` consumes the Redis ingest stream, commits Postgres before publishing `odp.record.committed`, and retries or reaps pending work. Its post-commit publish can still fail. | [`odp-rs/crates/odp-store/src/main.rs:54-92`](../../../odp-rs/crates/odp-store/src/main.rs); [`odp-rs/crates/odp-store/src/writer.rs:65-123`](../../../odp-rs/crates/odp-store/src/writer.rs); [`odp-rs/crates/odp-bus/src/redis_streams.rs:94-108`](../../../odp-rs/crates/odp-bus/src/redis_streams.rs). | Postgres persistence is the definitive data fact. The committed stream is a useful, post-commit notification but requires a query-based reconciliation fallback. |
| III is documented as a router: it receives requests, routes to workers, and routes responses back as needed. | [III Engine documentation](https://iii.dev/docs/using-iii/engine). | The engine is not evidence that it owns durable Admin Run or Attempt state. |

## Proposed immutable command

Admin creates one immutable **`IIICollectionCommand v1`** transactionally with the Admin Run and an outbound delivery record. It represents one desired collection operation, not one transport try.

```text
command_id             UUID; immutable Admin operation identity
run_id                 Admin Workflow Run identity
workflow_id            immutable Workflow identity
workflow_version_id    published version actually being run
node_id                source/package node requesting collection
collector_function_id  fixed as odp.collect::opencli_snapshot for this vertical
collector_payload      canonical JSON snapshot: site, command, args, format, mode,
                       explicit source_id and source-binding revision
payload_sha256         hash of canonical collector_payload
trace_id               immutable Run correlation ID
policy_snapshot        timeout, retry and cancellation policy version
created_at             Admin timestamp
```

The command does **not** contain mutable status, a collector response, an ODP acceptance count, or a Delivery decision. It is persisted before outward dispatch, so Admin can recover it after restart and retry transmission without rewriting intent.

Each execution try has an immutable **`IIICollectionAttempt v1`**:

```text
attempt_id             Admin-allocated UUID
attempt_number         monotonic within command_id
task_id                UUID correlation value carried into Record v2 for this attempt
command_id             parent command
trace_id               copied correlation value
submitted_at           first attempted III delivery
accepted_at            recorded only after the bridge's explicit acceptance event
terminal_at            recorded only after reconciliation
```

`command_id` is the business-operation identity. `attempt_id` distinguishes a new execution after an accepted attempt has failed or become indeterminate. `task_id` and `trace_id` correlate the collector and ODP facts; they do not make III authoritative for the Admin Run.

## Acceptance and authority

### Acceptance acknowledgement

The contract has three intentionally distinct facts:

1. **`admin_requested`** — Admin has committed the command and outbound delivery record. This is the only fact guaranteed before an external call.
2. **`iii_accepted`** — a dedicated III execution bridge has received the immutable `(command_id, attempt_id, payload_sha256)` and emitted an idempotent acceptance receipt to Admin's durable lifecycle ledger. It is an execution-admission observation, not collection completion.
3. **`odp_committed`** — reconciliation has found the corresponding persisted ODP records, or has established the permitted zero-record result from the collector result plus the final ODP query boundary.

The current repository has no public durable `iii_accepted` observer. Therefore `iii_accepted` MUST be introduced as an explicit bridge contract; until it exists, an SDK return, CLI exit, timeout, or the start of a collector function MUST NOT be labelled a durable acceptance acknowledgement.

### Authority split

| Fact | Authoritative owner | Reason |
| --- | --- | --- |
| Desired operation, published workflow/version, attempt allocation, cancellation intent, user-visible Run state, and canonical lifecycle events | **Admin** | Admin owns Project/Workflow/Run and Studio observes Admin's event spine. |
| Function routing, current worker execution, collector process observation | **III / collector** | III's supported responsibility is routing functions to workers; the collector owns the immediate OpenCLI call. |
| Accepted ingress result | **odp-ingest** | It knows whether each Record v2 event reached the configured bus, but not whether `odp-store` committed it. |
| Persisted Record fact | **ODP Postgres / odp-store** | Store writes `odp_records` and only emits `odp.record.committed` after transaction commit. |

No layer may promote another layer's partial fact into a stronger terminal fact. In particular, `accepted > 0`, a collector `ok: true`, or a returned synchronous dict MUST NOT transition an attempt to completed.

## Durable lifecycle and result contract

Admin's existing durable Run-event persistence is the canonical **lifecycle ledger**. The proposed bridge appends events keyed by `(command_id, attempt_id, sequence)`; Admin deduplicates exact re-delivery and rejects an event whose immutable command hash does not match. Required event kinds are:

```text
admin_requested
submitted_to_iii
bridge_accepted
collector_started
collector_returned
odp_ingress_observed       # counts only; never terminal
odp_reconciliation_started
odp_committed              # one or more persisted record references
completed_empty            # only after the defined final query finds no expected records
failed | partial | indeterminate
cancel_requested | cancelled
```

The return path is **at-least-once lifecycle delivery into Admin plus a query-based reconciler**:

- The bridge sends lifecycle observations to Admin's durable endpoint/ledger. Repeated delivery is normal and is deduplicated by the event key.
- `odp.record.committed` is a post-commit acceleration signal containing the original RecordEvent and record ID. A consumer may use it to append `odp_committed` rapidly.
- Admin MUST reconcile against `odp_records` using the recorded correlation boundary (`task_id`, `trace_id`, source identity, and the completed attempt's expected source set). This is mandatory because the store warns rather than retries a failed post-commit `publish_committed` call.
- A data-bearing terminal state is written only after reconciliation records the persisted record identifiers and a complete/partial accounting. An empty terminal state needs an explicit, collector-declared zero-item result plus the same final query boundary; it must not be inferred from timeout or absent messages.

There is currently **no Admin-accessible ODP records query endpoint**. #23 and #24 own the endpoint, authentication/authorization boundary, exact query shape, correlation boundary, and EvidenceBatch materialization. This decision fixes the handoff rule only: a stream notification improves latency; a durable ODP query establishes final data truth.

## Retry, cancellation, and restart semantics

| Situation | Required meaning |
| --- | --- |
| No `bridge_accepted` before a delivery deadline | Re-deliver the **same attempt** and unchanged immutable command. This is transport at-least-once, not a new collection intent. |
| `bridge_accepted` but no terminal result by the policy deadline | Mark the attempt indeterminate, reconcile ODP, then allocate a **new attempt** only if policy permits. The new attempt keeps `command_id` and gets a new `attempt_id` and `task_id`. |
| Duplicate bridge/collector/lifecycle event | Deduplicate by `(command_id, attempt_id, sequence)` and validate `payload_sha256`; do not execute a second business operation merely because reporting duplicated. |
| Duplicate ODP event | Let ODP's `(source_id,event_id)` uniqueness protect persisted records, while Admin still records the attempt-level duplicate/reconciliation accounting. |
| Admin restart | Replay unsent outbound commands, resume event delivery, and reconcile all nonterminal attempts. Never mark them failed merely because the Admin process restarted. |
| III or collector restart | Treat worker liveness as an observation. Re-deliver unaccepted attempts; reconcile accepted/nonterminal attempts before allocating another attempt. |
| ODP/store restart or backlog | Keep the attempt nonterminal while Redis/ODP recovery proceeds. Ingest acceptance is insufficient; Postgres reconciliation decides completion, partial, or indeterminate. |
| Cancellation request | Persist `cancel_requested` in Admin and deliver it to the bridge. A running collector is `cancelled` only after a cooperative cancellation acknowledgement. A CLI/SDK timeout is **not** cancellation. Late collector/ODP facts remain auditable and are reconciled; policy then decides whether the Run is completed, partial, or cancelled-with-effects. |

## Rejected alternatives

| Alternative | Why it is not selected |
| --- | --- |
| Treat `iii trigger` / `worker.trigger` synchronous return as the whole protocol | Official III semantics are direct function invocation; the current collector returns a collector-specific dict. Neither creates durable Run/Attempt state or observes ODP storage. |
| Use CLI timeout as cancellation | Timeout only ends the caller's wait. There is no current cancel contract proving that collector or ODP work stopped. |
| Fire-and-forget `Void` | It intentionally removes the caller's result, so it cannot provide acceptance, lifecycle, or recovery evidence. |
| Let III engine own product Run and Attempt authority | The engine is a router. Admin owns the Project/Workflow/Run event spine and Studio's observable state; ODP owns persisted records. |
| Send Admin directly to `odp.ingest::batch` or ODP HTTP | It bypasses the destination's required III collector path and loses collector-specific execution semantics. |
| Treat `accepted/duplicates/rejected/errors` from ingest as terminal | It reports ingress/bus publication outcomes, not `odp-store` PostgreSQL durability. |
| Treat collector completion as store completion | The collector returns after the ingest bridge response; the store independently consumes and commits from Redis. |
| Rely only on `odp.record.committed` | It is post-commit, but the store explicitly only warns if publication fails. A durable query reconciler is required. |
| Adopt an III durable queue before verifying the repository's pinned 0.19.4 surface and deployment configuration | The current project config has HTTP, cron, and observability workers, not a verified durable command queue. A future queue may become an implementation option only after compatibility and durability semantics are proved; it cannot be assumed by this decision. |

## Risks that the specification must carry forward

1. **False completion:** the largest current risk is collapsing bridge reachability, collector success, ingest acceptance, and Postgres commit into one status.
2. **Duplicate effects:** retry needs distinct command and attempt identities; ODP dedup protects records but does not make Admin's lifecycle idempotent.
3. **Lost post-commit notification:** `odp.record.committed` is not a substitute for reconciliation query.
4. **Cancellation fiction:** cancellation cannot be reported complete until a running worker acknowledges a cooperative stop; late committed data must remain visible.
5. **Cross-plane drift:** every external event must carry a validated immutable command/attempt identity rather than relying solely on task/trace correlation.

## Independent-audit checklist

Before this conclusion becomes a decision, an auditor must verify that a future specification:

- preserves the stated authority split and never treats a synchronous collector dict or ingest acceptance as a terminal Run result;
- defines authenticated, idempotent Admin lifecycle ingress and its sequence/replay rules;
- defines the exact ODP reconciliation query, expected-source/empty-result boundary, and EvidenceBatch handoff;
- defines cancellation acknowledgement and late-effect accounting rather than using timeout as a proxy;
- proves the vertical with III and Redis/odp-store enabled, plus restart, duplicate, no-bus, DLQ, missing committed notification, and cancellation negative cases.

## Primary sources

1. [`backend/api/v1/workflows.py`](../../../backend/api/v1/workflows.py#L215-L231)
2. [`backend/workflow/opencli_hda_tracer.py`](../../../backend/workflow/opencli_hda_tracer.py#L2348-L2406)
3. [`iii/README.md`](../../../iii/README.md#L12-L27)
4. [`iii/workers/collector-opencli/src/main.py`](../../../iii/workers/collector-opencli/src/main.py#L35-L98)
5. [`iii/workers/odp-ingest-bridge/src/main.py`](../../../iii/workers/odp-ingest-bridge/src/main.py#L32-L83)
6. [`iii/lib/odp_record.py`](../../../iii/lib/odp_record.py#L83-L112)
7. [`odp-rs/crates/odp-ingest/src/handlers.rs`](../../../odp-rs/crates/odp-ingest/src/handlers.rs#L65-L123)
8. [`odp-rs/crates/odp-ingest/src/main.rs`](../../../odp-rs/crates/odp-ingest/src/main.rs#L24-L43)
9. [`odp-rs/crates/odp-store/src/main.rs`](../../../odp-rs/crates/odp-store/src/main.rs#L54-L92)
10. [`odp-rs/crates/odp-store/src/writer.rs`](../../../odp-rs/crates/odp-store/src/writer.rs#L65-L123)
11. [`odp-rs/crates/odp-bus/src/redis_streams.rs`](../../../odp-rs/crates/odp-bus/src/redis_streams.rs#L94-L108)
12. [III 0.19 Functions](https://iii.dev/docs/0-19-0/using-iii/functions.md)
13. [III Engine](https://iii.dev/docs/using-iii/engine)
