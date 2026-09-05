# Select the First Real Delivery Vertical and Its Outcome Evidence

**Wayfinder research for [#26](https://github.com/1012839419a-alt/opencli-Razormind-gjx/issues/26)**  
**Status:** adopted Wayfinder decision; not implemented.

## Decision

Select one narrow **controlled durable webhook receiver** as the first real Delivery vertical:

```text
Admin DeliveryAuthorizationDecisionV1
  → new receipt-capable webhook v2 executor/adapter over an isolated real network
  → delivery-receiver acceptance destination
  → signed durable receipt + authenticated status query/callback
  → Admin Delivery Execution Result
  → Admin Business Outcome
```

The destination is a required new **acceptance fixture/service contract** named
`delivery-receiver`. It is implemented only by a follow-up ticket in the
isolated acceptance stack, not by this research. It is not a customer
destination, a generic event platform, a new provider abstraction, or a test
double. It accepts exactly this vertical's payload v2, verifies its signature
and pinned authority tuple, durably records it, and returns a repeatable signed
receipt/status. It needs no operator-provided third-party credential: the stack
injects a per-environment receiver secret securely into sender and receiver
configuration, never into a payload, event, response body, or log.

This is the smallest available path that can exercise actual HTTP, DNS/network
routing, request signing, a durable receiver write, idempotency, a receiver
receipt, and a determinate **controlled acceptance outcome** without treating a
mocked transport or a bare HTTP 2xx as success. It is not a claim about current
repository capability or a customer/business-domain outcome.

## Observed facts

| Observed fact | Primary source | Consequence |
| --- | --- | --- |
| The generic webhook notifier validates and DNS-pins the configured URL through the SSRF guard, sends an optional `X-Signature-256` HMAC, treats any HTTP success status as `success`, and retains at most 1,000 characters of response body. | [`backend/notifiers/webhook_notifier.py:18-62`](../../../backend/notifiers/webhook_notifier.py#L18-L62) | It is a real HTTP execution seam with signing and SSRF controls, but it has no durable receipt/status/idempotency semantics and cannot establish an outcome from its boolean. |
| The workflow webhook executor emits `workflow.webhook.evidence_batch.v1`, sends sanitized item fields, and returns `deliveryAttempted: true, delivered: true` whenever the notifier's boolean succeeds. Network failures and a false result are workflow errors. | [`backend/workflow/webhook_delivery.py:25-116`](../../../backend/workflow/webhook_delivery.py#L25-L116) | Existing `delivered` means transport success only. It is neither a Delivery Execution Result record nor a Business Outcome. |
| The webhook runtime contract is marked as a real webhook delivery path, but requires send permission, EvidenceBatch projection, and configured URL. | [`backend/workflow/runtime_contracts.py:428-449`](../../../backend/workflow/runtime_contracts.py#L428-L449); [`backend/workflow/runtime_registry.py:1026-1067`](../../../backend/workflow/runtime_registry.py#L1026-L1067) | Generic webhook is an existing guarded transport seam only. A new receipt-capable v2 executor/adapter is required; the current notifier cannot validate a signed receipt, query status, or establish an outcome. |
| The live generic-webhook test creates a `webhook.site` token and polls its latest request. The destination is external and ephemeral. | [`tests/integration/test_generic_webhook_live.py:20-101`](../../../tests/integration/test_generic_webhook_live.py#L20-L101) | It is useful live transport evidence, not a durable receiver, deterministic receipt, or outcome source. |
| Workflow conformance webhook tests replace the guarded client with `httpx.MockTransport`. | [`tests/integration/test_workflow_conformance.py:285-400`](../../../tests/integration/test_workflow_conformance.py#L285-L400) | A mocked 2xx cannot prove routing, signature verification, receiver persistence, idempotency, receipt, or outcome. |
| Email requires SMTP host/user/password; Feishu, DingTalk, and WeCom notifiers require configured third-party webhook URL, secret, token, or key material. | [`backend/notifiers/email_notifier.py:22-49`](../../../backend/notifiers/email_notifier.py#L22-L49); [`backend/notifiers/feishu_notifier.py:55-103`](../../../backend/notifiers/feishu_notifier.py#L55-L103); [`backend/notifiers/dingtalk_notifier.py:35-62`](../../../backend/notifiers/dingtalk_notifier.py#L35-L62); [`backend/notifiers/wecom_notifier.py:23-54`](../../../backend/notifiers/wecom_notifier.py#L23-L54) | They are excluded from the first vertical because they need human-managed real credentials and third-party destination behavior. |
| The current compose stack contains no durable webhook-receiver service, and no current webhook contract supplies receiver persistence, callback, status query, or idempotency. | [`docker-compose.yml:11-80`](../../../docker-compose.yml#L11-L80); [`backend/notifiers/webhook_notifier.py:35-62`](../../../backend/notifiers/webhook_notifier.py#L35-L62) | The selected receiver and protocol are explicit new implementation/acceptance prerequisites. |
| #25 makes Admin's `DeliveryAuthorizationDecisionV1` the sole side-effect authority and requires immutable operation, target/policy, graph/revision/manifest, sanitized payload, and approval pins; execution result and outcome are distinct facts. | [`docs/wayfinder/iii-vertical/define-researchgraph-delivery-authority.md:156-229`](define-researchgraph-delivery-authority.md#L156-L229) | #26 consumes those pins and chooses their first concrete destination. It does not weaken or duplicate #25 authority. |

## Alternatives considered

| Option | Real executable seam | Needs external human credential | Durable receiver/outcome already present | Decision |
| --- | --- | --- | --- | --- |
| Generic webhook → controlled receiver | Yes: existing guarded HTTP transport seam; a new v2 receipt-capable adapter is required. | No, once the isolated receiver secret is stack-provisioned. | No; add only this fixture/service contract. | **Select.** |
| `webhook.site` | Yes, live public HTTP. | No token is manually configured, but endpoint is ephemeral/public. | No deterministic durable outcome or receiver ownership. | Reject as acceptance destination. |
| Feishu/DingTalk/WeCom | Existing notifier modules. | Yes: external URL/key/secret. | Third-party response is not this vertical's deterministic outcome. | Reject. |
| SMTP email | Existing notifier module. | Yes: SMTP host/user/password. | No controlled receipt/outcome. | Reject. |
| API/feed/IM sink | No current governed Delivery sink for this path. | Varies. | No. | Reject; no implementation seam to exercise. |

## Proposed minimal acceptance destination

### 1. Destination and resolved target

**New contract — `ControlledDeliveryReceiverV1`.** In an isolated acceptance
stack, a single receiver process owns exactly:

```text
POST /v1/deliveries
GET  /v1/deliveries/{delivery_operation_id}
POST /v1/deliveries/{delivery_operation_id}/callback   # optional async confirmation
```

The acceptance profile introduces a first-class immutable
`controlled-delivery-receiver` binding. The server—not the request client,
workflow input, payload, or headers—resolves this binding to the fixed service
identity `delivery-receiver`, its expected Docker network, and its approved
container IP scope. The binding fixes route, destination scope, binding
revision, and receiver-policy version before execution.

The sender retains SSRF validation, DNS pinning, certificate/SNI validation,
and redirect refusal. The acceptance-profile resolver validates that the
resolved address belongs to the expected service identity and Docker network/IP
scope before the connection is opened, and the pinned connection is used for
the request to prevent DNS rebinding. This is not an ordinary arbitrary-private
URL allowlist and it MUST NOT permit caller-supplied private, loopback, link
local, redirected, or alternate-container targets. The acceptance fixture must
include negative requests for malicious private/loopback origins, DNS-rebound
origins, and redirect targets.

It persists deliveries and canonical receipts on its own durable
volume/database that survives process restart during the acceptance scenario.
No other provider or payload family is added.

### 2. Payload v2, sender authentication, and receipt-capable executor

**New contract — receipt-capable v2 executor/adapter.** The current generic
notifier remains a useful guarded HTTP transport seam only. It cannot validate
receiver receipts, query receiver status, apply operation-level idempotency, or
turn a response into an outcome; it MUST NOT be described as a direct reuse for
this vertical. A new narrow v2 executor/adapter sends and verifies only the
following acceptance protocol and records its execution evidence.

**New contract — `workflow.webhook.evidence_batch.v2`.** The adapter derives
one sanitized canonical JSON payload from the already persisted
`DeliveryAuthorizationDecisionV1`. It sends:

```text
Headers
  Idempotency-Key: {deliveryOperationId}:{decisionHash}
  X-MAC-Version: v1
  X-Key-Id: {acceptance-profile receiver key ID}
  X-Delivery-Operation-Id: {deliveryOperationId}
  X-Decision-Hash: {decisionHash}
  X-Payload-Hash: sha256:{canonical payload hash}
  X-Delivery-Timestamp: {UTC instant}
  X-Delivery-Nonce: {cryptographically random single-use nonce}
  X-Signature-256: sha256={HMAC-SHA256(
    macVersion || keyId || timestamp || nonce || operationId ||
    decisionHash || payloadHash || canonicalBody
  )}

Body
  schema: workflow.webhook.evidence_batch.v2
  deliveryOperationId, decisionId, decisionHash
  workspace/project/workflow/version/run/node scope
  resolvedTarget and destination binding revision
  destination policy version/snapshot hash
  runEventSequence, researchRevisionId, claim/scenario/coverage hashes
  terminal manifest tuples and sanitized payload-manifest/reference hashes
  sanctioned delivery items only
  approval/confirmation evidence references and actor identifiers
```

The receiver validates MAC version, trusted key ID, timestamp within the fixed
clock-skew window, nonce single use/replay protection, operation/decision/
payload identities, canonical payload hash/body, resolved-target scope, and
the allowed v2 schema before any durable write. A timestamp alone is never
freshness or replay protection. It does not receive raw ODP JSONB, credentials,
mutable unpinned graph state, a generic URL, or the injected receiver secret.
The pre-existing v1 payload remains a compatibility transport payload and MUST
NOT be passed to this receiver as proof of research-authorized Delivery.

### 3. Idempotency, receipts, and receiver state

The receiver's durable idempotency key is exactly:

```text
(deliveryOperationId, decisionHash)
```

Its transaction writes the canonical validated request, request hash, immutable
receiver receipt, and state together. The same key plus identical hash returns
the original signed receipt and never repeats the receiver's business action.
The same operation ID with a different decision or payload hash is a terminal
conflict; it is not a retry. A receiver receipt includes:

```text
receiptId, receiptVersion, deliveryOperationId, decisionHash, payloadHash
receiverState, receivedAt, receiverSequence
receiverPolicyVersion, confirmationReference or null
signature/keyId
```

The receiver signs the canonical receipt with its environment identity. Admin
verifies this signature before recording it. `GET` returns the same signed
receipt/state for the exact operation/decision pair; an optional callback uses
the same signature and monotonic receiver sequence. The status endpoint and
callback are acceptance-specific protocol obligations, not a generalized
webhook framework.

Allowed receiver states are `received`, `accepted`, `rejected`, and `unknown`.
`accepted` means the receiver durably performed the one defined acceptance
business action; `received` is only durable intake. The selected minimal
**controlled acceptance outcome** is therefore:

```text
accepted = a valid, pinned v2 delivery was durably persisted and accepted
           exactly once by ControlledDeliveryReceiverV1
```

It is determined only from a valid signed `accepted` receipt/status/callback,
not from request dispatch or HTTP status alone. It is deliberately not a
customer-facing or general business-domain outcome.

### 4. Execution Result versus Business Outcome

Admin appends an immutable **Delivery Execution Result** for every HTTP attempt:

```text
deliveryOperationId, decisionHash, attemptNumber, submittedAt, completedAt
resolved target/binding revision, request hash, timeout budget
transport result/status, signed receipt reference or absence, external IDs
retry classification, error category, response evidence (sanitized/truncated)
```

A 2xx creates at most a transport-success Execution Result. It is not an
`accepted` Business Outcome because the receiver may not have validated,
persisted, or completed the defined action. Admin appends a separate immutable
**Business Outcome** only after verified receiver evidence classifies the
operation as `accepted`, `rejected`, or `unknown`; this record cites the receipt
or status-query/callback evidence and the frozen destination policy.

### 5. Failure, retry, duplicate, and unknown policy

The receiver policy snapshot pinned by #25 defines bounded retry count, per
attempt timeout, total deadline, confirmation polling window, and continuation/
compensation behavior. For this vertical the rules are:

| Event | Execution Result | Business Outcome / next action |
| --- | --- | --- |
| Valid v2 + signed `accepted` receipt/status | Transport success with receipt. | `accepted`; no more sends. |
| Valid v2 + durable `rejected` receipt | Transport success with rejection receipt. | `rejected`; terminal, no automatic retry. |
| Deterministic 4xx (invalid signature/schema/pin, target scope, conflict) | Failed or rejected attempt, preserving sanitized reason. | Terminal `rejected`/configuration failure; do not retry with altered payload. |
| Retryable 429/5xx or connection failure before receipt | Failed/uncertain attempt with timestamp and error. | Retry the exact same operation/decision/payload tuple within policy budget. |
| Timeout or connection loss after request may have arrived | `unknown` execution attempt, never `delivered`. | Query signed status before resend. If absent, retry same idempotency tuple; after deadline without proof, Business Outcome is `unknown`. |
| Duplicate retry with same tuple | Execution records the retry and original receipt reference. | Receiver returns the original receipt; no duplicate business action. |
| Same operation ID with different decision/payload hash | Terminal conflict Execution Result. | `rejected`; require a new authorization/operation, not overwrite. |
| Status/callback unavailable beyond deadline | `unknown` execution state with all attempts recorded. | `unknown` Business Outcome; follow the frozen continuation/compensation policy, never infer success from 2xx. |

## Why this is real and why mock 2xx is insufficient

This vertical's acceptance run must launch the isolated Admin/ODP/III path and
the separate durable receiver, issue a real TCP/HTTP request across their
container network, and observe receiver persistence plus an authenticated
receipt/status query. It must demonstrate signature rejection, one successful
idempotent duplicate, a deterministic rejection, a timeout/unknown followed by
status reconciliation, and immutable Execution Result versus Business Outcome
records.

`httpx.MockTransport` does not cross the network, execute DNS/SSRF policy,
validate receiver HMAC, persist an idempotency key, or provide an independently
signed durable status. `webhook.site` crosses a network but is public and
ephemeral, cannot provide the controlled receiver's durable receiver state or
Business Outcome. A bare 2xx from either is transport evidence only.

## Explicit non-goals

- Implementing `delivery-receiver`, the acceptance-profile receiver binding,
the receipt-capable v2 executor/adapter, an ODP read service, a generic webhook
bus, or a marketplace of delivery providers. A subsequent implementation ticket
must provide the required fixture/service contract.
- Replacing the current generic webhook v1 path for all callers.
- Using third-party chat/email credentials or an operator-managed endpoint as
the first vertical's required destination.
- Treating a decision, `publishAllowed`, graph `authoritative` flag, mock
transport, webhook.site observation, HTTP 2xx, or a receiver `received` state
as a Business Outcome.

## Implementation acceptance boundary

This proposal can be implemented only after evidence proves that:

1. The required new acceptance fixture/service runs independently on the
isolated stack, keeps its durable ledger across restart, and uses a first-class
server-resolved receiver binding with fixed service identity/Docker
network/IP scope. It retains SSRF/DNS pinning/redirect protections and proves
that malicious private, loopback, rebinding, and redirect targets fail.
2. The new v2 executor/adapter—not the current notifier—validates signed
receipts/status, while the receiver rejects invalid MAC version/key ID,
nonce/clock-window, authority, payload pins, and schema. Secrets are safely
injected and absent from payloads/logs.
3. The receiver has exact `(deliveryOperationId, decisionHash)` idempotency,
and Admin records a separate immutable Execution Result for every attempt and a
controlled acceptance outcome only from a verified signed receipt, callback, or
status query.
4. The acceptance scenario exercises real network success, deterministic 4xx,
retryable failure, timeout/unknown reconciliation, and duplicate replay without
creating a second receiver action.

## Primary sources

1. [`backend/notifiers/webhook_notifier.py`](../../../backend/notifiers/webhook_notifier.py#L18-L62)
2. [`backend/workflow/webhook_delivery.py`](../../../backend/workflow/webhook_delivery.py#L25-L116)
3. [`backend/workflow/runtime_contracts.py`](../../../backend/workflow/runtime_contracts.py#L428-L449)
4. [`backend/workflow/runtime_registry.py`](../../../backend/workflow/runtime_registry.py#L1026-L1067)
5. [`tests/integration/test_generic_webhook_live.py`](../../../tests/integration/test_generic_webhook_live.py#L20-L101)
6. [`tests/integration/test_workflow_conformance.py`](../../../tests/integration/test_workflow_conformance.py#L285-L400)
7. [`backend/notifiers/email_notifier.py`](../../../backend/notifiers/email_notifier.py#L22-L49)
8. [`backend/notifiers/feishu_notifier.py`](../../../backend/notifiers/feishu_notifier.py#L55-L103)
9. [`backend/notifiers/dingtalk_notifier.py`](../../../backend/notifiers/dingtalk_notifier.py#L35-L62)
10. [`backend/notifiers/wecom_notifier.py`](../../../backend/notifiers/wecom_notifier.py#L23-L54)
11. [`docker-compose.yml`](../../../docker-compose.yml#L11-L80)
12. [`docs/wayfinder/iii-vertical/define-researchgraph-delivery-authority.md`](define-researchgraph-delivery-authority.md#L156-L229)
