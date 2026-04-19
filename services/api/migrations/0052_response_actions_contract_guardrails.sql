ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS result_summary TEXT NULL,
    ADD COLUMN IF NOT EXISTS operator_notes TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_mode_check'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions
            ADD CONSTRAINT response_actions_mode_check
            CHECK (mode IN ('simulated', 'recommended', 'live'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_status_check'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions
            ADD CONSTRAINT response_actions_status_check
            CHECK (status IN ('pending', 'executed', 'failed', 'canceled'));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_type_check'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions
            ADD CONSTRAINT response_actions_type_check
            CHECK (
                action_type IN (
                    'freeze_wallet',
                    'block_transaction',
                    'revoke_approval',
                    'disable_monitored_system',
                    'suppress_rule',
                    'notify_team'
                )
            );
    END IF;
END $$;
