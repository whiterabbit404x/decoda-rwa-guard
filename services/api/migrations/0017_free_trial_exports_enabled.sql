UPDATE plan_entitlements
SET
    exports_enabled = TRUE,
    features = COALESCE(features, '{}'::jsonb) || jsonb_build_object('exports', true)
WHERE plan_key = 'free_trial';
