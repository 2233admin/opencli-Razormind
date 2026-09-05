# Frontend Experience Foundation

Status: implemented; verification evidence is recorded in `qa.md`.

This change integrates completed Designer Pipeline work into the shared OpenCLI Admin frontend layer without reopening the approved direction.

## Integrated inputs

- `physical-motion-baseline`: timing, easing, tactile primitives, and reduced motion.
- `compact-system-pulse`: neutral operational state and responsive density rules.
- `modern-visualization-stress`: deterministic stress evidence and disabled chart path tweening.
- `login-background-themes`: explicit state, truthful fallback, and reduced-motion conventions.
- Route motion from `codex/workflow-studio-motion-wip`: persistent shell plus pathname-keyed SSGOI transitions.

## Foundation contract

- Typography roles live in `app/globals.css` and use Noto Sans SC / IBM Plex Mono.
- Physical response tokens are shared by buttons, cards, switches, toggles, tabs, and sliders.
- Sidebar and header remain mounted while only routed content animates.
- SSGOI owns global route movement. Persistent shell elements must not mount React ViewTransition boundaries, which can snapshot the whole document during navigation.
- `prefers-reduced-motion` collapses route, local, and primitive motion.
- Route loading and recovery use App Router `loading.tsx` and `error.tsx` boundaries.
- Navigation only exposes routes present in the current checkout.

Route motion reads the shared spatial tokens and observes runtime `prefers-reduced-motion` changes. See `docs/verification/frontend-route-motion.md` for browser measurements and regression commands.
