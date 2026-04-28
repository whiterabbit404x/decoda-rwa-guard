ALTER TABLE monitoring_reconcile_runs
    ADD COLUMN IF NOT EXISTS transition_version INTEGER NOT NULL DEFAULT 0;

UPDATE monitoring_reconcile_runs
SET transition_version = COALESCE(transition_version, 0)
WHERE transition_version IS NULL;

ALTER TABLE monitoring_reconcile_events
    ADD COLUMN IF NOT EXISTS step_name TEXT;

UPDATE monitoring_reconcile_events
SET step_name = COALESCE(NULLIF(btrim(step_name), ''), NULLIF(btrim(payload->>'stage'), ''), event_type, 'unknown')
WHERE step_name IS NULL OR btrim(step_name) = '';

ALTER TABLE monitoring_reconcile_events
    ALTER COLUMN step_name SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_events_workspace_run_step
    ON monitoring_reconcile_events (workspace_id, run_id, step_name, event_at DESC, created_at DESC);

CREATE OR REPLACE FUNCTION monitoring_reconcile_status_transition_allowed(previous_status text, next_status text)
RETURNS boolean
LANGUAGE SQL
IMMUTABLE
AS $$
    SELECT CASE
        WHEN previous_status IN ('completed', 'failed') THEN previous_status = next_status
        WHEN previous_status = 'running' THEN next_status IN ('running', 'completed', 'failed')
        WHEN previous_status = 'queued' THEN next_status IN ('queued', 'running', 'completed', 'failed')
        ELSE next_status IN ('queued', 'running', 'completed', 'failed')
    END;
$$;

CREATE OR REPLACE FUNCTION enforce_monitoring_reconcile_run_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT monitoring_reconcile_status_transition_allowed(OLD.status, NEW.status) THEN
        NEW.status := OLD.status;
        NEW.status_reason_code := OLD.status_reason_code;
        NEW.status_reason_detail := OLD.status_reason_detail;
        NEW.reason_codes := OLD.reason_codes;
        NEW.result_summary := OLD.result_summary;
        NEW.completed_at := OLD.completed_at;
    END IF;

    IF NEW.status IN ('completed', 'failed') AND NEW.completed_at IS NULL THEN
        NEW.completed_at := NOW();
    END IF;

    NEW.transition_version := COALESCE(OLD.transition_version, 0) + 1;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_monitoring_reconcile_run_transition ON monitoring_reconcile_runs;
CREATE TRIGGER trg_monitoring_reconcile_run_transition
BEFORE UPDATE ON monitoring_reconcile_runs
FOR EACH ROW
EXECUTE FUNCTION enforce_monitoring_reconcile_run_transition();
