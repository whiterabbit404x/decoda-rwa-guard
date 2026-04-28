ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ;

UPDATE response_actions
SET approved_at = COALESCE(
        approved_at,
        NULLIF(execution_metadata ->> 'approved_at', '')::timestamptz
    ),
    failed_at = COALESCE(
        failed_at,
        CASE
            WHEN status = 'failed' THEN COALESCE(NULLIF(execution_metadata ->> 'finalized_at', '')::timestamptz, executed_at, created_at)
            ELSE NULL
        END
    )
WHERE approved_at IS NULL
   OR (status = 'failed' AND failed_at IS NULL);
