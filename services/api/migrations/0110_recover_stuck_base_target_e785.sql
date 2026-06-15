-- Migration 0110: Recover the specific Base target that remained dead-lettered
-- after the cursor/timestamp repairs in 0104-0109.
--
-- Target e7851a52-8fb1-48cd-84a3-d033f591c5dd (workspace 1155f479-3e5b-4d90-be6c-fd6c1d6b957d,
-- chain=base, wallet=0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f) kept reporting
-- skipped_dead_lettered=1 in the worker cycle summary, which starved all live polling for
-- the workspace. The worker now self-heals dead-lettered targets after a short backoff
-- (MONITORING_DEAD_LETTER_RETRY_SECONDS), but we clear this target immediately on deploy so
-- it returns to normal due-selection on the very next cycle.
--
-- Workspace-scoped and idempotent: only touches this single target, only when it is still
-- dead-lettered. Also clears any stale lease so the target can be claimed right away.

UPDATE targets
SET monitoring_dead_lettered_at = NULL,
    monitoring_delivery_attempts = 0,
    monitoring_claimed_by = NULL,
    monitoring_claimed_at = NULL,
    monitoring_lease_token = NULL,
    monitoring_lease_expires_at = NULL,
    last_run_status = 'recovered',
    updated_at = NOW()
WHERE id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'::uuid
  AND workspace_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'::uuid
  AND deleted_at IS NULL
  AND monitoring_dead_lettered_at IS NOT NULL;
