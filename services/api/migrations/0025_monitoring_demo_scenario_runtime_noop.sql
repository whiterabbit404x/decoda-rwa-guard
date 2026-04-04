UPDATE targets
SET monitoring_demo_scenario = NULL,
    updated_at = NOW()
WHERE monitoring_demo_scenario IS NOT NULL;
