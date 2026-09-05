# OpenAlice UI adoption provenance

## 2026-09-06: recoverable work navigation

The controlling product document remains
[the platform PRD](../opencli-agent-data-operations-platform-PRD.md).
The existing umbrella issue [#116](https://github.com/2233admin/opencli-Razormind/issues/116)
continues to track broader adoption; this increment does not close it.

`frontend/lib/work-tabs.ts` and `frontend/components/shell/work-tabs.tsx`
apply the open-or-focus, adjacent-close, and active-view-only behavior examined in
[OpenAlice's store](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/tabs/store.ts)
and [TabHost](https://github.com/TraderAlice/OpenAlice/blob/52b51f29809178594b7b57bf666133829368b7b4/ui/src/components/TabHost.tsx).
They use the existing Next router and do not create a second workspace, execution
store, background page tree, or session runtime. Closing a tab makes no API mutation.
No upstream implementation was copied: the inspected upstream LICENSE is AGPL-3.0,
while this repository's LICENSE is Apache-2.0. Any future verbatim imports need their
own explicit provenance and license treatment; this record does not relicense either project.

Tabs retain project, workflow, run, trace, conversation and record navigation IDs.
They are bounded to 16 and stored separately per authenticated identity and workspace.
Restoration reparses only allowlisted product routes and context parameters; persisted
IDs and labels are recomputed. Page APIs still validate object access. Storage denial
does not prevent navigation. The current URL wins over restored history; restored
tabs do not automatically launch conversations or executions.

### Remaining product gaps observed in source

- Project sessions and inline Inbox replies already have local implementations;
  their presence is not evidence of full live-model acceptance.
- The data page's `ProjectInputsView` groups source records and has disabled upload.
  It is not a file/artifact viewer. OpenAlice's `FileViewerPage` and shared
  `FileContentView` are relevant design references, but need a real authorized
  artifact read contract before being claimed as delivered here.
- `RunContextBanner` explicitly says data is project-scoped, not run-filtered.
  Remembering a run in navigation does not implement run-filtered result queries.
- The upstream PTY runtime, financial connectors and directory/Git workspace store
  are not replacements for the PRD's governed Project, Connection, Proposal and Run
  objects. Reuse evaluations must bind those capabilities to the existing contracts.

These are gaps for #116, not a declaration that all OpenAlice capabilities were imported.

This bounded adaptation draws on the OpenAlice snapshot at
`52b51f29809178594b7b57bf666133829368b7b4`, read from the repository's
research checkout. No OpenAlice source file was copied.

- `ui/src/components/PageSidebarLayout.tsx` informed
  `frontend/components/experience/context-page-layout.tsx`: a semantic
  main/context split that keeps the current Base UI and Tailwind stack, with
  narrow screens reading the context before the canvas.
- `ui/src/components/workspace/WorkspaceView.tsx` informed the searchable
  session selector in the global Agent dock. It preserves the project's server
  conversations and router rather than bringing over OpenAlice terminal or tab
  lifecycle code.
- `ui/src/components/workspace/CreateWorkspaceForm.tsx` and
  `ui/src/hooks/useCreateWorkspace.ts` informed the rule that creation surfaces
  must share durable submission semantics. OpenCLI continues to use its existing
  bootstrap and draft revision APIs because its Project and Workspace have
  different authorization meanings.

- `ui/src/components/workspace/useReorderMotion.ts` informed the small
  `use-reorder-motion.ts` FLIP hook used by Inbox queues. OpenCLI uses its own
  180ms response duration and easing, cancels obsolete animations, and responds
  to reduced-motion changes while an animation is running.
- The Inbox origin and inquiry flow informed links from verified work-item
  evidence to the original conversation and project. Backend-stamped IDs remain
  subject to existing Workspace permissions; arbitrary evidence URLs are never
  treated as navigation destinations.
- Agent project writes and manual Studio writes share bootstrap/draft services.
  This adapts OpenAlice's single work-definition principle to existing database
  models, proposal confirmation, and optimistic draft revisions.

Source links are pinned in [the original assessment](openalice-reuse-assessment.md).
Delivered behavior and verification are recorded in
[the integration report](../verification/openalice-product-journey.md).
