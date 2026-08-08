# QA

Status: implemented; authenticated visual acceptance remains gated by credentials.

- Design foundation: ready.
- Motion foundation: ready.
- Optional visual/motion companion skills: unavailable; built-in gates are the fallback.
- Production frontend build and TypeScript: passed.
- Production API image build/import: passed.
- Public event stream smoke: passed for started, understanding, and governed failure recovery.
- Browser QA: login and Bootstrap surface rendered correctly; authenticated Agent Dock inspection requires a user login and was not bypassed.
- Docker health: API, frontend, and browser healthy.
- Durable run migration: applied (`z7a8b9c0d1e2 -> a8b9c0d1e2f3`); the API OpenAPI document exposes run detail and ordered event replay endpoints.
- Reconnect behavior: the stream creates its durable run before work begins and does not cancel background execution when its client disconnects; the dock retains the run/session identifiers and replays terminal events after a transport error.
- Computer-use capability differed from its documentation; Chrome browser control was used as the supported fallback.
- Remote Issue/PR: not published.
