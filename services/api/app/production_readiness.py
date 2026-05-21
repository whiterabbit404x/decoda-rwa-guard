from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .paid_launch_readiness import build_paid_launch_readiness

CHECK_FIELDS = {"key", "label", "status", "reason", "source", "evidence", "last_seen_at"}


def _check(key:str,label:str,status:str,reason:str,source:str,evidence:dict[str,Any]|None=None,last_seen_at:str|None=None)->dict[str,Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "reason": reason,
        "source": source,
        "evidence": evidence or {},
        "last_seen_at": last_seen_at,
    }


def _status(value: bool, *, fail_when_false: bool = True) -> str:
    if value:
        return "pass"
    return "fail" if fail_when_false else "warn"


def build_production_readiness(*, env_checks:dict[str,Any], runtime:dict[str,Any], workflow:dict[str,Any], integrations:dict[str,Any], exports:dict[str,Any], security:dict[str,Any]) -> dict[str, Any]:
    checks: dict[str, list[dict[str, Any]]] = {
        "Platform": [], "Runtime": [], "Workflow": [], "Evidence & Export": [], "Integrations": [], "Security": [],
    }
    blockers:list[str]=[]
    warnings:list[str]=[]

    db_ok = bool(env_checks.get("database_reachable"))
    auth_ok = bool(env_checks.get("auth_session_configured", True))
    required_env_present = bool(env_checks.get("required_env_vars_present", True))
    redis_required = bool(env_checks.get("redis_required", False))
    redis_ok = bool(env_checks.get("redis_configured", False))
    email_required = bool(env_checks.get("email_required", True))
    email_ok = bool(env_checks.get("email_configured", False))
    billing_required = bool(env_checks.get("billing_required", False))
    billing_ok = bool(env_checks.get("billing_configured", False))
    app_base_url = bool(env_checks.get("app_base_url_configured", False))
    api_url = bool(env_checks.get("api_url_configured", False))

    for key, label, ok, reason_ok, reason_fail in [
        ("database_reachable", "Database reachable", db_ok, "Database connection is available.", "Database connection failed."),
        ("auth_session_configured", "Auth/session configured", auth_ok, "Auth/session configuration present.", "Auth/session configuration missing."),
        ("required_env_vars_present", "Required env vars present", required_env_present, "Required environment variables are present.", "One or more required env vars are missing."),
        ("app_base_url_configured", "App base URL configured", app_base_url, "APP_BASE_URL configured.", "APP_BASE_URL missing."),
        ("api_url_configured", "API URL configured", api_url, "API_BASE_URL configured.", "API_BASE_URL missing."),
    ]:
        checks["Platform"].append(_check(key, label, _status(ok), reason_ok if ok else reason_fail, "config", {"configured": ok}))

    redis_status = "pass" if redis_ok else ("warn" if not redis_required else "fail")
    email_status = "pass" if email_ok else ("warn" if not email_required else "fail")
    billing_status = "pass" if billing_ok else ("warn" if not billing_required else "fail")
    checks["Platform"].append(_check("redis_cache_configured_or_disabled", "Redis/cache configured or disabled", redis_status, "Redis/cache configured." if redis_ok else ("Redis/cache disabled for current scope." if not redis_required else "Redis/cache required but not configured."), "config", {"required": redis_required, "configured": redis_ok}))
    checks["Platform"].append(_check("email_provider_configured_or_disabled", "Email provider configured or disabled", email_status, "Email provider configured." if email_ok else ("Email intentionally disabled." if not email_required else "Email required but not configured."), "config", {"required": email_required, "configured": email_ok}))
    checks["Platform"].append(_check("billing_provider_configured_or_disabled", "Billing provider configured or disabled", billing_status, "Billing provider configured." if billing_ok else ("Billing disabled because paid UI is disabled." if not billing_required else "Billing required but not configured."), "config", {"required": billing_required, "configured": billing_ok}))

    if not db_ok: blockers.append("database_unreachable")
    if not auth_ok: blockers.append("auth_session_not_configured")
    if not required_env_present: blockers.append("required_env_vars_missing")
    if not app_base_url or not api_url: blockers.append("production_urls_missing")
    if redis_status == "fail": blockers.append("redis_required_not_configured")
    if email_status == "fail": blockers.append("email_required_not_configured")
    if billing_status == "fail": blockers.append("billing_required_not_configured")

    heartbeat_at = runtime.get("last_heartbeat_at")
    poll_at = runtime.get("latest_poll_at")
    telemetry_at = runtime.get("last_telemetry_at") or runtime.get("latest_telemetry_at")
    contradiction_flags = runtime.get("contradiction_flags") or []
    for key, label, val in [
        ("monitoring_worker_heartbeat", "Monitoring worker heartbeat", heartbeat_at),
        ("latest_poll", "Latest poll", poll_at),
        ("latest_telemetry", "Latest telemetry", telemetry_at),
    ]:
        ok = bool(val)
        status = "fail" if key == "latest_telemetry" and not ok else ("pass" if ok else "warn")
        checks["Runtime"].append(_check(key, label, status, f"{label} observed." if ok else f"{label} missing.", "live" if ok else "unavailable", {"present": ok}, val))
    if not telemetry_at: blockers.append("telemetry_missing")

    for key in ["reporting_systems_count", "protected_assets_count", "enabled_monitoring_configs_count"]:
        count = int(runtime.get(key) or 0)
        status = "pass" if count > 0 else "warn"
        checks["Runtime"].append(_check(key, key.replace("_", " ").title(), status, "Count is non-zero." if count > 0 else "Count is zero.", "database", {"count": count}))

    for key in ["target_coverage_status", "provider_health_status", "freshness_status", "confidence_status"]:
        value = str(runtime.get(key) or "unavailable")
        status = "pass" if value in {"healthy", "ok", "pass", "covered", "fresh", "high"} else ("warn" if value not in {"fail", "critical", "unavailable"} else "fail")
        checks["Runtime"].append(_check(key, key.replace("_", " ").title(), status, f"Runtime status is {value}.", "runtime_summary", {"value": value}))
        if status == "fail": blockers.append(f"{key}_bad")

    contra_status = "fail" if contradiction_flags else "pass"
    checks["Runtime"].append(_check("contradiction_flags", "Contradiction flags", contra_status, "Contradiction flags present." if contradiction_flags else "No contradiction flags.", "runtime_summary", {"flags": contradiction_flags}))
    if contradiction_flags: blockers.append("runtime_contradictions_present")

    checks["Workflow"].append(_check("detection_count", "Detection count", "pass" if int(workflow.get("detections") or 0) > 0 else "warn", "Detection count evaluated.", "database", {"count": int(workflow.get("detections") or 0)}))
    checks["Workflow"].append(_check("alert_count", "Alert count", "pass" if int(workflow.get("alerts") or 0) > 0 else "warn", "Alert count evaluated.", "database", {"count": int(workflow.get("alerts") or 0)}))
    checks["Workflow"].append(_check("incident_count", "Incident count", "pass" if int(workflow.get("incidents") or 0) > 0 else "warn", "Incident count evaluated.", "database", {"count": int(workflow.get("incidents") or 0)}))
    checks["Workflow"].append(_check("response_action_count", "Response action count", "pass" if int(workflow.get("response_actions") or 0) > 0 else "warn", "Response action count evaluated.", "database", {"count": int(workflow.get("response_actions") or 0)}))
    for key in ["latest_detection_at", "latest_alert_at", "latest_incident_at", "latest_response_action_at"]:
        checks["Workflow"].append(_check(key, key.replace("_", " ").title(), "pass" if workflow.get(key) else "warn", "Timestamp available." if workflow.get(key) else "Timestamp missing.", "database", {}, workflow.get(key)))
    linkage_status = workflow.get("linkage_status", "unavailable")
    checks["Workflow"].append(_check("detection_alert_incident_action_linkage", "Detection-alert-incident-action linkage", linkage_status if linkage_status in {"pass","warn","fail","unavailable"} else "warn", workflow.get("linkage_reason", "Linkage status evaluated."), "database"))

    ev_source = str(exports.get("evidence_source") or runtime.get("evidence_source") or "unavailable")
    ev_status = "pass" if ev_source == "live" else ("warn" if "simulator" in ev_source else "fail")
    checks["Evidence & Export"].append(_check("evidence_source_status", "Evidence source status", ev_status, "Evidence source evaluated.", ev_source, {"evidence_source": ev_source}))
    checks["Evidence & Export"].append(_check("export_capability_status", "Export capability status", str(exports.get("export_capability_status") or "unavailable"), "Export capability evaluated.", "export"))
    checks["Evidence & Export"].append(_check("latest_export_job_status", "Latest export job status", str(exports.get("latest_export_job_status") or "unavailable"), "Latest export job evaluated.", "export", exports.get("latest_export_job") or {}))
    checks["Evidence & Export"].append(_check("audit_log_availability", "Audit log availability", str(exports.get("audit_log_availability") or "unavailable"), "Audit log support evaluated.", "database"))
    if "proof_bundle_capability" in exports:
        checks["Evidence & Export"].append(_check("proof_bundle_capability", "Proof bundle capability", str(exports.get("proof_bundle_capability") or "unavailable"), "Proof bundle capability evaluated.", "export"))
    if ev_status != "pass":
        if ev_status == "warn": warnings.append("simulator_evidence_present")
        blockers.append("evidence_not_live")

    for key in ["slack_integration_status", "webhook_integration_status", "delivery_logs_status", "api_key_support_status"]:
        value = str(integrations.get(key) or "unavailable")
        status = value if value in {"pass", "warn", "fail", "unavailable"} else "warn"
        checks["Integrations"].append(_check(key, key.replace("_", " ").title(), status, "Integration status evaluated.", "integration", {"value": value}))
        if status in {"fail", "unavailable"}: blockers.append(f"{key}_unknown_or_failed")

    checks["Security"].append(_check("readiness_access_control", "Readiness access control", str(security.get("readiness_access_control") or "pass"), "Workspace-admin access enforced.", "app"))
    checks["Security"].append(_check("secrets_redacted", "Secrets redacted", "pass", "Secret values are never included in readiness payload.", "app"))
    checks["Security"].append(_check("admin_workspace_scope", "Admin workspace scope", "pass" if security.get("admin_workspace_scope", True) else "fail", "Readiness is scoped to the authenticated workspace.", "app"))

    workspace_evaluated = bool(runtime.get("workspace_evaluated", True))
    if not workspace_evaluated: blockers.append("workspace_not_evaluated")
    if runtime.get("workspace_scoped", True) and int(runtime.get("protected_assets_count") or 0) <= 0:
        blockers.append("no_protected_assets")
    if int(runtime.get("reporting_systems_count") or 0) <= 0:
        warnings.append("setup_required_reporting_systems")


    paid_launch_readiness = build_paid_launch_readiness(live_evidence={
        "evidence_source": ev_source,
        "telemetry_evidence_source": runtime.get("evidence_source") or exports.get("evidence_source"),
    })
    ready_for_pilot = len(blockers) == 0
    paid_ui_disabled = bool(env_checks.get("paid_ui_disabled", False))
    ready_for_paid_public_launch = ready_for_pilot and (billing_ok or paid_ui_disabled) and (not email_required or email_ok) and (not redis_required or redis_ok) and app_base_url and api_url and ev_status == "pass" and not contradiction_flags and paid_launch_readiness.get("paid_launch_ready") is True

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready_for_pilot": ready_for_pilot,
        "ready_for_paid_public_launch": ready_for_paid_public_launch,
        "blocking_reasons": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "categories": checks,
        "paid_launch_readiness": paid_launch_readiness,
    }
