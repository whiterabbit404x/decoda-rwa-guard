"""Composed, workspace-scoped read model for Screen 6 (Active Alerts).

One request returns a mutually-consistent snapshot: the paginated active alerts,
the severity tab totals, filter metadata, the cluster summary, the top
recommendation, and the last triage state. Counts and rows are derived from the
SAME normalized active-alert population in the same request, so a tab badge can
never disagree with the table or with pagination.

Strictly read-only: building this summary performs no writes, so opening or
refreshing Screen 6 never runs triage, creates clusters, or mutates an alert.
"""

from __future__ import annotations

from typing import Any, Optional

from services.api.app import pilot
from services.api.app.domains.alert_triage import clustering, service
from services.api.app.domains.alert_triage import config as cfg

_ACTIVE_STATUS_ORDER = ('open', 'acknowledged', 'investigating', 'linked_to_incident', 'suppressed')


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


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------
def _serialize_alert(item: dict[str, Any], normalized: dict[str, Any], membership: dict[str, Any]) -> dict[str, Any]:
    alert_id = normalized['alert_id']
    cluster = membership.get(alert_id)
    return {
        'id': alert_id,
        'title': item.get('title') or 'Untitled alert',
        'severity': normalized['severity'],
        'status': str(item.get('status') or '').strip().lower() or 'open',
        'asset_id': normalized.get('asset_id'),
        # Truthful fallback: the canonical asset name, else the payload label, else
        # an explicit "Unassigned asset" — never a made-up asset.
        'asset_name': normalized.get('asset_name') or 'Unassigned asset',
        'asset_known': bool(normalized.get('asset_name')),
        'chain_id': normalized.get('chain_id'),
        'tx_hash': normalized.get('tx_hash'),
        'contract_address': normalized.get('contract_address'),
        'actor': normalized.get('actor'),
        'detection_family': normalized.get('detection_family'),
        'detection_family_label': cfg.detection_family_label(normalized.get('detection_family')),
        'occurred_at': _iso(item.get('created_at')),
        'incident_id': normalized.get('incident_id'),
        'detection_id': item.get('detection_id'),
        'cluster': (
            {
                'id': str(cluster['cluster_id']),
                'title': cluster.get('title'),
                'severity': cluster.get('derived_severity'),
                'member_count': cluster.get('member_count'),
            }
            if cluster else None
        ),
    }


def _triage_priority(alert: dict[str, Any]) -> tuple[int, float]:
    cluster = alert.get('cluster')
    if not cluster:
        return (0, 0.0)
    return (cfg.SEVERITY_RANK.get(str(cluster.get('severity') or 'low'), 1), float(cluster.get('member_count') or 0))


def _matches_search(alert: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    haystack = ' '.join(
        str(v or '') for v in (
            alert.get('title'), alert.get('asset_name'), alert.get('tx_hash'),
            alert.get('contract_address'), alert.get('actor'), alert.get('id'),
            (alert.get('cluster') or {}).get('title') if alert.get('cluster') else None,
            (alert.get('cluster') or {}).get('id') if alert.get('cluster') else None,
        )
    ).lower()
    return q in haystack


# --------------------------------------------------------------------------
# Cluster / triage state
# --------------------------------------------------------------------------
def _serialize_cluster_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': str(row['id']),
        'title': row.get('title'),
        'derived_severity': row.get('derived_severity'),
        'confidence': _num(row.get('confidence')),
        'confidence_band': row.get('confidence_band'),
        'confidence_factors': row.get('confidence_factors') or [],
        'severity_factors': row.get('severity_factors') or [],
        'reason_codes': row.get('reason_codes') or [],
        'reason_labels': [cfg.reason_label(c) for c in (row.get('reason_codes') or [])],
        'recommendation_category': row.get('recommendation_category'),
        'recommendation_label': cfg.recommendation_label(row.get('recommendation_category')),
        'recommendation_summary': row.get('recommendation_summary'),
        'recommendation_source': row.get('recommendation_source') or 'rules_based',
        'primary_asset_id': str(row['primary_asset_id']) if row.get('primary_asset_id') else None,
        'primary_asset_name': row.get('primary_asset_name'),
        'detection_family': row.get('detection_family'),
        'detection_family_label': cfg.detection_family_label(row.get('detection_family')),
        'chain_id': row.get('chain_id'),
        'member_count': int(row.get('member_count') or 0),
    }


def _build_triage_state(connection: Any, workspace_id: str, *, schema_ok: bool) -> dict[str, Any]:
    ai_is_available = cfg.ai_available()
    if not schema_ok:
        return {
            'schema_ready': False,
            'ai_available': ai_is_available,
            'mode': 'unavailable',
            'processing': False,
            'last_run': None,
            'cluster_count': 0,
            'unclustered_count': None,
            'top_recommendation': None,
            'clusters_preview': [],
            'state': 'provisioning',
        }

    last_run_row = connection.execute(
        '''
        SELECT id, status, mode, triggered_by, alerts_considered, clusters_total, clusters_created,
               clusters_updated, unclustered_count, ai_enriched_count, error_code, started_at, completed_at
        FROM alert_triage_runs
        WHERE workspace_id = %s AND status IN ('completed', 'failed')
        ORDER BY created_at DESC LIMIT 1
        ''',
        (workspace_id,),
    ).fetchone()
    last_success = connection.execute(
        '''
        SELECT id, mode, clusters_total, unclustered_count, alerts_considered, ai_enriched_count, completed_at
        FROM alert_triage_runs
        WHERE workspace_id = %s AND status = 'completed'
        ORDER BY created_at DESC LIMIT 1
        ''',
        (workspace_id,),
    ).fetchone()
    processing_row = connection.execute(
        "SELECT id, started_at FROM alert_triage_runs WHERE workspace_id = %s AND status = 'running' ORDER BY started_at DESC LIMIT 1",
        (workspace_id,),
    ).fetchone()

    cluster_rows = [
        dict(r) for r in connection.execute(
            '''
            SELECT id, title, derived_severity, confidence, confidence_band, confidence_factors, severity_factors,
                   reason_codes, recommendation_category, recommendation_summary, recommendation_source,
                   primary_asset_id, primary_asset_name, detection_family, chain_id, member_count
            FROM alert_clusters
            WHERE workspace_id = %s AND status = 'active'
            ORDER BY CASE derived_severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     confidence DESC NULLS LAST, member_count DESC, created_at DESC
            LIMIT 25
            ''',
            (workspace_id,),
        ).fetchall()
    ]
    clusters = [_serialize_cluster_row(r) for r in cluster_rows]

    # Mode reflects the last SUCCESSFUL run (truthful: never claim AI-assisted from
    # a run that did not use a live provider). Falls back to availability.
    if last_success is not None:
        mode = str(last_success.get('mode') or 'rules_based')
    else:
        mode = 'rules_based' if ai_is_available else 'rules_based'

    if processing_row is not None:
        state = 'processing'
    elif last_success is None:
        state = 'not_run'
    elif last_run_row is not None and str(last_run_row.get('status')) == 'failed':
        # Preserve the previous successful result, but surface the latest failure.
        state = 'degraded'
    else:
        state = 'ready'

    def _run_dict(row: Any) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        return {
            'id': str(row['id']),
            'status': row.get('status'),
            'mode': row.get('mode'),
            'triggered_by': row.get('triggered_by'),
            'alerts_considered': int(row.get('alerts_considered') or 0),
            'clusters_total': int(row.get('clusters_total') or 0),
            'clusters_created': int(row.get('clusters_created') or 0),
            'unclustered_count': int(row.get('unclustered_count') or 0),
            'ai_enriched_count': int(row.get('ai_enriched_count') or 0),
            'error_code': row.get('error_code'),
            'started_at': _iso(row.get('started_at')),
            'completed_at': _iso(row.get('completed_at')),
        }

    unclustered = int(last_success.get('unclustered_count')) if last_success is not None else None
    return {
        'schema_ready': True,
        'ai_available': ai_is_available,
        'mode': mode,
        'processing': processing_row is not None,
        'last_run': _run_dict(last_run_row),
        'last_success_at': _iso(last_success.get('completed_at')) if last_success is not None else None,
        'cluster_count': len(clusters),
        'unclustered_count': unclustered,
        'top_recommendation': clusters[0] if clusters else None,
        'clusters_preview': clusters,
        'state': state,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def build_screen6_summary(
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
    pilot.require_live_mode()
    max_limit = max(1, min(int(limit or 25), 100))
    offset = max(0, int(offset or 0))
    sev_filter = str(severity or '').strip().lower() or None
    if sev_filter and sev_filter not in cfg.SEVERITY_RANK:
        raise pilot.HTTPException(status_code=400, detail='Invalid severity filter.')
    status_filter = str(status_value or '').strip().lower() or None
    asset_filter = str(asset_id or '').strip() or None
    cluster_filter = str(cluster_id or '').strip() or None
    query = str(search or '').strip().lower()

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']

        schema_ok = service.schema_ready(connection)
        active_rows, truncated = service.load_active_alerts(connection, workspace_id=workspace_id)

        # Cluster membership map (only when the triage schema exists).
        membership: dict[str, dict[str, Any]] = {}
        if schema_ok and active_rows:
            member_rows = connection.execute(
                '''
                SELECT m.alert_id, c.id AS cluster_id, c.title, c.derived_severity, c.member_count
                FROM alert_cluster_members m
                JOIN alert_clusters c ON c.id = m.cluster_id AND c.workspace_id = m.workspace_id
                WHERE m.workspace_id = %s AND c.status = 'active'
                ''',
                (workspace_id,),
            ).fetchall()
            for r in member_rows:
                membership[str(r['alert_id'])] = dict(r)

        # Serialize the whole active population once.
        serialized: list[dict[str, Any]] = []
        asset_options: dict[str, str] = {}
        statuses_present: set[str] = set()
        for item in active_rows:
            normalized = clustering.normalize_alert(item)
            alert = _serialize_alert(item, normalized, membership)
            serialized.append(alert)
            key = alert['asset_id'] or 'unassigned'
            asset_options.setdefault(key, alert['asset_name'])
            statuses_present.add(alert['status'])

        # Scope = active population filtered by everything EXCEPT the severity tab,
        # so the tab badges show real per-severity totals within the current scope.
        def _in_scope(a: dict[str, Any]) -> bool:
            if asset_filter and (a['asset_id'] or 'unassigned') != asset_filter:
                return False
            if status_filter and a['status'] != status_filter:
                return False
            if cluster_filter and (not a['cluster'] or a['cluster']['id'] != cluster_filter):
                return False
            if query and not _matches_search(a, query):
                return False
            return True

        scoped = [a for a in serialized if _in_scope(a)]
        severity_totals = {
            'all': len(scoped),
            'critical': sum(1 for a in scoped if a['severity'] == 'critical'),
            'high': sum(1 for a in scoped if a['severity'] == 'high'),
            'medium': sum(1 for a in scoped if a['severity'] == 'medium'),
            'low': sum(1 for a in scoped if a['severity'] == 'low'),
        }

        tab_filtered = [a for a in scoped if not sev_filter or a['severity'] == sev_filter]
        # Deterministic ordering: severity, triage priority, occurrence time, id.
        tab_filtered.sort(
            key=lambda a: (
                cfg.SEVERITY_RANK.get(a['severity'], 1),
                _triage_priority(a),
                a['occurred_at'] or '',
                a['id'],
            ),
            reverse=True,
        )
        total = len(tab_filtered)
        # Gracefully clamp an out-of-range page (deleted/moved last-page item).
        if offset >= total and total > 0:
            offset = max(0, ((total - 1) // max_limit) * max_limit)
        page = tab_filtered[offset: offset + max_limit]

        triage = _build_triage_state(connection, workspace_id, schema_ok=schema_ok)

        return {
            'alerts': page,
            'severity_totals': severity_totals,
            'pagination': {
                'limit': max_limit,
                'offset': offset,
                'total': total,
                'has_more': offset + max_limit < total,
                'showing_from': (offset + 1) if total > 0 else 0,
                'showing_to': min(offset + max_limit, total),
            },
            'filters': {
                'assets': [{'value': k, 'label': v} for k, v in sorted(asset_options.items(), key=lambda kv: kv[1].lower())],
                'statuses': [s for s in _ACTIVE_STATUS_ORDER if s in statuses_present],
                'active': {
                    'severity': sev_filter,
                    'asset_id': asset_filter,
                    'status': status_filter,
                    'cluster_id': cluster_filter,
                    'search': str(search or '').strip() or None,
                },
            },
            'triage': triage,
            'active_alert_count': len(serialized),
            'truncated': truncated,
            'generated_at': _iso(pilot.utc_now()),
        }
