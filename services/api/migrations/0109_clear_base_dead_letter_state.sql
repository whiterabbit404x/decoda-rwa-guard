-- Migration 0109: Recover dead-lettered Base targets after cursor repair.
--
-- Migrations 0107 and 0108 repaired corrupted block cursors and timestamps that
-- caused monitoring to fail for Base chain targets.  Targets that failed due to
-- those corrupted values may have been dead-lettered.  Now that the data is clean,
-- reset dead-letter state so the monitoring loop retries them immediately.

UPDATE targets
SET monitoring_dead_lettered_at = NULL,
    monitoring_delivery_attempts = 0,
    updated_at = NOW()
WHERE monitoring_dead_lettered_at IS NOT NULL
  AND lower(chain_network) IN ('base', 'base-mainnet');
