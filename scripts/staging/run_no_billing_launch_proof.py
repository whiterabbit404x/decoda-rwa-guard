#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = REPO_ROOT / 'artifacts' / 'launch-proof'
REQUIRED_STAGING_ENV = (
    'STAGING_BASE_URL',
    'STAGING_API_URL',
    'STAGING_EVIDENCE_EMAIL',
    'STAGING_EVIDENCE_PASSWORD',
)


@dataclass
class ProofStep:
    name: str
    command: list[str]
    required: bool
    status: str
    returncode: int | None
    log_file: str
    note: str = ''


def run_step(
    *,
    name: str,
    command: list[str],
    artifact_dir: Path,
    required: bool = True,
    env: dict[str, str] | None = None,
) -> ProofStep:
    process = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    output = (process.stdout + '\n' + process.stderr).strip() + '\n'
    log_file = artifact_dir / f'{name}.log'
    log_file.write_text(output, encoding='utf-8')
    status = 'pass' if process.returncode == 0 else 'fail'
    return ProofStep(
        name=name,
        command=command,
        required=required,
        status=status,
        returncode=process.returncode,
        log_file=str(log_file.relative_to(REPO_ROOT)),
    )


def write_summary(artifact_dir: Path, steps: list[ProofStep]) -> None:
    ok = all(step.status == 'pass' for step in steps if step.required)
    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'repository': str(REPO_ROOT),
        'ok': ok,
        'steps': [asdict(step) for step in steps],
    }
    (artifact_dir / 'summary.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    summary_lines = [
        '# No-billing launch proof run',
        '',
        f"- Generated: {payload['generated_at']}",
        f"- Overall status: {'pass' if ok else 'fail'}",
        '',
        '## Steps',
    ]
    for step in steps:
        summary_lines.append(
            f"- `{step.name}`: {step.status.upper()} (required={str(step.required).lower()}) — `{step.log_file}`"
        )
        if step.note:
            summary_lines.append(f"  - note: {step.note}")
    (artifact_dir / 'summary.md').write_text('\n'.join(summary_lines) + '\n', encoding='utf-8')


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    artifact_dir = ARTIFACT_ROOT / timestamp
    artifact_dir.mkdir(parents=True, exist_ok=True)

    steps: list[ProofStep] = [
        run_step(name='01_npm_ci', command=['npm', 'ci'], artifact_dir=artifact_dir),
        run_step(name='02_build_web', command=['npm', 'run', 'build:web'], artifact_dir=artifact_dir),
        run_step(name='03_validate_no_billing_launch', command=['make', 'validate-no-billing-launch'], artifact_dir=artifact_dir),
    ]

    missing_staging = [name for name in REQUIRED_STAGING_ENV if not os.getenv(name, '').strip()]
    if missing_staging:
        steps.append(
            ProofStep(
                name='04_optional_staging_evidence',
                command=['python', 'scripts/staging/run_evidence_flow.py'],
                required=False,
                status='skip',
                returncode=None,
                log_file='n/a',
                note='Skipped because missing env vars: ' + ', '.join(missing_staging),
            )
        )
    else:
        evidence_env = os.environ.copy()
        evidence_env['STAGING_EVIDENCE_OUTPUT_DIR'] = str((artifact_dir / 'staging-evidence').relative_to(REPO_ROOT))
        steps.append(
            run_step(
                name='04_optional_staging_evidence',
                command=['python', 'scripts/staging/run_evidence_flow.py'],
                artifact_dir=artifact_dir,
                required=False,
                env=evidence_env,
            )
        )

    write_summary(artifact_dir, steps)
    print(f'Launch proof artifacts written to {artifact_dir.relative_to(REPO_ROOT)}')

    required_failed = [step for step in steps if step.required and step.status != 'pass']
    return 1 if required_failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
