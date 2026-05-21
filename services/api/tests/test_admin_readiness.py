from services.api.app.production_readiness import build_production_readiness


def _base(**overrides):
    payload = dict(
        env_checks={
            "database_reachable": True,
            "auth_session_configured": True,
            "required_env_vars_present": True,
            "redis_required": False,
            "redis_configured": False,
            "billing_required": False,
            "billing_configured": False,
            "paid_ui_disabled": True,
            "email_required": True,
            "email_configured": True,
            "app_base_url_configured": True,
            "api_url_configured": True,
        },
        runtime={
            "last_heartbeat_at": "2026-01-01T00:00:00Z",
            "latest_poll_at": "2026-01-01T00:00:30Z",
            "last_telemetry_at": "2026-01-01T00:01:00Z",
            "evidence_source": "live",
            "workspace_evaluated": True,
            "workspace_scoped": True,
            "protected_assets_count": 1,
            "reporting_systems_count": 1,
            "enabled_monitoring_configs_count": 1,
            "target_coverage_status": "covered",
            "provider_health_status": "healthy",
            "freshness_status": "fresh",
            "confidence_status": "high",
            "contradiction_flags": [],
        },
        workflow={"detections": 1, "alerts": 1, "incidents": 1, "response_actions": 1, "linkage_status": "pass", "linkage_reason": "ok"},
        integrations={"slack_integration_status": "pass", "webhook_integration_status": "pass", "delivery_logs_status": "pass", "api_key_support_status": "pass"},
        exports={"evidence_source": "live", "export_capability_status": "pass", "latest_export_job_status": "pass", "audit_log_availability": "pass", "proof_bundle_capability": "pass"},
        security={"readiness_access_control": "pass", "admin_workspace_scope": True},
    )
    payload.update(overrides)
    return payload


def test_pilot_ready_all_pass():
    out = build_production_readiness(**_base())
    assert out["ready_for_pilot"] is True


def test_missing_telemetry_blocks_readiness():
    out = build_production_readiness(**_base(runtime={**_base()["runtime"], "last_telemetry_at": None}))
    assert out["ready_for_pilot"] is False
    assert "telemetry_missing" in out["blocking_reasons"]


def test_heartbeat_only_does_not_pass_telemetry():
    out = build_production_readiness(**_base(runtime={**_base()["runtime"], "last_telemetry_at": None}))
    runtime_checks = out["categories"]["Runtime"]
    assert any(c["key"] == "latest_telemetry" and c["status"] == "fail" for c in runtime_checks)


def test_protected_assets_zero_blocks_when_workspace_scoped():
    out = build_production_readiness(**_base(runtime={**_base()["runtime"], "protected_assets_count": 0}))
    assert "no_protected_assets" in out["blocking_reasons"]


def test_reporting_systems_zero_warns_not_pass():
    out = build_production_readiness(**_base(runtime={**_base()["runtime"], "reporting_systems_count": 0}))
    assert "setup_required_reporting_systems" in out["warnings"]


def test_contradiction_flags_block_pilot_readiness():
    out = build_production_readiness(**_base(runtime={**_base()["runtime"], "contradiction_flags": ["x"]}))
    assert out["ready_for_pilot"] is False


def test_simulator_evidence_blocks_paid_launch():
    out = build_production_readiness(**_base(exports={**_base()["exports"], "evidence_source": "simulator"}, runtime={**_base()["runtime"], "evidence_source": "simulator"}))
    assert out["ready_for_paid_public_launch"] is False


def test_missing_billing_when_required_blocks_launch():
    env = {**_base()["env_checks"], "paid_ui_disabled": False, "billing_required": True, "billing_configured": False}
    out = build_production_readiness(**_base(env_checks=env))
    assert out["ready_for_paid_public_launch"] is False


def test_email_missing_when_required_blocks_launch():
    env = {**_base()["env_checks"], "email_required": True, "email_configured": False}
    out = build_production_readiness(**_base(env_checks=env))
    assert out["ready_for_paid_public_launch"] is False


def test_no_secret_values_returned():
    out = build_production_readiness(**_base())
    assert "sk_live" not in str(out)


def test_required_check_fields_present():
    out = build_production_readiness(**_base())
    for cat in out["categories"].values():
        for check in cat:
            for field in ("key", "label", "status", "reason", "source", "evidence", "last_seen_at"):
                assert field in check


def test_security_integrations_and_export_checks_present():
    out = build_production_readiness(**_base())
    assert len(out["categories"]["Security"]) > 0
    assert len(out["categories"]["Integrations"]) > 0
    assert any(c["key"] == "export_capability_status" for c in out["categories"]["Evidence & Export"])


def test_includes_paid_launch_readiness_section():
    out = build_production_readiness(**_base())
    assert 'paid_launch_readiness' in out
    for key in ('billing_ready','billing_webhook_ready','email_ready','provider_ready','paid_launch_ready','paid_launch_status','paid_launch_blockers'):
        assert key in out['paid_launch_readiness']
