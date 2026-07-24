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


def _empty_summary(total_assets: int, total_value: Any, *, reason: str) -> dict[str, Any]:
    return {
        'total_assets': total_assets,
        'total_protected_value_usd': _f(total_value) or 0.0,
        'assessed_assets': 0,
        'risk_level_counts': {'low': 0, 'medium': 0, 'high': 0, 'critical': 0},
        'reserve_coverage': {
            'coverage_percent': None,
            'status': 'insufficient_evidence',
            'assets_included': 0,
            'last_verified_at': None,
        },
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
        'ai_summary': ('No assessments yet — the Asset Risk Assessor has not produced results for this workspace. '
                       + reason).strip(),
        'ai_summary_source': 'deterministic',
        'score_version': 'asset-risk-v1',
    }


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

    if not _table_exists(connection, 'asset_risk_assessments'):
        return _empty_summary(total_assets, total_value, reason='Assessment storage is provisioning.')

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
        return _empty_summary(total_assets, total_value, reason='Awaiting the first assessment cycle.')

    risk_counts = {'low': 0, 'medium': 0, 'high': 0, 'critical': 0}
    completeness_sum = 0.0
    confidence_sum = 0.0
    latest_assessment_at = None
    reserve_num = Decimal('0')
    reserve_den = Decimal('0')
    reserve_assets = 0
    last_verified_at = None
    reserve_has_critical = False
    reserve_has_warning = False

    for row in rows:
        level = str(row.get('risk_level') or 'low').lower()
        if level in risk_counts:
            risk_counts[level] += 1
        completeness_sum += float(row.get('data_completeness') or 0)
        confidence_sum += float(row.get('confidence') or 0)
        assessed_at = row.get('assessed_at')
        if assessed_at is not None and (latest_assessment_at is None or assessed_at > latest_assessment_at):
            latest_assessment_at = assessed_at
        rstatus = str(row.get('reserve_status') or '')
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

    # Aggregate reserve coverage from verified evidence only.
    if reserve_assets == 0 or reserve_den <= 0:
        reserve_coverage = {
            'coverage_percent': None,
            'status': 'insufficient_evidence',
            'assets_included': reserve_assets,
            'last_verified_at': last_verified_at.isoformat() if last_verified_at is not None else None,
        }
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

    summary = {
        'total_assets': total_assets,
        'total_protected_value_usd': _f(total_value) or 0.0,
        'assessed_assets': assessed_assets,
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
        'score_version': 'asset-risk-v1',
    }
    summary['ai_summary'] = build_summary_narrative(summary)
    summary['ai_summary_source'] = 'deterministic'
    return summary


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

    if at_risk == 0 and anomalies == 0 and gaps == 0 and stale == 0:
        cov = reserve.get('coverage_percent')
        if reserve.get('status') == 'healthy' and cov is not None:
            return f'All assessed assets are within expected ranges. Aggregate reserve coverage is {cov:.0f}%.'
        if reserve.get('status') == 'insufficient_evidence':
            return 'No active findings, but reserve coverage cannot be verified for the current asset set.'
        return 'All assessed assets are within expected ranges with no active findings.'

    if at_risk > 0:
        parts.append(f'{at_risk} asset(s) require review')
    reserve_status = reserve.get('status')
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
        return 'Assessment complete. Review flagged assets for details.'
    lead = parts[0][0].upper() + parts[0][1:]
    rest = parts[1:]
    if not rest:
        return lead + '.'
    second = '; '.join(rest)
    second = second[0].upper() + second[1:]
    return lead + '. ' + second + '.'


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
