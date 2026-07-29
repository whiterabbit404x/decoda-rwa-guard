"""Request-level wrappers for the Alert Triage Agent routes (Screen 6).

Thin: authenticate, resolve the workspace, apply workspace-scoped filters, and
serialize. All heavy logic lives in service.py / summary.py / clustering.py.
Every query is workspace-scoped; a cluster or alert from another workspace is
never returned. GETs are strictly read-only.
"""

from __future__ import annotations

from typing import Any, Optional

from services.api.app import pilot
from services.api.app.domains.alert_triage import service, summary
from services.api.app.domains.alert_triage import config as cfg
from services.api.app.domains.alert_triage.summary import _iso, _num, _serialize_cluster_row


def summary_endpoint(
    request: Any,
    *,
    severity: Optional[str] = None,
    asset_id: Optional[str] = None,
    status_value: Optional[str] = None,
    search: Optional[str] = None,
    cluster_id: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    return summary.build_screen6_summary(
        request, severity=severity, asset_id=asset_id, status_value=status_value,
        search=search, cluster_id=cluster_id, limit=limit, offset=offset,
    )


def run_triage_endpoint(request: Any) -> dict[str, Any]:
    return service.request_triage(request)


def clusters_endpoint(request: Any, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """All active clusters ordered by triage priority (read-only)."""
    pilot.require_live_mode()
    max_limit = max(1, min(int(limit or 50), 100))
    offset = max(0, int(offset or 0))
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        if not service.schema_ready(connection):
            return {'clusters': [], 'total': 0, 'limit': max_limit, 'offset': offset, 'schema_ready': False}
        total = int((connection.execute(
            "SELECT COUNT(*) AS n FROM alert_clusters WHERE workspace_id = %s AND status = 'active'",
            (workspace_id,),
        ).fetchone() or {}).get('n') or 0)
        rows = connection.execute(
            '''
            SELECT id, title, derived_severity, confidence, confidence_band, confidence_factors, severity_factors,
                   reason_codes, recommendation_category, recommendation_summary, recommendation_source,
                   primary_asset_id, primary_asset_name, detection_family, chain_id, member_count,
                   first_seen_at, last_seen_at
            FROM alert_clusters
            WHERE workspace_id = %s AND status = 'active'
            ORDER BY CASE derived_severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     confidence DESC NULLS LAST, member_count DESC, created_at DESC
            LIMIT %s OFFSET %s
            ''',
            (workspace_id, max_limit, offset),
        ).fetchall()
        clusters = []
        for r in rows:
            c = _serialize_cluster_row(dict(r))
            c['first_seen_at'] = _iso(dict(r).get('first_seen_at'))
            c['last_seen_at'] = _iso(dict(r).get('last_seen_at'))
            clusters.append(c)
        return {'clusters': clusters, 'total': total, 'limit': max_limit, 'offset': offset, 'schema_ready': True}


def cluster_detail_endpoint(cluster_id: str, request: Any) -> dict[str, Any]:
    """A single cluster and its member alerts (read-only)."""
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        if not service.schema_ready(connection):
            raise pilot.HTTPException(status_code=404, detail='Cluster not found.')
        row = connection.execute(
            '''
            SELECT id, title, derived_severity, confidence, confidence_band, confidence_factors, severity_factors,
                   reason_codes, recommendation_category, recommendation_summary, recommendation_source,
                   primary_asset_id, primary_asset_name, detection_family, chain_id, member_count,
                   first_seen_at, last_seen_at
            FROM alert_clusters
            WHERE id = %s::uuid AND workspace_id = %s AND status = 'active'
            ''',
            (cluster_id, workspace_id),
        ).fetchone()
        if row is None:
            raise pilot.HTTPException(status_code=404, detail='Cluster not found.')
        cluster = _serialize_cluster_row(dict(row))
        cluster['first_seen_at'] = _iso(dict(row).get('first_seen_at'))
        cluster['last_seen_at'] = _iso(dict(row).get('last_seen_at'))

        members = connection.execute(
            '''
            SELECT a.id, a.title, a.severity, a.status, a.created_at, a.incident_id,
                   COALESCE(a.payload->>'tx_hash', a.payload->'evidence'->>'tx_hash') AS tx_hash,
                   ast.name AS asset_name
            FROM alert_cluster_members m
            JOIN alerts a ON a.id = m.alert_id AND a.workspace_id = m.workspace_id
            LEFT JOIN targets t ON t.id = a.target_id AND t.workspace_id = a.workspace_id
            LEFT JOIN assets ast ON ast.id = t.asset_id AND ast.workspace_id = a.workspace_id
            WHERE m.workspace_id = %s AND m.cluster_id = %s::uuid
            ORDER BY CASE lower(a.severity) WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     a.created_at DESC
            ''',
            (workspace_id, cluster_id),
        ).fetchall()
        cluster['members'] = [
            {
                'id': str(m['id']),
                'title': m.get('title') or 'Untitled alert',
                'severity': cfg.normalize_severity(m.get('severity')),
                'status': str(m.get('status') or '').strip().lower() or 'open',
                'asset_name': m.get('asset_name') or 'Unassigned asset',
                'tx_hash': m.get('tx_hash'),
                'incident_id': str(m['incident_id']) if m.get('incident_id') else None,
                'occurred_at': _iso(m.get('created_at')),
            }
            for m in members
        ]
        # Audit the manual cluster review (read-with-intent), best-effort.
        try:
            service._audit(connection, request=request, action='alerts.triage.cluster_reviewed',
                           workspace_id=workspace_id, user_id=user['id'],
                           metadata={'cluster_id': cluster_id, 'member_count': cluster['member_count']})
            connection.commit()
        except Exception:
            pass
        return {'cluster': cluster}
