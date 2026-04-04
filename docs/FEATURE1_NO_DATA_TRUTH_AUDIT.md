# Feature 1 No-Data Truth Audit

## Scope
Audit date: 2026-04-04 (UTC). This audit tracks targeted removals of "no data => safe/neutral" semantics in LIVE/HYBRID monitoring paths.

## Removed paths

| File | Old behavior | Why unsafe | New behavior |
|---|---|---|---|
| `services/api/app/monitoring_runner.py` | Provider failures with zero events were coerced into `degraded` in the generic no-events branch. | Failed collection can be silently flattened into a less explicit state and make downstream summaries look like ordinary no-alert operation. | Explicitly preserve `failed` (`status='failed'`, `source_status='failed'`) when provider result is failed and no events were observed. |
| `services/api/app/activity_providers.py` | Provider results did not require a typed detection outcome; some downstream consumers could treat empty event lists as implicit calm/neutral state. | Missing typed outcomes allows accidental "empty means safe" inference across API/UI surfaces. | Added mandatory `detection_outcome` on all provider result returns (`NO_EVIDENCE`, `MONITORING_DEGRADED`, `ANALYSIS_FAILED`, `NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE`, `DEMO_ONLY`). |
| `apps/web/app/(product)/alerts-page-client.tsx` | Empty state showed `No findings yet.` for all modes. | In LIVE/HYBRID, zero alerts can be caused by no evidence/degraded collection; copy was reassuring/neutral without evidence. | Empty state now branches on runtime truth status and uses no-evidence/degraded wording (e.g. `No real evidence observed yet. Zero alerts is not proof of safety.`). |
| `apps/web/app/(product)/incidents-page-client.tsx` | Empty state showed `No incidents yet.` for all modes. | In LIVE/HYBRID, zero incidents can be caused by missing evidence and should never imply safe operation. | Empty state now uses explicit no-evidence/degraded wording in LIVE/HYBRID (e.g. `Zero incidents is not proof of safety.`). |
| `apps/web/app/monitoring-status-contract.ts` | Runtime contract lacked typed `detection_outcome` field. | UI layers could omit outcome discrimination and regress toward neutral default interpretations. | Added explicit `detection_outcome` union to the shared runtime status contract. |

## Confirmed retained safeguards
- LIVE/HYBRID no-provider-data remains mapped to `NO_EVIDENCE`/`UNKNOWN_RISK` with `claim_safe=false`.
- Claim validator still fails when `recent_real_event_count == 0`, when truthfulness is `unknown_risk`, and when demo/synthetic evidence leaks into the evidence window.
- Workspace/target persistence continues to store no-evidence/degraded/failed timestamps and rollups (`last_no_evidence_at`, `last_degraded_at`, `last_failed_monitoring_at`, `recent_evidence_state`, `recent_truthfulness_state`, `recent_real_event_count`).

## Policy reminder
No data is not safety. No alert is not proof of safety. In LIVE/HYBRID, reassurance is only valid when real evidence is observed and recent truthfulness is not unknown risk.
