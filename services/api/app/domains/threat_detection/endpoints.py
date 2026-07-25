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


def summary_endpoint(request: Any, *, window_days: int = 7) -> dict[str, Any]:
    return summary.build_summary_for_request(request, window_days=window_days)


def _serialize_detection_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': str(row['id']),
        'detection_type': row.get('detection_type'),
        'detection_type_label': tdc.DETECTION_TYPE_LABELS.get(str(row.get('detection_type')), row.get('detection_type')),
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
    limit: int,
    offset: int,
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
        if detection_type and str(detection_type).strip().lower() not in tdc.DETECTION_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid detection type filter.')
        if severity and str(severity).strip().lower() not in _VALID_SEVERITIES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid severity filter.')

        window_clause_days: Optional[int] = None
        if window_days is not None:
            window_clause_days = max(1, min(int(window_days), 365))

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
        if window_clause_days is not None:
            where.append("td.detected_at >= NOW() - (%s || ' days')::interval")
            params.append(str(window_clause_days))
        where_sql = ' AND '.join(where)

        total_row = connection.execute(
            f'SELECT COUNT(*) AS n FROM threat_detections td WHERE {where_sql}', tuple(params)
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
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Promoted detections (excludes sub-threshold anomalies)."""
    return _list_detections(
        request, statuses=_PROMOTED_STATUSES, detection_type=detection_type, severity=severity,
        status_value=status_value, asset_id=asset_id, min_confidence=min_confidence,
        window_days=window_days, limit=limit, offset=offset,
    )


def anomalies_endpoint(
    request: Any,
    *,
    detection_type: Optional[str] = None,
    asset_id: Optional[str] = None,
    window_days: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Sub-threshold anomalies that have NOT crossed detection criteria."""
    result = _list_detections(
        request, statuses=('anomaly',), detection_type=detection_type, severity=None,
        status_value=None, asset_id=asset_id, min_confidence=None,
        window_days=window_days, limit=limit, offset=offset,
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


def telemetry_endpoint(
    request: Any,
    *,
    event_type: Optional[str] = None,
    asset_id: Optional[str] = None,
    evidence_source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
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

        if not service._table_exists(connection, 'telemetry_events'):
            return {'telemetry': [], 'total': 0, 'limit': max_limit, 'offset': offset, 'degraded': True, 'degraded_reason': 'telemetry_unavailable'}

        params: list[Any] = [workspace_id]
        where = ['te.workspace_id = %s']
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
        where_sql = ' AND '.join(where)

        total = int((connection.execute(f'SELECT COUNT(*) AS n FROM telemetry_events te WHERE {where_sql}', tuple(params)).fetchone() or {}).get('n') or 0)
        rows = connection.execute(
            f'''
            SELECT te.id, te.event_type, te.asset_id, te.provider_type, te.evidence_source, te.observed_at,
                   te.payload_json, a.name AS asset_name
            FROM telemetry_events te
            LEFT JOIN assets a ON a.id = te.asset_id AND a.workspace_id = te.workspace_id
            WHERE {where_sql}
            ORDER BY te.observed_at DESC
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
                'source': r.get('provider_type'),
                'evidence_source': r.get('evidence_source'),
                'evidence_quality': _telemetry_evidence_quality(r.get('event_type'), payload),
                'tx_hash': payload.get('tx_hash') or payload.get('transaction_hash') or payload.get('hash'),
                'block_number': payload.get('block_number') or payload.get('block'),
                'observed_at': _iso(r.get('observed_at')),
            })
        return {'telemetry': telemetry, 'total': total, 'limit': max_limit, 'offset': offset, 'degraded': False}
