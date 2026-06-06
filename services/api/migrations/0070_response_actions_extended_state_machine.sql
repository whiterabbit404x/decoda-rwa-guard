-- Migration 0070: Extend response action execution state machine
--
-- Adds canonical execution states for the truthful execution state machine:
--   simulated         - dry-run completed; no on-chain effect
--   proposal_created  - Safe/multisig proposal created; awaiting approval
--   awaiting_approval - proposal submitted; waiting for required signers
--   submitted         - transaction in chain mempool
--   confirmed         - transaction confirmed on-chain (tx_hash must be set)
--   cancelled         - action cancelled before execution
--
-- Legacy states are retained for backward compatibility:
--   simulated_executed, recommended_approved, proposed, live_manual_required
--
-- Migrates all 'simulated_executed' rows to 'simulated' to remove
-- the misleading 'executed' label for simulation-only actions.

ALTER TABLE response_actions
    DROP CONSTRAINT IF EXISTS response_actions_execution_state_check;

ALTER TABLE response_actions
    ADD CONSTRAINT response_actions_execution_state_check
    CHECK (
        execution_state IN (
            -- Canonical states (new)
            'simulated',
            'proposal_created',
            'awaiting_approval',
            'submitted',
            'confirmed',
            'cancelled',
            -- Shared states (existing + new)
            'failed',
            'unsupported',
            -- Legacy states (retained for backward compat)
            'simulated_executed',
            'recommended_approved',
            'proposed',
            'live_manual_required'
        )
    );

-- Migrate legacy 'simulated_executed' to canonical 'simulated'.
-- This removes the misleading 'executed' label for simulation-only actions.
UPDATE response_actions
SET execution_state = 'simulated'
WHERE execution_state = 'simulated_executed';

-- Add execution_mode column if not present (tracks simulation vs live vs proposal)
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(64);

-- Add proposal_id for tracking Safe/multisig proposal identifiers
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS proposal_id VARCHAR(256);

-- Add approval_required flag for destructive action gating
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS approval_required BOOLEAN NOT NULL DEFAULT FALSE;

-- Add approved_by for recording who approved live destructive actions
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS approved_by VARCHAR(256);

-- Add submitted_at for tracking when a transaction was submitted to the chain
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;

-- Add confirmed_at for tracking on-chain confirmation
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;

-- Add failure_reason (structured) separate from error_reason (free text)
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(512);

-- Add execution_provider to record which executor/provider was used
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS execution_provider VARCHAR(64);

-- Add simulation_result_json for storing simulation output artifacts
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS simulation_result_json JSONB;

-- Back-fill execution_mode for existing simulated rows
UPDATE response_actions
SET execution_mode = 'simulation'
WHERE execution_state = 'simulated'
  AND execution_mode IS NULL;

-- Back-fill execution_mode for existing proposed rows (safe/governance proposals)
UPDATE response_actions
SET execution_mode = 'safe_proposal'
WHERE execution_state = 'proposed'
  AND execution_mode IS NULL;
