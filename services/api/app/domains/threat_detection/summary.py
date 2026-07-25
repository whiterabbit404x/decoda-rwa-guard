"""Canonical workspace-level threat-monitoring summary (read-only).

Single source of truth for Screen 5's KPIs, charts, watchlist, and the Threat
Detection Engineer panel. Everything here is workspace-scoped and derived from
persisted telemetry + detections — the frontend never recomputes a count.

Strictly GET-only: building this summary performs no writes, so merely opening or
refreshing Screen 5 can never create detections, alerts, incidents, or AI runs.

Truthfulness rules enforced here:
  * zero != unavailable != no_telemetry: a change percent is emitted ONLY when a
    valid non-zero comparison period exists (otherwise ``None``).
  * resolved/dismissed detections are NOT active detections.
  * anomalies (sub-threshold) are counted and surfaced separately from detections.
  * detectors that require evidence the platform does not have are reported as
    unsupported — never silently omitted, never shown as "0 found".
  * simulator/replay evidence is labelled and never counted as live coverage.
"""

from __future__ import annotations

from typing import Any, Optional

from services.api.app.domains.threat_detection import ai_explanation
from services.api.app.domains.threat_detection import config as tdc

_ACTIVE_STATUSES = ('open', 'investigating')


def _table_exists(connection: Any, name: str) -> bool:
    try:
        row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS ok', (f'public.{name}',)).fetchone()
        return bool((row or {}).get('ok'))
    except Exception:
        return False


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _change_percent(current: int, previous: int) -> Optional[float]:
    """Percent change, or None when there is no valid comparison base.

    A trend is fabricated if we divide by zero, so previous==0 yields None (the UI
    shows no trend), never +100% or +Inf."""
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 1)


# --------------------------------------------------------------------------
# Worker health (workspace-agnostic liveness from a persisted heartbeat)
# --------------------------------------------------------------------------
def worker_health(connection: Any, *, config: dict[str, Any], now: Any) -> dict[str, Any]:
    enabled = bool(config.get('enabled'))
    health = {'enabled': enabled, 'healthy': False, 'last_heartbeat_at': None, 'last_completed_at': None}
    if not _table_exists(connection, 'threat_detection_worker_state'):
        return health
    try:
        row = connection.execute(
            'SELECT MAX(heartbeat_at) AS hb, MAX(last_completed_at) AS done FROM threat_detection_worker_state'
        ).fetchone() or {}
    except Exception:
        return health
    hb = row.get('hb')
    health['last_heartbeat_at'] = _iso(hb)
    health['last_completed_at'] = _iso(row.get('done'))
    health['healthy'] = enabled and tdc.worker_heartbeat_is_fresh(hb, now, config)
    return health


# --------------------------------------------------------------------------
# Telemetry counts + volume buckets
# --------------------------------------------------------------------------
def _telemetry_counts(connection: Any, *, workspace_id: str, window_start: Any, window_end: Any, prev_start: Any) -> tuple[int, int, Any, dict[str, int]]:
    """(current_count, previous_count, last_telemetry_at, source_breakdown)."""
    if not _table_exists(connection, 'telemetry_events'):
        return 0, 0, None, {}
    row = connection.execute(
        '''
        SELECT
            COUNT(*) FILTER (WHERE observed_at >= %s AND observed_at <= %s) AS current_count,
            COUNT(*) FILTER (WHERE observed_at >= %s AND observed_at < %s) AS previous_count,
            MAX(observed_at) AS last_at,
            COUNT(*) FILTER (WHERE observed_at >= %s AND observed_at <= %s AND evidence_source = 'live') AS live_count,
            COUNT(*) FILTER (WHERE observed_at >= %s AND observed_at <= %s AND evidence_source = 'simulator') AS sim_count,
            COUNT(*) FILTER (WHERE observed_at >= %s AND observed_at <= %s AND evidence_source = 'replay') AS replay_count
        FROM telemetry_events
        WHERE workspace_id = %s AND observed_at >= %s
        ''',
        (
            window_start, window_end,
            prev_start, window_start,
            window_start, window_end,
            window_start, window_end,
            window_start, window_end,
            workspace_id, prev_start,
        ),
    ).fetchone() or {}
    breakdown = {
        'live': int(row.get('live_count') or 0),
        'simulator': int(row.get('sim_count') or 0),
        'replay': int(row.get('replay_count') or 0),
    }
    return int(row.get('current_count') or 0), int(row.get('previous_count') or 0), row.get('last_at'), breakdown


def _telemetry_volume_buckets(connection: Any, *, workspace_id: str, window_start: Any, window_end: Any, window_seconds: int) -> list[dict[str, Any]]:
    if not _table_exists(connection, 'telemetry_events'):
        return []
    # ~48 buckets across the window (bounded), computed portably from epoch floor.
    bucket_seconds = max(3600, window_seconds // 48)
    try:
        rows = connection.execute(
            '''
            SELECT (floor(extract(epoch FROM observed_at) / %s) * %s)::bigint AS bucket_epoch,
                   COUNT(*) AS count,
                   COUNT(*) FILTER (WHERE evidence_source = 'live') AS live_count,
                   COUNT(*) FILTER (WHERE evidence_source <> 'live') AS other_count
            FROM telemetry_events
            WHERE workspace_id = %s AND observed_at >= %s AND observed_at <= %s
            GROUP BY 1
            ORDER BY 1
            ''',
            (int(bucket_seconds), int(bucket_seconds), workspace_id, window_start, window_end),
        ).fetchall()
    except Exception:
        return []
    return [
        {
            'bucket_epoch': int(r.get('bucket_epoch') or 0),
            'count': int(r.get('count') or 0),
            'live_count': int(r.get('live_count') or 0),
            'other_count': int(r.get('other_count') or 0),
            'bucket_seconds': int(bucket_seconds),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Detection counts, by-type, MTTD, top detection, watchlist
# --------------------------------------------------------------------------
def _detection_counts(connection: Any, *, workspace_id: str, window_start: Any, prev_start: Any) -> dict[str, int]:
    row = connection.execute(
        '''
        SELECT
            COUNT(*) FILTER (WHERE status = ANY(%s) AND detected_at >= %s) AS detection_count,
            COUNT(*) FILTER (WHERE status = ANY(%s) AND detected_at >= %s AND detected_at < %s) AS previous_detection_count,
            COUNT(*) FILTER (WHERE status = 'anomaly' AND last_seen_at >= %s) AS anomaly_count,
            COUNT(*) FILTER (WHERE status = 'anomaly' AND last_seen_at >= %s AND last_seen_at < %s) AS previous_anomaly_count
        FROM threat_detections
        WHERE workspace_id = %s
        ''',
        (
            list(_ACTIVE_STATUSES), window_start,
            list(_ACTIVE_STATUSES), prev_start, window_start,
            window_start,
            prev_start, window_start,
            workspace_id,
        ),
    ).fetchone() or {}
    return {
        'detection_count': int(row.get('detection_count') or 0),
        'previous_detection_count': int(row.get('previous_detection_count') or 0),
        'anomaly_count': int(row.get('anomaly_count') or 0),
        'previous_anomaly_count': int(row.get('previous_anomaly_count') or 0),
    }


def _detections_by_type(connection: Any, *, workspace_id: str, window_start: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        '''
        SELECT detection_type, COUNT(*) AS count
        FROM threat_detections
        WHERE workspace_id = %s AND status = ANY(%s) AND detected_at >= %s
        GROUP BY detection_type
        ''',
        (workspace_id, list(_ACTIVE_STATUSES), window_start),
    ).fetchall()
    counts = {str(r.get('detection_type')): int(r.get('count') or 0) for r in rows}
    support = tdc.detector_support()
    result: list[dict[str, Any]] = []
    # Emit every canonical type: supported ones (with real counts) and unsupported
    # ones flagged, so the UI never implies an unsupported detector "found 0".
    for dtype in tdc.DETECTION_TYPES:
        meta = support.get(dtype, {'supported': dtype in counts})
        result.append(
            {
                'type': dtype,
                'label': tdc.DETECTION_TYPE_LABELS.get(dtype, dtype),
                'count': counts.get(dtype, 0),
                'supported': bool(meta.get('supported')),
                'unsupported_reason': None if meta.get('supported') else meta.get('reason'),
            }
        )
    return result


def _mean_time_to_detect(connection: Any, *, workspace_id: str, window_start: Any) -> Optional[float]:
    """Average seconds from the earliest supporting on-chain event to when the
    detection was raised, over promoted detections in the window. None when there
    is no promoted detection to measure (unavailable, never a fabricated 0)."""
    if not _table_exists(connection, 'threat_detection_evidence'):
        return None
    try:
        row = connection.execute(
            '''
            SELECT AVG(EXTRACT(EPOCH FROM (td.detected_at - ev.min_observed))) AS mttd
            FROM threat_detections td
            JOIN LATERAL (
                SELECT MIN(observed_at) AS min_observed
                FROM threat_detection_evidence e
                WHERE e.detection_id = td.id
            ) ev ON TRUE
            WHERE td.workspace_id = %s AND td.status = ANY(%s) AND td.detected_at >= %s
              AND ev.min_observed IS NOT NULL AND td.detected_at >= ev.min_observed
            ''',
            (workspace_id, list(_ACTIVE_STATUSES), window_start),
        ).fetchone() or {}
    except Exception:
        return None
    value = row.get('mttd')
    return round(float(value), 2) if value is not None else None


def _serialize_top_detection(connection: Any, *, workspace_id: str, row: dict[str, Any]) -> dict[str, Any]:
    detection_id = str(row['id'])
    tx_rows = connection.execute(
        '''
        SELECT DISTINCT transaction_hash FROM threat_detection_evidence
        WHERE detection_id = %s AND transaction_hash IS NOT NULL LIMIT 10
        ''',
        (detection_id,),
    ).fetchall()
    actor_rows = connection.execute(
        '''
        SELECT DISTINCT actor_address FROM threat_detection_evidence
        WHERE detection_id = %s AND actor_address IS NOT NULL LIMIT 10
        ''',
        (detection_id,),
    ).fetchall()
    asset_name = None
    if row.get('primary_asset_id'):
        asset = connection.execute(
            'SELECT name FROM assets WHERE id = %s AND workspace_id = %s',
            (str(row['primary_asset_id']), workspace_id),
        ).fetchone()
        asset_name = (asset or {}).get('name')
    return {
        'detection_id': detection_id,
        'detection_type': row.get('detection_type'),
        'title': row.get('title'),
        'summary': row.get('ai_summary') or row.get('explanation'),
        'severity': row.get('severity'),
        'confidence': _num(row.get('confidence')),
        'status': row.get('status'),
        'affected_asset_ids': [str(row['primary_asset_id'])] if row.get('primary_asset_id') else [],
        'affected_asset_names': [asset_name] if asset_name else [],
        'actor_addresses': [str(r['actor_address']) for r in actor_rows if r.get('actor_address')],
        'transaction_hashes': [str(r['transaction_hash']) for r in tx_rows if r.get('transaction_hash')],
        'chain_id': row.get('chain_id'),
        'first_seen_at': _iso(row.get('first_seen_at')),
        'last_seen_at': _iso(row.get('last_seen_at')),
        'detected_at': _iso(row.get('detected_at')),
        'event_count': int(row.get('event_count') or 0),
        'evidence_count': int(row.get('evidence_count') or 0),
        'evidence_quality': row.get('evidence_quality'),
        'evidence_source': row.get('evidence_source'),
        'recommended_next_step': row.get('recommended_next_step'),
        'explanation': row.get('explanation'),
        'ai_summary': row.get('ai_summary'),
        'ai_summary_source': row.get('ai_summary_source'),
        'alert_eligible': bool(row.get('alert_eligible')),
        'alert_id': str(row['linked_alert_id']) if row.get('linked_alert_id') else None,
        'incident_id': str(row['linked_incident_id']) if row.get('linked_incident_id') else None,
    }


def _top_detection(connection: Any, *, workspace_id: str) -> Optional[dict[str, Any]]:
    row = connection.execute(
        '''
        SELECT * FROM threat_detections
        WHERE workspace_id = %s AND status = ANY(%s)
        ORDER BY CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                 confidence DESC, last_seen_at DESC
        LIMIT 1
        ''',
        (workspace_id, list(_ACTIVE_STATUSES)),
    ).fetchone()
    if row is None:
        return None
    return _serialize_top_detection(connection, workspace_id=workspace_id, row=dict(row))


def _watchlist_matches(connection: Any, *, workspace_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """Actor/address matches grounded in active detections (never a fabricated
    watchlist). One row per (actor, detection) with a real evidence link."""
    rows = connection.execute(
        '''
        SELECT e.actor_address, td.id AS detection_id, td.detection_type, td.title, td.confidence,
               td.status, td.severity, td.primary_asset_id, td.evidence_source,
               MIN(e.observed_at) AS first_seen, MAX(e.observed_at) AS last_seen, COUNT(*) AS hits
        FROM threat_detection_evidence e
        JOIN threat_detections td ON td.id = e.detection_id AND td.workspace_id = e.workspace_id
        WHERE e.workspace_id = %s AND td.status = ANY(%s) AND e.actor_address IS NOT NULL
        GROUP BY e.actor_address, td.id, td.detection_type, td.title, td.confidence, td.status, td.severity, td.primary_asset_id, td.evidence_source
        ORDER BY MAX(e.observed_at) DESC
        LIMIT %s
        ''',
        (workspace_id, list(_ACTIVE_STATUSES), int(limit)),
    ).fetchall()
    asset_names: dict[str, Optional[str]] = {}
    matches: list[dict[str, Any]] = []
    for r in rows:
        asset_id = str(r['primary_asset_id']) if r.get('primary_asset_id') else None
        if asset_id and asset_id not in asset_names:
            asset = connection.execute('SELECT name FROM assets WHERE id = %s AND workspace_id = %s', (asset_id, workspace_id)).fetchone()
            asset_names[asset_id] = (asset or {}).get('name')
        matches.append(
            {
                'actor_address': r.get('actor_address'),
                'asset_id': asset_id,
                'asset_name': asset_names.get(asset_id) if asset_id else None,
                'match_reason': r.get('title') or r.get('detection_type'),
                'detection_type': r.get('detection_type'),
                'severity': r.get('severity'),
                'confidence': _num(r.get('confidence')),
                'status': r.get('status'),
                'evidence_source': r.get('evidence_source'),
                'first_seen_at': _iso(r.get('first_seen')),
                'last_seen_at': _iso(r.get('last_seen')),
                'hits': int(r.get('hits') or 0),
                'detection_id': str(r['detection_id']),
            }
        )
    return matches


def _evidence_coverage(connection: Any, *, workspace_id: str) -> dict[str, Any]:
    row = connection.execute(
        '''
        SELECT
            COUNT(*) FILTER (WHERE status = ANY(%s)) AS active_total,
            COUNT(*) FILTER (WHERE status = ANY(%s) AND evidence_source = 'live') AS live_active,
            COUNT(*) FILTER (WHERE status = ANY(%s) AND evidence_source <> 'live') AS non_live_active
        FROM threat_detections
        WHERE workspace_id = %s
        ''',
        (list(_ACTIVE_STATUSES), list(_ACTIVE_STATUSES), list(_ACTIVE_STATUSES), workspace_id),
    ).fetchone() or {}
    return {
        'active_detections': int(row.get('active_total') or 0),
        'live_backed': int(row.get('live_active') or 0),
        'non_live_backed': int(row.get('non_live_active') or 0),
    }


# --------------------------------------------------------------------------
# Canonical builder
# --------------------------------------------------------------------------
def build_threat_summary(connection: Any, *, workspace_id: str, config: dict[str, Any] | None = None, window_days: int = 7) -> dict[str, Any]:
    from services.api.app import pilot

    cfg = config or tdc.engine_config()
    now = pilot.utc_now()
    window_days = max(1, min(int(window_days), 90))
    window_seconds = window_days * 24 * 60 * 60
    from datetime import timedelta

    window_end = now
    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)

    detections_table = _table_exists(connection, 'threat_detections')
    degraded_reasons: list[str] = []

    tele_current, tele_previous, last_telemetry_at, source_breakdown = _telemetry_counts(
        connection, workspace_id=workspace_id, window_start=window_start, window_end=window_end, prev_start=prev_start
    )
    volume_buckets = _telemetry_volume_buckets(
        connection, workspace_id=workspace_id, window_start=window_start, window_end=window_end, window_seconds=window_seconds
    )

    # Data freshness — distinguishes unavailable / no_telemetry / stale / fresh.
    if not _table_exists(connection, 'telemetry_events'):
        data_freshness = 'unavailable'
    elif last_telemetry_at is None:
        data_freshness = 'no_telemetry'
    else:
        age = (now - (last_telemetry_at if getattr(last_telemetry_at, 'tzinfo', None) else last_telemetry_at.replace(tzinfo=now.tzinfo))).total_seconds()
        data_freshness = 'fresh' if age <= int(cfg['telemetry_stale_seconds']) else 'stale'
    if data_freshness == 'stale':
        degraded_reasons.append('telemetry_stale')
    if data_freshness == 'no_telemetry':
        degraded_reasons.append('no_telemetry')

    counts = {'detection_count': 0, 'previous_detection_count': 0, 'anomaly_count': 0, 'previous_anomaly_count': 0}
    detections_by_type = [
        {'type': t, 'label': tdc.DETECTION_TYPE_LABELS.get(t, t), 'count': 0,
         'supported': tdc.detector_support().get(t, {}).get('supported', False),
         'unsupported_reason': None if tdc.detector_support().get(t, {}).get('supported') else tdc.detector_support().get(t, {}).get('reason')}
        for t in tdc.DETECTION_TYPES
    ]
    mttd = None
    top = None
    watchlist: list[dict[str, Any]] = []
    evidence_coverage = {'active_detections': 0, 'live_backed': 0, 'non_live_backed': 0}
    if detections_table:
        counts = _detection_counts(connection, workspace_id=workspace_id, window_start=window_start, prev_start=prev_start)
        detections_by_type = _detections_by_type(connection, workspace_id=workspace_id, window_start=window_start)
        mttd = _mean_time_to_detect(connection, workspace_id=workspace_id, window_start=window_start)
        top = _top_detection(connection, workspace_id=workspace_id)
        watchlist = _watchlist_matches(connection, workspace_id=workspace_id)
        evidence_coverage = _evidence_coverage(connection, workspace_id=workspace_id)
    else:
        degraded_reasons.append('detection_storage_provisioning')

    worker = worker_health(connection, config=cfg, now=now)
    if cfg.get('enabled') and not worker.get('healthy'):
        degraded_reasons.append('worker_unhealthy')

    ingestion_health = {
        'last_telemetry_at': _iso(last_telemetry_at),
        'data_freshness': data_freshness,
        'telemetry_events_window': tele_current,
        'source_breakdown': source_breakdown,
        'worker': worker,
    }

    summary = {
        'workspace_id': workspace_id,
        'generated_at': _iso(now),
        'window_days': window_days,
        'window_start': _iso(window_start),
        'window_end': _iso(window_end),
        'telemetry_events_count': tele_current,
        'previous_telemetry_events_count': tele_previous,
        'telemetry_change_percent': _change_percent(tele_current, tele_previous),
        'detection_count': counts['detection_count'],
        'previous_detection_count': counts['previous_detection_count'],
        'detection_change_percent': _change_percent(counts['detection_count'], counts['previous_detection_count']),
        'anomaly_count': counts['anomaly_count'],
        'previous_anomaly_count': counts['previous_anomaly_count'],
        'anomaly_change_percent': _change_percent(counts['anomaly_count'], counts['previous_anomaly_count']),
        'mean_time_to_detect_seconds': mttd,
        'telemetry_volume_buckets': volume_buckets,
        'detections_by_type': detections_by_type,
        'active_watchlist_matches': watchlist,
        'top_detection': top,
        'ingestion_health': ingestion_health,
        'evidence_coverage': evidence_coverage,
        'data_freshness': data_freshness,
        'degraded_reasons': degraded_reasons,
        'detector_support': tdc.detector_support(),
        'detector_version': tdc.DETECTOR_VERSION,
        'engine_panel': _build_engine_panel(top, data_freshness, degraded_reasons, worker, counts),
    }
    return summary


def _build_engine_panel(top: Optional[dict[str, Any]], data_freshness: str, degraded_reasons: list[str], worker: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    """Canonical state for the Threat Detection Engineer panel, so the UI never
    invents a scary alert when evidence is thin, and never claims healthy when
    ingestion is degraded."""
    if data_freshness == 'unavailable':
        state = 'unavailable'
        headline = 'Threat detection storage is provisioning.'
    elif data_freshness == 'no_telemetry':
        state = 'no_telemetry'
        headline = 'No telemetry has arrived yet — nothing to correlate.'
    elif 'worker_unhealthy' in degraded_reasons:
        state = 'provider_degraded'
        headline = 'Detection worker heartbeat is stale; detections may be delayed.'
    elif top is None:
        if data_freshness == 'stale':
            state = 'telemetry_stale'
            headline = 'Telemetry is stale; no material threat detected in fresh data.'
        elif counts.get('anomaly_count', 0) > 0:
            state = 'anomalies_only'
            headline = 'Sub-threshold anomalies observed; none has crossed detection criteria.'
        else:
            state = 'no_material_threat'
            headline = 'No material threat detected in the current window.'
    elif str(top.get('status')) == 'investigating':
        state = 'investigation_open'
        headline = 'An investigation is already open for the top detection.'
    else:
        state = 'active_detection'
        headline = top.get('ai_summary') or top.get('title') or 'Active threat detection requires review.'

    return {
        'state': state,
        'headline': headline,
        'detection': top,
        'ai_summary_source': (top or {}).get('ai_summary_source') or 'deterministic',
        'can_investigate': bool(top is not None and str(top.get('status')) in ('open', 'investigating') and int((top or {}).get('evidence_count') or 0) > 0),
    }


# --------------------------------------------------------------------------
# Request helper
# --------------------------------------------------------------------------
def build_summary_for_request(request: Any, *, window_days: int = 7) -> dict[str, Any]:
    from services.api.app import pilot

    pilot.require_live_mode()
    cfg = tdc.engine_config()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        summary = build_threat_summary(connection, workspace_id=workspace_context['workspace_id'], config=cfg, window_days=window_days)
        # GET is strictly read-only: no commit, no writes.
        return {'summary': summary, 'workspace': workspace_context['workspace']}
