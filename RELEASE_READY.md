# RELEASE_READY

Last reconciled: **2026-04-03**.

## Launch validation commands

- `make validate-no-billing-launch` → pilot launch gate (billing intentionally disabled).
- `make validate-launch` → strict broad self-serve gate (includes provider + staging requirements).
- `make proof-no-billing-launch` → deterministic artifact bundle for no-billing public beta evidence (`artifacts/launch-proof/latest/{summary.json,summary.md}`).

Both commands emit machine-readable JSON and category summaries.
Browser runtime checks are required by default. Only no-billing pilot runs may skip browser runtime when `ALLOW_BROWSER_RUNTIME_SKIP=true` is set explicitly.

## Validation categories

1. `local_repo_integrity`
2. `frontend_build_reproducibility`
3. `browser_e2e_runtime`
4. `api_runtime_readiness`
5. `live_provider_configuration`
6. `staging_evidence`

## Readiness tiers

### 1) Production-polished pilot launch (current target)
Pass criteria:
- `make validate-no-billing-launch` passes.
- `BILLING_PROVIDER=none` is asserted in proof output (`00_assert_no_billing_mode`).
- Billing may be `not_configured` only when `BILLING_PROVIDER=none`.
- Auth/session/workspace/runtime checks still must pass.
- Public/legal/support/trust pages are present and coherent.
- Integrations are self-serve via manual Slack/webhook setup with masked secrets, test-send actions, delivery logs, and worker-health guidance.

### 2) Broad self-serve launch (future)
Pass criteria:
- `make validate-launch` passes.
- Billing/email/Redis/provider checks are fully verified in deployed staging.
- Staging evidence artifacts are generated and archived.

### 3) Enterprise procurement posture (out of scope for this pass)
Requires all broad self-serve criteria plus formal compliance/control evidence and procurement artifacts.

## Honest status for this repository

- **Pilot launch:** ready when `BILLING_PROVIDER=none` and no-billing validation passes.
- **Public marketing traffic:** ready (site copy and legal/commercial pages align with pilot mode).
- **Broad paid self-serve:** **not yet** (billing enablement intentionally deferred).
- **Slack OAuth app install/callback/interactivity:** **not yet** in this pass; manual Slack setup is the supported launch path.
