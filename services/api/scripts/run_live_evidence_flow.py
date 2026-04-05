#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


ALLOWED_STATUSES = {
    'live_coverage_confirmed',
    'live_coverage_denied',
    'monitoring_execution_failed',
    'asset_configuration_incomplete',
}


def _request(method: str, url: str, *, token: str, workspace_id: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = Request(
        url,
        data=data,
        method=method.upper(),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
            'x-workspace-id': workspace_id,
        },
    )
    with urlopen(req, timeout=30) as resp:  # nosec B310
        return json.loads(resp.read().decode('utf-8') or '{}')


def _missing_asset_fields(asset: dict) -> list[str]:
    required = [
        'id',
        'asset_identifier',
        'asset_symbol',
        'token_contract_address',
        'treasury_ops_wallets',
        'custody_wallets',
        'expected_flow_patterns',
        'expected_counterparties',
        'expected_approval_patterns',
        'venue_labels',
        'expected_liquidity_baseline',
        'oracle_sources',
        'expected_oracle_freshness_seconds',
        'expected_oracle_update_cadence_seconds',
    ]
    missing: list[str] = []
    for key in required:
        value = asset.get(key)
        if value is None:
            missing.append(key)
        elif isinstance(value, str) and not value.strip():
            missing.append(key)
        elif isinstance(value, (list, dict)) and len(value) == 0:
            missing.append(key)
    return missing


def main() -> int:
    api_url = (os.getenv('API_URL') or 'http://localhost:8000').rstrip('/')
    token = os.getenv('PILOT_AUTH_TOKEN', '').strip()
    workspace_id = os.getenv('WORKSPACE_ID', '').strip()
    if not (os.getenv('EVM_RPC_URL') or '').strip():
        print('Set EVM_RPC_URL for real evidence generation', file=sys.stderr)
        return 2
    if not token or not workspace_id:
        print('Set PILOT_AUTH_TOKEN and WORKSPACE_ID', file=sys.stderr)
        return 2

    asset_identifier = os.getenv('EVIDENCE_ASSET_IDENTIFIER', 'USTB-REAL')
    asset = _request(
        'POST',
        f'{api_url}/assets',
        token=token,
        workspace_id=workspace_id,
        body={
            'name': f'Protected Treasury Asset {asset_identifier}',
            'asset_symbol': os.getenv('EVIDENCE_ASSET_SYMBOL', 'USTB'),
            'asset_identifier': asset_identifier,
            'chain_network': 'ethereum',
            'token_contract_address': os.getenv('EVIDENCE_ASSET_CONTRACT', '0x' + 'a' * 40),
            'treasury_ops_wallets': [os.getenv('EVIDENCE_WALLET_ADDRESS', '0x1111111111111111111111111111111111111111')],
            'custody_wallets': [os.getenv('EVIDENCE_CUSTODY_WALLET', '0x2222222222222222222222222222222222222222')],
            'expected_counterparties': [os.getenv('EVIDENCE_COUNTERPARTY', '0x3333333333333333333333333333333333333333')],
            'expected_flow_patterns': [
                {'source_class': 'treasury_ops', 'destination_class': 'custody'},
                {'source_class': 'custody', 'destination_class': 'approved_external_counterparty', 'required_checkpoint': 'monitored_venue'},
            ],
            'expected_approval_patterns': {'allowed_spenders': [os.getenv('EVIDENCE_SPENDER', '0x4444444444444444444444444444444444444444')], 'max_amount': 1000000},
            'venue_labels': [os.getenv('EVIDENCE_VENUE', '0x5555555555555555555555555555555555555555')],
            'expected_liquidity_baseline': {'baseline_outflow_volume': 100000, 'baseline_transfer_count': 5, 'minimum_transfer_count': 1},
            'baseline_status': 'ready',
            'baseline_confidence': 0.9,
            'baseline_coverage': 0.9,
            'oracle_sources': [os.getenv('EVIDENCE_ORACLE_SOURCE', 'oracle-a')],
            'expected_oracle_freshness_seconds': 120,
            'expected_oracle_update_cadence_seconds': 120,
        },
    )
    target = _request(
        'POST',
        f'{api_url}/targets',
        token=token,
        workspace_id=workspace_id,
        body={
            'name': f'live-evidence-wallet-{uuid.uuid4().hex[:8]}',
            'target_type': 'wallet',
            'chain_network': 'ethereum',
            'wallet_address': os.getenv('EVIDENCE_WALLET_ADDRESS', '0x1111111111111111111111111111111111111111'),
            'monitoring_enabled': True,
            'monitoring_mode': 'stream',
            'chain_id': 1,
            'asset_label': 'Treasury reserve wallet',
            'asset_id': asset.get('id'),
            'enabled': True,
            'auto_create_incidents': True,
            'severity_threshold': 'high',
        },
    )
    target_id = target.get('id')
    run_cycle = _request('POST', f'{api_url}/ops/monitoring/run', token=token, workspace_id=workspace_id, body={'worker_name': 'evidence-worker', 'limit': 100})
    alerts = _request('GET', f'{api_url}/alerts?target_id={target_id}&status_value=open', token=token, workspace_id=workspace_id)
    incidents = _request('GET', f'{api_url}/incidents?target_id={target_id}', token=token, workspace_id=workspace_id)
    runs = _request('GET', f'{api_url}/pilot/history?kind=analysis_runs', token=token, workspace_id=workspace_id)
    artifacts_dir = Path(os.getenv('FEATURE1_EVIDENCE_DIR', 'services/api/artifacts/live_evidence/latest')).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    alert_rows = alerts.get('alerts', []) if isinstance(alerts.get('alerts'), list) else []
    incident_rows = incidents.get('incidents', []) if isinstance(incidents.get('incidents'), list) else []
    run_rows = runs.get('analysis_runs', []) if isinstance(runs.get('analysis_runs'), list) else []
    run_rows = [item for item in run_rows if isinstance(item, dict)]
    worker_runs = [
        item for item in run_rows
        if str(((item.get('response_payload') or {}).get('monitoring_path') or 'worker')).lower() == 'worker'
        and str(item.get('target_id') or '') == str(target_id or '')
        and 'manual_run_once' not in json.dumps(item).lower()
    ]

    coverage_rows = [
        ((item.get('response_payload') or {}).get('protected_asset_coverage_record') or {})
        for item in worker_runs
        if isinstance(item, dict)
    ]
    coverage_rows = [item for item in coverage_rows if isinstance(item, dict) and item]
    first_coverage = coverage_rows[0] if coverage_rows else {}

    market_coverage_status = first_coverage.get('market_coverage_status') or 'insufficient_real_evidence'
    oracle_coverage_status = first_coverage.get('oracle_coverage_status') or 'insufficient_real_evidence'
    market_claim_eligible = bool(first_coverage.get('market_claim_eligible'))
    oracle_claim_eligible = bool(first_coverage.get('oracle_claim_eligible'))
    enterprise_claim_eligibility = bool(first_coverage.get('enterprise_claim_eligibility'))
    market_observation_count = int(first_coverage.get('market_observation_count') or 0)
    oracle_observation_count = int(first_coverage.get('oracle_observation_count') or 0)
    real_provider_observations_present = market_observation_count > 0 and oracle_observation_count > 0

    claim_ineligibility_reasons = sorted(
        {
            str(reason)
            for reason in [
                *(first_coverage.get('claim_ineligibility_reasons') or []),
                *[
                    reason
                    for item in alert_rows
                    for reason in (((item.get('payload') or {}).get('claim_ineligibility_reasons')) or [])
                ],
            ]
            if str(reason).strip()
        }
    )

    missing_fields = _missing_asset_fields(asset)
    if missing_fields:
        claim_ineligibility_reasons = sorted(set([*claim_ineligibility_reasons, *[f'missing_{field}' for field in missing_fields]]))

    lifecycle_checks_performed = sorted(
        {
            str(result.get('detector_family'))
            for alert in alert_rows
            for result in ((alert.get('payload') or {}).get('detector_results') or [])
            if isinstance(result, dict) and result.get('lifecycle_stage')
        }
    )
    anomalies_observed = any(str(((item.get('payload') or {}).get('detector_status') or '')).lower() == 'anomaly_detected' for item in alert_rows)

    if missing_fields:
        status_value = 'asset_configuration_incomplete'
        reason = 'asset_context_missing_required_fields'
    elif not worker_runs:
        status_value = 'monitoring_execution_failed'
        reason = 'worker_monitoring_not_executed'
    elif enterprise_claim_eligibility and market_claim_eligible and oracle_claim_eligible and real_provider_observations_present:
        status_value = 'live_coverage_confirmed'
        reason = None
    else:
        status_value = 'live_coverage_denied'
        reason = 'coverage_requirements_not_satisfied'
        if not real_provider_observations_present:
            claim_ineligibility_reasons = sorted(set([*claim_ineligibility_reasons, 'missing_real_provider_observations']))

    if status_value not in ALLOWED_STATUSES:
        status_value = 'monitoring_execution_failed'
        reason = 'invalid_status_guard_triggered'

    summary = {
        'protected_asset': {
            'asset_id': asset.get('id'),
            'asset_identifier': asset.get('asset_identifier'),
            'symbol': asset.get('asset_symbol'),
            'chain_id': 1,
            'contract_address': asset.get('token_contract_address'),
            'treasury_ops_wallets': asset.get('treasury_ops_wallets') or [],
            'custody_wallets': asset.get('custody_wallets') or [],
            'expected_flow_patterns': asset.get('expected_flow_patterns') or [],
            'expected_counterparties': asset.get('expected_counterparties') or [],
            'expected_approval_patterns': asset.get('expected_approval_patterns') or {},
            'venue_labels': asset.get('venue_labels') or [],
            'expected_liquidity_baseline': asset.get('expected_liquidity_baseline') or {},
            'oracle_sources': asset.get('oracle_sources') or [],
            'expected_oracle_freshness_seconds': asset.get('expected_oracle_freshness_seconds'),
            'expected_oracle_update_cadence_seconds': asset.get('expected_oracle_update_cadence_seconds'),
        },
        'target_identity': {
            'target_id': target_id,
            'name': target.get('name'),
            'target_type': target.get('target_type'),
            'chain_network': target.get('chain_network'),
        },
        'workspace_id': workspace_id,
        'worker_run': run_cycle,
        'worker_monitoring_executed': bool(worker_runs),
        'alert_count': len(alert_rows),
        'incident_count': len(incident_rows),
        'run_count': len(run_rows),
        'worker_run_count': len(worker_runs),
        'status': status_value,
        'reason': reason,
        'enterprise_claim_eligibility': enterprise_claim_eligibility,
        'market_claim_eligible': market_claim_eligible,
        'oracle_claim_eligible': oracle_claim_eligible,
        'market_coverage_status': market_coverage_status,
        'oracle_coverage_status': oracle_coverage_status,
        'market_provider_count': int(first_coverage.get('market_provider_count') or 0),
        'market_provider_reachable_count': int(first_coverage.get('market_provider_reachable_count') or 0),
        'market_provider_fresh_count': int(first_coverage.get('market_provider_fresh_count') or 0),
        'market_provider_names': first_coverage.get('market_provider_names') or [],
        'market_observation_count': market_observation_count,
        'oracle_provider_count': int(first_coverage.get('oracle_provider_count') or 0),
        'oracle_provider_reachable_count': int(first_coverage.get('oracle_provider_reachable_count') or 0),
        'oracle_provider_fresh_count': int(first_coverage.get('oracle_provider_fresh_count') or 0),
        'oracle_provider_names': first_coverage.get('oracle_provider_names') or [],
        'oracle_observation_count': oracle_observation_count,
        'claim_ineligibility_reasons': claim_ineligibility_reasons,
        'external_market_telemetry_present': market_observation_count > 0,
        'real_oracle_observations_present': oracle_observation_count > 0,
        'lifecycle_checks_executed': bool(lifecycle_checks_performed),
        'lifecycle_checks_performed': lifecycle_checks_performed,
        'anomalies_observed': anomalies_observed,
        'result_scope': 'enterprise_claim_eligible' if enterprise_claim_eligibility else 'internal_only',
        'missing_asset_context_fields': missing_fields,
    }

    evidence_rows: list[dict] = [{
        'record_type': 'coverage_evaluation',
        'status': summary['status'],
        'reason': summary.get('reason'),
        'protected_asset_context': summary['protected_asset'],
        'target_identity': summary['target_identity'],
        'worker_monitoring_executed': summary['worker_monitoring_executed'],
        'worker_run_count': summary['worker_run_count'],
        'coverage_record': first_coverage,
        'market_coverage_status': market_coverage_status,
        'oracle_coverage_status': oracle_coverage_status,
        'enterprise_claim_eligibility': enterprise_claim_eligibility,
        'claim_ineligibility_reasons': claim_ineligibility_reasons,
        'lifecycle_checks_performed': lifecycle_checks_performed,
        'anomalies_observed': anomalies_observed,
    }]

    for item in alert_rows:
        payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
        evidence_rows.append({
            'record_type': 'alert_evaluation',
            'alert_id': item.get('id'),
            'analysis_run_id': item.get('analysis_run_id'),
            'detector_family': payload.get('detector_family'),
            'detector_status': payload.get('detector_status'),
            'market_coverage_status': payload.get('market_coverage_status'),
            'oracle_coverage_status': payload.get('oracle_coverage_status'),
            'enterprise_claim_eligibility': bool(payload.get('enterprise_claim_eligibility')),
            'claim_ineligibility_reasons': payload.get('claim_ineligibility_reasons') or [],
        })

    (artifacts_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    (artifacts_dir / 'alerts.json').write_text(json.dumps(alert_rows, indent=2, default=str))
    (artifacts_dir / 'incidents.json').write_text(json.dumps(incident_rows, indent=2, default=str))
    (artifacts_dir / 'evidence.json').write_text(json.dumps(evidence_rows, indent=2, default=str))
    (artifacts_dir / 'runs.json').write_text(json.dumps(run_rows, indent=2, default=str))
    (artifacts_dir / 'report.md').write_text(
        '# Feature1 Evidence\n\n'
        f"- status: `{summary['status']}`\n"
        f"- worker_monitoring_executed: `{summary['worker_monitoring_executed']}`\n"
        f"- enterprise_claim_eligibility: `{summary['enterprise_claim_eligibility']}`\n"
        f"- market_coverage_status: `{summary['market_coverage_status']}`\n"
        f"- oracle_coverage_status: `{summary['oracle_coverage_status']}`\n"
        f"- claim_ineligibility_reasons: `{summary['claim_ineligibility_reasons']}`\n"
    )
    print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2, default=str))
    return 0 if summary['status'] in {'live_coverage_confirmed', 'live_coverage_denied'} else 3


if __name__ == '__main__':
    raise SystemExit(main())
