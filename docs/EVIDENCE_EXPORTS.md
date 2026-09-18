# Evidence Exports — Tamper-Evident Bundle Format

## Overview

Every `proof_bundle` and `incident_report` export produced by Decoda RWA Guard includes a cryptographic evidence manifest and a detached seal. This makes every export **tamper-evident**.

Two guarantees are provided, and they are deliberately never merged:

| Guarantee | What it proves | What you need to check it |
|---|---|---|
| **Integrity** | The packaged bytes have not changed since the package was created. | The package alone. No key of any kind. |
| **Authenticity** | The manifest was signed by the holder of Decoda's evidence signing key. | Decoda's **public** verification key. |

Packages sealed from this release forward carry an **Ed25519 public-key signature**, so a customer, auditor or security team can verify **authenticity offline** — with no Decoda login, no Decoda API call, no database access, no Decoda secret, and no reliance on the Decoda dashboard's "Verified" badge.

Packages exported *before* this release carry the legacy HMAC seal only. Their integrity remains fully verifiable; their authenticity is **not** independently verifiable, and no Decoda surface claims otherwise. See [Legacy HMAC packages](#legacy-hmac-packages).

```bash
python -m decoda_evidence_verifier verify evidence-package.zip \
    --keyring decoda-evidence-keys.json
```

Full verifier documentation: [`tools/decoda_evidence_verifier/README.md`](../tools/decoda_evidence_verifier/README.md).

---

## Package structure (downloaded `.zip`)

```text
EV-2026-017/
  manifest.json                        the sealed manifest
  manifest.sig                         the detached seal document
  seal.json                            byte-identical copy of manifest.sig
  VERIFY.md                            offline verification instructions
  verification.json                    the backend verification result
  artifacts/
    on-chain/… operational/… policy/…  the ORIGINAL evidence artifacts —
    human-actions/… package/…          the exact canonical-JSON bytes the
                                       manifest hashes
  reports/investigation.md             human-readable reading of the evidence
  verification/
    README.txt                         how to re-verify offline
    signing-key.json                   PUBLIC signer identifiers
    decoda-evidence-keys.json          PUBLIC Ed25519 keyring (convenience copy)
```

Nothing private is ever written into a package: no signing secret, no private key, no API, database or storage credential. `assert_no_secret_material` scans every emitted byte — including for the live signing secret **by value** — and the build fails closed on a match.

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

## Merkle Root — `decoda-merkle-v1` (manifest schema 2.0+)

An artifact-set commitment independent of manifest field ordering and key naming: one value that changes if any artifact changes, is added, removed or reordered.

```text
leaf  = SHA256(0x00 || utf8(path) || 0x1F || ascii(lower(sha256_hex)))
node  = SHA256(0x01 || left_digest || right_digest)
order = leaves sorted ascending by the UTF-8 BYTES of "path"
odd   = an unpaired last node is PROMOTED unchanged (never duplicated)
empty = no root at all (never a placeholder digest)
```

`0x00` / `0x01` domain separation stops a 64-byte node preimage being reinterpreted as a leaf (second-preimage attack). Promotion rather than duplication keeps the artifact-set → root map injective (CVE-2012-2459). `path` is the LOGICAL manifest path, never a storage key, so the root does not depend on where bytes live.

Implemented independently in `services/api/app/evidence_merkle.py` (backend) and `tools/decoda_evidence_verifier/verifier.py` (offline verifier). Neither calls the other; shared test vectors prove they agree.

---

## Seal Schema

The seal is written to the package as `seal.json`, and to the downloadable ZIP as both `seal.json` and `manifest.sig` (byte-identical).

### Schema 2 — current (public-key verifiable)

```json
{
  "signature_algorithm": "HMAC-SHA256",
  "key_id": "env-default",
  "key_version": "env-current",
  "key_provider": "env",
  "signed_manifest_sha256": "<hex>",
  "signature": "<64-char hex HMAC-SHA256 digest>",
  "signed_at": "2026-01-01T00:00:00+00:00",

  "schema_version": 2,
  "signatures": [
    {
      "signature_format": "decoda-evidence-signature-v1",
      "signature_format_version": 1,
      "algorithm": "Ed25519",
      "key_id": "decoda-evidence-2026-01",
      "signed_object": "manifest_sha256",
      "signing_domain": "DECODA-EVIDENCE-MANIFEST-V1",
      "signed_manifest_sha256": "<hex>",
      "signature": "<base64 Ed25519 signature>",
      "public_key_verifiable": true
    }
  ]
}
```

The HMAC fields keep their exact original meaning and position. `schema_version` and `signatures` are **purely additive**, so every existing reader and every already-exported package is unaffected. A seal with no `schema_version`/`signatures` is schema 1 (HMAC only) and its meaning is unchanged.

### The two seals prove different things

| Seal | Key | Who can verify | Who can forge |
|---|---|---|---|
| `HMAC-SHA256` | shared secret | only a holder of the secret (Decoda) | **anyone holding the secret** |
| `Ed25519` | asymmetric | anyone, with the **public** key | only the holder of the private key |

This is exactly why HMAC cannot establish authenticity to a third party: handing a customer the verification key hands them the forging key. Ed25519 separates the two roles.

### Exact signed bytes

```python
payload = b"DECODA-EVIDENCE-MANIFEST-V1\x00" + bytes.fromhex(manifest_sha256)
```

* 27-byte ASCII domain tag, one `0x00` separator, the raw 32-byte manifest digest.
* Exactly 60 bytes, fixed length. **No JSON is signed**, so there is no serializer for a verifier to reproduce and no delimiter to confuse.
* The domain tag means a Decoda evidence signature can never be replayed as a signature over a different Decoda object.
* `manifest_sha256` covers the canonical manifest body, which carries every artifact digest, the Merkle root, the policy snapshot and the audit anchor — so those 60 bytes transitively commit to the whole package.

A verifier **must recompute** `manifest_sha256` from the manifest bytes it holds rather than trusting the field. Decoda's backend and the standalone verifier both do.

Implementation: `services/api/app/evidence_ed25519.py`. Ed25519 itself comes from the `cryptography` package (already a pinned dependency). Nothing here implements the algorithm.

---

## Verification Process

### Integrity (needs no key)

1. **File integrity** — for each entry in `manifest.files`: hash the stored canonical-JSON bytes with SHA-256 and compare to `entry.sha256`; compare the byte length to `entry.size_bytes`.
2. **Manifest integrity** — remove `manifest_sha256`, serialize with `canonical_json`, SHA-256, compare to `manifest.manifest_sha256`.
3. **Merkle root** — recompute from `(path, sha256)` pairs and compare to `manifest.merkle_root`.

### Authenticity (needs the public key)

4. **Ed25519 signature** — recompute `manifest_sha256`, build the 60-byte payload, verify `signatures[0].signature` against the public key named by `signatures[0].key_id`.

Backend: `verify_evidence_package_document()` in `services/api/app/evidence_verification.py`, which reports `integrity` and `authenticity` as separate axes. `EvidenceManifestSigner.verify()` returns an `authenticity` block whose `independently_verifiable` flag is true **only** for a verified Ed25519 signature.

Offline: `tools/decoda_evidence_verifier`.

---

## Public verification keys

### Keyring format

```json
{
  "schema_version": 1,
  "issuer": "Decoda RWA Guard",
  "keys": [
    {
      "key_id": "decoda-evidence-2026-01",
      "algorithm": "Ed25519",
      "public_key": "<base64 of the raw 32-byte public key>",
      "status": "active"
    }
  ]
}
```

`public_key` may also be a PEM SubjectPublicKeyInfo block. `status` is `active` or `retired`.

### Distribution

| Source | Use |
|---|---|
| `GET /.well-known/decoda-evidence-keys.json` | Canonical published keyring. Unauthenticated — a key only customers can fetch is not a public key. |
| `verification/decoda-evidence-keys.json` inside the ZIP | **Convenience copy only.** |

**Offline verification never requires the endpoint.** Pin the keyring once, verify forever with no network.

A public key carried inside the package it verifies establishes **no trust by itself** — whoever could alter the package could alter the bundled key with it. The verifier therefore requires `--allow-bundled-keyring` to use it, and always prints which key source was exercised.

### Key rotation

1. Provision a new Ed25519 seed under the `EVIDENCE_SIGNING_ED25519` managed-key purpose and set `EVIDENCE_SIGNING_ED25519_KEY_ID` to the new identifier.
2. Publish the old PUBLIC key with `status: "retired"` via `EVIDENCE_SIGNING_ED25519_PUBLIC_KEYS` (`key_id:base64_public_key:retired`, comma-separated).
3. New packages sign with the new key; old packages keep verifying against the retired public key.

**A retired PUBLIC key is never deleted.** Retiring the private half does not retire the evidence it signed, and historical evidence must remain verifiable indefinitely.

Legacy HMAC rotation is unchanged: set a new `EXPORT_SIGNING_SECRET` and a new `EXPORT_SIGNING_KEY_ID`; the `key_id` in the seal identifies which key signed.

---

## Private key handling

The Ed25519 private key is a 32-byte seed loaded through `services/api/app/managed_keys.py` under the `EVIDENCE_SIGNING_ED25519` purpose. It:

* is **never** written into an evidence package;
* is **never** returned through any API;
* is **never** logged (failures log the key id and provider only);
* is **not** committed to source control;
* is read **only** at signing time;
* is **never** available to the offline verifier.

There is deliberately **no built-in development keypair**. A signing key shipped in source would be forgeable by anyone who can read this repository, so an unprovisioned deployment simply produces HMAC-only seals and reports authenticity as not independently verifiable.

Signing happens **in application memory**, not in an HSM or KMS. `hardware_backed` is `false` and every surface says so. Ed25519 buys independent verifiability, **not** hardware custody.

---

## Legacy HMAC packages

Packages exported before this release carry only the HMAC seal. Nothing migrates or rewrites them.

| Surface | Legacy package | Ed25519-signed package |
|---|---|---|
| Offline verifier | `Integrity: VERIFIED` / `Authenticity: NOT INDEPENDENTLY VERIFIABLE` (exit `3`) | `Integrity: VERIFIED` / `Authenticity: VERIFIED` (exit `0`) |
| Dashboard | "Legacy shared-secret HMAC seal. Integrity is verified; authenticity is **not independently verifiable** outside Decoda." | "Public-key verifiable · Ed25519", with key id |

The offline verifier **never** prints `Authenticity: VERIFIED` for an HMAC seal, because the reader does not hold — and must not be given — the secret that would check it.

---

## Offline verifier

```bash
python -m decoda_evidence_verifier verify evidence-package.zip --keyring decoda-evidence-keys.json
python -m decoda_evidence_verifier verify evidence-package.zip --json
python -m decoda_evidence_verifier inspect evidence-package.zip
```

Exit codes: `0` verified · `1` verification FAILED · `2` unusable package or usage error · `3` integrity verified but authenticity not independently verifiable.

It imports no Decoda application code, holds no secret, opens no socket, writes nothing, extracts nothing, and cannot sign. Full documentation, including the security properties and the untrusted-input defences, is in [`tools/decoda_evidence_verifier/README.md`](../tools/decoda_evidence_verifier/README.md).

---

## What offline verification proves — and does not

**Proves:**

* every packaged artifact still hashes to the SHA-256 the manifest records, at the recorded byte length;
* the manifest hashes to its own `manifest_sha256`;
* the Merkle root recomputes from the artifact set;
* for an Ed25519-signed package: the manifest digest was signed by the holder of the corresponding Decoda private signing key;
* the package records an audit-chain anchor, covered by the manifest hash and signature.

**Does NOT prove:**

* that every source event was truthful at ingestion time;
* that the blockchain, RPC provider or data feed was honest;
* that an off-chain system was correct;
* the entire historical server-side audit chain — only the single anchor value the package records, which is why the verifier reports audit linkage as `present`, never `verified`;
* hardware custody of the signing key (signing is in-process; `hardware_backed` is `false`).

---

## Cross-implementation test vectors

`tools/decoda_evidence_verifier/testvectors/evidence-test-vectors.json` pins canonical JSON bytes, per-file SHA-256, the manifest digest, Merkle leaves and root, the exact signed payload and a signature. Three implementations assert against it — backend generation, backend verification and the standalone verifier — so a silent drift in any serializer or hash fails a test before it can break evidence in the field.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `EXPORT_SIGNING_SECRET` | **production/staging** | HMAC signing secret. Minimum 32 bytes. |
| `EVIDENCE_SIGNING_SECRET` | Alternative | Accepted if `EXPORT_SIGNING_SECRET` is not set. |
| `EXPORT_SIGNING_KEY_ID` | Optional | Key rotation label. Default: `env-default`. |
| `EVIDENCE_SIGNING_ED25519_PRIVATE_KEY` | **Required for public-key authenticity** | Base64 of a 32-byte Ed25519 seed. **Development / self-hosted fallback only** — production should provision this through `MANAGED_KEY_PROVIDER` under the `EVIDENCE_SIGNING_ED25519` purpose. Unset means HMAC-only seals and authenticity reported as not independently verifiable. |
| `EVIDENCE_SIGNING_ED25519_KEY_ID` | Recommended | Key identifier recorded in every signature, e.g. `decoda-evidence-2026-01`. Default: `decoda-evidence-ed25519`. |
| `EVIDENCE_SIGNING_ED25519_KEY_SECRET_ID` | When managed provider | AWS Secrets Manager secret id for the Ed25519 seed. |
| `EVIDENCE_SIGNING_ED25519_KEY_ENCODING` | Optional | `base64` when the stored secret is raw 32 bytes base64-encoded. |
| `EVIDENCE_SIGNING_ED25519_PUBLIC_KEYS` | Optional | Comma-separated `key_id:base64_public_key[:status]` entries kept in the published keyring. This is how RETIRED public keys stay published so historical evidence remains verifiable. |
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

**This conflicts with customer deletion, and the conflict is deliberate — but it must not be hidden.** A retention sweep, an end-of-Pilot purge, and a customer's own immediate-deletion request all call `delete_object`. On a COMPLIANCE-mode bucket that call succeeds and writes a delete marker while the locked version stays retrievable until its retention date. Decoda therefore records the bucket's object-lock state in every `storage_delete` event (`details.storage`), so the deletion receipt says a locked version may persist rather than implying the bytes are gone. Set the bucket's Object Lock retention period no longer than the evidence retention period you publish, or be prepared to state the difference to customers.

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

1. **Not hardware-custodied**: Ed25519 signing happens in application memory with fetched key material. It is independently verifiable, but it is **not** an HSM/KMS-backed, hardware-custodied signature. `hardware_backed` is `false` and every surface reports it that way. A future KMS/HSM signer would set it true; nothing in this release does.
2. **Public-key signing must be provisioned**: A deployment with no `EVIDENCE_SIGNING_ED25519` key produces HMAC-only seals. That is truthfully reported everywhere, but until the key is provisioned in an environment, packages from that environment are not independently verifiable.
3. **Legacy packages stay legacy**: Packages exported before this release cannot gain a public-key signature retroactively without re-signing bytes that were sealed under different provenance. Nothing rewrites them; their integrity remains verifiable and their authenticity does not.
4. **Audit log pre-migration rows**: Rows inserted before migration `0091` have `row_hash = NULL` and are not included in chain verification. They are not flagged as tampered.
5. **Single audit anchor**: A package records one `previous_audit_anchor_hash`. Offline verification proves the anchor value is sealed; it cannot prove the chain behind it, because the chain is not in the package. The verifier reports audit linkage as `present`, never `verified`.
6. **In-transit integrity**: The HMAC and signature protect against post-export tampering, not interception. Use TLS for transport.
7. **Trust in the published key**: Offline authenticity is only as good as the keyring the auditor pinned. A keyring taken from inside the package it verifies proves nothing; obtain it from `.well-known` or a contractual channel.
8. **Seal downgrade is possible but not exploitable**: the seal is a detached document, so it is not covered by `manifest_sha256`. Anyone can strip the `signatures` block from a package and leave the HMAC. Doing so cannot forge anything — it can only make a package report `Authenticity: NOT INDEPENDENTLY VERIFIABLE` where it would have reported `VERIFIED`. The failure direction is closed: no edit to a package can turn an unsigned or altered package into a `VERIFIED` authenticity result without the private key. An auditor who expects a signed package and sees "not independently verifiable" should treat that as a red flag and request the package again.
