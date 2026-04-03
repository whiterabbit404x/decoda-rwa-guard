# Monitoring demo leakage audit (April 3, 2026)

This note tracks the concrete demo/synthetic leakage paths removed from live/hybrid monitoring code paths.

## Removed leakage points

1. `services/api/app/activity_providers.py`
   - **Leakage before:** wallet/contract/market synthetic generators could still be selected outside strict demo isolation.
   - **Fix:** added `fetch_target_activity_result` with typed status/synthetic fields and strict mode guards; live/hybrid now fail closed to `degraded` when provider evidence is absent; demo generators are blocked via guard assertions in live/hybrid.

2. `services/api/app/monitoring_runner.py`
   - **Leakage before:** `_fallback_response` produced deterministic analysis outputs when threat engine failed, including in hybrid mode.
   - **Fix:** removed synthetic scoring fallback path; failures now emit explicit degraded analysis records (`analysis_status=analysis_failed`, `evidence_state=degraded`, `confidence_basis=none`) instead of synthetic-success responses.

3. `services/api/app/monitoring_runner.py` target config updates
   - **Leakage before:** `monitoring_demo_scenario` could be set through authenticated monitoring target updates without runtime-mode restriction.
   - **Fix:** patch endpoint now rejects demo scenario configuration when ingestion mode is `live` or `hybrid`.

4. `apps/web/app/monitoring-overview-panel.tsx` and `workspace-monitoring-mode-banner.tsx`
   - **Leakage before:** monitoring surfaces could imply healthy/green state without explicitly showing evidence basis.
   - **Fix:** UI now surfaces evidence state, confidence basis, and synthetic leak status; empty evidence defaults to explicit non-reassuring copy ("No real events observed yet").
