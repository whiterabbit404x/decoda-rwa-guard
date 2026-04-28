DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_runs_status_reason_code_not_blank_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_runs
            ADD CONSTRAINT monitoring_reconcile_runs_status_reason_code_not_blank_check
            CHECK (status_reason_code IS NULL OR length(btrim(status_reason_code)) > 0);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_events_reason_code_not_blank_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_events
            ADD CONSTRAINT monitoring_reconcile_events_reason_code_not_blank_check
            CHECK (reason_code IS NULL OR length(btrim(reason_code)) > 0);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION monitoring_reason_codes_jsonb_is_valid(input jsonb)
RETURNS boolean
LANGUAGE SQL
IMMUTABLE
AS $$
    SELECT (
        jsonb_typeof(input) = 'array'
        AND NOT jsonb_path_exists(input, '$[*] ? (@.type() != "string" || @ == "")')
        AND NOT jsonb_path_exists(input, '$[*] ? (@ like_regex "^\\s+$")')
    );
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_runs_reason_codes_array_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_runs
            ADD CONSTRAINT monitoring_reconcile_runs_reason_codes_array_check
            CHECK (monitoring_reason_codes_jsonb_is_valid(reason_codes));
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_events_reason_codes_array_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_events
            ADD CONSTRAINT monitoring_reconcile_events_reason_codes_array_check
            CHECK (monitoring_reason_codes_jsonb_is_valid(reason_codes));
    END IF;
END $$;
