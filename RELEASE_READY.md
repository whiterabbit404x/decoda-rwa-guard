# RELEASE_READY

Last reconciled: **2026-04-02**.

## Validation model now used

Release validation is now grouped into explicit launch-gate categories with machine-readable output:

1. `local_repo_integrity`
2. `frontend_build_reproducibility`
3. `browser_e2e_runtime`
4. `api_runtime_readiness`
5. `live_provider_configuration`
6. `staging_evidence`

Commands:

- `make validate-production`
- `make validate-staging`
- `make validate-launch` (runs both)

Each command returns JSON plus an operator summary. Any category failure is a **no-go**.

## What is verified in-repo

- Backend readiness and auth/billing diagnostics checks run in deterministic pytest gates.
- Frontend reproducibility is validated by checking declared, lockfile-resolved, and installed versions for Next.js and Playwright.
- Playwright runtime checks now distinguish:
  - package missing
  - browser binaries missing
  - runtime ready
- Staging evidence flow now executes a real path (landing, sign-in, protected access, onboarding read, create/list asset workflow) and saves evidence artifacts.
- Live provider smoke now reports structured statuses (`verified`, `configured_unverified`, `not_configured`, `fail`) for email, billing, Redis, staging readiness, and EVM RPC.

## What is only verifiable in staging/external env

- Real provider connectivity and non-placeholder secrets.
- Real staging account auth/MFA outcomes.
- External network path to staging API and EVM RPC provider.

## Readiness tiers

### Ready for pilot
- Local gate passes except explicitly external-only checks marked `configured_unverified`.
- Controlled customer onboarding can proceed.

### Ready for broad self-serve sale
- `make validate-launch` passes fully.
- Staging evidence artifacts are generated and archived.
- Billing/email/Redis are `verified` in provider smoke (not `not_configured`).

### Ready for enterprise procurement
In addition to broad self-serve readiness:
- SOC2/control evidence package and incident-response artifacts are complete.
- Procurement-facing security/compliance requirements are signed off.

## Honest recommendation

**Current recommendation: conditional NO-GO for broad self-serve until external staging evidence is run with real provider credentials in your deployment environment.**

Reason: the launch gates now enforce real staging/provider checks, which cannot be fully satisfied by code-only/local execution without configured external services.
