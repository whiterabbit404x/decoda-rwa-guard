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


def _age_seconds(value: Any, now: Any) -> Optional[int]:
    """Seconds between ``value`` (datetime or ISO string) and ``now``. None if
    unparseable/missing. Naive timestamps are treated as UTC (fail-safe)."""
    if value is None or now is None:
        return None
    ts = value
    if isinstance(ts, str):
        try:
            from datetime import datetime as _dt

            ts = _dt.fromisoformat(ts.replace('Z', '+00:00'))
        except ValueError:
            return None
    try:
        from datetime import timezone

        if getattr(ts, 'tzinfo', None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int((now - ts).total_seconds()))
    except Exception:
        return None


def _next_action(*, data_freshness: str, worker_status: str, top: Optional[dict[str, Any]], counts: dict[str, int]) -> str:
    """Canonical Screen 5 next action, in strict priority order. NEVER returns
    ``open_incident`` — with zero canonical detections there is nothing to escalate.

      1. stale/offline/unavailable ingestion  -> diagnose_ingestion
      2. fresh open detection, not investigated -> start_investigation
      3. existing open investigation            -> open_investigation
      4. healthy and no material threat         -> none
    """
    if data_freshness in ('stale', 'no_telemetry', 'unavailable') or worker_status in ('stale', 'offline'):
        return 'diagnose_ingestion'
    status = str((top or {}).get('status') or '')
    if top is not None and status == 'open':
        return 'start_investigation'
    if top is not None and status == 'investigating':
        return 'open_investigation'
    return 'none'


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
    """(current_count, previous_count, last_security_telemetry_at, source_breakdown).

    Counts CANONICAL SECURITY telemetry only:
      * ingestion/runtime rows (rpc_polling heartbeats, provider checks, cursor
        updates, …) are excluded via the single canonical predicate, so a poll
        heartbeat is never counted as an on-chain threat event;
      * a raw event and its normalized/derived representation of the *same*
        transaction collapse to one canonical event (COUNT DISTINCT on the tx
        hash), so a transaction is never double-counted.
    ``last_at`` is the newest SECURITY telemetry — freshness is judged from real
    on-chain evidence, not from an ingestion heartbeat."""
    if not _table_exists(connection, 'telemetry_events'):
        return 0, 0, None, {}
    runtime_types = tdc.runtime_event_types()
    canon = tdc.TELEMETRY_TX_HASH_SQL
    row = connection.execute(
        f'''
        SELECT
            COUNT(DISTINCT canon) FILTER (WHERE observed_at >= %s AND observed_at <= %s) AS current_count,
            COUNT(DISTINCT canon) FILTER (WHERE observed_at >= %s AND observed_at < %s) AS previous_count,
            MAX(observed_at) FILTER (WHERE observed_at >= %s AND observed_at <= %s) AS last_at,
            COUNT(DISTINCT canon) FILTER (WHERE observed_at >= %s AND observed_at <= %s AND evidence_source = 'live') AS live_count,
            COUNT(DISTINCT canon) FILTER (WHERE observed_at >= %s AND observed_at <= %s AND evidence_source = 'simulator') AS sim_count,
            COUNT(DISTINCT canon) FILTER (WHERE observed_at >= %s AND observed_at <= %s AND evidence_source = 'replay') AS replay_count
        FROM (
            SELECT observed_at, evidence_source,
                   COALESCE({canon}, id::text) AS canon
            FROM telemetry_events
            WHERE workspace_id = %s AND observed_at >= %s
              AND lower(event_type) <> ALL(%s)
        ) security_events
        ''',
        (
            window_start, window_end,
            prev_start, window_start,
            window_start, window_end,
            window_start, window_end,
            window_start, window_end,
            window_start, window_end,
            workspace_id, prev_start, runtime_types,
        ),
    ).fetchone() or {}
    breakdown = {
        'live': int(row.get('live_count') or 0),
        'simulator': int(row.get('sim_count') or 0),
        'replay': int(row.get('replay_count') or 0),
    }
    return int(row.get('current_count') or 0), int(row.get('previous_count') or 0), row.get('last_at'), breakdown


def _last_ingestion_at(connection: Any, *, workspace_id: str) -> Any:
    """Most recent ingestion/runtime signal (rpc_polling / provider check / …).

    This proves the monitoring *loop ran* (poll heartbeat); it is deliberately
    separate from security telemetry freshness. Returns None if none exists."""
    if not _table_exists(connection, 'telemetry_events'):
        return None
    try:
        row = connection.execute(
            '''
            SELECT MAX(observed_at) AS last_ingestion_at
            FROM telemetry_events
            WHERE workspace_id = %s AND lower(event_type) = ANY(%s)
            ''',
            (workspace_id, tdc.runtime_event_types()),
        ).fetchone() or {}
    except Exception:
        return None
    return row.get('last_ingestion_at')


def _last_security_telemetry_ever(connection: Any, *, workspace_id: str) -> Any:
    """Most recent SECURITY telemetry across ALL time (NOT windowed).

    Lets the UI distinguish "no security telemetry in the selected window" (older
    events exist outside it) from "no security telemetry has EVER arrived". Runtime
    heartbeats (rpc_polling / provider checks / …) are excluded via the same
    canonical predicate, so a poll heartbeat never masquerades as a security event.
    Returns None when the workspace has never received any security telemetry."""
    if not _table_exists(connection, 'telemetry_events'):
        return None
    try:
        row = connection.execute(
            '''
            SELECT MAX(observed_at) AS last_at
            FROM telemetry_events
            WHERE workspace_id = %s AND lower(event_type) <> ALL(%s)
            ''',
            (workspace_id, tdc.runtime_event_types()),
        ).fetchone() or {}
    except Exception:
        return None
    return row.get('last_at')


def _empty_state_reason(*, data_freshness: str, last_security_ever: Any, last_ingestion_at: Any) -> str:
    """Canonical empty-state classification so the UI never shows "no telemetry has
    arrived yet" when older security telemetry exists outside the selected window.

      * unavailable                        — detection/telemetry storage provisioning
      * telemetry_stale                    — security telemetry exists in-window but stale
      * none                               — fresh security telemetry present (not empty)
      * no_security_telemetry_in_window    — none in-window, but security telemetry exists ever
      * runtime_only_telemetry             — none ever, but ingestion heartbeats exist
      * no_telemetry_ever                  — nothing relevant has ever arrived
    """
    if data_freshness == 'unavailable':
        return 'unavailable'
    if data_freshness == 'stale':
        return 'telemetry_stale'
    if data_freshness == 'fresh':
        return 'none'
    # data_freshness == 'no_telemetry': nothing in the selected window.
    if last_security_ever is not None:
        return 'no_security_telemetry_in_window'
    if last_ingestion_at is not None:
        return 'runtime_only_telemetry'
    return 'no_telemetry_ever'


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
              AND lower(event_type) <> ALL(%s)
            GROUP BY 1
            ORDER BY 1
            ''',
            (int(bucket_seconds), int(bucket_seconds), workspace_id, window_start, window_end, tdc.runtime_event_types()),
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
        meta = _type_support_meta(dtype, support)
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


def _type_support_meta(dtype: str, support: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Support metadata for a detection type. ``other`` is a count-only CATCH-ALL
    category (always a valid bucket, never a capability-gated detector), so it is
    reported ``supported=True`` and never rolled into the "not evaluated" note that
    lists trace/oracle-dependent detectors the platform genuinely cannot run."""
    if dtype == 'other':
        return {'supported': True, 'reason': None}
    return support.get(dtype, {'supported': False, 'reason': None})


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
def _operational_integrity_block(
    connection: Any, *, workspace_id: str, window_start: Any, now: Any,
) -> dict[str, Any]:
    """The Operational Integrity lane's own truthful capability + coverage state.

    Kept as its own block rather than folded into detections_by_type: the
    Overview card describes the behavioral/cyber detectors, and mixing a
    business-reconciliation detector into that list would make "0" mean two
    different things in one column.

    Every count here is a real query result. Every unsupported detector says WHY,
    so a detector whose authoritative source does not exist is never reported as
    "evaluated, found none".
    """
    from services.api.app.domains.operational_integrity import config as oic
    from services.api.app.domains.operational_integrity import service as ois

    support = oic.detector_support()
    counts: dict[str, int] = {}
    if _table_exists(connection, 'threat_detections'):
        try:
            rows = connection.execute(
                '''
                SELECT td.detection_type AS type_key, COUNT(*) AS n
                FROM threat_detections td
                WHERE td.workspace_id = %s
                  AND td.status = ANY(%s)
                  AND td.detected_at >= %s
                  AND COALESCE(NULLIF(td.category, ''), %s) = %s
                GROUP BY td.detection_type
                ''',
                (
                    workspace_id, list(_ACTIVE_STATUSES), window_start,
                    tdc.CATEGORY_CYBER_SECURITY, tdc.CATEGORY_OPERATIONAL_INTEGRITY,
                ),
            ).fetchall()
            counts = {str(r.get('type_key')): int(r.get('n') or 0) for r in rows}
        except Exception:  # noqa: BLE001 - a read failure is reported, never faked as zero
            counts = {}

    by_type = [
        {
            'type': dtype,
            'label': oic.DETECTION_TYPE_LABELS.get(dtype, dtype),
            'count': counts.get(dtype, 0),
            'supported': bool(support.get(dtype, {}).get('supported')),
            'unsupported_reason': (
                None if support.get(dtype, {}).get('supported') else support.get(dtype, {}).get('reason')
            ),
        }
        for dtype in oic.DETECTION_TYPES
    ]

    try:
        coverage = ois.telemetry_coverage(connection, workspace_id=workspace_id, now=now)
    except Exception:  # noqa: BLE001 - an unreadable coverage state is UNAVAILABLE, never LIVE
        coverage = {
            'state': ois.COVERAGE_UNAVAILABLE, 'telemetry_source': None,
            'telemetry_stage': 'UNKNOWN', 'last_issuance_telemetry_at': None,
            'authoritative_sources': 0, 'authorized_records': 0,
            'preconfirmation_available': False, 'reasons': ['coverage_unreadable'],
        }

    return {
        'category': tdc.CATEGORY_OPERATIONAL_INTEGRITY,
        'label': tdc.CATEGORY_LABELS[tdc.CATEGORY_OPERATIONAL_INTEGRITY],
        'detection_count': sum(counts.get(t, 0) for t in oic.DETECTION_TYPES),
        'by_type': by_type,
        'detector_support': support,
        'matcher_version': oic.MATCHER_VERSION,
        'coverage': coverage,
    }


def build_threat_summary(connection: Any, *, workspace_id: str, config: dict[str, Any] | None = None, window_days: int = 7, window: str | None = None) -> dict[str, Any]:
    from services.api.app import pilot

    cfg = config or tdc.engine_config()
    now = pilot.utc_now()
    # One canonical selected window (24h / 7d / 30d), shared by every section.
    resolved_window = tdc.resolve_window(window, window_days)
    window_key = resolved_window['key']
    window_seconds = int(resolved_window['seconds'])
    window_days = max(1, int(round(resolved_window['days'])))
    from datetime import timedelta

    window_end = now
    window_start = now - timedelta(seconds=window_seconds)
    prev_start = now - timedelta(seconds=window_seconds * 2)

    detections_table = _table_exists(connection, 'threat_detections')
    degraded_reasons: list[str] = []

    tele_current, tele_previous, last_telemetry_at, source_breakdown = _telemetry_counts(
        connection, workspace_id=workspace_id, window_start=window_start, window_end=window_end, prev_start=prev_start
    )
    last_ingestion_at = _last_ingestion_at(connection, workspace_id=workspace_id)
    last_security_ever = _last_security_telemetry_ever(connection, workspace_id=workspace_id)
    volume_buckets = _telemetry_volume_buckets(
        connection, workspace_id=workspace_id, window_start=window_start, window_end=window_end, window_seconds=window_seconds
    )

    # Data freshness — judged from the newest SECURITY telemetry (not an ingestion
    # heartbeat). Distinguishes unavailable / no_telemetry / stale / fresh.
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
        # "no_telemetry" here means nothing in the SELECTED WINDOW. Only surface the
        # "no telemetry has ever arrived" reason when the workspace has genuinely
        # never received security telemetry; when older telemetry exists outside the
        # window, emit the in-window reason so the banner never falsely claims the
        # workspace has no telemetry at all.
        degraded_reasons.append(
            'no_security_telemetry_in_window' if last_security_ever is not None else 'no_telemetry'
        )

    # Canonical empty-state reason: distinguishes "no security telemetry in the
    # selected window" (older events exist) from "no telemetry has ever arrived".
    empty_state_reason = _empty_state_reason(
        data_freshness=data_freshness, last_security_ever=last_security_ever, last_ingestion_at=last_ingestion_at,
    )

    counts = {'detection_count': 0, 'previous_detection_count': 0, 'anomaly_count': 0, 'previous_anomaly_count': 0}
    _support = tdc.detector_support()
    detections_by_type = [
        {'type': t, 'label': tdc.DETECTION_TYPE_LABELS.get(t, t), 'count': 0,
         'supported': bool(_type_support_meta(t, _support).get('supported')),
         'unsupported_reason': None if _type_support_meta(t, _support).get('supported') else _type_support_meta(t, _support).get('reason')}
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

    # Canonical operational classification of the ingestion worker. A heartbeat/poll
    # that is hours old is NEVER 'healthy'/'stable'. The ingestion signal (last poll)
    # is separate from security-telemetry freshness above.
    worker_status = tdc.classify_worker_status(
        last_ingestion_at=last_ingestion_at, now=now, config=cfg, worker_enabled=bool(worker.get('enabled')),
    )
    heartbeat_age_seconds = _age_seconds(worker.get('last_heartbeat_at'), now)
    poll_age_seconds = _age_seconds(last_ingestion_at, now)
    last_security_age_seconds = _age_seconds(last_telemetry_at, now)

    ingestion_health = {
        'last_telemetry_at': _iso(last_telemetry_at),
        'last_security_telemetry_at': _iso(last_telemetry_at),
        'last_security_telemetry_ever_at': _iso(last_security_ever),
        'last_ingestion_at': _iso(last_ingestion_at),
        'data_freshness': data_freshness,
        'empty_state_reason': empty_state_reason,
        'telemetry_events_window': tele_current,
        'source_breakdown': source_breakdown,
        'worker': worker,
        'worker_status': worker_status,
        'heartbeat_age_seconds': heartbeat_age_seconds,
        'poll_age_seconds': poll_age_seconds,
        'last_security_age_seconds': last_security_age_seconds,
    }

    next_action = _next_action(
        data_freshness=data_freshness, worker_status=worker_status, top=top, counts=counts,
    )

    summary = {
        'workspace_id': workspace_id,
        'generated_at': _iso(now),
        'window': window_key,
        'window_days': window_days,
        'window_seconds': window_seconds,
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
        'empty_state_reason': empty_state_reason,
        'last_security_telemetry_ever_at': _iso(last_security_ever),
        'worker_status': worker_status,
        'next_action': next_action,
        'degraded_reasons': degraded_reasons,
        'detector_support': tdc.detector_support(),
        'detector_version': tdc.DETECTOR_VERSION,
        # Both lanes a detection can belong to, so the Detections filter renders
        # real backend categories rather than a hard-coded frontend list.
        'detection_categories': [
            {'value': key, 'label': tdc.CATEGORY_LABELS[key]} for key in tdc.DETECTION_CATEGORIES
        ],
        'operational_integrity': _operational_integrity_block(
            connection, workspace_id=workspace_id, window_start=window_start, now=now,
        ),
        'engine_panel': _build_engine_panel(
            top, data_freshness, degraded_reasons, worker, counts,
            next_action=next_action, worker_status=worker_status, ingestion_health=ingestion_health,
            evidence_coverage=evidence_coverage, empty_state_reason=empty_state_reason,
            last_security_ever_iso=_iso(last_security_ever),
        ),
    }
    return summary


def _build_engine_panel(
    top: Optional[dict[str, Any]],
    data_freshness: str,
    degraded_reasons: list[str],
    worker: dict[str, Any],
    counts: dict[str, int],
    *,
    next_action: str = 'none',
    worker_status: str = 'unknown',
    ingestion_health: dict[str, Any] | None = None,
    evidence_coverage: dict[str, Any] | None = None,
    empty_state_reason: str = 'none',
    last_security_ever_iso: Optional[str] = None,
) -> dict[str, Any]:
    """Canonical state for the Threat Detection Engineer panel, so the UI never
    invents a scary alert when evidence is thin, and never claims healthy when
    ingestion is degraded. Also carries the exact operational fields the panel
    renders, so the frontend derives nothing on its own."""
    if data_freshness == 'unavailable':
        state = 'unavailable'
        headline = 'Threat detection storage is provisioning.'
    elif data_freshness == 'no_telemetry':
        state = 'no_telemetry'
        # Distinguish "none in the selected window" from "none ever" so the panel
        # never claims "no telemetry has arrived yet" when older events exist.
        if empty_state_reason == 'no_security_telemetry_in_window':
            headline = 'No security telemetry in the selected window.'
        elif empty_state_reason == 'runtime_only_telemetry':
            headline = 'Monitoring is running, but no on-chain security telemetry has arrived yet.'
        else:
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

    ih = ingestion_health or {}
    ec = evidence_coverage or {}
    finding, explanation = _engine_finding(state, top, empty_state_reason=empty_state_reason, last_security_ever_iso=last_security_ever_iso)
    return {
        'state': state,
        'headline': headline,
        'finding': finding,
        'explanation': explanation,
        'detection': top,
        'ai_summary_source': (top or {}).get('ai_summary_source') or 'deterministic',
        'next_action': next_action,
        'recommended_action': next_action,
        'can_investigate': bool(top is not None and str(top.get('status')) in ('open', 'investigating') and int((top or {}).get('evidence_count') or 0) > 0),
        'fields': {
            # "Latest security telemetry" is the newest security event EVER — the same
            # value the overview chart's empty state shows — so the panel never renders
            # "—" while the summary already carries a historical timestamp outside the
            # selected window.
            'last_security_telemetry_at': ih.get('last_security_telemetry_at') or ih.get('last_security_telemetry_ever_at'),
            # Worker liveness: the threat worker's own heartbeat when present, otherwise
            # the canonical ingestion signal (last runtime/poll event) that drives the
            # stale worker_status — so the panel's heartbeat age matches the global
            # runtime strip instead of collapsing to "—".
            'worker_heartbeat_at': (ih.get('worker') or {}).get('last_heartbeat_at') or ih.get('last_ingestion_at'),
            'worker_status': worker_status,
            'ingestion_status': data_freshness,
            'evidence_coverage': {
                'active_detections': int(ec.get('active_detections') or 0),
                'live_backed': int(ec.get('live_backed') or 0),
                'non_live_backed': int(ec.get('non_live_backed') or 0),
            },
            'detection_confidence': _num((top or {}).get('confidence')) if top else None,
        },
    }


def _engine_finding(
    state: str,
    top: Optional[dict[str, Any]],
    *,
    empty_state_reason: str = 'none',
    last_security_ever_iso: Optional[str] = None,
) -> tuple[str, str]:
    """(finding, explanation) copy for the panel — truthful per canonical state.

    ``finding``, ``explanation`` and the panel headline are kept deliberately
    distinct (never the same sentence three times) so the panel reads as one
    structured statement rather than a repeated "no telemetry" refrain."""
    if state in ('telemetry_stale', 'provider_degraded'):
        return (
            'No fresh evidence available',
            'No material threat can be evaluated because fresh on-chain security telemetry is unavailable.',
        )
    if state == 'no_telemetry':
        if empty_state_reason == 'no_security_telemetry_in_window':
            explanation = 'No on-chain security events were recorded during the selected window.'
            if last_security_ever_iso:
                explanation += ' Older security telemetry exists outside it.'
            return ('No security telemetry in the selected period', explanation)
        if empty_state_reason == 'runtime_only_telemetry':
            return (
                'No security telemetry yet',
                'The monitoring worker is reporting ingestion heartbeats, but no on-chain security events have been ingested yet.',
            )
        return ('No telemetry received', 'No on-chain security telemetry has ever arrived for this workspace, so there is nothing to correlate.')
    if state == 'unavailable':
        return ('Provisioning', 'Threat detection storage is still provisioning for this workspace.')
    if state == 'anomalies_only':
        return ('Sub-threshold anomalies', 'Deviations were observed but none has crossed detection criteria; no confirmed threat.')
    if state == 'investigation_open':
        return ('Investigation in progress', 'An investigation is already open for the highest-priority detection.')
    if state == 'active_detection':
        return ('Active detection requires review', (top or {}).get('ai_summary') or (top or {}).get('title') or 'A material detection is awaiting investigation.')
    return ('No material threat', 'No material threat detected in the current window from fresh security telemetry.')


# --------------------------------------------------------------------------
# Request helper
# --------------------------------------------------------------------------
def build_summary_for_request(request: Any, *, window_days: int = 7, window: str | None = None) -> dict[str, Any]:
    from services.api.app import pilot

    pilot.require_live_mode()
    cfg = tdc.engine_config()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        summary = build_threat_summary(connection, workspace_id=workspace_context['workspace_id'], config=cfg, window_days=window_days, window=window)
        # GET is strictly read-only: no commit, no writes.
        return {'summary': summary, 'workspace': workspace_context['workspace']}
