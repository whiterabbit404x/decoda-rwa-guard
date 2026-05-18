from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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


def build_production_readiness(*, env_checks:dict[str,Any], runtime:dict[str,Any], workflow:dict[str,Any], integrations:dict[str,Any], exports:dict[str,Any], security:dict[str,Any]) -> dict[str, Any]:
    checks: dict[str, list[dict[str, Any]]] = {
        "Platform": [], "Runtime": [], "Workflow": [], "Evidence & Export": [], "Integrations": [], "Security": [],
    }
    blockers:list[str]=[]
    warnings:list[str]=[]

    db_ok = bool(env_checks.get("database_reachable"))
    checks["Platform"].append(_check("database_reachable","Database reachable","pass" if db_ok else "fail", "Database connection is available." if db_ok else "Database connection failed.","database",{"reachable":db_ok}))
    if not db_ok: blockers.append("database_unreachable")

    telemetry_at = runtime.get("last_telemetry_at")
    heartbeat_at = runtime.get("last_heartbeat_at")
    telemetry_ok = bool(telemetry_at)
    checks["Runtime"].append(_check("latest_telemetry","Latest telemetry","pass" if telemetry_ok else "fail", "Telemetry timestamp exists." if telemetry_ok else "Telemetry missing; heartbeat alone is insufficient.", "live" if telemetry_ok else "unavailable", {"has_telemetry":telemetry_ok}, telemetry_at or heartbeat_at))
    if not telemetry_ok: blockers.append("telemetry_missing")

    evidence_source = str(exports.get("evidence_source") or runtime.get("evidence_source") or "unavailable")
    ev_status = "pass" if evidence_source=="live" else ("warn" if evidence_source=="simulator" else "fail")
    checks["Evidence & Export"].append(_check("evidence_source_status","Evidence source",ev_status, "Live evidence available." if ev_status=="pass" else ("Simulator evidence cannot be treated as live readiness." if ev_status=="warn" else "Evidence unavailable."), evidence_source if evidence_source in {"live","simulator"} else "unavailable", {"evidence_source":evidence_source}))
    if ev_status=="warn": warnings.append("simulator_evidence_present")
    if ev_status=="fail": blockers.append("evidence_unavailable")

    billing_required = bool(env_checks.get("billing_required", False))
    billing_configured = bool(env_checks.get("billing_configured", False))
    bill_status = "pass" if billing_configured else ("warn" if not billing_required else "fail")
    checks["Platform"].append(_check("billing_provider","Billing provider configured or disabled",bill_status, "Billing provider configured." if billing_configured else ("Billing intentionally disabled for pilot scope." if not billing_required else "Billing required but not configured."), "config", {"required":billing_required,"configured":billing_configured}))
    if bill_status=="fail": blockers.append("billing_required_not_configured")
    if bill_status=="warn": warnings.append("billing_disabled")

    email_required = bool(env_checks.get("email_required", True))
    email_configured = bool(env_checks.get("email_configured", False))
    email_status = "pass" if email_configured else ("warn" if not email_required else "fail")
    checks["Platform"].append(_check("email_provider","Email provider configured or disabled",email_status, "Email provider configured." if email_configured else ("Email intentionally disabled." if not email_required else "Email provider required but missing."), "config", {"required":email_required,"configured":email_configured}))
    if email_status=="fail": blockers.append("email_required_not_configured")

    checks["Workflow"].append(_check("detection_alert_incident_action_linkage","Detection-alert-incident-action linkage", workflow.get("linkage_status","unavailable"), workflow.get("linkage_reason","Workflow linkage unavailable."), "database", {k:workflow.get(k) for k in ("detections","alerts","incidents","response_actions")}, workflow.get("latest_response_action_at") or workflow.get("latest_incident_at") or workflow.get("latest_alert_at") or workflow.get("latest_detection_at")))
    if workflow.get("linkage_status") != "pass":
        warnings.append("workflow_linkage_incomplete")

    checks["Runtime"].append(_check("monitoring_worker_heartbeat","Monitoring worker heartbeat", "pass" if heartbeat_at else "warn", "Heartbeat observed." if heartbeat_at else "No worker heartbeat observed.", "live" if heartbeat_at else "unavailable", {"last_heartbeat_at":heartbeat_at}, heartbeat_at))

    ready_for_pilot = len(blockers) == 0
    ready_for_paid_public_launch = ready_for_pilot and billing_configured and email_configured and evidence_source == "live"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready_for_pilot": ready_for_pilot,
        "ready_for_paid_public_launch": ready_for_paid_public_launch,
        "blocking_reasons": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "categories": checks,
    }
