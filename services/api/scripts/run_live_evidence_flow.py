#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


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
        and 'manual_run_once' not in json.dumps(item).lower()
    ]
    strict_alerts = []
    for item in alert_rows:
        payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
        observed = payload.get('observed_evidence') if isinstance(payload.get('observed_evidence'), dict) else {}
        if (
            str(observed.get('evidence_origin') or '').lower() == 'real'
            and str(payload.get('monitoring_path') or '').lower() == 'worker'
            and str(payload.get('detector_status') or '') == 'anomaly_detected'
            and str(payload.get('detector_family') or '') in {'counterparty', 'flow_pattern', 'approval_pattern', 'liquidity_venue', 'oracle_integrity'}
            and not any(token in json.dumps(payload).lower() for token in ('demo', 'synthetic', 'fallback'))
        ):
            strict_alerts.append(item)
    high_alert_ids = {item.get('id') for item in strict_alerts if str(item.get('severity') or '').lower() in {'high', 'critical'}}
    strict_incidents = [item for item in incident_rows if set(item.get('linked_alert_ids') or []) & high_alert_ids]
    evidence_rows = [((item.get('payload') or {}).get('detector_results') or []) for item in strict_alerts if isinstance(item, dict)]
    insufficient_detected = any(str((item.get('payload') or {}).get('detector_status') or '') in {'insufficient_real_evidence', 'no_real_data'} for item in alert_rows)
    passed = bool(worker_runs and strict_alerts and (not high_alert_ids or strict_incidents) and not insufficient_detected)

    summary = {
        'protected_asset': {
            'asset_id': asset.get('id'),
            'asset_identifier': asset.get('asset_identifier'),
            'symbol': asset.get('asset_symbol'),
            'chain_id': 1,
            'contract_address': asset.get('token_contract_address'),
            'treasury_ops_wallets': asset.get('treasury_ops_wallets') or [],
            'custody_wallets': asset.get('custody_wallets') or [],
        },
        'target_id': target_id,
        'workspace_id': workspace_id,
        'worker_run': run_cycle,
        'alert_count': len(alert_rows),
        'incident_count': len(incident_rows),
        'run_count': len(run_rows),
        'worker_run_count': len(worker_runs),
        'strict_alert_count': len(strict_alerts),
        'strict_incident_count': len(strict_incidents),
        'status': 'pass' if passed else 'fail',
        'failure_reason': None if passed else 'missing_worker_real_asset_anomaly_evidence',
        'enterprise_claim_eligibility': passed,
        'market_coverage_status': ((strict_alerts[0].get('payload') or {}).get('market_coverage_status') if strict_alerts else 'insufficient_real_evidence'),
        'oracle_coverage_status': ((strict_alerts[0].get('payload') or {}).get('oracle_coverage_status') if strict_alerts else 'insufficient_real_evidence'),
        'provider_coverage_status': ((strict_alerts[0].get('payload') or {}).get('provider_coverage_status') if strict_alerts else {}),
        'enterprise_claim_eligible_results': [
            {
                'analysis_run_id': item.get('analysis_run_id'),
                'detector_family': (item.get('payload') or {}).get('detector_family'),
            }
            for item in strict_alerts
            if bool((item.get('payload') or {}).get('enterprise_claim_eligibility'))
        ],
        'internal_only_results': [
            {
                'analysis_run_id': item.get('analysis_run_id'),
                'detector_family': (item.get('payload') or {}).get('detector_family'),
            }
            for item in alert_rows
            if not bool((item.get('payload') or {}).get('enterprise_claim_eligibility'))
        ],
        'claim_ineligibility_reasons': sorted(
            {
                str(reason)
                for item in alert_rows
                for reason in (((item.get('payload') or {}).get('claim_ineligibility_reasons')) or [])
            }
        ),
        'insufficient_real_evidence_detected': insufficient_detected,
    }

    (artifacts_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    (artifacts_dir / 'alerts.json').write_text(json.dumps(alert_rows, indent=2, default=str))
    (artifacts_dir / 'incidents.json').write_text(json.dumps(incident_rows, indent=2, default=str))
    (artifacts_dir / 'evidence.json').write_text(json.dumps(evidence_rows, indent=2, default=str))
    (artifacts_dir / 'runs.json').write_text(json.dumps(run_rows, indent=2, default=str))
    (artifacts_dir / 'report.md').write_text(
        '# Feature1 Evidence\n\n'
        f"- status: `{summary['status']}`\n"
        f"- worker_run_count: `{summary['worker_run_count']}`\n"
        f"- strict_alert_count: `{summary['strict_alert_count']}`\n"
        f"- strict_incident_count: `{summary['strict_incident_count']}`\n"
        f"- enterprise_claim_eligibility: `{summary['enterprise_claim_eligibility']}`\n"
        f"- market_coverage_status: `{summary['market_coverage_status']}`\n"
        f"- oracle_coverage_status: `{summary['oracle_coverage_status']}`\n"
    )
    print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2, default=str))
    return 0 if passed else 3


if __name__ == '__main__':
    raise SystemExit(main())
