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
