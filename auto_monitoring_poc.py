#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib import error, request

def _headers(api_token: str, workspace_id: str | None) -> dict[str, str]:
    headers = {'Authorization': f'Bearer {api_token}', 'Content-Type': 'application/json'}
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    return headers


def _http_json(url: str, headers: dict[str, str], *, method: str = 'GET', body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload_bytes = None
    if body is not None:
        payload_bytes = json.dumps(body).encode('utf-8')
    req = request.Request(url, data=payload_bytes, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:
            text = response.read().decode('utf-8')
            return json.loads(text) if text.strip() else {}
    except error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='ignore')
        raise SystemExit(f'HTTP {exc.code} {method} {url}: {detail}') from exc


def _target_by_name(api_url: str, headers: dict[str, str], target_name: str) -> dict[str, Any]:
    targets = _http_json(f'{api_url}/targets', headers).get('targets') or []
    for target in targets:
        if str(target.get('name') or '').strip() == target_name:
            return target
    raise SystemExit(f'target not found: {target_name}')


def _verify_auto(args: argparse.Namespace) -> dict[str, Any]:
    headers = _headers(args.api_token, args.workspace_id)
    target = _target_by_name(args.api_url, headers, args.target_name)
    target_id = str(target['id'])

    patch_payload: dict[str, Any] = {
        'monitoring_enabled': True,
        'monitoring_mode': 'poll',
        'monitoring_interval_seconds': args.interval,
        'severity_threshold': args.threshold,
        'auto_create_alerts': True,
    }
    if args.scenario is not None:
        patch_payload['monitoring_scenario'] = args.scenario

    patched = _http_json(f'{args.api_url}/monitoring/targets/{target_id}', headers, method='PATCH', body=patch_payload).get('target') or {}

    before_count = len((_http_json(f'{args.api_url}/alerts', headers).get('alerts') or []))

    run_result = _http_json(f'{args.api_url}/monitoring/run-once/{target_id}', headers, method='POST')

    after_count = len((_http_json(f'{args.api_url}/alerts', headers).get('alerts') or []))

    report = {
        'target_id': target_id,
        'target_name': args.target_name,
        'config': {
            'monitoring_scenario': patched.get('monitoring_scenario') or patched.get('monitoring_demo_scenario'),
            'severity_threshold': patched.get('severity_threshold'),
            'monitoring_interval_seconds': patched.get('monitoring_interval_seconds'),
            'auto_create_alerts': patched.get('auto_create_alerts'),
        },
        'before_alert_count': before_count,
        'after_alert_count': after_count,
        'alert_count_increased': after_count > before_count,
        'run_result': run_result,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='POC helper for monitoring auto-alert verification.')
    parser.add_argument('--api-url', default=os.getenv('PILOT_API_URL', 'http://localhost:8000'))
    parser.add_argument('--api-token', default=os.getenv('PILOT_API_TOKEN', ''))
    parser.add_argument('--workspace-id', default=os.getenv('PILOT_WORKSPACE_ID', ''))
    subparsers = parser.add_subparsers(dest='command', required=True)

    verify = subparsers.add_parser('verify-auto', help='Configure monitoring and verify alerts increment.')
    verify.add_argument('--target-name', required=True)
    verify.add_argument('--interval', type=int, default=60)
    verify.add_argument('--threshold', choices=['low', 'medium', 'high', 'critical'], default='medium')
    verify.add_argument('--scenario', choices=['safe', 'low_risk', 'medium_risk', 'high_risk', 'flash_loan_like', 'admin_abuse_like', 'risky_approval_like'])
    verify.add_argument('--report-file')

    args = parser.parse_args()
    if not args.api_token:
        raise SystemExit('PILOT_API_TOKEN or --api-token is required')

    if args.command == 'verify-auto':
        report = _verify_auto(args)
        output = json.dumps(report, indent=2)
        if args.report_file:
            with open(args.report_file, 'w', encoding='utf-8') as handle:
                handle.write(output + '\n')
        print(output)


if __name__ == '__main__':
    main()
