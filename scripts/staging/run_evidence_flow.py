#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REQUIRED_ENV = [
    'STAGING_BASE_URL',
    'STAGING_EVIDENCE_EMAIL',
    'STAGING_EVIDENCE_PASSWORD',
    'STAGING_EMAIL_VERIFICATION_SOURCE',
    'STAGING_SLACK_WEBHOOK_URL',
]


def fail(msg: str) -> None:
    print(f'ERROR: {msg}', file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    missing = [key for key in REQUIRED_ENV if not os.getenv(key, '').strip()]
    if missing:
        fail('Missing required staging credentials/config: ' + ', '.join(missing) + '. Set these env vars and retry.')

    evidence_root = Path('evidence')
    for rel in ('screenshots', 'traces', 'api', 'logs', 'exports'):
        (evidence_root / rel).mkdir(parents=True, exist_ok=True)

    summary = {
        'status': 'framework_ready',
        'base_url': os.getenv('STAGING_BASE_URL'),
        'flow': ['signup', 'verify-email', 'signin', 'mfa', 'workspace', 'target', 'alert', 'export', 'integration-delivery'],
        'note': 'Run Playwright/API drivers against staging with provided credentials.',
    }
    (evidence_root / 'summary.md').write_text(
        '# Staging Evidence Run\n\n- Status: framework ready\n- Flow prepared and credential checks passed.\n',
        encoding='utf-8',
    )
    (evidence_root / 'api' / 'run.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('Staging evidence framework checks passed.')


if __name__ == '__main__':
    main()
