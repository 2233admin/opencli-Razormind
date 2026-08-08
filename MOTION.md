---
schema: design-pipeline.motion-foundation.v0.1
name: OpenCLI operator motion language
posture: minimal
primitiveRegistry: design-pipeline.motion-primitives.v1
---

## Motion Thesis

Motion confirms a completed operator action or a changed system state. It never delays access to credentials, recovery, or operational data.

## Motion Principles

- Keep authentication transitions short, interruptible, and secondary to the active form state.
- Never move focused controls or change their order while the user is typing.
- Prefer opacity and color feedback over layout movement for repeated operational use.

## Motion Vocabulary

- primitive: reveal.trim-line
  - Use only for a non-blocking transition between login states.

## Procedural Motion

No procedural motion is used for authentication or recovery surfaces.

## Runtime Policy

CSS transitions are the default adapter for small state changes. The existing Motion React adapter may preserve the selected primitive where it is already loaded; no new animation runtime is introduced.

## Reduced Motion

When `prefers-reduced-motion` is enabled, state changes use immediate opacity changes and do not animate position, scale, or background effects.

Fallback: every animated confirmation has an immediate static state change with the same text and focus result.

## Source Decisions

- Adopted: the existing login surface's short, non-blocking confirmation transitions; this keeps the new authentication states consistent with repeated console use.
- Rejected: decorative background and position animation for password and recovery states; these make an access-critical form less legible and are not required for the operator workflow.
- Authored for `openspec/changes/local-admin-onboarding`; no external motion implementation or visual reference is adopted.
