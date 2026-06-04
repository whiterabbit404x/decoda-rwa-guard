# Sell-Now Proof

**Generated:** 2026-06-04T09:47:59.517566+00:00

## Readiness Summary

| Flag | Value |
|---|---|
| sell_now_managed_ready | NO |
| broad_paid_saas_ready | NO |
| safe_to_sell_broadly_today | NO |

## Evidence

| Field | Value |
|---|---|
| provider_ready | YES |
| live_evidence_ready | NO |
| evidence_source | live |
| github_actions_visible_green | YES |

## Staging / Infrastructure

| Field | Value |
|---|---|
| staging_runtime_reachable | YES |
| staging_database_reachable | YES |
| staging_worker_enabled | YES |
| billing_ready | YES |
| email_ready | YES |

## Blockers

- frontend_build not_run: run npm run build in CI; broad paid SaaS requires a passing frontend build
- readiness_validation not_run: run validate_production_readiness.py; broad paid SaaS requires a passing readiness validation
- live telemetry freshness check failed: April 2026 telemetry used in June 2026 proof run; exceeds 30-day freshness window
- live_evidence_ready=false: no live telemetry chain proven in CI artifact
- contradiction: final-readiness (strict=true) says safe_to_sell_broadly_today=false; sell-now must not contradict

## Contradiction Flags

- final-readiness (strict=true) says safe_to_sell_broadly_today=false; sell-now must not contradict

## Safe Claims

- controlled pilot ready: single customer with direct onboarding, no billing required
- overall readiness score: 100/100

## Prohibited Claims

- Do NOT claim live monitoring proven from real RPC data
- Do NOT claim this product is ready for managed or pilot customer delivery
- Do NOT claim broad paid SaaS readiness
- Do NOT claim billing, email, or staging are production-ready
- Do NOT claim safe_to_sell_broadly_today=true

## Sources

| Source | Loaded |
|---|---|
| github_proof | YES |
| staging_proof | YES |
| live_evidence_proof | YES |
| launch_proof | YES |
| final_readiness | YES |
| release_proof | YES |
| ci_gates | YES |
| api_live_evidence | YES |
