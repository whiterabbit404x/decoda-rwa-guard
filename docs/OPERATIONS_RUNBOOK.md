# Operations Runbook

## Health and readiness
- Liveness: `GET /health`
- Production readiness: `GET /health/readiness`
- Deployment diagnostics: `GET /health/details`
- Monitoring worker heartbeat: `GET /ops/monitoring/health`

A production deployment is **not ready** when `/health/readiness` returns `status=not_ready`.

## Secret rotation checklist
Rotate on a fixed cadence (90 days recommended) and immediately after incidents:
1. `AUTH_TOKEN_SECRET`
   - Rotate to a new random value.
   - Deploy API + web together.
   - Expect all active sessions to require re-login.
2. `STRIPE_WEBHOOK_SECRET`
   - Add new webhook endpoint secret in Stripe.
   - Deploy new secret.
   - Remove old secret after successful event verification.
3. `EMAIL_RESEND_API_KEY`
   - Create new key in Resend.
   - Update `EMAIL_PROVIDER=resend` + `EMAIL_RESEND_API_KEY`.
   - Send test via integration health endpoint.
4. Workspace webhook secrets / Slack tokens
   - Rotate from integrations settings for each workspace.
   - Validate test delivery and event signatures.

## Postgres backup + restore drill
1. Create backup
   - `pg_dump "$DATABASE_URL" --format=custom --file=backup.dump`
2. Validate backup file
   - `pg_restore --list backup.dump | head`
3. Restore into staging DB
   - `createdb drill_restore`
   - `pg_restore --no-owner --no-privileges --dbname=drill_restore backup.dump`
4. Run app smoke checks against restored DB
   - `pytest -q services/api/tests/test_pilot_auth_self_serve.py`

## Incident response
### Billing webhook outage
- Symptom: Stripe events not updating subscriptions.
- Actions:
  1. Check Stripe dashboard event delivery failures.
  2. Verify `STRIPE_WEBHOOK_SECRET` and API logs for signature failures.
  3. Replay events from Stripe dashboard after remediation.

### Email outage
- Symptom: verification/reset emails not delivered.
- Actions:
  1. Check `/health/readiness` and integration health email section.
  2. Validate `EMAIL_PROVIDER=resend`, API key, and `EMAIL_FROM`.
  3. Retry test email delivery.

### Redis outage
- Symptom: readiness reports auth limiter degraded.
- Actions:
  1. Restore Redis connectivity.
  2. Confirm `/health/readiness` clears REDIS error.
  3. Monitor auth request latency and 429 patterns.

### Monitoring worker stalled
- Symptom: `/ops/monitoring/health` stale heartbeat.
- Actions:
  1. Restart worker process.
  2. Run one cycle manually via `/ops/monitoring/run`.
  3. Validate new alerts and worker heartbeat timestamp.

## Disaster recovery and data governance

The authoritative RPO/RTO targets, PostgreSQL/Redis/checkpoint/export/webhook recovery procedures, retention and legal-hold workflow, managed-key rotation, isolated restore validation, and regional/provider game-day procedure are maintained in [Disaster Recovery, Data Governance, and Key Rotation](./DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md).

## Security incident command system

### Severity and response targets

| Severity | Definition | Examples | Acknowledge | Incident commander | Executive/customer update |
|---|---|---|---:|---:|---:|
| **SEV-1 Critical** | Confirmed or strongly suspected compromise with material confidentiality, integrity, availability, safety, or cross-tenant impact. | Signing/encryption key exposure, active account takeover, destructive production access, cross-workspace data disclosure. | 15 minutes | 15 minutes | Initial internal update in 30 minutes; customer/legal cadence set within 60 minutes. |
| **SEV-2 High** | Significant production/security impact that is contained, limited in scope, or has no confirmed data access. | Exploited vulnerability on an isolated service, credential misuse blocked by controls, prolonged monitoring blind spot. | 30 minutes | 30 minutes | Stakeholder update in 60 minutes; customer decision within 4 hours. |
| **SEV-3 Medium** | Degraded control or localized incident with a safe workaround and no evidence of compromise. | Failed credential rotation, delayed webhook delivery, scanner finding requiring expedited remediation. | 4 business hours | Same business day | Status in normal support/change channels. |
| **SEV-4 Low** | Minor defect, policy exception, or informational event with negligible current risk. | Non-sensitive misconfiguration, unsuccessful attack without control degradation. | 2 business days | As assigned | No customer notice unless scope changes. |

The first qualified responder is incident commander until handoff is explicitly recorded. The commander assigns operations, security/forensics, communications, legal/privacy, and scribe roles; opens a restricted incident channel and case; records UTC timestamps; and establishes the next update time. Page the security on-call for every SEV-1/SEV-2. Escalate to the executive owner and legal/privacy lead for suspected personal-data, regulated-data, customer-secret, or law-enforcement impact. Escalate to affected infrastructure and identity providers through their security channels, not ordinary support, when their control plane may be involved.

### Triage, containment, evidence preservation, and eradication

1. **Declare and scope:** record detection source, reporter, affected tenants/regions/releases/identities, earliest known indicator, and confidence. Do not wait for perfect attribution before declaring.
2. **Preserve evidence before destructive action:** snapshot relevant volumes and databases where lawful, export immutable cloud/identity/audit logs, retain suspicious objects, capture process/network metadata, and calculate SHA-256 hashes. Record collector, source, UTC time, command/tool version, hash, storage location, and every custody transfer. Never place raw secrets, tokens, or unnecessary customer payloads in tickets or chat.
3. **Contain:** revoke sessions and scoped credentials; disable compromised integrations; quarantine hosts/workloads; block indicators; freeze risky deployment paths; and reduce privileges. Prefer reversible isolation over deletion until evidence is preserved. Do not rotate an encryption key before confirming historical versions remain available for decryption.
4. **Eradicate:** remove persistence, patch the root cause, rebuild from trusted artifacts, rotate every credential reachable from the compromised principal, and validate IAM/network/pipeline policy. Treat logs from a compromised control plane as untrusted until corroborated.
5. **Recover:** restore in stages, canary critical workflows, verify tenant isolation and audit-chain integrity, monitor enhanced detections, and obtain incident-commander/security approval before full traffic restoration.
6. **Close:** document root cause, impact, timeline, containment/recovery decisions, notification rationale, control failures, follow-up owners/dates, and evidence-retention/legal-hold requirements. Complete a blameless review within five business days for SEV-1/SEV-2.

### Customer and regulatory notification

Legal/privacy owns the notification determination and applicable deadlines. Security supplies verified facts; support/customer success supplies the affected-customer list; communications prevents speculation. Record the decision even when notification is not required. Notices must identify what happened, concrete dates/times, affected data/services, containment completed, customer actions, ongoing risk, and a monitored contact. Send material updates on the cadence declared by the incident commander and a final report when facts stabilize. Never claim “no access” solely because access logs are absent.

### Credential compromise procedure

1. Identify the credential type, provider/key ID, version, scope, owner, last-known-good use, dependent services, and every environment/region where it is replicated.
2. Preserve access logs and provider version metadata, then revoke or disable the exposed version immediately. For JWT signing compromise, revoke active sessions/token families and promote a new signing version. For encryption compromise, retain the old version under restricted decrypt-only access until data is re-encrypted; do not destroy it prematurely. For API keys, webhook secrets, SCIM tokens, and provider credentials, disable the old version and issue a new version through the credential-rotation workflow.
3. Rotate upstream and downstream credentials reachable by the compromised principal, including CI/CD, recovery-region, DNS, cloud, and break-glass credentials. Validate that caches and long-running workers loaded the new version.
4. Query `/workspace/security/credential-rotation/history` and provider audit logs to reconcile created, active, grace, revoked, retired, and destroyed versions. Investigate any untracked version or failed rotation event as at least SEV-2.
5. Test authentication, decryption of historical ciphertext, evidence verification, webhook signatures, SCIM provisioning, and provider integrations. Monitor rejected old-version use as a compromise indicator.
