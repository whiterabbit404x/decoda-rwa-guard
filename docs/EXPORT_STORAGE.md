# Export Storage

## Backends
- `local` backend for development only.
- `s3` backend for staging/production durability.

## Environment
- `EXPORT_STORAGE_BACKEND=local|s3`
- `EXPORTS_DIR` (local dev)
- `EXPORT_S3_BUCKET`
- `EXPORT_S3_REGION`
- `EXPORT_S3_PREFIX`
- `EXPORT_S3_ENDPOINT` (optional for S3-compatible providers)

## Behavior
- Export jobs now persist storage metadata (`storage_backend`, `storage_object_key`).
- Download endpoint streams bytes from configured storage backend.
- Local filesystem backend is blocked in staging/production.
