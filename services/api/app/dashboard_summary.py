"""Dashboard / Executive Summary aggregation, persistence and response contract.

This orchestration layer sits between the raw workspace data and Screen 2. It:

  * gathers **workspace-scoped** aggregates (reusing the canonical monitoring
    summary as the source of truth for counts, plus a few defensive extra
    queries for the severity breakdown, recent alerts, data-source count and
    trend history),
  * feeds those aggregates into the deterministic
    :mod:`services.api.app.dashboard_scoring` engine,
  * gets-or-creates the idempotent Executive Brief
    (:mod:`services.api.app.dashboard_executive_brief`),
  * persists a periodic snapshot for the seven-day trend and deltas,
  * assembles the JSON response contract the frontend consumes.

Every DB read is wrapped so a missing optional table/column degrades to a safe
default instead of failing the whole dashboard — the same fail-open-for-reads,
fail-closed-for-claims posture used elsewhere in the app. Score/brief helpers
are pure so they are unit-tested without a database.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from services.api.app.dashboard_scoring import (
    AlertCluster,
    HealthInputs,
    RiskInputs,
    compute_health_score,
    compute_risk_score,
    provider_degradation_factor,
)
from services.api.app.dashboard_executive_brief import (
    BRIEF_PROMPT_VERSION,
    brief_idempotency_key,
    build_deterministic_brief,
    generate_executive_brief,
)

# Telemetry older than this many seconds is "stale". Matches the monitoring
# default polling/telemetry window used across the app.
TELEMETRY_WINDOW_SECONDS = 900
SNAPSHOT_MIN_INTERVAL_SECONDS = 300  # never persist more than one snapshot / 5 min
TREND_DAYS = 7

_ACTIVE_ALERT_STATUSES = ('open', 'acknowledged', 'investigating')
_ACTIVE_INCIDENT_STATUSES = ('open', 'acknowledged')


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_fetchone(connection: Any, sql: str, params: tuple) -> dict[str, Any]:
    """Run a scoped read, returning {} on any failure (optional-table tolerant)."""
    try:
        row = connection.execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _safe_fetchall(connection: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(sql, params).fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception:
        return []


# --------------------------------------------------------------------------
# Aggregate gathering (workspace-scoped DB reads)
# --------------------------------------------------------------------------


def gather_dashboard_aggregates(
    connection: Any,
    *,
    workspace_id: str,
    now: datetime,
    canonical_summary: dict[str, Any] | None,
    background_loop_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect all workspace-scoped facts Screen 2 needs.

    ``canonical_summary`` is the authoritative monitoring runtime summary; its
    counts/freshness are trusted and never re-derived. The extra queries here
    only add the breakdown/trend data the summary does not carry. Every value is
    scoped by ``workspace_id`` — no cross-tenant aggregation.
    """
    summary = canonical_summary or {}
    wp = (workspace_id,)

    # --- Counts trusted from the canonical summary -------------------------
    active_alert_count = _int(summary.get('active_alerts_count'))
    open_incident_count = _int(summary.get('active_incidents_count'))
    monitored_asset_count = _int(summary.get('protected_assets_count'))
    configured_systems = _int(summary.get('configured_systems') or summary.get('monitored_systems_count'))
    reporting_systems = _int(summary.get('reporting_systems_count'))
    telemetry_freshness = str(summary.get('telemetry_freshness') or 'unavailable')
    last_telemetry_at = _parse_dt(summary.get('last_telemetry_at'))
    last_heartbeat_at = _parse_dt(summary.get('last_heartbeat_at'))

    # --- Alert severity breakdown (active only) ----------------------------
    alert_rows = _safe_fetchall(
        connection,
        f"SELECT severity, COUNT(*) AS c FROM alerts "
        f"WHERE workspace_id = %s AND status IN {_ACTIVE_ALERT_STATUSES} "
        f"GROUP BY severity",
        wp,
    )
    alert_severity_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
    for row in alert_rows:
        sev = str(row.get('severity') or 'medium').lower()
        if sev in alert_severity_counts:
            alert_severity_counts[sev] += _int(row.get('c'))

    # --- Recent alerts (five most important) -------------------------------
    recent_alert_rows = _safe_fetchall(
        connection,
        f"SELECT id, title, severity, status, alert_type, created_at "
        f"FROM alerts WHERE workspace_id = %s AND status IN {_ACTIVE_ALERT_STATUSES} "
        f"ORDER BY "
        f"CASE lower(severity) WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
        f"created_at DESC LIMIT 5",
        wp,
    )
    recent_alerts = [
        {
            'id': str(r.get('id')),
            'title': str(r.get('title') or r.get('alert_type') or 'Alert'),
            'severity': str(r.get('severity') or 'medium').lower(),
            'status': str(r.get('status') or 'open').lower(),
            'asset': str(r.get('alert_type') or ''),
            'occurred_at': _iso(_parse_dt(r.get('created_at'))),
            'url': f"/alerts/{r.get('id')}",
        }
        for r in recent_alert_rows
    ]

    # --- Active incidents by severity + 24h opened/resolved ----------------
    incident_rows = _safe_fetchall(
        connection,
        f"SELECT severity FROM incidents WHERE workspace_id = %s AND status IN {_ACTIVE_INCIDENT_STATUSES}",
        wp,
    )
    incident_severities = [str(r.get('severity') or 'medium').lower() for r in incident_rows]
    incidents_critical_high = sum(1 for s in incident_severities if s in {'critical', 'high'})
    since_24h = now - timedelta(hours=24)
    opened_row = _safe_fetchone(
        connection,
        "SELECT COUNT(*) AS c FROM incidents WHERE workspace_id = %s AND created_at >= %s",
        (workspace_id, since_24h),
    )
    incidents_opened_24h = _int(opened_row.get('c'))
    resolved_row = _safe_fetchone(
        connection,
        "SELECT COUNT(*) AS c FROM incidents WHERE workspace_id = %s AND status = 'resolved' "
        "AND created_at >= %s",
        (workspace_id, since_24h),
    )
    incidents_resolved_24h = _int(resolved_row.get('c'))

    # --- Active monitors + data sources ------------------------------------
    monitor_row = _safe_fetchone(
        connection,
        "SELECT COUNT(*) AS c FROM monitored_systems WHERE workspace_id = %s AND status = 'active'",
        wp,
    )
    active_monitor_count = _int(monitor_row.get('c')) or reporting_systems
    source_row = _safe_fetchone(
        connection,
        "SELECT COUNT(DISTINCT chain_network) AS c FROM targets WHERE workspace_id = %s",
        wp,
    )
    data_source_count = _int(source_row.get('c'))

    # --- Asset criticality of affected assets ------------------------------
    affected_asset_criticalities = _affected_asset_criticalities(connection, workspace_id)

    # --- Anomaly volume (detections in 24h) --------------------------------
    anomaly_row = _safe_fetchone(
        connection,
        "SELECT COUNT(*) AS c FROM detections WHERE workspace_id = %s AND created_at >= %s",
        (workspace_id, since_24h),
    )
    anomaly_count_24h = _int(anomaly_row.get('c'))
    prev_anomaly_row = _safe_fetchone(
        connection,
        "SELECT COUNT(*) AS c FROM detections WHERE workspace_id = %s AND created_at >= %s AND created_at < %s",
        (workspace_id, now - timedelta(hours=48), since_24h),
    )
    anomaly_prev_24h = _int(prev_anomaly_row.get('c'))

    # --- Telemetry volume (24h vs prev 24h) --------------------------------
    telemetry_events_24h = _telemetry_count(connection, workspace_id, since_24h, now)
    telemetry_events_prev_24h = _telemetry_count(connection, workspace_id, now - timedelta(hours=48), since_24h)

    # --- Provider / worker / target health (fallback-aware) ----------------
    stale_target_count = max(configured_systems - reporting_systems, 0) if telemetry_freshness != 'fresh' else 0
    providers = _derive_providers(summary)
    required_worker_count, healthy_worker_count, missing_worker_refs = _derive_workers(
        summary, background_loop_health, last_heartbeat_at, now,
    )
    degraded_provider_count = sum(
        1 for p in providers if not p.get('primary_healthy', True) or p.get('rate_limited', False)
    )
    infra_components = _derive_infra(summary)
    worker_failure_count = max(required_worker_count - healthy_worker_count, 0)

    degradation_factor = provider_degradation_factor(
        providers=providers,
        configured_target_count=configured_systems,
        stale_target_count=stale_target_count,
    )

    # --- Citations (workspace-scoped source refs the brief may cite) -------
    citations = _build_candidate_citations(
        recent_alerts=recent_alerts,
        incident_rows=incident_rows,
        stale_target_count=stale_target_count,
    )

    metrics = {
        # Truthful: no valuation source exists, so value is unavailable (null),
        # which the UI renders as "Not available" — never $0.
        'total_asset_value_usd': None,
        'monitored_asset_count': monitored_asset_count,
        'active_monitor_count': active_monitor_count,
        'data_source_count': data_source_count,
        'open_incident_count': open_incident_count,
        'active_alert_count': active_alert_count,
        'uptime_30d_percent': _uptime_30d(background_loop_health),
    }

    return {
        'period_start': _iso(since_24h),
        'period_end': _iso(now),
        'metrics': metrics,
        'alert_severity_counts': alert_severity_counts,
        'alert_cluster_count': _count_alert_clusters(alert_severity_counts, active_alert_count),
        'incident_severities': incident_severities,
        'incidents_opened_24h': incidents_opened_24h,
        'incidents_resolved_24h': incidents_resolved_24h,
        'incidents_critical_high': incidents_critical_high,
        'affected_asset_criticalities': affected_asset_criticalities,
        'anomaly_count_24h': anomaly_count_24h,
        'anomaly_rate_current': anomaly_count_24h,
        'anomaly_rate_baseline': anomaly_prev_24h,
        'telemetry_events_24h': telemetry_events_24h,
        'telemetry_events_prev_24h': telemetry_events_prev_24h,
        'telemetry_freshness': telemetry_freshness,
        'last_telemetry_at': _iso(last_telemetry_at),
        'degraded_provider_count': degraded_provider_count,
        'stale_target_count': stale_target_count,
        'stale_target_refs': [],
        'worker_failure_count': worker_failure_count,
        'providers': providers,
        'required_worker_count': required_worker_count,
        'healthy_worker_count': healthy_worker_count,
        'missing_worker_refs': missing_worker_refs,
        'infra_components': infra_components,
        'configured_target_count': configured_systems,
        'reporting_target_count': reporting_systems,
        'critical_incident_count': sum(1 for s in incident_severities if s == 'critical'),
        'critical_alert_count': alert_severity_counts.get('critical', 0),
        'monitoring_degradation_factor': degradation_factor,
        'pending_control_gap_count': len(summary.get('contradiction_flags') or []),
        'recent_alerts': recent_alerts,
        'citations': citations,
        'top_anomalies': [],
        'detection_freshness_known': last_telemetry_at is not None,
        'detection_fresh': telemetry_freshness == 'fresh',
    }


def _telemetry_count(connection: Any, workspace_id: str, start: datetime, end: datetime) -> int:
    row = _safe_fetchone(
        connection,
        "SELECT COUNT(*) AS c FROM telemetry_events WHERE workspace_id = %s "
        "AND observed_at >= %s AND observed_at < %s",
        (workspace_id, start, end),
    )
    return _int(row.get('c'))


def _affected_asset_criticalities(connection: Any, workspace_id: str) -> list[str]:
    """Risk tiers of assets tied to active alerts. Defensive: empty on failure."""
    rows = _safe_fetchall(
        connection,
        f"SELECT DISTINCT a.risk_tier AS risk_tier FROM assets a "
        f"WHERE a.workspace_id = %s AND a.deleted_at IS NULL "
        f"AND EXISTS (SELECT 1 FROM alerts al WHERE al.workspace_id = a.workspace_id "
        f"AND al.status IN {_ACTIVE_ALERT_STATUSES})",
        (workspace_id,),
    )
    return [str(r.get('risk_tier') or 'medium').lower() for r in rows]


def _count_alert_clusters(severity_counts: dict[str, int], total: int) -> int:
    return sum(1 for v in severity_counts.values() if v > 0) or (1 if total > 0 else 0)


def _derive_providers(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive provider health from canonical evidence facts (no secrets, no URLs).

    A single logical provider entry is synthesized from the summary's evidence
    source + contradiction flags. When live evidence is fresh the primary is
    healthy; a degraded/limited runtime marks the primary unhealthy while
    treating any still-flowing telemetry as an active fallback (so the health
    scorer classifies it degraded, not critical).
    """
    evidence = str(summary.get('evidence_source_summary') or '')
    freshness = str(summary.get('telemetry_freshness') or 'unavailable')
    runtime = str(summary.get('runtime_status') or '')
    if evidence in {'live_provider', 'live'} and freshness == 'fresh':
        return [{'name': 'primary_provider', 'primary_healthy': True, 'fallback_healthy': True, 'rate_limited': False}]
    if runtime == 'offline' or freshness == 'unavailable':
        return [{'name': 'primary_provider', 'primary_healthy': False, 'fallback_healthy': False, 'rate_limited': False}]
    # Degraded: primary impaired but telemetry still arriving (fallback active).
    return [{'name': 'primary_provider', 'primary_healthy': False, 'fallback_healthy': freshness != 'unavailable', 'rate_limited': False}]


def _derive_workers(
    summary: dict[str, Any],
    background_loop_health: dict[str, Any] | None,
    last_heartbeat_at: datetime | None,
    now: datetime,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Required vs healthy worker heartbeat counts, defensively derived."""
    health = background_loop_health or {}
    # Preferred: explicit loop-health signal.
    if isinstance(health, dict) and health:
        healthy_flag = health.get('healthy')
        if isinstance(healthy_flag, bool):
            required = 1
            healthy = 1 if healthy_flag else 0
            missing = [] if healthy_flag else [{'id': 'monitoring_worker', 'label': 'monitoring worker'}]
            return required, healthy, missing
    # Fallback: heartbeat freshness proves the worker is alive.
    if last_heartbeat_at is not None:
        fresh = (now - last_heartbeat_at).total_seconds() <= TELEMETRY_WINDOW_SECONDS
        return 1, (1 if fresh else 0), ([] if fresh else [{'id': 'monitoring_worker', 'label': 'monitoring worker'}])
    # No workers required (unconfigured workspace) — neutral.
    if _int(summary.get('configured_systems')) <= 0:
        return 0, 0, []
    return 1, 0, [{'id': 'monitoring_worker', 'label': 'monitoring worker'}]


def _derive_infra(summary: dict[str, Any]) -> list[dict[str, Any]]:
    db_healthy = summary.get('db_failure_classification') in (None, '', 'none')
    return [{'name': 'database', 'healthy': bool(db_healthy)}]


def _uptime_30d(background_loop_health: dict[str, Any] | None) -> float | None:
    health = background_loop_health or {}
    for key in ('uptime_30d_percent', 'uptime_percent', 'uptime_ratio'):
        if key in health and health[key] is not None:
            try:
                value = float(health[key])
                return value * 100 if value <= 1 else value
            except (TypeError, ValueError):
                continue
    return None


def _build_candidate_citations(
    *,
    recent_alerts: list[dict[str, Any]],
    incident_rows: list[dict[str, Any]],
    stale_target_count: int,
) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for alert in recent_alerts:
        citations.append({
            'source_type': 'alert',
            'source_id': alert['id'],
            'label': alert['title'],
            'occurred_at': alert.get('occurred_at'),
            'url': alert['url'],
        })
    for row in incident_rows:
        if row.get('id'):
            citations.append({
                'source_type': 'incident',
                'source_id': str(row.get('id')),
                'label': f"{row.get('severity', 'incident')} incident",
                'occurred_at': _iso(_parse_dt(row.get('created_at'))),
                'url': f"/incidents/{row.get('id')}",
            })
    return citations


# --------------------------------------------------------------------------
# Score input mapping (pure)
# --------------------------------------------------------------------------


def risk_inputs_from_aggregates(agg: dict[str, Any]) -> RiskInputs:
    clusters: list[AlertCluster] = []
    for severity, count in (agg.get('alert_severity_counts') or {}).items():
        if _int(count) > 0:
            clusters.append(AlertCluster(severity=severity, count=_int(count), key=f'severity:{severity}'))
    return RiskInputs(
        incident_severities=list(agg.get('incident_severities') or []),
        alert_clusters=clusters,
        alert_severity_counts=dict(agg.get('alert_severity_counts') or {}),
        anomaly_rate_current=float(agg.get('anomaly_rate_current') or 0),
        anomaly_rate_baseline=float(agg.get('anomaly_rate_baseline') or 0),
        anomaly_count_24h=_int(agg.get('anomaly_count_24h')),
        affected_asset_criticalities=list(agg.get('affected_asset_criticalities') or []),
        monitoring_degradation_factor=float(agg.get('monitoring_degradation_factor') or 0),
        pending_control_gap_count=_int(agg.get('pending_control_gap_count')),
    )


def health_inputs_from_aggregates(agg: dict[str, Any]) -> HealthInputs:
    return HealthInputs(
        configured_target_count=_int(agg.get('configured_target_count')),
        reporting_target_count=_int(agg.get('reporting_target_count')),
        stale_target_count=_int(agg.get('stale_target_count')),
        telemetry_freshness=str(agg.get('telemetry_freshness') or 'unavailable'),
        stale_target_refs=list(agg.get('stale_target_refs') or []),
        required_worker_count=_int(agg.get('required_worker_count')),
        healthy_worker_count=_int(agg.get('healthy_worker_count')),
        missing_worker_refs=list(agg.get('missing_worker_refs') or []),
        providers=list(agg.get('providers') or []),
        detection_fresh=bool(agg.get('detection_fresh', True)),
        detection_freshness_known=bool(agg.get('detection_freshness_known', True)),
        infra_components=list(agg.get('infra_components') or []),
        critical_incident_count=_int(agg.get('critical_incident_count')),
        critical_alert_count=_int(agg.get('critical_alert_count')),
    )


# --------------------------------------------------------------------------
# Snapshots: trend, deltas, persistence
# --------------------------------------------------------------------------


def list_recent_snapshots(connection: Any, workspace_id: str, *, days: int = TREND_DAYS, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    return _safe_fetchall(
        connection,
        "SELECT captured_at, risk_score, risk_band, health_score, health_status, "
        "active_alert_count, open_incident_count, monitored_asset_count "
        "FROM dashboard_snapshots WHERE workspace_id = %s AND captured_at >= %s "
        "ORDER BY captured_at ASC",
        (workspace_id, since),
    )


def latest_snapshot(connection: Any, workspace_id: str) -> dict[str, Any]:
    return _safe_fetchone(
        connection,
        "SELECT captured_at, risk_score, risk_band, health_score, health_status, "
        "active_alert_count, open_incident_count, monitored_asset_count "
        "FROM dashboard_snapshots WHERE workspace_id = %s ORDER BY captured_at DESC LIMIT 1",
        (workspace_id,),
    )


def build_risk_trend(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real snapshot history only — never synthesize zero-value days as data."""
    trend: list[dict[str, Any]] = []
    for snap in snapshots:
        captured = _parse_dt(snap.get('captured_at'))
        trend.append({
            'captured_at': _iso(captured),
            'risk_score': _int(snap.get('risk_score')),
            'health_score': _int(snap.get('health_score')),
            'active_alert_count': _int(snap.get('active_alert_count')),
            'open_incident_count': _int(snap.get('open_incident_count')),
        })
    return trend


def compute_deltas(metrics: dict[str, Any], risk: dict[str, Any], health: dict[str, Any], prev: dict[str, Any]) -> dict[str, Any]:
    """Snapshot-over-snapshot deltas. None when no prior snapshot exists."""
    if not prev:
        return {
            'risk_score': None,
            'system_health_score': None,
            'active_alert_count': None,
            'open_incident_count': None,
        }
    return {
        'risk_score': _int(risk.get('score')) - _int(prev.get('risk_score')),
        'system_health_score': _int(health.get('score')) - _int(prev.get('health_score')),
        'active_alert_count': _int(metrics.get('active_alert_count')) - _int(prev.get('active_alert_count')),
        'open_incident_count': _int(metrics.get('open_incident_count')) - _int(prev.get('open_incident_count')),
    }


def persist_dashboard_snapshot(
    connection: Any,
    *,
    workspace_id: str,
    response: dict[str, Any],
    now: datetime,
    prev_snapshot: dict[str, Any] | None = None,
    min_interval_seconds: int = SNAPSHOT_MIN_INTERVAL_SECONDS,
) -> bool:
    """Persist a snapshot unless one was captured within ``min_interval_seconds``.

    Returns True if a row was written. Never one snapshot per page request:
    throttled by the last capture time.
    """
    if prev_snapshot:
        last_captured = _parse_dt(prev_snapshot.get('captured_at'))
        if last_captured and (now - last_captured).total_seconds() < min_interval_seconds:
            return False
    metrics = response.get('metrics', {})
    risk = response.get('_risk_components', [])
    health = response.get('_health_components', [])
    import json as _json
    try:
        connection.execute(
            "INSERT INTO dashboard_snapshots (id, workspace_id, captured_at, risk_score, risk_band, "
            "health_score, health_status, active_alert_count, open_incident_count, monitored_asset_count, "
            "active_monitor_count, data_source_count, uptime_30d_percent, total_asset_value_usd, "
            "risk_components, health_components) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(uuid.uuid4()), workspace_id, now,
                _int(metrics.get('risk_score')), str(metrics.get('risk_band') or 'low'),
                _int(metrics.get('system_health_score')), str(metrics.get('system_health_status') or 'not_configured'),
                _int(metrics.get('active_alert_count')), _int(metrics.get('open_incident_count')),
                _int(metrics.get('monitored_asset_count')), _int(metrics.get('active_monitor_count')),
                _int(metrics.get('data_source_count')), metrics.get('uptime_30d_percent'),
                metrics.get('total_asset_value_usd'),
                _json.dumps(risk), _json.dumps(health),
            ),
        )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Executive brief: idempotent get-or-create
# --------------------------------------------------------------------------


def get_or_create_executive_brief(
    connection: Any,
    *,
    workspace_id: str,
    aggregates: dict[str, Any],
    provider: Any,
    now: datetime,
    model: str = '',
    prompt_version: str = BRIEF_PROMPT_VERSION,
    logger: Any = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Return today's brief, generating and storing it once if absent.

    Idempotent on (workspace_id, reporting_date, brief_version, prompt_version):
    a second call the same day returns the stored row without re-calling the
    model. If persistence is unavailable the brief is still generated in-memory
    so the dashboard renders.
    """
    reporting_date = now.date().isoformat()
    key = brief_idempotency_key(workspace_id, reporting_date, prompt_version)

    existing = _safe_fetchone(
        connection,
        "SELECT headline, summary, key_findings, recommended_focus, citations, confidence, "
        "generation_mode, provider, model, prompt_version, created_at "
        "FROM dashboard_executive_briefs WHERE workspace_id = %s AND idempotency_key = %s",
        (workspace_id, key),
    )
    if existing:
        return _row_to_brief(existing, aggregates)

    if provider is None:
        brief = build_deterministic_brief(aggregates)
    else:
        brief = generate_executive_brief(
            aggregates=aggregates, provider=provider, model=model,
            prompt_version=prompt_version, logger=logger,
        )
    brief['generated_at'] = _iso(now)
    brief['period_start'] = aggregates.get('period_start')
    brief['period_end'] = aggregates.get('period_end')

    if persist:
        _persist_brief(connection, workspace_id=workspace_id, key=key, reporting_date=reporting_date, brief=brief, aggregates=aggregates, now=now)
    return brief


def _persist_brief(connection: Any, *, workspace_id: str, key: str, reporting_date: str, brief: dict[str, Any], aggregates: dict[str, Any], now: datetime) -> None:
    import json as _json
    try:
        connection.execute(
            "INSERT INTO dashboard_executive_briefs (id, workspace_id, idempotency_key, reporting_date, "
            "period_start, period_end, headline, summary, key_findings, recommended_focus, citations, "
            "confidence, generation_mode, provider, model, prompt_version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (workspace_id, idempotency_key) DO NOTHING",
            (
                str(uuid.uuid4()), workspace_id, key, reporting_date,
                _parse_dt(aggregates.get('period_start')), _parse_dt(aggregates.get('period_end')),
                brief.get('headline', ''), brief.get('summary', ''),
                _json.dumps(brief.get('key_findings', [])), _json.dumps(brief.get('recommended_focus', [])),
                _json.dumps(brief.get('citations', [])), float(brief.get('confidence') or 0),
                brief.get('generation_mode', 'deterministic_fallback'),
                brief.get('provider'), brief.get('model'), brief.get('prompt_version'),
            ),
        )
    except Exception:
        pass


def _row_to_brief(row: dict[str, Any], aggregates: dict[str, Any]) -> dict[str, Any]:
    def _load(value: Any) -> Any:
        if isinstance(value, (list, dict)):
            return value
        if isinstance(value, str) and value:
            import json as _json
            try:
                return _json.loads(value)
            except ValueError:
                return []
        return []

    return {
        'headline': row.get('headline', ''),
        'summary': row.get('summary', ''),
        'key_findings': _load(row.get('key_findings')),
        'recommended_focus': _load(row.get('recommended_focus')),
        'citations': _load(row.get('citations')),
        'confidence': float(row.get('confidence') or 0),
        'generation_mode': row.get('generation_mode', 'deterministic_fallback'),
        'provider': row.get('provider'),
        'model': row.get('model'),
        'prompt_version': row.get('prompt_version'),
        'generated_at': _iso(_parse_dt(row.get('created_at'))),
        'period_start': aggregates.get('period_start'),
        'period_end': aggregates.get('period_end'),
    }


# --------------------------------------------------------------------------
# Response contract assembly (pure)
# --------------------------------------------------------------------------


def derive_data_freshness(last_telemetry_at: str | None, now: datetime, window_seconds: int = TELEMETRY_WINDOW_SECONDS) -> dict[str, Any]:
    parsed = _parse_dt(last_telemetry_at)
    if parsed is None:
        return {'status': 'unavailable', 'latest_event_at': None, 'age_seconds': None}
    age = int((now - parsed).total_seconds())
    status = 'fresh' if age <= window_seconds else 'stale'
    return {'status': status, 'latest_event_at': _iso(parsed), 'age_seconds': max(age, 0)}


def build_executive_summary_response(
    *,
    aggregates: dict[str, Any],
    risk: dict[str, Any],
    health: dict[str, Any],
    brief: dict[str, Any],
    trend: list[dict[str, Any]],
    deltas: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Assemble the full Screen 2 response contract from computed parts."""
    metrics_in = aggregates.get('metrics', {})
    metrics = {
        'total_asset_value_usd': metrics_in.get('total_asset_value_usd'),
        'monitored_asset_count': _int(metrics_in.get('monitored_asset_count')),
        'active_monitor_count': _int(metrics_in.get('active_monitor_count')),
        'data_source_count': _int(metrics_in.get('data_source_count')),
        'open_incident_count': _int(metrics_in.get('open_incident_count')),
        'active_alert_count': _int(metrics_in.get('active_alert_count')),
        'risk_score': _int(risk.get('score')),
        'risk_band': risk.get('band', 'low'),
        'system_health_score': _int(health.get('score')),
        'system_health_status': health.get('status', 'not_configured'),
        'uptime_30d_percent': metrics_in.get('uptime_30d_percent'),
        'critical_or_high_incident_count': _int(aggregates.get('incidents_critical_high')),
        'deltas': deltas,
    }
    return {
        'generated_at': _iso(now),
        'data_freshness': derive_data_freshness(aggregates.get('last_telemetry_at'), now),
        'executive_brief': {
            'period_start': aggregates.get('period_start'),
            'period_end': aggregates.get('period_end'),
            'headline': brief.get('headline', ''),
            'summary': brief.get('summary', ''),
            'key_findings': brief.get('key_findings', []),
            'recommended_focus': brief.get('recommended_focus', []),
            'confidence': brief.get('confidence', 0),
            'generation_mode': brief.get('generation_mode', 'deterministic_fallback'),
            'generated_at': brief.get('generated_at'),
            'provider': brief.get('provider'),
            'model': brief.get('model'),
            'prompt_version': brief.get('prompt_version'),
            'citations': brief.get('citations', []),
        },
        'metrics': metrics,
        'risk_trend': trend,
        'trend_available': len(trend) > 0,
        'recent_alerts': aggregates.get('recent_alerts', []),
        'ai_copilot': {
            'generated_at': brief.get('generated_at'),
            'top_risk_drivers': risk.get('top_risk_drivers', []),
            'system_health_insights': health.get('insights', []),
            'recommended_focus': brief.get('recommended_focus', []),
            'generation_mode': brief.get('generation_mode', 'deterministic_fallback'),
        },
        # Private keys (leading underscore) consumed by snapshot persistence only.
        '_risk_components': risk.get('components', []),
        '_health_components': health.get('components', []),
    }


# --------------------------------------------------------------------------
# Best-effort Redis response cache (env-gated, fully guarded)
# --------------------------------------------------------------------------
#
# Bounds recomputation on rapid refreshes and doubles as a soft manual-refresh
# limiter. Disabled by default (TTL 0) so behavior stays deterministic; enable
# by setting DASHBOARD_CACHE_TTL_SECONDS>0 with a configured REDIS_URL. The
# expensive work (daily brief, throttled snapshots) is already idempotent, so a
# short TTL is a safe approximation of "invalidate when data changes"; the
# frontend's SSE feed still drives immediate client-side refresh on real events.


def _cache_ttl_seconds() -> int:
    try:
        return int(os.getenv('DASHBOARD_CACHE_TTL_SECONDS', '0').strip() or '0')
    except ValueError:
        return 0


def _cache_client() -> Any | None:
    if _cache_ttl_seconds() <= 0:
        return None
    url = os.getenv('REDIS_URL', '').strip()
    if not url:
        return None
    try:
        import redis  # reuse the same client library as alert streaming
        return redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
    except Exception:
        return None


def _cache_key(workspace_id: str) -> str:
    return f'dashboard:executive-summary:{workspace_id}'


def dashboard_cache_get(workspace_id: str) -> dict[str, Any] | None:
    client = _cache_client()
    if client is None:
        return None
    try:
        raw = client.get(_cache_key(workspace_id))
        if raw:
            return json.loads(raw)
    except Exception:
        return None
    return None


def dashboard_cache_set(workspace_id: str, payload: dict[str, Any]) -> None:
    client = _cache_client()
    if client is None:
        return
    try:
        client.set(_cache_key(workspace_id), json.dumps(payload, default=str), ex=_cache_ttl_seconds())
    except Exception:
        pass


def build_dashboard_summary(
    connection: Any,
    *,
    workspace_id: str,
    canonical_summary: dict[str, Any] | None,
    background_loop_health: dict[str, Any] | None = None,
    provider: Any = None,
    now: datetime | None = None,
    model: str = '',
    logger: Any = None,
    persist: bool = True,
) -> dict[str, Any]:
    """End-to-end: gather -> score -> brief -> trend/deltas -> assemble -> persist.

    This is the single entry point the endpoint calls. Everything it touches is
    scoped to ``workspace_id``.
    """
    now = now or datetime.now(timezone.utc)
    aggregates = gather_dashboard_aggregates(
        connection, workspace_id=workspace_id, now=now,
        canonical_summary=canonical_summary, background_loop_health=background_loop_health,
    )
    risk = compute_risk_score(risk_inputs_from_aggregates(aggregates)).to_dict()
    health = compute_health_score(health_inputs_from_aggregates(aggregates)).to_dict()
    aggregates['risk'] = risk
    aggregates['health'] = health

    prev = latest_snapshot(connection, workspace_id)
    snapshots = list_recent_snapshots(connection, workspace_id, now=now)
    trend = build_risk_trend(snapshots)
    deltas = compute_deltas(aggregates['metrics'], risk, health, prev)

    brief = get_or_create_executive_brief(
        connection, workspace_id=workspace_id, aggregates=aggregates,
        provider=provider, now=now, model=model, logger=logger, persist=persist,
    )

    response = build_executive_summary_response(
        aggregates=aggregates, risk=risk, health=health, brief=brief,
        trend=trend, deltas=deltas, now=now,
    )
    if persist:
        persist_dashboard_snapshot(
            connection, workspace_id=workspace_id, response=response, now=now, prev_snapshot=prev,
        )
    # Strip private persistence-only keys before returning to the caller.
    response.pop('_risk_components', None)
    response.pop('_health_components', None)
    return response
