---
status: accepted
---

# Use a Local Administrator Password After Bootstrap

## Context

ADR-0005 protects the fleet API with a static token and deliberately avoids a multi-user identity system. That token is appropriate for agents and API clients, but requiring an operator to retrieve and paste deployment credentials for every browser session makes a fresh self-hosted console difficult to enter. OIDC cannot be the only human login path because a single-operator installation may not have an identity provider.

## Decision

Keep the single-operator model and add one persistent local administrator credential:

- First-run setup requires the existing `BOOTSTRAP_ADMIN_TOKEN` and can create only the fixed `local-admin` record.
- The operator chooses a password of at least 12 characters. The backend stores only a salted scrypt hash with fixed, reviewed work parameters.
- Successful setup or password login returns a server-signed, 12-hour local administrator session.
- A valid local session crosses the Fleet middleware for browser API requests and resolves to the existing platform-administrator identity.
- Bootstrap remains valid as an explicit recovery credential; it is not the primary daily login control.
- OIDC remains optional and independent. Static Fleet tokens remain the machine-client credential.
- Only local-auth status, setup, and login are public API paths. Failed credential attempts are rate-limited per client.

This decision does not add registration, invitations, multiple local accounts, workspace-specific local roles, or password-reset email.

## Consequences

- A new self-hosted deployment can establish a human login without configuring OIDC.
- Daily browser access no longer exposes deployment tokens in the main login flow.
- Compromise of the signing key can forge local sessions, so production deployments must continue generating a strong `SECRET_KEY`.
- Rate limiting is process-local; deployments with multiple API workers should enforce an additional shared limit at the reverse proxy.
- Losing both the local password and Bootstrap credential still requires operator access to the deployment environment.

## Rejected Alternatives

- Keep Bootstrap as the daily login: preserves an opaque operational-token workflow and increases routine exposure of a high-privilege recovery secret.
- Allow the first visitor to create an administrator without a credential: permits remote takeover when a new console is network reachable.
- Require OIDC before first use: makes the default single-operator deployment depend on external identity infrastructure.
- Add a general user database and password-reset system: exceeds the single-operator threat model and creates unnecessary identity lifecycle scope.
