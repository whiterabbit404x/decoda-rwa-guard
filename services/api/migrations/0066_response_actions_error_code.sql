ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS error_code TEXT;

UPDATE response_actions
SET error_code = COALESCE(error_code, execution_metadata ->> 'error_code')
WHERE error_code IS NULL;
