# Upgrade Notes

## Frontend/runtime
- Auth moved to HttpOnly server-managed session cookies via Next.js auth route handlers.
- Browser localStorage token persistence removed.
- CSRF token cookie + header validation added for authenticated auth mutations.

## Backend/security
- Integration secret storage now uses AES-256-GCM envelope payloads (`SECRETS_MASTER_KEY`).
- Legacy base64 secret values are lazily upgraded on access and can be bulk-converted with `python services/api/scripts/reencrypt_integration_secrets.py`.
- Export artifact persistence now uses pluggable storage backend (`EXPORTS_STORAGE_BACKEND=local|s3`).
