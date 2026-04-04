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
    artifacts_dir = Path(os.getenv('FEATURE1_EVIDENCE_DIR', 'services/api/artifacts/live_evidence')).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    alert_rows = alerts.get('alerts', []) if isinstance(alerts.get('alerts'), list) else []
    incident_rows = incidents.get('incidents', []) if isinstance(incidents.get('incidents'), list) else []
    run_rows = runs.get('analysis_runs', []) if isinstance(runs.get('analysis_runs'), list) else []
    evidence_rows = [((item.get('payload') or {}).get('detector_results') or []) for item in alert_rows if isinstance(item, dict)]

    summary = {
        'target_id': target_id,
        'workspace_id': workspace_id,
        'worker_run': run_cycle,
        'alert_count': len(alert_rows),
        'incident_count': len(incident_rows),
        'run_count': len(run_rows),
        'status': 'pass' if incident_rows else 'fail',
        'failure_reason': None if incident_rows else 'no_incidentworthy_real_anomaly_detected',
    }

    (artifacts_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    (artifacts_dir / 'alerts.json').write_text(json.dumps(alert_rows, indent=2, default=str))
    (artifacts_dir / 'incidents.json').write_text(json.dumps(incident_rows, indent=2, default=str))
    (artifacts_dir / 'evidence.json').write_text(json.dumps(evidence_rows, indent=2, default=str))
    (artifacts_dir / 'runs.json').write_text(json.dumps(run_rows, indent=2, default=str))
    (artifacts_dir / 'report.md').write_text('# Feature1 Evidence\n\nWorker-driven monitoring artifacts exported.\n')
    print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2, default=str))
    return 0 if incident_rows else 3


if __name__ == '__main__':
    raise SystemExit(main())
