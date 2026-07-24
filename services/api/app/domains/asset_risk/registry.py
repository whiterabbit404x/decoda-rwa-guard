"""Protected Asset Registry — table-ready enrichment, filtering, and details.

This layer keeps the heavy list/filter/sort logic and the registry-specific
create fields out of the oversized pilot.py route module. It:
  * merges the latest persisted risk assessment onto each asset row (the
    frontend never recomputes risk),
  * applies server-side search / filter / sort / pagination,
  * validates + persists the registry/reserve configuration fields on create,
    and enqueues an initial assessment,
  * builds the asset details / latest-assessment payloads,
  * runs an on-demand assessment through the idempotent job pattern.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from services.api.app import pilot
from services.api.app.domains.asset_risk import config as arc
from services.api.app.domains.asset_risk import service
from services.api.app.domains.asset_risk import summary as arsummary
from services.api.app.domains.asset_risk import worker

try:  # fastapi is stubbed in the offline test runner
    from fastapi import HTTPException, status
except Exception:  # pragma: no cover
    HTTPException = pilot.HTTPException  # type: ignore
    status = pilot.status  # type: ignore

_ADDRESS_RE = re.compile(r'^0x[a-fA-F0-9]{40}$')
_MAX_PAGE_SIZE = 100
_DEFAULT_PAGE_SIZE = 25

_SORTABLE = {
    'name': 'name',
    'value': 'value_usd',
    'value_usd': 'value_usd',
    'risk': 'risk_score',
    'risk_score': 'risk_score',
    'last_assessed': 'last_assessed_at',
    'last_assessed_at': 'last_assessed_at',
}


# --------------------------------------------------------------------------
# Number / value helpers
# --------------------------------------------------------------------------
def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _num(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _first_query_value(query_params: Any, key: str) -> str | None:
    if query_params is None:
        return None
    try:
        value = query_params.get(key)
    except Exception:
        return None
    if value is None:
        return None
    return str(value).strip() or None


# --------------------------------------------------------------------------
# List enrichment + server-side filter/sort/paginate
# --------------------------------------------------------------------------
def attach_risk_and_filter(
    connection: Any,
    *,
    workspace_id: str,
    assets: list[dict[str, Any]],
    query_params: Any,
) -> dict[str, Any]:
    """Merge latest risk assessment onto each asset, then filter/sort/paginate.

    Returns {assets, total, page, page_size, filtered_total, facets}. When no
    registry query params are present the full enriched set is returned so
    existing callers keep working.
    """
    asset_ids = [str(a['id']) for a in assets]
    latest = _load_latest_assessments(connection, workspace_id, asset_ids)
    finding_counts = _load_active_finding_counts(connection, workspace_id, asset_ids)

    for a in assets:
        aid = str(a['id'])
        assessment = latest.get(aid)
        rwa_type = a.get('rwa_asset_type')
        a['rwa_asset_type'] = rwa_type
        a['rwa_asset_type_label'] = arc.rwa_type_label(rwa_type) if rwa_type else pilot_asset_type_label(a.get('asset_type'))
        a['custodian'] = a.get('custodian')
        a['value_usd'] = _num(a.get('value_usd'))
        a['active_findings_count'] = int(finding_counts.get(aid, 0))
        # Whether reserve backing applies to this asset at all (type-driven or a
        # configured feed). A wallet / non-reserve asset must never be described as
        # missing reserve evidence — its reserve status is "not applicable".
        reserve_required = arc.reserve_required_for(rwa_type, a.get('reserve_feed_type'))
        a['reserve_required'] = reserve_required
        if assessment is not None:
            a['risk_score'] = int(assessment['risk_score']) if assessment.get('risk_score') is not None else None
            a['risk_level'] = assessment.get('risk_level')
            a['confidence'] = _num(assessment.get('confidence'))
            a['reserve_status'] = assessment.get('reserve_status')
            a['reserve_coverage_percent'] = _num(assessment.get('reserve_coverage_percent'))
            a['assessment_status'] = assessment.get('status')
            a['last_assessed_at'] = assessment['assessed_at'].isoformat() if assessment.get('assessed_at') is not None else None
            # Monitoring health: prefer the assessment's fail-closed value, but if
            # the asset lost its telemetry after the assessment, do not overstate.
            a['monitoring_health'] = _reconcile_health(assessment.get('monitoring_health'), a)
        else:
            a['risk_score'] = None
            a['risk_level'] = 'unassessed'
            a['confidence'] = None
            # Unassessed non-reserve assets are "not applicable", not "unknown" —
            # reserve simply does not apply. Reserve-backed assets stay null until
            # an assessment runs (frontend renders that as pending, not verified).
            a['reserve_status'] = None if reserve_required else 'not_applicable'
            a['reserve_coverage_percent'] = None
            a['assessment_status'] = 'not_assessed'
            a['last_assessed_at'] = None
            a['monitoring_health'] = _reconcile_health(None, a)

    facets = _build_facets(assets)

    search = (_first_query_value(query_params, 'search') or '').lower()
    filter_type = _first_query_value(query_params, 'asset_type')
    filter_network = _first_query_value(query_params, 'network')
    filter_risk = _first_query_value(query_params, 'risk_level')
    filter_health = _first_query_value(query_params, 'monitoring_health')
    filter_custodian = _first_query_value(query_params, 'custodian')
    sort_key = _SORTABLE.get((_first_query_value(query_params, 'sort') or '').lower(), None)
    sort_dir = (_first_query_value(query_params, 'dir') or 'desc').lower()
    has_params = any([
        search, filter_type, filter_network, filter_risk, filter_health, filter_custodian, sort_key,
        _first_query_value(query_params, 'page'), _first_query_value(query_params, 'page_size'),
    ])

    def matches(a: dict[str, Any]) -> bool:
        if search:
            hay = ' '.join(str(a.get(k) or '') for k in ('name', 'identifier', 'custodian', 'asset_symbol', 'token_symbol', 'chain_network')).lower()
            if search not in hay:
                return False
        if filter_type and filter_type != 'all' and str(a.get('rwa_asset_type') or a.get('asset_type') or '').lower() != filter_type.lower():
            return False
        if filter_network and filter_network != 'all' and str(a.get('chain_network') or '').lower() != filter_network.lower():
            return False
        if filter_risk and filter_risk != 'all' and str(a.get('risk_level') or '').lower() != filter_risk.lower():
            return False
        if filter_health and filter_health != 'all' and str(a.get('monitoring_health') or '').lower() != filter_health.lower():
            return False
        if filter_custodian and filter_custodian != 'all' and str(a.get('custodian') or '').lower() != filter_custodian.lower():
            return False
        return True

    filtered = [a for a in assets if matches(a)] if has_params else list(assets)

    if sort_key:
        reverse = sort_dir != 'asc'

        def sort_value(a: dict[str, Any]) -> Any:
            v = a.get(sort_key)
            if sort_key in ('value_usd', 'risk_score'):
                return (v is None, v if v is not None else 0)
            if sort_key == 'last_assessed_at':
                return (v is None, v or '')
            return (False, str(v or '').lower())

        filtered.sort(key=sort_value, reverse=reverse)

    filtered_total = len(filtered)
    page = max(1, _safe_int(_first_query_value(query_params, 'page'), 1))
    page_size = min(_MAX_PAGE_SIZE, max(1, _safe_int(_first_query_value(query_params, 'page_size'), _DEFAULT_PAGE_SIZE)))
    if has_params:
        start = (page - 1) * page_size
        page_items = filtered[start:start + page_size]
    else:
        page_items = filtered
        page_size = filtered_total or _DEFAULT_PAGE_SIZE

    return {
        'assets': page_items,
        'total': len(assets),
        'filtered_total': filtered_total,
        'page': page,
        'page_size': page_size,
        'facets': facets,
    }


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return default


def _reconcile_health(assessment_health: Any, asset: dict[str, Any]) -> str:
    """Never show a healthier state than current live telemetry supports."""
    ah = str(assessment_health or '').strip().lower()
    monitoring_status = str(asset.get('monitoring_status') or '')
    if monitoring_status == 'not_configured' and not asset.get('has_monitoring_target'):
        return 'not_configured'
    if ah in {'healthy', 'warning', 'critical', 'degraded', 'provisioning', 'not_configured'}:
        if ah == 'healthy' and asset.get('has_telemetry') is False:
            return 'warning'
        return ah
    return 'unknown'


def pilot_asset_type_label(asset_type: Any) -> str:
    text = str(asset_type or '').strip()
    return text.replace('-', ' ').title() if text else 'Unclassified'


def _load_latest_assessments(connection: Any, workspace_id: str, asset_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not asset_ids or not service._table_exists(connection, 'asset_risk_assessments'):
        return {}
    rows = connection.execute(
        '''
        SELECT DISTINCT ON (asset_id)
            asset_id, risk_score, risk_level, confidence, reserve_status, reserve_coverage_percent,
            monitoring_health, status, assessed_at
        FROM asset_risk_assessments
        WHERE workspace_id = %s AND asset_id = ANY(%s::uuid[])
        ORDER BY asset_id, assessed_at DESC
        ''',
        (workspace_id, asset_ids),
    ).fetchall()
    return {str(r['asset_id']): dict(r) for r in rows}


def _load_active_finding_counts(connection: Any, workspace_id: str, asset_ids: list[str]) -> dict[str, int]:
    if not asset_ids or not service._table_exists(connection, 'asset_risk_findings'):
        return {}
    rows = connection.execute(
        '''
        SELECT asset_id, COUNT(*) AS n FROM asset_risk_findings
        WHERE workspace_id = %s AND asset_id = ANY(%s::uuid[]) AND status = 'active'
        GROUP BY asset_id
        ''',
        (workspace_id, asset_ids),
    ).fetchall()
    return {str(r['asset_id']): int(r['n']) for r in rows}


def _build_facets(assets: list[dict[str, Any]]) -> dict[str, list[str]]:
    networks = sorted({str(a.get('chain_network')) for a in assets if a.get('chain_network')})
    custodians = sorted({str(a.get('custodian')) for a in assets if a.get('custodian')})
    types = sorted({str(a.get('rwa_asset_type')) for a in assets if a.get('rwa_asset_type')})
    return {'networks': networks, 'custodians': custodians, 'asset_types': types}


# --------------------------------------------------------------------------
# Create-time registry field validation + persistence
# --------------------------------------------------------------------------
def validate_registry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the registry/reserve fields. Raises HTTPException(400) on bad input."""
    def bad(field: str, message: str) -> None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={'message': message, 'field_errors': {field: message}})

    rwa_asset_type = str(payload.get('rwa_asset_type') or '').strip().lower() or None
    if rwa_asset_type is not None and rwa_asset_type not in arc.RWA_ASSET_TYPES:
        bad('rwa_asset_type', 'Asset type is not a supported RWA type.')

    reserve_feed_type = str(payload.get('reserve_feed_type') or 'none').strip().lower()
    if reserve_feed_type not in arc.RESERVE_FEED_TYPES:
        bad('reserve_feed_type', 'Reserve feed type is invalid.')

    reserve_feed_identifier = str(payload.get('reserve_feed_identifier') or '').strip() or None
    if reserve_feed_identifier:
        _validate_feed_identifier(reserve_feed_identifier, bad)

    value_usd = _to_decimal(payload.get('value_usd'))
    if value_usd is not None and value_usd < 0:
        bad('value_usd', 'Value must be zero or positive.')
    reserve_value_usd = _to_decimal(payload.get('reserve_value_usd'))
    if reserve_value_usd is not None and reserve_value_usd < 0:
        bad('reserve_value_usd', 'Reserve value must be zero or positive.')
    reference_price_usd = _to_decimal(payload.get('reference_price_usd'))
    if reference_price_usd is not None and reference_price_usd < 0:
        bad('reference_price_usd', 'Price must be zero or positive.')
    circulating_supply = _to_decimal(payload.get('circulating_supply'))
    if circulating_supply is not None and circulating_supply < 0:
        bad('circulating_supply', 'Circulating supply must be zero or positive.')

    min_ratio = _to_decimal(payload.get('reserve_min_coverage_ratio'))
    if min_ratio is not None and (min_ratio <= 0 or min_ratio > 100):
        bad('reserve_min_coverage_ratio', 'Minimum coverage ratio must be between 0 and 100.')

    interval = payload.get('reserve_update_interval_seconds')
    interval_val = None
    if interval not in (None, ''):
        try:
            interval_val = max(0, int(interval))
        except (ValueError, TypeError):
            bad('reserve_update_interval_seconds', 'Update interval must be a whole number of seconds.')

    token_decimals = payload.get('token_decimals')
    token_decimals_val = None
    if token_decimals not in (None, ''):
        try:
            token_decimals_val = int(token_decimals)
        except (ValueError, TypeError):
            bad('token_decimals', 'Token decimals must be a whole number.')
        if token_decimals_val is not None and (token_decimals_val < 0 or token_decimals_val > 36):
            bad('token_decimals', 'Token decimals must be between 0 and 36.')

    return {
        'rwa_asset_type': rwa_asset_type,
        'custodian': (str(payload.get('custodian') or '').strip() or None),
        'token_symbol': (str(payload.get('token_symbol') or payload.get('asset_symbol') or '').strip() or None),
        'token_decimals': token_decimals_val,
        'price_source': (str(payload.get('price_source') or '').strip() or None),
        'reserve_feed_type': reserve_feed_type,
        'reserve_feed_identifier': reserve_feed_identifier,
        'reserve_min_coverage_ratio': min_ratio,
        'reserve_update_interval_seconds': interval_val,
        'value_usd': value_usd,
        'reserve_value_usd': reserve_value_usd,
        'reference_price_usd': reference_price_usd,
        'circulating_supply': circulating_supply,
        'reserve_verified': bool(payload.get('reserve_verified')) and reserve_value_usd is not None,
    }


def _validate_feed_identifier(identifier: str, bad: Any) -> None:
    """SSRF guard: if the reserve feed identifier is a URL, only allow http(s)
    to non-private hosts. Non-URL identifiers (opaque IDs) are allowed as-is."""
    if '://' not in identifier:
        return
    parsed = urlparse(identifier)
    if parsed.scheme not in ('http', 'https'):
        bad('reserve_feed_identifier', 'Reserve feed URL must use http or https.')
    host = (parsed.hostname or '').lower()
    if not host or host in ('localhost', '127.0.0.1', '::1', '0.0.0.0', 'metadata.google.internal'):
        bad('reserve_feed_identifier', 'Reserve feed URL host is not allowed.')
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            bad('reserve_feed_identifier', 'Reserve feed URL must not target a private/internal address.')
    except ValueError:
        pass  # hostname, not a literal IP — acceptable


def persist_registry_fields(
    connection: Any,
    *,
    workspace_id: str,
    asset_id: str,
    payload: dict[str, Any],
    user_id: str,
) -> dict[str, Any]:
    """Apply validated registry/reserve fields to the asset and seed initial
    observations + an assessment job. Returns the validated field dict."""
    fields = validate_registry_payload(payload)
    reserve_verified_at_sql = 'reserve_verified_at = NOW()' if fields['reserve_verified'] else 'reserve_verified_at = reserve_verified_at'

    connection.execute(
        f'''
        UPDATE assets SET
            rwa_asset_type = COALESCE(%s, rwa_asset_type),
            custodian = COALESCE(%s, custodian),
            token_symbol = COALESCE(%s, token_symbol),
            token_decimals = COALESCE(%s, token_decimals),
            price_source = COALESCE(%s, price_source),
            reserve_feed_type = %s,
            reserve_feed_identifier = COALESCE(%s, reserve_feed_identifier),
            reserve_min_coverage_ratio = COALESCE(%s, reserve_min_coverage_ratio),
            reserve_update_interval_seconds = COALESCE(%s, reserve_update_interval_seconds),
            value_usd = COALESCE(%s, value_usd),
            reserve_value_usd = COALESCE(%s, reserve_value_usd),
            reference_price_usd = COALESCE(%s, reference_price_usd),
            circulating_supply = COALESCE(%s, circulating_supply),
            {reserve_verified_at_sql},
            updated_by_user_id = %s,
            updated_at = NOW()
        WHERE id = %s AND workspace_id = %s
        ''',
        (
            fields['rwa_asset_type'], fields['custodian'], fields['token_symbol'], fields['token_decimals'], fields['price_source'],
            fields['reserve_feed_type'], fields['reserve_feed_identifier'], fields['reserve_min_coverage_ratio'],
            fields['reserve_update_interval_seconds'], fields['value_usd'], fields['reserve_value_usd'],
            fields['reference_price_usd'], fields['circulating_supply'], user_id, asset_id, workspace_id,
        ),
    )

    # Seed an initial valuation snapshot from the operator-provided reference price
    # so the market baseline can begin accumulating (marked with its source).
    if fields['reference_price_usd'] is not None and service._table_exists(connection, 'asset_valuation_snapshots'):
        connection.execute(
            '''
            INSERT INTO asset_valuation_snapshots (id, workspace_id, asset_id, price_usd, source, is_estimated, observed_at)
            VALUES (%s, %s, %s, %s, 'initial_reference', FALSE, NOW())
            ''',
            (str(uuid.uuid4()), workspace_id, asset_id, fields['reference_price_usd']),
        )

    # Enqueue an initial assessment (idempotent). When on-demand assessment is
    # enabled we run it inline so the registry immediately reflects a deterministic
    # score even where the background worker is disabled; otherwise the queued job is
    # left for a healthy worker to claim. Best-effort: any failure leaves the queued
    # job behind and never blocks asset creation.
    if service._table_exists(connection, 'asset_risk_jobs'):
        enq = worker.enqueue_assessment(
            connection, workspace_id=workspace_id, asset_id=asset_id,
            trigger_source='asset_created', requested_by_user_id=user_id,
        )
        if not arc.assessor_config().get('on_demand_enabled'):
            return fields
        try:
            fresh = connection.execute(
                'SELECT * FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
                (asset_id, workspace_id),
            ).fetchone()
            if fresh is not None:
                service.assess_asset(
                    connection, workspace_id=workspace_id, asset_row=dict(fresh), trigger_source='asset_created'
                )
                connection.execute(
                    "UPDATE asset_risk_jobs SET status = 'completed', completed_at = NOW(), updated_at = NOW() WHERE id = %s AND status = 'queued'",
                    (enq['job_id'],),
                )
        except Exception:  # noqa: BLE001 - creation must not fail on initial assessment
            import logging as _logging
            _logging.getLogger(__name__).warning('event=asset_initial_assessment_deferred asset_id=%s', asset_id)
    return fields


# --------------------------------------------------------------------------
# Details + latest assessment
# --------------------------------------------------------------------------
def get_latest_assessment_payload(connection: Any, *, workspace_id: str, asset_id: str) -> dict[str, Any]:
    """Latest assessment + active findings + trend history. Truthful when absent."""
    if not service._table_exists(connection, 'asset_risk_assessments'):
        return {'assessment': None, 'status': 'provisioning', 'findings': [], 'history': [], 'valuation_history': []}
    latest = connection.execute(
        '''
        SELECT * FROM asset_risk_assessments
        WHERE workspace_id = %s AND asset_id = %s
        ORDER BY assessed_at DESC LIMIT 1
        ''',
        (workspace_id, asset_id),
    ).fetchone()
    if latest is None:
        return {'assessment': None, 'status': 'not_assessed', 'findings': [], 'history': [], 'valuation_history': []}

    findings = connection.execute(
        '''
        SELECT finding_type, severity, status, title, detail, evidence, occurrence_count, first_seen_at, last_seen_at, alert_id
        FROM asset_risk_findings
        WHERE workspace_id = %s AND asset_id = %s AND status = 'active'
        ORDER BY CASE lower(severity) WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, last_seen_at DESC
        ''',
        (workspace_id, asset_id),
    ).fetchall() if service._table_exists(connection, 'asset_risk_findings') else []

    history = connection.execute(
        '''
        SELECT risk_score, risk_level, confidence, reserve_coverage_percent, monitoring_health, status, assessed_at
        FROM asset_risk_assessments
        WHERE workspace_id = %s AND asset_id = %s
        ORDER BY assessed_at DESC LIMIT 30
        ''',
        (workspace_id, asset_id),
    ).fetchall()

    valuation_history = []
    if service._table_exists(connection, 'asset_valuation_snapshots'):
        valuation_history = connection.execute(
            '''
            SELECT price_usd, market_value_usd, source, is_estimated, observed_at
            FROM asset_valuation_snapshots
            WHERE workspace_id = %s AND asset_id = %s
            ORDER BY observed_at DESC LIMIT 60
            ''',
            (workspace_id, asset_id),
        ).fetchall()

    return {
        'assessment': pilot._json_safe_value(dict(latest)),
        'status': str(latest.get('status') or 'completed'),
        'findings': [pilot._json_safe_value(dict(f)) for f in findings],
        'history': [pilot._json_safe_value(dict(h)) for h in history],
        'valuation_history': [pilot._json_safe_value(dict(v)) for v in valuation_history],
    }


# --------------------------------------------------------------------------
# Endpoint entrypoints (thin; called from main.py)
# --------------------------------------------------------------------------
def risk_summary_endpoint(request: Any) -> dict[str, Any]:
    return arsummary.build_risk_summary_for_request(request)


def latest_assessment_endpoint(asset_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        asset = connection.execute(
            'SELECT id FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
            (asset_id, workspace_id),
        ).fetchone()
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        payload = get_latest_assessment_payload(connection, workspace_id=workspace_id, asset_id=asset_id)
        payload['workspace'] = workspace_context['workspace']
        return payload


def _resolve_execution_mode(connection: Any, config: dict[str, Any], now: Any) -> tuple[str, bool]:
    """Choose how a Run-assessment click executes, from canonical runtime facts.

    * ``on_demand``   — bounded synchronous assessment runs inline (default). Safe
                        even with the background worker down; reads stored evidence only.
    * ``background``  — on-demand is disabled but a healthy worker exists: enqueue and
                        let the worker claim it.
    * ``unavailable`` — no execution path: on-demand disabled AND no healthy worker.
                        The endpoint returns a structured 503 and creates no job.
    """
    _hb, worker_healthy = arsummary._worker_heartbeat(connection, config=config, now=now)
    if config.get('on_demand_enabled'):
        return 'on_demand', worker_healthy
    if worker_healthy:
        return 'background', worker_healthy
    return 'unavailable', worker_healthy


def trigger_assessment_endpoint(asset_id: str, request: Any) -> dict[str, Any]:
    """Run an assessment under an explicit execution policy.

    On-demand runs a bounded synchronous assessment inline for immediate feedback.
    When on-demand is disabled and no healthy worker exists there is no execution
    path, so the call returns a structured 503 and creates no queued job (the asset
    stays not_started) rather than a job that would pend forever. Repeated clicks
    never create duplicate concurrent jobs (idempotent enqueue)."""
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        # Operational action -> monitoring.configure permission (RBAC enforced).
        user, workspace_context = pilot.require_ops_rbac_guard(connection, request)
        workspace_id = workspace_context['workspace_id']
        user_id = str(user['id'])
        asset_row = connection.execute(
            'SELECT * FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
            (asset_id, workspace_id),
        ).fetchone()
        if asset_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        if not service._table_exists(connection, 'asset_risk_jobs'):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Asset risk assessment storage is provisioning. Try again shortly.')

        cfg = arc.assessor_config()
        now = pilot.utc_now()
        execution_mode, worker_healthy = _resolve_execution_mode(connection, cfg, now)
        service.log_assessment_event(
            'asset_assessment_requested', workspace_id=workspace_id, asset_id=asset_id,
            user_id=user_id, execution_mode=execution_mode, worker_healthy=worker_healthy,
        )

        # No execution path: fail closed. Do NOT create a queued job.
        if execution_mode == 'unavailable':
            service.log_assessment_event(
                'asset_assessment_blocked', workspace_id=workspace_id, asset_id=asset_id,
                execution_mode='unavailable', failure_code='assessment_worker_unavailable',
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    'code': 'assessment_worker_unavailable',
                    'message': 'The Asset Risk Assessor worker is unavailable and on-demand assessment is disabled. Enable the worker or on-demand assessment to run assessments.',
                    'execution_mode': 'unavailable',
                },
            )

        service.log_assessment_event(
            'asset_assessment_execution_selected', workspace_id=workspace_id, asset_id=asset_id, execution_mode=execution_mode,
        )
        enq = worker.enqueue_assessment(
            connection, workspace_id=workspace_id, asset_id=asset_id,
            trigger_source='manual', requested_by_user_id=user_id,
        )
        if not enq['enqueued']:
            # An active (queued/running) job already exists — never start a duplicate.
            service.log_assessment_event(
                'asset_assessment_deduplicated', workspace_id=workspace_id, asset_id=asset_id,
                job_id=enq['job_id'], status=enq['status'],
            )
            connection.commit()
            return {'status': enq['status'], 'job_id': enq['job_id'], 'asset_id': asset_id,
                    'execution_mode': execution_mode, 'deduplicated': True}
        service.log_assessment_event(
            'asset_assessment_queued', workspace_id=workspace_id, asset_id=asset_id, job_id=enq['job_id'], execution_mode=execution_mode,
        )

        # Background mode: leave the queued job for the healthy worker to claim.
        if execution_mode == 'background':
            connection.commit()
            return {'status': 'queued', 'job_id': enq['job_id'], 'asset_id': asset_id, 'execution_mode': 'background'}

        # On-demand: claim the job we just created and run it inline.
        claimed = connection.execute(
            "UPDATE asset_risk_jobs SET status = 'running', lease_owner = %s, lease_expires_at = NOW() + INTERVAL '5 minutes', started_at = NOW(), heartbeat_at = NOW(), attempts = attempts + 1, updated_at = NOW() WHERE id = %s AND status = 'queued' RETURNING id",
            (f'inline:{user_id}', enq['job_id']),
        ).fetchone()
        if claimed is None:
            connection.commit()
            return {'status': 'running', 'job_id': enq['job_id'], 'asset_id': asset_id, 'execution_mode': 'on_demand'}
        service.log_assessment_event(
            'asset_assessment_started', workspace_id=workspace_id, asset_id=asset_id,
            job_id=enq['job_id'], execution_mode='on_demand',
        )

        try:
            started = pilot.utc_now()
            outcome = service.assess_asset(
                connection, workspace_id=workspace_id, asset_row=dict(asset_row), trigger_source='manual'
            )
            connection.execute(
                "UPDATE asset_risk_jobs SET status = 'completed', completed_at = NOW(), heartbeat_at = NOW(), lease_owner = NULL, last_error = NULL, failure_code = NULL, updated_at = NOW() WHERE id = %s",
                (enq['job_id'],),
            )
            duration = max(0.0, (pilot.utc_now() - started).total_seconds())
            result_status = str(outcome.get('status') or 'completed')
            completion_event = 'asset_assessment_partial' if result_status in ('partial', 'degraded') else 'asset_assessment_completed'
            service.log_assessment_event(
                completion_event, workspace_id=workspace_id, asset_id=asset_id,
                assessment_id=outcome.get('assessment_id'), job_id=enq['job_id'], execution_mode='on_demand',
                duration=round(duration, 3), result=result_status, risk_score=outcome.get('risk_score'),
                risk_level=outcome.get('risk_level'), findings=outcome.get('findings_count'),
            )
            pilot.log_audit(
                connection, action='asset.risk_assessment.trigger', entity_type='asset', entity_id=asset_id,
                request=request, user_id=user_id, workspace_id=workspace_id,
                metadata={'risk_score': outcome.get('risk_score'), 'risk_level': outcome.get('risk_level'),
                          'trigger': 'manual', 'execution_mode': 'on_demand', 'result': result_status},
            )
            connection.commit()
            return {'status': result_status, 'job_id': enq['job_id'], 'assessment': outcome, 'execution_mode': 'on_demand'}
        except Exception as exc:
            connection.execute(
                "UPDATE asset_risk_jobs SET status = 'failed', last_error = %s, failure_code = 'assessment_error', lease_owner = NULL, updated_at = NOW() WHERE id = %s",
                (str(exc)[:500], enq['job_id']),
            )
            service.log_assessment_event(
                'asset_assessment_failed', workspace_id=workspace_id, asset_id=asset_id,
                job_id=enq['job_id'], execution_mode='on_demand', failure_code='assessment_error', error=type(exc).__name__,
            )
            connection.commit()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Assessment failed to complete. It has been recorded as failed; retry to run it again.')
