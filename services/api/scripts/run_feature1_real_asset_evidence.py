#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def _request_json(url: str, *, method: str = 'GET', token: str = '', workspace_id: str = '', payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8') or '{}')
            return resp.status, data
    except urllib.error.HTTPError as exc:
        text = exc.read().decode('utf-8', errors='ignore')
        try:
            return exc.code, json.loads(text or '{}')
        except Exception:
            return exc.code, {'error': text}


def main() -> int:
    parser = argparse.ArgumentParser(description='Run Feature 1 real-asset evidence flow.')
    parser.add_argument('--api-url', default=os.getenv('FEATURE1_API_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--token', default=os.getenv('FEATURE1_API_TOKEN', ''))
    parser.add_argument('--workspace-id', default=os.getenv('FEATURE1_WORKSPACE_ID', ''))
    parser.add_argument('--target-id', default=os.getenv('FEATURE1_TARGET_ID', ''))
    args = parser.parse_args()

    status, runtime = _request_json(f"{args.api_url.rstrip('/')}/ops/monitoring/runtime-status", token=args.token, workspace_id=args.workspace_id)
    if status != 200:
        print(json.dumps({'status': 'fail', 'reason': 'runtime_unavailable', 'http_status': status, 'runtime': runtime}, indent=2))
        return 1
    configured_mode = str(runtime.get('configured_mode') or runtime.get('mode') or '').upper()
    if configured_mode not in {'LIVE', 'HYBRID'}:
        print(json.dumps({'status': 'inconclusive', 'reason': 'mode_not_live_or_hybrid', 'configured_mode': configured_mode}, indent=2))
        return 2

    _, provider_checks = _request_json(f"{args.api_url.rstrip('/')}/ops/production-claim-validator", token=args.token, workspace_id=args.workspace_id)
    checks = provider_checks.get('checks') if isinstance(provider_checks.get('checks'), dict) else {}
    if checks.get('evm_rpc_reachable') is False:
        print(json.dumps({'status': 'inconclusive', 'reason': 'provider_not_reachable', 'checks': checks}, indent=2))
        return 2

    status, targets_payload = _request_json(f"{args.api_url.rstrip('/')}/targets", token=args.token, workspace_id=args.workspace_id)
    if status != 200:
        print(json.dumps({'status': 'fail', 'reason': 'targets_unavailable', 'http_status': status}, indent=2))
        return 1
    targets = targets_payload.get('targets') if isinstance(targets_payload.get('targets'), list) else []
    target = next((item for item in targets if not args.target_id or str(item.get('id')) == args.target_id), None)
    if target is None:
        print(json.dumps({'status': 'inconclusive', 'reason': 'no_monitored_target_found'}, indent=2))
        return 2

    run_status, run_payload = _request_json(f"{args.api_url.rstrip('/')}/monitoring/run-once/{target['id']}", method='POST', token=args.token, workspace_id=args.workspace_id)
    if run_status != 200:
        print(json.dumps({'status': 'fail', 'reason': 'monitoring_run_failed', 'http_status': run_status, 'response': run_payload}, indent=2))
        return 1

    _, alerts_payload = _request_json(f"{args.api_url.rstrip('/')}/alerts?target_id={target['id']}", token=args.token, workspace_id=args.workspace_id)
    _, incidents_payload = _request_json(f"{args.api_url.rstrip('/')}/incidents?target_id={target['id']}", token=args.token, workspace_id=args.workspace_id)
    export_status, export_payload = _request_json(
        f"{args.api_url.rstrip('/')}/exports/feature1-evidence",
        method='POST',
        token=args.token,
        workspace_id=args.workspace_id,
        payload={'format': 'json', 'filters': {'target_id': target['id']}},
    )

    alerts = alerts_payload.get('alerts') if isinstance(alerts_payload.get('alerts'), list) else []
    incidents = incidents_payload.get('incidents') if isinstance(incidents_payload.get('incidents'), list) else []
    latest_alert = alerts[0] if alerts else None
    anomaly_basis = ((latest_alert or {}).get('payload') or {}).get('anomaly_basis') if isinstance((latest_alert or {}).get('payload'), dict) else None
    evidence = ((latest_alert or {}).get('payload') or {}).get('observed_evidence') if isinstance((latest_alert or {}).get('payload'), dict) else None

    outcome = 'pass' if latest_alert and anomaly_basis and evidence else 'inconclusive'
    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'status': outcome,
        'workspace_id': args.workspace_id or targets_payload.get('workspace', {}).get('id'),
        'target_id': target.get('id'),
        'asset_id': target.get('asset_id'),
        'chain': target.get('chain_network'),
        'evidence_window': {'start': target.get('monitoring_checkpoint_at'), 'end': target.get('last_checked_at')},
        'last_real_event_observed': evidence,
        'finding_ids': [item.get('analysis_run_id') for item in ([run_payload] if isinstance(run_payload, dict) else []) if item.get('analysis_run_id')],
        'alert_ids': [item.get('id') for item in alerts[:5]],
        'incident_ids': [item.get('id') for item in incidents[:5]],
        'anomaly_basis': anomaly_basis,
        'baseline_context': ((latest_alert or {}).get('payload') or {}).get('baseline_reference') if isinstance((latest_alert or {}).get('payload'), dict) else None,
        'export_job': export_payload if export_status == 200 else {'status': 'failed', 'response': export_payload},
    }
    print(json.dumps(result, indent=2))
    return 0 if outcome == 'pass' else 2


if __name__ == '__main__':
    sys.exit(main())
