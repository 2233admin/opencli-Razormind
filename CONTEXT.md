# OpenCLI Operations Platform Context

OpenCLI is an Agent-driven research and intelligence pipeline for continuously collecting, processing, reviewing, and delivering data. OpenCLI Admin is its self-hosted operator console for turning recurring data needs into observable, governable Projects and Workflows.

## Public Release Baseline

The public v0.4.0 release has one end-to-end product path:

<strong>Project → Workflow → Run → Records / Evidence → Delivery</strong>

The default Docker installation includes the Admin frontend, FastAPI backend, SQLite storage, and an interactive Chromium/noVNC browser execution resource for QR-code or account login. OpenCLI, RSS, API, web, and tool nodes feed the same Workflow runtime; runs expose node events, traces, outputs, evidence, and records.

AI providers, authenticated websites, and notification channels require operator-owned credentials or browser sessions. Remote Agents, PostgreSQL, Celery/Redis, Kats, Dify/Graphon, ODP/III, and other compatibility runtimes are optional deployment profiles rather than default-install promises.

This document defines product language and object boundaries. It is not a release checklist; [README.md](README.md) is the public installation and capability contract.

## Language

**OpenCLI Platform**:
The Agent-driven platform that continuously collects, processes, and delivers data toward an operator-defined outcome. Agent interaction shapes and operates the work, but data movement and transformation remain the platform's core purpose.
_Avoid_: general-purpose Agent platform, chatbot platform, scraping tool

**OpenCLI Admin**:
The operator console for creating, observing, reviewing, and governing work executed by the OpenCLI Platform.
_Avoid_: Dashboard wall, generic admin panel, OpenCLI Platform

**Workspace**:
The governance and resource boundary that owns members, roles, Connections, Plugin Installations, reusable Runtime Agents, and Execution Resources and contains Projects. It is selected or administered through workspace controls and Governance & Settings rather than presented as a daily business object in the primary navigation.
_Avoid_: Project, Work Item, main work list, sidebar business destination

**Governance & Settings**:
The workspace and system control surface for members and roles, Connections, model providers and defaults, Plugin Installations, execution policy, data retention and masking, email and notification channels, approvals, audit, system health, backup, and destructive administration. It manages shared policy and resources rather than personal display preferences or daily operational work.
_Avoid_: model-provider-only page, personal theme settings, generic resource list, operational dashboard

**Personal Preferences**:
The operator-owned presentation and notification choices reached from the account menu, including theme, language, timezone, sidebar behavior, default landing destination, and personal alert preferences. They do not change workspace execution, access, retention, or delivery policy.
_Avoid_: Workspace policy, provider configuration, shared notification channel

**Setup Center**:
The persistent workspace readiness surface that shows which shared capabilities and configurations are ready, missing, unhealthy, or need attention, recommends the next useful setup action, and remains available after initial onboarding. It links each requirement to Agent-Guided Configuration or the corresponding manual Governance & Settings control.
_Avoid_: one-time welcome wizard, duplicate settings store, generic documentation checklist

**Capability Readiness**:
A derived status for one usable capability or capability family based on its installed and enabled Plugin, required Connections and Runtime Bindings, granted permissions, available Execution Resources, policy, schema compatibility, and current probes. Readiness is reported per capability instead of collapsed into one workspace completion percentage.
_Avoid_: global setup-complete flag, installed-equals-ready, manually maintained readiness state

**Project Readiness**:
The derived ability of a Project's current Workflows and Automations to run and deliver, calculated only from the capabilities, Connections, Runtime Bindings, permissions, Execution Resources, and delivery channels they actually require. Unrelated missing workspace capabilities do not block the Project.
_Avoid_: workspace onboarding percentage, manually toggled ready state, publishing as proof of executability

**Agent-Guided Configuration**:
A Conversational Operations flow in which the Operator Agent gathers the operator's intent and missing values, inspects current configuration, runs safe probes, and produces an Agent Operation Proposal against the same authoritative settings and resources used by manual controls. Credentials, external effects, permissions, and destructive changes still require the normal governed confirmation path.
_Avoid_: hidden chat configuration, Agent-owned settings, bypassing validation or approval

**OpenCLI Plugin**:
A versioned, workspace-installable package that extends OpenCLI through a Capability Manifest. A Plugin may register typed Workflow nodes, Agent tools, Sources, Sinks, Triggers, model or Agent strategies, runtime requirements, probes, permissions, credentials, and scoped configuration without becoming a competing product area. Installation makes capabilities discoverable; workspace enablement and policy determine whether they are runnable.
_Avoid_: arbitrary frontend module, copied product shell, hardcoded node card, ungoverned script bundle

**Plugin Installation**:
The workspace-owned record of an OpenCLI Plugin version, its enabled state, configuration, granted permissions, runtime availability, health probes, and upgrade status. Projects reference the capabilities it exposes rather than owning separate plugin copies.
_Avoid_: Project-owned plugin binary, global implicit enablement, installing by placing files in a Workflow

**Plugin Hub**:
The Provider-package management surface with separate Installed and Marketplace views. It installs, upgrades, configures, and reports the readiness of Plugin packages. A Provider may own a drill-down management surface for its complete registered capability directory, such as OpenCLI websites and commands or optional RSS OPML onboarding, while the Hub still presents only one top-level card per Provider.
_Avoid_: flattening every command into a plugin card, hiding Provider capabilities, source workspace, one plugin card per command

**Workflow Node Selector**:
The Studio add-node surface that exposes capabilities registered by installed Providers, grouped as Blocks, Data Sources, Tools, Start, and Snippets. Blocks include the platform's Dify-compatible workflow components, Start owns triggers, and Snippets owns reusable HDA/packages. Selecting a node configures a Workflow; installing or governing its Provider remains a Plugin Hub or Governance responsibility.
_Avoid_: plugin installer inside the Canvas, mixing commands with nodes, hardcoded source-pack picker

**Declarative Plugin UI**:
The platform-rendered configuration, node inspector, tool description, status, and validation UI generated from a Plugin's manifest, schemas, icons, and localized metadata. Plugins do not inject arbitrary frontend code, top-level navigation, or competing application shells in the initial plugin contract.
_Avoid_: plugin iframe, arbitrary React bundle, plugin-owned global route

**Project**:
The primary user-owned object for one continuing data outcome. It owns one or more Workflows together with the resource and delivery bindings, lifecycle, and run history needed to keep that outcome operating.
_Avoid_: Workflow, Task, one-off Run

**Project Draft**:
A durable, resumable Project created as soon as the operator confirms an initial goal and name. Agent conversation may progressively add its Sources, rules, delivery bindings, Automations, and Primary Workflow without leaving the work trapped in chat history.
_Avoid_: temporary chat plan, unsaved creation wizard, runnable Project

**Project Creation Entry**:
The unified start surface offering Agent-guided creation as the primary action, template selection, and blank creation. Every path creates the same durable Project Draft and Primary Workflow, keeps the Agent Dock available, and converges into the same editor, readiness, validation, and proposal model.
_Avoid_: three independent creation flows, template-only application type, unsaved chat creation

**Blank Creation Guide**:
The lightweight Source → Process → Deliver guidance shown in an empty Primary Workflow. It suggests the next useful node or Agent action without inventing runnable capabilities, forcing a wizard, or hiding the normal Workflow Canvas.
_Avoid_: empty canvas with no next action, separate blank-app form, fake starter nodes

**Project Template**:
A read-only, one-time blueprint for creating a Project Draft, containing an outcome, Primary Workflow graph and defaults, required capability and Connection types, recommended Automations and Destinations, and Agent continuation guidance. Instantiation copies the blueprint into the new Project without maintaining a live runtime link to the template; unresolved prerequisites become Capability Gaps.
_Avoid_: synchronized parent template, embedded credentials, immediately runnable promise, separate application type

**Conversational Operations Surface**:
The system-wide AI interaction entry through which an operator can create, inspect, and operate Projects, Workflows, Sources, Automations, Agents, Runs, and delivery settings. It acts on the same governed domain objects as the visual UI and never owns a separate shadow state.
_Avoid_: project creation chatbot, separate AI workspace, chat-only configuration

**Agent Operation Proposal**:
A reviewable change or action prepared through the Conversational Operations Surface with its target objects, base revisions, diff, risk, validation evidence, and required confirmation. Safe draft edits may apply with an undo path; publishing, activation, external effects, deletion, permissions, and credentials require explicit approval and the normal Gate and evidence path. Before application, its target revisions, Connection Bindings, permissions, and policy assumptions are checked again. Non-overlapping changes may be safely rebased with the final diff shown again; overlapping or authority-changing conflicts invalidate the proposal and require regeneration from current state.
_Avoid_: hidden Agent mutation, chat command bypass, Workflow-only proposal, applying a stale diff, silently overwriting human edits

**Proposal Revision Guard**:
The optimistic-concurrency contract shared by Agent Operation Proposals and human draft edits. Every change references the base revision of each affected object; application either preserves a proven non-overlapping rebase or stops on conflict. P0 collaboration shows editor presence and conflicts but does not require CRDT-style simultaneous canvas editing.
_Avoid_: global edit lock, last-write-wins, stale proposal approval, Google Docs-style graph collaboration as a prerequisite

**Agent Dock**:
The persistent global form of the Conversational Operations Surface. It follows the operator across OpenCLI Admin, carries the active context, and presents the same messages and Agent Operation Proposals used by embedded conversations.
_Avoid_: standalone AI page, workflow-only drawer, unrelated chatbot

**Operator Agent**:
The first-party OpenCLI Agent that helps an operator inspect and operate the platform through the Agent Dock and Embedded Agent Conversations. It creates governed proposals and control requests but is not itself a Workflow execution node.
_Avoid_: Runtime Agent, chatbot, hidden administrator

**Runtime Agent**:
A reusable intelligent worker assigned to one or more Workflow nodes to process data, choose tools, or produce typed outputs during a Run. Its runtime identity, permissions, sessions, and evidence are separate from the Operator Agent.
_Avoid_: Operator Agent, generic model, Agent-as-Source

**Data Analysis Plugin**:
An optional OpenCLI Plugin that registers governed analysis capabilities as Workflow nodes and Agent tools, turning authorized data, files, semantic context, and an analysis objective into auditable Runs and reusable tables, charts, reports, SQL records, and evidence. A bundled DataFoundry-backed implementation may adapt selected Apache-2.0 capabilities, but it does not become a second product shell or a required platform kernel.
_Avoid_: embedded DataFoundry UI, mandatory analysis subsystem, top-level DataFoundry product area

**External Analysis Runtime**:
An optional connected analysis service, including a separately deployed DataFoundry instance, that fulfills a Data Analysis Plugin's governed execution contract for remote or scaled analysis. OpenCLI retains Project, Workflow, authorization, Run, evidence, artifact reference, and delivery ownership.
_Avoid_: Execution Resource, iframe integration, remote system as authoritative Project state

**Agent Deployment**:
A Project-authorized placement of a workspace-owned Runtime Agent with its effective model, resources, tools, and permissions. Workflow nodes reference Agent Deployments rather than copying Agent definitions or assigning a global Agent directly.
_Avoid_: Agent copy, global Agent assignment, Automation Agent

**Agent Collaboration Topology**:
The Project-scoped graph of human members, Agent Deployments, Work Items, dependencies, handoffs, confirmations, and deliverables. It shows who or what is responsible for project work without duplicating the Workflow's executable data graph or the infrastructure resource graph.
_Avoid_: Workflow Canvas, Worker topology, generic org chart

**Work Item**:
A Project-scoped unit of collaborative work owned by a human member or Agent Deployment. It has an objective, dependencies, status, handoffs, required confirmations, and an expected Deliverable; it may request or observe Workflow Runs but is not itself an execution record.
_Avoid_: Run, backend Task, generic notification

**Run**:
One governed execution attempt of an explicit Workflow Version with resolved inputs, bindings, permissions, Execution Resources, status, trace, evidence, Records, and artifacts. A Run may fulfill or unblock a Work Item, but execution history remains distinct from collaborative planning.
_Avoid_: Work Item, mutable Workflow draft, Automation

**Execution Task**:
An internal schedulable unit created within a Run and dispatched to an Execution Resource. It exists for orchestration, retry, capacity, and diagnosis and is visible only where technical execution detail is needed.
_Avoid_: user-facing Work Item, top-level Tasks page, Project objective

**Task**:
The legacy code and API name for an Execution Task. It may remain at compatibility boundaries during migration but must not label collaborative Work Items or serve as an undifferentiated product concept.
_Avoid_: user-facing Task when Work Item or Run is intended

**Inbox**:
The single global queue of authoritative objects that currently require the operator's review or action, including assigned Work Items, Agent Operation Proposals awaiting confirmation, failed Runs requiring intervention, delivery exceptions, and access requests. It answers only “what needs me now”; entries link to their underlying objects and do not create a second copy of status or ownership. Project context is displayed as metadata rather than creating project-scoped Inbox modes, and “待我处理” is descriptive copy rather than a separate domain object.
_Avoid_: project-specific Inbox, Work Item list, notification archive, duplicated task state, separate Inbox-owned workflow

**External Operator Agent**:
An authorized external Agent, such as Codex or Claude Code, that operates OpenCLI through the same governed control capabilities as the Operator Agent. It uses explicit identity, context, permissions, proposals, Gates, and evidence rather than receiving a privileged bypass.
_Avoid_: direct database Agent, unrestricted administrator token, separate automation backdoor

**Agent Control API**:
The authoritative control boundary used by first-party and External Operator Agents to inspect state, create Agent Operation Proposals, confirm governed actions, execute them, and read results. MCP and SDKs expose this boundary; browser control is a fallback for capabilities the API does not yet provide.
_Avoid_: UI automation as protocol, raw REST mutation set, MCP-only business logic

**Control Action**:
A structured request to change or operate a Project, Workflow, Automation, Run, execution resource, or external system. Human and Agent requests use the same permission check, risk policy, Gate path, Actuator, and evidence trail.
_Avoid_: Agent bypass, direct SSH action, ungoverned API mutation

**Connection**:
A workspace-owned reusable authentication or connectivity configuration, such as an API token, browser login, cookie store, mailbox, or service endpoint. Projects receive scoped authorization to reference a Connection without copying its sensitive material.
_Avoid_: Source, embedded credential, project-owned secret copy

**Connection Binding**:
A Project-authorized reference from a Source, Destination, Node Instance, Agent Deployment, or Automation to a compatible workspace Connection and permitted capability scope. The Connection and workspace policy define the maximum available account capabilities; the Project Binding grants only a subset, and each Node Instance selects the smaller subset it requires. Project creation and templates may prompt the operator to select an existing Connection or enter real credentials to create one, but templates, Workflows, Agent context, and frontend state store only the binding and never secret material.
_Avoid_: credential copied into a template, plaintext node parameter, implied access to every capability on an account, project-wide access inherited by every node

**Execution Grant**:
A short-lived backend-issued authorization for one concrete Run, Node Instance, operation, and trigger identity or Automation. It is derived from the intersection of the workspace Connection capability, workspace policy, Project Connection Binding, Node-declared scope, and triggering actor's authority. A plugin receives only the mediated capability needed for that execution; it cannot enumerate unrelated Connections, obtain reusable secret material, or persist a broader login state.
_Avoid_: raw secret passed to plugin code, long-lived project token, worker-wide credential access, frontend-generated authorization

**Execution Delegation Chain**:
The auditable identity path that explains who or what authorized an execution, such as human → Operator Agent → proposal → Automation → Run → Node Instance → Execution Grant. It is attached to control and execution evidence so an Agent or scheduler never erases the accountable actor and policy context.
_Avoid_: system user as universal actor, audit entry containing only the Run ID, Agent action without originating authority

**Source**:
A Workspace-owned reusable external data endpoint. It owns the channel or adapter, site or endpoint, Connection reference, credential references, and health state, and remains authoritative across every Project that is allowed to use it. Semantic configuration changes create a Source Revision; credential rotation, health observations, and safety revocation may update immediately without changing collection semantics.
_Avoid_: Connection, credential, Project-specific query, node-owned source configuration

**Source Revision**:
An immutable semantic configuration of a Source, including its adapter, endpoint, and Connection reference. Operational state and credential material are resolved under current safety policy rather than frozen into the revision.
_Avoid_: mutable latest Source configuration, copied credentials, health snapshot

**Source Binding**:
A Project-owned authorization and collection scope for using a Workspace-owned Source. It selects permitted capabilities or commands and defines Project-specific targets such as queries, markets, repository paths, filters, and permission subsets; every binding observes authoritative Source changes without copying or mutating the Source.
_Avoid_: inline source definition, copied source configuration, direct cross-Project Source access, editing a Source through node parameters

**Source Binding Revision**:
An immutable version of a Source Binding that references an exact Source Revision and freezes its Project-specific capabilities, targets, filters, and permission subset. A Workflow Version pins Source Binding Revisions; semantic edits create new revisions and require validation and publication before Automations use them.
_Avoid_: latest binding, mutable published node binding, silent Automation drift

**Destination**:
A Project-owned external write or delivery target and permitted scope, such as mailbox recipients, a site account or community, repository, webhook, storage location, or publishing channel. It references a workspace-owned Connection without copying credentials and grants only the write capabilities the Project is allowed to use.
_Avoid_: Connection, Sink Node, unrestricted account access, generic output format

**Sink Node**:
A Workflow node that uses a Plugin-provided write capability to deliver, publish, upload, message, comment, or otherwise send governed Records and artifacts to a Destination. Read and write capabilities for the same external system may share a Connection but retain separate permissions, Gates, limits, and evidence.
_Avoid_: Source, Destination configuration, ungoverned side effect

**Delivery**:
One governed attempt to send selected Records or artifacts through a Sink Node or approved Control Action to a Destination. It records resolved target, payload reference, policy and confirmation evidence, external result, timestamps, retries, an Execution Result, and a Business Outcome without exposing secret material. A technically accepted request is not treated as proof that the external business effect succeeded. When the Business Outcome is pending or unknown, policy decides whether the Workflow may continue, must wait, should retry or compensate, or must create a human-action item in the global Inbox.
_Avoid_: artifact, Destination, notification, assumed external success, treating HTTP acceptance as business completion, universally blocking every asynchronous delivery

**Delivery Execution Result**:
The technical result of submitting one Delivery to the external system, such as accepted, rejected, or transport failure. It answers whether the operation was submitted, not whether its intended external effect was achieved.
_Avoid_: delivery receipt as business proof, conflating network success with outcome success

**Delivery Business Outcome**:
The later semantic result of a Delivery, such as confirmed, failed, pending, or unknown, derived from a receipt, callback, status query, semantic response rule, or authorized human confirmation. Its evidence and confidence remain attached to the Delivery.
_Avoid_: optimistic success, Agent-only judgment without evidence, mandatory synchronous confirmation for every Destination

**Side Effect Operation**:
The stable business identity for one intended external write, send, publish, comment, upload, or mutation. It owns an immutable Operation ID reused by every retry and Delivery attempt, along with deduplication decisions, external object identifiers, evidence, and the eventual Business Outcome. A retry creates another attempt under the same operation rather than a new business intent.
_Avoid_: new identity per retry, assuming exactly-once transport, treating duplicate attempts as independent Deliveries

**Side Effect Contract**:
The Plugin capability declaration that states how an external mutation can be made safe: a native external idempotency key, a lookup-before-write and platform deduplication ledger, or an explicit non-idempotent classification. Unknown execution results prohibit blind retry; the runtime first performs the declared status or lookup strategy and then follows the configured recovery policy. Non-idempotent capabilities require stricter confirmation and retry limits. Compensation is a new governed reverse operation with its own evidence, not a fictional rollback of an external effect.
_Avoid_: unconditional automatic retry, hidden plugin-specific deduplication, generic transaction rollback across external systems, Agent deciding idempotency from prose

**Agent Context Binding**:
The explicit Project, page, selected object, Run, and permission context attached to an Agent conversation. It lets the Agent resolve references without inventing targets and remains visible to the operator when a proposal is reviewed.
_Avoid_: hidden page context, guessed target, prompt-only memory

**Embedded Agent Conversation**:
A scene-focused presentation of the same Agent conversation inside project creation, Workflow authoring, Run diagnosis, or delivery configuration. It shares context and proposals with the Agent Dock rather than creating a separate assistant.
_Avoid_: second assistant, isolated creation chat, page-local shadow session

**Primary Workflow**:
The Project's default Workflow and normal entry point for authoring and operational status. Additional Workflows exist only when part of the Project needs an independent trigger, published version, or run lifecycle.
_Avoid_: only Workflow, default graph, treating every branch as a separate Workflow

**Collection Operations**: The operator-facing domain for deciding what should be collected, how Workflows process it, when Automations run, what recently happened, what was delivered, and which failures or actions currently require attention. Its surfaces are distributed through the Project's Orchestration, Operations, and Data navigation rather than a competing all-in-one console.
_Avoid_: Source Workflow Workbench

**Agent**:
A workspace-owned, reusable definition of an intelligent worker, independent of any project-specific runtime or permission assignment.
_Avoid_: Bot, runtime

**Node Eligibility**:
The rule that only an executable data or control-flow step may be represented as a Workflow node. Sources, transforms, Agent steps, branches, approvals, and Sinks may be nodes; Projects, Connections, Destinations, Plugin Installations, Agent definitions, Execution Resources, Automations, Runs, Deliveries, Work Items, permissions, and governance policies remain referenced or produced domain objects.
_Avoid_: everything-as-a-node, resource topology inside Workflow, Run records represented as authoring nodes

### Node Scope

**Operator Node**: A first-layer, Dify-style business step that is the normal authoring surface for operators. It communicates intent and hides implementation complexity until the operator chooses to enter it.
_Avoid_: top-level primitive, infrastructure node

**Expandable Node**: An Operator Node whose manifest exposes an inspectable or editable internal implementation graph. Entering it opens a separate node scope with breadcrumbs while the parent Workflow continues to treat it as one versioned node with stable external ports.
_Avoid_: infinite inline nesting, mandatory internal graph, changing parent ports implicitly

**Node Definition**: A versioned reusable definition of a node's stable identity, external ports, parameter interface, internal implementation graph, capability requirements, probes, and migration contract. Plugin-provided definitions are locked for ordinary use and installed versions may coexist.
_Avoid_: Workflow placement, mutable shared singleton, node instance parameters

**Node Instance**: One placement of a Node Definition in a Workflow with Project-specific parameters, bindings, position, and state. Editing ordinary instance parameters does not modify the Node Definition or other instances.
_Avoid_: Node Definition, installed plugin package, execution Run

**Project Node Definition**: A Project-owned definition explicitly derived from a locked Plugin Node Definition when an operator chooses to customize internal structure. It records source Plugin and version, exposes its changed state and diff, can be restored or rebased deliberately, and is not overwritten by Plugin upgrades.
_Avoid_: silently unlocked shared definition, anonymous detached graph, editing vendor package in place, publishing to a workspace node library

**Implementation Node**: An executable step inside an Expandable Node's optional internal graph. It exists only when the node package benefits from exposing meaningful implementation stages and does not impose a fixed hierarchy on every node.
_Avoid_: required second layer, runtime location, duplicate Operator Node

**Primitive Node**: A low-level executable operation exposed inside an implementation graph for advanced composition or diagnosis when doing so is useful. It may appear at different depths and is not a required fourth layer.
_Avoid_: Primitive Capability (the catalog ability referenced by a node), mandatory deepest layer, unlimited nesting

**Node Scope**: The currently edited graph boundary: the parent Workflow or one Expandable Node's internal graph. Breadcrumb navigation makes scope explicit; normal authoring begins in the Workflow and enters internal scope only on demand.
_Avoid_: fixed four-level hierarchy, hidden scope change, treating scope as runtime placement

**Live Collection View**: The operator-facing view of an active collection run as it happens, including streamed progress, rendered browser or pipeline state, and run-specific artifacts. It is anchored to a Recent Run, not to the default configuration surface.
_Avoid_: Static task log, canvas-only monitoring

**Operations Agent**:
An Agent that observes the platform, maintains its operation, and proposes evidence-backed improvements within its assigned permissions.
_Avoid_: External Agent Consumer, unrestricted administrator

**Run Attention Filter**: The Inbox view limited to Runs that require operator intervention, such as review, retry, acknowledgement, or dismissal. It is a filter over the single global Inbox and does not create a second run-specific queue or copy Run state.
_Avoid_: separate run-specific Inbox, recent tasks table, static run history

**Recovery Case**:
A durable human-intervention object created only when a failed or uncertain execution cannot be resolved safely by declared automatic policy. It appears in the single global Inbox and remains linked to the original Project, Workflow Version, Run, failed Node Instance, Side Effect Operation, safe checkpoint, and redacted execution evidence. It offers only recovery actions declared by the affected capability and allowed by current authorization, such as reauthenticate or replace a Connection Binding, query external status, retry the same operation, adjust permitted input, skip, compensate, or terminate. Resolution resumes from a safe checkpoint without overwriting the original failure history.
_Avoid_: generic error notification, copied Run state, free-form production shell, new Work Item for every failure, blind retry button

**Recovery Action**:
One typed, authorized, confirmed, and audited action available on a Recovery Case. Its preconditions and side-effect safety come from the node capability, Side Effect Contract, checkpoint, and current policy rather than an Agent improvising a command.
_Avoid_: arbitrary admin command, hidden state edit, recovery without evidence, action that changes the original Run history

**Deployment Revision**:
An immutable state of an Agent Deployment that an enabled Automation pins until an administrator explicitly upgrades it. Emergency suspension and permission revocation remain immediately effective.
_Avoid_: Latest deployment, live configuration

**Automation**:
A project-owned schedule or trigger that starts a specific published Workflow Version using only Agent Deployments authorized for that Project.
_Avoid_: Workflow, scheduled agent

**Collection Request**:
A user- or Agent-authored statement of the data to acquire, its sources, timeliness, media types, processing, and delivery needs. It is resolved into a Workflow Draft rather than executed by a separate collection engine.
_Avoid_: Collection Task, source configuration, workflow

**Workflow Version**:
An immutable, published form of a Workflow that an Automation can execute.
_Avoid_: Latest workflow, draft

**Agent Node**:
A Workflow node whose execution is assigned to one authorized Agent Deployment. Agent assignment belongs to the node, not to the Automation that triggers the Workflow.
_Avoid_: Automation agent, global agent

**Actuator**:
The mandatory boundary through which an Agent causes a system or external side effect; no Agent may bypass it.
_Avoid_: Direct action, privileged tool

**Blocked Run**:
A Run that cannot safely continue until a policy, permission, or approval condition is resolved. It is resumable and is not a failed Run.
_Avoid_: Failed run, paused agent

**Run**:
One logical execution of a Workflow Version. Resolving a block or retrying a node continues the same Run with another attempt; explicitly rerunning the whole Workflow creates a new Run.
_Avoid_: Trace, session, node attempt

### Workflow

**Collection Need**: The operator's desired collection outcome, expressed in domain language before node design. It is translated into a Workflow Draft made of executable nodes and resource bindings; it is not itself a node strategy or adapter configuration.
_Avoid_: treating a user request as raw node params, confusing intent with execution strategy

**Runtime-Aware Workflow Drafting**: The process of translating a Collection Need into a Workflow Draft using the system's known executable capabilities, adapter metadata, and resource resolvers. AI may propose the mapping, but missing capabilities or resources are represented as blocked gaps rather than runnable-looking nodes.
_Avoid_: AI freely drawing fake capabilities, optimistic runnable projections, silent fallbacks

**Artifact Link**:
A Run-specific relationship identifying an Artifact as an input, output, evidence, or attachment.
_Avoid_: Artifact copy, ownership

**Workflow**:
A Project-owned node graph containing any number of sources, transforms, Agent steps, controls, merges, and delivery sinks. The Workflow is the executable design; its published versions are what Automations and Runs execute.
_Avoid_: Plan, graph project, per-source pipeline

**Workflow Version**:
An immutable published form of a Workflow. It pins exact Source Binding Revisions, Capability versions, and other executable definitions; Runs and Automations reference it rather than a mutable draft or latest binding.
_Avoid_: latest Workflow, activated Workflow, draft snapshot

**Automation**:
A Project-owned trigger that runs a specific Workflow Version on a schedule or event. Activation belongs to the Automation; publishing a Workflow Version does not start execution by itself.
_Avoid_: activated Project, activated Workflow, schedule-only object

**Project Operational State**:
The derived summary of the Project's Workflows, Automations, resources, and recent Runs, such as not running, running, partially running, or blocked. It reports state but does not independently activate execution.
_Avoid_: Project activation, manually stored health badge

**Global Overview**:
The cross-Project operating view of data production, failures, pending attention, Agent and Worker availability, delivery, and anomalous Projects. It summarizes the portfolio without exposing one Project's detailed Workflow graph. Global and Project Overview reuse the same metric definitions and visualization components: the Global Overview aggregates, ranks, and highlights anomalous Projects, while Project Overview filters and drills into one Project. Metric meaning remains stable across both scopes.
_Avoid_: infrastructure metric wall, Project Overview, mixed-context dashboard

**Project Overview**:
The decision and analysis view for one Project's Workflows, Sources, Automations, Runs, Records, delivery state, and unresolved work. It inherits the active Project context and excludes unrelated Projects. Its first screen combines the Project outcome, operational state, readiness, blockers, next actions, active failures, and pending Agent or human attention with the primary collection, freshness, processing, and delivery charts. Below it, the full previously developed visualization set remains visible and is grouped by data production, execution, delivery, Agent collaboration, and resource/cost. Charts support time filtering and diagnosis and must deep-link to the underlying operational object rather than being removed in favor of summary cards. The interaction reference is Linear's work-oriented clarity, not an IDE-style resource explorer or multi-pane shell.
_Avoid_: Global Overview, Workflow editor, duplicated admin console, replacing charts with KPI cards, chart wall without actions or drill-down, VS Code-style tree navigation for ordinary operations, hiding the full chart set behind collapsed sections

**Project Navigation**:
The task-oriented navigation inside one Project. The application keeps one global sidebar; entering a Project adds a Linear-style horizontal local navigation beneath the Project identity instead of a second persistent sidebar or resource tree. It has six stable entries: Overview (outcome, readiness, key signals, blockers, next actions), Orchestration (Workflow draft, validation, versions, and publishing), Operations (Automations, Runs, Deliveries, and recovery), Data (Sources, Destinations, Records, Artifacts, and lineage), Collaboration (Work Items, Agents, members, dependencies, and collaboration topology), and Settings (Project access, authorized resources, limits, retention policy, and dangerous operations). Cross-cutting concerns such as evidence, risk, readiness, cost, search, and Agent assistance remain embedded in these tasks instead of becoming more navigation entries. The node canvas is an Orchestration surface, not the shell for the rest of the Project.
_Avoid_: object-by-object navigation, top-level Workflow/Source tabs, Project Inbox, settings scattered across operational pages, nested permanent sidebars, IDE-style resource tree, canvas-first Project shell

**Plan**:
The legacy code and document name for a Workflow. It may remain at compatibility boundaries during migration, but must not appear as a competing product concept.
_Avoid_: user-facing Plan, treating Plan and Workflow as different execution designs

**Canvas Source Node**: An atomic Workflow Node that represents exactly one executable collection source through exactly one Project-owned Source Binding. It may set bounded Run controls such as time window, item limit, sampling, and input/output mapping, but cannot run by referencing or editing a Workspace Source directly.
_Avoid_: decorative source node, abstract placeholder, UI-only source, inline source definition, a `sources[]` collection inside one atomic node

**Data Source**: The legacy API and storage name for persisted Source Node configuration and Source State. It remains an implementation boundary where required by existing collectors, but it is not a top-level operator object, catalog entry, or enablement workspace.
_Avoid_: standalone source manager, built-in source pack, public source directory, user-facing object separate from its Source Node

**Website Collection Capability**: An executable browser, Skill, or site-adapter capability that reads a website and emits Record Candidates or Runtime Artifacts. It is registered in the Capability Catalog and exposed through a Source Node only when its runtime binding and probes support execution.
_Avoid_: source directory, website card as a durable Source, claiming a capability is runnable before its runtime binding and probes pass

**Executable Canvas Node**: Any node on the Collection Canvas that participates in a Workflow. It must either execute, route, transform, store, notify, gate, or expose package-owned executable internals; if it lacks a runtime binding, the node is explicitly blocked rather than treated as decorative.
_Avoid_: fake node, visual-only node, silent mock execution

**AgentRuntime Node**: A control node inside a Workflow that uses trace, state, resources, and operator policy to choose or prepare the next action. It may call tools, but it is not a collection source and does not own source-health attribution.
_Avoid_: treating an agent as a Data Source, agent-as-source attribution, generic agent playground node

**Tool Capability Node**: A Workflow node that declares an executable tool capability the operator can configure, validate, and bind to a runtime, such as an OpenCLI command, browser action, HTTP request, script runner, site adapter, or normalization step.
_Avoid_: per-call canvas nodes, trace-as-authoring, hiding executable capability behind an agent prompt

**Signal**:
A structured, expiring Data Feed record through which an Agent, Workflow, or system publishes information, need, capability, or alert for semantic subscription. Delivery retains the match reason, evidence, visibility, acknowledgement, and feedback.
_Avoid_: Notification, raw broadcast, Agent message

**Run Checkpoint**: A state snapshot produced at a recoverable boundary during one Run. It references the Workflow Version, Run, node position, state, resources, and artifacts needed for resume, branch, or replay; it is not a Canvas node or part of the Workflow structure.
_Avoid_: checkpoint-as-node, storing recovery semantics in the Workflow definition, blindly resuming after Workflow changes

**Run State**: The structured runtime state carried through one Run, including node outputs, intermediate variables, merge results, and Agent decision context. It is produced and consumed by executable nodes during the Run.
_Avoid_: global mutable scratchpad, hiding run state inside prompts, confusing runtime state with source memory

**Source State**: The durable collection state owned by a Data Source, such as cursor position, last-seen item, site health, and recent successful collection time. It participates in Source Health and the control loop, even when Workflow nodes read or update it.
_Avoid_: treating source memory as generic run variables, writing source health into shared Run State

**State Capability Node**: A Workflow node that explicitly reads, writes, maps, or gates Run State as part of the executable graph. It can expose state behavior on the Collection Canvas without turning every stored state object into a canvas node.
_Avoid_: generic StateNode, invisible state mutation, canvas nodes for every state record

**Run Trace**: The technical event stream for one Run, including node lifecycle events, tool calls, inputs, outputs, artifact pointers, timing, token use, cost, and errors.
_Avoid_: treating trace as the authoring graph, using control evidence as a substitute for run execution details

**Device**:
A physical machine, virtual machine, browser host, or edge device that contributes execution capacity to the platform.
_Avoid_: Node, Worker

**Control Suggestion Node**: A Workflow node that produces a control suggestion and supporting evidence, often from an AgentRuntime Node or rule evaluation. It does not execute the action; execution must pass through the Actuator and produce Control Evidence Entries.
_Avoid_: agent-direct actuator execution, prompt-hidden automation, suggestions that bypass Advisory or Automatic Mode

**Gate Node**: A Control-family Workflow node that deterministically allows, blocks, pauses, or routes execution based on human approval, rules, permissions, quality thresholds, schema checks, resource readiness, or policy state. AgentRuntime Nodes may advise, but Gate Nodes express the boundary that permits flow to continue. Gate types include human, policy, quality, schema, resource, and mode gates.
_Avoid_: hiding approval or policy gates inside agent prompts, letting agent judgment directly mutate execution flow

**Imported Runtime Graph**: An external agent or workflow graph imported with its original runtime structure, state semantics, checkpoint behavior, and execution constraints preserved. It may come from systems such as LangGraph, LangChain, or Pi, but it must be observable and governable inside OpenCLI Admin.
_Avoid_: flattening external runtime semantics into ordinary Workflow nodes by default, treating imports as screenshots or decorative diagrams

**Runtime Package Node**: An Executable Canvas Node that wraps an Imported Runtime Graph or package-owned executable internals as one governable Workflow node. It exposes inputs, outputs, runtime binding, resource requirements, trace mapping, checkpoint mapping, and permission boundaries without forcing the imported graph to become native Workflow structure.
_Avoid_: fake compatibility nodes, uncontrolled foreign executors, expanding every imported node onto the Collection Canvas by default

**Runtime Capability Mapping**: The translation contract that maps an external runtime's tool calls, state, trace, checkpoints, interrupts, and control suggestions into OpenCLI Admin concepts. It internalizes operational capabilities without pretending every external graph is natively authored as an OpenCLI Workflow.
_Avoid_: shallow importer, visual-only compatibility, losing external runtime semantics during import

**Control Session**:
A time-limited, scope-limited, fully audited interactive terminal session opened by an authorized Control Action. Its commands and outputs are retained as evidence; an Agent receives one only through the same explicit authorization path as a human administrator.
_Avoid_: Raw SSH, permanent shell access, Actuator bypass

**Integration Package**:
A versioned, installable extension that adds collection, processing, delivery, or supporting service capabilities while keeping its upstream project independently upgradeable.
_Avoid_: Fork, copied dependency, plugin

**Managed Integration**:
An Integration Package whose external service can be deployed, configured, monitored, and upgraded by the platform as part of one system installation.
_Avoid_: Embedded source code, separately operated service

**Integration Catalog**:
The platform's curated set of official Integration Packages together with locally imported custom packages. It is not a public community marketplace.
_Avoid_: Marketplace, package store

**Local-first Data Plane**:
The platform boundary in which collected data, derived artifacts, indexes, and processing remain in the user's environment by default, while explicit cloud processing remains supported and visible.
_Avoid_: Offline-only system, local model only

**Verified Capability**:
A capability that a discovered local or cloud deployment has demonstrated through a runnable Workflow check. Discovery alone does not make a capability available to production nodes.
_Avoid_: Installed model, advertised feature

**Capability Requirement**:
A hardware- and vendor-neutral requirement declared by a Workflow Node for scheduling, such as browser session, network region, model function, memory, accelerator, or media support. The scheduler matches it only to a compatible Verified Capability.
_Avoid_: GPU model, fixed Device, machine role

**Capability Gap**: An explicit blocked gap produced when a Workflow or Imported Runtime Graph requires a capability that has no runnable mapping in the Capability Catalog, or whose availability is blocked by schema, dependency, resource, permission, or probe failure.
_Avoid_: failing import silently, pretending missing tools are runnable, deleting unsupported external graph structure

**Inbox**:
The user-facing view of unresolved requests that require attention or approval. It does not own a separate approval state.
_Avoid_: Approval database, notification log

**My Work**:
The personal Inbox view of items for which the current user is the required or assigned next actor.
_Avoid_: Notifications, all activity, personal backlog

**Project Triage**:
The project-level Inbox view of unresolved items that still need classification, acceptance, dismissal, or conversion into planned work. It remains useful when one person operates the Workspace.
_Avoid_: My Work, project notifications, issue tracker

**Project Context**:
The active Project filter and navigation context applied to shared platform modules. A Project aggregates its Workflows, recent results, Data Feeds, Automations, and Triage without duplicating global data-source, run, device, worker, or integration administration.
_Avoid_: Project admin console, nested workspace, duplicated module

**Data View**: The operator-facing surface for inspecting Records as fields and rows together with their Workflow, Run, Source Node, and task lineage. It presents what the collected data looks like and does not configure, enable, or catalog Sources.
_Avoid_: Source workspace, collection configuration page, per-source enablement list

**Record Acceptance Gate**: A Gate Node that decides whether a Record Candidate becomes a Record based on schema completeness, dedupe result, lineage preservation, quality threshold, review policy, and automatic-acceptance rules.
_Avoid_: normalize-implies-accepted, silently storing raw candidates as records, accepting records without lineage

**Runtime Artifact**: A non-record output produced during execution, such as a screenshot, HTML snapshot, trace attachment, LLM summary, diagnostic report, or checkpoint blob. It may support evidence or debugging without becoming a Record.
_Avoid_: forcing every artifact into records, sending diagnostic blobs as business results by default

**Artifact Transform Node**: A Transform-family Workflow node that explicitly converts Runtime Artifacts into Record Candidates, Run State, diagnostics, review material, or other typed outputs. Artifacts must pass through a typed transform before entering record or business-result flows.
_Avoid_: artifact-to-record shortcuts, untyped artifact edges, treating screenshots or HTML as records without extraction

**Merge Node**: A Workflow node that combines multiple upstream streams, candidates, records, or artifacts into a shared downstream segment while preserving input lineage and attribution. A merge failure belongs to Workflow Health, not to every upstream source's Source State.
_Avoid_: implicit fan-in, losing source lineage after merge, blaming all upstream sources for shared-segment failures

**Typed Port**: A typed input or output boundary on an executable node, such as Record Candidate stream, Record stream, Runtime Artifact stream, Run State patch, or Control Suggestion. Typed Ports let operators and AI compose Workflows without writing glue code or connecting incompatible flows.
_Avoid_: untyped canvas edges, prompt-only data contracts, letting artifacts flow as records without an explicit transform

**Merge Strategy**: The explicit strategy a Merge Node uses to combine compatible upstream flows, such as concat, key join, dedupe, priority, or windowed merge. The strategy never removes the need to preserve lineage.
_Avoid_: hidden merge behavior, accidental concat, dedupe that discards attribution

**Lineage**: The preserved origin chain for an output item, including source, node, run, tool call, artifact, and merge path references. It lets downstream records and failures remain attributable after fan-in.
_Avoid_: anonymous merged output, source attribution guessed after the fact

**No-Code Workflow Assembly**: The design goal that operators and AI should assemble useful collection Workflows from Business Capabilities, Typed Ports, presets, explicit strategies, and default gates without writing custom code for ordinary cases. AI-generated drafts should add gates around safety boundaries, external egress, control actions, low-level primitives, unverified resources, imported runtimes, and record acceptance.
_Avoid_: SDK-first workflow creation, requiring raw scripts for common collection and merge patterns, one-click Workflows without safety or quality gates

**Workflow Draft**: The mutable authoring graph proposed by an operator or Agent before publication. It may contain Capability Gaps, Draft Source Nodes, unbound resources, or unresolved port checks and cannot be executed as if it were a published Workflow Version.
_Avoid_: AI-generated runnable-looking fake Workflows, silently running drafts, mutable version used by an Automation

**Validated Workflow Draft**: A Workflow Draft whose executable nodes have validated runtime bindings, capability availability, typed-port compatibility, and required resource bindings. It may be published into an immutable Workflow Version; Runs and Automations execute that version rather than the mutable draft.
_Avoid_: running unverified graph drafts, treating palette presence as execution readiness, running the mutable draft authoritatively

**Workflow Change Proposal**: A proposed modification to an existing Workflow Draft, often generated by an Agent, that must show base revisions, the diff, capability and resource impact, and checkpoint or replay implications before approval.
_Avoid_: Agent directly mutating a published Workflow Version, hidden Workflow rewrites, stale proposal applied after conflicting edits

**Workflow Intent Entry**: A conversational or structured entry point where an operator describes a desired collection outcome and receives a Workflow Draft or Workflow Change Proposal. It is an intent surface, not a hidden Workflow editor.
_Avoid_: chat-only Workflow state, Workflows that exist only in an assistant transcript

**Capability Discovery Entry**: The search and recommendation surface for finding available Business Capabilities, Presets, and blocked gaps from the Capability Catalog. It helps assemble Workflows but does not replace the Collection Canvas.
_Avoid_: static raw node palette, frontend-only capability menus

**Packaged Node Preset**: A ready-to-place node package that wraps a Business Capability with default parameters, resource hints, output ports, labels, tags, probes, and safety limits. A multi-source preset expresses each source as an internal atomic Canvas Source Node with its own Source Binding rather than storing several source configurations on one node. In the Palette and Canvas it is the operator-facing "封装好的节点"; the underlying capability remains catalog-owned.
_Avoid_: treating presets as only saved form params, hardcoded frontend nodes, presets without capability ownership

**Node Preset Family**: A stable grouping for Packaged Node Presets by workflow role: Source, Transform, Flow, Sink, Control, or Runtime Package. AgentRuntime Nodes belong to Control; imported LangGraph, LangChain, Pi, or package-owned graphs belong to Runtime Package. Families keep the palette extensible as new nodes are added.
_Avoid_: organizing presets only by implementation technology, one flat node list, mixing sources and sinks under adapter names

**Node Onboarding Path**: The standard path for adding a new node: define the Business Capability, declare its Capability Manifest, implement the runtime binding, add probes and availability checks, package a Packaged Node Preset, assign a Node Preset Family, and let the Canvas discover it.
_Avoid_: frontend-first node cards, palette entries without runtime bindings, adding nodes outside the Capability Catalog

**Canvas Approval Surface**: The authoritative surface where Workflow Drafts, Capability Gaps, and Workflow Change Proposals are reviewed, edited, validated, and published. Agent output must land here before it can affect an executable Workflow Version.
_Avoid_: approving workflow changes only in chat, hidden mutations outside the canvas

**Execution Resource**: Workspace-visible compute and runtime capacity that can execute Workflow work, such as a Worker, Runner, host, runtime environment, or capacity pool. The Execution Resources surface reports availability, capabilities, placement, load, and health; it does not contain Workflow nodes, Agent work ownership, credentials, browser sessions, or business accounts.
_Avoid_: Workflow node, Agent Collaboration Topology, credentials or cookies presented as infrastructure

**Runtime Binding**: A governed reference that gives a Workflow node or Agent Deployment access to required session state, profile binding, account context, or other non-compute runtime dependency. Secrets remain isolated behind Connections or the relevant credential store and are never copied into node parameters.
_Avoid_: hand-filled cookie params, credentials hidden in node params, treating a Runtime Binding as an Execution Resource

**Execution Resource Topology**: An optional analytical view of execution infrastructure and its operational relationships, used when a workspace has enough Workers, Runners, hosts, runtimes, or dependencies that a flat status view no longer explains placement, reachability, capacity, or failure impact. The default Execution Resources surface remains an operating view; topology is not a replacement for the Workflow Canvas or Agent Collaboration Topology.
_Avoid_: making every resource list a graph, mixing Workflow nodes or Agent work ownership into infrastructure topology

**Two-Tier Attribution**: The observability contract for Workflows. A source node is a real Data Source and its collection segment keeps per-source measurement unchanged; everything downstream of a merge belongs to Workflow Health, and a shared-segment failure is never written into any source's state.
_Avoid_: blaming all upstream sources, Workflow-only attribution

**Workflow Health**: The health of a Workflow's shared post-merge segments, measured per Workflow node and kept as its own dimension beside per-source measurement. A dedupe node failing does not make its upstream sources degraded.
_Avoid_: folding Workflow failures into Source State

**Preset**: A packaged, one-click node configuration (e.g. an OpenCLI site + command + format bundled as "雪球·热帖") registered in the Capability Catalog and searchable from the palette. Presets are fed from backend adapter metadata, never hardcoded in the frontend. The advanced inspector still exposes raw parameters.
_Avoid_: raw site/command dropdowns as the default UI

**Draft Source Node**: A source node placed on the Collection Canvas that does not yet reference a real Data Source. It renders visibly unmaterialized, cannot run, and does not enter the control loop until it is materialized into an entity.
_Avoid_: fake canvas-only nodes that look real

**Dry-Run Preview**: A backend-executed preview of a Workflow Draft on fixture or explicitly bounded sample data, displayed in the browser and labeled as non-production. It never produces authoritative collection or delivery results; authoritative Runs execute a published Workflow Version.
_Avoid_: browser-side "real" Runs, split-brain execution, preview with ungoverned external side effects

**External Agent Consumer**:
An Agent or tool that consumes processed internet data without operating or maintaining the collection platform.
_Avoid_: Operations Agent, collector

**Agent Session**:
A continuous Agent conversation that is independent of Run identity. A Run uses a fresh Session by default, while an Automation may explicitly reuse one across Runs.
_Avoid_: Run, agent process

**Agent Invocation**:
The participation of an Agent Session in a specific Run and Agent Node execution.
_Avoid_: Agent Session, Run

**Artifact**:
An immutable output or evidence object that can be referenced by multiple Runs without being copied or rewritten.
_Avoid_: Mutable file, run attachment

**Data Feed**:
A published, permissioned data product with a stable contract through which processed collection results can be queried, subscribed to, replayed, or pushed to external consumers. It may combine governed outputs from one or more Projects without turning those Projects into external Sources.
_Avoid_: Workflow output, raw record, delivery channel

**Data Subscription**:
An independently configured consumer of a Data Feed with its own protocol, filter, cursor, retry, and delivery state. Adding a consumer does not modify or rerun the producing Workflow.
_Avoid_: Workflow branch, duplicated collection, feed copy

**Query Request**:
A freshness-bounded request from an Agent or application for existing or newly collected data. It searches eligible Data Feeds and indexes first, triggers an authorized Workflow when data is missing or stale, may stream partial results, and returns provenance rather than inventing missing facts.
_Avoid_: Chat answer, separate search engine, ungrounded prompt

**Coverage Policy**:
A Project or Data Feed constraint defining required source classes, regions, languages, time range, and independent-source minimums. Query and collection Nodes continue authorized collection when coverage is insufficient and report remaining gaps explicitly.
_Avoid_: Search ranking, source list, completeness claim

**Finding**:
An evidence-backed observation of an anomaly, coverage gap, risk, or optimization opportunity. Policy routes it to the Overview, Inbox, an Agent conversation, or an authorized low-risk Control Action; a Finding is not itself a notification or approval request.
_Avoid_: Alert notification, Gate Request, Agent opinion

**Collected Record**:
A captured unit of internet data that preserves its source metadata and original content or media Artifact.
_Avoid_: Processed result, summary

**Derived Representation**:
An immutable text, media, or structured form produced from a Collected Record with traceable Run, node, tool, and model provenance.
_Avoid_: Overwritten record, normalized truth

**Tool**:
A callable capability supplied by an Integration Package for collection, processing, delivery, or operation.
_Avoid_: Plugin, node

**Node**:
The product form through which a Tool participates in a Workflow. A Node may encapsulate multiple internal steps without exposing an additional product concept.
_Avoid_: HDA, recipe, composite

**Custom Node**:
A Workspace-owned Node definition created when a user explicitly saves changes to an installed Node's internal implementation. It is reusable across Projects and versioned independently from the installed Node.
_Avoid_: Modified official node, project node

**Worker**:
An execution process running on a Device that performs collection, browser, model, or processing work.
_Avoid_: Device, Node, Agent

**Control Plane**:
The API and coordination services that own configuration, scheduling, policy, Gate state, Run metadata, and Worker registration. Its web frontend is a client and may be deployed separately.
_Avoid_: Frontend, management Mac, Worker

**Execution Plane**:
The pool of authenticated Workers that lease tasks and provide verified browser, collection, model, media, or processing capabilities. Workers may run on desktops, servers, NAS devices, cloud hosts, or edge devices independently of the Control Plane and storage location.
_Avoid_: Mac cluster, frontend host, SSH fleet

**Worker Connection**:
The authenticated outbound channel through which a Worker registers, reports health and capabilities, leases tasks, and returns results to the Control Plane. A private overlay network is optional, and SSH is reserved for installation, maintenance, or a governed Control Session rather than routine scheduling.
_Avoid_: SSH scheduler, LAN trust, network reachability

**Overview**:
The global operational view organized around Project data production, freshness, failures, Data Feeds, Automations, and cross-project anomalies, with only a small system-wide summary above it.
_Avoid_: Infrastructure dashboard, second monitoring product, metric wall

**Gate Request**:
The single pending fact that records why a Run requires approval before it may continue. Approval is limited to the request's exact action, target, parameters, permissions, and validity period.
_Avoid_: Inbox item, approval notification

**Gate Decision**:
The single recorded outcome of a Gate Request.
_Avoid_: Inbox action, run status
