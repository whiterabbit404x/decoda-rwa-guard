# Sell-Now Proof

**Generated:** 2026-06-04T04:35:17.392860+00:00

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
| live_evidence_ready | YES |
| evidence_source | live |
| github_actions_visible_green | YES |

## Staging / Infrastructure

| Field | Value |
|---|---|
| staging_runtime_reachable | YES |
| staging_database_reachable | YES |
| staging_worker_enabled | YES |
| billing_ready | NO |
| email_ready | NO |

## Blockers

- contradiction: release-proof release_status=fail: required CI gates or test suites are failing; safe_to_sell_broadly_today cannot be true
- contradiction: release-proof ci_required_gates_ready=false
- contradiction: final-readiness says safe_to_sell_broadly_today=false; sell-now must not contradict
- billing_ready=false: billing provider not configured
- email_ready=false: email provider not configured

## Contradiction Flags

- release-proof release_status=fail: required CI gates or test suites are failing; safe_to_sell_broadly_today cannot be true
- release-proof ci_required_gates_ready=false
- final-readiness says safe_to_sell_broadly_today=false; sell-now must not contradict

## Safe Claims

- controlled pilot ready: single customer with direct onboarding, no billing required
- overall readiness score: 90/100

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
| api_live_evidence | YES |
