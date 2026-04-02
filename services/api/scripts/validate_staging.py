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


def check_web_next_version_sync() -> Check:
    command = ['python', '-c', 'validate web next dependency lockstep']
    package_json = REPO_ROOT / 'apps/web/package.json'
    installed_next_json = REPO_ROOT / 'node_modules/next/package.json'

    try:
        declared = json.loads(package_json.read_text()).get('dependencies', {}).get('next')
    except Exception as exc:
        return Check(name='web_next_version_sync', command=command, passed=False, output=f'Unable to read apps/web/package.json: {exc}')

    if not declared:
        return Check(name='web_next_version_sync', command=command, passed=False, output='apps/web/package.json does not declare dependencies.next; cannot validate build dependency version.')

    if not installed_next_json.exists():
        return Check(
            name='web_next_version_sync',
            command=command,
            passed=False,
            output='node_modules/next/package.json is missing. Run npm install with registry access and rerun validation.',
        )

    try:
        installed = json.loads(installed_next_json.read_text()).get('version')
    except Exception as exc:
        return Check(name='web_next_version_sync', command=command, passed=False, output=f'Unable to read installed next version: {exc}')

    if declared != installed:
        return Check(
            name='web_next_version_sync',
            command=command,
            passed=False,
            output=f'Mismatch: apps/web/package.json declares next={declared} but installed node_modules has next={installed}.',
        )

    return Check(name='web_next_version_sync', command=command, passed=True, output=f'next dependency matches declared version ({installed}).')


def main() -> int:
    env = os.environ.copy()
    run_live_smoke = env.get('ENABLE_LIVE_PROVIDER_SMOKE', '').lower() == 'true'
    run_web_e2e = env.get('ENABLE_PLAYWRIGHT_E2E', 'true').lower() == 'true'

    web_env = env.copy()
    web_env.setdefault('NEXT_PUBLIC_LIVE_MODE_ENABLED', 'true')
    web_env.setdefault('API_URL', env.get('STAGING_API_URL', 'https://api.staging.example.com'))
    lockfile_exists = (REPO_ROOT / 'package-lock.json').exists() or (REPO_ROOT / 'apps/web/package-lock.json').exists()

    checks = [
        run_check('api_production_startup_validation', ['pytest', '-q', 'services/api/tests/test_production_startup_validation.py']),
        run_check('api_billing_runtime_validation', ['pytest', '-q', 'services/api/tests/test_billing_runtime.py']),
        run_check('api_auth_health_diagnostics', ['pytest', '-q', 'services/api/tests/test_auth_health_diagnostics.py']),
        check_web_next_version_sync(),
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
