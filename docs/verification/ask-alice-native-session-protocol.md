# Ask Alice native session protocol: verification boundary

Date: 2026-09-06. Follow-up to [#116](https://github.com/2233admin/opencli-Razormind/issues/116),
starting from the delivered integration commit `1db20f8edf781bc69f2f9f749157323fe9c7cb57`.
This work is on `codex/ask-alice-native-continuation-20260906`; it does not merge upstream main.

## Delivered changes

- Chat execution-target discovery now resolves published V2 bindings using the
  existing capability selector. A published native Agent no longer causes a V1
  attribute error. Missing configuration, disconnected/incompatible nodes, and
  unavailable native chat integration have distinct reasons and useful setup links.
- Blank runtime advertisements and capabilities are rejected/filtered. Workspace
  authorization remains mandatory; node addresses and IDs are not exposed by chat.
- Claude readiness includes its required capability identifier. Failed/nonzero
  version probes remain blocked even if their stdout resembles a version string.
- The opt-in `isolated_session` adapter defines the runner protocol in
  [ADR-0048](../adr/0048-isolated-native-session-protocol.md). It passes explicit
  start/resume intent, server session and actor/workspace/conversation scope, and
  requires matching per-request acknowledgments. The runner owns native references
  and persistence. No adapter-local history or random ID is presented as native resume.
- Protocol requests, frames, aggregate text, process waits, and diagnostics handling
  are bounded. Cancellation closes the invocation. Windows orphan cleanup remains a
  responsibility of the independently provisioned isolation supervisor.

## Fresh checks

The focused Windows test set covers HTTP with isolated SQLite (catalog, scope,
existing background double-turn/idempotency/recovery), runtime selection, Claude
readiness, and isolated-runner protocol behavior. The complete set before adding
cancellation cases passed **32 tests**, with **2 Linux-only skips**; the final
protocol rerun passed **13 tests**, including the two new cancellation/aclose cases.
Together these cover **34 distinct Windows test cases**.

The protocol suite includes real benign subprocesses: a disk-backed fixture survives
an adapter restart, rejects a changed actor, and drains a large stderr stream while
receiving a large request. Silent and nonterminating Windows probe children time out.
The fixture's readiness claims are test data; this is not a model or isolation test.

Two Linux process tests passed in a disposable container based on the existing API
image, with networking disabled and the worktree mounted read-only. Both verify
cleanup of a real SIGTERM-ignoring descendant after its parent has exited:

```text
python -m unittest tests.unit.agent_runtimes.test_isolated_session_cleanup -v
```

Touched Python files passed Ruff, the frontend passed `tsc --noEmit --incremental false`,
the changed API type passed scoped ESLint, and `git diff --check` passed.

An independent Sol reviewer examined the catalog/Claude fixes and the protocol.
Review found blank capability projection, process cleanup, duplicated instructions,
and request bound gaps; the lead fixed them and added regression coverage. Luna
handled lookup/readiness fixes, Terra implemented the protocol draft, and the lead
performed integration, real-child tests, and publication. The protocol required
lead rework and independent review because process lifecycle was more demanding
than its first draft anticipated.

## Not established by this delivery

This is protocol groundwork with no production consumer. Native `/chat` remains
blocked; Operations Agent dispatch currently supplies no AgentSession ID. Real
Codex/Claude runner provisioning, server-owned node/session bindings, and governed
tool/proposal integration still require implementation and verification. The new
adapter only projects text and terminal events and has no production tool gateway.

The actual database read at `2026-09-06T15:18:51Z` still contained zero enabled
providers, zero enabled LLM models, and zero published Agents in the target workspace.
There has been no real two-turn model acceptance. Choosing/configuring the execution
connection remains necessary; this work does not imply that credentials alone
complete native integration.

No deployment or database migration was performed. The existing API image remained
healthy after verification, the integration worktree's two generated frontend config
changes were preserved, and the completed Doubao pipeline was not executed again.
The rejected dashboard chat/catalog blocks remain removed. Issue #116 stays open.
