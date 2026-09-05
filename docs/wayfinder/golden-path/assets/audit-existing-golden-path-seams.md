# Golden-path seam audit

> Historical investigation. The original findings below describe the code at the time of that investigation. In particular, the statements that Project, Workflow Draft, and Workflow Version persistence do not exist are superseded by the current Studio implementation. For the 2026-09-05 source baseline and remaining gaps, read the [current product contract findings](../../../usage/product-contract-findings.md). This note preserves the original investigation rather than rewriting its historical conclusions.

## Resolution

Keep the existing workflow authoring/compiler/runtime-event seam and the outbound Worker connection seam. Build the golden path by adding a persistent Project and published Workflow backbone around them, then adapt the working legacy collection pipeline behind Workflow Nodes. Do not attempt a broad rewrite of collectors, the canvas, or remote browser transport.

## Reusable foundations

| Golden-path concern | Existing seam | Decision |
|---|---|---|
| Workspace identity and authorization | Users, Workspaces, memberships, service identities, Workspace RBAC, fleet authentication | Reuse; add Project authorization beneath Workspace rather than another tenant model. |
| Collection connectors | Channel registry plus API, RSS, web scraper, Crawl4AI, OpenCLI, browser-act, CLI, and skill channels | Reuse behind installed Tools/Nodes; do not expose channel implementation as the product model. |
| Collection execution | Existing source/task pipeline includes normalization, deduplication, AI processing, storage strategies, cursors, domain limiting, notifications, and error taxonomy | Preserve behind a compatibility Node while Workflow Run becomes the user-visible execution identity. |
| Workflow authoring | WorkflowProject schema, demand assembler, structured patches, compiler, node registry, package internals, external-runtime import | Reuse as the Draft representation; persist it instead of sending it only request-to-request. |
| Workflow execution evidence | WorkflowRun and ordered WorkflowRunEvent persistence, replayable projections, checkpoints, trace endpoints, SSE/event mirror | Reuse as the highest execution seam; extend its identity and attempts instead of introducing another top-level Run. |
| Capability matching | Fleet inventory and capability-match APIs already project Agent/browser/runtime state | Reuse the matcher behind vendor-neutral Capability Requirements; replace static inventory assumptions incrementally. |
| Remote execution transport | Browser and node reverse WebSocket endpoints support outbound connections through NAT and have fleet-auth tests | Reuse as Worker Connection transport; add leases and verified capability semantics rather than scheduling through SSH. |
| Agent runtimes | Process/protocol RuntimeAdapter plus pi, OpenTabs, and MiniFlow adapters | Reuse; LangGraph and other runtimes remain optional adapters. |
| Closed-loop operations | Measurements, objectives, policy, Gate evaluation, Actuator, action ledger, outcome evaluation, and independent cycle task | Reuse algorithms and evidence; generalize the source-specific persistence boundary. |
| Frontend workflow surface | React Flow canvas, compiler bridge, demand draft, fleet matching, run trace, checkpoints, node inspector and three-lens groundwork | Reuse; remove fixture-first product behavior and place it inside a Project journey. |
| Frontend API/auth | Shared API client, auth-token/session helpers, TanStack Query hooks, loading/error states | Reuse as the frontend/backend seam. |

## Missing persistent product facts

| Fact | Current state | Required convergence |
|---|---|---|
| Project | No Project database model or Project routes; WorkflowProject is a Pydantic canvas payload, not the ownership container | Add Workspace-owned Project and Project Context. |
| Workflow Draft and Workflow Version | Graphs compile and run from request payloads but are not saved or published as immutable versions | Persist mutable Drafts and immutable published Versions; Automations pin a Version. |
| Collection Request | Demand-draft endpoint assembles patches but does not persist the request, resolution, or Project relationship | Persist the intent and its generated Draft relationship. |
| Artifact and Derived Representation | Workflow nodes carry artifact-shaped JSON and legacy records carry mutable raw/normalized/enrichment JSON; no reusable immutable Artifact fact exists | Add immutable Artifact/representation facts and link them to Runs and collected records. |
| Data Feed and Data Subscription | Webhooks, notification channels, consumer grants, and ODP sinks exist independently; no stable published data-product contract exists | Add Data Feed as the publish boundary, then adapt webhook/database/UI consumers as subscriptions. |
| Query Request | No freshness-bounded query API or persisted request | Add a query surface over Feed/index lookup and authorized Workflow invocation. |
| Finding and Coverage Policy | Control coverage calculations and canvas contract findings exist, but no unified evidence fact or project/feed policy exists | Normalize them into Finding and Coverage Policy rather than UI-only diagnostics. |
| Signal | No semantic broadcast record or subscription match evidence | Defer implementation until the golden-path Feed works, but reserve it as a Feed record type. |
| Connection and Browser Profile | Source credentials, CookieJar, browser bindings, and channel config are separate implementation facts | Project Source must reference a Workspace Connection/Profile; do not copy secrets into nodes. |

## Conflicting legacy models

1. **Two execution systems.** CollectionTask/TaskRun and WorkflowRun both represent user work. The legacy pipeline is operationally richer for collection; WorkflowRun is the correct product identity and event seam.
2. **DataSource owns too much.** It combines target, channel implementation, processing configuration, sink migration strategy, control objective, schedule state, and review state. Split product ownership through Connection, Source, Workflow, and policy while adapting the existing row during migration.
3. **CollectedRecord is mutable processing state.** Raw, normalized, enrichment, delivery status, and errors live on one row. Preserve it during migration, but new outputs require immutable Artifact and Derived Representation lineage.
4. **Automation has the wrong contract.** It is Workspace-owned and stores prompt, executor, schedule, approval mode, and an arbitrary project JSON object. The target model is Project-owned and pins a published Workflow Version and authorized Agent Deployment.
5. **Inbox owns a second workflow.** OperationsWorkItem has open/in-progress/resolved/closed/dismissed state and approvals embedded in evidence. The agreed model derives actionable views from Finding and Gate facts.
6. **Control Action is source-specific.** The current evidence ledger is valuable but cannot yet represent the normalized target, exact parameters, permission scope, validity, and general Device/Worker/external-system actions.
7. **Worker identity is fragmented.** Celery WorkerNode, EdgeNode, reverse WebSocket agents, browser instances, and runtime inventory overlap. Build one Device/Worker read model before replacing storage tables.
8. **Frontend navigation exposes infrastructure before intent.** There is no Project home, Collection Request entry, Data Feed, Query surface, or Integration Catalog. The canvas is a standalone route and most administrative pages are global lists.
9. **Overview encodes the old demo.** It highlights opinion monitoring, Feishu delivery, infrastructure allocation, and silently substitutes animated demo telemetry when the backend fails. This hides real system failure and does not organize around Project outcomes.
10. **Canvas remains fixture/mock-first.** Several catalog nodes, templates, agent proposals, and simulations default to fixtures or mock delivery. These are useful test assets but cannot masquerade as production capability.
11. **Automation and Agent UX are combined but not connected.** The page lists independent APIs, reports automations as waiting for a first run, and lacks the Project/Workflow/Agent Deployment relationships needed to explain what an Agent is doing.
12. **Terminology leaks implementation.** HDA remains in schema, endpoints, filenames, and frontend bridges. It may survive internally during migration but must not remain a user-facing product concept.

## Smallest convergence path

1. Add persistent Project, Collection Request, Workflow Draft, and immutable Workflow Version facts around the existing WorkflowProject schema and compiler.
2. Make WorkflowRun the one user-visible Run; wrap the existing collection pipeline as a runtime/Node compatibility adapter before deleting any legacy execution code.
3. Add immutable Artifact/Derived Representation lineage and a minimal Data Feed publish/read path. Adapt the existing record and ODP sinks rather than replacing both at once.
4. Add Query Request over the minimal Feed and one authorized on-demand Workflow; defer broad search federation.
5. Project the existing fleet sources into one Device/Worker capability view; preserve reverse WebSocket transport.
6. Normalize Finding, Gate Request/Decision, and general Control Action facts; derive Inbox views from them while reading old OperationsWorkItem rows through a compatibility projection.
7. Rebuild the frontend journey around Project outcomes and truthful backend state, reusing the canvas and API client. Fixtures remain explicit test/demo mode only.

## Highest useful verification seams

- Backend integration seam: authenticated API from Collection Request through persisted Draft/Version, Workflow Run events, Artifact, Feed, Query Request, and Gate resume.
- Frontend end-to-end seam: browser journey from Project creation to generated Workflow, Test/Live evidence, published Feed, Query result, and Inbox resolution.
- Runtime conformance seam: the same capability contract passes for local and reverse-connected Workers without asserting a GPU model.
- Data-quality seam: query results expose freshness, provenance, Coverage Policy gaps, replay, and deterministic record identity.

## Evidence

- Targeted backend command exercised workflow patches, fleet matching, dashboard projection, automations, Workspaces, and WebSocket authentication: **42 behavior tests passed**. Pytest returned nonzero only because running this subset produced 42.28% repository coverage below the global 80% threshold.
- Frontend `pnpm exec tsc --noEmit` passed.
- The audit is read-only and does not claim the full repository suite is green.
