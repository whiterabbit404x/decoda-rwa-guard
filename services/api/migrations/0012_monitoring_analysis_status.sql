ALTER TABLE analysis_runs
    ADD COLUMN IF NOT EXISTS analysis_source TEXT,
    ADD COLUMN IF NOT EXISTS analysis_status TEXT,
    ADD COLUMN IF NOT EXISTS degraded_reason TEXT;

UPDATE analysis_runs
SET analysis_source = COALESCE(analysis_source, source),
    analysis_status = COALESCE(analysis_status, 'completed')
WHERE analysis_source IS NULL OR analysis_status IS NULL;
