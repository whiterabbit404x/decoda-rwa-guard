# Go-to-market truthful claims: Strategic Infrastructure Guard

## Allowed claims

You can claim the platform provides live monitoring for tokenized RWA / treasury-like assets when all of the following are true:

- `MONITORING_INGESTION_MODE` is `live` or `hybrid`.
- `LIVE_MONITORING_ENABLED=true`.
- `EVM_RPC_URL` is configured and reachable.
- Watcher source status is active and checkpoints are advancing.
- Alerts/incidents and audit evidence are persisted from real events.
- Validator confirms no synthetic leakage (`synthetic_leak_detected=false`) and recent monitoring evidence is real (`recent_evidence_state=real`).
- Validator confirms `recent_real_event_count>0`, `recent_truthfulness_state!=unknown_risk`, and `recent_claim_safe_window_passed=true`.
- Production claim validator reports `PASS`.

## Disallowed claims

Do **not** claim live protection when:

- deployment is in demo mode;
- RPC is missing/unreachable;
- watcher is degraded with stale checkpoints;
- demo/synthetic payloads are being used for wallet/contract monitoring evidence;
- validator reports `recent_evidence_state` as `demo`, `degraded`, `missing`, `no_evidence`, or `failed`.
- validator reports `recent_confidence_basis=none` or `synthetic_leak_detected=true`.
- validator reports `unknown_risk_detected=true`, `no_evidence_detected=true`, or `degraded_window_detected=true`.

## Truth-preserving runtime semantics

- DEMO mode is strictly synthetic and always tagged as synthetic.
- LIVE/HYBRID never substitute demo payloads for missing provider data.
- No provider evidence is treated as `no_evidence` / `degraded` / `failed`, never as safe or normal.
- No alert is never treated as proof of safety.
- Degraded/unknown states are expected, persisted, and visible to operators.

## Operator proof checklist

1. Run `python services/api/scripts/run_live_claim_check.py`.
2. Run `python services/api/scripts/run_live_evidence_flow.py`.
3. Capture `/ops/monitoring/health` and `/ops/production-claim-validator` outputs.
4. Export alert delivery logs (webhook/slack/email) and related audit entries.
