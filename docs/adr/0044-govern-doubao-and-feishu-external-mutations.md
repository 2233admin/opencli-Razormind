# ADR 0044: Govern Doubao capture and Feishu result synchronization as external mutations

## Status

Accepted

## Context

The Gaojixing workflow asks questions through an authenticated local Doubao browser and can
optionally append the captured result to a Feishu spreadsheet. Both operations mutate systems
outside the control plane. The spreadsheet write path also crosses a container-to-host boundary
because the operator's authenticated `lark-cli` session remains on the host.

Records are the authoritative durable result. Feishu synchronization is a configured projection;
it must not become a second source of truth or expose Feishu credentials in workflow parameters,
events, or logs. The operator also requires each completed Doubao conversation to be deleted after
its visible answer and evidence have been captured.

## Decision

- Agent-mode Doubao capture requires `canMutateExternalSites` and declares the bounded actions
  `doubao.ask`, `doubao.read`, and `doubao.delete` in its task scope.
- The exact Feishu question is sent in the task's dedicated `input.question` field. A descriptive
  message may accompany it but is never parsed as the authoritative question.
- Conversation cleanup occurs only after the complete visible response, links, sharing metadata,
  and recommendation fields have been assembled and handed to the control plane as a pre-cleanup
  evidence event. The control plane atomically writes that event to its Gaojixing evidence spool and
  only then sends an application-level `persisted` acknowledgement. Receipts are immutable
  create-or-read files, and directory metadata is synchronized where the host filesystem supports it.
  The Agent waits for that exact event acknowledgement before cleanup; delivery failure, persistence
  failure, disconnect, or acknowledgement timeout all abort iteration before deletion. Cleanup is
  confirmed only after the deletion dialog and exact conversation entry remain absent across stable
  observations. Unconfirmed cleanup fails closed and stops the remaining batch; an already captured
  successful prefix remains available to the workflow.
- The Records sink commits authoritative records before attempting Feishu synchronization.
  A synchronization failure cannot roll back or overwrite those records.
- Feishu synchronization is disabled by default and requires explicit target configuration plus
  `canMutateExternalSites`. The backend calls only the configured host bridge endpoint.
- A bridge token is mandatory for non-loopback listeners. The bridge accepts only allowlisted
  spreadsheet targets, bounds local CLI process execution time, and verifies every appended row by
  address and idempotency value. Cleartext non-loopback binding is limited to the same-host Docker
  Desktop boundary and must not be published through the host firewall; deployments that cross a
  host boundary must terminate TLS before the bridge. The bridge does not silently create or widen
  Windows HTTP.sys URL ACLs; that host-level permission remains an explicit operator action.
- Idempotency is scoped to the workflow run and stable source-row identity. Replaying one run skips
  duplicates, while a later run may append a fresh observation for the same source row.
- Dynamic column labels and mappings are permitted, but credentials and bridge tokens remain in
  environment/connection boundaries rather than the workflow graph.

## Consequences

The workflow can preserve local authoritative data even when Feishu is unavailable, and accidental
cross-target writes are rejected. Operators must configure and authorize synchronization before it
runs. Deleting Doubao conversations makes their captured URLs historical identifiers rather than a
guarantee that the remote conversation will remain retrievable; the stored answer and extracted
evidence therefore remain the durable review surface. Pre-cleanup spool files are an intentional
recovery surface and may be retained even when later Records or Feishu projection steps fail.
