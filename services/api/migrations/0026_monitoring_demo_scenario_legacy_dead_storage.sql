UPDATE targets
SET monitoring_demo_scenario = NULL
WHERE monitoring_demo_scenario IS NOT NULL;

COMMENT ON COLUMN targets.monitoring_demo_scenario IS 'Legacy deprecated column. Runtime paths must ignore this field.';
