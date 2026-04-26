ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS provider_request_id TEXT,
    ADD COLUMN IF NOT EXISTS provider_response_id TEXT,
    ADD COLUMN IF NOT EXISTS error_reason TEXT;

UPDATE response_actions
SET provider_request_id = COALESCE(provider_request_id, execution_artifacts #>> '{provider,external_request_id}'),
    provider_response_id = COALESCE(provider_response_id, safe_tx_hash),
    error_reason = COALESCE(error_reason, CASE WHEN status = 'failed' THEN result_summary ELSE NULL END)
WHERE provider_request_id IS NULL
   OR provider_response_id IS NULL
   OR error_reason IS NULL;
