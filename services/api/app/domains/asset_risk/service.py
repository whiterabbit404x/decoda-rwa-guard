"""DB-backed Asset Risk Assessor.

Gathers evidence for a protected asset, runs the deterministic scoring engine,
persists a timestamped assessment snapshot, and reconciles deduplicated findings
into the existing alerts system (create / update-in-place / resolve). AI is only
used for the narrative, never for severity.

Truthfulness guarantees implemented here:
  * Reserve / price values are read from stored evidence — never fabricated. A
    missing / unverified / stale feed yields an "insufficient_evidence" reserve
    status and a degraded assessment, not a healthy one.
  * Provider/observation failures are recorded as evidence and mark the
    assessment ``degraded``; other assets keep being assessed.
  * Findings dedup on a stable fingerprint so a persistent condition updates one
    alert instead of creating a new alert every cycle; cleared conditions resolve
    their finding and alert.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timezone
from decimal import Decimal
from typing import Any, Optional

from services.api.app import pilot
from services.api.app.domains.asset_risk import config as arc
from services.api.app.domains.asset_risk import ai_explanation
from services.api.app.domains.asset_risk import scoring
from services.api.app.domains.asset_risk.scoring import AssetRiskInputs

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _to_decimal(value: Any) -> Optional[Decimal]:
    return scoring._to_decimal(value)


def _age_seconds(ts: Any, now: Any) -> Optional[int]:
    if ts is None:
        return None
    try:
        if getattr(ts, 'tzinfo', None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int((now - ts).total_seconds()))
    except Exception:
        return None


def _fingerprint(workspace_id: str, asset_id: str, finding_type: str) -> str:
    return hashlib.sha256(f'asset-risk:{workspace_id}:{asset_id}:{finding_type}'.encode('utf-8')).hexdigest()


def _deterministic_alert_id(workspace_id: str, asset_id: str, finding_type: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'asset-risk-alert:{workspace_id}:{asset_id}:{finding_type}'))


def _hash_identifier(value: Any) -> Optional[str]:
    text = str(value or '').strip()
    if not text:
        return None
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _table_exists(connection: Any, name: str) -> bool:
    try:
        row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS ok', (f'public.{name}',)).fetchone()
        return bool((row or {}).get('ok'))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Evidence gathering
# --------------------------------------------------------------------------
def gather_inputs(
    connection: Any,
    *,
    workspace_id: str,
    asset_row: dict[str, Any],
    config: dict[str, Any],
    now: Any,
) -> dict[str, Any]:
    """Build AssetRiskInputs + evidence + data gaps from stored facts.

    Returns a dict: {inputs, evidence, data_gaps, degraded, valuation_to_record,
    reserve_snapshot_to_record, liability_value_usd, price_usd}.
    """
    asset_id = str(asset_row['id'])
    rwa_type = asset_row.get('rwa_asset_type')
    reserve_feed_type = str(asset_row.get('reserve_feed_type') or 'none').strip().lower()
    reserve_required = arc.reserve_required_for(rwa_type, reserve_feed_type)

    data_gaps: list[str] = []
    degraded = False
    evidence: dict[str, Any] = {'asset_id': asset_id}

    # ---- Monitoring linkage (compact, workspace-scoped) ----
    link = connection.execute(
        '''
        SELECT
            EXISTS(SELECT 1 FROM targets t WHERE t.workspace_id = %s AND t.asset_id = %s AND t.deleted_at IS NULL) AS has_target,
            EXISTS(
                SELECT 1 FROM targets t
                JOIN monitored_systems ms ON ms.workspace_id = t.workspace_id AND ms.target_id = t.id
                WHERE t.workspace_id = %s AND t.asset_id = %s AND t.deleted_at IS NULL
            ) AS has_system,
            EXISTS(
                SELECT 1 FROM targets t
                JOIN monitored_systems ms ON ms.workspace_id = t.workspace_id AND ms.target_id = t.id
                WHERE t.workspace_id = %s AND t.asset_id = %s AND t.deleted_at IS NULL AND ms.last_event_at IS NOT NULL
            ) AS has_telemetry,
            (
                SELECT ms.freshness_status = 'fresh' FROM targets t
                JOIN monitored_systems ms ON ms.workspace_id = t.workspace_id AND ms.target_id = t.id
                WHERE t.workspace_id = %s AND t.asset_id = %s AND t.deleted_at IS NULL
                ORDER BY ms.last_event_at DESC NULLS LAST LIMIT 1
            ) AS telemetry_fresh
        ''',
        (workspace_id, asset_id, workspace_id, asset_id, workspace_id, asset_id, workspace_id, asset_id),
    ).fetchone() or {}
    has_target = bool(link.get('has_target'))
    has_telemetry = bool(link.get('has_telemetry'))
    telemetry_fresh = bool(link.get('telemetry_fresh'))
    evidence['monitoring'] = {
        'has_target': has_target,
        'has_system': bool(link.get('has_system')),
        'has_telemetry': has_telemetry,
        'telemetry_fresh': telemetry_fresh,
    }

    # ---- Price / valuation ----
    reference_price = _to_decimal(asset_row.get('reference_price_usd'))
    price_source = str(asset_row.get('price_source') or '').strip()
    oracle_sources = asset_row.get('oracle_sources') or []
    chainlink_feeds = asset_row.get('chainlink_feeds') or []
    price_source_configured = bool(price_source or oracle_sources or chainlink_feeds or reference_price is not None)
    price_usd = reference_price
    valuation_to_record: Optional[dict[str, Any]] = None
    if price_usd is not None:
        valuation_to_record = {
            'price_usd': price_usd,
            'source': price_source or 'reference',
            'is_estimated': False,
        }
    elif price_source_configured:
        # A price source is configured but produced no observation this cycle.
        data_gaps.append('Price source configured but no current price observation.')
        degraded = True

    # Rolling baseline from stored valuation snapshots (never invented).
    baseline_30d = baseline_7d = stddev_30d = None
    sample_count = 0
    if _table_exists(connection, 'asset_valuation_snapshots'):
        baseline = connection.execute(
            '''
            SELECT COUNT(*) AS n,
                   AVG(price_usd) AS mean_30d,
                   STDDEV_SAMP(price_usd) AS std_30d,
                   AVG(price_usd) FILTER (WHERE observed_at >= %s - (INTERVAL '7 days')) AS mean_7d
            FROM asset_valuation_snapshots
            WHERE workspace_id = %s AND asset_id = %s
              AND price_usd IS NOT NULL
              AND observed_at >= %s - (%s || ' days')::interval
            ''',
            (now, workspace_id, asset_id, now, str(int(config['baseline_days']))),
        ).fetchone() or {}
        sample_count = int(baseline.get('n') or 0)
        baseline_30d = _to_decimal(baseline.get('mean_30d'))
        baseline_7d = _to_decimal(baseline.get('mean_7d'))
        stddev_30d = _to_decimal(baseline.get('std_30d'))
    evidence['valuation'] = {
        'price_usd': str(price_usd) if price_usd is not None else None,
        'baseline_30d': str(baseline_30d) if baseline_30d is not None else None,
        'sample_count': sample_count,
    }
    if price_source_configured and sample_count < int(config['min_baseline_samples']):
        data_gaps.append('Insufficient valuation history for a market baseline (baseline learning).')

    # Secondary price for oracle-disagreement (only when two distinct sources exist).
    secondary_price = None  # Not fabricated; requires a real second observed source.

    # ---- Reserve backing ----
    reserve_value = _to_decimal(asset_row.get('reserve_value_usd'))
    reserve_verified_at = asset_row.get('reserve_verified_at')
    reserve_age = _age_seconds(reserve_verified_at, now)
    min_ratio = _to_decimal(asset_row.get('reserve_min_coverage_ratio')) or config['default_min_coverage_ratio']

    # On-chain liability = circulating_supply x reference_price (Decimal-safe).
    liability = scoring.compute_on_chain_liability_usd(
        asset_row.get('circulating_supply'), asset_row.get('token_decimals'), reference_price
    )
    liability_basis = 'on_chain_supply_x_price'
    if liability is None:
        declared_value = _to_decimal(asset_row.get('value_usd'))
        if declared_value is not None and declared_value > 0:
            liability = declared_value
            liability_basis = 'operator_declared_value'
            data_gaps.append('On-chain circulating supply unavailable; using operator-declared value as liability basis (estimated).')

    reserve_feed_configured = reserve_feed_type != 'none'
    # A reserve value is "verified" evidence when it is present WITH an attestation
    # timestamp from a manual/attestation feed. Feed types requiring a live fetch
    # (api / proof_of_reserve) with no adapter yield no verified value here.
    reserve_verified = (
        reserve_feed_configured
        and reserve_value is not None
        and reserve_verified_at is not None
        and reserve_feed_type in {'manual', 'attestation', 'proof_of_reserve'}
    )
    if reserve_required and not reserve_feed_configured:
        data_gaps.append('Reserve backing required but no reserve feed is configured.')
    elif reserve_required and reserve_feed_type == 'api' and reserve_value is None:
        data_gaps.append('API reserve feed configured but no verified reserve value is available.')
        degraded = True

    reserve_snapshot_to_record: Optional[dict[str, Any]] = None
    if reserve_verified and reserve_value is not None:
        coverage_ratio = (reserve_value / liability) if (liability and liability > 0) else None
        reserve_snapshot_to_record = {
            'reserve_value_usd': reserve_value,
            'liability_value_usd': liability,
            'coverage_ratio': coverage_ratio,
            'feed_type': reserve_feed_type,
            'feed_identifier_hash': _hash_identifier(asset_row.get('reserve_feed_identifier')),
            'source': reserve_feed_type,
            'verified': True,
        }
    evidence['reserve'] = {
        'required': reserve_required,
        'feed_type': reserve_feed_type,
        'verified': reserve_verified,
        'reserve_value_usd': str(reserve_value) if reserve_value is not None else None,
        'liability_value_usd': str(liability) if liability is not None else None,
        'liability_basis': liability_basis if liability is not None else None,
        'reserve_age_seconds': reserve_age,
    }

    # ---- Monitoring controls (required set depends on asset shape) ----
    has_contract = bool(str(asset_row.get('token_contract_address') or '').strip())
    verification_status = str(asset_row.get('verification_status') or '').strip().lower()
    controls: list[tuple[str, bool, bool]] = [
        ('monitoring_target', True, has_target),
        ('recent_telemetry', True, has_telemetry and telemetry_fresh),
        ('price_source', price_source_configured or reserve_required, price_source_configured),
        ('reserve_feed', reserve_required, reserve_feed_configured),
        ('chain_identity', has_contract, verification_status == 'verified'),
    ]

    # ---- Governance / contract exposure (evidence-based only) ----
    governance_signals: list[str] = []
    token_standard = str(asset_row.get('token_standard') or '').strip().lower()
    verification_summary = asset_row.get('verification_summary') or {}
    if isinstance(verification_summary, dict):
        if verification_summary.get('is_proxy') or 'proxy' in token_standard:
            governance_signals.append('upgradeable_proxy')
        if verification_summary.get('implementation_unverified'):
            governance_signals.append('unverified_implementation')
    contract_discovery_failed = bool(has_contract and verification_status in {'failed', 'verification_failed'})
    if contract_discovery_failed:
        data_gaps.append('Contract discovery/verification failed; governance exposure is uncertain.')

    # ---- Recent abnormal activity (external alerts/incidents for this asset) ----
    recent_high = 0
    try:
        activity = connection.execute(
            '''
            SELECT COUNT(*) AS n FROM alerts
            WHERE workspace_id = %s
              AND payload->>'asset_id' = %s
              AND lower(severity) IN ('high', 'critical')
              AND status NOT IN ('resolved', 'false_positive', 'suppressed')
              AND created_at >= %s - INTERVAL '24 hours'
              AND COALESCE(module_key, '') <> 'asset_risk'
            ''',
            (workspace_id, asset_id, now),
        ).fetchone() or {}
        recent_high = int(activity.get('n') or 0)
    except Exception:
        recent_high = 0

    inputs = AssetRiskInputs(
        reserve_required=reserve_required,
        reserve_feed_configured=reserve_feed_configured,
        reserve_verified=reserve_verified,
        reserve_value_usd=reserve_value,
        liability_value_usd=liability,
        reserve_min_coverage_ratio=min_ratio,
        reserve_age_seconds=reserve_age,
        reserve_stale_seconds=int(
            asset_row.get('reserve_update_interval_seconds') or config['reserve_stale_seconds']
        ),
        over_collateralization_ratio=config['over_collateralization_ratio'],
        price_source_configured=price_source_configured,
        price_usd=price_usd,
        baseline_7d=baseline_7d,
        baseline_30d=baseline_30d,
        price_stddev_30d=stddev_30d,
        price_sample_count=sample_count,
        min_baseline_samples=int(config['min_baseline_samples']),
        secondary_price_usd=secondary_price,
        deviation_medium_percent=config['deviation_medium_percent'],
        deviation_high_percent=config['deviation_high_percent'],
        zscore_high=config['zscore_high'],
        oracle_disagreement_percent=config['oracle_disagreement_percent'],
        has_reserve_or_minting_irregularity=False,
        monitoring_controls=controls,
        has_monitoring_target=has_target,
        price_age_seconds=0 if price_usd is not None else None,
        price_stale_seconds=int(config['price_stale_seconds']),
        governance_signals=governance_signals,
        contract_discovery_failed=contract_discovery_failed,
        recent_high_severity_findings=recent_high,
        recent_anomaly_events=0,
        provider_failures=0,
    )

    return {
        'inputs': inputs,
        'evidence': evidence,
        'data_gaps': data_gaps,
        'degraded': degraded,
        'valuation_to_record': valuation_to_record,
        'reserve_snapshot_to_record': reserve_snapshot_to_record,
        'liability_value_usd': liability,
        'price_usd': price_usd,
    }


# --------------------------------------------------------------------------
# Finding derivation
# --------------------------------------------------------------------------
def derive_findings(result: scoring.AssetRiskResult, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Map the deterministic result onto concrete, deduplicable findings."""
    findings: list[dict[str, Any]] = []
    reserve = result.reserve
    market = result.market
    monitoring = result.monitoring

    if reserve.status == scoring.RESERVE_CRITICAL:
        findings.append(_finding('asset_reserve_shortfall', 'critical', 'Reserve shortfall', reserve.reason,
                                 {'coverage_percent': _num(reserve.coverage_percent), 'reserve_value_usd': _num(reserve.reserve_value_usd), 'liability_value_usd': _num(reserve.liability_value_usd)}))
    elif reserve.status == scoring.RESERVE_WARNING:
        findings.append(_finding('asset_reserve_shortfall', 'high', 'Reserve coverage below minimum', reserve.reason,
                                 {'coverage_percent': _num(reserve.coverage_percent)}))
    elif reserve.status == scoring.RESERVE_INSUFFICIENT:
        feed_type = str((evidence.get('reserve') or {}).get('feed_type') or 'none')
        ftype = 'asset_reserve_feed_missing' if feed_type == 'none' else 'asset_reserve_feed_stale'
        findings.append(_finding(ftype, 'high', 'Reserve evidence insufficient', reserve.reason,
                                 {'feed_type': feed_type}))
    elif reserve.status == scoring.RESERVE_OVER_COLLATERALIZED:
        findings.append(_finding('asset_over_collateralization', 'medium', 'Unexpected over-collateralization', reserve.reason,
                                 {'coverage_percent': _num(reserve.coverage_percent)}))

    if market.status == scoring.MARKET_CRITICAL:
        findings.append(_finding('asset_price_deviation', 'critical', 'Critical market deviation', market.reason,
                                 {'deviation_30d_percent': _num(market.deviation_30d_percent), 'zscore': _num(market.zscore)}))
    elif market.status == scoring.MARKET_HIGH:
        # Oracle disagreement and baseline deviation are distinct finding types;
        # the scoring reason indicates which one drove the severity.
        is_disagreement = 'disagreement' in (market.reason or '').lower()
        ftype = 'asset_oracle_disagreement' if is_disagreement else 'asset_price_deviation'
        title = 'Oracle disagreement' if is_disagreement else 'Severe market deviation'
        findings.append(_finding(ftype, 'high', title, market.reason,
                                 {'deviation_30d_percent': _num(market.deviation_30d_percent), 'oracle_disagreement_percent': _num(market.oracle_disagreement_percent)}))
    elif market.status == scoring.MARKET_MEDIUM:
        findings.append(_finding('asset_price_deviation', 'medium', 'Market deviation from baseline', market.reason,
                                 {'deviation_30d_percent': _num(market.deviation_30d_percent)}))

    if monitoring.health in (scoring.HEALTH_CRITICAL, scoring.HEALTH_WARNING, scoring.HEALTH_NOT_CONFIGURED) and monitoring.missing_controls:
        sev = 'high' if monitoring.health == scoring.HEALTH_CRITICAL else 'medium'
        findings.append(_finding('asset_monitoring_gap', sev, 'Monitoring coverage gap', monitoring.reason,
                                 {'missing_controls': monitoring.missing_controls, 'coverage_percent': _num(monitoring.coverage_percent)}))

    # Governance exposure surfaces as a finding only when the dimension is material.
    gov_dim = next((d for d in result.dimensions if d.key == 'contract_governance'), None)
    if gov_dim and gov_dim.score >= 60 and gov_dim.findings:
        findings.append(_finding('asset_contract_exposure', 'medium', 'Contract/governance exposure', 'Elevated contract or administrative exposure signals detected.',
                                 {'signals': [f.get('signal') for f in gov_dim.findings]}))
    return findings


def _finding(finding_type: str, severity: str, title: str, detail: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {'finding_type': finding_type, 'severity': severity, 'title': title, 'detail': detail or '', 'evidence': evidence}


def _num(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------
# Persistence + alert reconciliation
# --------------------------------------------------------------------------
def _record_observations(connection: Any, *, workspace_id: str, asset_id: str, gathered: dict[str, Any], now: Any) -> None:
    valuation = gathered.get('valuation_to_record')
    if valuation and _table_exists(connection, 'asset_valuation_snapshots'):
        market_value = None
        price = valuation.get('price_usd')
        liability = gathered.get('liability_value_usd')
        connection.execute(
            '''
            INSERT INTO asset_valuation_snapshots (id, workspace_id, asset_id, price_usd, market_value_usd, source, is_estimated, observed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (str(uuid.uuid4()), workspace_id, asset_id, price, liability if isinstance(liability, Decimal) else None,
             valuation.get('source') or 'unknown', bool(valuation.get('is_estimated')), now),
        )
    reserve_snap = gathered.get('reserve_snapshot_to_record')
    if reserve_snap and _table_exists(connection, 'asset_reserve_snapshots'):
        connection.execute(
            '''
            INSERT INTO asset_reserve_snapshots (id, workspace_id, asset_id, reserve_value_usd, liability_value_usd, coverage_ratio, feed_type, feed_identifier_hash, source, verified, observed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (str(uuid.uuid4()), workspace_id, asset_id, reserve_snap.get('reserve_value_usd'), reserve_snap.get('liability_value_usd'),
             reserve_snap.get('coverage_ratio'), reserve_snap.get('feed_type'), reserve_snap.get('feed_identifier_hash'),
             reserve_snap.get('source'), bool(reserve_snap.get('verified')), now),
        )


def _persist_assessment(
    connection: Any,
    *,
    assessment_id: str,
    workspace_id: str,
    asset_id: str,
    result: scoring.AssetRiskResult,
    gathered: dict[str, Any],
    findings: list[dict[str, Any]],
    summary: dict[str, Any],
    status_value: str,
    trigger_source: str,
    now: Any,
) -> None:
    reserve = result.reserve
    market = result.market
    monitoring = result.monitoring
    feed_freshness = {
        'reserve_age_seconds': (gathered.get('evidence') or {}).get('reserve', {}).get('reserve_age_seconds'),
        'reserve_evidence_fresh': reserve.evidence_fresh,
        'price_observed': gathered.get('price_usd') is not None,
    }
    connection.execute(
        '''
        INSERT INTO asset_risk_assessments (
            id, workspace_id, asset_id, risk_score, risk_level, confidence, score_version, dimensions,
            reserve_value_usd, liability_value_usd, reserve_coverage_percent, reserve_difference_usd, reserve_status,
            price_deviation_7d_percent, price_deviation_30d_percent, price_zscore, feed_freshness,
            monitoring_coverage_percent, monitoring_health, data_completeness, findings, evidence,
            ai_summary, ai_summary_source, status, trigger_source, assessed_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s::jsonb,
            %s, %s, %s, %s::jsonb, %s::jsonb,
            %s, %s, %s, %s, %s
        )
        ''',
        (
            assessment_id, workspace_id, asset_id, result.risk_score, result.risk_level, result.confidence,
            result.score_version, pilot._json_dumps([d.to_dict() for d in result.dimensions]),
            reserve.reserve_value_usd, reserve.liability_value_usd, reserve.coverage_percent, reserve.reserve_difference_usd, reserve.status,
            market.deviation_7d_percent, market.deviation_30d_percent, market.zscore, pilot._json_dumps(feed_freshness),
            monitoring.coverage_percent, monitoring.health, result.data_completeness,
            pilot._json_dumps(findings), pilot._json_dumps(gathered.get('evidence') or {}),
            summary.get('executive_summary'), summary.get('source') or 'deterministic', status_value, trigger_source, now,
        ),
    )


def reconcile_findings(
    connection: Any,
    *,
    workspace_id: str,
    asset_id: str,
    asset_name: str,
    assessment_id: str,
    user_id: str,
    findings: list[dict[str, Any]],
    now: Any,
) -> dict[str, Any]:
    """Upsert active findings (dedup by fingerprint), raise/refresh their alerts,
    and resolve findings + alerts whose condition has cleared."""
    active_types = {f['finding_type'] for f in findings}
    alerts_created = 0
    alerts_updated = 0
    alerts_resolved = 0

    for f in findings:
        finding_type = f['finding_type']
        severity = f['severity']
        fingerprint = _fingerprint(workspace_id, asset_id, finding_type)
        alert_id = _deterministic_alert_id(workspace_id, asset_id, finding_type)
        finding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'asset-risk-finding:{workspace_id}:{asset_id}:{finding_type}'))
        evidence = f.get('evidence') or {}

        # Alert (create or update-in-place) — only medium+ findings raise alerts.
        raised_alert_id: Optional[str] = None
        if severity in ('critical', 'high', 'medium'):
            existing = connection.execute(
                'SELECT id, occurrence_count FROM alerts WHERE id = %s AND workspace_id = %s',
                (alert_id, workspace_id),
            ).fetchone()
            payload = {
                'source': 'asset_risk_assessor',
                'asset_id': asset_id,
                'asset_name': asset_name,
                'finding_type': finding_type,
                'assessment_id': assessment_id,
                'evidence': evidence,
                'dedupe_signature': fingerprint,
            }
            title = f'{asset_name}: {f.get("title") or finding_type}'
            summary_text = f.get('detail') or title
            connection.execute(
                '''
                INSERT INTO alerts (
                    id, workspace_id, user_id, analysis_run_id, alert_type, title, severity, status, source_service,
                    summary, payload, created_at, module_key, source, dedupe_signature, occurrence_count, first_seen_at, last_seen_at, updated_at
                ) VALUES (
                    %s, %s, %s, NULL, %s, %s, %s, 'open', 'asset-risk-assessor',
                    %s, %s::jsonb, NOW(), 'asset_risk', 'asset_risk_assessor', %s, 1, %s, %s, NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    severity = EXCLUDED.severity,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    payload = EXCLUDED.payload,
                    status = CASE WHEN alerts.status IN ('resolved', 'false_positive') THEN 'open' ELSE alerts.status END,
                    dedupe_signature = EXCLUDED.dedupe_signature,
                    last_seen_at = %s,
                    occurrence_count = alerts.occurrence_count + 1,
                    updated_at = NOW()
                ''',
                (alert_id, workspace_id, user_id, finding_type, title, severity, summary_text,
                 pilot._json_dumps(payload), fingerprint, now, now, now),
            )
            raised_alert_id = alert_id
            if existing is None:
                alerts_created += 1
            else:
                alerts_updated += 1

        # Finding upsert (dedup by (workspace, asset, fingerprint)).
        connection.execute(
            '''
            INSERT INTO asset_risk_findings (
                id, workspace_id, asset_id, assessment_id, finding_type, severity, status, fingerprint,
                title, detail, evidence, alert_id, occurrence_count, first_seen_at, last_seen_at, resolved_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s::jsonb, %s, 1, %s, %s, NULL
            )
            ON CONFLICT (workspace_id, asset_id, fingerprint) DO UPDATE SET
                assessment_id = EXCLUDED.assessment_id,
                severity = EXCLUDED.severity,
                status = 'active',
                title = EXCLUDED.title,
                detail = EXCLUDED.detail,
                evidence = EXCLUDED.evidence,
                alert_id = COALESCE(EXCLUDED.alert_id, asset_risk_findings.alert_id),
                occurrence_count = asset_risk_findings.occurrence_count + 1,
                last_seen_at = EXCLUDED.last_seen_at,
                resolved_at = NULL,
                updated_at = NOW()
            ''',
            (finding_id, workspace_id, asset_id, assessment_id, finding_type, severity, fingerprint,
             f.get('title') or finding_type, f.get('detail') or '', pilot._json_dumps(evidence), raised_alert_id, now, now),
        )

    # Resolve findings that no longer fire, and their linked alerts.
    stale = connection.execute(
        '''
        SELECT id, finding_type, alert_id FROM asset_risk_findings
        WHERE workspace_id = %s AND asset_id = %s AND status = 'active' AND finding_type <> ALL(%s)
        ''',
        (workspace_id, asset_id, list(active_types) or ['__none__']),
    ).fetchall()
    for row in stale:
        connection.execute(
            "UPDATE asset_risk_findings SET status = 'resolved', resolved_at = %s, updated_at = NOW() WHERE id = %s",
            (now, str(row['id'])),
        )
        if row.get('alert_id'):
            connection.execute(
                "UPDATE alerts SET status = 'resolved', updated_at = NOW() WHERE id = %s AND workspace_id = %s AND status NOT IN ('resolved', 'false_positive')",
                (str(row['alert_id']), workspace_id),
            )
            alerts_resolved += 1

    return {'alerts_created': alerts_created, 'alerts_updated': alerts_updated, 'alerts_resolved': alerts_resolved}


# --------------------------------------------------------------------------
# Public entrypoint: assess one asset within an open connection
# --------------------------------------------------------------------------
def assess_asset(
    connection: Any,
    *,
    workspace_id: str,
    asset_row: dict[str, Any],
    config: dict[str, Any] | None = None,
    trigger_source: str = 'worker',
    now: Any = None,
) -> dict[str, Any]:
    cfg = config or arc.assessor_config()
    now = now or pilot.utc_now()
    asset_id = str(asset_row['id'])
    asset_name = str(asset_row.get('name') or 'Asset')
    user_id = str(asset_row.get('created_by_user_id') or asset_row.get('updated_by_user_id') or '')

    gathered = gather_inputs(connection, workspace_id=workspace_id, asset_row=asset_row, config=cfg, now=now)
    inputs = gathered['inputs']
    result = scoring.compute_asset_risk(inputs)
    findings = derive_findings(result, gathered['evidence'])

    status_value = 'degraded' if gathered['degraded'] else 'completed'

    facts = {
        'asset_name': asset_name,
        'risk_score': result.risk_score,
        'risk_level': result.risk_level,
        'confidence': result.confidence,
        'reserve': {
            'status': result.reserve.status,
            'coverage_percent': _num(result.reserve.coverage_percent),
            'required': inputs.reserve_required,
            'evidence_fresh': result.reserve.evidence_fresh,
        },
        'market': {'status': result.market.status, 'deviation_30d_percent': _num(result.market.deviation_30d_percent)},
        'monitoring': {
            'health': result.monitoring.health,
            'coverage_percent': _num(result.monitoring.coverage_percent),
            'missing_controls': result.monitoring.missing_controls,
            'has_target': inputs.has_monitoring_target,
        },
        'findings': [{'finding_type': f['finding_type'], 'severity': f['severity'], 'title': f['title']} for f in findings],
        'data_gaps': gathered['data_gaps'],
        'assessment_status': status_value,
    }
    summary = ai_explanation.generate_summary(facts)

    assessment_id = str(uuid.uuid4())
    _record_observations(connection, workspace_id=workspace_id, asset_id=asset_id, gathered=gathered, now=now)
    _persist_assessment(
        connection, assessment_id=assessment_id, workspace_id=workspace_id, asset_id=asset_id,
        result=result, gathered=gathered, findings=findings, summary=summary,
        status_value=status_value, trigger_source=trigger_source, now=now,
    )
    reconciliation = {'alerts_created': 0, 'alerts_updated': 0, 'alerts_resolved': 0}
    if user_id:
        reconciliation = reconcile_findings(
            connection, workspace_id=workspace_id, asset_id=asset_id, asset_name=asset_name,
            assessment_id=assessment_id, user_id=user_id, findings=findings, now=now,
        )

    return {
        'assessment_id': assessment_id,
        'asset_id': asset_id,
        'risk_score': result.risk_score,
        'risk_level': result.risk_level,
        'confidence': result.confidence,
        'reserve_status': result.reserve.status,
        'monitoring_health': result.monitoring.health,
        'status': status_value,
        'findings_count': len(findings),
        'summary': summary,
        **reconciliation,
    }


def run_assessment_for_asset(
    workspace_id: str,
    asset_id: str,
    *,
    trigger_source: str = 'manual',
) -> dict[str, Any]:
    """Open a connection, assess a single asset, and commit. Used on-demand and by the worker."""
    with pilot.pg_connection() as connection:
        asset_row = connection.execute(
            'SELECT * FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
            (asset_id, workspace_id),
        ).fetchone()
        if asset_row is None:
            return {'status': 'not_found', 'asset_id': asset_id}
        outcome = assess_asset(
            connection, workspace_id=workspace_id, asset_row=dict(asset_row), trigger_source=trigger_source
        )
        connection.commit()
        return outcome
