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

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_runs_reason_codes_array_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_runs
            ADD CONSTRAINT monitoring_reconcile_runs_reason_codes_array_check CHECK (
                jsonb_typeof(reason_codes) = 'array'
                AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(reason_codes) AS code
                    WHERE jsonb_typeof(code) <> 'string'
                       OR length(btrim(code #>> '{}')) = 0
                )
            );
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
            ADD CONSTRAINT monitoring_reconcile_events_reason_codes_array_check CHECK (
                jsonb_typeof(reason_codes) = 'array'
                AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(reason_codes) AS code
                    WHERE jsonb_typeof(code) <> 'string'
                       OR length(btrim(code #>> '{}')) = 0
                )
            );
    END IF;
END $$;
