#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _read_runtime_payload(api_url: str, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    request = Request(f"{api_url.rstrip('/')}/ops/monitoring/runtime-status", headers=headers)
    with urlopen(request, timeout=20) as response:  # nosec B310
        status = int(getattr(response, 'status', 200) or 200)
        payload = json.loads(response.read().decode('utf-8'))
    return status, payload if isinstance(payload, dict) else {}


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _extract_state(payload: dict[str, object]) -> dict[str, object]:
    summary = payload.get('workspace_monitoring_summary') if isinstance(payload.get('workspace_monitoring_summary'), dict) else {}
    runtime_status = str(payload.get('runtime_status') or summary.get('runtime_status') or '').strip().lower()
    configured_systems = _as_int(payload.get('configured_systems') if payload.get('configured_systems') is not None else summary.get('configured_systems'))
    workspace_id = payload.get('workspace_id') or summary.get('workspace_id')
    workspace_slug = payload.get('workspace_slug') or summary.get('workspace_slug')
    status_reason = str(payload.get('status_reason') or summary.get('status_reason') or '').strip() or None
    return {
        'runtime_status': runtime_status,
        'configured_systems': configured_systems,
        'workspace_id': workspace_id,
        'workspace_slug': workspace_slug,
        'status_reason': status_reason,
    }


def main() -> int:
    api_url = (os.getenv('API_URL') or 'http://localhost:8000').strip().rstrip('/')
    auth_token = (os.getenv('PILOT_AUTH_TOKEN') or '').strip()
    workspace_id = (os.getenv('RUNTIME_STATUS_WORKSPACE_ID') or os.getenv('WORKSPACE_ID') or '').strip()
    fail_statuses = {
        token.strip().lower()
        for token in (os.getenv('RUNTIME_STATUS_RELEASE_GATE_FAIL_STATUSES') or 'degraded,offline').split(',')
        if token.strip()
    }
    attempts = max(1, int(os.getenv('RUNTIME_STATUS_RELEASE_GATE_ATTEMPTS', '3')))
    interval_seconds = max(0, int(os.getenv('RUNTIME_STATUS_RELEASE_GATE_INTERVAL_SECONDS', '20')))

    headers = {'Content-Type': 'application/json'}
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'
    if workspace_id:
        headers['X-Workspace-Id'] = workspace_id

    observations: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []

    for idx in range(attempts):
        attempt = idx + 1
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            status_code, payload = _read_runtime_payload(api_url, headers)
            state = _extract_state(payload)
            state.update({'attempt': attempt, 'observed_at': observed_at, 'http_status': status_code})
            observations.append(state)
            configured_systems = int(state['configured_systems'])
            runtime_status = str(state['runtime_status'])
            if status_code == 200 and (configured_systems <= 0 or runtime_status not in fail_statuses):
                result = {
                    'ok': True,
                    'api_url': api_url,
                    'workspace_id': state['workspace_id'],
                    'workspace_slug': state['workspace_slug'],
                    'attempts': attempt,
                    'fail_statuses': sorted(fail_statuses),
                    'observations': observations,
                }
                print(json.dumps(result, indent=2))
                return 0
        except HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace') if hasattr(exc, 'read') else str(exc)
            errors.append({'attempt': attempt, 'error': 'http_error', 'status_code': exc.code, 'detail': detail, 'observed_at': observed_at})
        except URLError as exc:
            errors.append({'attempt': attempt, 'error': 'connection_error', 'detail': str(exc), 'observed_at': observed_at})

        if attempt < attempts and interval_seconds > 0:
            time.sleep(interval_seconds)

    failure_messages: list[str] = []
    if errors:
        failure_messages.extend(
            [f"attempt {entry['attempt']}: {entry['error']} ({entry.get('status_code') or entry.get('detail')})" for entry in errors]
        )
    for observation in observations:
        if int(observation.get('configured_systems') or 0) > 0 and str(observation.get('runtime_status') or '') in fail_statuses:
            failure_messages.append(
                'attempt '
                f"{observation['attempt']}: runtime_status={observation.get('runtime_status')} "
                f"with configured_systems={observation.get('configured_systems')} status_reason={observation.get('status_reason')}"
            )

    result = {
        'ok': False,
        'api_url': api_url,
        'workspace_id': observations[-1].get('workspace_id') if observations else workspace_id or None,
        'workspace_slug': observations[-1].get('workspace_slug') if observations else None,
        'attempts': attempts,
        'fail_statuses': sorted(fail_statuses),
        'observations': observations,
        'errors': errors,
        'failures': failure_messages or ['runtime-status release gate failed with no successful observations.'],
    }
    print(json.dumps(result, indent=2))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
