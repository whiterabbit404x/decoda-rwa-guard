# Incident Response Runbook

**Decoda RWA Guard — On-Call Operator Reference**  
Version: 1.0 · Audience: On-call engineers, security, and operations

For broader disaster recovery, RPO/RTO targets, and data governance see
[`docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md`](../DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md).  
For credential rotation steps see [`docs/OPERATIONS_RUNBOOK.md`](../OPERATIONS_RUNBOOK.md).

---

## 1. Severity Levels

| Severity | Definition | Response target |
|---|---|---|
| **SEV-1** | Active compromise, cross-tenant data disclosure, signing/encryption key exposure, destructive production access | Acknowledge 15 min · IC assigned 15 min · Customer/legal cadence set 60 min |
| **SEV-2** | Significant impact, contained; exploited vulnerability on isolated service, prolonged monitoring blind spot | Acknowledge 30 min · IC assigned 30 min · Stakeholder update 60 min |
| **SEV-3** | Degraded control, safe workaround, no evidence of compromise | Acknowledge 4 business hours · Status in normal channels |
| **SEV-4** | Minor defect, non-sensitive misconfiguration, policy exception | Acknowledge 2 business days |

The **first qualified responder** is Incident Commander (IC) until explicit handoff is recorded.  
Page security on-call for every SEV-1/SEV-2. Escalate to executive owner and legal/privacy for suspected
personal-data, regulated-data, or customer-secret impact.

---

## 2. Alert Triage Process

1. **Receive alert** — source: monitoring system, customer report, automated detection, security scanner.
2. **Open a restricted incident channel** (e.g. `#incident-YYYYMMDD-N`) and create an incident record.
3. **Record scope**: affected tenants, workspaces, services, indicators, earliest timestamp.
4. **Assign severity** using the table above.
5. **Do not take containment action before preserving evidence** (see §5).

### Quick health endpoints (no auth required for liveness, Bearer token for readiness)

```bash
# Is the API process alive?
curl https://api.decoda.app/health

# Is the API ready to serve traffic? (Bearer token required)
curl -H "Authorization: Bearer $PROBE_TOKEN" https://api.decoda.app/health/readiness

# Full deployment diagnostics
curl -H "Authorization: Bearer $PROBE_TOKEN" https://api.decoda.app/health/details

# Monitoring worker heartbeat
curl -H "Authorization: Bearer $PROBE_TOKEN" https://api.decoda.app/ops/monitoring/health

# Retention worker health
curl -H "Authorization: Bearer $PROBE_TOKEN" https://api.decoda.app/ops/retention/health
```

---

## 3. Suspected False Positive Process

1. Document the detection: alert ID, workspace, detection type, timestamp.
2. Review the detection evidence at `GET /alerts/{alert_id}/evidence`.
3. Check monitoring run context: `GET /monitoring/runs` filtered to the relevant workspace.
4. If the raw on-chain data confirms no anomaly, mark the alert suppressed:
   `POST /alerts/{alert_id}/suppress` with `reason` and `decision`.
5. Create a finding decision record documenting the false-positive determination.
6. **Do not modify or delete** the original detection record — the audit chain is append-only.
7. If the false-positive indicates a systematic detection tuning problem, file a SEV-3/4 to fix the module.

---

## 4. Suspected Live Incident Process

### Initial Response (first 15 minutes for SEV-1)

1. **Declare** — record UTC timestamp, reporter, detection source, affected resources.
2. **Preserve evidence BEFORE containment** (see §5).
3. **Contain** — revoke suspicious sessions, disable compromised integrations, quarantine workloads.
4. **Notify** — page IC, security on-call, and executive owner per severity.

### Investigation

```bash
# Export audit log for affected workspace
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "X-Workspace-Id: $WORKSPACE_ID" \
     -X POST https://api.decoda.app/exports \
     -H "Content-Type: application/json" \
     -d '{"export_type":"report","format":"json","filters":{"report_template":"compliance_audit_export"}}'

# Check workspace monitoring debug snapshot
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "X-Workspace-Id: $WORKSPACE_ID" \
     https://api.decoda.app/monitoring/debug

# List active sessions for a suspect user (requires admin token)
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     https://api.decoda.app/auth/sessions
```

---

## 5. Evidence Export and Preservation

**Collect before destructive action**: snapshot databases, export logs, retain suspicious objects.

```bash
# Create a proof bundle export (cryptographically signed)
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "X-Workspace-Id: $WORKSPACE_ID" \
     -X POST https://api.decoda.app/exports/proof-bundle \
     -H "Content-Type: application/json" \
     -d '{"include_alert_ids": ["<alert-id>"], "notes": "Incident $INCIDENT_ID evidence"}'

# Export the incident report
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "X-Workspace-Id: $WORKSPACE_ID" \
     -X POST https://api.decoda.app/exports/incident-report \
     -H "Content-Type: application/json" \
     -d '{"incident_id": "<incident-id>"}'

# Verify an export signature (local)
python services/api/scripts/export_live_proof_chain.py --verify <export-file>
```

**Hash chain integrity**: every audit log row has a `row_hash` and `previous_row_hash`. Verify chain
integrity by confirming `row_hash` values are monotonically linked:

```sql
SELECT id, created_at, row_hash, previous_row_hash
FROM audit_logs
WHERE workspace_id = '<workspace-id>'
ORDER BY created_at, id;
```

---

## 6. Audit Log Preservation

- Audit logs are append-only. Do NOT delete rows during an incident.
- Legal hold: `POST /workspace/data/legal-holds` before any retention sweep that might touch incident data.
- Audit logs are replicated to the configured export storage (S3/WORM) on each export job.
- If export storage is unavailable, the database copy is the authoritative record.

```bash
# Create a legal hold (prevents retention deletion for the specified data classes)
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "X-Workspace-Id: $WORKSPACE_ID" \
     -X POST https://api.decoda.app/workspace/data/legal-holds \
     -H "Content-Type: application/json" \
     -d '{"data_classes":["audit_logs","detections","telemetry"],"reason":"Incident $INCIDENT_ID hold"}'
```

---

## 7. Customer Notification Guidance

Legal/privacy owns notification determination and deadlines. Security supplies verified facts.

- **Do not speculate** — confirm the scope before notifying.
- Notices must include: what happened, date/time range, affected data/services, containment completed,
  customer actions required, ongoing risk, a monitored contact.
- **Never claim "no access"** solely because access logs are absent.
- For EU/US data: consult legal on GDPR Art. 33/34 (72-hour supervisory authority window) and relevant
  US state/federal requirements.

---

## 8. Rollback Steps

### API service rollback (Railway)

```bash
# Roll back to the previous deployment in Railway dashboard:
# Project → Service → Deployments → select previous → Redeploy

# Or via Railway CLI:
railway rollback --service api
```

### Database migration rollback

Migrations are forward-only. For a bad migration:
1. Restore from the last known-good backup (see `docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md`).
2. Deploy the reverted application version.
3. Test with `GET /health/readiness` and smoke tests: `python -m pytest services/api/tests/test_admin_readiness.py -q`.

### Web frontend rollback (Vercel)

Navigate to Vercel dashboard → Project → Deployments → select previous deployment → Promote to production.

---

## 9. Redis Outage Handling

**Symptoms**: `/health/readiness` reports `REDIS` error; SSE stream returns 503;
rate limiting falls back to per-process memory (warning logged).

**Impact**:
- Real-time alert stream (`/stream/alerts`) becomes unavailable.
- Distributed rate limiting degrades to per-instance.
- Session blacklist may allow recently-revoked sessions for the outage duration.

**Remediation**:
1. Restore Redis connectivity (restart, failover, or provision new instance).
2. Verify: `redis-cli -u $REDIS_URL PING` should return `PONG`.
3. Confirm readiness clears: `GET /health/readiness` should no longer show REDIS error.
4. After restore, previously blacklisted tokens remain invalid (TTL set at blacklist time).
5. Alert stream clients will automatically reconnect via `Last-Event-ID` header.

**Production guard**: If `REDIS_URL` is absent, the API refuses to accept connections
in production (`LIVE_MODE_ENABLED=true`). This is fail-closed by design.

---

## 10. EVM RPC Provider Outage Handling

**Symptoms**: `/ops/monitoring/health` shows stale heartbeat or `rpc_failure` errors;
monitoring runs complete with zero telemetry; alerts for "monitoring blind spot" may fire.

**Remediation**:
1. Check the RPC provider status page (Alchemy, Infura, QuickNode, or private node).
2. If provider-side outage: wait for recovery or switch to backup RPC URL.
3. Update `EVM_RPC_URL_<CHAIN>` environment variable with backup provider URL.
4. Restart the monitoring worker to pick up the new RPC URL.
5. Trigger a manual monitoring run to backfill missed cycles:
   `POST /ops/monitoring/run` with `{"force": true}`.
6. Review the telemetry gap in `/monitoring/runs` and note it in the incident timeline.

**Note**: Detection is paused during an RPC outage. No false negatives are generated — the
system reports "monitoring unavailable" rather than "safe". This is fail-closed by design.

---

## 11. Database Outage Handling

**Symptoms**: API returns 503/500 errors; `/health` reports not-ready;
all authenticated endpoints fail.

**Remediation**:
1. Check database connectivity: `psql "$DATABASE_URL" -c "SELECT 1"`
2. For Neon/hosted PostgreSQL: check provider status page and connection pool limits.
3. If connection pool exhausted: restart API service to reset pool.
4. If database is down: activate standby/replica if configured, or restore from backup.
5. After restore, run migrations: `python scripts/migrate.py` or via startup flag.
6. Verify: `GET /health/readiness` must return `status=ready` before re-routing traffic.

---

## 12. Billing and Webhook Outage Handling

**Symptoms**: Stripe/Paddle events not updating subscriptions; checkout fails; webhook delivery failures.

**Remediation**:
1. Check provider dashboard (Stripe/Paddle) for delivery failures.
2. Verify webhook signature secret: `STRIPE_WEBHOOK_SECRET` or `PADDLE_WEBHOOK_SECRET`.
3. Check API logs for signature validation errors.
4. Replay missed events from provider dashboard after fixing the signature.
5. Manual subscription fix (emergency only, with audit): update `billing_subscriptions` table directly.
6. Billing status: `GET /billing/status` for real-time provider health.

---

## 13. Escalation Checklist

- [ ] Incident channel opened and IC assigned
- [ ] Severity set and acknowledged
- [ ] Affected workspaces and tenants identified
- [ ] Evidence preserved (export bundle created, legal hold placed if needed)
- [ ] Containment action taken (sessions revoked, integrations disabled if needed)
- [ ] Security on-call paged (SEV-1/SEV-2)
- [ ] Executive owner notified (SEV-1/SEV-2)
- [ ] Legal/privacy consulted (if personal data or regulated data affected)
- [ ] Affected infrastructure providers notified via security channels (if their control plane is involved)
- [ ] Customer notification determination made by legal/privacy

---

## 14. Post-Incident Review Checklist

Complete within 5 business days for SEV-1/SEV-2, 10 business days for SEV-3.

- [ ] Root cause identified and documented
- [ ] Full timeline reconstructed (UTC timestamps)
- [ ] Impact assessed: workspaces, users, data, services
- [ ] Containment and recovery actions documented
- [ ] Evidence chain confirmed intact (`row_hash` chain verified)
- [ ] Customer notification rationale recorded (whether sent or not, and why)
- [ ] Control failures identified
- [ ] Follow-up remediation items created with owners and due dates
- [ ] Evidence retention/legal-hold requirements confirmed
- [ ] Blameless review conducted (no individual blame, focus on system failures)
- [ ] Learnings distributed to relevant teams
