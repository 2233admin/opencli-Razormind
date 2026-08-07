# Local administrator onboarding design

Selected direction: C from `directions.md`.

- Login hierarchy: local administrator password is the default when local setup exists; OIDC is the primary choice only when configured; recovery is a disclosure, not a peer action.
- Fresh state: explain that setup uses the Bootstrap credential printed by the installer. The setup form contains that credential, a password, and confirmation, with visible password-manager-compatible labels.
- Error state: invalid setup or login credentials state the recovery action without echoing secret values.
- Abuse boundary: setup and login failures are rate-limited per client; malformed stored password hashes cannot select an attacker-controlled scrypt work factor.
- Accessibility: labelled password fields, matching error messages, keyboard-first form submission, no hidden focus changes, and reduced-motion-safe state transitions.
- Existing project `DESIGN.md` remains the visual authority.
