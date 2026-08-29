"""Request-level wrappers for the Threat Detection Engineer routes.

Thin: authenticate, resolve the workspace, apply workspace-scoped filters, and
serialize. All heavy logic lives in service.py / summary.py. Every query is
workspace-scoped; a detection from another workspace is never returned. These are
all read-only except ``investigate_endpoint`` (a POST).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, status

from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import service, summary

_PROMOTED_STATUSES = ('open', 'investigating', 'resolved', 'dismissed')
_VALID_STATUSES = ('anomaly', 'open', 'investigating', 'resolved', 'dismissed')
_VALID_SEVERITIES = ('low', 'medium', 'high', 'critical')


def _iso(value: Any) -> Optional[str]:
    return summary._iso(value)


def _num(value: Any) -> Optional[float]:
    return summary._num(value)


def summary_endpoint(request: Any, *, window_days: int = 7, window: Optional[str] = None) -> dict[str, Any]:
    return summary.build_summary_for_request(request, window_days=window_days, window=window)


def _amount(value: Any) -> Optional[str]:
    """Base-unit amounts cross the wire as STRINGS.

    They are uint256-range integers; serializing them as JSON numbers would push
    them through a double and silently corrupt a reconciliation value. The
    frontend formats the string and never does arithmetic on it."""
    if value is None:
        return None
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _serialize_detection_row(row: dict[str, Any]) -> dict[str, Any]:
    detection_type = str(row.get('detection_type') or '')
    labels = tdc.all_detection_type_labels()
    return {
        'id': str(row['id']),
        'detection_type': row.get('detection_type'),
        'detection_type_label': labels.get(detection_type, row.get('detection_type')),
        # Category is a stored fact; the default only covers rows written before
        # the column existed, so a filter can never silently mis-bucket a row.
        'category': str(row.get('category') or tdc.default_category(detection_type)),
        'title': row.get('title'),
        'severity': row.get('severity'),
        'confidence': _num(row.get('confidence')),
        'status': row.get('status'),
        'chain_id': row.get('chain_id'),
        'primary_asset_id': str(row['primary_asset_id']) if row.get('primary_asset_id') else None,
        'asset_name': row.get('asset_name'),
        'evidence_source': row.get('evidence_source'),
        'evidence_quality': row.get('evidence_quality'),
        'baseline_window': row.get('baseline_window'),
        'event_count': int(row.get('event_count') or 0),
        'actor_count': int(row.get('actor_count') or 0),
        'transaction_count': int(row.get('transaction_count') or 0),
        'evidence_count': int(row.get('evidence_count') or 0),
        'explanation': row.get('explanation'),
        'recommended_next_step': row.get('recommended_next_step'),
        'alert_eligible': bool(row.get('alert_eligible')),
        'ai_summary': row.get('ai_summary'),
        'ai_summary_source': row.get('ai_summary_source'),
        'linked_alert_id': str(row['linked_alert_id']) if row.get('linked_alert_id') else None,
        'linked_incident_id': str(row['linked_incident_id']) if row.get('linked_incident_id') else None,
        'first_seen_at': _iso(row.get('first_seen_at')),
        'last_seen_at': _iso(row.get('last_seen_at')),
        'detected_at': _iso(row.get('detected_at')),
        # --- Operational Integrity (null for cyber-lane rows) ---------------
        'deterministic_reason_code': row.get('deterministic_reason_code'),
        'operational_checks': row.get('operational_checks') or {},
        'matcher_version': row.get('matcher_version'),
        'observed_amount': _amount(row.get('observed_amount')),
        'expected_amount': _amount(row.get('expected_amount')),
        'variance_amount': _amount(row.get('variance_amount')),
        'amount_decimals': row.get('amount_decimals'),
        'amount_unit': row.get('amount_unit'),
        'operation': row.get('operation'),
        'tx_hash': row.get('tx_hash'),
        'block_number': row.get('block_number'),
        'telemetry_source': row.get('telemetry_source'),
        'telemetry_stage': row.get('telemetry_stage'),
        'telemetry_observed_at': _iso(row.get('telemetry_observed_at')),
        'preconfirmation_received_at': _iso(row.get('preconfirmation_received_at')),
        'provenance': row.get('provenance') or {},
    }


def _list_detections(
    request: Any,
    *,
    statuses: tuple[str, ...],
    detection_type: Optional[str],
    severity: Optional[str],
    status_value: Optional[str],
    asset_id: Optional[str],
    min_confidence: Optional[float],
    window_days: Optional[int],
    window: Optional[str],
    limit: int,
    offset: int,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> dict[str, Any]:
    from services.api.app import pilot

    pilot.require_live_mode()
    max_limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']

        if not service._table_exists(connection, 'threat_detections'):
            return {'detections': [], 'total': 0, 'limit': max_limit, 'offset': offset, 'degraded': True, 'degraded_reason': 'detection_storage_provisioning'}

        # Validate + narrow the status set.
        effective_statuses = list(statuses)
        if status_value:
            sv = str(status_value).strip().lower()
            if sv not in _VALID_STATUSES:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid status filter.')
            effective_statuses = [sv] if sv in statuses or statuses == _VALID_STATUSES else effective_statuses
            if sv not in effective_statuses:
                effective_statuses = [sv]
        if detection_type and str(detection_type).strip().lower() not in tdc.all_detection_types():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid detection type filter.')
        if severity and str(severity).strip().lower() not in _VALID_SEVERITIES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid severity filter.')
        # Category is a first-class filter over a stored column, not a cosmetic
        # label: selecting Operational Integrity narrows real backend records.
        category_filter: Optional[str] = None
        if category:
            category_filter = tdc.normalize_category(category)
            if category_filter is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid detection category filter.')

        # One canonical selected window (24h / 7d / 30d), shared with the summary.
        window_clause_seconds: Optional[int] = None
        if window is not None or window_days is not None:
            window_clause_seconds = int(tdc.resolve_window(window, window_days)['seconds'])

        params: list[Any] = [workspace_id, effective_statuses]
        where = ['td.workspace_id = %s', 'td.status = ANY(%s)']
        if detection_type:
            where.append('td.detection_type = %s')
            params.append(str(detection_type).strip().lower())
        if severity:
            where.append('td.severity = %s')
            params.append(str(severity).strip().lower())
        if asset_id:
            where.append('td.primary_asset_id = %s::uuid')
            params.append(str(asset_id))
        if min_confidence is not None:
            where.append('td.confidence >= %s')
            params.append(float(min_confidence))
        if category_filter is not None:
            # COALESCE covers rows written before the category column existed;
            # the default mirrors tdc.default_category so the SQL and the
            # serializer can never disagree about which lane a row is in.
            where.append(
                "COALESCE(NULLIF(td.category, ''), CASE WHEN td.detection_type = ANY(%s) THEN %s ELSE %s END) = %s"
            )
            params.extend([
                list(tdc.OPERATIONAL_INTEGRITY_TYPES),
                tdc.CATEGORY_OPERATIONAL_INTEGRITY,
                tdc.CATEGORY_CYBER_SECURITY,
                category_filter,
            ])
        search_term = str(search or '').strip()
        if search_term:
            # Free-text search over the customer-visible identifiers only. Bound
            # as a parameter (never interpolated) and length-capped.
            like = f'%{search_term[:120].lower()}%'
            where.append(
                '(lower(td.title) LIKE %s OR lower(COALESCE(td.detection_type, \'\')) LIKE %s '
                "OR lower(COALESCE(td.deterministic_reason_code, '')) LIKE %s "
                "OR lower(COALESCE(td.tx_hash, '')) LIKE %s "
                "OR lower(COALESCE(a.name, '')) LIKE %s)"
            )
            params.extend([like, like, like, like, like])
        if window_clause_seconds is not None:
            where.append("td.detected_at >= NOW() - (%s || ' seconds')::interval")
            params.append(str(window_clause_seconds))
        where_sql = ' AND '.join(where)

        total_row = connection.execute(
            f'''
            SELECT COUNT(*) AS n
            FROM threat_detections td
            LEFT JOIN assets a ON a.id = td.primary_asset_id AND a.workspace_id = td.workspace_id
            WHERE {where_sql}
            ''',
            tuple(params),
        ).fetchone() or {}
        total = int(total_row.get('n') or 0)

        rows = connection.execute(
            f'''
            SELECT td.*, a.name AS asset_name
            FROM threat_detections td
            LEFT JOIN assets a ON a.id = td.primary_asset_id AND a.workspace_id = td.workspace_id
            WHERE {where_sql}
            ORDER BY CASE td.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     td.detected_at DESC
            LIMIT %s OFFSET %s
            ''',
            tuple(params + [max_limit, offset]),
        ).fetchall()
        return {
            'detections': [_serialize_detection_row(dict(r)) for r in rows],
            'total': total,
            'limit': max_limit,
            'offset': offset,
            'degraded': False,
        }


def detections_endpoint(
    request: Any,
    *,
    detection_type: Optional[str] = None,
    severity: Optional[str] = None,
    status_value: Optional[str] = None,
    asset_id: Optional[str] = None,
    min_confidence: Optional[float] = None,
    window_days: Optional[int] = None,
    window: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> dict[str, Any]:
    """Promoted detections (excludes sub-threshold anomalies)."""
    return _list_detections(
        request, statuses=_PROMOTED_STATUSES, detection_type=detection_type, severity=severity,
        status_value=status_value, asset_id=asset_id, min_confidence=min_confidence,
        window_days=window_days, window=window, limit=limit, offset=offset,
        category=category, search=search,
    )


def anomalies_endpoint(
    request: Any,
    *,
    detection_type: Optional[str] = None,
    asset_id: Optional[str] = None,
    window_days: Optional[int] = None,
    window: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> dict[str, Any]:
    """Sub-threshold anomalies that have NOT crossed detection criteria."""
    result = _list_detections(
        request, statuses=('anomaly',), detection_type=detection_type, severity=None,
        status_value=None, asset_id=asset_id, min_confidence=None,
        window_days=window_days, window=window, limit=limit, offset=offset,
        category=category, search=search,
    )
    # Anomaly-specific framing: why each has not been promoted.
    for row in result.get('detections', []):
        row['anomalies'] = True
        row['promotion_state'] = 'below_threshold'
        row['not_promoted_reason'] = (
            'Deviation observed but evidence strength/severity has not crossed the promotion threshold.'
        )
    result['anomalies'] = result.pop('detections', [])
    return result


def detection_detail_endpoint(detection_id: str, request: Any) -> dict[str, Any]:
    from services.api.app import pilot

    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']

        if not service._table_exists(connection, 'threat_detections'):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Detection not found.')

        row = connection.execute(
            '''
            SELECT td.*, a.name AS asset_name
            FROM threat_detections td
            LEFT JOIN assets a ON a.id = td.primary_asset_id AND a.workspace_id = td.workspace_id
            WHERE td.id = %s::uuid AND td.workspace_id = %s
            ''',
            (detection_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Detection not found.')
        detection = _serialize_detection_row(dict(row))
        detection['score_inputs'] = dict(row).get('score_inputs') or {}
        detection['cluster_key'] = dict(row).get('cluster_key')
        # The Operational Integrity Analysis panel renders BACKEND FACTS. The
        # checks were decided by the deterministic matcher when the detection was
        # written; nothing is recomputed here and no model is consulted.
        analysis = operational_analysis(dict(row))
        if analysis is not None:
            detection['operational_analysis'] = analysis

        evidence_rows = connection.execute(
            '''
            SELECT id, telemetry_id, transaction_hash, block_number, actor_address, evidence_type,
                   evidence_quality, evidence_payload, observed_at, created_at
            FROM threat_detection_evidence
            WHERE detection_id = %s AND workspace_id = %s
            ORDER BY observed_at DESC NULLS LAST, created_at DESC
            LIMIT 200
            ''',
            (detection_id, workspace_id),
        ).fetchall()
        evidence = [
            {
                'id': str(e['id']),
                'telemetry_id': str(e['telemetry_id']) if e.get('telemetry_id') else None,
                'transaction_hash': e.get('transaction_hash'),
                'block_number': e.get('block_number'),
                'actor_address': e.get('actor_address'),
                'evidence_type': e.get('evidence_type'),
                'evidence_quality': e.get('evidence_quality'),
                'evidence_payload': e.get('evidence_payload') or {},
                'observed_at': _iso(e.get('observed_at')),
            }
            for e in evidence_rows
        ]
        return {'detection': detection, 'evidence': evidence}


def operational_analysis(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Structured operational-integrity analysis for one stored detection.

    Returns None for a cyber-lane detection (there is nothing operational to
    show) and for an operational row written before the checks column existed —
    an absent analysis is reported as absent, never rendered as all-clear.
    """
    from services.api.app.domains.operational_integrity import config as oic
    from services.api.app.domains.operational_integrity import explanation, schemas

    detection_type = str(row.get('detection_type') or '')
    category = str(row.get('category') or tdc.default_category(detection_type))
    if category != tdc.CATEGORY_OPERATIONAL_INTEGRITY:
        return None

    stored = row.get('operational_checks')
    checks = stored if isinstance(stored, dict) else {}
    ordered = [checks[key] for key in schemas.CHECK_ORDER if isinstance(checks.get(key), dict)]
    severity = str(row.get('severity') or '')
    conclusion = _conclusion_from_stored(ordered, severity)

    facts = {
        'detection_type': detection_type,
        'category': category,
        'severity': severity,
        'conclusion': conclusion,
        'deterministic_reason_code': row.get('deterministic_reason_code'),
        'confidence': _num(row.get('confidence')),
        'operation': row.get('operation'),
        'observed_amount': _amount(row.get('observed_amount')),
        'expected_amount': _amount(row.get('expected_amount')),
        'variance_amount': _amount(row.get('variance_amount')),
        'operational_checks': checks,
        'telemetry_source': row.get('telemetry_source'),
        'telemetry_stage': row.get('telemetry_stage'),
        'tx_hash': row.get('tx_hash'),
        'provenance': row.get('provenance') or {},
    }
    narrative = explanation.build_deterministic_narrative(facts)
    return {
        'checks': ordered,
        'checks_available': bool(ordered),
        'conclusion': conclusion,
        'deterministic_reason_code': row.get('deterministic_reason_code'),
        'confidence': _num(row.get('confidence')),
        'matcher_version': row.get('matcher_version'),
        'detection_type_label': oic.DETECTION_TYPE_LABELS.get(detection_type, row.get('detection_type')),
        # AI text is a narrative field. It is stored beside the verdict, never
        # inside it, and the authority label says so on screen.
        'narrative': narrative,
        'ai_summary': row.get('ai_summary'),
        'ai_summary_source': row.get('ai_summary_source') or 'deterministic',
        'ai_authority': explanation.AI_AUTHORITY_LABEL,
    }


def _conclusion_from_stored(ordered_checks: list[dict[str, Any]], severity: str) -> str:
    """Conclusion derived from the STORED check statuses.

    Kept in one place so the panel heading and the checks below it can never
    contradict each other. An empty check set is INDETERMINATE — an operational
    row with no recorded checks has proven nothing, and must not read as clear."""
    from services.api.app.domains.operational_integrity import schemas

    statuses = {str(c.get('status') or '').upper() for c in ordered_checks}
    if not statuses:
        return schemas.CONCLUSION_INDETERMINATE
    if schemas.FAIL in statuses:
        return (
            schemas.CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY
            if str(severity or '').lower() == 'critical'
            else schemas.CONCLUSION_OPERATIONAL_ANOMALY
        )
    if schemas.UNKNOWN in statuses:
        return schemas.CONCLUSION_INDETERMINATE
    return schemas.CONCLUSION_OPERATIONALLY_AUTHORIZED


def investigate_endpoint(detection_id: str, request: Any) -> dict[str, Any]:
    return service.start_investigation(detection_id, request)


# --------------------------------------------------------------------------
# Telemetry tab — canonical normalized telemetry list (read-only)
# --------------------------------------------------------------------------
_PRIVILEGED_EVENT_QUALITY = {
    'ownership_transferred', 'owner_changed', 'role_granted', 'role_revoked', 'admin_changed',
    'paused', 'unpaused', 'upgraded', 'implementation_upgraded', 'proxy_upgraded', 'minter_added',
    'minter_removed', 'blacklist_updated', 'config_changed', 'parameter_changed',
}


def _telemetry_evidence_quality(event_type: str, payload: Any) -> str:
    """Coarse evidence-quality label for a raw telemetry row, derived truthfully
    from the event type (decoded privileged event vs a normalized transfer)."""
    et = str(event_type or '').strip().lower()
    if et in _PRIVILEGED_EVENT_QUALITY:
        return 'decoded_call'
    payload = payload if isinstance(payload, dict) else {}
    frm = str(payload.get('from') or payload.get('from_address') or '').lower()
    to = str(payload.get('to') or payload.get('to_address') or '').lower()
    zero = '0x0000000000000000000000000000000000000000'
    if frm == zero or to in (zero, '0x000000000000000000000000000000000000dead'):
        return 'event_logs'
    return 'normalized_telemetry'


def _ingestion_source(provider_type: Any, event_type: Any) -> str:
    """Canonical ingestion-source key (HOW the row was captured), distinct from the
    evidence MODE (live/simulator/replay) and from freshness. The frontend maps
    this key to a human label."""
    pt = str(provider_type or '').strip().lower()
    et = str(event_type or '').strip().lower()
    if 'quicknode' in pt or pt == 'quicknode_stream':
        return 'quicknode_stream'
    if 'webhook' in pt:
        return 'webhook'
    if 'backfill' in pt or et == 'backfill':
        return 'backfill'
    if pt in ('evm_rpc', 'rpc', 'rpc_polling') or et in ('rpc_polling', 'poll', 'poll_heartbeat'):
        return 'rpc_polling'
    if pt in ('guided_workflow', 'manual', 'imported', 'simulator'):
        return 'manual'
    return pt or 'unknown'


def _row_freshness(observed_at: Any, now: Any, stale_seconds: int) -> str:
    """Per-row freshness (fresh | stale | unknown). An old row captured live is
    still stale today — freshness is time-based, never inferred from the source."""
    if observed_at is None:
        return 'unknown'
    ts = observed_at
    if isinstance(ts, str):
        try:
            from datetime import datetime as _dt

            ts = _dt.fromisoformat(ts.replace('Z', '+00:00'))
        except ValueError:
            return 'unknown'
    try:
        from datetime import timezone

        if getattr(ts, 'tzinfo', None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return 'fresh' if (now - ts).total_seconds() <= int(stale_seconds) else 'stale'
    except Exception:
        return 'unknown'


def telemetry_endpoint(
    request: Any,
    *,
    event_type: Optional[str] = None,
    asset_id: Optional[str] = None,
    evidence_source: Optional[str] = None,
    freshness: Optional[str] = None,
    category: Optional[str] = None,
    window_days: Optional[int] = None,
    window: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    from services.api.app import pilot
    from services.api.app.domains.threat_detection import config as tdc

    pilot.require_live_mode()
    max_limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    now = pilot.utc_now()
    cfg = tdc.engine_config()
    stale_seconds = int(cfg['telemetry_stale_seconds'])
    # Default view is canonical SECURITY telemetry: ingestion/runtime heartbeats
    # (rpc_polling, provider checks, cursor updates, …) are excluded so a poll
    # heartbeat is never shown as an on-chain security event.
    cat = str(category or 'security').strip().lower()
    if cat not in ('security', 'runtime', 'all'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid telemetry category.')
    fresh_filter: Optional[str] = None
    if freshness:
        fresh_filter = str(freshness).strip().lower()
        if fresh_filter not in ('fresh', 'stale'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid freshness filter.')

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']

        if not service._table_exists(connection, 'telemetry_events'):
            return {'telemetry': [], 'total': 0, 'limit': max_limit, 'offset': offset, 'degraded': True, 'degraded_reason': 'telemetry_unavailable'}

        params: list[Any] = [workspace_id]
        where = ['te.workspace_id = %s']
        if cat == 'security':
            where.append('lower(te.event_type) <> ALL(%s)')
            params.append(tdc.runtime_event_types())
        elif cat == 'runtime':
            where.append('lower(te.event_type) = ANY(%s)')
            params.append(tdc.runtime_event_types())
        if event_type:
            where.append('te.event_type = %s')
            params.append(str(event_type).strip().lower())
        if asset_id:
            where.append('te.asset_id = %s::uuid')
            params.append(str(asset_id))
        if evidence_source:
            sv = str(evidence_source).strip().lower()
            if sv not in ('live', 'simulator', 'replay'):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid evidence source filter.')
            where.append('te.evidence_source = %s')
            params.append(sv)
        if window is not None or window_days is not None:
            where.append("te.observed_at >= NOW() - (%s || ' seconds')::interval")
            params.append(str(int(tdc.resolve_window(window, window_days)['seconds'])))
        if fresh_filter == 'fresh':
            where.append('te.observed_at >= NOW() - (%s || \' seconds\')::interval')
            params.append(str(stale_seconds))
        elif fresh_filter == 'stale':
            where.append('te.observed_at < NOW() - (%s || \' seconds\')::interval')
            params.append(str(stale_seconds))
        where_sql = ' AND '.join(where)

        # Canonical key collapses a raw event and its normalized/derived twin of the
        # SAME transaction into one row (never double-counted). Total reflects the
        # deduplicated canonical count so pagination stays truthful.
        canon = f"COALESCE({tdc.TELEMETRY_TX_HASH_SQL.replace('payload_json', 'te.payload_json')}, te.id::text)"
        total = int((connection.execute(
            f'SELECT COUNT(*) AS n FROM (SELECT DISTINCT {canon} AS canon_key FROM telemetry_events te WHERE {where_sql}) canonical_events',
            tuple(params),
        ).fetchone() or {}).get('n') or 0)
        rows = connection.execute(
            f'''
            SELECT * FROM (
                SELECT DISTINCT ON ({canon})
                       te.id, te.event_type, te.asset_id, te.provider_type, te.evidence_source, te.observed_at,
                       te.payload_json, a.name AS asset_name
                FROM telemetry_events te
                LEFT JOIN assets a ON a.id = te.asset_id AND a.workspace_id = te.workspace_id
                WHERE {where_sql}
                ORDER BY {canon}, te.observed_at DESC
            ) deduped
            ORDER BY observed_at DESC
            LIMIT %s OFFSET %s
            ''',
            tuple(params + [max_limit, offset]),
        ).fetchall()
        telemetry = []
        for r in rows:
            payload = r.get('payload_json') or {}
            if not isinstance(payload, dict):
                payload = {}
            telemetry.append({
                'id': str(r['id']),
                'event_type': r.get('event_type'),
                'asset_id': str(r['asset_id']) if r.get('asset_id') else None,
                'asset_name': r.get('asset_name'),
                'provider_type': r.get('provider_type'),
                # Ingestion source (HOW captured), evidence mode (live/sim/replay),
                # evidence quality, and freshness are four independent facts.
                'ingestion_source': _ingestion_source(r.get('provider_type'), r.get('event_type')),
                'evidence_source': r.get('evidence_source'),
                'evidence_mode': r.get('evidence_source'),
                'evidence_quality': _telemetry_evidence_quality(r.get('event_type'), payload),
                'freshness': _row_freshness(r.get('observed_at'), now, stale_seconds),
                'tx_hash': payload.get('tx_hash') or payload.get('transaction_hash') or payload.get('hash'),
                'block_number': payload.get('block_number') or payload.get('block'),
                'observed_at': _iso(r.get('observed_at')),
            })
        return {'telemetry': telemetry, 'total': total, 'limit': max_limit, 'offset': offset, 'category': cat, 'degraded': False}
