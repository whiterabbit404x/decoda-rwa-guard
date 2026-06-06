# Evidence Exports — Tamper-Evident Bundle Format

## Overview

Every `proof_bundle` and `incident_report` export produced by Decoda RWA Guard includes a cryptographic evidence manifest and HMAC seal. This makes every export tamper-evident: a regulator, insurer, or legal reviewer can independently verify that files were not altered after export without requiring access to the Decoda backend.

---

## Bundle Structure

A signed export bundle (JSON format) contains the following keys in `rows[0]`:

| File | Description |
|------|-------------|
| `summary.json` | Export metadata, evidence source type, status |
| `alerts.json` | Linked alert records |
| `incidents.json` | Incident record |
| `detections.json` | Detection records |
| `response_actions.json` | Response action records |
| `audit_log.json` | Audit log entries for the incident |
| `detection_metrics.json` | Raw detection metric / telemetry evidence |
| `evidence.json` | Structured evidence payloads |
| **`manifest.json`** | **SHA-256 hash of every file + manifest integrity hash** |
| **`seal.json`** | **HMAC-SHA256 signature over the canonical manifest** |

---

## Manifest Schema

```json
{
  "manifest_version": "1.0",
  "export_id": "<uuid>",
  "export_type": "proof_bundle",
  "workspace_id": "<uuid>",
  "generated_at": "2026-01-01T00:00:00+00:00",
  "generated_by_user_id": "<uuid or null>",
  "source_resource_type": "incident",
  "source_resource_id": "<incident-uuid>",
  "storage_backend": "s3",
  "app_version": "<git-sha if available>",
  "previous_audit_anchor_hash": "<sha256 of last audit row at export time>",
  "files": [
    {
      "path": "alerts.json",
      "sha256": "<hex>",
      "size_bytes": 1234
    }
  ],
  "manifest_sha256": "<sha256 of manifest body without this field>"
}
```

### manifest_sha256 computation

The `manifest_sha256` is computed as:

```
SHA-256( canonical_json(manifest_without_manifest_sha256) )
```

where `canonical_json` means:
- Keys sorted alphabetically (recursive)
- No spaces (compact separators `,` and `:`)
- UTF-8 encoding
- No BOM

---

## Seal Schema

```json
{
  "signature_algorithm": "HMAC-SHA256",
  "key_id": "env-default",
  "signed_manifest_sha256": "<hex>",
  "signature": "<64-char hex HMAC-SHA256 digest>",
  "signed_at": "2026-01-01T00:00:00+00:00"
}
```

The `signature` is computed as:

```
HMAC-SHA256( key=EXPORT_SIGNING_SECRET, message=canonical_json(manifest) )
```

where `manifest` is the full manifest dict **including** `manifest_sha256`.

The raw secret is never included in the bundle or logs.

---

## Verification Process

To verify a bundle:

1. **File integrity** — for each entry in `manifest.files`:
   - Serialize `file_value` with `canonical_json`
   - Compute `SHA-256`
   - Compare to `entry.sha256`

2. **Manifest integrity** — remove `manifest_sha256` from the manifest, serialize with `canonical_json`, compute `SHA-256`, compare to `manifest.manifest_sha256`

3. **HMAC signature** — serialize the full manifest (including `manifest_sha256`) with `canonical_json`, compute `HMAC-SHA256` with the signing secret, compare to `seal.signature` using `hmac.compare_digest`

The helper function `verify_bundle()` in `services/api/app/evidence_signing.py` implements all three steps.

---

## Key Rotation

1. Set a new `EXPORT_SIGNING_SECRET` value
2. Set `EXPORT_SIGNING_KEY_ID` to a new identifier (e.g., `v2-2026-06`)
3. Old bundles remain verifiable with the old secret; new bundles use the new secret
4. The `key_id` field in `seal.json` identifies which key was used to sign

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `EXPORT_SIGNING_SECRET` | **production/staging** | HMAC signing secret. Minimum 32 bytes. |
| `EVIDENCE_SIGNING_SECRET` | Alternative | Accepted if `EXPORT_SIGNING_SECRET` is not set. |
| `EXPORT_SIGNING_KEY_ID` | Optional | Key rotation label. Default: `env-default`. |
| `EXPORT_STORAGE_BACKEND` | **production** | Must be `s3` in production. |
| `EXPORT_S3_BUCKET` | When `s3` | S3 bucket for export storage. |
| `EXPORT_S3_REGION` | When `s3` | S3 region. Default: `us-east-1`. |
| `EXPORT_S3_PREFIX` | When `s3` | Key prefix. Default: `decoda-exports`. |
| `EXPORT_S3_OBJECT_LOCK_ENABLED` | Optional | `true`/`false` override for Object Lock status. |
| `EXPORT_ALLOW_LOCAL_IN_PRODUCTION` | Emergency | `true` to allow local storage in prod (unsafe). |

---

## Production Behavior

- If `EXPORT_SIGNING_SECRET` / `EVIDENCE_SIGNING_SECRET` is absent in production/staging, export creation **fails closed with HTTP 503**.
- If `EXPORT_STORAGE_BACKEND=local` in production without `EXPORT_ALLOW_LOCAL_IN_PRODUCTION=true`, storage initialization **raises RuntimeError**.
- Local/dev mode uses a deterministic non-production test secret. `seal.json` includes a `warning` field labelling it as `DEV_MODE_TEST_SECRET`.

---

## S3 Object Lock / WORM Storage

When `EXPORT_STORAGE_BACKEND=s3`:
- Set `EXPORT_S3_OBJECT_LOCK_ENABLED=true` to declare Object Lock is enabled.
- The API response for export creation includes `object_lock_enabled` when known.
- Object Lock (COMPLIANCE mode recommended) prevents any user including bucket owners from deleting or modifying objects during the retention period.

---

## Audit Log Hash Chaining

Each row in `audit_logs` is protected by a hash chain:

```
row_hash = SHA-256( canonical_json({
  id, workspace_id, user_id, action, entity_type,
  entity_id, created_at, metadata_sha256, previous_row_hash
}) )
```

The `previous_row_hash` of each row links back to the preceding row in the workspace-scoped chain. Modifying, deleting, or inserting rows breaks the chain.

The `verify_audit_chain()` function in `evidence_signing.py` verifies the chain for a list of rows ordered by `created_at ASC`.

The latest audit chain head hash (`audit_chain_head_hash`) is embedded in export manifests as `previous_audit_anchor_hash`, linking the export to the state of the audit log at time of export.

### Columns added by migration `0091_audit_log_hash_chain.sql`

| Column | Type | Description |
|--------|------|-------------|
| `row_hash` | TEXT | SHA-256 chain hash for this row |
| `previous_row_hash` | TEXT | Hash of the preceding row (NULL for genesis) |
| `hash_algorithm` | TEXT | `sha256` constant |
| `sealed_at` | TIMESTAMPTZ | Timestamp when hash was computed (= created_at) |

---

## Known Limitations

1. **HMAC, not asymmetric signatures**: The current scheme uses HMAC-SHA256 (symmetric key). A verifier needs access to the shared secret. For fully non-repudiable signatures suitable for court evidence, Ed25519 asymmetric signing (with public key publication) would be preferred. This is deferred.
2. **Audit log pre-migration rows**: Rows inserted before migration `0091` have `row_hash = NULL` and are not included in chain verification. They are not flagged as tampered.
3. **In-transit integrity**: The bundle is a JSON file. The HMAC protects against post-export tampering but not against interception. Use TLS for transport.
4. **Key management**: Signing keys are managed via environment variables. A dedicated KMS (AWS KMS, Vault) integration would further reduce key exposure risk.
