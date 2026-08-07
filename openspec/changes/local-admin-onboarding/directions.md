# Directions

## A. Expose Bootstrap on the main login form

Fastest implementation, but preserves the opaque-token onboarding failure. Rejected.

## B. Unauthenticated create-admin form

Feels simple on localhost but permits the first remote visitor to claim the administrator role. Rejected.

## C. One-time bootstrap step, then local password login

The installer prints the existing Bootstrap credential beside direct setup instructions. The login page uses it only while creating the local administrator, then switches to a password-first local path; OIDC remains available when configured and recovery is secondary. Selected because it preserves the existing safe trust handoff without making a deployment token the daily login experience.
