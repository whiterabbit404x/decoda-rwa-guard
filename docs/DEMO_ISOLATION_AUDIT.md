# DEMO Isolation Audit (April 3, 2026)

This audit tracks demo/synthetic leakage points removed from LIVE/HYBRID runtime paths.

| File path | Previous behavior | New behavior | Disposition |
|---|---|---|---|
| `services/api/app/activity_providers.py` | LIVE/HYBRID provider no-data conditions were returned as generic degraded and provider exceptions were not typed. | LIVE/HYBRID now return explicit `status=no_evidence` (no provider events) or `status=failed` (provider error), `claim_safe=false`, and typed `error_code` / `reason_code`. Synthetic paths are blocked by central mode guards. | Removed invalid fallback and isolated DEMO paths. |
| `services/api/app/monitoring_mode.py` | Mode helper coverage was incomplete for explicit degraded/synthetic assertions. | Added central mode guard helpers: `is_degraded_mode`, `require_real_evidence`, and `assert_no_synthetic_path`; LIVE/HYBRID/DEGRADED hard-block synthetic attempts. | Hardened and centralized. |
| `services/api/app/monitoring_runner.py` | No-evidence and failed provider states could be persisted as generic `no_events`, reducing visibility into degraded truthfulness. | Persists explicit evidence truth fields (`recent_evidence_state`, `recent_confidence_basis`, `last_no_evidence_at`, `last_failed_monitoring_at`, etc.) and marks no-evidence/failed runs explicitly while keeping `claim_safe=false`. | Removed silent fallback semantics. |
| `services/api/app/monitoring_runner.py` (`production_claim_validator`) | Claim validator had synthetic checks but no explicit claim-safe window field and weaker missing/degraded evidence gating. | Validator now exposes `recent_claim_safe_window_passed` and fails when evidence is not real/provider-backed, when synthetic leakage exists, or when recent evidence is degraded/missing. | Hardened fail-closed behavior. |
| `apps/web/app/monitoring-overview-panel.tsx` | UI claim copy could be generic and did not consistently prioritize no-evidence/degraded truth-preserving wording. | UI now renders explicit non-reassuring evidence copy: `No real evidence observed yet` or `Monitoring degraded` unless real evidence exists. | Isolated false-green messaging risk. |

## Notes

- DEMO mode remains available and explicitly synthetic.
- LIVE and HYBRID remain real-evidence-only; reconnect/backfill are allowed, but demo replacement is blocked.
