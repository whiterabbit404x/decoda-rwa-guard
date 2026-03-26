ALTER TABLE workspace_slack_integrations
    ADD COLUMN IF NOT EXISTS slack_mode TEXT NOT NULL DEFAULT 'webhook',
    ADD COLUMN IF NOT EXISTS bot_token_encrypted TEXT,
    ADD COLUMN IF NOT EXISTS bot_token_last4 TEXT,
    ADD COLUMN IF NOT EXISTS severity_routing JSONB NOT NULL DEFAULT '{"low":"default","medium":"default","high":"default","critical":"default"}'::jsonb;

ALTER TABLE slack_deliveries
    ADD COLUMN IF NOT EXISTS provider_mode TEXT NOT NULL DEFAULT 'webhook';

CREATE INDEX IF NOT EXISTS idx_workspace_slack_integrations_mode
    ON workspace_slack_integrations(workspace_id, slack_mode, enabled);
