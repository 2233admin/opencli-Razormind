---
status: accepted
---

# Bind product conversations to durable Agent runs

Ask Alice needs to survive page refreshes, show the tool activity of each turn,
and continue the same authorized project conversation. A second chat form or a
static capability catalog does not provide those behaviors.

## Decision

Reuse `AgentConversation` and `AgentConversationTurn` for product history, and
`AgentSession`, `AgentRun`, and `AgentRunEvent` for execution. A conversation
owns one lazily created Agent session. Each accepted turn owns one run. Their
creation is atomic, and retrying its request ID returns the same turn and run.
An active-turn database constraint prevents concurrent execution in one
conversation. The existing chat executor is called once per accepted turn.

Resolve an enabled provider and model from the server catalog, then pin that
pair to the conversation. Revalidate it before execution. Provider mode uses
bounded history replay; exceeding the bound requires a new conversation.
History replay is not native runtime resume.

Background requests return a durable run reference. Events are persisted with
an ascending sequence and replayed after the last sequence observed by the
client. Switching or disconnecting the browser does not cancel execution.
Permission checks apply to the stored conversation scope, not client-supplied
session or run identifiers. Events expose only redacted public activity.
Mutations continue to use the existing proposal and confirmation boundary.
Only the conversation creator may send, close, or reopen it; workspace members
may still read conversations that pass the existing scope authorization.

The main chat page opts into project conversations in its session list. Every
candidate is reauthorized against its stored scope and validated project
context. The default list behavior remains unchanged for other callers. This
bounded list scans at most 50 candidates before applying the requested limit.

Project results use the conversation's authorized Studio scope from ADR-0045.
The governed workspace ID used to store the conversation is not automatically
the Studio workspace ID. Selecting another conversation replaces the entire
result scope, including clearing the panel for an unscoped conversation.

## Deployment constraint

The in-process background executor is opt-in through
`AGENT_CONVERSATION_EXECUTION_MODE=local_single_process`. Deployment must have
exactly one API process and one API replica for the database. Explicitly
configured multiple workers are rejected. An application process cannot prove
that another container is absent; the deployment must enforce that condition.

Only this mode runs conversation recovery after migrations and before request
handling. Unfinished conversation runs become `interrupted`, and their active
turn slots are released. They are never automatically replayed, since a tool
may have already reached an external operation. Shutdown cancels and awaits
the process's background tasks. Multi-process background execution requires a
separate owner/lease design and is not supported by this decision.

## Native runtimes and verification

Native candidates must come from published bindings in the authorized
workspace. A locally installed or logged-in CLI alone is not a ready platform
runtime. Existing isolated-runner and filesystem controls remain mandatory.
Native continuation additionally needs a server-owned runtime session mapping
and an advertised resume capability; this provider-mode change does not add it.

Validation includes real HTTP against isolated SQLite, deterministic substitution
only at the model boundary, browser refresh and session switching, and migration
checks against a backup of existing data. These tests do not establish that a
real model or native runtime has been configured successfully.

This adapts the OpenAlice session, execution-readiness, and result-navigation
contracts to existing OpenCLI objects. It does not copy OpenAlice implementation
source or implement the future `CapabilityRouter` described by ADR-0042.
