#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / (os.getenv('STAGING_EVIDENCE_OUTPUT_DIR', 'evidence'))

REQUIRED_ENV = [
    'STAGING_BASE_URL',
    'STAGING_API_URL',
    'STAGING_EVIDENCE_EMAIL',
    'STAGING_EVIDENCE_PASSWORD',
]


def fail(msg: str) -> None:
    print(f'ERROR: {msg}', file=sys.stderr)
    raise SystemExit(1)


def ensure_dirs() -> None:
    for rel in ('screenshots', 'traces', 'api', 'logs', 'exports'):
        (EVIDENCE_ROOT / rel).mkdir(parents=True, exist_ok=True)


def run_playwright() -> tuple[int, str]:
    env = os.environ.copy()
    env['RUN_REAL_STAGING_EVIDENCE'] = 'true'
    if env.get('STAGING_EVIDENCE_TRACE', '').lower() == 'true':
        env.setdefault('PWDEBUG', '0')
    command = ['npx', 'playwright', 'test', 'apps/web/tests/staging-evidence-flow.spec.ts']
    process = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, env=env)
    output = (process.stdout + '\n' + process.stderr).strip()
    return process.returncode, output


def write_summary(status: str, detail: str, returncode: int | None = None) -> None:
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'returncode': returncode,
        'required_env': REQUIRED_ENV,
        'trace_enabled': os.getenv('STAGING_EVIDENCE_TRACE', '').lower() == 'true',
        'detail': detail,
    }
    (EVIDENCE_ROOT / 'api' / 'run.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    (EVIDENCE_ROOT / 'summary.md').write_text(
        '\n'.join(
            [
                '# Staging Evidence Run',
                '',
                f"- Generated: {summary['generated_at']}",
                f"- Status: {status}",
                f"- Trace enabled: {summary['trace_enabled']}",
                f"- Return code: {returncode if returncode is not None else 'n/a'}",
                '',
                '## Operator notes',
                '- Artifacts are written under evidence/screenshots, evidence/traces, and evidence/api.',
                '- Use `STAGING_EVIDENCE_TRACE=true` for deeper debugging when a run fails.',
            ]
        )
        + '\n',
        encoding='utf-8',
    )


def main() -> None:
    missing = [key for key in REQUIRED_ENV if not os.getenv(key, '').strip()]
    ensure_dirs()
    if missing:
        detail = 'Missing required staging credentials/config: ' + ', '.join(missing)
        write_summary('missing_configuration', detail)
        fail(detail + '. Set these env vars and retry.')

    rc, output = run_playwright()
    (EVIDENCE_ROOT / 'logs' / 'staging-evidence-playwright.log').write_text(output + '\n', encoding='utf-8')
    status = 'passed' if rc == 0 else 'failed'
    write_summary(status, 'Playwright staging evidence flow executed.', returncode=rc)
    print(f'Staging evidence flow {status}. Artifacts written to {EVIDENCE_ROOT}')
    if rc != 0:
        raise SystemExit(rc)


if __name__ == '__main__':
    main()
