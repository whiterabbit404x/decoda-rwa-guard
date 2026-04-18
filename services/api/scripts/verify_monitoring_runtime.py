from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import error, parse, request


def _parse_iso8601(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = f'{text[:-1]}+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _request_json(
    *,
    api_url: str,
    path: str,
    token: str,
    workspace_id: str | None = None,
    method: str = 'GET',
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{api_url.rstrip('/')}{path}"
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    req = request.Request(url, method=method, data=body)
    req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Accept', 'application/json')
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    if workspace_id:
        req.add_header('x-workspace-id', workspace_id)
    try:
        with request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{method} {path} failed: {exc.code} {detail}') from exc
    except error.URLError as exc:
        raise RuntimeError(f'{method} {path} failed: {exc}') from exc


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate monitoring worker runtime health and evidence freshness.')
    parser.add_argument('--api-url', required=True, help='API base URL, e.g. https://<service>.up.railway.app')
    parser.add_argument('--token', required=True, help='Operator bearer token')
    parser.add_argument('--workspace-id', default=None, help='Workspace UUID')
    parser.add_argument('--poll-seconds', type=int, default=30, help='Delay between health checks')
    parser.add_argument('--cycles', type=int, default=2, help='Number of health checks')
    parser.add_argument('--evidence-max-age-minutes', type=int, default=20, help='Maximum allowed age for latest evidence row')
    parser.add_argument('--trigger-run', action='store_true', help='Invoke POST /ops/monitoring/run before polling')
    parser.add_argument('--worker-name', default='railway-monitoring-worker')
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()

    failures: list[str] = []
    health_snapshots: list[dict[str, Any]] = []

    if args.trigger_run:
        _request_json(
            api_url=args.api_url,
            path='/ops/monitoring/run',
            token=args.token,
            workspace_id=args.workspace_id,
            method='POST',
            payload={'worker_name': args.worker_name, 'limit': max(1, min(args.limit, 200))},
        )

    for index in range(max(1, args.cycles)):
        health = _request_json(
            api_url=args.api_url,
            path='/ops/monitoring/health',
            token=args.token,
            workspace_id=args.workspace_id,
        )
        health_snapshots.append(health)
        if index < args.cycles - 1:
            time.sleep(max(1, args.poll_seconds))

    runtime = _request_json(
        api_url=args.api_url,
        path='/ops/monitoring/runtime-status',
        token=args.token,
        workspace_id=args.workspace_id,
    )
    evidence_payload = _request_json(
        api_url=args.api_url,
        path=f"/ops/monitoring/evidence?{parse.urlencode({'limit': 25})}",
        token=args.token,
        workspace_id=args.workspace_id,
    )

    latest_health = health_snapshots[-1] if health_snapshots else {}
    _require(bool(latest_health.get('worker_running')), 'health.worker_running is not true', failures)
    _require(not bool(latest_health.get('heartbeat_stale')), 'health.heartbeat_stale is true', failures)

    if len(health_snapshots) >= 2:
        first_cycle = _parse_iso8601(health_snapshots[0].get('last_cycle_at'))
        last_cycle = _parse_iso8601(health_snapshots[-1].get('last_cycle_at'))
        _require(
            first_cycle is not None and last_cycle is not None and last_cycle >= first_cycle,
            'last_cycle_at did not progress across polling window',
            failures,
        )
        first_overdue = health_snapshots[0].get('overdue_targets')
        last_overdue = health_snapshots[-1].get('overdue_targets')
        if isinstance(first_overdue, int) and isinstance(last_overdue, int):
            _require(last_overdue <= first_overdue, 'overdue_targets increased during polling window', failures)

    _require(int(runtime.get('reporting_systems') or 0) > 0, 'runtime-status.reporting_systems <= 0', failures)
    _require(not str(runtime.get('status_reason') or '').startswith('degraded'), 'runtime-status has degraded status_reason', failures)

    evidence_rows = evidence_payload.get('evidence') if isinstance(evidence_payload.get('evidence'), list) else []
    _require(bool(evidence_rows), 'evidence endpoint returned no rows', failures)
    if evidence_rows:
        latest_observed_at = _parse_iso8601(evidence_rows[0].get('observed_at'))
        freshness_deadline = datetime.now(timezone.utc) - timedelta(minutes=max(1, args.evidence_max_age_minutes))
        _require(
            latest_observed_at is not None and latest_observed_at >= freshness_deadline,
            f'latest evidence row is older than {args.evidence_max_age_minutes} minutes',
            failures,
        )

    summary = {
        'checks_passed': len(failures) == 0,
        'failures': failures,
        'health': latest_health,
        'runtime_status': runtime,
        'evidence_count': len(evidence_rows),
        'latest_evidence': evidence_rows[0] if evidence_rows else None,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
