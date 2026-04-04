# Feature 1 fail-open audit (LIVE/HYBRID no-data truth hardening)

Audit date: 2026-04-04 (UTC).

This audit records focused removals of remaining fail-open semantics where missing evidence could still look safe/neutral.

| File path | Old behavior | Why unsafe | New behavior |
| --- | --- | --- | --- |
| `services/api/app/monitoring_runner.py` (`production_claim_validator`) | Claim validator required positive counts but did not require the last real evidence to be recent within an explicit evidence window. | Stale historical activity could satisfy parts of the validator while current live evidence was absent. | Added `MONITORING_EVIDENCE_WINDOW_SECONDS` enforcement (`evidence_window_recent_real_events`) and made it a required pass check. Validator now fails when the latest real event is stale even if counts are non-zero. |
| `services/api/app/monitoring_runner.py` (`production_claim_validator`) | Runtime checks split `evm_rpc_reachable` and watcher activity, but there was no explicit unified check for provider reachability or active backfill path. | Missing explicit guard can hide fail-open logic drift in future changes. | Added explicit required check `provider_reachable_or_backfilling` and made it part of validator PASS criteria. |
| `services/api/app/monitoring_runner.py` (`monitoring_runtime_status`) | Runtime status exposed recent rollups but not canonical `evidence_state` / `truthfulness_state` / `latest_block` / `error_code` fields from the shared truth model. | Downstream consumers could regress to implicit status inference from sparse fields. | Runtime status now emits canonical truth fields (`evidence_state`, `truthfulness_state`, `latest_block`, `error_code`) in addition to existing rollups, preserving fail-closed semantics end-to-end. |
| `apps/web/app/monitoring-overview-panel.tsx` | Empty evidence copy did not explicitly distinguish stale checkpoint windows from generic no-evidence states. | LIVE/HYBRID empty state could remain too generic and look calmer than warranted. | Added explicit stale-checkpoint copy (`Checkpoint stale. Awaiting live evidence.`) and preserved no-evidence fail-closed wording (`Zero alerts is not proof of safety.`). |

## Resulting guarantees

- In LIVE/HYBRID, no data does not map to safety: missing/stale evidence keeps truthfulness unknown and claim-safe false.
- “No confirmed anomaly” remains distinct from safety and only applies when real evidence exists.
- Validator PASS now requires recent real evidence inside the configured evidence window.
- Empty UI states remain explicit about no-evidence/degraded conditions and avoid healthy/all-clear framing.
