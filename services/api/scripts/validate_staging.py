#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Check:
    name: str
    command: list[str]
    passed: bool
    output: str
    skipped: bool = False


def run_check(name: str, command: list[str], *, env: dict[str, str] | None = None, skip: bool = False, skip_reason: str = '') -> Check:
    if skip:
        return Check(name=name, command=command, passed=True, output=skip_reason or 'skipped', skipped=True)
    process = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, env=env)
    output = (process.stdout + '\n' + process.stderr).strip()
    return Check(name=name, command=command, passed=process.returncode == 0, output=output)


def main() -> int:
    env = os.environ.copy()
    run_live_smoke = env.get('ENABLE_LIVE_PROVIDER_SMOKE', '').lower() == 'true'
    run_web_e2e = env.get('ENABLE_PLAYWRIGHT_E2E', 'true').lower() == 'true'

    web_env = env.copy()
    web_env.setdefault('NEXT_PUBLIC_LIVE_MODE_ENABLED', 'true')
    web_env.setdefault('API_URL', env.get('STAGING_API_URL', 'http://127.0.0.1:8000'))
    lockfile_exists = (REPO_ROOT / 'package-lock.json').exists() or (REPO_ROOT / 'apps/web/package-lock.json').exists()

    checks = [
        run_check('api_production_startup_validation', ['pytest', '-q', 'services/api/tests/test_production_startup_validation.py']),
        run_check('api_billing_runtime_validation', ['pytest', '-q', 'services/api/tests/test_billing_runtime.py']),
        run_check('api_auth_health_diagnostics', ['pytest', '-q', 'services/api/tests/test_auth_health_diagnostics.py']),
        run_check('web_build', ['npm', 'run', 'build', '--workspace', 'apps/web'], env=web_env),
        run_check('web_audit', ['npm', 'audit', '--workspace', 'apps/web', '--audit-level=high'], skip=not lockfile_exists, skip_reason='No npm lockfile present; generate lockfile in CI with registry access before audit.'),
        run_check(
            'web_playwright_e2e',
            ['npx', 'playwright', 'test', 'apps/web/tests/feature4-smoke.spec.ts'],
            skip=not run_web_e2e,
            skip_reason='ENABLE_PLAYWRIGHT_E2E=false',
        ),
        run_check(
            'live_provider_smoke',
            ['python', 'services/api/scripts/smoke_live_providers.py'],
            skip=not run_live_smoke,
            skip_reason='ENABLE_LIVE_PROVIDER_SMOKE is not true',
        ),
    ]

    payload = {
        'ok': all(check.passed for check in checks),
        'checks': [asdict(check) for check in checks],
    }
    print(json.dumps(payload, indent=2))
    print('\nValidation Summary:')
    for check in checks:
        if check.skipped:
            marker = 'SKIP'
        else:
            marker = 'PASS' if check.passed else 'FAIL'
        print(f"[{marker}] {check.name}: {' '.join(check.command)}")

    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
