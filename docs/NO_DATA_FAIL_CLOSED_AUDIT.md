# No-data fail-closed audit (Feature 1 final truthfulness)

This audit lists the concrete “no data => safe/neutral/no-alert” paths removed or hardened so LIVE/HYBRID fail closed.

## File-by-file removals / hardening

1. **`services/api/app/monitoring_runner.py`**
   - **Old behavior:** Runtime status could remain `LIVE`/`HYBRID` when `recent_real_event_count == 0` or `recent_truthfulness_state == unknown_risk`; UI tone could still look live/healthy.
   - **Why misleading:** “No recent alerts/events” could be read as healthy operations.
   - **New behavior:** `monitoring_runtime_status()` now forces `mode=DEGRADED` and `provider_health=degraded` for LIVE/HYBRID evidence gaps (`no_evidence`/`missing`/`failed`/`degraded`, zero real events, or unknown risk).

2. **`services/api/app/monitoring_runner.py`**
   - **Old behavior:** Per-event metadata truthfulness could inherit `claim_safe=true` from analyzer response payloads.
   - **Why misleading:** “No confirmed anomaly” could collapse into an implied safety claim.
   - **New behavior:** Monitoring analysis now persists `claim_safe=false` and `truthfulness_state=not_claim_safe` for this flow; “no confirmed anomaly” remains a distinct detection outcome only.

3. **`services/api/app/monitoring_runner.py`**
   - **Old behavior:** Target source status for empty LIVE/HYBRID cycles was generalized to degraded.
   - **Why misleading:** It reduced precision between no-evidence and degraded/failure states.
   - **New behavior:** `source_status` now explicitly records `no_evidence` when provider status is `no_evidence`, and keeps degraded only for degraded/failed paths.

4. **`apps/web/app/workspace-monitoring-mode-banner.tsx`**
   - **Old behavior:** Banner tone depended mainly on `status.mode`; it could remain visually live while evidence was absent.
   - **Why misleading:** Empty evidence windows could still look operationally healthy.
   - **New behavior:** Banner now fail-closes tone to degraded when real events are absent or truthfulness is `unknown_risk`.

5. **`apps/web/app/monitoring-overview-panel.tsx`**
   - **Old behavior:** “No confirmed anomaly…” copy could appear from `evidence_state=real` even with `recent_real_event_count=0`/unknown risk.
   - **Why misleading:** It could imply reassurance without current real evidence.
   - **New behavior:** Reassurance copy now requires `real` evidence + positive real-event count + non-unknown truthfulness; otherwise copy is explicit no-evidence/degraded wording including “Zero alerts is not proof of safety.”

## Current fail-closed semantics enforced

- `no data != safe`
- `no alert != safe`
- LIVE/HYBRID evidence gaps resolve to `NO_EVIDENCE`, `UNKNOWN_RISK`, `MONITORING_DEGRADED`, or `ANALYSIS_FAILED` semantics.
- “No confirmed anomaly detected in observed evidence” is distinct from any global safety claim.
