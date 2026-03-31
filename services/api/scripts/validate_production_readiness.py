#!/usr/bin/env python3
from __future__ import annotations

import json
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


def run_check(name: str, command: list[str]) -> Check:
    process = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    output = (process.stdout + '\n' + process.stderr).strip()
    return Check(name=name, command=command, passed=process.returncode == 0, output=output)


def main() -> int:
    checks = [
        run_check('auth_signup_verify_signin_mfa_flow', ['pytest', '-q', 'services/api/tests/test_pilot_auth_self_serve.py']),
        run_check('monitoring_target_alert_deterministic_flow', ['pytest', '-q', 'services/api/tests/test_monitoring_automation.py']),
        run_check('slack_webhook_delivery_routing', ['pytest', '-q', 'services/api/tests/test_slack_routing_foundations.py']),
        run_check('exports_and_operable_routes', ['pytest', '-q', 'services/api/tests/test_operable_saas_workflows.py']),
        run_check('runtime_health_readiness_guards', ['pytest', '-q', 'services/api/tests/test_auth_health_diagnostics.py', 'services/api/tests/test_monitoring_mode_defaults.py']),
        run_check('frontend_mfa_source_assertions', ['npx', 'playwright', 'test', 'apps/web/tests/mfa-flows.spec.ts']),
    ]

    payload = {
        'ok': all(check.passed for check in checks),
        'checks': [asdict(check) for check in checks],
    }
    print(json.dumps(payload, indent=2))

    for check in checks:
        marker = 'PASS' if check.passed else 'FAIL'
        print(f"[{marker}] {check.name}: {' '.join(check.command)}")

    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
