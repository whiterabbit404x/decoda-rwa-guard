# SaaS Demo/Fallback Leakage Audit

**Audit date:** 2026-05-13
**Audit scope:** Full repo — apps/web, services/api, services/*, packages/*, contracts/
**Audit type:** Read-only. No application code was modified.
**Branch:** `claude/follow-claude-guidelines-R4RmJ`

---

## 1. Executive Verdict

### Is customer-facing demo/fallback leakage present?

**Yes — partial, with meaningful mitigations already in place.**

The codebase has sophisticated guards: evidence source discrimination, copy sanitization, env-gated fallback paths, explicit `source: 'fallback'` tagging, and strong tests. However, several real leakage paths survive into customer-facing surfaces, and at least two of them carry cross-workspace data that does not belong to the requesting tenant.

### Does it block pilot SaaS?

**Not outright, but three findings must be resolved before a pilot customer can trust the evidence they see.** The compliance and resilience fallback payloads serve hardcoded governance action IDs, wallet addresses, and attestation hashes that belong to no real workspace.

### Does it block public paid SaaS?

**Yes.** The `ENABLE_DEMO_FALLBACKS` guard correctly prevents full fallback snapshots from rendering in production, but the API-level compliance and resilience fallback routes (`/compliance/dashboard`, `/compliance/governance/actions`, `/resilience/dashboard`, `/resilience/incidents`) serve static fallback payloads to **all workspaces** whenever backend services are unavailable — regardless of `NODE_ENV`. These responses carry `source: 'fallback'` and `degraded: True`, but the customer-facing UI does not currently surface those fields as a visible warning on the compliance/resilience dashboard pages.

### Top 5 Highest-Risk Areas

| Rank | Area | Risk |
|------|------|------|
| 1 | API compliance fallback payload served cross-workspace | Governance actions with IDs `gov-fallback-003`, wallet addresses, and attestation hashes `fallback-003` that belong to no real workspace are served to any workspace when compliance-service is down |
| 2 | API resilience fallback payload served cross-workspace | Incidents `evt-fallback-0001/0002` with hardcoded asset IDs and attestation hashes served to any workspace when reconciliation-service is down |
| 3 | Risk dashboard fallback transaction hash `0xphase1sample` | Visible in fallback risk queue entries when risk-engine is degraded (API layer), with `source: 'fallback'` in payload but UI labeling not guaranteed to surface this |
| 4 | `threat-monitoring-panel.tsx` "All pipeline stages are operational" claim | This message fires when `blocker === null`, but the blocker logic does not verify that active telemetry/detections are from live (not simulator) evidence; simulator events can satisfy `telemetryOk` and `detectionOk`, leading to a false "operational" claim |
| 5 | `fallback` alerts/incidents labeled "Simulator" in executive summary | `source === 'fallback'` records in the alerts/incidents panel receive a "Simulator" StatusPill — but fallback ≠ simulator; this mislabels the data source type |

---

## 2. Findings Table

| ID | Severity | File Path | Pattern Found | Customer-Facing Risk | Clearly Labeled? | Recommended Fix | Session |
|----|----------|-----------|---------------|---------------------|-----------------|-----------------|---------|
| F-01 | Critical | `services/api/app/main.py:1917–1964` | `fallback_compliance_dashboard()` served on `/compliance/dashboard`, `/compliance/governance/actions`, `/compliance/policy/state` when compliance-service unavailable | Hardcoded governance action IDs (`gov-fallback-003`), wallet addresses, attestation hashes (`fallback-003`), and asset IDs (`USTB-2026`) are served to **all workspaces** without workspace scoping | No — payload has `source: 'fallback'` and `degraded: True` in JSON but compliance page UI does not visibly warn the customer | Return `503` or empty degraded payload instead of cross-workspace static data; or add workspace-scoped "service unavailable" response | 2 |
| F-02 | Critical | `services/api/app/main.py:1967–2027` | `fallback_resilience_dashboard()` served on `/resilience/dashboard`, `/resilience/incidents` when reconciliation-service unavailable | Incidents `evt-fallback-0001/0002` with hardcoded attestation hashes (`fallback-event-0001/0002`), asset IDs (`USTB-2026`), and timestamps (`2026-03-18`) are served to any workspace | No — payload tagged `source: 'fallback'`/`degraded: True` but resilience page UI does not surface this as a visible banner | Return `503` or workspace-scoped "service unavailable" response; never serve cross-tenant static records | 2 |
| F-03 | High | `apps/web/app/dashboard-data.ts:352–509` + `:1358` | `fallbackRiskDashboard` with `tx_hash: '0xphase1sample'`, `source: 'fallback'`, stale timestamps | When `ENABLE_DEMO_FALLBACKS=true && NODE_ENV !== 'production'` (i.e., staging), full risk snapshot with fake tx hashes is rendered without a customer-visible warning label | Partial — `source: 'fallback'` is in payload; `normalizeDashboardPresentationState` converts it to `limited_coverage` label, but individual transaction rows show `0xphase1sample` | Add prominent "service unavailable" banner to risk dashboard page when `source === 'fallback'`; strip sample tx hashes from fallback payload | 6 |
| F-04 | High | `apps/web/app/threat-monitoring-panel.tsx:240–330,:597–600` | `telemetryOk = !!lastTelemetryAt \|\| telemetry.length > 0` + "All pipeline stages are operational" | This message fires when `blocker === null`. The blocker logic never checks whether telemetry/detections are from live vs. simulator evidence. Simulator events satisfy `telemetryOk`, so the claim can appear when only simulator data exists | No — no evidence source guard before displaying this claim | Check `isSimulatorMode` before rendering "All pipeline stages are operational"; replace with "All pipeline stages are active (simulator mode)" when simulator | 2 |
| F-05 | High | `apps/web/app/dashboard-executive-summary.tsx:461–463,:523–525` | `alert.source === 'fallback'` → label "Simulator" | Fallback-sourced alerts and incidents receive a "Simulator" StatusPill — fallback ≠ simulator; this mislabels the evidence type to the customer | No — "Simulator" is wrong label for `source === 'fallback'` | Use "Fallback" or "Unavailable" pill label for `source === 'fallback'`; keep "Simulator" only for confirmed simulator sources | 2 |
| F-06 | High | `services/api/app/main.py:4299–4583` | `fallback_compliance_dashboard()` hardcodes `generated_at: '2026-03-18T11:00:00Z'`, attestation hashes `fallback-001/002/003`, and wallet `0xblocked000000000000000000000000000000003` | Stale timestamps and opaque wallet addresses leak into governance action detail responses visible to customers | No | Replace static fallback payloads with workspace-scoped "service unavailable" shells that return zero counts and no records | 2 |
| F-07 | High | `services/api/app/main.py:4471–4662` | `fallback_resilience_dashboard()` hardcodes `attestation_hash: 'fallback-event-0001/0002'`, asset `USTB-2026`, critical/high severity | Fake incidents with `severity: critical/high` and hardcoded `attestation_hash` values appear as real evidence when reconciliation-service is down | No — tagged `source: 'fallback'` in payload only | Same as F-02: return workspace-scoped empty response or `503` | 2 |
| F-08 | Medium | `apps/web/app/dashboard-data.ts:1056–1061` | `fallbackSnapshotsEnabled()` guard: `ENABLE_DEMO_FALLBACKS=true && NODE_ENV !== 'production'` | In a staging deployment with `ENABLE_DEMO_FALLBACKS=true`, all four fallback dashboard snapshots render with fake data (fake tx hashes, governance IDs, incidents). A pilot customer using a staging URL sees fabricated evidence | Partial — `NODE_ENV` guard blocks production, but staging is not customer-isolated | Document that `ENABLE_DEMO_FALLBACKS` must never be set on any customer-reachable deployment; add explicit check: remove the flag from non-dev environments | 6 |
| F-09 | Medium | `apps/web/app/dashboard-data.ts:523–645,:760–895` | `fallbackComplianceDashboard.sample_scenarios` and `fallbackThreatDashboard.sample_scenarios` dicts with scenario description strings | If any rendering code traverses `sample_scenarios`, raw scenario strings like `'compliant-transfer-approved'` or `'demo-scenario'` may surface to customers | Partial — these dicts are not rendered by known current UI, but their presence inside response payloads is a latent risk | Strip `sample_scenarios` from all customer-facing API responses; keep only in test fixtures | 4 |
| F-10 | Medium | `services/api/app/monitoring_runner.py:2259–2290` | `ingestion_source == 'demo'` → `confidence_basis: 'demo_scenario'`, `evidence_state: 'demo'`, `detection_outcome: 'DEMO_ONLY'` | If a live-mode workspace inadvertently receives a `demo`-source ingestion event, its detection record is stamped `DEMO_ONLY` — but the detection still appears in the detection list | Partial — `evidence_state: 'demo'` is set, but it is unclear whether detection list pages filter or label `DEMO_ONLY` records | Add explicit filter/label for `detection_outcome === 'DEMO_ONLY'` on detection-list and alert pages | 4 |
| F-11 | Medium | `apps/web/app/pilot-mode-banner.tsx:20` | "Live feed temporarily unavailable. Configure deployment environment variables to restore workspace data." | Message implies live feed exists but is temporarily down, when the real cause is misconfigured env vars. A pilot customer may think a system is recovering when it is not configured | No — no distinction between "unavailable" and "not configured" | Change copy to "Live workspace not configured. Contact your administrator to connect live data sources." | 8 |
| F-12 | Medium | `apps/web/app/dashboard-executive-summary.tsx:125–127` | `healthProvable = monitoringHealthyCopyAllowed(truth) && truth.monitoring_status === 'live'` → `'All systems operational'` | `monitoringHealthyCopyAllowed` is correctly gated on `reporting_systems_count > 0`, `continuity_status === 'continuous_live'`, and `telemetry_freshness !== 'unavailable'`. However, it does not explicitly exclude `evidence_source_summary === 'simulator'`, so a workspace in live mode with only simulator evidence passing all gate conditions could display "All systems operational" | Partial — `isSimulator` is derived separately but is not fed back into `healthProvable` | Add `&& monitoringTruth.evidence_source_summary !== 'simulator'` to `healthProvable` computation | 2 |
| F-13 | Medium | `services/api/app/main.py:665` | `'status': 'Live' if payload_source == 'live' and not degraded else 'Fallback' if payload_source == 'fallback' or degraded else 'Pending'` | The string `'Fallback'` is returned in dependency health status payloads. If this reaches a customer-visible health panel without relabeling, it exposes internal terminology | Partial — this is in internal diagnostics but the `/health/details` endpoint is accessible | Relabel `'Fallback'` to `'Unavailable'` in customer-facing health status responses | 6 |
| F-14 | Medium | `services/api/app/main.py:1813` | `/risk/dashboard` description: "falls back to explicit demo-safe records when the risk-engine is unavailable" | The FastAPI description string uses "demo-safe records" which is internal terminology. If any API documentation is exposed to pilot customers, this leaks product internals | No | Change description to "returns a zero-count degraded response when the risk-engine is unavailable" | 6 |
| F-15 | Low | `apps/web/app/settings-page-client.tsx:370` | `liveModeConfigured ? 'Connected to live data sources' : 'Using sample data only'` | "Using sample data only" is shown in the customer-facing settings page when live mode is not configured. "Sample" is internal terminology; customer may not understand it | Partial — shown in settings, not in monitoring/evidence pages | Relabel to "Live data source not connected" to match the SaaS workflow context | 8 |
| F-16 | Low | `services/api/app/main.py:1095–1117` | `demo_seed_status()` result exposed in `/debug/fixtures` endpoint as `demoSeedPresent`, `demoSeedStatus`, `demoSeedEmail` | Internal demo seed state visible to anyone who can reach the debug endpoint. Exposes `demo@decoda.app` email and seed diagnostics | No — debug endpoint should not be publicly reachable | Gate `/debug/fixtures` behind admin authentication or remove from production routes | 6 |
| F-17 | Low | `services/api/app/main.py:308,:316` | `DEFAULT_RISK_SAMPLE_REQUEST` with `tx_hash: '0xphase1sample'` used as risk-engine request fallback payload | Sample tx hash appears in logs/traces when risk-engine falls back. Not customer-visible in UI, but pollutes audit logs | Partial — only in server logs | Replace sample tx hash with a deterministic but clearly labeled test marker | 6 |
| F-18 | Low | `services/threat-engine/data/safe_transaction.json:20` | `"description": "Safe settlement scenario for UI demos."` | This string is embedded in threat-engine data file. Not customer-facing but could appear in threat-engine response metadata | Partial — depends on whether threat-engine returns raw metadata | Remove "UI demos" from production data description | 6 |
| F-19 | Low | `services/risk-engine/scripts/seed.py:31` | `'VaR and stress thresholds loaded from local sample data.'` in seed metric value | Dev seed only; not production-facing | Yes — seed scripts are dev-only | No action required | N/A |
| F-20 | Low | All `services/*/scripts/seed.py` files | Seed scripts with dev-only service metrics | Dev-only, never reach production | Yes | No action required | N/A |

---

## 3. Categorized Findings

### Customer-Facing UI Leakage

- **F-04** — "All pipeline stages are operational" fires without simulator evidence guard
- **F-05** — Fallback alerts/incidents mislabeled as "Simulator"
- **F-11** — Pilot mode banner implies live feed is temporarily down rather than not configured
- **F-12** — "All systems operational" headline not guarded against simulator-only evidence
- **F-15** — Settings page uses "sample" terminology

### Backend/API Fallback Behavior

- **F-01** — Compliance fallback dashboard served cross-workspace on service outage
- **F-02** — Resilience fallback dashboard served cross-workspace on service outage
- **F-06** — Hardcoded timestamps/IDs in compliance fallback payload
- **F-07** — Hardcoded severity/attestation hashes in resilience fallback payload
- **F-13** — `'Fallback'` string in health status API response
- **F-14** — "demo-safe records" in FastAPI route description string
- **F-17** — `0xphase1sample` tx hash in risk-engine sample request

### Runtime Status Truthfulness Risk

- **F-04** — Pipeline operational claim not guarded against simulator telemetry
- **F-10** — `DEMO_ONLY` detections not guaranteed to be filtered on detection-list pages
- **F-12** — `healthProvable` not explicitly excluding simulator evidence source

### Evidence/Export Truthfulness Risk

- **F-01/F-02** — Fallback governance actions and incidents carry `attestation_hash` values (`fallback-003`, `fallback-event-0001`) that could be interpreted as real cryptographic proofs
- **F-09** — `sample_scenarios` dicts in compliance/threat fallback payloads are latent export risk

### Test/Seed/Dev-Only Acceptable Usage

- **F-19** — `risk-engine/scripts/seed.py` sample metric values (dev-only)
- **F-20** — All `services/*/scripts/seed.py` files (dev-only)
- All `apps/web/tests/` mock/fixture usage is correct and acceptable
- `services/api/tests/` FakeConnection/fake_health helpers are test-only and acceptable

### Documentation-Only Acceptable Usage

- `docs/MONITORING_DEMO_LEAKAGE_AUDIT.md`, `docs/DEMO_ISOLATION_AUDIT.md` — prior audits, acceptable
- `docs/FEATURE1_STAGING_EVIDENCE_FLOW.md` — staging context, acceptable

---

## 4. Specific SaaS Truthfulness Checks

### No data must not equal safe

**Status: PASS with caveat.**
`customer-status-badge.ts` and `dashboard-status-presentation.ts` both map `unavailable` to a `critical` tone (not safe). The `EmptyStateBlocker` component correctly prevents silent empty states. However, the compliance and resilience pages may render fallback counts (e.g., `allowlisted_wallet_count: 2`) that give a false impression of data presence when the service is actually down (F-01, F-02).

### No alert must not equal healthy

**Status: PASS.**
`monitoringHealthyCopyAllowed` requires `active_alerts_count` checks are not in scope here (it gates on `continuity_status` and `reporting_systems_count`), and alert pages show empty-state blockers when no alerts exist. No silent "healthy" claim from empty alert list was found.

### Heartbeat must not equal telemetry

**Status: PASS.**
The pipeline node state machine in `threat-monitoring-panel.tsx` separates `Heartbeat`, `Poll`, and `Telemetry` as distinct nodes with separate timestamps (`last_heartbeat_at`, `last_poll_at`, `last_telemetry_at`). `monitoringHealthyCopyAllowed` requires `hasLiveTelemetry(truth)` which checks `last_telemetry_at` separately from heartbeat.

### Poll must not equal telemetry

**Status: PASS.**
`last_poll_at` and `last_telemetry_at` are tracked and displayed as separate fields across `workspace-monitoring-truth.ts`, `system-health/page.tsx`, and `monitoring-status-presentation.ts`. Poll completion does not satisfy the telemetry freshness check.

### Simulator must not equal live evidence

**Status: MOSTLY PASS with gaps at F-04 and F-12.**
The evidence source discrimination system is extensive and well-tested. `NON_LIVE_PROVIDER_SOURCE_TYPES` excludes `demo`, `simulator`, `replay`, `unknown` from live-provider classification. However, the "All pipeline stages are operational" claim (F-04) and the `healthProvable` "All systems operational" headline (F-12) are not fully guarded against simulator-only evidence.

### Fallback must not equal customer evidence

**Status: FAIL for F-01, F-02, F-06, F-07.**
The compliance and resilience fallback payloads contain governance action IDs, attestation hashes, and incident records that can be read as real customer evidence. While tagged `source: 'fallback'` internally, the customer-facing pages do not surface a visible warning.

### Offline must not conflict with live telemetry

**Status: PASS.**
`workspace-monitoring-truth.ts` `monitoringHealthyCopyAllowed` requires `runtime_status === 'live'` and `continuity_status === 'continuous_live'`. Offline runtime status prevents healthy claims. No contradicting UI path was found.

### Operational must require reporting systems and fresh telemetry

**Status: MOSTLY PASS with gap at F-04.**
`monitoringHealthyCopyAllowed` correctly requires `reporting_systems_count > 0`, `hasLiveTelemetry`, and `continuity_status === 'continuous_live'`. The gap is in `threat-monitoring-panel.tsx` where the "All pipeline stages are operational" message is controlled by the `blocker` null check, which does not invoke `monitoringHealthyCopyAllowed`.

---

## 5. Fix Plan: Future Claude Sessions

### Session 2: Runtime Truthfulness (Priority: Urgent)

Scope:
- Fix F-04: Add simulator evidence guard to "All pipeline stages are operational" claim in `threat-monitoring-panel.tsx`
- Fix F-05: Change `source === 'fallback'` pill label from "Simulator" to "Unavailable" in `dashboard-executive-summary.tsx`
- Fix F-12: Add `evidence_source_summary !== 'simulator'` guard to `healthProvable` in `dashboard-executive-summary.tsx`
- Fix F-01/F-02 (API layer): Replace `fallback_compliance_dashboard()` and `fallback_resilience_dashboard()` on live API routes with workspace-scoped "service unavailable" shells (zero counts, no records, no fake IDs)

Prompt title: **"Session 2: Fix runtime truthfulness — operational claims, fallback label mislabeling, API compliance/resilience fallback cross-workspace data"**

### Session 3: Asset-Target-Monitoring Linkage

Scope:
- Audit the asset → target → monitoring config chain for any unscoped or demo-seeded linkage
- Ensure monitoring config always requires a real asset and target before permitting telemetry ingestion
- Validate that demo-seeded domain targets (F-01 adjacent) cannot be confused with customer targets

Prompt title: **"Session 3: Audit asset-target-monitoring linkage for demo/unscoped contamination"**

### Session 4: Detection-Alert-Incident-Action Chain

Scope:
- Fix F-10: Add visible label/filter for `detection_outcome === 'DEMO_ONLY'` on detection-list and alert pages
- Fix F-09: Strip `sample_scenarios` dict from all customer-facing API responses
- Audit that `DEMO_EVIDENCE` evidence state is never presented as real customer evidence in the incident response chain

Prompt title: **"Session 4: Fix detection-alert-incident chain demo/fallback evidence labeling"**

### Session 5: Workspace Authorization

Scope:
- Verify all API routes use workspace-scoped queries (no cross-tenant fallback data)
- Confirm compliance and resilience endpoints after Session 2 fix return workspace-scoped data only
- Audit monitoring runner queries for unscoped cross-workspace evidence ingestion

Prompt title: **"Session 5: Workspace authorization audit — verify all API routes are scoped to requesting workspace"**

### Session 6: Production Readiness

Scope:
- Fix F-08: Document and enforce that `ENABLE_DEMO_FALLBACKS` must never be set on any customer-reachable deployment
- Fix F-13: Relabel `'Fallback'` to `'Unavailable'` in health status API responses
- Fix F-14: Update FastAPI route descriptions to remove "demo-safe" language
- Fix F-16: Gate `/debug/fixtures` behind admin auth or remove from production
- Fix F-17: Replace `0xphase1sample` tx hash in `DEFAULT_RISK_SAMPLE_REQUEST`
- Fix F-18: Remove "UI demos" from `safe_transaction.json` description

Prompt title: **"Session 6: Production readiness — env var guards, API description cleanup, debug endpoint hardening"**

### Session 7: Evidence/Export Proof Quality

Scope:
- Fix F-03: Add prominent "service unavailable" banner to risk/compliance/resilience dashboard pages when `source === 'fallback'`
- Fix F-06/F-07: Replace hardcoded attestation hashes in fallback payloads with clearly invalid placeholder format (`attestation_hash: 'N/A (service unavailable)'`)
- Verify that export/evidence download routes cannot include fallback or simulator records without explicit customer consent labeling

Prompt title: **"Session 7: Evidence and export proof quality — fallback attestation hashes, service unavailable banners, export guards"**

### Session 8: Onboarding/Dashboard Polish

Scope:
- Fix F-11: Update pilot-mode banner copy from "Live feed temporarily unavailable" to "Live workspace not configured"
- Fix F-15: Update settings page "Using sample data only" to "Live data source not connected"
- Audit onboarding flow for any demo/simulator data that could be confused with first real customer data

Prompt title: **"Session 8: Onboarding and dashboard copy polish — remove internal terminology from customer surfaces"**

### Session 9: Final Workflow Validation

Scope:
- End-to-end validation of the signup → workspace → onboarding → asset → target → monitoring → telemetry → detection → alert → incident → action → export workflow
- Confirm no demo/fallback/simulator data appears in the workflow without explicit labeling
- Run full test suite after all sessions complete

Prompt title: **"Session 9: Final workflow validation — end-to-end SaaS truthfulness pass"**

---

## 6. Prioritized Next Action

**Exact next Claude prompt title:**

> **"Session 2: Fix runtime truthfulness — operational claims, fallback label mislabeling, and API compliance/resilience cross-workspace fallback data"**
>
> Files to change:
> - `apps/web/app/threat-monitoring-panel.tsx` — guard "All pipeline stages are operational" against simulator mode
> - `apps/web/app/dashboard-executive-summary.tsx` — change `source === 'fallback'` pill label from "Simulator" to "Unavailable"; add `evidence_source_summary !== 'simulator'` to `healthProvable`
> - `services/api/app/main.py` — replace `fallback_compliance_dashboard()` and `fallback_resilience_dashboard()` on live API routes with workspace-scoped "service unavailable" shells
> - Add or update tests for each change

---

## Appendix: Key Evidence Locations

| Concern | File | Lines |
|---------|------|-------|
| `fallbackSnapshotsEnabled` guard | `apps/web/app/dashboard-data.ts` | 1056–1061 |
| Full risk fallback snapshot | `apps/web/app/dashboard-data.ts` | 352–509 |
| Full compliance fallback snapshot | `apps/web/app/dashboard-data.ts` | 523–645 |
| Full resilience fallback snapshot | `apps/web/app/dashboard-data.ts` | 646–745 |
| Full threat fallback snapshot | `apps/web/app/dashboard-data.ts` | 760–895 |
| `0xphase1sample` tx hash | `apps/web/app/dashboard-data.ts` | 375, 462 |
| `gov-fallback-003` action IDs | `apps/web/app/dashboard-data.ts` | 581–623 |
| API compliance fallback route | `services/api/app/main.py` | 1917–1964 |
| API resilience fallback route | `services/api/app/main.py` | 1967–2027 |
| API `fallback_compliance_dashboard()` | `services/api/app/main.py` | 4299–4383 |
| API `fallback_resilience_dashboard()` | `services/api/app/main.py` | 4471–4662 |
| "All pipeline stages are operational" | `apps/web/app/threat-monitoring-panel.tsx` | 597–600 |
| Blocker logic (no simulator guard) | `apps/web/app/threat-monitoring-panel.tsx` | 263–330 |
| Fallback alerts/incidents → "Simulator" pill | `apps/web/app/dashboard-executive-summary.tsx` | 461–463, 523–525 |
| `healthProvable` without simulator guard | `apps/web/app/dashboard-executive-summary.tsx` | 125–127 |
| `monitoringHealthyCopyAllowed` definition | `apps/web/app/workspace-monitoring-truth.ts` | 229–239 |
| Pilot mode banner copy | `apps/web/app/pilot-mode-banner.tsx` | 19–20 |
| Settings "Using sample data only" | `apps/web/app/settings-page-client.tsx` | 370 |
| Public SaaS hardening test | `apps/web/tests/public-saas-hardening.spec.ts` | 14–34 |
| `DEMO_ONLY` detection outcome | `services/api/app/monitoring_runner.py` | 2259–2290 |
| `NON_LIVE_PROVIDER_SOURCE_TYPES` | `services/api/app/monitoring_runner.py` | 85 |
