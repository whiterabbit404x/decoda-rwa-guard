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
