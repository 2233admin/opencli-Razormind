---
title: 'ResearchGraph Workflow Plugin Seam'
type: 'refactor'
created: '2026-08-29'
baseline_commit: 'b9ec317efda601322f6c314af8d18b5700b7aeb0'
status: 'in-progress'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** ResearchGraph is presently hard-wired into generic workflow event persistence, tracing, router assembly, and the frontend run panel. The core consequently understands `details.researchGraph`, imports ResearchGraph schemas, and statically loads the graph UI, preventing an operator from disabling or replacing that feature without changing core behavior.

**Approach:** Establish an explicit, lifecycle-aware workflow-plugin seam. A real ResearchGraph adapter will register event contribution, validation/projection, API routes, and a run-panel extension through generic contracts; a test adapter will prove replacement. The default enabled installation preserves existing ResearchGraph behavior, while an empty/disabled installation leaves legacy generic workflow execution and frontend compilation independent of ResearchGraph.

## Boundaries & Constraints

**Always:** `WorkflowRunEvent` remains the single append-only authority and generic event fields remain opaque to core. `workflow_runtime.py`, `workflow_run_events.py`, `opencli_hda_tracer.py`, `api/v1/__init__.py`, `studio.py`, `schemas/workflow.py`, and `run-trace-panel.tsx` may import only the generic workflow-plugin/run-panel-extension seam: they must contain no ResearchGraph imports, types, payload-key checks, UI state, loading, or rendering. Plugin registration is explicit, ordered, atomic, registry-instance-local, and test-isolated; there are no decorator or import-time side effects. Startup is idempotent, pre-start shutdown is safe, and shutdown reverses only started adapters. Route assembly completes before `create_app` returns; lifespan only starts/stops adapters. The production ResearchGraph adapter owns schema parsing, lineage contribution, transcript validation, projection, mutations, scoped/unscoped routes, and panel extension. Default configuration enables that adapter so existing public routes and workflows retain their behavior; explicitly disabling it contributes no plugin routes, events, projections, or UI extension.

**Ask First:** Adding a dependency, a second persistence authority, a new authorization policy, changing existing route URLs or default enablement, or changing meanings of legacy workflow events.

**Never:** Add a directory-only “plugins” label without contracts and injection; use decorator side effects or unconditional domain imports as registration; make core serialize/deserialise ResearchGraph facts; let a disabled plugin break normal run creation/replay/build; mutate historical events; overwrite unrelated dirty files or baseline data.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Default real adapter | ResearchGraph adapter configured and application assembled | Its routes, lineage contributor, projection/mutation service, and panel extension are registered; existing graph APIs replay identically | Existing validation/conflict responses remain unchanged |
| Explicitly disabled | Empty/disabled plugin configuration | Core app builds and ordinary workflow runs/replay work; graph routes, contributions, and UI extension are absent | No import-time domain failure or leaked graph payload validation |
| Replacement adapter | Isolated registry with a test adapter for the ResearchGraph key | Registry dispatches the replacement for its declared capabilities and records lifecycle calls | Duplicate/unknown keys fail deterministically without partially registering |
| Lifecycle boundary | Start twice, stop without start, then stop started adapters | Start is idempotent; pre-start stop is a no-op; started adapters stop in reverse order | One adapter failure is surfaced with context and does not corrupt registry state |
| Core import guard | Core and panel source modules | AST/import sweep finds only generic seam dependencies, never ResearchGraph types, keys, or UI ownership | Test fails on any forbidden direct coupling |
| Isolated concurrent registries | Two registry instances and a failing registration attempt | Registries do not share entries; failed batch registration leaves its target unchanged | Counters prove only declared replacement capabilities execute and real-adapter counters remain zero |

</frozen-after-approval>

## Code Map

- `backend/workflow/workflow_run_events.py` -- generic allocator currently imports and branches on ResearchGraph; replace this hard-coded validation with a generic plugin dispatch point.
- `backend/workflow/opencli_hda_tracer.py` -- currently invokes ResearchGraph lineage directly after operator output; emit plugin contributions through the generic registry instead.
- `backend/workflow/research_graph.py`, `research_graph_lineage.py`, and `research_graph_mutations.py` -- move behind the real adapter; retain deterministic fold, lineage, allocator use, and conflict semantics.
- `backend/api/v1/__init__.py`, `backend/api/v1/studio.py`, and `backend/api/v1/research_graph_routes.py` -- replace unconditional graph router inclusion with adapter route contribution while retaining current enabled paths.
- `backend/main.py` -- integrate generic registry startup after migrations and ordered shutdown before process resources close.
- `backend/agent_runtimes/registry.py` and `backend/acquisition/registry.py` -- reuse explicit lookup/snapshot conventions, not eager decorator registration.
- `frontend/components/flow/run-trace-panel.tsx` -- remove ResearchGraph client/hook/readout ownership; render a typed, optional extension slot only.
- `frontend/components/flow/workflow-editor-overlays.tsx` -- inject configured run-panel extensions at the existing panel composition point.
- `frontend/components/flow/research-graph-readout.tsx`, `use-research-graph.ts`, and `frontend/lib/workflow/research-graph-client.ts` -- become implementation details of the real ResearchGraph run-panel adapter.
- `frontend/lib/plugins/backend-plugin-catalog.ts` and `use-opencli-adapter-registry.ts` -- existing enabled-gated optionality conventions for frontend configuration.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/workflow/workflow_plugins.py` -- define domain-neutral adapter, event contribution/validation/projection, route contribution, lifecycle, explicit registry, configuration selection, and test snapshot APIs -- creates the real seam without core domain imports.
- [ ] `backend/workflow/workflow_run_events.py` and `backend/workflow/opencli_hda_tracer.py` -- dispatch registered adapters for candidate-event validation and post-operator event contribution -- removes `researchGraph` checks and direct lineage imports from core paths.
- [ ] `backend/workflow/research_graph*.py`, `backend/api/v1/research_graph_routes.py`, `backend/api/v1/__init__.py`, `backend/api/v1/studio.py`, and `backend/main.py` -- implement/register the production adapter, contribute existing routes, and wire lifecycle/configuration -- preserves enabled endpoints and makes disablement/replacement real.
- [ ] `frontend/lib/workflow/run-panel-extensions.ts`, `frontend/components/flow/run-trace-panel.tsx`, `frontend/components/flow/workflow-editor-overlays.tsx`, and a ResearchGraph adapter module -- provide typed optional extension composition and move all graph state/loading/mutation/readout ownership out of core -- makes an empty list the legacy frontend path.
- [ ] `tests/unit/test_workflow_plugins.py`, existing ResearchGraph API/unit tests, and `frontend/scripts/check-workflow-regressions.mjs` -- add AST/import sweeps for protected core modules, enabled/disabled route snapshots, ordinary compile/run/replay under disablement, real/replacement counters, atomic/two-registry isolation, lifecycle failure/reverse/idempotent behavior, and enabled/empty panel composition -- proves a real seam rather than source-only scaffolding.

**Acceptance Criteria:**
- Given the default application assembly, when a ResearchGraph mutation or scoped graph request is issued, then the existing API behavior and event-backed deterministic projection remain available through the registered real adapter.
- Given ResearchGraph is explicitly disabled, when ordinary workflows are compiled, run, and replayed, then generic core paths do not import or invoke ResearchGraph and the frontend panel builds/renders without a graph extension.
- Given an isolated registry containing a test adapter for the same capability, when routes/events/projection are dispatched, then the test adapter receives only its declared callbacks and the real adapter is not invoked.
- Given adapter lifecycle calls, when start/stop are repeated or stopped before start, then behavior is idempotent and registry state remains reusable by later tests.
- Given the protected core modules and an empty frontend extension list, when AST/import and frontend compile checks run, then no ResearchGraph type, key, state, loader, renderer, or direct dependency survives outside the registered adapters.
- Given enabled and disabled app assemblies, when route snapshots and ordinary compile/run/replay are exercised, then enabled legacy graph paths are byte-for-byte path-compatible while disabled assemblies have neither graph route family and ordinary workflows remain usable.
- Given two registries plus a replacement adapter and a registration/lifecycle failure, when dispatch and start/stop execute, then only declared replacement counters advance, real-adapter counters stay zero, registration is atomic, registries remain isolated, and stop order is reverse/idempotent.

## Design Notes

The seam is capability-oriented rather than a generic service locator: adapters receive generic event/run context and return generic event envelopes or UI extension components. The append allocator persists opaque details after registered candidate validation; the ResearchGraph adapter owns decoding `researchGraph` and can therefore be absent without a core type dependency. Router contribution completes at application construction; lifespan is reserved exclusively for ordered adapter start/stop.

## Verification

**Commands:**
- `uv run pytest --no-cov tests/unit/test_workflow_plugins.py tests/unit/test_research_graph.py tests/unit/test_workflow_run_events.py tests/integration/test_workflow_deep_research_api.py tests/integration/test_trigger_scoped_workflow_execution.py` -- expected: real, disabled, replacement, lifecycle, route, event, and projection contracts pass.
- `node --test --test-name-pattern=ResearchGraph scripts/check-workflow-regressions.mjs` -- expected: enabled adapter injection and empty-extension legacy panel checks pass.
- `npx tsc --noEmit` -- expected: the core panel compiles without a ResearchGraph import.
- `npm run test:workflow-contracts && npm run build` -- expected: frontend contracts and production build pass.
