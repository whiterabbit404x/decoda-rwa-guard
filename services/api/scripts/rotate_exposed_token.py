#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_AUDIT_LOOKBACK_MINUTES = 180


@dataclass
class ApiResponse:
    status: int
    payload: dict[str, Any]


class IncidentResponseError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_request(
    *,
    api_url: str,
    method: str,
    path: str,
    token: str | None = None,
    workspace_id: str | None = None,
    body: dict[str, Any] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> ApiResponse:
    headers = {'content-type': 'application/json'}
    if token:
        headers['authorization'] = f'Bearer {token}'
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    data = json.dumps(body).encode('utf-8') if body is not None else None
    url = f"{api_url.rstrip('/')}{path}"
    req = request.Request(url, method=method.upper(), headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode('utf-8')
            payload = json.loads(raw) if raw else {}
            return ApiResponse(status=response.status, payload=payload if isinstance(payload, dict) else {'raw': payload})
    except error.HTTPError as exc:
        detail = exc.read().decode('utf-8') if exc.fp else '{}'
        try:
            payload = json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            payload = {'detail': detail}
        return ApiResponse(status=exc.code, payload=payload if isinstance(payload, dict) else {'raw': payload})


def _workspace_from_user(user: dict[str, Any], explicit_workspace_id: str | None) -> str:
    if explicit_workspace_id:
        return explicit_workspace_id
    current = user.get('current_workspace') if isinstance(user.get('current_workspace'), dict) else {}
    workspace_id = str(current.get('id') or user.get('current_workspace_id') or '').strip()
    if not workspace_id:
        raise IncidentResponseError('Unable to resolve workspace id from /auth/me response; provide --workspace-id.')
    return workspace_id


def _signin_for_new_token(api_url: str, *, email: str, password: str, timeout_seconds: int) -> tuple[str, dict[str, Any]]:
    response = _json_request(
        api_url=api_url,
        method='POST',
        path='/auth/signin',
        body={'email': email, 'password': password},
        timeout_seconds=timeout_seconds,
    )
    if response.status != 200:
        raise IncidentResponseError(f'Failed to sign in for new token (status={response.status}): {response.payload}')
    if response.payload.get('mfa_required'):
        challenge_token = str(response.payload.get('challenge_token') or '')
        raise IncidentResponseError(
            'MFA is required for this user. Complete /auth/mfa/complete-signin manually to mint a replacement token. '
            f'challenge_token={challenge_token!r}'
        )
    access_token = str(response.payload.get('access_token') or '').strip()
    if not access_token:
        raise IncidentResponseError(f'/auth/signin succeeded but access_token was missing: {response.payload}')
    user = response.payload.get('user') if isinstance(response.payload.get('user'), dict) else {}
    return access_token, user


def _summarize_audit_activity(audit_logs: list[dict[str, Any]], *, lookback_minutes: int) -> dict[str, Any]:
    lookback_start = _utc_now() - timedelta(minutes=max(1, lookback_minutes))
    recent = [entry for entry in audit_logs if (_parse_timestamp(entry.get('created_at')) or datetime.min.replace(tzinfo=timezone.utc)) >= lookback_start]

    suspicious_prefixes = (
        'workspace.',
        'invitation.',
        'integration.',
        'template.',
        'export.',
        'target.',
        'asset.',
        'incident.',
        'action.',
        'finding.',
        'policy.',
    )
    suspicious_actions = [
        entry for entry in recent
        if isinstance(entry.get('action'), str)
        and (entry['action'].startswith(suspicious_prefixes) or entry['action'] in {'auth.mfa_disabled', 'auth.password_reset'})
    ]

    return {
        'lookback_minutes': lookback_minutes,
        'lookback_start': lookback_start.isoformat(),
        'recent_event_count': len(recent),
        'suspicious_event_count': len(suspicious_actions),
        'suspicious_events': suspicious_actions,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    leaked_me = _json_request(
        api_url=args.api_url,
        method='GET',
        path='/auth/me',
        token=args.leaked_token,
        timeout_seconds=args.timeout,
    )
    if leaked_me.status != 200:
        raise IncidentResponseError(f'Leaked token is not valid for /auth/me (status={leaked_me.status}): {leaked_me.payload}')
    leaked_user = leaked_me.payload.get('user') if isinstance(leaked_me.payload.get('user'), dict) else {}
    workspace_id = _workspace_from_user(leaked_user, args.workspace_id)

    sessions_before = _json_request(
        api_url=args.api_url,
        method='GET',
        path='/auth/sessions',
        token=args.leaked_token,
        workspace_id=workspace_id,
        timeout_seconds=args.timeout,
    )
    signout_all = _json_request(
        api_url=args.api_url,
        method='POST',
        path='/auth/signout-all',
        token=args.leaked_token,
        workspace_id=workspace_id,
        timeout_seconds=args.timeout,
    )
    if signout_all.status != 200:
        raise IncidentResponseError(f'Failed to revoke sessions via /auth/signout-all (status={signout_all.status}): {signout_all.payload}')

    new_token, signed_in_user = _signin_for_new_token(
        args.api_url,
        email=args.email,
        password=args.password,
        timeout_seconds=args.timeout,
    )
    new_workspace_id = _workspace_from_user(signed_in_user, workspace_id)

    diagnostics: dict[str, Any] = {}
    diagnostics['auth_me'] = _json_request(
        api_url=args.api_url,
        method='GET',
        path='/auth/me',
        token=new_token,
        timeout_seconds=args.timeout,
    ).payload
    diagnostics['auth_sessions'] = _json_request(
        api_url=args.api_url,
        method='GET',
        path='/auth/sessions',
        token=new_token,
        workspace_id=new_workspace_id,
        timeout_seconds=args.timeout,
    ).payload
    diagnostics['runtime_status'] = _json_request(
        api_url=args.api_url,
        method='GET',
        path='/ops/monitoring/runtime-status',
        token=new_token,
        workspace_id=new_workspace_id,
        timeout_seconds=args.timeout,
    ).payload

    history = _json_request(
        api_url=args.api_url,
        method='GET',
        path=f"/pilot/history?{parse.urlencode({'limit': args.audit_limit})}",
        token=new_token,
        workspace_id=new_workspace_id,
        timeout_seconds=args.timeout,
    )
    audit_logs = history.payload.get('audit_logs') if isinstance(history.payload.get('audit_logs'), list) else []
    audit_summary = _summarize_audit_activity(audit_logs, lookback_minutes=args.audit_lookback_minutes)

    report = {
        'executed_at': _utc_now().isoformat(),
        'api_url': args.api_url,
        'user_id': leaked_user.get('id'),
        'workspace_id': new_workspace_id,
        'steps': {
            'revoke_rotate_sessions': {
                'sessions_before_rotation': sessions_before.payload,
                'signout_all_response': signout_all.payload,
            },
            'new_token_issued': True,
            'diagnostics_with_new_token': diagnostics,
            'audit_recent_activity': audit_summary,
        },
        'notes': [
            'Audit findings are inferred from workspace/user audit logs in the configured lookback window.',
            'Per-token request telemetry is not exposed through public API routes, so suspicious events are heuristic-based.',
        ],
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Incident workflow: rotate leaked auth token, issue replacement, rerun diagnostics, and audit recent workspace activity.')
    parser.add_argument('--api-url', default='http://localhost:8000')
    parser.add_argument('--leaked-token', required=True, help='Exposed bearer token to revoke/rotate.')
    parser.add_argument('--email', required=True, help='User email used to issue replacement token after rotation.')
    parser.add_argument('--password', required=True, help='User password used to issue replacement token after rotation.')
    parser.add_argument('--workspace-id', default='', help='Optional workspace id override for x-workspace-id headers.')
    parser.add_argument('--audit-lookback-minutes', type=int, default=DEFAULT_AUDIT_LOOKBACK_MINUTES)
    parser.add_argument('--audit-limit', type=int, default=200)
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument('--output', default='services/api/artifacts/security/latest/token_rotation_report.json')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        report = run(args)
    except IncidentResponseError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1

    print(json.dumps(
        {
            'executed_at': report['executed_at'],
            'user_id': report.get('user_id'),
            'workspace_id': report.get('workspace_id'),
            'new_token_issued': report['steps']['new_token_issued'],
            'suspicious_event_count': report['steps']['audit_recent_activity']['suspicious_event_count'],
            'output': args.output,
        },
        indent=2,
    ))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
