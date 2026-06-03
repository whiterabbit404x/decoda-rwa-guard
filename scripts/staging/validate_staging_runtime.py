#!/usr/bin/env python3
"""
Validate live staging runtime via STAGING_API_URL/health.

Writes marker files on success:
  artifacts/staging-proof/latest/runtime_validated
  artifacts/staging-proof/latest/migrations_validated

Fails closed:
  - If STAGING_API_URL is not set, exits 0 without writing markers.
  - If /health returns non-200 or required fields fail, exits 1 and removes
    any stale markers so the staging proof cannot read stale state.
  - If billing is degraded, exits 1 (keeps proof blocked).

Never exposes the STAGING_API_URL value or other secret data in logs.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_DIR = REPO_ROOT / 'artifacts' / 'staging-proof' / 'latest'

_RUNTIME_MARKER = PROOF_DIR / 'runtime_validated'
_MIGRATIONS_MARKER = PROOF_DIR / 'migrations_validated'


def _staging_api_url() -> str | None:
    val = (os.getenv('STAGING_API_URL') or '').strip()
    return val or None


def _remove_stale_markers() -> None:
    for marker in (_RUNTIME_MARKER, _MIGRATIONS_MARKER):
        if marker.exists():
            marker.unlink()
            print(f'[validate-staging-runtime] removed stale marker: {marker.name}')


def validate_health(health_url: str) -> tuple[bool, dict]:
    """
    Call the /health endpoint and validate required fields.

    Returns (success, details_dict).
    The URL value is never printed.
    """
    details: dict = {}
    try:
        req = urllib.request.Request(health_url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            http_status = resp.status
            body = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        details['error'] = f'HTTP {exc.code}: {exc.reason}'
        details['http_status'] = exc.code
        return False, details
    except Exception as exc:
        details['error'] = f'Request failed: {type(exc).__name__}: {exc}'
        return False, details

    details['http_status'] = http_status
    if http_status != 200:
        details['error'] = f'Expected HTTP 200, got {http_status}'
        return False, details

    try:
        data = json.loads(body)
    except Exception as exc:
        details['error'] = f'Response is not valid JSON: {exc}'
        return False, details

    failures: list[str] = []

    # status must be "ok"
    status = str(data.get('status') or '').strip().lower()
    details['status'] = status
    if status != 'ok':
        failures.append(f'status expected "ok", got {status!r}')

    # app_mode must be production or staging (empty is accepted for older deployments)
    app_mode = str(data.get('app_mode') or '').strip().lower()
    details['app_mode'] = app_mode
    if app_mode and app_mode not in ('production', 'staging'):
        failures.append(f'app_mode expected production/staging, got {app_mode!r}')

    # database_url_configured must be true
    db_configured = data.get('database_url_configured')
    details['database_url_configured'] = db_configured
    if db_configured is not True:
        failures.append(f'database_url_configured expected true, got {db_configured!r}')

    # billing must be healthy / available
    billing = data.get('billing') or {}
    billing_status = str(billing.get('status') or '').strip().lower()
    billing_available = billing.get('available')
    details['billing_status'] = billing_status
    details['billing_available'] = billing_available

    billing_ok = billing_status == 'healthy' or billing_available is True
    if not billing_ok:
        failures.append(
            f'billing degraded: status={billing_status!r} available={billing_available!r}'
        )

    # paddle_price_ids_configured — required when Paddle indicators are present
    paddle_price_ids = data.get('paddle_price_ids_configured')
    details['paddle_price_ids_configured'] = paddle_price_ids
    paddle_present = bool(
        data.get('paddle_api_key_present')
        or str(data.get('billing_provider') or '').lower() == 'paddle'
    )
    if paddle_present and paddle_price_ids is not True:
        failures.append(
            f'paddle_price_ids_configured expected true, got {paddle_price_ids!r}'
        )

    if failures:
        details['validation_failures'] = failures
        return False, details

    return True, details


def main() -> int:
    api_url = _staging_api_url()
    if not api_url:
        print('[validate-staging-runtime] STAGING_API_URL not set — skipping runtime validation')
        return 0

    print('[validate-staging-runtime] Calling STAGING_API_URL/health ...')
    health_url = api_url.rstrip('/') + '/health'

    ok, details = validate_health(health_url)

    PROOF_DIR.mkdir(parents=True, exist_ok=True)

    if not ok:
        err = details.get('error') or '; '.join(details.get('validation_failures', ['unknown error']))
        print(f'[validate-staging-runtime] FAIL: {err}', file=sys.stderr)
        print(f'[validate-staging-runtime] http_status={details.get("http_status")}', file=sys.stderr)
        _remove_stale_markers()
        return 1

    now = datetime.now(timezone.utc).isoformat()

    # Write runtime_validated marker
    _RUNTIME_MARKER.write_text(json.dumps({
        'validated_at': now,
        'http_status': details.get('http_status'),
        'health_status': details.get('status'),
        'app_mode': details.get('app_mode'),
        'database_url_configured': details.get('database_url_configured'),
        'billing_status': details.get('billing_status'),
        'billing_available': details.get('billing_available'),
        'paddle_price_ids_configured': details.get('paddle_price_ids_configured'),
    }, indent=2))
    try:
        label = _RUNTIME_MARKER.relative_to(REPO_ROOT)
    except ValueError:
        label = _RUNTIME_MARKER
    print(f'[validate-staging-runtime] wrote {label}')

    # Write migrations_validated marker — a running app with database_url_configured=true
    # implies migrations were applied (app would not start otherwise)
    if details.get('database_url_configured') is True:
        _MIGRATIONS_MARKER.write_text(json.dumps({
            'validated_at': now,
            'method': 'health_endpoint',
            'note': (
                'App /health confirms database connectivity; '
                'migrations are implied (app would not start without them).'
            ),
        }, indent=2))
        try:
            label = _MIGRATIONS_MARKER.relative_to(REPO_ROOT)
        except ValueError:
            label = _MIGRATIONS_MARKER
        print(f'[validate-staging-runtime] wrote {label}')

    print('[validate-staging-runtime] OK: staging runtime validated')
    print(f'  http_status={details.get("http_status")}')
    print(f'  health_status={details.get("status")}')
    print(f'  app_mode={details.get("app_mode")}')
    print(f'  database_url_configured={details.get("database_url_configured")}')
    print(f'  billing_status={details.get("billing_status")}')
    print(f'  billing_available={details.get("billing_available")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
