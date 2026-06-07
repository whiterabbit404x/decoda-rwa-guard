# Enterprise Readiness

Last updated: **2026-05-22**

This document describes the current enterprise readiness posture of **Decoda RWA Guard**.

> **Honesty notice:** This document describes what is implemented and tested in the codebase today. It does not claim SOC 2, ISO 27001, FedRAMP, PCI DSS, HIPAA, or any other formal audit certification unless the repository contains certified evidence of such. Claims in this document are limited to what is provable from codebase artifacts.

---

## 1. Security Posture Summary

### Authentication and Session Management
- JWT-based auth sessions with workspace-scoped access control.
- Auth session tables include `expires_at`, role validation, and workspace membership checks.
- Session creation and rotation are covered by test suite (`test_pilot_auth_self_serve.py`, `test_auth_security_foundation.py`).
- Rate limiting infrastructure is defined in `services/api/app/pilot.py`.

### Access Control
- All API routes are workspace-scoped; cross-tenant queries are prohibited by architecture rules.
- Role mappings and workspace membership are enforced at the data layer.
- No unscoped cross-tenant queries are permitted by `CLAUDE.md` architecture rules.

### Data Protection
- Secret encryption utilities covered by `test_secret_crypto.py`.
- Secrets are never included in proof artifacts or admin readiness endpoints (only boolean presence flags and env var names are returned).
- Secret scanning is performed on all proof artifacts by `scripts/validate_release_proof.py`.

### Known Security Limitations
- No formal penetration test has been performed on this codebase.
- OWASP ASVS compliance has not been formally verified.
- No WAF, DDoS protection, or advanced threat detection is configured in this repository.
- TLS termination, network segmentation, and cloud security controls are deployment-environment responsibilities.

---

## 2. Tenant Isolation Summary

- All monitoring, telemetry, detection, alert, incident, and evidence records are workspace-scoped.
- Workspace isolation is verified by `test_workspace_readiness_gate_aggregation.py` and `test_saas_workflow_validation.py`.
- The SaaS workflow test confirms end-to-end workspace isolation: User → Workspace → Asset → Target → Config → Telemetry → Detection → Alert → Incident → Action → Export.
- Cross-workspace data leakage is architecturally prohibited; any change introducing unscoped queries must be rejected.

---

## 3. Runtime Truthfulness Summary

- Signal independence is enforced: heartbeat, poll, and telemetry are independent signals with separate freshness thresholds.
- Contradiction guards prevent impossible states (e.g., healthy monitoring with zero telemetry).
- 12 contradiction conditions are defined and tested in `test_runtime_truthfulness.py`.
- No data is never presented as safe; no alert is never presented as healthy.
- Simulator evidence is never presented as live customer evidence.
- Full specification: `docs/RUNTIME_TRUTHFULNESS.md`

---

## 4. Evidence Export Truthfulness Summary

- All exported evidence packages include source labels: `live`, `simulator`, `fixture`, `unavailable`, `missing`, or `unknown`.
- Simulator or fixture evidence is never presented as customer-facing live proof.
- Package status values (`complete`, `partial`, `blocked`) are derived from canonical backend facts.
- Export truthfulness is verified by `test_evidence_export_truthfulness.py` and `test_proof_bundle_export.py`.
- Full specification: `docs/EVIDENCE_EXPORT_TRUTHFULNESS.md`

---

## 5. Billing/Email/Provider Readiness Summary

Broad paid SaaS launch requires all of the following gates to pass:

| Gate | Env Var(s) | Status |
|------|-----------|--------|
| Billing provider | `BILLING_PROVIDER`, `STRIPE_SECRET_KEY` / `PADDLE_API_KEY` | **Blocked in local/CI** |
| Billing webhook | `STRIPE_WEBHOOK_SECRET` / `PADDLE_WEBHOOK_SECRET` | **Blocked in local/CI** |
| Email provider | `EMAIL_PROVIDER`, `SENDGRID_API_KEY` / `RESEND_API_KEY` / `SMTP_*` | **Blocked in local/CI** |
| Live chain provider | `EVM_RPC_URL` (non-placeholder) | **Blocked in local/CI** |

- Fail-closed: `BILLING_PROVIDER=none` or absent → `billing_ready=false`
- Placeholder values (e.g. `your_key`, `example`) → treated as misconfigured
- Tested by `test_paid_launch_readiness.py`
- Implementation: `services/api/app/paid_launch_readiness.py`

---

## 6. Release Proof Artifact Paths

| Artifact | Path |
|---------|------|
| CI required gates | `artifacts/release-proof/latest/ci-required-gates.json` |
| Release proof summary | `artifacts/release-proof/latest/summary.json` |
| Release proof manifest | `artifacts/release-proof/latest/manifest.json` |
| Test report summary | `artifacts/release-proof/latest/test-report-summary.json` |
| Launch proof summary | `artifacts/launch-proof/latest/summary.json` |
| Final readiness summary | `artifacts/final-readiness/latest/summary.json` |

All artifacts include SHA256 integrity fields and are validated by `scripts/validate_release_proof.py`.

---

## 7. Incident/Audit Evidence Model

The canonical SaaS workflow chain is:

```
Signup/Login → Workspace → Onboarding → Asset Registry →
Monitoring Target → Monitoring Config → Runtime Status →
Telemetry → Detection → Alert → Incident →
Response Action → Evidence/Export/Audit
```

- Every step is tested in `test_saas_workflow_validation.py`.
- Incident timelines are covered by `test_monitoring_investigation_timeline.py`.
- Response action governance is covered by `test_response_action_live_governance.py`.
- Evidence export chain completeness is verified by `test_proof_bundle_export.py`.
- Audit exports include source labels, timestamps, and workspace scope.

---

## 8. Known Limitations

1. **No live staging credentials in CI**: Billing, email, and provider credentials are not present in normal CI runs. This is intentional and fail-closed. CI proves fail-closed behavior, not live provider readiness.
2. **No formal compliance certification**: SOC 2, ISO 27001, FedRAMP, and similar certifications have not been obtained. Do not claim these without evidence.
3. **No penetration test**: No third-party security assessment has been performed.
4. **Simulator evidence is not live evidence**: Any guided simulator or demo data is labeled as `simulator` and cannot be used as customer proof.
5. **Frontend build not proven in CI**: The frontend build test (`npm run build`) runs in the `required-gates` CI job but may not run locally without Node.js dependencies.
6. **Staging validation is manual**: The `staging_validation` gate requires real execution with live credentials; it cannot be proven in local or standard CI mode.

---

## 9. Required Production Environment Variables

### Core platform
```
DATABASE_URL
SECRET_KEY
APP_BASE_URL
API_BASE_URL
```

### Billing (required for broad paid launch)
```
BILLING_PROVIDER=paddle
PADDLE_ENVIRONMENT=production
PADDLE_API_KEY=...
PADDLE_WEBHOOK_SECRET=...
PADDLE_PRICE_ID=pri_...
# Stripe is an alternative only when BILLING_PROVIDER=stripe:
# STRIPE_SECRET_KEY=sk_live_...
# STRIPE_WEBHOOK_SECRET=whsec_...
# STRIPE_PRICE_ID=price_...
```

### Email (required for broad paid launch)
```
EMAIL_PROVIDER=sendgrid|resend|smtp
SENDGRID_API_KEY=SG....
# or RESEND_API_KEY / SMTP_HOST+SMTP_USER+SMTP_PASSWORD
EMAIL_FROM=noreply@yourdomain.com
EMAIL_DOMAIN=yourdomain.com
```

### Chain provider (required for monitoring)
```
EVM_RPC_URL=https://mainnet.infura.io/v3/...
```

### Live evidence (required for broad paid SaaS)
```
LIVE_PROVIDER_PROOF_PRESENT=true
```

---

## 10. Staging Validation Checklist

Before marking `safe_to_sell_broadly_today=true`, complete all of the following:

- [ ] Deploy to a real staging environment with live credentials
- [ ] Confirm the selected billing provider has its provider-specific credentials, webhook secret, price ID, and environment configured (Paddle does not require Stripe variables)
- [ ] Confirm `EMAIL_PROVIDER` and corresponding API key are set with live values
- [ ] Confirm `EVM_RPC_URL` points to a live (non-placeholder) RPC endpoint
- [ ] Confirm `LIVE_PROVIDER_PROOF_PRESENT=true`
- [ ] Run `python scripts/validate_100_percent_readiness.py --mode staging --strict`
- [ ] Verify `artifacts/final-readiness/latest/summary.json` shows `production_100_percent_ready: true`
- [ ] Verify `safe_to_sell_broadly_today: true` in the summary
- [ ] Archive the final-readiness summary alongside the release decision

---

## 11. Simulator Proof Disclaimer

> **Simulator evidence is not live provider proof.**
>
> Any evidence generated by guided simulator scripts (`generate_guided_simulator_readiness_bundle.py`, demo seed data, or any script with `simulator` in its name) is labeled with `evidence_source: simulator` and **cannot** satisfy the `live_evidence_ready` gate.
>
> The `safe_to_sell_broadly_today` flag will remain `false` until live evidence from a real blockchain RPC endpoint is confirmed.

---

## 12. How to Run Final Readiness Validation

```bash
# Local/CI fail-closed mode (expected: production_100_percent_ready=false)
python scripts/validate_100_percent_readiness.py --mode local

# CI mode (same as local, used in GitHub Actions)
python scripts/validate_100_percent_readiness.py --mode ci

# Staging strict mode (requires real credentials; safe_to_sell_broadly_today possible)
python scripts/validate_100_percent_readiness.py --mode staging --strict

# Full make target (runs all session test suites + generates proof + validates)
make validate-100-percent-readiness
```

Inspect results:
```bash
cat artifacts/final-readiness/latest/summary.json
```

---

> **Do not sell broadly until `safe_to_sell_broadly_today` is `true` in staging or production strict mode.**
