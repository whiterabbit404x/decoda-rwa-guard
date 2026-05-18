from services.api.app.production_readiness import build_production_readiness


def _base(**overrides):
    payload = dict(
        env_checks={"database_reachable": True, "billing_required": False, "billing_configured": False, "email_required": True, "email_configured": True},
        runtime={"last_heartbeat_at": "2026-01-01T00:00:00Z", "last_telemetry_at": "2026-01-01T00:01:00Z", "evidence_source": "live"},
        workflow={"detections": 1, "alerts": 1, "incidents": 1, "response_actions": 1, "linkage_status": "pass", "linkage_reason": "ok"},
        integrations={},
        exports={"evidence_source": "live"},
        security={},
    )
    payload.update(overrides)
    return payload


def test_pilot_ready_all_pass():
    out = build_production_readiness(**_base())
    assert out["ready_for_pilot"] is True


def test_missing_telemetry_fails_not_pass():
    out = build_production_readiness(**_base(runtime={"last_heartbeat_at": "2026-01-01T00:00:00Z", "last_telemetry_at": None, "evidence_source": "live"}))
    assert out["ready_for_pilot"] is False
    assert "telemetry_missing" in out["blocking_reasons"]


def test_heartbeat_only_does_not_pass_telemetry():
    out = build_production_readiness(**_base(runtime={"last_heartbeat_at": "2026-01-01T00:00:00Z", "last_telemetry_at": None, "evidence_source": "live"}))
    runtime_checks = out["categories"]["Runtime"]
    assert any(c["key"] == "latest_telemetry" and c["status"] == "fail" for c in runtime_checks)


def test_simulator_is_warn_not_live():
    out = build_production_readiness(**_base(exports={"evidence_source": "simulator"}, runtime={"last_heartbeat_at": "2026-01-01T00:00:00Z", "last_telemetry_at": "2026-01-01T00:01:00Z", "evidence_source": "simulator"}))
    ev = [c for c in out["categories"]["Evidence & Export"] if c["key"] == "evidence_source_status"][0]
    assert ev["status"] == "warn"
    assert ev["source"] == "simulator"


def test_billing_required_missing_fails():
    out = build_production_readiness(**_base(env_checks={"database_reachable": True, "billing_required": True, "billing_configured": False, "email_required": True, "email_configured": True}))
    assert "billing_required_not_configured" in out["blocking_reasons"]


def test_email_missing_fails_when_required():
    out = build_production_readiness(**_base(env_checks={"database_reachable": True, "billing_required": False, "billing_configured": False, "email_required": True, "email_configured": False}))
    assert "email_required_not_configured" in out["blocking_reasons"]


def test_no_secret_values_returned():
    out = build_production_readiness(**_base())
    assert "sk_live" not in str(out)


def test_required_check_fields_present():
    out = build_production_readiness(**_base())
    for cat in out["categories"].values():
        for check in cat:
            for field in ("key", "label", "status", "reason", "source", "evidence", "last_seen_at"):
                assert field in check


def test_database_fail_blocks():
    out = build_production_readiness(**_base(env_checks={"database_reachable": False, "billing_required": False, "billing_configured": False, "email_required": True, "email_configured": True}))
    assert "database_unreachable" in out["blocking_reasons"]
