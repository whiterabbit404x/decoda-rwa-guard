#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_iso(value: object) -> datetime | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


def _contains_reason_code(container: object, code: str) -> bool:
    if isinstance(container, dict):
        for value in container.values():
            if _contains_reason_code(value, code):
                return True
    if isinstance(container, list):
        return any(_contains_reason_code(item, code) for item in container)
    return str(container or '').strip().lower() == code.lower()


def _read_runtime_payload(api_url: str, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    req = Request(f'{api_url.rstrip("/")}/ops/monitoring/runtime-status', headers=headers)
    with urlopen(req, timeout=20) as resp:  # nosec B310
        status = int(getattr(resp, 'status', 200) or 200)
        payload = json.loads(resp.read().decode('utf-8'))
    return status, payload if isinstance(payload, dict) else {}


def _status_reason_indicates_unavailable(reason: str) -> bool:
    normalized = reason.strip().lower()
    if not normalized:
        return False
    return any(
        token in normalized
        for token in (
            'runtime_status_unavailable',
            'runtime unavailable',
            'runtime_unavailable',
        )
    )


def main() -> int:
    api_url = (os.getenv('API_URL') or 'http://localhost:8000').strip().rstrip('/')
    token = os.getenv('PILOT_AUTH_TOKEN', '').strip()
    workspace_id = (os.getenv('RUNTIME_STATUS_WORKSPACE_ID') or os.getenv('WORKSPACE_ID') or '').strip()
    now = datetime.now(timezone.utc)
    max_clock_skew_seconds = 60
    max_coverage_staleness_seconds = max(60, int(os.getenv('RUNTIME_STATUS_MAX_COVERAGE_STALENESS_SECONDS', '900')))
    evidence_output_path = (os.getenv('RUNTIME_STATUS_GATE_EVIDENCE_PATH') or '').strip()

    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if workspace_id:
        headers['X-Workspace-Id'] = workspace_id

    try:
        status_code, payload = _read_runtime_payload(api_url, headers)
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace') if hasattr(exc, 'read') else str(exc)
        print(json.dumps({'ok': False, 'error': 'runtime_status_http_error', 'status_code': exc.code, 'detail': detail}, indent=2))
        return 2
    except URLError as exc:
        print(json.dumps({'ok': False, 'error': 'runtime_status_connection_error', 'detail': str(exc)}, indent=2))
        return 2

    summary = payload.get('workspace_monitoring_summary') if isinstance(payload.get('workspace_monitoring_summary'), dict) else {}

    workspace_runtime_id = payload.get('workspace_id') or summary.get('workspace_id')
    workspace_slug = payload.get('workspace_slug') or summary.get('workspace_slug')
    status_reason = str(payload.get('status_reason') or summary.get('status_reason') or '').strip()
    configuration_reason = str(payload.get('configuration_reason') or summary.get('configuration_reason') or '').strip()
    freshness_status = str(payload.get('freshness_status') or summary.get('freshness_status') or '').strip().lower()
    monitoring_mode = str(payload.get('monitoring_mode') or summary.get('monitoring_mode') or '').strip().lower()
    runtime_status = str(payload.get('runtime_status') or summary.get('runtime_status') or '').strip().lower()
    evidence_source = str(payload.get('evidence_source') or summary.get('evidence_source') or '').strip().lower()

    configured_systems = int((payload.get('configured_systems') if payload.get('configured_systems') is not None else summary.get('configured_systems')) or 0)
    reporting_systems = int((payload.get('reporting_systems') if payload.get('reporting_systems') is not None else summary.get('reporting_systems')) or 0)
    valid_protected_assets = int((payload.get('valid_protected_assets') if payload.get('valid_protected_assets') is not None else summary.get('valid_protected_assets')) or 0)
    linked_monitored_systems = int((payload.get('linked_monitored_systems') if payload.get('linked_monitored_systems') is not None else summary.get('linked_monitored_systems')) or 0)
    enabled_configs = int((payload.get('enabled_configs') if payload.get('enabled_configs') is not None else summary.get('enabled_configs')) or 0)
    valid_link_count = int((payload.get('valid_link_count') if payload.get('valid_link_count') is not None else summary.get('valid_link_count')) or 0)

    last_telemetry_at = _parse_iso(payload.get('last_telemetry_at') or summary.get('last_telemetry_at'))
    last_coverage_telemetry_at = _parse_iso(payload.get('last_coverage_telemetry_at') or summary.get('last_coverage_telemetry_at'))

    is_live_claim = monitoring_mode in {'live', 'hybrid'} or evidence_source == 'live' or runtime_status in {'healthy', 'degraded', 'idle'}
    workspace_configured = any(value > 0 for value in (configured_systems, valid_protected_assets, linked_monitored_systems, enabled_configs, valid_link_count))

    field_reason_codes = payload.get('field_reason_codes') if isinstance(payload.get('field_reason_codes'), dict) else summary.get('field_reason_codes')
    count_reason_codes = payload.get('count_reason_codes') if isinstance(payload.get('count_reason_codes'), dict) else summary.get('count_reason_codes')

    failures: list[str] = []

    if status_code != 200:
        failures.append(f'runtime-status returned HTTP {status_code}.')

    if not workspace_runtime_id or not workspace_slug:
        failures.append('workspace_id/workspace_slug must both be non-null.')

    if status_reason.startswith('runtime_status_degraded:'):
        failures.append(f'status_reason indicates degraded runtime: {status_reason}.')
    if _status_reason_indicates_unavailable(status_reason):
        failures.append(f'status_reason indicates runtime unavailable: {status_reason}.')

    if configuration_reason == 'runtime_status_unavailable':
        failures.append('configuration_reason=runtime_status_unavailable indicates telemetry is unavailable.')

    if evidence_source != 'live':
        failures.append(f'evidence_source must be live for pre-demo/pre-release gate (got {evidence_source or "<missing>"}).')

    if is_live_claim and freshness_status == 'unavailable':
        failures.append('freshness_status=unavailable while runtime claims live/hybrid mode.')

    if workspace_configured:
        if reporting_systems <= 0:
            failures.append('workspace is configured but reporting_systems is zero.')
    if is_live_claim and configured_systems > 0:
        if valid_protected_assets <= 0:
            failures.append('configured_systems>0 but valid_protected_assets is zero.')
        if linked_monitored_systems <= 0:
            failures.append('configured_systems>0 but linked_monitored_systems is zero.')
        if enabled_configs <= 0:
            failures.append('configured_systems>0 but enabled_configs is zero.')
        if valid_link_count <= 0:
            failures.append('configured_systems>0 but valid_link_count is zero.')

    if is_live_claim:
        if freshness_status != 'fresh':
            failures.append(f'live mode requires freshness_status=fresh (got {freshness_status or "<missing>"}).')
        if not last_telemetry_at or not last_coverage_telemetry_at:
            failures.append('live mode requires non-null last_telemetry_at and last_coverage_telemetry_at.')
        else:
            if (
                (last_telemetry_at - now).total_seconds() > max_clock_skew_seconds
                or (last_coverage_telemetry_at - now).total_seconds() > max_clock_skew_seconds
            ):
                failures.append('telemetry timestamps cannot be in the future.')
    if not last_coverage_telemetry_at:
        failures.append('last_coverage_telemetry_at must be non-null for pre-demo/pre-release gate.')
    else:
        coverage_age_seconds = int((now - last_coverage_telemetry_at).total_seconds())
        if coverage_age_seconds > max_coverage_staleness_seconds:
            failures.append(
                'last_coverage_telemetry_at is stale '
                f'({coverage_age_seconds}s old > {max_coverage_staleness_seconds}s window).'
            )

    if _contains_reason_code(field_reason_codes, 'query_failure') or _contains_reason_code(count_reason_codes, 'query_failure'):
        failures.append('runtime payload includes query_failure markers in reason codes.')

    if _contains_reason_code(field_reason_codes, 'schema_drift') or _contains_reason_code(count_reason_codes, 'schema_drift'):
        failures.append('runtime payload includes schema_drift markers, indicating runtime query mismatch.')

    ok = len(failures) == 0
    result = {
        'ok': ok,
        'api_url': api_url,
        'workspace_id': workspace_runtime_id,
        'workspace_slug': workspace_slug,
        'monitoring_mode': monitoring_mode or None,
        'runtime_status': runtime_status or None,
        'evidence_source': evidence_source or None,
        'freshness_status': freshness_status or None,
        'configured_systems': configured_systems,
        'reporting_systems': reporting_systems,
        'valid_protected_assets': valid_protected_assets,
        'linked_monitored_systems': linked_monitored_systems,
        'enabled_configs': enabled_configs,
        'valid_link_count': valid_link_count,
        'last_telemetry_at': last_telemetry_at.isoformat() if last_telemetry_at else None,
        'last_coverage_telemetry_at': last_coverage_telemetry_at.isoformat() if last_coverage_telemetry_at else None,
        'status_reason': status_reason or None,
        'configuration_reason': configuration_reason or None,
        'max_coverage_staleness_seconds': max_coverage_staleness_seconds,
        'failures': failures,
    }
    if evidence_output_path:
        output_path = Path(evidence_output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result['evidence_output_path'] = str(output_path)
        output_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
