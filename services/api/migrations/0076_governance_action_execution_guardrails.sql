ALTER TABLE governance_actions
    DROP CONSTRAINT IF EXISTS governance_actions_action_mode_requires_integration_check;

ALTER TABLE governance_actions
    ADD CONSTRAINT governance_actions_action_mode_requires_integration_check
    CHECK (
        action_mode <> 'executed'
        OR (
            COALESCE((payload -> 'integration_capability' ->> 'enabled')::boolean, false) = true
            AND (payload -> 'integration_capability' ->> 'recorded_at') IS NOT NULL
        )
    );
