# Brief

- Goal: make a fresh self-hosted deployment usable without an external identity provider or manual `.env` token lookup.
- Audience: a single operator deploying OpenCLI for the first time.
- Surface: installer output, login page, local-auth API, and existing API authorization boundary.
- Constraint: first administrator creation must remain safe when the console is network reachable.
- Non-goal: multi-user registration, invitations, password reset, or replacing OIDC.
- Acceptance: installer prints a first-run credential and clear setup instructions; that credential creates one local administrator; later password login opens the console without a Fleet token; Bootstrap remains recovery-only.
