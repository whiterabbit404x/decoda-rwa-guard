# Sell-Now Proof

**Generated:** 2026-06-04T14:51:21.540502+00:00

## Readiness Summary

| Flag | Value |
|---|---|
| sell_now_managed_ready | NO |
| broad_paid_saas_ready | NO |
| safe_to_sell_broadly_today | NO |

## Evidence

| Field | Value |
|---|---|
| provider_ready | NO |
| live_evidence_ready | NO |
| evidence_source | unknown |
| github_actions_visible_green | YES |

## Staging / Infrastructure

| Field | Value |
|---|---|
| staging_runtime_reachable | YES |
| staging_database_reachable | NO |
| staging_worker_enabled | NO |
| billing_ready | NO |
| email_ready | NO |

## Blockers

- provider_ready=false: live RPC provider not configured or not proven in CI artifact
- live_evidence_ready=false: no live telemetry chain proven in CI artifact
- evidence_source='unknown': not live evidence
- contradiction: api/live_evidence says provider_ready=true but live-evidence-proof says provider_ready=false
- contradiction: release-proof release_status=fail: required CI gates or test suites are failing; safe_to_sell_broadly_today cannot be true
- contradiction: release-proof ci_required_gates_ready=false
- staging_database_reachable=false
- staging_worker_enabled=false
- billing_ready=false: billing provider not configured
- email_ready=false: email provider not configured

## Contradiction Flags

- api/live_evidence says provider_ready=true but live-evidence-proof says provider_ready=false
- release-proof release_status=fail: required CI gates or test suites are failing; safe_to_sell_broadly_today cannot be true
- release-proof ci_required_gates_ready=false

## Safe Claims

- controlled pilot ready: single customer with direct onboarding, no billing required
- overall readiness score: 94/100

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
