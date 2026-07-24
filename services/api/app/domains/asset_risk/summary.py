"""Canonical workspace-level asset risk summary.

Single source of truth for the right-side "AI Asset Risk Assessor" panel on
Screen 3 (and any dashboard integration). Everything here is workspace-scoped and
derived from the latest persisted assessment per asset — the frontend never
recomputes it. Reserve coverage is aggregated only from *verified* reserve
evidence; assets without verified reserves are reported as insufficient evidence,
never folded silently into a healthy number.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from services.api.app.domains.asset_risk import config as arc
from services.api.app.domains.asset_risk.scoring import risk_level_for_score  # noqa: F401  (re-export convenience)

# Finding types that count as an "anomaly" for the panel's anomaly warnings.
_ANOMALY_FINDING_TYPES = (
    'asset_reserve_shortfall',
    'asset_price_deviation',
    'asset_oracle_disagreement',
    'asset_ledger_mismatch',
    'asset_supply_anomaly',
    'asset_over_collateralization',
)
_MONITORING_GAP_FINDING_TYPES = (
    'asset_monitoring_gap',
    'asset_reserve_feed_missing',
    'asset_reserve_feed_stale',
)
_SEVERITY_RANK = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}


def _table_exists(connection: Any, name: str) -> bool:
    try:
        row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS ok', (f'public.{name}',)).fetchone()
        return bool((row or {}).get('ok'))
    except Exception:
        return False


def _f(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _reserve_backed_count(connection: Any, workspace_id: str) -> int:
    """Number of enabled assets for which reserve backing is applicable.

    Reserve backing applies to an asset when its RWA type requires reserves OR
    it has an explicit reserve feed configured. Wallet / non-reserve assets are
    excluded, so an empty result means "no reserve-backed assets configured" —
    never "missing reserve evidence"."""
    reserve_types = [k for k, v in arc.RWA_ASSET_TYPES.items() if v.get('reserve_required')]
    try:
        row = connection.execute(
            '''
            SELECT COUNT(*) AS n FROM assets
            WHERE workspace_id = %s AND deleted_at IS NULL AND enabled = TRUE
              AND (
                  (reserve_feed_type IS NOT NULL AND lower(reserve_feed_type) <> 'none')
                  OR lower(COALESCE(rwa_asset_type, '')) = ANY(%s)
              )
            ''',
            (workspace_id, reserve_types),
        ).fetchone() or {}
        return int(row.get('n') or 0)
    except Exception:
        return 0


def _reserve_coverage_absent(reserve_backed_count: int, *, assets_included: int = 0, last_verified_at: Any = None) -> dict[str, Any]:
    """Reserve coverage block when no verified aggregate can be computed.

    Distinguishes "no reserve-backed assets configured" (not_configured) from
    "reserve-backed assets exist but no verified evidence" (insufficient_evidence).
    Never emits a 0% from an empty denominator."""
    status_value = 'insufficient_evidence' if reserve_backed_count > 0 else 'not_configured'
    return {
        'coverage_percent': None,
        'status': status_value,
        'assets_included': assets_included,
        'reserve_backed_count': reserve_backed_count,
        'last_verified_at': last_verified_at.isoformat() if last_verified_at is not None else None,
    }


def _empty_summary(total_assets: int, total_value: Any, reserve_backed_count: int, *, reason: str) -> dict[str, Any]:
    reserve_coverage = _reserve_coverage_absent(reserve_backed_count)
    return {
        'total_assets': total_assets,
        'total_protected_value_usd': _f(total_value) or 0.0,
        'assessed_assets': 0,
        'reserve_backed_count': reserve_backed_count,
        'risk_level_counts': {'low': 0, 'medium': 0, 'high': 0, 'critical': 0},
        'reserve_coverage': reserve_coverage,
        'anomaly_warnings': {'assets': 0, 'highest_severity': None},
        'monitoring_gaps': {
            'assets': 0,
            'missing_reserve_feed': 0,
            'stale_oracle': 0,
            'no_target': 0,
            'incomplete_provider': 0,
        },
        'stale_feed_count': 0,
        'latest_assessment_at': None,
        'data_completeness': 0.0,
        'confidence': 0.0,
        'assessment_status': 'not_started',
        'ai_summary': _empty_narrative(total_assets, reserve_backed_count),
        'ai_summary_source': 'deterministic',
        'score_version': 'asset-risk-v1',
    }


def _empty_narrative(total_assets: int, reserve_backed_count: int) -> str:
    """Deterministic panel text for a workspace with no completed assessments."""
    if total_assets == 0:
        return ('No protected assets are registered yet. Add a wallet, smart contract, or '
                'reserve-backed asset to begin monitoring.')
    lead = 'No assessment has completed. Run the first assessment to establish monitoring coverage.'
    if reserve_backed_count == 0:
        return 'No reserve-backed assets are configured. ' + lead
    return lead


def build_risk_summary(connection: Any, *, workspace_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or arc.assessor_config()

    totals = connection.execute(
        '''
        SELECT COUNT(*) AS total_assets, COALESCE(SUM(value_usd), 0) AS total_value
        FROM assets WHERE workspace_id = %s AND deleted_at IS NULL AND enabled = TRUE
        ''',
        (workspace_id,),
    ).fetchone() or {}
    total_assets = int(totals.get('total_assets') or 0)
    total_value = totals.get('total_value') or 0
    reserve_backed_count = _reserve_backed_count(connection, workspace_id)

    if not _table_exists(connection, 'asset_risk_assessments'):
        empty = _empty_summary(total_assets, total_value, reserve_backed_count, reason='Assessment storage is provisioning.')
        empty['worker'] = worker_health(connection, workspace_id=workspace_id, config=cfg, latest_assessment_at=None)
        return empty

    # Latest assessment per asset (workspace-scoped).
    rows = connection.execute(
        '''
        SELECT DISTINCT ON (a.asset_id)
            a.asset_id, a.risk_score, a.risk_level, a.confidence, a.data_completeness,
            a.reserve_status, a.reserve_value_usd, a.liability_value_usd, a.reserve_coverage_percent,
            a.monitoring_health, a.status, a.assessed_at, a.feed_freshness
        FROM asset_risk_assessments a
        WHERE a.workspace_id = %s
        ORDER BY a.asset_id, a.assessed_at DESC
        ''',
        (workspace_id,),
    ).fetchall()

    if not rows:
        empty = _empty_summary(total_assets, total_value, reserve_backed_count, reason='Awaiting the first assessment cycle.')
        empty['worker'] = worker_health(connection, workspace_id=workspace_id, config=cfg, latest_assessment_at=None)
        return empty

    risk_counts = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
    completeness_sum = 0.0
    confidence_sum = 0.0
    latest_assessment_at = None
    reserve_num = Decimal('0')
    reserve_den = Decimal('0')
    reserve_assets = 0
    reserve_applicable_assessments = 0
    degraded_assessments = 0
    last_verified_at = None
    reserve_has_critical = False
    reserve_has_warning = False

    for row in rows:
        level = str(row.get('risk_level') or 'low').lower()
        if level in risk_counts:
            risk_counts[level] += 1
        completeness_sum += float(row.get('data_completeness') or 0)
        confidence_sum += float(row.get('confidence') or 0)
        if str(row.get('status') or '') in ('degraded', 'partial'):
            degraded_assessments += 1
        assessed_at = row.get('assessed_at')
        if assessed_at is not None and (latest_assessment_at is None or assessed_at > latest_assessment_at):
            latest_assessment_at = assessed_at
        rstatus = str(row.get('reserve_status') or '')
        # Reserve is "applicable" to any assessment whose status is a reserve
        # verdict (not a wallet / not-applicable). insufficient_evidence counts as
        # reserve-backed-but-unverified — it must not read as not_configured.
        if rstatus not in ('', 'not_applicable', 'not_required'):
            reserve_applicable_assessments += 1
        rv = row.get('reserve_value_usd')
        lv = row.get('liability_value_usd')
        if rstatus in ('healthy', 'warning', 'critical', 'over_collateralized') and rv is not None and lv is not None:
            reserve_num += Decimal(str(rv))
            reserve_den += Decimal(str(lv))
            reserve_assets += 1
            if last_verified_at is None or (assessed_at is not None and assessed_at > last_verified_at):
                last_verified_at = assessed_at
            if rstatus == 'critical':
                reserve_has_critical = True
            elif rstatus == 'warning':
                reserve_has_warning = True

    assessed_assets = len(rows)
    # Reserve is backed if configured (assets table) OR any assessment carried a
    # reserve verdict. Guards against a not_configured verdict when an asset's
    # reserve is required-but-unverified.
    effective_reserve_backed = max(reserve_backed_count, reserve_applicable_assessments)

    # Aggregate reserve coverage from verified evidence only. Never emit a 0% from
    # an empty denominator; distinguish not_configured from insufficient_evidence.
    if reserve_assets == 0 or reserve_den <= 0:
        reserve_coverage = _reserve_coverage_absent(
            effective_reserve_backed, assets_included=reserve_assets, last_verified_at=last_verified_at,
        )
    else:
        coverage_percent = (reserve_num / reserve_den * Decimal('100')).quantize(Decimal('0.01'))
        if reserve_has_critical:
            status_value = 'critical'
        elif reserve_has_warning or coverage_percent < Decimal('100'):
            status_value = 'warning'
        else:
            status_value = 'healthy'
        reserve_coverage = {
            'coverage_percent': _f(coverage_percent),
            'status': status_value,
            'assets_included': reserve_assets,
            'reserve_backed_count': effective_reserve_backed,
            'last_verified_at': last_verified_at.isoformat() if last_verified_at is not None else None,
        }

    # Active findings aggregation (anomalies, monitoring gaps, stale feeds).
    anomaly_assets = 0
    highest_severity = None
    gap_assets = 0
    missing_reserve_feed = 0
    stale_oracle = 0
    no_target = 0
    if _table_exists(connection, 'asset_risk_findings'):
        finding_rows = connection.execute(
            '''
            SELECT asset_id, finding_type, severity FROM asset_risk_findings
            WHERE workspace_id = %s AND status = 'active'
            ''',
            (workspace_id,),
        ).fetchall()
        anomaly_asset_ids: set[str] = set()
        gap_asset_ids: set[str] = set()
        for fr in finding_rows:
            ftype = str(fr.get('finding_type') or '')
            sev = str(fr.get('severity') or 'medium').lower()
            if highest_severity is None or _SEVERITY_RANK.get(sev, 0) > _SEVERITY_RANK.get(highest_severity, 0):
                highest_severity = sev
            if ftype in _ANOMALY_FINDING_TYPES:
                anomaly_asset_ids.add(str(fr.get('asset_id')))
            if ftype in _MONITORING_GAP_FINDING_TYPES:
                gap_asset_ids.add(str(fr.get('asset_id')))
            if ftype == 'asset_reserve_feed_missing':
                missing_reserve_feed += 1
            if ftype == 'asset_reserve_feed_stale':
                stale_oracle += 1
            if ftype == 'asset_monitoring_gap':
                no_target += 1
        anomaly_assets = len(anomaly_asset_ids)
        gap_assets = len(gap_asset_ids)

    stale_feed_count = missing_reserve_feed + stale_oracle
    worker = worker_health(connection, workspace_id=workspace_id, config=cfg, latest_assessment_at=latest_assessment_at)
    assessment_status = _rollup_assessment_status(
        total_assets=total_assets, assessed_assets=assessed_assets, degraded=degraded_assessments,
        active_jobs=int(worker.get('queued', 0)) + int(worker.get('running', 0)),
        latest_assessment_at=latest_assessment_at, stale_seconds=int(cfg['assessment_stale_seconds']),
    )

    summary = {
        'total_assets': total_assets,
        'total_protected_value_usd': _f(total_value) or 0.0,
        'assessed_assets': assessed_assets,
        'reserve_backed_count': effective_reserve_backed,
        'risk_level_counts': risk_counts,
        'reserve_coverage': reserve_coverage,
        'anomaly_warnings': {'assets': anomaly_assets, 'highest_severity': highest_severity},
        'monitoring_gaps': {
            'assets': gap_assets,
            'missing_reserve_feed': missing_reserve_feed,
            'stale_oracle': stale_oracle,
            'no_target': no_target,
            'incomplete_provider': max(0, gap_assets - no_target),
        },
        'stale_feed_count': stale_feed_count,
        'latest_assessment_at': latest_assessment_at.isoformat() if latest_assessment_at is not None else None,
        'data_completeness': round(completeness_sum / assessed_assets, 3) if assessed_assets else 0.0,
        'confidence': round(confidence_sum / assessed_assets, 3) if assessed_assets else 0.0,
        'assessment_status': assessment_status,
        'worker': worker,
        'score_version': 'asset-risk-v1',
    }
    summary['ai_summary'] = build_summary_narrative(summary)
    summary['ai_summary_source'] = 'deterministic'
    return summary


def _rollup_assessment_status(
    *, total_assets: int, assessed_assets: int, degraded: int, active_jobs: int,
    latest_assessment_at: Any, stale_seconds: int,
) -> str:
    """Workspace-level assessment status for the AI panel.

    One of: not_started | running | partial | stale | complete. Fail-closed: a
    degraded assessment or an incompletely-assessed workspace reads as ``partial``,
    never ``complete``."""
    if active_jobs > 0:
        return 'running'
    if total_assets == 0 or assessed_assets == 0:
        return 'not_started'
    if latest_assessment_at is not None and stale_seconds > 0:
        try:
            from services.api.app import pilot
            age = (pilot.utc_now() - latest_assessment_at).total_seconds()
            if age > stale_seconds:
                return 'stale'
        except Exception:
            pass
    if degraded > 0:
        return 'partial'
    if assessed_assets < total_assets:
        return 'partial'
    return 'complete'


def worker_health(connection: Any, *, workspace_id: str, config: dict[str, Any], latest_assessment_at: Any) -> dict[str, Any]:
    """Assessment worker visibility for the panel (workspace-scoped).

    Reports whether the background worker is enabled, the queue depth, whether a
    job is running, the last successful assessment time, and the most recent
    worker error. When the worker is disabled, on-demand assessments still run
    inline — the panel uses ``enabled`` to explain the difference truthfully."""
    health = {
        'enabled': bool(config.get('enabled')),
        'queued': 0,
        'running': 0,
        'failed': 0,
        'last_completed_at': latest_assessment_at.isoformat() if latest_assessment_at is not None else None,
        'last_error': None,
        'last_error_at': None,
    }
    if not _table_exists(connection, 'asset_risk_jobs'):
        return health
    try:
        rows = connection.execute(
            '''
            SELECT status, COUNT(*) AS n FROM asset_risk_jobs
            WHERE workspace_id = %s AND status IN ('queued', 'running', 'failed')
            GROUP BY status
            ''',
            (workspace_id,),
        ).fetchall()
        for r in rows:
            key = str(r.get('status') or '')
            if key in health:
                health[key] = int(r.get('n') or 0)
        err = connection.execute(
            '''
            SELECT last_error, updated_at FROM asset_risk_jobs
            WHERE workspace_id = %s AND status = 'failed' AND last_error IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            ''',
            (workspace_id,),
        ).fetchone()
        if err is not None:
            health['last_error'] = str(err.get('last_error') or '')[:300] or None
            le_at = err.get('updated_at')
            health['last_error_at'] = le_at.isoformat() if le_at is not None else None
    except Exception:
        return health
    return health


def build_summary_narrative(summary: dict[str, Any]) -> str:
    """Concise, evidence-grounded narrative for the panel (deterministic).

    Example: "Two assets require review. One reserve feed is stale and one asset
    exceeded its 30-day price deviation baseline."
    """
    parts: list[str] = []
    counts = summary.get('risk_level_counts') or {}
    at_risk = int(counts.get('high', 0)) + int(counts.get('critical', 0))
    anomalies = int((summary.get('anomaly_warnings') or {}).get('assets') or 0)
    gaps = int((summary.get('monitoring_gaps') or {}).get('assets') or 0)
    stale = int(summary.get('stale_feed_count') or 0)
    reserve = summary.get('reserve_coverage') or {}
    reserve_status = reserve.get('status')

    # Reserve context that must never imply missing evidence for asset sets that
    # simply have no reserve-backed assets.
    reserve_note = ''
    if reserve_status == 'not_configured':
        reserve_note = 'No reserve-backed assets are configured.'
    elif reserve_status == 'insufficient_evidence':
        reserve_note = 'Reserve coverage cannot be verified for the current asset set.'

    if at_risk == 0 and anomalies == 0 and gaps == 0 and stale == 0:
        cov = reserve.get('coverage_percent')
        if reserve_status == 'healthy' and cov is not None:
            return f'All assessed assets are within expected ranges. Aggregate reserve coverage is {cov:.0f}%.'
        if reserve_note:
            return (reserve_note + ' No active findings across assessed assets.').strip()
        return 'All assessed assets are within expected ranges with no active findings.'

    if at_risk > 0:
        parts.append(f'{at_risk} asset(s) require review')
    if reserve_status == 'critical':
        parts.append('aggregate reserve coverage is below the required minimum')
    elif reserve_status == 'warning':
        parts.append('aggregate reserve coverage is slightly below target')
    if stale > 0:
        parts.append(f'{stale} reserve/oracle feed(s) are missing or stale')
    if anomalies > 0:
        parts.append(f'{anomalies} asset(s) show active market/reserve anomalies')
    if gaps > 0:
        parts.append(f'{gaps} asset(s) have monitoring gaps')

    if not parts:
        return (reserve_note or 'Assessment complete. Review flagged assets for details.').strip()
    lead = parts[0][0].upper() + parts[0][1:]
    rest = parts[1:]
    body = lead + ('.' if not rest else '. ' + '; '.join(rest)[0].upper() + '; '.join(rest)[1:] + '.')
    # Prefix the reserve note for not_configured sets so a wallet-only workspace
    # never reads as "missing reserve evidence".
    if reserve_status == 'not_configured':
        return (reserve_note + ' ' + body).strip()
    return body


def build_risk_summary_for_request(request: Any) -> dict[str, Any]:
    """Endpoint helper: authenticate, resolve workspace, build the canonical summary."""
    from services.api.app import pilot

    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        summary = build_risk_summary(connection, workspace_id=workspace_context['workspace_id'])
        return {'summary': summary, 'workspace': workspace_context['workspace']}
