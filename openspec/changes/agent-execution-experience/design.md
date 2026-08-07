# Design

Project foundation: `../../../DESIGN.md`.

Use a single quiet run timeline inside the existing dock. The active item has a small pulse; completed, approval, and failure states use semantic icons and text. Each item is a public execution fact, never hidden reasoning.

## Event model

`run.started`, `phase.changed`, `tool.started`, `tool.completed`, `approval.required`, `run.completed`, and `run.failed`. Each event may include a public label, detail, target, result summary, recovery action, and surface reference. Raw tool arguments and results remain server-side.

## Components

- Run header: user goal and current status.
- Timeline: ordered public activity events.
- Target card: object type/name and intended effect.
- Approval card: summary, user-facing change, approve/reject.
- Live surface: app refresh receipt or same-origin noVNC frame when provided.
- Result card: outcome, evidence counts, duration, and next action.

## Accessibility

Timeline uses an ordered list and polite live announcements. Approval buttons remain keyboard reachable. Errors use `role=alert`. Focus moves only for explicit approval or recovery actions.
