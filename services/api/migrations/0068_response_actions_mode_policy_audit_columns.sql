ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS tx_hash TEXT,
    ADD COLUMN IF NOT EXISTS result_status TEXT;

UPDATE response_actions
SET tx_hash = COALESCE(tx_hash, safe_tx_hash, NULLIF(execution_metadata ->> 'tx_hash', '')),
    result_status = COALESCE(result_status, status, NULLIF(execution_metadata ->> 'final_status', ''))
WHERE tx_hash IS NULL
   OR result_status IS NULL;
