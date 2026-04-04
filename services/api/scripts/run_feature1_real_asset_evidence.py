#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
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
    except urllib.error.URLError as exc:
        return 503, {'error': str(exc.reason), 'code': 'connection_unavailable'}


def main() -> int:
    parser = argparse.ArgumentParser(description='Run Feature 1 real-asset evidence flow.')
    parser.add_argument('--api-url', default=os.getenv('FEATURE1_API_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--token', default=os.getenv('FEATURE1_API_TOKEN', ''))
    parser.add_argument('--workspace-id', default=os.getenv('FEATURE1_WORKSPACE_ID', ''))
    parser.add_argument('--target-id', default=os.getenv('FEATURE1_TARGET_ID', ''))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    artifacts_dir = Path(os.getenv('FEATURE1_EVIDENCE_DIR', 'services/api/artifacts/live_evidence/latest')).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        summary = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'status': 'inconclusive',
            'reason': 'dry_run',
        }
        (artifacts_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
        (artifacts_dir / 'alerts.json').write_text(json.dumps([], indent=2))
        (artifacts_dir / 'incidents.json').write_text(json.dumps([], indent=2))
        (artifacts_dir / 'runs.json').write_text(json.dumps([], indent=2))
        (artifacts_dir / 'evidence.json').write_text(json.dumps([], indent=2))
        print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 0

    status, runtime = _request_json(f"{args.api_url.rstrip('/')}/ops/monitoring/runtime-status", token=args.token, workspace_id=args.workspace_id)
    if status != 200:
        print(json.dumps({'status': 'fail', 'reason': 'runtime_unavailable', 'http_status': status, 'runtime': runtime}, indent=2))
        return 1

    mode = str(runtime.get('configured_mode') or runtime.get('mode') or '').upper()
    if mode not in {'LIVE', 'HYBRID'}:
        print(json.dumps({'status': 'inconclusive', 'reason': 'mode_not_live_or_hybrid', 'configured_mode': mode}, indent=2))
        return 2

    status, targets_payload = _request_json(f"{args.api_url.rstrip('/')}/targets", token=args.token, workspace_id=args.workspace_id)
    targets = targets_payload.get('targets') if isinstance(targets_payload.get('targets'), list) else []
    target = next((item for item in targets if (not args.target_id or str(item.get('id')) == args.target_id)), None)
    if status != 200 or target is None:
        print(json.dumps({'status': 'inconclusive', 'reason': 'no_monitored_target_found'}, indent=2))
        return 2

    _request_json(
        f"{args.api_url.rstrip('/')}/ops/monitoring/run",
        method='POST',
        token=args.token,
        workspace_id=args.workspace_id,
        payload={'worker_name': 'feature1-proof-worker', 'limit': 100},
    )

    _, alerts_payload = _request_json(f"{args.api_url.rstrip('/')}/alerts?target_id={target['id']}", token=args.token, workspace_id=args.workspace_id)
    _, incidents_payload = _request_json(f"{args.api_url.rstrip('/')}/incidents?target_id={target['id']}", token=args.token, workspace_id=args.workspace_id)
    _, runs_payload = _request_json(f"{args.api_url.rstrip('/')}/pilot/history?kind=analysis_runs", token=args.token, workspace_id=args.workspace_id)

    alerts = alerts_payload.get('alerts') if isinstance(alerts_payload.get('alerts'), list) else []
    incidents = incidents_payload.get('incidents') if isinstance(incidents_payload.get('incidents'), list) else []
    runs = runs_payload.get('analysis_runs') if isinstance(runs_payload.get('analysis_runs'), list) else []

    strict_alerts = []
    for alert in alerts:
        payload = alert.get('payload') if isinstance(alert.get('payload'), dict) else {}
        observed = payload.get('observed_evidence') if isinstance(payload.get('observed_evidence'), dict) else {}
        if (
            str(observed.get('evidence_origin') or '').lower() == 'real'
            and str(payload.get('detector_status') or '') == 'anomaly_detected'
            and str(payload.get('detector_family') or '') in {'counterparty', 'flow_pattern', 'approval_pattern', 'liquidity_venue', 'oracle_integrity'}
            and str(payload.get('monitoring_path') or '') == 'worker'
            and str(payload.get('source') or '').lower() == 'live'
            and not bool(payload.get('degraded'))
        ):
            strict_alerts.append(alert)

    strict_incidents = []
    high_ids = {item.get('id') for item in strict_alerts if str(item.get('severity') or '').lower() in {'high', 'critical'}}
    for incident in incidents:
        linked = set(incident.get('linked_alert_ids') or [])
        if linked & high_ids:
            strict_incidents.append(incident)

    insufficient_evidence = any(
        str(((item.get('payload') or {}).get('detector_status') or '')).lower() in {'insufficient_real_evidence', 'no_real_data'}
        for item in strict_alerts
    )
    worker_run_ids = [item.get('id') for item in runs if str(((item.get('response_payload') or {}).get('monitoring_path') or 'worker')).lower() == 'worker']
    pass_status = bool(worker_run_ids and strict_alerts and (not high_ids or strict_incidents) and not insufficient_evidence)
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'status': 'pass' if pass_status else 'fail',
        'workspace_id': args.workspace_id,
        'target_id': target.get('id'),
        'asset_id': target.get('asset_id'),
        'protected_asset': {'symbol': target.get('asset_symbol'), 'identifier': target.get('asset_identifier')},
        'monitoring_target': {'name': target.get('name'), 'target_type': target.get('target_type'), 'chain_network': target.get('chain_network')},
        'material_anomaly_reason': ((strict_alerts[0].get('payload') or {}).get('anomaly_basis') if strict_alerts else None),
        'detector_families_executed': sorted({str(((item.get('payload') or {}).get('detector_family') or '')) for item in strict_alerts if item.get('payload')}),
        'alert_ids': [item.get('id') for item in strict_alerts],
        'incident_ids': [item.get('id') for item in strict_incidents],
        'worker_generated_runs': worker_run_ids[:20],
        'insufficient_real_evidence_detected': insufficient_evidence,
        'enterprise_claim_eligible': pass_status,
        'why_material': 'Asset-specific detector fired from worker-generated real telemetry and persisted alerts/incidents exist.' if pass_status else 'Missing strict worker-driven real anomaly evidence bundle.',
    }

    (artifacts_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    (artifacts_dir / 'alerts.json').write_text(json.dumps(alerts, indent=2))
    (artifacts_dir / 'incidents.json').write_text(json.dumps(incidents, indent=2))
    (artifacts_dir / 'runs.json').write_text(json.dumps(runs, indent=2))
    (artifacts_dir / 'evidence.json').write_text(json.dumps([((item.get('payload') or {}).get('detector_results')) for item in strict_alerts], indent=2))
    (artifacts_dir / 'report.md').write_text(
        '# Feature1 Real Asset Evidence\n\n'
        f"- status: `{summary['status']}`\n"
        f"- asset: `{summary['protected_asset']}`\n"
        f"- detector_families_executed: `{summary['detector_families_executed']}`\n"
        f"- enterprise_claim_eligible: `{summary['enterprise_claim_eligible']}`\n"
    )
    print(json.dumps({**summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
    return 0 if pass_status else 2


if __name__ == '__main__':
    sys.exit(main())
