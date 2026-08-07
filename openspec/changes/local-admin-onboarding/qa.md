# QA

## Automated evidence

- Backend local-auth, identity, and model tests: pass.
- Ruff on changed Python authentication files: pass.
- TypeScript type-check: pass.
- Targeted ESLint on changed authentication UI files: pass.
- Login theme and authentication-path regression script: pass.
- Next.js production build: pass.
- Alembic graph: one head, including the local administrator migration.
- Design and motion foundation checks: ready.

## Covered behavior

- Fresh installation reports that no local administrator exists.
- A valid Bootstrap credential creates the only local administrator.
- Invalid Bootstrap credentials and passwords are rejected.
- Repeated failed attempts are rate-limited.
- A signed local session crosses Fleet auth and resolves to a platform administrator identity.
- A second setup attempt is rejected.
- Daily login uses the local administrator password.
- Recovery remains hidden behind a secondary disclosure.
- OIDC remains optional, including its advanced Fleet-token setting.

## Known validation gap

No screenshot baseline or browser E2E harness exists for the login page. The production frontend build and source-level login regression script cover this change; interactive visual review remains appropriate during PR review.

A clean Compose image build was attempted after the application checks, but the local Docker Desktop BuildKit store became read-only while committing downloaded base-image layers (`metadata_v2.db: read-only file system`). The failure occurred before project compilation inside Docker and is host-storage related. After clearing the failed build cache, the installed release stack was restarted and reverified healthy. A clean Docker builder should repeat the image build during CI or PR review.
