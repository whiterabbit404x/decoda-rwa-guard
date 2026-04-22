#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _headers() -> dict[str, str]:
    headers = {'accept': 'application/json'}
    token = os.getenv('AUTH_TOKEN', '').strip()
    workspace_id = os.getenv('WORKSPACE_ID', '').strip()
    if token:
        headers['authorization'] = f'Bearer {token}'
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    return headers


def _request_json(base_url: str, path: str) -> tuple[int | None, Any, str | None]:
    url = urllib.parse.urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
    request = urllib.request.Request(url, headers=_headers(), method='GET')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            status = int(getattr(response, 'status', 200))
            payload = json.loads(response.read().decode('utf-8'))
            return status, payload, None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {'detail': body}
        return exc.code, payload, f'HTTP {exc.code}'
    except Exception as exc:  # noqa: BLE001
        return None, {}, str(exc)


def _iso_age_seconds(value: Any) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _extract_list(payload: Any, preferred_keys: list[str]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in preferred_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def main() -> int:
    base_url = os.getenv('MONITORING_BASE_URL', 'http://127.0.0.1:8000')
    max_event_age_seconds = int(os.getenv('MAX_EVENT_AGE_SECONDS', '900'))

    checks: list[CheckResult] = []

    status_code, runtime_payload, runtime_error = _request_json(base_url, '/ops/monitoring/runtime-status')
    if runtime_error and not isinstance(runtime_payload, dict):
        print(json.dumps({'ok': False, 'error': runtime_error, 'endpoint': '/ops/monitoring/runtime-status'}, indent=2))
        return 1

    summary = runtime_payload.get('workspace_monitoring_summary') if isinstance(runtime_payload, dict) else None
    if not isinstance(summary, dict):
        summary = runtime_payload if isinstance(runtime_payload, dict) else {}

    blocking_fields = {
        'telemetry_freshness': summary.get('telemetry_freshness'),
        'confidence': summary.get('confidence'),
        'evidence_source_summary': summary.get('evidence_source_summary'),
        'status_reason': summary.get('status_reason'),
        'guard_flags': summary.get('guard_flags'),
        'db_failure_reason': summary.get('db_failure_reason'),
    }
    blocking_reasons = []
    if str(blocking_fields['telemetry_freshness'] or '').lower() == 'unavailable':
        blocking_reasons.append('telemetry_unavailable')
    if str(blocking_fields['confidence'] or '').lower() in {'low', 'unavailable', ''}:
        blocking_reasons.append('confidence_not_verifiable')
    if str(blocking_fields['evidence_source_summary'] or '').lower() in {'none', 'simulator', 'replay', ''}:
        blocking_reasons.append('evidence_not_live')
    if blocking_fields['status_reason']:
        blocking_reasons.append(f"status_reason:{blocking_fields['status_reason']}")
    if isinstance(blocking_fields['guard_flags'], list) and blocking_fields['guard_flags']:
        blocking_reasons.append('guard_flags_present')
    if blocking_fields['db_failure_reason']:
        blocking_reasons.append('db_failure_reason_present')

    checks.append(
        CheckResult(
            name='runtime truth payload fields',
            ok=status_code == 200,
            detail=f'status={status_code} fields={blocking_fields}',
        )
    )

    health_code, health_payload, _ = _request_json(base_url, '/ops/monitoring/health')
    worker_running = bool((health_payload or {}).get('worker_running')) if isinstance(health_payload, dict) else False
    provider_reachable = bool((runtime_payload or {}).get('provider_reachable')) if isinstance(runtime_payload, dict) else False
    db_failure_reason = summary.get('db_failure_reason') if isinstance(summary, dict) else None
    checks.append(CheckResult('monitoring worker running', worker_running, f'worker_running={worker_running}'))
    checks.append(CheckResult('provider feed reachable', provider_reachable, f'provider_reachable={provider_reachable}'))
    checks.append(CheckResult('DB writes succeeding', not bool(db_failure_reason), f'db_failure_reason={db_failure_reason!r}'))

    systems_code, systems_payload, _ = _request_json(base_url, '/monitoring/systems')
    systems = _extract_list(systems_payload, ['systems', 'items'])
    enabled_systems = [row for row in systems if bool(row.get('is_enabled', True))]
    stale_or_missing_events: list[str] = []
    for row in enabled_systems:
        system_id = str(row.get('id') or 'unknown')
        event_age = _iso_age_seconds(row.get('last_event_at'))
        heartbeat_age = _iso_age_seconds(row.get('last_heartbeat'))
        if event_age is None or event_age > max_event_age_seconds:
            if heartbeat_age is not None:
                stale_or_missing_events.append(f'{system_id}:heartbeat_only_or_stale_event')
            else:
                stale_or_missing_events.append(f'{system_id}:no_event_no_heartbeat')

    checks.append(
        CheckResult(
            name='enabled systems have fresh last_event_at (not heartbeat-only)',
            ok=systems_code == 200 and not stale_or_missing_events,
            detail=f'status={systems_code} enabled={len(enabled_systems)} issues={stale_or_missing_events}',
        )
    )

    evidence_code, evidence_payload, _ = _request_json(base_url, '/ops/monitoring/evidence?limit=100')
    detections_code, detections_payload, _ = _request_json(base_url, '/detections?limit=100')
    alerts_code, alerts_payload, _ = _request_json(base_url, '/alerts?limit=100')

    evidence_rows = _extract_list(evidence_payload, ['evidence', 'items'])
    detection_rows = _extract_list(detections_payload, ['detections', 'items'])
    alert_rows = _extract_list(alerts_payload, ['alerts', 'items'])

    detections_by_id = {str(row.get('id')): row for row in detection_rows if row.get('id')}
    alerts_by_detection_id = {
        str(row.get('detection_id')): row
        for row in alert_rows
        if row.get('detection_id')
    }
    linked_chain = None
    for ev in evidence_rows:
        detection_id = ev.get('detection_id') or ev.get('linked_detection_id')
        if not detection_id:
            continue
        detection = detections_by_id.get(str(detection_id))
        if not detection:
            continue
        alert = alerts_by_detection_id.get(str(detection_id))
        if alert:
            linked_chain = {
                'evidence_id': ev.get('id'),
                'detection_id': detection.get('id'),
                'alert_id': alert.get('id'),
            }
            break

    checks.append(
        CheckResult(
            name='real evidence-linked chain (evidence → detection → alert)',
            ok=bool(linked_chain),
            detail=(
                f'evidence_status={evidence_code} detections_status={detections_code} alerts_status={alerts_code} '
                f'chain={linked_chain}'
            ),
        )
    )

    degraded = str((runtime_payload if isinstance(runtime_payload, dict) else {}).get('mode') or '').upper() == 'DEGRADED'
    telemetry_available = str(blocking_fields.get('telemetry_freshness') or '').lower() != 'unavailable'
    checks.append(
        CheckResult(
            name='status can leave DEGRADED only when telemetry is not Unavailable',
            ok=(not degraded) if telemetry_available else degraded,
            detail=f"mode={runtime_payload.get('mode') if isinstance(runtime_payload, dict) else None} telemetry_freshness={blocking_fields.get('telemetry_freshness')}",
        )
    )

    payload = {
        'ok': all(item.ok for item in checks) and not blocking_reasons,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'base_url': base_url,
        'status_code': status_code,
        'blocking_fields': blocking_fields,
        'blocking_reasons': blocking_reasons,
        'checks': [item.__dict__ for item in checks],
        'health_status_code': health_code,
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
