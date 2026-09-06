# ADR 0048: Isolated native session protocol

## Status

Accepted as protocol groundwork; unavailable by default.

## Decision

`isolated_session` is a registered Agent runtime that launches only the
administrator-configured absolute executable in `AGENT_SESSION_ISOLATED_RUNNER`.
It uses JSON stdin and bounded JSONL stdout: `--opencli-session-probe` must
attest schema v1, OS isolation, authentication, persistent native resume,
scope binding, and a governed tool gateway. A binary/version/login check is not
readiness. Each turn uses `--opencli-session-turn`, passes the server
`AgentTask.session_id`, bound workspace/conversation/actor scope, and task
policy. The runner owns the durable mapping from that server session and scope
to its opaque native reference; no raw native ref crosses this adapter. The
runner must explicitly acknowledge the exact server-session/scope binding and
whether a resume was requested before any output is accepted.

The request forwards permissions, budget, required capabilities, and evidence
requirements. The administrator's runner must enforce them. `checkpoint="external"`
describes runner-owned durable storage, not a database owned by this adapter.

The adapter never inherits its full environment, never accepts controller
binary/args/env/cwd overrides, never accepts browser-supplied native CLI refs,
and does not publish the opaque native id in runtime events. It has bounded
requests (256 KiB), frames (64 KiB), and total text (512 KiB), continuous stderr
draining, turn deadlines, and bounded cleanup on errors/cancellation. POSIX cleanup
kills the disposable runner process group, including descendants after the parent
has exited. Windows uses a bounded best-effort process-tree termination; a runner's
OS containment supervisor must own orphan cleanup after parent exit. This adapter
alone does not provide a Windows Job Object or prove OS isolation.

## Consequences

The runner operator must provision real OS containment, credentials, durable
native-session storage, scope enforcement, and the tool gateway. This adapter
can only require attestations; it cannot prove containment or create credentials.
Only text and terminal frames are implemented at this boundary; it does not
connect tool calls or tool results to a production gateway. The current chat path
remains intentionally blocked for native continuation
until its controller/session ownership and governed tool-gateway integration is
wired. No database schema, browser transport, Codex, or Claude adapter changes
are implied by this ADR.

There is no production consumer of this protocol yet: Operations Agent dispatch
currently sends no AgentSession ID, and native Ask Alice execution stays blocked.
Protocol fakes, including a real child process with disk-backed test state, do
not establish real model execution, native CLI resume, or OS containment.
