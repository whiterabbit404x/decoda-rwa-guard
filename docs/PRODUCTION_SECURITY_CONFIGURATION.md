# Production security configuration — operator verification checklist

Every control in this file is **deployment-dependent**: it lives in Railway,
Vercel, Neon, S3 or AWS Secrets Manager, not in this repository. Source code
cannot prove any of it, so none of it may be stated as a Decoda application
guarantee on `/trust`, `/privacy`, `/security` or in sales material until an
operator has verified it and recorded the result here.

The rule this file exists to enforce:

> A public claim is either provable from code in this repository, or it names
> the infrastructure that provides it. It is never inferred from what a provider
> "normally" does.

Each row gives the setting, the expected state, how an operator checks it, and
**exactly which public claim the check unlocks**. Until a row is verified, the
corresponding `/trust` line stays in its current, weaker form.

Record verification as: date, operator, evidence (console screenshot, CLI
output, or ticket). **Never paste secret values, connection strings, key
material or bucket ARNs containing account identifiers into this file.**

---

## 1. Transport security (Vercel — web)

| Field | Value |
| --- | --- |
| **Setting** | HTTPS enforcement and automatic HTTP→HTTPS redirect on the production domain |
| **Expected** | All production domains serve HTTPS only; plain HTTP redirects with 301/308 |
| **How to verify** | `curl -sSI http://<domain>/` → expect `301`/`308` to `https://`. Then `curl -sSI https://<domain>/` → expect `200` plus `strict-transport-security: max-age=63072000; includeSubDomains; preload` |
| **Claim unlocked** | "Production web traffic is served over HTTPS by the deployment platform" |
| **Verified** | ☐ not yet verified |

HSTS itself **is** in this repository — `apps/web/next.config.js` emits it when
`NODE_ENV=production` or `APP_MODE=production`. What is *not* provable from the
repo is that the production build actually runs with one of those set. Confirm
the header is present on a live response, not just in the config.

> ⚠️ **`includeSubDomains; preload` is a commitment, not a default.** The header
> currently shipped asserts that *every* subdomain of the apex domain is
> HTTPS-only, and `preload` is effectively irreversible once the domain is
> accepted into the browser preload list. Before this reaches a domain with
> sibling subdomains, enumerate them and confirm each one serves HTTPS. If any
> does not, drop `includeSubDomains` and `preload` and ship
> `max-age=31536000` alone first. Removing `preload` later requires a removal
> request to hstspreload.org and browser-release lead time.

## 2. Transport security (Railway — API)

| Field | Value |
| --- | --- |
| **Setting** | HTTPS termination on the public API domain |
| **Expected** | The public API hostname serves HTTPS; plain HTTP is redirected or refused |
| **How to verify** | `curl -sSI http://<api-domain>/health` and `curl -sSI https://<api-domain>/health` |
| **Claim unlocked** | "Production API traffic is served over HTTPS by the deployment platform" |
| **Verified** | ☐ not yet verified |

The FastAPI service does **not** emit HSTS, does **not** redirect HTTP, and does
**not** enforce a minimum TLS version. `railway.json` contains no TLS settings.
So a TLS version or cipher claim for the API is **unsupported from this
repository in every deployment** and must not appear on a public page.

## 3. Internal service isolation

| Field | Value |
| --- | --- |
| **Setting** | Network reachability of `RISK_ENGINE_URL`, `THREAT_ENGINE_URL`, `COMPLIANCE_SERVICE_URL`, `RECONCILIATION_SERVICE_URL` |
| **Expected** | Private-network only; not routable from the public internet |
| **How to verify** | From outside the deployment network, attempt `curl -sS <service-url>/health`. Expect connection refused / timeout, **not** a JSON body |
| **Claim unlocked** | "Internal analysis services are separated from the public internet by deployment network configuration" |
| **Verified** | ☐ not yet verified |

> ⚠️ `services/api/app/main.py::request_json` sends **no credential** on these
> calls — only `Content-Type: application/json` — and the analysis services
> declare no authentication dependency. Network isolation is therefore the
> *only* control on this path. If any of these hostnames is publicly routable,
> it is an unauthenticated analysis endpoint, and the claim in §3 is false.
> This is the highest-value row in this file.

## 4. Database — Neon

| Field | Value |
| --- | --- |
| **Setting** | Connection TLS (`sslmode` in `DATABASE_URL`) |
| **Expected** | `sslmode=require` or stricter in the production connection string |
| **How to verify** | Inspect the Railway variable **without printing it in full**; confirm the `sslmode=` parameter is present and at least `require` |
| **Claim unlocked** | "Database connections are encrypted in transit" |
| **Verified** | ☐ not yet verified |

Application code never sets `sslmode`. `pilot.py::pg_connection` passes the DSN
through to `psycopg.connect` unchanged, so connection encryption is entirely a
property of the configured URL.

| Field | Value |
| --- | --- |
| **Setting** | Neon storage encryption at rest |
| **Expected** | Provider-managed encryption at rest enabled |
| **How to verify** | Neon console / provider documentation for the specific plan and region; attach the provider statement |
| **Claim unlocked** | "Database storage is encrypted at rest **by our infrastructure provider**" — attributed to the provider, never phrased as a Decoda application control |
| **Verified** | ☐ not yet verified |

| Field | Value |
| --- | --- |
| **Setting** | Neon PITR / backup retention window |
| **Expected** | A stated retention window, recorded here |
| **How to verify** | Neon console → branch → PITR settings |
| **Claim unlocked** | Nothing new. It **bounds** the existing honest statement that residual copies persist "until their normal backup-retention cycle completes" |
| **Verified** | ☐ not yet verified |

| Field | Value |
| --- | --- |
| **Setting** | Database region |
| **Expected** | EU region, matching the `/trust` statement "hosted in the EU (Neon)" |
| **How to verify** | Neon console → project region |
| **Claim unlocked** | The existing EU data-residency statement on `/trust` |
| **Verified** | ☐ not yet verified |

## 5. Evidence object storage (S3 or S3-compatible)

| Field | Value |
| --- | --- |
| **Setting** | Bucket default encryption on `EXPORT_S3_BUCKET` |
| **Expected** | Default encryption enabled (SSE-S3 or SSE-KMS) |
| **How to verify** | `aws s3api get-bucket-encryption --bucket <bucket>` → a `ServerSideEncryptionConfiguration` rule |
| **Claim unlocked** | "Evidence packages are stored with server-side encryption at rest" — **currently removed from `/trust`** |
| **Verified** | ☐ not yet verified |

> `export_storage.py::S3ExportStorage.write_bytes` calls `put_object(Bucket=…,
> Key=…, Body=…)` with **no `ServerSideEncryption` argument**. Objects therefore
> inherit whatever the bucket applies. `EXPORT_S3_ENDPOINT` also permits any
> S3-compatible provider (MinIO, R2, Backblaze), which may have entirely
> different defaults — so this must be verified per deployment, not once.

| Field | Value |
| --- | --- |
| **Setting** | KMS key, if SSE-KMS is used |
| **Expected** | A customer-managed key with a rotation policy |
| **How to verify** | `aws kms get-key-rotation-status --key-id <id>` |
| **Claim unlocked** | A KMS claim. **No Decoda code passes `KMSKeyId`**, so without this row the word "KMS" must not appear in customer-facing copy |
| **Verified** | ☐ not yet verified |

| Field | Value |
| --- | --- |
| **Setting** | Object Lock / versioning |
| **Expected** | Object Lock enabled if WORM durability is being represented to customers |
| **How to verify** | `aws s3api get-object-lock-configuration --bucket <bucket>`; `aws s3api get-bucket-versioning --bucket <bucket>` |
| **Claim unlocked** | A WORM/immutable-storage claim. Decoda **reads** this state (`object_lock_status`) and reports it; it never **sets** it, and never applies a retention header on write |
| **Verified** | ☐ not yet verified |

## 6. Evidence signing — Ed25519

| Field | Value |
| --- | --- |
| **Setting** | `EVIDENCE_SIGNING_ED25519` key material provisioned for the API and every worker that seals packages |
| **Expected** | Provisioned via `MANAGED_KEY_PROVIDER=aws_secrets_manager` + `EVIDENCE_SIGNING_ED25519_KEY_SECRET_ID`, or `EVIDENCE_SIGNING_ED25519_PRIVATE_KEY` for self-hosted |
| **How to verify** | Export a fresh evidence package and confirm `seal.json` has `schema_version` v2 and a `signatures[]` entry with `algorithm: Ed25519`. Then verify it offline: `python -m decoda_evidence_verifier verify <pkg>.zip --keyring <keyring>.json` → exit code `0` |
| **Claim unlocked** | "New evidence packages carry a public-key signature that proves origin offline" **as an unconditional statement**. Until then `/trust` correctly says "where a deployment has provisioned…" |
| **Verified** | ☐ not yet verified |

> There is **no startup check** requiring this key and it is **not in
> `.env.example`**. `evidence_signing.py::_ed25519_signature_for` returns `None`
> when it is absent, and the package is sealed HMAC-only — which every Decoda
> surface then correctly reports as "authenticity not independently
> verifiable". An unconfigured production deployment is therefore *silently*
> HMAC-only. Verify per environment, including workers.

| Field | Value |
| --- | --- |
| **Setting** | Published public keyring reachable by customers |
| **Expected** | The keyring JSON is published at a stable, documented URL |
| **How to verify** | Fetch it from outside the product and verify a real package against it with `--keyring` (never `--allow-bundled-keyring`) |
| **Claim unlocked** | "verified offline using the **published** public verification key" |
| **Verified** | ☐ not yet verified |

> The signing key is a raw seed loaded into application memory. It is **not**
> HSM- or KMS-custodied. `hardware_backed` is `False` for every signer in this
> build and the UI is gated on it, so **"HSM", "hardware-backed" and
> "non-exportable" must not appear in customer-facing copy** unless a signer is
> added that performs the signing operation inside a KMS/HSM boundary.

## 7. Managed key provider

| Field | Value |
| --- | --- |
| **Setting** | `MANAGED_KEY_PROVIDER`, `MANAGED_KEY_ENFORCEMENT` |
| **Expected** | `aws_secrets_manager` and, once all secret IDs are provisioned, `strict` |
| **How to verify** | Readiness output reports whether the managed provider is configured; startup logs `secret_encryption_mode=legacy_environment_key` while still on static env keys |
| **Claim unlocked** | The word "managed" in "managed, versioned application key". Under the default `env` provider the key is a static environment variable with **no durable version history** and no rotation path |
| **Verified** | ☐ not yet verified |

Defaults are `MANAGED_KEY_PROVIDER=env` and
`MANAGED_KEY_ENFORCEMENT=compatibility`, so a production deployment can legally
run on static environment keys today. `rotate_managed_key()` **requires** AWS
Secrets Manager and raises otherwise.

## 8. Retention worker

| Field | Value |
| --- | --- |
| **Setting** | `retention-worker` process running |
| **Expected** | Running continuously; `Procfile` declares `retention-worker: python -m services.api.app.retention_worker` |
| **How to verify** | Confirm the Railway process is up and that a recent run is recorded |
| **Claim unlocked** | Every retention period published on `/privacy` and `/trust`. If the worker is not running, **nothing is deleted on schedule** and those pages become inaccurate |
| **Verified** | ☐ not yet verified |

> 🔴 **Known defect, must be resolved before the audit-anonymization claim is
> accurate.** Migration `0100` installs
> `guard_audit_logs_append_only`, which permits `DELETE` when
> `app.retention_worker='on'` but raises `'audit_logs is append-only'` on
> **every** `UPDATE`, unconditionally. `data_retention.py` anonymizes audit rows
> with an `UPDATE` (`ANONYMIZE_SQL['audit_logs']`), and
> `pilot_retention.py` selects `'anonymize'` as the audit mode for the
> end-of-Pilot sequence. Against real PostgreSQL that `UPDATE` raises. The
> existing test asserts only on the migration's **text**, never executing the
> trigger, so this is not caught. Until it is fixed, treat "audit logs are
> anonymized" on `/privacy` as unproven. `/trust` no longer claims immutability
> and states that audit records are removed on the published schedule, which is
> true under either resolution.

## 9. Migrations

| Field | Value |
| --- | --- |
| **Setting** | Applied migration version |
| **Expected** | At or beyond `0155`; **`0150` is the floor for mandatory Pilot MFA** |
| **How to verify** | Compare the applied migration version against `services/api/migrations/` |
| **Claim unlocked** | "MFA is mandatory for every human user of a Pilot workspace". Before `0150` there is no organization plan to read, so the Pilot floor does not apply and the configurable workspace policy decides |
| **Verified** | ☐ not yet verified |

Also relevant: `0091` (audit hash chain), `0100` (append-only audit trigger),
`0155` (telemetry privacy policy).

---

## Summary — what this repository proves on its own

**Provable from code, in every deployment:**

- AES-256-GCM encryption of workspace secrets under a versioned key, random
  96-bit nonce per secret, GCM authentication tag, AAD binding each ciphertext
  to its record (`secret_crypto.py`)
- Salted scrypt password hashing (`pilot.py::hash_password`)
- Per-file SHA-256, canonical manifest hash, and a `decoda-merkle-v1` Merkle
  root over the artifact set (`evidence_signing.py`, `evidence_merkle.py`)
- Offline integrity verification with no Decoda secret, account or API access
  (`tools/decoda_evidence_verifier`, Python standard library only)
- Append-only audit rows — no `UPDATE` from anything, no `DELETE` from the
  application — plus a per-workspace SHA-256 hash chain (migrations `0100`,
  `0091`)
- Mandatory Pilot MFA enforced server-side at one chokepoint, given migration
  `0150` (`mfa_authorization.py`, `pilot.py::require_pilot_mfa`)
- Pilot execution boundary refusing production state changes
  (`execution_authorization.py`)
- Mandatory credential stripping at telemetry ingestion (`telemetry_privacy.py`)
- No customer wallet private key, seed phrase or signing credential is accepted
  anywhere in the product
- HSTS emitted by the web application in production (`next.config.js`)
- No third-party analytics in the authenticated UI

**Not provable from code — every row above:** TLS versions and ciphers, HTTP→HTTPS
redirection, database encryption at rest, backup retention, object-storage
encryption at rest, Object Lock, KMS, whether Ed25519 signing is provisioned,
whether the managed key provider is in use, whether the retention worker runs,
and internal-service network isolation.
