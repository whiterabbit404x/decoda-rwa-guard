# Disaster Recovery, Data Governance, and Key Rotation Runbook

## Service objectives

These are production targets measured from the first confirmed customer impact. They are not platform defaults.

| Capability | RPO | RTO | Recovery source / degraded behavior |
|---|---:|---:|---|
| PostgreSQL system of record | 5 minutes | 60 minutes | PITR plus cross-region replica/snapshot; API becomes read-only or unavailable rather than writing to fallback storage. |
| Evidence exports and manifests | 15 minutes | 4 hours | Versioned S3 bucket with Object Lock and cross-region replication. Existing evidence remains downloadable when the API is degraded. |
| Redis rate limiting/session revocation cache | 0 minutes for authoritative data | 15 minutes | PostgreSQL remains authoritative. Use bounded in-process rate limiting only under an approved incident flag; session revocation continues against PostgreSQL. |
| Redis pub/sub live alert stream | No durability promise | 15 minutes | Clients reconnect and reload authoritative alerts from PostgreSQL. Do not represent missed pub/sub messages as persisted events. |
| Monitoring checkpoints and worker heartbeats | 5 minutes | 30 minutes | Restore PostgreSQL checkpoints, pause duplicate workers, then resume with idempotency keys. |
| Webhook and notification queues | 5 minutes | 60 minutes | PostgreSQL queue rows are authoritative; reclaim `delivering` rows, preserve attempt counters, and replay with idempotency/event IDs. |
| Authentication and managed keys | 0 minutes | 60 minutes | Secret-manager multi-region replication and version aliases. Fail closed if active key material cannot be loaded. |

Alert when 50% of an RTO is consumed. Declare a recovery-objective breach at the target and create a customer-visible incident update.

## PostgreSQL recovery

1. Declare the incident, freeze migrations, suspend retention/deletion execution, and record the source region's last healthy transaction timestamp.
2. Select a PITR point no older than the five-minute RPO. Restore into a **new isolated database**, never over the damaged primary.
3. Set `RESTORE_VALIDATION_ISOLATED=true`, configure the restored database and export-storage replica, and run:
   ```bash
   RESTORE_VALIDATION_ISOLATED=true RESTORE_DATABASE_URL=postgresql://... \
     python services/api/scripts/validate_backup_restore.py \
     --source-region us-east-1 --recovery-region us-west-2 --backup-id <provider-id>
   ```
4. Require all of: migrations present, workspace counts plausible, audit chains valid, evidence manifests valid, and historical signing-key versions retrievable.
5. Fence the old primary. Update the database secret/endpoint, start one monitoring worker, verify checkpoint progress, and then scale workers.
6. Resume writes only after a canary workspace completes authentication, ingestion, detection, incident, export, and webhook delivery checks.
7. Record the run in `recovery_validation_runs`, attach provider timestamps, and calculate measured RPO/RTO.

## Redis-dependent workload recovery

Redis is never the source of record for detections, incidents, audit entries, exports, webhooks, or session revocation.

* **Rate limits:** temporary in-memory fallback requires an incident flag, one instance or intentionally conservative limits, and an on-call acknowledgement. Remove the flag after Redis is healthy.
* **Session revocation:** PostgreSQL `revoked_at` remains authoritative. A Redis miss must not restore a revoked session.
* **SSE/pub-sub:** reconnect subscribers and force an HTTP refresh from PostgreSQL. Do not replay synthetic alerts.
* **Locks/idempotency:** verify no live worker owns the old lock, then restart one worker. Database uniqueness/idempotency constraints must reject duplicates.

Check Redis connectivity, latency, evictions, memory pressure, replication lag, rate-limit fallback counters, SSE reconnect rate, and worker duplicate-key errors before closing the incident.

## Monitoring checkpoints

1. Stop all but one monitoring worker in both regions.
2. Compare `monitoring_checkpoints`, `monitoring_runs`, `monitoring_event_receipts`, and `telemetry_events` timestamps with provider block heights/timestamps.
3. Resume from the last committed checkpoint. Never advance a checkpoint before its telemetry and detection transaction commits.
4. Confirm idempotency conflicts are harmless, ingestion freshness is within SLO, and no simulator/replay evidence appears in live workspace data.
5. Scale gradually and watch database locks, provider errors, queue depth, detection throughput, and heartbeat freshness.

## Evidence exports

* Replicate the export bucket cross-region with versioning and Object Lock enabled.
* Every proof bundle and incident report stores its signing key ID/version. Verification loads that exact historical version; never rewrite old seals during rotation.
* During recovery, validate the file hashes, canonical manifest hash, signature, and previous audit anchor. A missing historical key version is an integrity failure.
* Retention deletion first checks legal holds, then removes the object, tombstones the export row, and writes `data_deletion_events`. Object Lock may intentionally delay physical deletion; record the provider retention date rather than claiming deletion completed.

## Webhook and notification queues

1. Pause dispatchers while the database role is changing.
2. After recovery, move stale `delivering` attempts back to `queued` only when their lease/timeout has expired.
3. Preserve `event_id`, destination, attempt count, next-attempt time, and response metadata. Never create a new logical event to retry an old delivery.
4. Process oldest due items first with bounded concurrency. Honor `Retry-After`, retry schedules, and terminal/dead-letter states.
5. Compare queue depth, oldest age, success rate, duplicate responses, and dead letters against pre-incident baselines.

## Workspace retention, deletion, and legal holds

Workspace administrators configure retention through `/workspace/retention-policies`. Supported classes are telemetry, detections, alerts, incidents, audit logs, exports, and user data. Policies persist in PostgreSQL and are not inferred from frontend state.

Retention is provisioned, not opted into. Every Pilot workspace receives the default policy on creation (`pilot_retention.ensure_workspace_retention_policies`), migration 0154 backfills existing Pilot workspaces, and the worker heals any that are still missing one. A policy seeded onto a workspace that already held records carries `effective_from`, so the sweep cannot act on it until that date — seeding a policy is never itself a bulk deletion. `GET /workspace/retention-policies` reports `enforced` separately from `enabled` for exactly this reason; a period that has not started is not a period in force. Scale and Enterprise workspaces are deliberately not seeded: their retention is a contract term.

The default Pilot periods and the one place they are defined are in `services/api/app/pilot_retention.py`:

| Class | Retained | Mode |
|---|---:|---|
| telemetry | 90 days | hard delete |
| detections | 180 days | hard delete |
| alerts (carries findings and their on-chain evidence rows) | 180 days | hard delete |
| incidents | 365 days | hard delete |
| exports (evidence packages + object) | 365 days | hard delete |
| audit_logs | 365 days | anonymize |
| user_data | 30 days | anonymize |

### End-of-Pilot lifecycle

A Pilot is open-ended by default: no deadline, no grace window, no deletion schedule. The clock starts only when the end is RECORDED — a founder calling `POST /admin/customers/{id}/status` with `expired`, or the worker observing that a dated evaluation passed its own `evaluation_expires_at` (recorded at that deadline, not at the moment it was noticed).

Recording the end writes `organizations.pilot_ended_at` / `pilot_grace_ends_at` and queues two deletion requests per workspace, both `request_type = 'pilot_end_purge'`, both waiting on `next_attempt_at`:

1. at `pilot_ended_at + 30 days` — telemetry, detections, alerts, incidents and exports hard-deleted; audit logs anonymized in the same operation.
2. at `pilot_ended_at + 365 days` — the remaining anonymized audit skeleton hard-deleted.

Reactivating, extending, setting a future deadline, suspending, or upgrading to Scale/Enterprise all route through `organizations.reconcile_pilot_retention`, which clears the recorded end and cancels every queued purge still in `approved`. A request already leased or completed is never rewritten.

An organization that is `expired` with NO `evaluation_expires_at` has no recorded end date. Nothing invents one: no grace window starts and no deletion is ever queued for it. The founder console reports "Pilot end date not recorded"; ending the Pilot explicitly is what starts the clock.

### What the engine does NOT sweep

The seven data classes cover the operational security record. They do NOT cover workspace configuration, and no customer-facing surface may imply otherwise: the asset registry, targets and monitoring configuration, integrations and their stored credentials, API keys, webhooks, notification destinations, membership and invitations, governance policies, onboarding sessions, billing records, and workspace settings all survive every schedule on this page. `/privacy` states this explicitly under "What the schedule does not cover". There is no self-serve workspace/tenant deletion today: `DELETE /auth/delete-account` anonymizes ONE USER and revokes their sessions, it does not delete a workspace. Removing workspace configuration is an operator action on request — a gap worth closing, and one that must not be described to customers as if it were automatic.

**The retention worker is what performs all of this.** If `retention-worker` is not deployed, the periods published on `/privacy` and shown in Settings → Security are not applied and a queued end-of-Pilot deletion never executes. `/ops` readiness reports worker freshness; a stale worker is reported as stale, never assumed healthy.

Deletion is two-step and auditable:

1. Create `/workspace/deletion-requests` with classes, cutoff, subject (for user data), a reason, and `confirm: "DELETE"`. The typed confirmation is required for every request type and is checked before any connection is opened, so a mis-click, a replayed body, or an integration calling the endpoint by accident cannot destroy a workspace's records.
2. A fresh legal-hold query occurs both at request time and immediately before approval/execution.
3. A reauthenticated administrator calls the approve-and-execute endpoint. Each class writes a `data_deletion_events` record with counts and chain anchors, and the response carries `deletion_report_sha256` — the deletion receipt. The report holds ids, counts, timestamps and hashes only; it contains none of the deleted content, so it can be handed to a customer as proof.
4. User data is anonymized and sessions are revoked. Evidence/audit retention is independently controlled; legal holds take precedence.
5. Release of a legal hold requires reauthentication and a release reason. Releasing a hold does not automatically execute a previously blocked CUSTOMER request; create or explicitly re-review it. A blocked end-of-Pilot purge (`request_type = 'pilot_end_purge'`) IS resumed, because it is a scheduled policy rather than a human decision — a hold must defer a scheduled deletion, not cancel it permanently. The engine re-checks holds at execution, so a request still covered by another hold simply parks again.

Review deletion events weekly and reconcile exported-object tombstones with provider inventory. Never report physical deletion while provider Object Lock or replication still retains a version. Every `storage_delete` event now records the backend's object-lock state in `details.storage`, so a receipt written against a COMPLIANCE-mode bucket says that a locked version may persist rather than implying the bytes are gone.

### Backups — DEPLOYMENT-DEPENDENT

Nothing in this repository can prove backup retention, and no customer-facing surface may state a backup deletion timeline. Deletion removes data from the ACTIVE database and from the configured export object store only. Residual copies live in:

* the PostgreSQL provider's PITR window and snapshot retention (Neon or equivalent) — see "Service objectives" above for the RPO the window is sized to; and
* the export bucket's versioning, Object Lock and lifecycle configuration (`EXPORT_S3_BUCKET`).

Confirm both against the settings actually in use before publishing any statement about backup timing. The published wording is deliberately limited to what is verifiable: data is removed from active systems on the stated schedule, and residual encrypted copies may remain until the provider's normal backup-retention cycle completes.

## Managed keys and rotation

Production/staging should set `MANAGED_KEY_PROVIDER=aws_secrets_manager`. To prevent a deployment outage, the default `MANAGED_KEY_ENFORCEMENT=compatibility` temporarily accepts pre-existing `AUTH_TOKEN_SECRET`, `SECRET_ENCRYPTION_KEY`, and `EXPORT_SIGNING_SECRET` values while emitting startup warnings and a failing, non-blocking `managed_key_provider` readiness check. Configure:

* `AUTH_TOKEN_KEY_SECRET_ID` and optional `AUTH_TOKEN_KEY_VERSION`
* `SECRET_ENCRYPTION_KEY_SECRET_ID`, `SECRET_ENCRYPTION_KEY_ENCODING=base64`, and optional version
* `EVIDENCE_SIGNING_KEY_SECRET_ID` and optional version

Safe rollout procedure:

1. Deploy with `MANAGED_KEY_ENFORCEMENT=compatibility`; existing environment keys continue to work.
2. Provision and test all three managed secret IDs in every active and recovery region.
3. Set `MANAGED_KEY_PROVIDER=aws_secrets_manager` while enforcement remains `compatibility`, deploy, and verify authentication, existing secret decryption, new export signing, and historical evidence verification.
4. Set `MANAGED_KEY_ENFORCEMENT=strict` only after the managed-provider deployment is healthy. Strict mode then fails startup if configuration regresses to environment-backed keys.
5. Do not rotate an environment-backed encryption or signing key during compatibility mode because `env-current` cannot identify old provider material. Move to versioned managed secrets first.

Rotation procedure:

1. Create a new provider version without changing `AWSCURRENT`; test retrieval from every active/recovery region.
2. Register the version in `managed_key_versions` as staged/metadata, then promote it to `AWSCURRENT` during a change window.
3. Restart/cycle API workers to clear key caches. Authentication-key rotation invalidates outstanding CSRF/session-token hashes by design; notify users and revoke sessions in a controlled window.
4. New encrypted secrets and evidence use the new version. Existing encrypted values and seals carry their original version and resolve it directly.
5. Keep old encryption/signing versions in `verify_only` until all ciphertext is re-encrypted and all evidence retention/legal-hold periods expire. Never destroy a signing version while an evidence object or legal hold references it.
6. Test historical evidence verification before and after promotion, then record activation, rollback, and retirement timestamps in `managed_key_versions`.

Rollback changes the provider stage/alias to the prior version and cycles workers. Do not edit historical evidence seals.

## Regional/provider outage exercise

Run quarterly and after material architecture changes:

1. Announce a game day and define abort thresholds. Capture healthy baseline metrics.
2. Simulate loss of the primary region/provider control plane, not merely an application restart.
3. Restore/promote PostgreSQL and export storage in the recovery region; use replicated secret-manager versions and independent DNS/control-plane credentials.
4. Execute isolated restore validation, then canary the complete live workflow.
5. Shift traffic gradually. Verify outbound webhook source/network policy, email provider, RPC providers, and monitoring worker fencing.
6. Measure actual RPO/RTO, document data gaps and duplicates, and enter the run in `recovery_validation_runs` as `regional_failover` or `provider_failover`.
7. Test failback as a separate change after replication is healthy. Never fail back automatically into a possibly stale primary.

Required evidence: incident timeline, provider backup IDs, DNS/traffic timestamps, validation JSON, audit/evidence integrity results, queue-depth graphs, checkpoint comparison, customer communication, measured RPO/RTO, and assigned remediation owners.

## Security incident recovery and evidence preservation

Security incidents use the severity model and command roles in `docs/OPERATIONS_RUNBOOK.md`. During disaster recovery, availability work must not overwrite forensic evidence or silently weaken tenant isolation. Before restore/failover, preserve database/cloud/identity/application audit logs, affected images and volumes, deployment manifests, SBOMs, artifact signatures, provider key-version metadata, queue/checkpoint state, and relevant network telemetry. Hash exports with SHA-256, write them to access-controlled immutable storage, document chain of custody, and place evidence under legal hold when instructed by legal/privacy.

Recovery authorization requires the incident commander, recovery lead, and security lead to record: chosen restore point; expected/observed data loss; compromised identities and revoked credential versions; validation owner; rollback threshold; and customer-notification status. Restore only signed artifacts whose provenance and SBOM match the approved release. Recreate infrastructure from reviewed definitions rather than repairing a potentially compromised host in place.

### Credential-compromise recovery matrix

| Material | Immediate containment | Recovery validation | Retirement/destruction condition |
|---|---|---|---|
| JWT signing material | Promote a new managed-provider version; revoke exposed version and affected sessions; block old-version verification when compromise is confirmed. | New tokens carry the new key version; revoked sessions fail; all regions resolve the intended active version. | Retire after maximum token lifetime and investigation hold; destroy only with security/legal approval. |
| Encryption keys | Deny new encryption with the exposed version; restrict it to decrypt-only; promote a new version. | Re-encrypt inventory in bounded batches; prove old and new ciphertext decrypt; reconcile failures and backups. | Destroy only after live data, backups, replicas, exports, and legal holds no longer require it. |
| API keys / SCIM tokens | Revoke the exact version and associated sessions/jobs; create a replacement with least privilege. | Claim replacement once through the authenticated workflow; verify old secret rejection and new use. | Destroy pending-secret ciphertext after claim; retain fingerprint/status history. |
| Webhook secrets | Disable delivery if needed; rotate secret and coordinate receiver cutover. | Verify signed test delivery, old-secret rejection after grace, retry/dead-letter health. | Revoke at grace expiry; retain only fingerprint and audit metadata. |
| OIDC/Slack/provider credentials | Disable the integration and revoke at the provider; rotate any reachable credentials. | Reauthorize through the provider; test minimum scopes, issuer/team identity, and audit events. | Remove encrypted old material after provider confirms revocation and evidence is retained. |

Automated policies are persisted in `credential_rotation_policies`; version state is recorded in `credential_versions`; immutable operational events are recorded in `credential_rotation_events`. Run `python -m services.api.app.run_credential_rotation_worker` from a singleton scheduled job. Failed events are not silently retried: page on repeated failures, correct the cause, and rerun under the same incident/change record. Provider-issued credentials that cannot be safely synthesized are automatically disabled at expiry and require self-serve reauthorization with a replacement secret.

### Recovery completion checklist

* Reconcile PostgreSQL, object storage, queues, monitoring checkpoints, and audit-chain anchors against the chosen recovery point.
* Confirm all exposed credential versions are revoked, all active versions are present in every region, and historical decrypt/verification versions remain available only where required.
* Validate workspace isolation using at least two tenant test fixtures and verify no demo/fallback data appears in live workspaces.
* Run application health, authentication, monitoring/detection, response-action, export signing/verification, notification, webhook, SCIM, and billing canaries.
* Restore alerting and enhanced monitoring before normal traffic. Observe for attempted use of revoked credentials and replayed queue events.
* Record measured RPO/RTO, missing/duplicated records, validation evidence, customer/regulatory notices, residual risk acceptance, and follow-up owners.
