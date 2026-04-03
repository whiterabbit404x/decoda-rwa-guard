# No-evidence truthfulness audit (Feature 1)

## Findings and fixes

1. **`services/api/app/monitoring_runner.py`**
   - **Old behavior:** Empty LIVE/HYBRID event cycles set status to `no_events` / degraded in some branches but did not persist explicit truthfulness and real-event count; prior evidence metadata could be interpreted as reassuring.
   - **Why misleading:** “No alert/no events” could be read as low-risk rather than missing evidence.
   - **New behavior:** Persists `recent_evidence_state`, `recent_truthfulness_state`, `recent_real_event_count`, `last_no_evidence_at`, `last_degraded_at`, `last_failed_monitoring_at`; no-evidence/degraded stays explicit and claim-safe remains false.

2. **`services/api/app/activity_providers.py`**
   - **Old behavior:** Provider results exposed status/evidence booleans but lacked explicit evidence/truthfulness typing for failed/no-evidence paths.
   - **Why misleading:** Callers could collapse absence states into generic neutral handling.
   - **New behavior:** Adds typed `evidence_state` + `truthfulness_state` with fail-closed semantics for LIVE/HYBRID (`NO_EVIDENCE`, `DEGRADED_EVIDENCE`, `FAILED_EVIDENCE`, `UNKNOWN_RISK`).

3. **`services/api/app/monitoring_runner.py` (`production_claim_validator`)**
   - **Old behavior:** Validator focused on RPC reachability + recent metadata but did not strictly require positive recent real-event counts or explicit non-unknown truthfulness.
   - **Why misleading:** Claims might pass with insufficient real evidence volume.
   - **New behavior:** Validator now requires `recent_real_event_count>0`, `recent_truthfulness_state!=unknown_risk`; returns `unknown_risk_detected`, `no_evidence_detected`, `degraded_window_detected`, and `evidence_window_passed`.

4. **`services/event-watcher/app/main.py`**
   - **Old behavior:** Polling loops with no emitted events remained on polling source state without explicit no-evidence degradation.
   - **Why misleading:** Temporary provider silence could look calm/normal.
   - **New behavior:** Zero-event polling cycles set `source_status=no_evidence`, `degraded=true`, `degraded_reason=no_real_evidence_observed`.

5. **`apps/web/app/monitoring-overview-panel.tsx` and `apps/web/app/workspace-monitoring-mode-banner.tsx`**
   - **Old behavior:** Runtime panel text could read as generally healthy when claims endpoint had no explicit reason and evidence copy was minimal.
   - **Why misleading:** Empty/quiet monitoring could be interpreted as healthy by absence of alerts.
   - **New behavior:** UI now displays explicit evidence-gap/degraded copy and includes `recent_real_event_count` + `recent_truthfulness_state`; real-evidence no-anomaly copy is “No confirmed anomaly detected in observed evidence”.

6. **`README.md` and `docs/GO_TO_MARKET_TRUTHFUL_CLAIMS.md`**
   - **Old behavior:** Truthfulness guidance was present but did not explicitly require positive event count + non-unknown truthfulness in claim language.
   - **Why misleading:** Teams could over-index on “no alerts” language.
   - **New behavior:** Adds explicit “No alert is not proof of safety” rule and claim-validator gate requirements for real evidence and non-unknown risk states.
