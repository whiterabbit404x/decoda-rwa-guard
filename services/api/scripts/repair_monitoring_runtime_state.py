#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _parse_iso8601(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _request_json(base_url: str, path: str, *, token: str, workspace_id: str, method: str = 'GET', payload: dict | None = None) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    req = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {token}',
            'x-workspace-id': workspace_id,
        },
    )
    try:
        with urlopen(req, timeout=30) as response:  # nosec B310
            return response.status, json.loads(response.read().decode('utf-8') or '{}')
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode('utf-8') or '{}')
        except Exception:
            payload = {'error': str(exc)}
        return exc.code, payload
    except URLError as exc:
        return 0, {'error': str(exc)}


def _is_alert_stale(alert: dict, *, now: datetime, max_age: timedelta, evidence_by_alert_id: dict[str, list[dict]]) -> bool:
    if str(alert.get('status') or '').lower() != 'open':
        return False
    alert_id = str(alert.get('id') or '')
    evidence_rows = evidence_by_alert_id.get(alert_id, [])
    if evidence_rows:
        newest = max((_parse_iso8601(row.get('observed_at')) for row in evidence_rows), default=None)
        if newest is not None and (now - newest) <= max_age:
            return False
    payload = alert.get('payload') if isinstance(alert.get('payload'), dict) else {}
    linked_detection = str(alert.get('linked_detection_id') or alert.get('detection_id') or payload.get('detection_id') or '')
    if linked_detection:
        return True
    created_at = _parse_iso8601(alert.get('created_at'))
    if created_at is None:
        return True
    return (now - created_at) > max_age


def _group_evidence_by_alert(evidence_rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in evidence_rows:
        alert_id = str(row.get('alert_id') or '')
        if not alert_id:
            continue
        grouped.setdefault(alert_id, []).append(row)
    return grouped


def _extract_chain(detections: list[dict], alerts: list[dict], incidents: list[dict], evidence_rows: list[dict]) -> dict:
    real_evidence = [
        row for row in evidence_rows
        if str(row.get('evidence_origin') or row.get('source_provider') or '').lower() in {'live', 'real', 'rpc_backfill', 'polling', 'websocket', 'evm_rpc'}
    ]
    real_evidence.sort(key=lambda row: str(row.get('observed_at') or row.get('created_at') or ''), reverse=True)
    latest = real_evidence[0] if real_evidence else (evidence_rows[0] if evidence_rows else None)
    if not latest:
        return {}
    tx_hash = str(latest.get('tx_hash') or '')
    detection = next((d for d in detections if tx_hash and str(d.get('tx_hash') or '') == tx_hash), detections[0] if detections else None)
    alert = None
    incident = None
    if detection:
        detection_id = str(detection.get('id') or '')
        alert = next((a for a in alerts if str(a.get('linked_detection_id') or a.get('detection_id') or '') == detection_id), None)
    if alert:
        incident_id = str(alert.get('linked_incident_id') or alert.get('incident_id') or '')
        incident = next((i for i in incidents if str(i.get('id') or '') == incident_id), None)
    return {
        'evidence': latest,
        'detection': detection,
        'alert': alert,
        'incident': incident,
        'action': None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Repair monitoring claim state by ensuring real-event chain and resolving stale alerts.')
    parser.add_argument('--api-url', default=os.getenv('API_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--token', default=os.getenv('PILOT_AUTH_TOKEN', ''))
    parser.add_argument('--workspace-id', default=os.getenv('WORKSPACE_ID', ''))
    parser.add_argument('--stale-minutes', type=int, default=int(os.getenv('MONITORING_STALE_ALERT_MINUTES', '30')))
    args = parser.parse_args()

    if not args.token or not args.workspace_id:
        print(json.dumps({'ok': False, 'error': 'Set PILOT_AUTH_TOKEN and WORKSPACE_ID.'}, indent=2))
        return 2

    run_code, run_payload = _request_json(
        args.api_url,
        '/ops/monitoring/run',
        token=args.token,
        workspace_id=args.workspace_id,
        method='POST',
        payload={'worker_name': 'ops-claim-repair', 'limit': 100},
    )
    if run_code >= 400 or run_code == 0:
        print(json.dumps({'ok': False, 'error': 'monitoring_run_failed', 'status_code': run_code, 'payload': run_payload}, indent=2))
        return 2

    runtime_code, runtime = _request_json(args.api_url, '/ops/monitoring/runtime-status', token=args.token, workspace_id=args.workspace_id)
    if runtime_code >= 400 or runtime_code == 0:
        print(json.dumps({'ok': False, 'error': 'runtime_status_failed', 'status_code': runtime_code, 'payload': runtime}, indent=2))
        return 2

    detections_code, detections_payload = _request_json(args.api_url, '/detections?limit=100', token=args.token, workspace_id=args.workspace_id)
    alerts_code, alerts_payload = _request_json(args.api_url, '/alerts?status_value=open', token=args.token, workspace_id=args.workspace_id)
    incidents_code, incidents_payload = _request_json(args.api_url, '/incidents?status_value=open', token=args.token, workspace_id=args.workspace_id)
    evidence_query = urlencode({'limit': 200})
    evidence_code, evidence_payload = _request_json(args.api_url, f'/ops/monitoring/evidence?{evidence_query}', token=args.token, workspace_id=args.workspace_id)

    detections = detections_payload.get('detections', []) if detections_code < 400 and isinstance(detections_payload, dict) else []
    alerts = alerts_payload.get('alerts', []) if alerts_code < 400 and isinstance(alerts_payload, dict) else []
    incidents = incidents_payload.get('incidents', []) if incidents_code < 400 and isinstance(incidents_payload, dict) else []
    evidence_rows = evidence_payload.get('evidence', []) if evidence_code < 400 and isinstance(evidence_payload, dict) else []

    now = datetime.now(timezone.utc)
    max_age = timedelta(minutes=max(1, args.stale_minutes))
    evidence_by_alert_id = _group_evidence_by_alert(evidence_rows if isinstance(evidence_rows, list) else [])
    stale_alert_ids = [
        str(alert.get('id'))
        for alert in alerts
        if isinstance(alert, dict) and _is_alert_stale(alert, now=now, max_age=max_age, evidence_by_alert_id=evidence_by_alert_id)
    ]

    resolved: list[str] = []
    for alert_id in stale_alert_ids:
        if not alert_id:
            continue
        code, _ = _request_json(args.api_url, f'/alerts/{alert_id}/resolve', token=args.token, workspace_id=args.workspace_id, method='POST')
        if code < 400 and code != 0:
            resolved.append(alert_id)

    runtime_recheck_code, runtime_recheck = _request_json(args.api_url, '/ops/monitoring/runtime-status', token=args.token, workspace_id=args.workspace_id)

    chain = _extract_chain(
        detections if isinstance(detections, list) else [],
        alerts if isinstance(alerts, list) else [],
        incidents if isinstance(incidents, list) else [],
        evidence_rows if isinstance(evidence_rows, list) else [],
    )

    risk_indicators = runtime_recheck.get('claim_safety_risk_indicators', []) if isinstance(runtime_recheck, dict) else []
    output = {
        'ok': True,
        'monitoring_run': run_payload,
        'runtime_before': runtime,
        'runtime_after': runtime_recheck,
        'checks': {
            'recent_real_event_count_gt_zero': int(runtime_recheck.get('recent_real_event_count') or 0) > 0,
            'last_real_event_at_present': bool(runtime_recheck.get('last_real_event_at')),
            'claim_validator_pass': str(runtime_recheck.get('claim_validator_status') or '') == 'PASS',
            'claim_safe_true': bool(runtime_recheck.get('claim_safe')),
            'no_recent_real_events_cleared': 'no_recent_real_events' not in [str(item) for item in risk_indicators],
        },
        'resolved_stale_alert_ids': resolved,
        'chain': chain,
        'api_status_codes': {
            'runtime_before': runtime_code,
            'runtime_after': runtime_recheck_code,
            'detections': detections_code,
            'alerts': alerts_code,
            'incidents': incidents_code,
            'evidence': evidence_code,
        },
    }
    print(json.dumps(output, indent=2, default=str))
    return 0 if all(output['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
