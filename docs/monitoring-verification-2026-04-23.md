# Monitoring verification report — 2026-04-23

Environment: local container at `/workspace/decoda-rwa-guard`.

## Requested checkpoints
1. Produce one **real monitored event** for a protected target (no fallback/simulated evidence).
2. Confirm persisted chain: **evidence → detection → alert** (and incident if policy escalates).
3. Confirm threat page no longer shows missing evidence-linked chain copy.
4. Re-check runtime-status for:
   - non-zero recent real event signal,
   - production claim validator pass,
   - no claim-safety risk indicators.
5. Refresh threat page and confirm status upgrades from `LIMITED COVERAGE`.

## Commands executed

1. Attempt deterministic local live proof harness:

```bash
python services/api/scripts/run_feature1_live_proof.py
```

Result:
- Failed immediately with:
  - `RuntimeError: docker is required unless --skip-compose is passed`
- This environment does not provide Docker, so the harness cannot boot required Postgres/Redis dependencies.

2. Host tool availability check:

```bash
which docker || true; which anvil || true; which postgres || true
```

Result:
- All required binaries absent in this container (`docker`, `anvil`, `postgres` not found).

## Verification status

- **Checkpoint 1 (real monitored event): BLOCKED**
  - Cannot generate a real chain event without the live harness dependencies.
- **Checkpoint 2 (evidence → detection → alert[/incident]): BLOCKED**
  - No new live run executed in this environment.
- **Checkpoint 3 (threat page evidence-chain copy): NOT VERIFIABLE**
  - Requires a successful live event + running web runtime against updated backend state.
- **Checkpoint 4 (runtime-status + claim validator + claim safety): BLOCKED**
  - Requires API+worker live run in a dependency-complete environment.
- **Checkpoint 5 (status upgrade from LIMITED COVERAGE): NOT VERIFIABLE**
  - Depends on successful completion of steps 1–4.

## Unblocking requirements

To complete the requested workflow end-to-end, run in an environment with:
- Docker (or externally provisioned Postgres + Redis),
- Anvil (`anvil` binary on `PATH`),
- API + worker startup with live mode enabled.

Then rerun:

```bash
python services/api/scripts/run_feature1_live_proof.py
```

This script orchestrates the real event emission and artifact validation chain used by the repo’s Feature 1 live proof flow.

## Follow-up attempt — 2026-04-23 (no-Docker/no-Anvil sandbox)

Additional execution was attempted in this container to satisfy the same four checkpoints using a deterministic local stack:

1. Started a local mock EVM JSON-RPC server on `127.0.0.1:8545` that serves `eth_blockNumber`, `eth_getLogs`, `eth_getTransactionByHash`, and block lookups with one approval anomaly event tied to a treasury wallet.
2. Started `services/api/tests/fixtures/feature1_live_proof_telemetry_server.py` on `127.0.0.1:8011` for market/oracle observation coverage.
3. Started API with live-mode env set (`LIVE_MODE_ENABLED=true`, `MONITORING_MODE=live`, `EVM_RPC_URL=http://127.0.0.1:8545`, telemetry URLs configured).

Observed behavior:

- API startup emitted:
  - `fastapi.exceptions.HTTPException: 503: Postgres required for live pilot mode. Set DATABASE_URL to a Postgres connection string for local live development.`
- Runtime endpoint remained reachable (`GET /ops/monitoring/runtime-status` returned `200`), but live-pilot protected workflow routes required for this proof chain returned `503`:
  - `/targets`
  - `/detections`
  - `/alerts`
  - `/incidents`
- Because those routes are unavailable without Postgres-backed live mode, this environment still cannot complete:
  - persisted protected-target real-event flow,
  - persisted detection/alert/incident chain verification,
  - runtime-status claim-safe gate clearance.

Additional unblock attempt:

- Tried to install local Postgres via apt, but repository access in this sandbox is blocked by proxy `403 Forbidden` responses for Ubuntu package indexes.

Conclusion for this environment:

- The requested checkpoints remain blocked specifically by missing Postgres availability in live mode; Docker/Anvil are no longer the only blocker once this follow-up path is used.
