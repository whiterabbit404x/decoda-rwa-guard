# Feature 1 staging evidence flow

## Required env vars
- `FEATURE1_API_URL` (default `http://127.0.0.1:8000`)
- `FEATURE1_API_TOKEN` (bearer token)
- `FEATURE1_WORKSPACE_ID`
- Optional: `FEATURE1_TARGET_ID`

## Command
```bash
python services/api/scripts/run_feature1_real_asset_evidence.py
```

## Output interpretation
- `status=pass`: real monitored target produced real evidence-linked alert (and optional incident).
- `status=inconclusive`: live mode or provider/target/evidence not sufficient; no fake pass is emitted.
- `status=fail`: API/runtime/export path failed.

## Expected output fields
- workspace/target/asset identity
- chain and evidence window
- observed tx hash/block/event
- finding/alert/incident ids
- anomaly basis and baseline context
- export job reference for Feature 1 evidence bundle


## Worker-first proof requirement

Use `POST /ops/monitoring/run` (or `services/api/app/run_monitoring_worker.py --once`) to generate proof artifacts. Avoid using `POST /monitoring/run-once/{id}` for enterprise evidence claims.
