# PRD: Dify P0 Compatibility Runtime and Manifest Intake

Status: ready-for-implementation

Date: 2026-07-21

Owner: OpenCLI workflow and plugin platform

Grounding:

- Current Dify import: `frontend/lib/workflow/dify-translator.ts:42-127`
- Workflow compile seam: `backend/workflow/compiler.py:121-200`
- Runtime binding registry: `backend/workflow/runtime_registry.py:36-53,85-150`
- Run/event projection: `backend/workflow/opencli_hda_tracer.py:213-390,1240-1363`
- Plugin direction: `docs/adr/0014-datafoundry-backed-analysis-as-an-optional-plugin.md`, `docs/adr/0015-plugin-ui-is-declarative-and-platform-rendered.md`, `docs/adr/0019-locked-plugin-node-definitions-and-project-owned-derivatives.md`
- External runtime boundary: `docs/adr/0010-native-runtime-nodes-and-managed-external-graphs.md:1-17`
- Architecture decision: `docs/adr/0026-run-dify-workflows-through-a-pinned-graphon-sidecar.md`

## 1. Problem

The product currently says it can import Dify workflows, but the imported graph is not executable as Dify. The browser creates one compatibility package and preserves a visual internal graph, while model nodes use mock adapters, external nodes use fixtures, and network access is disabled (`frontend/lib/workflow/dify-translator.ts:188-220`).

The plugin center has the opposite problem: it presents provider metadata from a frontend constant (`frontend/lib/plugins/provider-catalog.ts:22-39`) but has no backend installation record or Dify manifest intake. This makes “available”, “installed”, “configured”, and “executable” easy to confuse.

P0 must prove two independent capabilities without creating another workflow product:

1. A supported Dify workflow can run inside the existing OpenCLI workflow/run/event system.
2. A Dify plugin package can be imported as declarative capability metadata and honestly report what is or is not executable.

## 2. Product outcome

After P0:

- A user imports a real Dify `workflow` or `advanced-chat` DSL.
- Studio shows one expandable Dify compatibility package with the original internal node identities.
- Compile performs a real Graphon inspection and shows structured blockers before execution.
- A supported workflow runs through Graphon and its nested node states appear in the existing Canvas and run trace.
- A user can upload a Dify `manifest.yaml` or `.difypkg`; the plugin center shows the installed provider, declared capabilities, permissions, signature state, and runtime state.
- Importing a manifest never implies that its Python/Node code can run. Missing runtime, model, credential, sandbox, network permission, or tool adapter is shown as `BLOCKED`.

## 3. Product model

The following concepts must remain distinct:

| Concept | Domain role | Is a Canvas node? |
|---|---|---|
| Plugin installation | Installed package metadata, version, trust and permissions | No |
| Provider configuration | Endpoint, credential references, enabled models/tools | No |
| Runtime resource | Local/LAN execution capacity and health | No |
| Plugin node definition | Locked reusable executable definition projected by a plugin | Catalog definition only |
| Dify workflow package | One managed external graph selected in a workflow | Yes |
| Imported Dify internal node | Read-only nested status/source anchor inside the package | Nested visual/event address |
| Workflow run | One execution and its projection/events | No |

This follows `docs/adr/0017-only-executable-flow-steps-are-workflow-nodes.md:1-13` and `docs/adr/0019-locked-plugin-node-definitions-and-project-owned-derivatives.md:1-13`.

## 4. P0 scope

### 4.1 Dify DSL intake

- Accept JSON or YAML with `kind: app`.
- Accept app modes `workflow` and `advanced-chat`.
- Reject configuration-only app modes as unsupported with a stable error code.
- Preserve:
  - app name, mode, DSL version and description;
  - original node ids, types, titles, positions and edges;
  - a canonical source payload, SHA-256 digest and source format;
  - the Graphon compatibility version used for inspection.
- Limit source payload to 1 MiB in P0.
- Never place source payload or ephemeral credentials into run event details.

### 4.2 Managed package compilation

- Add an explicit managed-package execution marker.
- Native HDA packages continue to expand into internal compiled nodes.
- Dify managed packages compile as one executable runtime node.
- Their internals remain available to Canvas and source attribution but are not independently dispatched by the native runtime.
- Compile calls Graphon inspection and converts dependencies/blockers into existing structured compile/runtime blocker shapes.

### 4.3 Graphon sidecar

- Pin Graphon to version `0.7.0`, commit `b187ce7927fea1a7c137b642be3f78e3abb9f7de`.
- Pin Dify Plugin Daemon Slim to tag `0.6.5`, commit `14877f8f8b6dd63d3cec760411a875cc8e077547` when model execution is enabled.
- Use Python 3.13 in the sidecar.
- Expose health, inspect, start, event replay and cancel endpoints.
- Enforce request size, execution timeout, output size and concurrency limits.
- Run with no Docker socket and a read-only filesystem except a bounded temporary directory.
- Default policy: network denied, code denied, tool denied.
- Network is enabled only when both the workflow permission and sidecar policy allow it.
- Code nodes remain blocked in P0 because Dify Sandbox is P1.

### 4.4 Runtime/resource binding

- Add runtime binding id `workflow.compat.dify.graphon`.
- Treat the sidecar as an execution resource with `local`, `lan`, and unavailable states, using the same “resource missing means blocked” behavior as other runtime resources.
- P0 only wires the local Docker Compose resource. The contract must not assume localhost so the same adapter can later target a LAN worker.
- Reuse saved OpenCLI model providers for endpoint/model/credential selection.
- Resolve credentials into an ephemeral grant immediately before dispatch.

### 4.5 Event and output projection

Map Graphon events into existing OpenCLI events:

| Graphon meaning | OpenCLI event |
|---|---|
| graph/node scheduled | `queued` |
| node entered | `started` |
| stream chunk | `partial` |
| tool invocation entered/exited | `tool_call_started` / `tool_call_completed` |
| node succeeded | `completed` |
| dependency/policy unavailable | `blocked` |
| node or graph failed | `failed` |

Every nested event uses:

- `nodeId`: stable composite id used by the projection;
- `nodePath`: `[packageNodeId, difySourceNodeId]`;
- `packageNodeId`: outer Dify package id;
- `internalNodeId`: original Dify source node id;
- `details.runtime`: `graphon`;
- `details.runtimeRunId` and `details.runtimeSequence`;
- redacted output preview, counts, and artifact reference where applicable.

The current event schema and projection already support nested locations and dynamic node states (`backend/schemas/workflow.py:723-779`, `backend/workflow/opencli_hda_tracer.py:1299-1363`).

### 4.6 Dify plugin Manifest intake

Inputs:

- standalone `manifest.yaml`;
- `.difypkg` ZIP created by the Dify CLI.

Security rules:

- maximum compressed size: 50 MiB;
- maximum entries: 2,000;
- maximum uncompressed total: 200 MiB;
- reject absolute paths, `..`, symlinks and duplicate normalized paths;
- read metadata files in memory; do not extract executable content;
- record `unsigned` or `present_unverified`; do not claim cryptographic verification in P0;
- an invalid archive or manifest is rejected atomically.

Persist one installation record with:

- stable provider key: `{author}/{name}`;
- source kind, digest, version and manifest spec version;
- labels/descriptions/icons;
- declared plugin types;
- declared permissions and required credentials;
- signature state;
- raw sanitized manifest;
- projected locked node definitions;
- runtime status and blocker list.

P0 recognizes the Dify manifest capability families:

- Tool
- Model
- Datasource
- Trigger
- Agent Strategy
- Endpoint/Extension

Only capabilities with a registered OpenCLI runtime adapter can be `READY`. All others are installed metadata with `BLOCKED` status. Plugin installations and provider configurations remain domain objects, not nodes.

### 4.7 Plugin center projection

- Replace the frontend-only installed state with a backend catalog response.
- Keep bundled OpenCLI providers as seed registrations returned by the same API.
- Add local-file import for Dify manifest/package.
- Provider detail shows declared capabilities, version, trust/signature state, permissions and blockers.
- Studio palette consumes locked node definitions from the capability endpoint.
- The plugin center does not render arbitrary plugin UI and does not expose a second source workbench.

## 5. P0 supported execution matrix

| Dify node family | P0 state | Notes |
|---|---|---|
| start, end, answer | Ready | Required acceptance path |
| if-else, template-transform, variable aggregator/assigner, list operator, parameter extractor | Ready when Graphon inspection says supported | Pure graph execution |
| HTTP request | Guarded | Requires workflow network permission and allowed domain |
| LLM | Guarded | Requires Graphon Slim resource plus matching OpenCLI model provider |
| tool | Blocked by default | Ready only after an explicit OpenCLI tool adapter mapping exists |
| code | Blocked | Dify Sandbox is P1 |
| knowledge retrieval | Blocked | Knowledge/RAG is not part of P0 |
| document/file operations | Blocked unless a file adapter is configured | No implicit host filesystem access |
| human input | Blocked | HITL mapping is later work |
| unsupported/unknown type | Blocked | Never silently downgraded to mock/fixture |

## 6. API contracts

### Workflow import/inspection

Add:

- `POST /api/v1/workflows/import/dify`
  - request: source string plus optional name;
  - response: `WorkflowProject`, translation report, inspection report.
- Existing `/api/v1/workflows/compile` remains the compile entrypoint.
- Existing `/api/v1/workflows/runs`, `/events`, `/events/stream`, `/trace` and `/projection` remain the run/result surface (`backend/api/v1/workflows.py:227-590`).

Stable blocker codes:

- `dify_app_mode_unsupported`
- `dify_source_too_large`
- `dify_source_digest_mismatch`
- `dify_graphon_unavailable`
- `dify_node_unsupported`
- `dify_network_permission_required`
- `dify_model_provider_required`
- `dify_slim_runtime_required`
- `dify_tool_adapter_required`
- `dify_sandbox_required`
- `dify_runtime_failed`

### Plugin catalog

Add:

- `GET /api/v1/plugins`
- `GET /api/v1/plugins/{installationId}`
- `POST /api/v1/plugins/import/dify` as multipart upload
- `DELETE /api/v1/plugins/{installationId}`

Uninstall fails with `409` when a durable workflow draft references a projected node definition. P0 does not silently rewrite projects.

## 7. Acceptance criteria

P0 is complete only when all of the following are demonstrated:

1. A committed fixture containing `start -> template-transform -> if-else -> answer/end` imports through the backend endpoint and produces one managed `package.compat.dify-workflow`.
2. Compile emits one executable package binding and does not compile imported internal nodes as native executors.
3. The fixture executes in the pinned Graphon sidecar and produces persisted OpenCLI nested node events in strictly increasing sequence order.
4. Run projection reconstructs package and internal-node status after the API process is restarted or events are reloaded from the database.
5. A second fixture containing `start -> llm -> answer` either completes using a configured OpenAI-compatible provider or returns the exact model/Slim blockers; it never runs in mock mode.
6. An HTTP fixture is blocked while network permission is off and completes against a local fixture server when permission and domain policy are enabled.
7. A code-node fixture returns `dify_sandbox_required`.
8. A valid Dify manifest and a valid `.difypkg` install as metadata and appear through `GET /api/v1/plugins`.
9. A malicious ZIP-slip package, oversized package, invalid YAML and duplicate provider/version are rejected with stable 4xx errors.
10. Imported plugin capabilities without adapters appear as `BLOCKED`; none are advertised as executable.
11. The plugin page reads installed state from the backend, and Studio sees projected locked node definitions without loading plugin-owned frontend code.
12. Existing Dify import regression, workflow compile, run projection, EvidenceBatch and control-plane tests remain green.

## 8. Non-goals

- Copying Dify’s frontend, app builder, tenant model, marketplace UI, branding or full API backend.
- Full Dify Plugin Daemon installation/lifecycle or arbitrary third-party plugin execution.
- Dify Sandbox/code execution.
- Knowledge base/RAG ingestion.
- Marketplace synchronization or automatic GitHub installation.
- Dify agent strategies and human-input workflows.
- Replacing native OpenCLI collection, RSS/API sources, cookies/profiles, workers, model providers, notifications or Agent runtimes.
- Flattening the Dify graph into native OpenCLI nodes.
- Moving Graphon work to LAN GPU nodes in P0; only the resource contract is reserved.

## 9. Upstream and license record

| Component | P0 use | Pin | License/source |
|---|---|---|---|
| Graphon | Dify DSL inspection and graph execution | `0.7.0` / `b187ce7927fea1a7c137b642be3f78e3abb9f7de` | Apache-2.0, https://github.com/langgenius/graphon |
| Dify Plugin Daemon Slim | Slim-backed LLM runtime helper | `0.6.5` / `14877f8f8b6dd63d3cec760411a875cc8e077547` | Apache-2.0, https://github.com/langgenius/dify-plugin-daemon |
| Dify Plugin SDK schema/docs | Manifest interpretation reference | compatible schema `meta.version` | Apache-2.0, https://github.com/langgenius/dify-plugin-sdks |

The Dify main application repository is a reference only. P0 does not copy its frontend or application code.

## 10. Delivery order

1. Sidecar dependency pin and contract fixture.
2. Backend Dify import/inspect boundary and canonical source payload.
3. Managed package compile binding and blocker projection.
4. Runtime execution, event mapping and output projection.
5. Manifest/package intake and backend plugin catalog.
6. Plugin page/Studio wiring plus full acceptance run.

The issue pack in `docs/dify-p0-compatibility-runtime-issues/` is the executable handoff for the next development session.
