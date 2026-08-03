-- Response Actions (Screen 8 — Playbook Execution Agent): domain action types.
--
-- The recommendation generator previously produced only 'notify_team' for every
-- incident, so the constrained action_type set (migration 0052) was limited to
-- six containment/notification keys. The Playbook Execution Agent now formulates
-- incident-specific mitigation steps (preserve evidence, increase monitoring,
-- isolate a degraded provider, rotate a compromised credential, escalate a
-- multisig, etc.), so the CHECK constraint is widened to the full canonical set
-- of response action types recognized by the API (RESPONSE_ACTION_TYPES in
-- pilot.py). Each key still maps to a canonical display title, a deterministic
-- runbook profile, and a truthful live-execution capability (mostly manual/dry-
-- run — no fake live adapters are introduced here).
--
-- Idempotent: drops the prior constraint (if present) and re-adds the widened
-- one, so the startup migration runner can re-apply it safely. Additive only —
-- no existing rows are modified or deleted.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_type_check'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions DROP CONSTRAINT response_actions_type_check;
    END IF;

    ALTER TABLE response_actions
        ADD CONSTRAINT response_actions_type_check
        CHECK (
            action_type IN (
                -- Containment
                'freeze_wallet',
                'pause_mint_redeem',
                'pause_asset_transfers',
                'block_transaction',
                -- Security
                'revoke_approval',
                'disable_integration',
                'rotate_credential',
                -- Communication / escalation
                'notify_team',
                'notify_compliance_team',
                'escalate_to_issuer',
                'escalate_multisig',
                -- Detection / monitoring
                'disable_monitored_system',
                'isolate_provider',
                'increase_monitoring',
                'suppress_rule',
                -- Forensics / evidence
                'preserve_evidence',
                'snapshot_chain_state',
                'generate_regulator_auditor_package'
            )
        );
END $$;

-- Add a truthful 'recommended' execution_state so a freshly recommended action
-- (planning step, no dry-run yet) is not forced to 'simulated'. Marking an
-- un-simulated recommendation as 'simulated' is the root cause of the misleading
-- "Simulated" pill on brand-new recommendations. Widen the constraint (keeping
-- every previously allowed value) so the recommend endpoint can record the
-- honest state.
DO $$
BEGIN
    ALTER TABLE response_actions DROP CONSTRAINT IF EXISTS response_actions_execution_state_check;

    ALTER TABLE response_actions
        ADD CONSTRAINT response_actions_execution_state_check
        CHECK (
            execution_state IN (
                -- Planning (new): recommended but not yet simulated/executed
                'recommended',
                -- Canonical states
                'simulated',
                'proposal_created',
                'awaiting_approval',
                'submitted',
                'confirmed',
                'cancelled',
                -- Shared states
                'failed',
                'unsupported',
                -- Legacy states (retained for backward compat)
                'simulated_executed',
                'recommended_approved',
                'proposed',
                'live_manual_required'
            )
        );
END $$;
