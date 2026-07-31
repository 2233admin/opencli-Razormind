# OpenCLI Admin Development Rules

These rules apply only to this repository. They extend the workspace rules in
`D:\projects\AGENTS.md`.

## Outcome And Verification

- Start each task by identifying the requested outcome, acceptance criteria,
  affected scope, and the smallest check that can prove completion.
- Keep changes small and reversible. Reuse existing project patterns and
  dependencies before adding abstractions or packages.
- Prefer deletion, the standard library, native platform features, and already
  installed dependencies before adding code or dependencies.
- Never simplify away trust-boundary validation, data-loss protection,
  security controls, or accessibility basics.
- Do not claim completion without fresh evidence. Prefer targeted tests first,
  then typecheck, lint, build, integration, or browser smoke checks according to
  the risk of the change.

## Documentation Hygiene

- Keep the repository root limited to durable product, architecture, testing,
  and agent-instruction entry points.
- Track active plans and tasks in GitHub Issues. Record durable architecture
  decisions in `docs/adr/`; do not add root-level `PLAN_*`, `GOAL*`, `HANDOFF*`,
  or `AUDIT*` files.

## Task-Aware Model Routing

Use a Tian Ji horse-racing strategy: match the task to the least expensive model
and agent lane that can complete it reliably. Do not default every task to the
strongest model, and do not use multiple agents when coordination costs more than
the work.

- Repository lookup, symbols, call chains, and pattern discovery: use the fast
  Explore/Spark lane.
- Small, explicit, routine implementation: prefer Luna High.
- Difficult single-module implementation: prefer Luna XHigh.
- Larger features with several connected components: prefer Terra Medium.
- Repository-wide changes, migrations, and broad refactors: prefer Terra High.
- Architecture, planning, ambiguous requirements, difficult debugging, security
  decisions, and final high-risk review: use Sol High.
- Test design and regression coverage: use a Test Engineer lane when the test
  surface is independently substantial.
- Material changes should receive an independent Verifier or reviewer pass;
  routine low-risk edits may be verified directly by the implementing agent.
- Ultra/parallel multi-agent execution is opt-in. Use it only when the task has
  multiple genuinely independent workstreams with clear ownership and merge
  boundaries.

Model names above describe routing preferences. Resolve the actual configured
model through the Codex/OMX runtime; never hardcode model IDs in application code,
scripts, or committed runtime configuration.

## Agent Delegation Contract

- The lead agent owns the complete goal, decomposition, integration, risk calls,
  and final verification.
- Delegate only bounded work with a clear scope, expected evidence, shared-file
  constraints, and a return condition.
- Prefer one specialist per concern: exploration, implementation, testing,
  debugging, or verification. Do not ask several agents to make overlapping edits.
- A child agent that discovers unexpected complexity, shared-file conflict, or a
  materially different problem must report upward instead of silently expanding
  scope.
- Escalate the model or re-split the task only when evidence shows the current
  lane is insufficient.

## Asynchronous Work And Notifications

- Run slow tests, builds, browser checks, and other non-interactive commands in a
  background process or persistent execution session when useful.
- Register for completion or poll through the available wait/notification
  mechanism; do not use arbitrary long sleeps and do not require the user to send
  `continue` for an already authorized task.
- Treat a completion notification as a new reasoning checkpoint: inspect the
  result, reconsider assumptions, and choose the next action instead of blindly
  continuing the previous plan.
- Keep the user informed with short progress updates when work remains active.

## Routing Experiment Log

For substantial development tasks, include a compact routing note in the final
report:

- task shape and selected lane;
- delegated agents and their bounded responsibilities;
- verification performed;
- any escalation or rework and why it was needed.

Use completed tasks to refine future routing based on delivery time, first-pass
success, regressions, review findings, and avoidable escalation. Optimize for
verified delivery and economical model use, not token consumption alone.
