# RELEASE_READY

## Release posture (billing disabled by default)

This repository is now configured for broad pilot evaluation **without billing as a runtime blocker**.

### What is now release-ready

- Billing is feature-flagged and defaults to disabled (`BILLING_ENABLED=false`, `BILLING_PROVIDER=none`) for local/staging operation.
- Core workflows no longer depend on checkout/session portal/payment webhooks:
  - sign-up/sign-in/email verification/MFA
  - workspace setup and membership
  - assets and targets
  - analyses, alerts/incidents
  - exports
  - webhook destinations
- Workspace plan enforcement is unmetered while billing is disabled, so exports/targets/seats are not blocked by subscription state.
- Settings UI now presents billing as **Coming soon / Contact sales** when billing is disabled instead of blocking user progression.
- Production safeguards remain in place for auth/email/redis/readiness diagnostics.

## Intentionally deferred (for later re-enable)

- Paddle checkout wiring in real customer flows.
- Subscription enforcement and invoice lifecycle logic.
- Payment webhook-driven entitlement changes as a go-live requirement.

## Go / No-Go

**Recommendation: GO for pilot evaluation with billing disabled.**

Use `BILLING_ENABLED=true` only when you are ready to validate provider checkout/webhook behavior.
