# Sell-Now Proof

**Generated:** 2026-06-03T15:42:52.242924+00:00

## Readiness Summary

| Flag | Value |
|---|---|
| sell_now_managed_ready | YES |
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
| staging_runtime_reachable | NO |
| staging_database_reachable | YES |
| staging_worker_enabled | YES |
| billing_ready | NO |
| email_ready | NO |

## Blockers

- staging_runtime_reachable=false
- billing_ready=false: billing provider not configured
- email_ready=false: email provider not configured

## Safe Claims

- controlled pilot ready: single customer with direct onboarding, no billing required
- overall readiness score: 90/100
- live EVM telemetry received and proven in CI artifact
- detection → alert → incident chain proven from live provider data

## Prohibited Claims

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
| api_live_evidence | YES |
