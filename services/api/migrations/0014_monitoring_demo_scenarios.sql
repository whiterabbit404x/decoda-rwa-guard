ALTER TABLE targets
    ADD COLUMN IF NOT EXISTS monitoring_demo_scenario TEXT NULL;

UPDATE targets
SET monitoring_demo_scenario = NULL
WHERE monitoring_demo_scenario IS NOT NULL
  AND monitoring_demo_scenario NOT IN ('safe', 'low_risk', 'medium_risk', 'high_risk', 'flash_loan_like', 'admin_abuse_like', 'risky_approval_like');
