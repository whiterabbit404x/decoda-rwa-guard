#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _request(method: str, url: str, *, token: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = Request(url, data=data, method=method.upper(), headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
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

    target = _request('POST', f'{api_url}/targets', token=token, body={
        'name': f'live-evidence-wallet-{uuid.uuid4().hex[:8]}',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': os.getenv('EVIDENCE_WALLET_ADDRESS', '0x1111111111111111111111111111111111111111'),
        'monitoring_enabled': True,
        'monitoring_mode': 'stream',
        'chain_id': 1,
        'asset_label': 'Treasury reserve wallet',
        'enabled': True,
    })
    target_id = target.get('id')
    run = _request('POST', f'{api_url}/monitoring/run-once/{target_id}', token=token)
    alerts = _request('GET', f'{api_url}/alerts?status_value=open', token=token)
    audit = _request('GET', f'{api_url}/pilot/history?kind=audit_logs', token=token)
    artifacts_dir = Path(os.getenv('FEATURE1_EVIDENCE_DIR', 'services/api/artifacts/live_evidence')).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    output = {'target': target, 'run': run, 'alerts': alerts.get('alerts', [])[:5], 'audit': audit.get('audit_logs', [])[:5]}
    (artifacts_dir / 'summary.json').write_text(json.dumps({'target_id': target_id, 'workspace_id': workspace_id, 'events_ingested': run.get('events_ingested', 0)}, indent=2))
    (artifacts_dir / 'alerts.json').write_text(json.dumps(output['alerts'], indent=2, default=str))
    (artifacts_dir / 'incidents.json').write_text(json.dumps([], indent=2))
    (artifacts_dir / 'evidence.json').write_text(json.dumps([item.get('payload', {}).get('observed_evidence') for item in output['alerts'] if isinstance(item, dict)], indent=2, default=str))
    print(json.dumps({**output, 'artifacts_dir': str(artifacts_dir)}, indent=2, default=str))
    return 0 if run.get('events_ingested', 0) > 0 else 3


if __name__ == '__main__':
    raise SystemExit(main())
