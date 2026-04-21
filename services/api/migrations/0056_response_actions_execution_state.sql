ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS execution_state TEXT NOT NULL DEFAULT 'simulated_executed';

UPDATE response_actions
SET execution_state = CASE
    WHEN status = 'executed' THEN 'simulated_executed'
    WHEN status IN ('pending', 'approved', 'planned') AND mode = 'live' THEN 'proposed'
    WHEN status = 'failed' THEN 'failed'
    WHEN status = 'canceled' THEN 'canceled'
    ELSE 'simulated_executed'
END
WHERE execution_state IS NULL
   OR execution_state = ''
   OR execution_state = 'simulated_executed';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_execution_state_check'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions
            ADD CONSTRAINT response_actions_execution_state_check
            CHECK (
                execution_state IN (
                    'simulated_executed',
                    'proposed',
                    'live_manual_required',
                    'unsupported',
                    'failed',
                    'canceled'
                )
            );
    END IF;
END $$;
