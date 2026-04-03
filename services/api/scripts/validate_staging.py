#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

CATEGORIES = [
    'local_repo_integrity',
    'frontend_build_reproducibility',
    'browser_e2e_runtime',
    'api_runtime_readiness',
    'live_provider_configuration',
    'staging_evidence',
]


@dataclass
class ValidationCheck:
    category: str
    name: str
    command: list[str]
    status: str
    detail: str
    remediation: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == 'pass'


def run_command(category: str, name: str, command: list[str], *, env: dict[str, str] | None = None, remediation: list[str] | None = None) -> ValidationCheck:
    process = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, env=env)
    output = (process.stdout + '\n' + process.stderr).strip()
    status = 'pass' if process.returncode == 0 else 'fail'
    return ValidationCheck(
        category=category,
        name=name,
        command=command,
        status=status,
        detail=output,
        remediation=remediation or [],
        metadata={'returncode': process.returncode},
    )


def check_playwright_runtime() -> ValidationCheck:
    node_check = subprocess.run(
        [
            'node',
            '-e',
            (
                "const fs=require('fs');"
                "let pkg='';"
                "try{pkg=require.resolve('playwright/package.json');}catch(e){console.log(JSON.stringify({state:'missing_package',message:e.message}));process.exit(2);}"
                "const p=require('playwright');const path=p.chromium.executablePath();"
                "console.log(JSON.stringify({state:fs.existsSync(path)?'ready':'missing_browser',pkg,path}));"
            ),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if node_check.returncode not in {0, 2}:
        return ValidationCheck(
            category='browser_e2e_runtime',
            name='playwright_runtime_detection',
            command=['node', '-e', 'playwright runtime detection'],
            status='fail',
            detail=(node_check.stdout + '\n' + node_check.stderr).strip(),
            remediation=[
                'Install dependencies first: `npm ci`.',
                'Then install browser runtime: `make install-web-test-runtime`.',
            ],
        )

    payload_raw = (node_check.stdout or '').strip().splitlines()[-1] if (node_check.stdout or '').strip() else '{}'
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        payload = {'state': 'unknown', 'raw': payload_raw}

    state = payload.get('state')
    if state == 'ready':
        return ValidationCheck(
            category='browser_e2e_runtime',
            name='playwright_runtime_detection',
            command=['node', '-e', 'playwright runtime detection'],
            status='pass',
            detail=f"Playwright package and Chromium runtime are available at {payload.get('path')}",
            metadata=payload,
        )
    if state == 'missing_package':
        return ValidationCheck(
            category='browser_e2e_runtime',
            name='playwright_runtime_detection',
            command=['node', '-e', 'playwright runtime detection'],
            status='fail',
            detail='Playwright package is not installed in node_modules.',
            remediation=[
                'Run `npm ci` from repository root.',
                'Then run `make install-web-test-runtime`.',
            ],
            metadata=payload,
        )
    if state == 'missing_browser':
        return ValidationCheck(
            category='browser_e2e_runtime',
            name='playwright_runtime_detection',
            command=['node', '-e', 'playwright runtime detection'],
            status='fail',
            detail=f"Playwright package is installed but Chromium runtime is missing at {payload.get('path')}",
            remediation=[
                'Run `make install-web-test-runtime` (or `npx playwright install chromium`).',
            ],
            metadata=payload,
        )
    return ValidationCheck(
        category='browser_e2e_runtime',
        name='playwright_runtime_detection',
        command=['node', '-e', 'playwright runtime detection'],
        status='fail',
        detail=f'Unable to determine Playwright runtime state: {payload}',
        remediation=['Run `npm ci` and `make install-web-test-runtime`, then retry.'],
    )


def run_validation(mode: str) -> int:
    env = os.environ.copy()
    normalized_mode = (mode or 'staging').strip().lower()
    checks: list[ValidationCheck] = []

    checks.extend(
        [
            run_command('local_repo_integrity', 'api_production_startup_validation', ['pytest', '-q', 'services/api/tests/test_production_startup_validation.py']),
            run_command('local_repo_integrity', 'api_billing_runtime_validation', ['pytest', '-q', 'services/api/tests/test_billing_runtime.py']),
            run_command('local_repo_integrity', 'api_auth_health_diagnostics', ['pytest', '-q', 'services/api/tests/test_auth_health_diagnostics.py']),
        ]
    )

    web_env = env.copy()
    web_env.setdefault('NEXT_PUBLIC_LIVE_MODE_ENABLED', 'true')
    web_env.setdefault('API_URL', env.get('STAGING_API_URL', 'https://api.staging.example.com'))

    checks.append(
        run_command(
            'frontend_build_reproducibility',
            'frontend_runtime_alignment',
            ['python', 'scripts/check_frontend_runtime_alignment.py'],
            remediation=[
                'Resolve package.json/package-lock.json drift, then rerun.',
                'Use `npm ci` for deterministic install before build validation.',
            ],
        )
    )
    checks.append(
        run_command(
            'frontend_build_reproducibility',
            'web_build',
            ['npm', 'run', 'build', '--workspace', 'apps/web'],
            env=web_env,
            remediation=['Run `npm ci` and re-run build with required Vercel-style env vars.'],
        )
    )

    runtime_check = check_playwright_runtime()
    checks.append(runtime_check)
    if runtime_check.passed:
        checks.append(
            run_command(
                'browser_e2e_runtime',
                'web_local_smoke',
                ['npx', 'playwright', 'test', 'apps/web/tests/feature4-smoke.spec.ts'],
                remediation=['Investigate failure in Playwright report; ensure local app endpoints are reachable.'],
            )
        )
    else:
        checks.append(
            ValidationCheck(
                category='browser_e2e_runtime',
                name='web_local_smoke',
                command=['npx', 'playwright', 'test', 'apps/web/tests/feature4-smoke.spec.ts'],
                status='skip',
                detail='Skipped because Playwright runtime is not ready.',
                remediation=runtime_check.remediation,
            )
        )

    checks.append(
        run_command(
            'api_runtime_readiness',
            'api_readiness_contract',
            ['pytest', '-q', 'services/api/tests/test_production_startup_validation.py', 'services/api/tests/test_auth_health_diagnostics.py'],
            remediation=['Resolve startup/readiness diagnostics failures before launch gate.'],
        )
    )

    checks.append(
        run_command(
            'live_provider_configuration',
            'provider_smoke',
            ['python', 'services/api/scripts/smoke_live_providers.py'],
            env={**env, 'VALIDATION_MODE': normalized_mode},
            remediation=['Configure real provider environment variables and verify staging API readiness URL.'],
        )
    )

    checks.append(
        run_command(
            'staging_evidence',
            'staging_evidence_flow',
            ['python', 'scripts/staging/run_evidence_flow.py'],
            remediation=['Set required STAGING_* environment variables, then rerun evidence flow.'],
        )
    )

    per_category = {category: [c for c in checks if c.category == category] for category in CATEGORIES}
    category_status = {
        category: ('pass' if all(c.status in {'pass', 'skip'} for c in items) and any(c.status == 'pass' for c in items) else 'fail')
        for category, items in per_category.items()
        if items
    }

    ok = all(c.status in {'pass', 'skip'} for c in checks) and all(status == 'pass' for status in category_status.values())
    payload = {
        'mode': normalized_mode,
        'ok': ok,
        'category_status': category_status,
        'checks': [asdict(check) for check in checks],
    }
    print(json.dumps(payload, indent=2))
    print('\nRelease Validation Summary')
    for category in CATEGORIES:
        status = category_status.get(category, 'fail')
        print(f"- {category}: {status.upper()}")
        for check in per_category.get(category, []):
            marker = {'pass': 'PASS', 'fail': 'FAIL', 'skip': 'SKIP'}[check.status]
            print(f"  [{marker}] {check.name}")
            if check.status == 'fail' and check.remediation:
                print(f"    remediation: {check.remediation[0]}")
    return 0 if ok else 1


def main() -> int:
    mode = os.getenv('VALIDATION_MODE', 'staging')
    return run_validation(mode=mode)


if __name__ == '__main__':
    raise SystemExit(main())
