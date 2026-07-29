"""Alert Triage Agent service — persistence + orchestration (workspace-scoped).

Responsibilities:
  * load the ACTIVE alert population for a workspace (canonical status/severity),
  * run the deterministic clustering + optional AI enrichment,
  * reconcile the result against the persisted clusters idempotently (re-running
    updates existing clusters and never duplicates memberships), and
  * write cluster history + the canonical workspace audit trail.

The reconciliation PLAN is computed by a pure function (``reconcile``) so its
idempotency, dedup, and change-detection can be unit tested without a database;
``apply_plan`` is the thin SQL layer that executes it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

from services.api.app import pilot
from services.api.app.domains.alert_triage import ai_enrichment, clustering
from services.api.app.domains.alert_triage import config as cfg

logger = logging.getLogger(__name__)

REQUIRED_TABLES = ('alert_clusters', 'alert_cluster_members', 'alert_triage_runs')

# Terminal alert statuses that are NOT active (never clustered / counted).
_TERMINAL_STATUSES = frozenset({'resolved', 'false_positive', 'dismissed', 'closed'})


def _table_exists(connection: Any, name: str) -> bool:
    try:
        row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS ok', (f'public.{name}',)).fetchone()
        return bool((row or {}).get('ok'))
    except Exception:
        return False


def schema_ready(connection: Any) -> bool:
    """True when migration 0134's core alert-triage tables exist (fail-closed)."""
    for table in REQUIRED_TABLES:
        if not _table_exists(connection, table):
            return False
    return True


def _is_active(item: dict[str, Any]) -> bool:
    st = str(item.get('status') or '').strip().lower()
    if st in _TERMINAL_STATUSES:
        return False
    if st == 'suppressed' and not item.get('detection_id'):
        return False
    return True


# --------------------------------------------------------------------------
# Active-alert loader (canonical severity/status via pilot._normalize_alert_view)
# --------------------------------------------------------------------------
def load_active_alerts(connection: Any, *, workspace_id: str, limit: int = cfg.MAX_ACTIVE_ALERTS_SCAN) -> tuple[list[dict[str, Any]], bool]:
    """Return (active_alert_rows, truncated). Rows carry the canonical asset name
    and risk tier via the target->asset join, and are normalized through the SAME
    ``_normalize_alert_view`` the Alerts list uses so severity/status never
    disagree with Screen 6's own table."""
    scan_limit = max(1, min(int(limit or cfg.MAX_ACTIVE_ALERTS_SCAN), cfg.MAX_ACTIVE_ALERTS_SCAN)) + 1
    rows = connection.execute(
        '''
        SELECT
            a.id, a.workspace_id, a.alert_type, a.title, a.severity, a.status, a.module_key,
            a.target_id, a.detection_id, a.incident_id, a.occurrence_count, a.created_at, a.opened_at,
            a.payload, a.findings,
            COALESCE(a.payload->>'tx_hash', a.payload->'evidence'->>'tx_hash') AS tx_hash,
            a.payload->>'from_address' AS from_address,
            a.payload->>'to_address' AS to_address,
            COALESCE(a.payload->>'contract_address', a.payload->>'contract_identifier', t.contract_identifier) AS contract_address,
            COALESCE(a.payload->>'chain_id', t.chain_id::text) AS chain_id,
            COALESCE(a.payload->>'confidence', a.findings->>'confidence') AS confidence,
            COALESCE(a.payload->>'detection_type', a.alert_type) AS detection_type,
            COALESCE(a.findings->>'detector_kind', a.findings->>'detector_family') AS detector_kind,
            t.asset_id AS primary_asset_id,
            ast.name AS primary_asset_name,
            ast.risk_tier AS asset_risk_tier
        FROM alerts a
        LEFT JOIN targets t ON t.id = a.target_id AND t.workspace_id = a.workspace_id
        LEFT JOIN assets ast ON ast.id = t.asset_id AND ast.workspace_id = a.workspace_id
        WHERE a.workspace_id = %s
          AND lower(COALESCE(a.status, '')) NOT IN ('resolved', 'false_positive', 'dismissed', 'closed')
        ORDER BY a.created_at DESC
        LIMIT %s
        ''',
        (workspace_id, scan_limit),
    ).fetchall()

    active: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        # Canonical severity/status normalization (single source of truth).
        pilot._normalize_alert_view(item)
        if _is_active(item):
            active.append(item)
    truncated = len(rows) > (scan_limit - 1)
    return active[: scan_limit - 1], truncated


# --------------------------------------------------------------------------
# Pure reconciliation planner
# --------------------------------------------------------------------------
def _cluster_changed(existing: dict[str, Any], desired: dict[str, Any]) -> list[dict[str, Any]]:
    """History entries for severity / recommendation changes on an existing cluster."""
    changes: list[dict[str, Any]] = []
    old_sev = str(existing.get('derived_severity') or '')
    new_sev = str(desired.get('derived_severity') or '')
    if old_sev and new_sev and old_sev != new_sev:
        changes.append({
            'change_type': 'severity_recalculated',
            'old_value': {'derived_severity': old_sev},
            'new_value': {'derived_severity': new_sev},
            'reason': 'recomputed from current members',
        })
    old_rec = str(existing.get('recommendation_category') or '')
    new_rec = str(desired.get('recommendation_category') or '')
    if old_rec and new_rec and old_rec != new_rec:
        changes.append({
            'change_type': 'recommendation_changed',
            'old_value': {'recommendation_category': old_rec},
            'new_value': {'recommendation_category': new_rec},
            'reason': 'recomputed from current members',
        })
    return changes


def reconcile(
    existing_clusters: list[dict[str, Any]],
    existing_members: list[dict[str, Any]],
    desired_clusters: list[dict[str, Any]],
    *,
    unclustered_alert_ids: list[str],
    id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> dict[str, Any]:
    """Compute an idempotent reconciliation plan (pure — no DB).

    Matches desired clusters to existing ones by fingerprint, so a re-run updates
    the same cluster rather than creating a duplicate; detects severity /
    recommendation / membership changes for the audit history; and archives
    clusters that lost all members.
    """
    existing_by_fp = {str(c['fingerprint']): dict(c) for c in existing_clusters}
    old_member_cluster = {str(m['alert_id']): str(m['cluster_id']) for m in existing_members}

    cluster_upserts: list[dict[str, Any]] = []
    member_sets: dict[str, list[str]] = {}
    history: list[dict[str, Any]] = []
    created = 0
    updated = 0

    desired_fps: set[str] = set()
    new_member_cluster: dict[str, str] = {}

    for desired in desired_clusters:
        fp = str(desired['fingerprint'])
        desired_fps.add(fp)
        existing = existing_by_fp.get(fp)
        # An archived cluster with the same fingerprint is REVIVED (the same
        # root cause recurred): reuse its row id so memberships stay FK-valid,
        # but record it as a (re-)creation.
        revived = existing is not None and str(existing.get('status') or 'active') == 'archived'
        if existing is None or revived:
            cluster_id = str(existing['id']) if revived else id_factory()
            is_new = not revived
            created += 1
            history.append({
                'change_type': 'cluster_created',
                'cluster_id': cluster_id,
                'old_value': None,
                'new_value': {
                    'fingerprint': fp,
                    'derived_severity': desired['derived_severity'],
                    'member_count': desired['member_count'],
                },
                'reason': 'recurred root-cause fingerprint' if revived else 'new root-cause fingerprint',
                'alert_ids': list(desired['member_alert_ids']),
            })
        else:
            cluster_id = str(existing['id'])
            is_new = False
            updated += 1
            for change in _cluster_changed(existing, desired):
                change['cluster_id'] = cluster_id
                change['alert_ids'] = list(desired['member_alert_ids'])
                history.append(change)

        cluster_upserts.append({'id': cluster_id, 'is_new': is_new, 'cluster': desired})
        member_sets[cluster_id] = list(desired['member_alert_ids'])
        for alert_id in desired['member_alert_ids']:
            new_member_cluster[str(alert_id)] = cluster_id

        # Membership change detection for this cluster.
        old_ids = {aid for aid, cid in old_member_cluster.items() if cid == cluster_id}
        new_ids = set(str(a) for a in desired['member_alert_ids'])
        if old_ids != new_ids:
            history.append({
                'change_type': 'membership_changed',
                'cluster_id': cluster_id,
                'old_value': {'alert_ids': sorted(old_ids)},
                'new_value': {'alert_ids': sorted(new_ids)},
                'reason': 'membership recomputed',
                'alert_ids': sorted(new_ids | old_ids),
            })

    # Alerts that left ALL clusters (now inactive or singleton).
    member_deletes = [aid for aid in old_member_cluster if aid not in new_member_cluster]

    # Currently-ACTIVE clusters no longer desired -> archive (0 members). Already
    # archived clusters that were loaded for revival are left untouched here.
    cluster_archives: list[str] = []
    for fp, existing in existing_by_fp.items():
        if fp not in desired_fps and str(existing.get('status') or 'active') == 'active':
            cluster_id = str(existing['id'])
            cluster_archives.append(cluster_id)
            history.append({
                'change_type': 'cluster_archived',
                'cluster_id': cluster_id,
                'old_value': {'fingerprint': fp, 'member_count': existing.get('member_count')},
                'new_value': {'status': 'archived'},
                'reason': 'no active members remain',
                'alert_ids': [],
            })

    return {
        'cluster_upserts': cluster_upserts,
        'member_sets': member_sets,
        'member_deletes': member_deletes,
        'cluster_archives': cluster_archives,
        'history': history,
        'counts': {
            'clusters_created': created,
            'clusters_updated': updated,
            'clusters_total': len(desired_clusters),
            'unclustered_count': len(unclustered_alert_ids),
        },
    }


# --------------------------------------------------------------------------
# DB apply layer
# --------------------------------------------------------------------------
def _load_existing(connection: Any, workspace_id: str, desired_fingerprints: list[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clusters = [
        dict(r) for r in connection.execute(
            'SELECT id, fingerprint, derived_severity, recommendation_category, member_count, status '
            "FROM alert_clusters WHERE workspace_id = %s AND status = 'active'",
            (workspace_id,),
        ).fetchall()
    ]
    # Also load ARCHIVED clusters whose fingerprint recurred this run, so a recurring
    # root cause revives its original cluster row (keeps FK-valid memberships) instead
    # of colliding on the workspace+fingerprint unique index.
    if desired_fingerprints:
        clusters.extend(
            dict(r) for r in connection.execute(
                'SELECT id, fingerprint, derived_severity, recommendation_category, member_count, status '
                "FROM alert_clusters WHERE workspace_id = %s AND status = 'archived' AND fingerprint = ANY(%s)",
                (workspace_id, list(desired_fingerprints)),
            ).fetchall()
        )
    members = [
        dict(r) for r in connection.execute(
            'SELECT alert_id, cluster_id FROM alert_cluster_members WHERE workspace_id = %s',
            (workspace_id,),
        ).fetchall()
    ]
    return clusters, members


def _write_history(connection: Any, *, workspace_id: str, run_id: str, entry: dict[str, Any], actor: str, actor_user_id: Optional[str]) -> None:
    connection.execute(
        '''
        INSERT INTO alert_cluster_history
            (id, workspace_id, cluster_id, triage_run_id, change_type, actor, actor_user_id, old_value, new_value, reason, alert_ids, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, NOW())
        ''',
        (
            str(uuid.uuid4()), workspace_id, entry.get('cluster_id'), run_id, entry['change_type'],
            actor, actor_user_id,
            pilot._json_dumps(entry.get('old_value')) if entry.get('old_value') is not None else None,
            pilot._json_dumps(entry.get('new_value')) if entry.get('new_value') is not None else None,
            entry.get('reason'), pilot._json_dumps(entry.get('alert_ids') or []),
        ),
    )


def apply_plan(connection: Any, *, workspace_id: str, plan: dict[str, Any], run_id: str, actor: str, actor_user_id: Optional[str], now: Any) -> None:
    """Execute a reconciliation plan against the database (workspace-scoped)."""
    # 1. Upsert clusters.
    for upsert in plan['cluster_upserts']:
        c = upsert['cluster']
        connection.execute(
            '''
            INSERT INTO alert_clusters
                (id, workspace_id, fingerprint, title, derived_severity, severity_factors, confidence,
                 confidence_band, confidence_factors, reason_codes, recommendation_category, recommendation_summary,
                 recommendation_source, primary_asset_id, primary_asset_name, chain_id, detection_family,
                 member_count, status, first_seen_at, last_seen_at, last_triage_run_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, 'active',
                    to_timestamp(%s), to_timestamp(%s), %s, NOW(), NOW())
            ON CONFLICT (workspace_id, fingerprint) DO UPDATE SET
                title = EXCLUDED.title,
                derived_severity = EXCLUDED.derived_severity,
                severity_factors = EXCLUDED.severity_factors,
                confidence = EXCLUDED.confidence,
                confidence_band = EXCLUDED.confidence_band,
                confidence_factors = EXCLUDED.confidence_factors,
                reason_codes = EXCLUDED.reason_codes,
                recommendation_category = EXCLUDED.recommendation_category,
                recommendation_summary = EXCLUDED.recommendation_summary,
                recommendation_source = EXCLUDED.recommendation_source,
                primary_asset_id = EXCLUDED.primary_asset_id,
                primary_asset_name = EXCLUDED.primary_asset_name,
                chain_id = EXCLUDED.chain_id,
                detection_family = EXCLUDED.detection_family,
                member_count = EXCLUDED.member_count,
                status = 'active',
                last_seen_at = EXCLUDED.last_seen_at,
                last_triage_run_id = EXCLUDED.last_triage_run_id,
                updated_at = NOW()
            ''',
            (
                upsert['id'], workspace_id, c['fingerprint'], c['title'], c['derived_severity'],
                pilot._json_dumps(c['severity_factors']), c['confidence'], c['confidence_band'],
                pilot._json_dumps(c['confidence_factors']), pilot._json_dumps(c['reason_codes']),
                c['recommendation_category'], c['recommendation_summary'],
                c.get('recommendation_source', 'rules_based'), c.get('primary_asset_id'),
                c.get('primary_asset_name'), c.get('chain_id'), c.get('detection_family'),
                c['member_count'],
                c.get('first_seen_epoch'), c.get('last_seen_epoch'), run_id,
            ),
        )

    # 2. Delete memberships for alerts that left all clusters.
    if plan['member_deletes']:
        connection.execute(
            'DELETE FROM alert_cluster_members WHERE workspace_id = %s AND alert_id = ANY(%s::uuid[])',
            (workspace_id, list(plan['member_deletes'])),
        )

    # 3. Upsert desired memberships (moves an alert to its correct cluster).
    for cluster_id, alert_ids in plan['member_sets'].items():
        for alert_id in alert_ids:
            connection.execute(
                '''
                INSERT INTO alert_cluster_members (id, workspace_id, cluster_id, alert_id, triage_run_id, added_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (workspace_id, alert_id) DO UPDATE SET
                    cluster_id = EXCLUDED.cluster_id,
                    triage_run_id = EXCLUDED.triage_run_id
                ''',
                (str(uuid.uuid4()), workspace_id, cluster_id, alert_id, run_id),
            )

    # 4. Archive clusters that lost all members, and refresh member_count.
    if plan['cluster_archives']:
        connection.execute(
            "UPDATE alert_clusters SET status = 'archived', member_count = 0, updated_at = NOW() "
            'WHERE workspace_id = %s AND id = ANY(%s::uuid[])',
            (workspace_id, list(plan['cluster_archives'])),
        )

    # 5. History.
    for entry in plan['history']:
        _write_history(connection, workspace_id=workspace_id, run_id=run_id, entry=entry, actor=actor, actor_user_id=actor_user_id)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _active_run(connection: Any, workspace_id: str) -> Optional[dict[str, Any]]:
    row = connection.execute(
        "SELECT id, started_at FROM alert_triage_runs WHERE workspace_id = %s AND status = 'running' ORDER BY started_at DESC LIMIT 1",
        (workspace_id,),
    ).fetchone()
    return dict(row) if row else None


def run_triage(
    connection: Any,
    *,
    workspace_id: str,
    user_id: Optional[str],
    triggered_by: str = 'system',
    request: Any = None,
    provider_override: Any = None,
    config: dict[str, Any] | None = None,
    now: Any = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Connection-level triage run. Deterministic clustering + optional AI
    enrichment, persisted idempotently with full audit. Auth is the caller's job."""
    if not schema_ready(connection):
        raise pilot.HTTPException(status_code=503, detail='Alert triage storage is provisioning. Try again shortly.')

    from services.api.app import ai_triage

    conf = config if config is not None else ai_triage.triage_config()
    now = now or pilot.utc_now()

    existing_run = _active_run(connection, workspace_id)
    if existing_run is not None:
        return {'status': 'already_running', 'run_id': str(existing_run['id']), 'triage_run': None}

    run_id = str(uuid.uuid4())
    actor = 'user' if triggered_by == 'user' else 'system'
    connection.execute(
        '''
        INSERT INTO alert_triage_runs (id, workspace_id, status, mode, triggered_by, triggered_by_user_id, started_at, created_at)
        VALUES (%s, %s, 'running', 'rules_based', %s, %s, NOW(), NOW())
        ''',
        (run_id, workspace_id, triggered_by, user_id if triggered_by == 'user' else None),
    )
    _write_history(connection, workspace_id=workspace_id, run_id=run_id,
                   entry={'change_type': 'triage_run_started', 'cluster_id': None, 'reason': triggered_by, 'alert_ids': []},
                   actor=actor, actor_user_id=user_id)
    _audit(connection, request=request, action='alerts.triage.run_started', workspace_id=workspace_id,
           user_id=user_id, metadata={'run_id': run_id, 'triggered_by': triggered_by})

    try:
        active_alerts, truncated = load_active_alerts(connection, workspace_id=workspace_id)
        normalized = [clustering.normalize_alert(a) for a in active_alerts]
        result = clustering.build_clusters(normalized)
        clusters = result['clusters']

        enrichment = ai_enrichment.enrich_clusters(clusters, config=conf, provider_override=provider_override)
        mode = 'ai_assisted' if enrichment['ai_used'] > 0 else 'rules_based'

        existing_clusters, existing_members = _load_existing(
            connection, workspace_id, [c['fingerprint'] for c in clusters])
        plan = reconcile(existing_clusters, existing_members, clusters,
                         unclustered_alert_ids=result['unclustered_alert_ids'])
        apply_plan(connection, workspace_id=workspace_id, plan=plan, run_id=run_id,
                   actor=actor, actor_user_id=user_id, now=now)

        counts = plan['counts']
        connection.execute(
            '''
            UPDATE alert_triage_runs
            SET status = 'completed', mode = %s, alerts_considered = %s, clusters_total = %s,
                clusters_created = %s, clusters_updated = %s, unclustered_count = %s,
                ai_enriched_count = %s, completed_at = NOW()
            WHERE id = %s AND workspace_id = %s
            ''',
            (mode, len(active_alerts), counts['clusters_total'], counts['clusters_created'],
             counts['clusters_updated'], counts['unclustered_count'], enrichment['ai_used'],
             run_id, workspace_id),
        )
        _write_history(connection, workspace_id=workspace_id, run_id=run_id,
                       entry={'change_type': 'triage_run_completed', 'cluster_id': None,
                              'new_value': {'clusters_total': counts['clusters_total'], 'mode': mode},
                              'reason': 'triage completed', 'alert_ids': []},
                       actor=actor, actor_user_id=user_id)
        _audit(connection, request=request, action='alerts.triage.run_completed', workspace_id=workspace_id,
               user_id=user_id, metadata={'run_id': run_id, 'mode': mode, 'clusters_total': counts['clusters_total'],
                                          'clusters_created': counts['clusters_created'],
                                          'unclustered_count': counts['unclustered_count'],
                                          'alerts_considered': len(active_alerts),
                                          'ai_error': enrichment.get('ai_error')})
        if commit:
            connection.commit()
        return {
            'status': 'completed',
            'run_id': run_id,
            'triage_run': {
                'id': run_id, 'status': 'completed', 'mode': mode,
                'alerts_considered': len(active_alerts), 'truncated': truncated,
                **counts, 'ai_error': enrichment.get('ai_error'),
            },
        }
    except pilot.HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail_run(connection, workspace_id=workspace_id, run_id=run_id, exc=exc, actor=actor,
                  user_id=user_id, request=request, commit=commit)
        logger.exception('event=alert_triage_run_failed workspace_id=%s run_id=%s', workspace_id, run_id)
        raise pilot.HTTPException(status_code=500, detail='Alert triage run failed.') from None


def _fail_run(connection: Any, *, workspace_id: str, run_id: str, exc: Exception, actor: str, user_id: Optional[str], request: Any, commit: bool) -> None:
    try:
        try:
            connection.rollback()
        except Exception:
            pass
        connection.execute(
            "UPDATE alert_triage_runs SET status = 'failed', error_code = %s, error_detail = %s, completed_at = NOW() "
            'WHERE id = %s AND workspace_id = %s',
            (exc.__class__.__name__, str(exc)[:500], run_id, workspace_id),
        )
        _write_history(connection, workspace_id=workspace_id, run_id=run_id,
                       entry={'change_type': 'triage_run_failed', 'cluster_id': None,
                              'new_value': {'error_code': exc.__class__.__name__}, 'reason': 'triage failed', 'alert_ids': []},
                       actor=actor, actor_user_id=user_id)
        _audit(connection, request=request, action='alerts.triage.run_failed', workspace_id=workspace_id,
               user_id=user_id, metadata={'run_id': run_id, 'error_code': exc.__class__.__name__})
        if commit:
            connection.commit()
    except Exception:
        logger.warning('event=alert_triage_fail_bookkeeping_failed run_id=%s', run_id)


def _audit(connection: Any, *, request: Any, action: str, workspace_id: str, user_id: Optional[str], metadata: dict[str, Any]) -> None:
    try:
        pilot.log_audit(connection, action=action, entity_type='alert_triage', entity_id=str(metadata.get('run_id') or workspace_id),
                        request=request, user_id=user_id, workspace_id=workspace_id, metadata=metadata)
    except Exception:  # pragma: no cover - audit must never break the operation
        logger.warning('event=alert_triage_audit_failed action=%s workspace_id=%s', action, workspace_id)


# --------------------------------------------------------------------------
# Request wrapper (auth + role check)
# --------------------------------------------------------------------------
def request_triage(request: Any) -> dict[str, Any]:
    """Run Triage command endpoint. Requires the workspace monitoring-config
    permission (a real backend command, role-checked, idempotent)."""
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        # Role check: manual triage is an operator action.
        user, workspace_context = pilot.require_ops_rbac_guard(connection, request)
        return run_triage(
            connection, workspace_id=workspace_context['workspace_id'], user_id=user['id'],
            triggered_by='user', request=request, now=pilot.utc_now(), commit=True,
        )
