ALTER TABLE export_jobs
    ADD COLUMN IF NOT EXISTS storage_backend TEXT,
    ADD COLUMN IF NOT EXISTS storage_object_key TEXT;

UPDATE export_jobs
SET storage_backend = COALESCE(storage_backend, 'local'),
    storage_object_key = COALESCE(storage_object_key, output_path)
WHERE storage_backend IS NULL OR storage_object_key IS NULL;

ALTER TABLE workspace_slack_integrations
    ADD COLUMN IF NOT EXISTS secret_scheme TEXT,
    ADD COLUMN IF NOT EXISTS secret_key_id TEXT;

UPDATE workspace_slack_integrations
SET secret_scheme = COALESCE(secret_scheme, 'legacy_b64'),
    secret_key_id = COALESCE(secret_key_id, 'legacy')
WHERE secret_scheme IS NULL OR secret_key_id IS NULL;
