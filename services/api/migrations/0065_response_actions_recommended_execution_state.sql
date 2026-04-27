ALTER TABLE response_actions
    DROP CONSTRAINT IF EXISTS response_actions_execution_state_check;

ALTER TABLE response_actions
    ADD CONSTRAINT response_actions_execution_state_check
    CHECK (
        execution_state IN (
            'simulated_executed',
            'recommended_approved',
            'proposed',
            'live_manual_required',
            'unsupported',
            'failed',
            'canceled'
        )
    );
