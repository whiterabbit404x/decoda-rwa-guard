# Secret Encryption

## Scheme
- Application secrets now use AES-256-GCM (`aes256gcm:v1`) with per-secret random IVs.
- Payload format: `aes256gcm:v1:<key_id>:<iv_b64url>:<ciphertext_b64url>`.
- Legacy base64 records are still readable for migration compatibility, then re-encrypted on updates.

## Environment
- `SECRET_ENCRYPTION_KEY`: base64-encoded 32-byte key.
- `SECRET_ENCRYPTION_KEY_ID`: key identifier for rotation metadata.

## Safety
- Production/staging startup fails when `SECRET_ENCRYPTION_KEY` is missing or malformed.
- API responses expose only masked/last4 metadata, never decrypted secrets.

## Production managed-key requirement

The static `SECRET_ENCRYPTION_KEY`, `AUTH_TOKEN_SECRET`, and `EXPORT_SIGNING_SECRET` variables are retained only for local development and tests. Staging and production must use the version-aware managed provider settings documented in `DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md`. New ciphertext uses `aes256gcm:v2` and embeds a non-secret provider key/version reference so old values remain decryptable after rotation. Evidence seals likewise store `key_id`, `key_version`, and `key_provider`; historical verification resolves the recorded version rather than the current version.
