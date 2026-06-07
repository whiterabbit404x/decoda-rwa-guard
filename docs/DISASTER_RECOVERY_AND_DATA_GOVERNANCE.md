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

Workspace administrators configure retention through `/workspace/retention-policies`. Supported classes are telemetry, detections, incidents, audit logs, exports, and user data. Policies persist in PostgreSQL and are not inferred from frontend state.

Deletion is two-step and auditable:

1. Create `/workspace/deletion-requests` with classes, cutoff, subject (for user data), and reason.
2. A fresh legal-hold query occurs both at request time and immediately before approval/execution.
3. A reauthenticated administrator calls the approve-and-execute endpoint. Each class writes a `data_deletion_events` record with counts and chain anchors.
4. User data is anonymized and sessions are revoked. Evidence/audit retention is independently controlled; legal holds take precedence.
5. Release of a legal hold requires reauthentication and a release reason. Releasing a hold does not automatically execute previously blocked requests; create or explicitly re-review a request.

Review deletion events weekly and reconcile exported-object tombstones with provider inventory. Never report physical deletion while provider Object Lock or replication still retains a version.

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
