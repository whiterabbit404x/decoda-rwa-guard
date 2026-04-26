ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS execution_artifacts JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS provider_receipts JSONB NOT NULL DEFAULT '[]'::jsonb;

UPDATE response_actions
SET execution_artifacts = COALESCE(execution_artifacts, '{}'::jsonb),
    provider_receipts = COALESCE(provider_receipts, '[]'::jsonb)
WHERE execution_artifacts IS NULL
   OR provider_receipts IS NULL;
