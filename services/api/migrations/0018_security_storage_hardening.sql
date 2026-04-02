ALTER TABLE workspace_slack_integrations
    ADD COLUMN IF NOT EXISTS webhook_secret_scheme TEXT NOT NULL DEFAULT 'aes256gcm-v1',
    ADD COLUMN IF NOT EXISTS bot_token_secret_scheme TEXT NOT NULL DEFAULT 'aes256gcm-v1',
    ADD COLUMN IF NOT EXISTS secret_kid TEXT NOT NULL DEFAULT 'default';

UPDATE export_jobs
SET output_path = CONCAT('local:', output_path)
WHERE output_path IS NOT NULL
  AND output_path <> ''
  AND POSITION(':' IN output_path) = 0;
