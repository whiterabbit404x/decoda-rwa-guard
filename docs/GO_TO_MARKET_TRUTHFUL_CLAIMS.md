# Go-to-market truthful claims: Strategic Infrastructure Guard

## Allowed claims

You can claim the platform provides live monitoring for tokenized RWA / treasury-like assets when all of the following are true:

- `MONITORING_INGESTION_MODE` is `live` or `hybrid`.
- `LIVE_MONITORING_ENABLED=true`.
- `EVM_RPC_URL` is configured and reachable.
- Watcher source status is active and checkpoints are advancing.
- Alerts/incidents and audit evidence are persisted from real events.
- Validator confirms no synthetic leakage (`synthetic_leak_detected=false`) and recent monitoring evidence is real (`recent_evidence_state=real`).
- Production claim validator reports `PASS`.

## Disallowed claims

Do **not** claim live protection when:

- deployment is in demo mode;
- RPC is missing/unreachable;
- watcher is degraded with stale checkpoints;
- demo/synthetic payloads are being used for wallet/contract monitoring evidence;
- validator reports `recent_evidence_state` as `demo`, `degraded`, or `missing`.

## Operator proof checklist

1. Run `python services/api/scripts/run_live_claim_check.py`.
2. Run `python services/api/scripts/run_live_evidence_flow.py`.
3. Capture `/ops/monitoring/health` and `/ops/production-claim-validator` outputs.
4. Export alert delivery logs (webhook/slack/email) and related audit entries.
