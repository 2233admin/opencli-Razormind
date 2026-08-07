# Agent execution experience

## Goal

Make every AI-driven operation legible as a real, live run: what is being understood, which object is being inspected or changed, what needs approval, what changed, and how to recover.

## Surfaces

- Global Agent dock on desktop and mobile.
- Chat reads and governed writes.
- Workflow and Operations Agent run adapters.
- Embedded live browser when an event exposes a browser surface.

## Constraints

- Never expose chain-of-thought, raw logs, credentials, or opaque payload dumps.
- Preserve Workspace authorization, governed confirmation, login, and Bootstrap Admin.
- Reuse current Next.js, FastAPI, query cache, run events, and noVNC surfaces.
- Production build and Docker deployment are acceptance gates.

## Acceptance

- Progress changes are driven by backend events, not timers.
- Tool events identify an action and target in human language.
- Writes pause before mutation and resume only after confirmation.
- Failure states offer safe retry or re-plan actions.
- Successful writes visibly synchronize the current software view.
- The terminal event contains a concise, evidence-based summary.
