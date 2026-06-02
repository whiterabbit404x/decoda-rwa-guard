# NIW Strategic Infrastructure Guard Positioning

**Decoda RWA Guard — National Interest Waiver (NIW) Evidence Document**

Last updated: **2026-06-02**

> **Honesty notice:** This document describes what is implemented and tested in the codebase today. Claims are limited to what is provable from repository artifacts. Controlled pilot readiness may be claimed where pilot gates pass. Broad paid SaaS readiness is explicitly not claimed because billing, email, staging runtime, staging database, and staging worker configuration are not yet proven in production mode. See `RELEASE_READY.md` for current gate status.

---

## 1. Proposed Endeavor

**Decoda RWA Guard** is a cybersecurity monitoring platform purpose-built for tokenized real-world asset (RWA) infrastructure, with a primary focus on U.S. Treasury bills, tokenized Treasuries, and debt-market settlement systems operating on distributed ledger networks.

The platform continuously monitors on-chain and off-chain signals for:

- Anomalous price deviations and oracle manipulation in tokenized Treasury markets
- Zero-day exploit vectors targeting smart-contract settlement infrastructure
- Cross-chain reconciliation failures that could propagate systemic risk across debt-market rails
- Sovereign compliance and geopatriation control violations in regulated asset pools
- Incident detection, alert escalation, and evidence export across the full asset → target → monitoring → detection → alert → incident → response → audit workflow

The proposed endeavor is to advance this platform from controlled pilot readiness into broad operational deployment protecting the infrastructure layer through which tokenized U.S. Treasuries and other tokenized debt instruments settle, are custodied, and are distributed to institutional and retail participants.

---

## 2. National Importance

Tokenized U.S. Treasury bills and tokenized Treasuries represent one of the fastest-growing segments of the digital asset ecosystem. As of 2024–2026, on-chain Treasury products have grown from under $1 billion to multi-billion dollar pools, managed by institutional issuers including BlackRock, Franklin Templeton, Ondo Finance, and others.

This infrastructure is nationally significant for three reasons:

1. **Federal debt-market stability:** Treasury bills are the primary instrument the U.S. government uses to fund short-term obligations. Tokenized versions introduce programmable settlement, fractionalization, and 24/7 liquidity—capabilities that reduce friction but also introduce novel attack surfaces including oracle manipulation, smart-contract exploit, and cross-chain settlement failure.

2. **Financial stability contagion risk:** A successful exploit of a large tokenized Treasury pool would not be isolated to digital asset holders. The underlying collateral is U.S. government debt. Cascading liquidations or oracle manipulation could trigger repricing across institutional portfolios, money market funds, and repo markets that hold equivalent instruments.

3. **Strategic competitive position:** Nation-state adversaries have demonstrated capability and intent to target blockchain-based financial infrastructure (Lazarus Group Treasury exploits, cross-chain bridge attacks totaling over $3 billion since 2022). Robust monitoring infrastructure protecting U.S.-issued tokenized debt is a national security asset.

---

## 3. Financial Stability Relevance

The Financial Stability Oversight Council (FSOC) 2023 Annual Report identified digital assets and stablecoins as an emerging financial stability risk. The OFR and Federal Reserve have specifically flagged the potential for tokenized Treasury products to amplify run dynamics if redemption and settlement infrastructure fails under stress.

Decoda RWA Guard directly addresses the monitoring gap identified in these reports by providing:

- **Real-time telemetry** from on-chain oracle feeds, price sources, and settlement workers
- **Fail-closed detection** that treats missing telemetry as a risk signal, never as safe
- **Workspace-scoped evidence chains** from raw telemetry through detection → alert → incident → response action → exportable audit bundle
- **Truthful runtime status** derived from canonical backend facts, not frontend assumptions

These capabilities map directly to the monitoring and early-warning functions that regulators have cited as missing from current market structure.

---

## 4. U.S. Treasury and Tokenized Debt-Market Infrastructure Relevance

The platform's architecture is designed around the specific risk topology of tokenized Treasuries and debt-market settlement:

| Risk vector | Platform capability | Repo artifact |
|---|---|---|
| Oracle price manipulation | Real-time oracle monitoring worker with anomaly scoring | `services/oracle-service/` |
| Smart-contract exploit / zero-day | Threat engine with explainable detection and market anomaly scoring | `services/threat-engine/` |
| Cross-chain reconciliation failure | Deterministic reconciliation service with backstop controls | `services/reconciliation-service/` |
| Sovereign compliance / geopatriation | Compliance service with governance action enforcement | `services/compliance-service/` |
| Evidence export for regulatory response | Proof bundle export, audit log, incident evidence chain | `services/api/app/evidence_export.py` |
| Fail-closed monitoring runtime | Runtime truthfulness tests, heartbeat/poll/telemetry separation | `services/api/tests/test_runtime_truthfulness.py` |

The monitoring target model explicitly distinguishes heartbeat (worker alive), poll (monitoring loop ran), and telemetry (asset data arrived), preventing false-healthy status for tokenized Treasury pools under active monitoring.

---

## 5. Critical and Emerging Technologies (CET) Alignment

This platform aligns with the **2024 U.S. Critical and Emerging Technologies List** category:

> **Data Privacy, Data Security, and Cybersecurity Technologies**

Specific subfield alignment:

| CET subfield | Platform alignment |
|---|---|
| **Distributed ledger technologies** | Monitoring architecture targets EVM-compatible chains carrying tokenized Treasuries; oracle worker polls on-chain state; event-watcher ingests on-chain settlement events |
| **Digital assets** | Primary protected asset class is tokenized U.S. Treasury bills and tokenized debt instruments; asset registry models tokenized asset metadata and custody chain |
| **Digital payment technologies** | Settlement reconciliation service monitors cross-chain payment finality and flags reconciliation failures in debt-instrument transfer rails |
| **Communications and network security** | API gateway enforces workspace-scoped access control; secret scanning on all proof artifacts; JWT session security with role validation; no cross-tenant data leakage by architecture rule |
| **Privacy-enhancing technologies** | Workspace isolation enforced at data layer; secrets never returned in API responses (boolean presence flags only); audit log provides traceable evidence without exposing raw credentials |

---

## 6. Evidence Map to Repository Artifacts

The following table maps NIW-relevant claims to concrete repository artifacts that can be independently verified.

| Claim | Artifact | Verification command |
|---|---|---|
| Tokenized Treasury monitoring architecture exists | `services/oracle-service/`, `services/api/app/monitoring_targets.py` | `grep -r "treasury" services/ --include="*.py" -l` |
| Fail-closed runtime truthfulness enforced | `services/api/tests/test_runtime_truthfulness.py` | `python -m pytest services/api/tests/test_runtime_truthfulness.py -q` |
| Asset → target → monitoring → detection → alert → incident → action → export chain | `services/api/tests/test_detection_alert_incident_action_chain.py` | `python -m pytest services/api/tests/test_detection_alert_incident_action_chain.py -q` |
| Workspace-scoped isolation (no cross-tenant leakage) | `services/api/tests/test_workspace_readiness_gate_aggregation.py` | `python -m pytest services/api/tests/test_workspace_readiness_gate_aggregation.py -q` |
| Live evidence distinct from simulator evidence | `services/api/app/paid_launch_readiness.py`, `services/api/tests/test_paid_launch_readiness.py` | `python -m pytest services/api/tests/test_paid_launch_readiness.py -q` |
| Proof bundle / evidence export for regulatory response | `services/api/tests/test_proof_bundle_export.py` | `python -m pytest services/api/tests/test_proof_bundle_export.py -q` |
| Controlled pilot readiness gate exists | `RELEASE_READY.md`, `artifacts/launch-proof/` | `make validate-readiness-proof` |
| Reconciliation / cross-chain settlement monitoring | `services/reconciliation-service/` | `ls services/reconciliation-service/` |
| Compliance / sovereign governance enforcement | `services/compliance-service/` | `ls services/compliance-service/` |
| Secret scanning on all proof artifacts | `scripts/validate_release_proof.py` | `python scripts/validate_release_proof.py` |

See `artifacts/niw-strategic-infrastructure-guard/evidence-map.json` for machine-readable version.

---

## 7. Truthful Limitations and Next Milestones

### What can be claimed today (as of 2026-06-02)

- **Controlled pilot readiness:** The platform passes pilot gates when `make validate-readiness-proof` passes with no fail-closed violations. Pilot evidence artifacts are present under `artifacts/launch-proof/latest/`.
- **Live evidence architecture:** The codebase distinguishes live provider evidence from simulator evidence in all readiness gates. Live evidence is tracked separately and cannot be substituted with simulator data.
- **Fail-closed monitoring runtime:** Runtime status is derived from canonical backend facts (heartbeat, poll, telemetry). Missing telemetry is treated as risk, not as safe.
- **Full SaaS workflow implemented:** The complete signup → workspace → onboarding → asset registry → monitoring target → monitoring config → runtime status → telemetry → detection → alert → incident → response action → evidence export → audit log workflow is implemented and tested.

### What cannot be claimed today

- **Broad paid SaaS readiness:** Not ready. Billing provider, email provider, staging runtime, staging database, and staging worker are not yet configured in production mode. Gates remain fail-closed on these blockers. See `RELEASE_READY.md` "Broad self-serve readiness" section.
- **Production deployment evidence:** No confirmed staging or production deployment with live credentials has been run to completion. `safe_to_sell_broadly_today` is `false` in all current validation modes.
- **Formal compliance certifications:** No SOC 2, FedRAMP, ISO 27001, or equivalent audit has been performed. Claims are limited to codebase-provable controls.
- **Regulatory approval:** No FinCEN, SEC, CFTC, or OCC regulatory approval or no-action letter has been obtained.

### Next milestones before broad paid SaaS

1. Configure and validate Stripe/Paddle billing in production mode.
2. Configure and validate email provider (SendGrid/Resend) with verified sender domain.
3. Configure and validate staging environment (STAGING_API_URL, STAGING_DATABASE_URL, STAGING_WORKER_ENABLED).
4. Run `make validate-100-percent-readiness` in `--mode staging --strict` and confirm `safe_to_sell_broadly_today=true`.
5. Attach CI artifact bundle (`artifacts/release-proof/latest/`) to release decision.

---

## 8. Readiness Truth Table

The following table is the authoritative single-source-of-truth for what may and may not be claimed as of the current repository state. It is derived from `artifacts/launch-proof/latest/summary.json` and must remain consistent with `artifacts/live-evidence-proof/latest/summary.json`, `artifacts/sell-now-proof/latest/summary.json`, and `scripts/validate_niw_positioning.py`.

| Readiness category | Status | Basis |
|---|---|---|
| NIW positioning | **ready** | `docs/NIW_STRATEGIC_INFRASTRUCTURE_GUARD.md` + `artifacts/niw-strategic-infrastructure-guard/evidence-map.json` pass `validate_niw_positioning.py` |
| Live provider evidence | **ready** | `artifacts/live-evidence-proof/latest/summary.json` confirms `provider_ready=true`, `provider_mode=live`, `live_evidence_ready=true`, `evidence_source=live` |
| Controlled pilot / managed sale | **ready** | `artifacts/sell-now-proof/latest/summary.json` confirms `sell_now_managed_ready=true` |
| Broad paid SaaS | **not ready** | Staging runtime, staging database, staging worker, billing, and email providers are not yet configured in production mode |

**What may be claimed:**
- NIW Strategic Infrastructure Guard positioning ready
- Controlled pilot / managed sale ready
- Live provider evidence ready
- not broad paid SaaS ready

**What may not be claimed:**
- Broad paid SaaS production ready
- Billing ready
- Staging runtime fully ready
- Staging database fully ready
- Worker fully ready

**Reason broad paid SaaS is not ready:** `STAGING_API_URL`, `STAGING_DATABASE_URL`, `STAGING_WORKER_ENABLED`, `BILLING_PROVIDER`, and `EMAIL_PROVIDER` are not set to production values. Gates remain fail-closed on all unproven blockers.

---

## 9. Summary

Decoda RWA Guard is a cybersecurity platform for tokenized Treasury and RWA settlement infrastructure that:

- Addresses a concrete national security and financial stability gap in U.S. debt-market tokenization
- Aligns with the 2024 U.S. CET list under "Data Privacy, Data Security, and Cybersecurity Technologies" across five subfields
- Is implemented with truthful, fail-closed readiness gates that prevent false claims of live monitoring health
- Supports controlled pilot operations today, with a documented path to broad paid SaaS readiness
- Generates exportable evidence chains for regulatory response, audit, and incident review

The platform is not broad paid SaaS ready today. It is controlled pilot ready when pilot gates pass, and the full SaaS workflow is implemented and tested end-to-end.
