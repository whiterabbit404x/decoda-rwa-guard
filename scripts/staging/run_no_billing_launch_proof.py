#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

IS_WINDOWS = platform.system() == 'Windows'

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = REPO_ROOT / 'artifacts' / 'launch-proof'
REQUIRED_STAGING_ENV = (
    'STAGING_BASE_URL',
    'STAGING_API_URL',
    'STAGING_EVIDENCE_EMAIL',
    'STAGING_EVIDENCE_PASSWORD',
)
NO_BILLING_PROVIDER = 'none'
# Per-step subprocess timeout in seconds. Steps are allowed to fail; the
# summary is always written even if individual steps time out or fail.
_STEP_TIMEOUT = 300


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
    timeout: int = _STEP_TIMEOUT,
) -> ProofStep:
    try:
        process = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            timeout=timeout,
        )
        output = (process.stdout + '\n' + process.stderr).strip() + '\n'
        status = 'pass' if process.returncode == 0 else 'fail'
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        output = f'step timed out after {timeout}s\n'
        status = 'fail'
        returncode = -1
    log_file = artifact_dir / f'{name}.log'
    log_file.write_text(output, encoding='utf-8')
    return ProofStep(
        name=name,
        command=command,
        required=required,
        status=status,
        returncode=returncode,
        log_file=str(log_file.relative_to(REPO_ROOT)),
    )


def _read_live_evidence_readiness() -> dict:
    """Read live-evidence-proof/latest/summary.json and extract readiness facts."""
    summary_path = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    if not summary_path.exists():
        return {
            'artifact_available': False,
            'live_provider_evidence_ready': False,
            'reason': 'artifacts/live-evidence-proof/latest/summary.json not found',
        }
    try:
        data = json.loads(summary_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            'artifact_available': False,
            'live_provider_evidence_ready': False,
            'reason': f'failed to read artifact: {exc}',
        }
    lpe = data.get('live_provider_evidence', {})
    ready = (
        lpe.get('provider_ready') is True
        and lpe.get('provider_mode') == 'live'
        and lpe.get('live_evidence_ready') is True
        and lpe.get('evidence_source') == 'live'
    )
    return {
        'artifact_available': True,
        'live_provider_evidence_ready': ready,
        'provider_ready': lpe.get('provider_ready', False),
        'provider_mode': lpe.get('provider_mode', 'unknown'),
        'live_evidence_ready': lpe.get('live_evidence_ready', False),
        'evidence_source': lpe.get('evidence_source', 'unknown'),
        'source': 'artifacts/live-evidence-proof/latest/summary.json',
    }


def _read_managed_pilot_readiness() -> dict:
    """Read sell-now-proof/latest/summary.json and extract managed pilot readiness."""
    summary_path = REPO_ROOT / 'artifacts' / 'sell-now-proof' / 'latest' / 'summary.json'
    if not summary_path.exists():
        return {
            'artifact_available': False,
            'managed_pilot_ready': False,
            'reason': 'artifacts/sell-now-proof/latest/summary.json not found',
        }
    try:
        data = json.loads(summary_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            'artifact_available': False,
            'managed_pilot_ready': False,
            'reason': f'failed to read artifact: {exc}',
        }
    ready = data.get('sell_now_managed_ready') is True
    return {
        'artifact_available': True,
        'managed_pilot_ready': ready,
        'sell_now_managed_ready': data.get('sell_now_managed_ready', False),
        'safe_claims': data.get('safe_claims', []),
        'source': 'artifacts/sell-now-proof/latest/summary.json',
    }


def _check_niw_positioning_readiness() -> dict:
    """Run validate_niw_positioning.py and return readiness result. Fail-closed."""
    try:
        result = subprocess.run(
            ['python', 'scripts/validate_niw_positioning.py'],
            cwd=REPO_ROOT,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=60,
        )
        output = (result.stdout + '\n' + result.stderr).strip()
        return {
            'niw_positioning_ready': result.returncode == 0,
            'validator_output': output,
            'validator': 'scripts/validate_niw_positioning.py',
        }
    except subprocess.TimeoutExpired:
        return {
            'niw_positioning_ready': False,
            'validator_output': 'NIW validator timed out',
            'validator': 'scripts/validate_niw_positioning.py',
        }


def _check_broad_paid_saas_gates() -> dict:
    """
    Check environment variables for broad paid SaaS readiness. Fail-closed.

    All gates must pass for broad_paid_saas_ready=true. Staging, billing,
    email, and auth secret must all be configured.
    """
    staging_api = os.getenv('STAGING_API_URL', '').strip()
    staging_app = os.getenv('STAGING_APP_URL', '').strip()
    staging_db = os.getenv('STAGING_DATABASE_URL', '').strip()
    staging_worker = os.getenv('STAGING_WORKER_ENABLED', '').strip().lower()
    billing = os.getenv('BILLING_PROVIDER', '').strip().lower()
    email = os.getenv('EMAIL_PROVIDER', '').strip().lower()
    auth_secret = os.getenv('STAGING_AUTH_TOKEN_SECRET', '').strip()

    gates: dict[str, bool] = {
        'staging_api_configured': bool(staging_api),
        'staging_app_configured': bool(staging_app),
        'staging_database_configured': bool(staging_db),
        'staging_worker_enabled': staging_worker == 'true',
        'billing_configured': billing not in ('', 'none'),
        'email_configured': email not in ('', 'none', 'unknown'),
        'auth_secret_configured': bool(auth_secret),
    }
    failed = [k for k, v in gates.items() if not v]
    return {
        **gates,
        'broad_paid_saas_ready': not failed,
        'blockers': [f'{k}=false' for k in failed],
    }


def _derive_allowed_claims(
    live_evidence: dict,
    managed_pilot: dict,
    niw: dict,
) -> list[str]:
    claims: list[str] = []
    if niw['niw_positioning_ready']:
        claims.append('NIW Strategic Infrastructure Guard positioning ready')
    if managed_pilot['managed_pilot_ready']:
        claims.append('controlled pilot / managed sale ready')
    if live_evidence['live_provider_evidence_ready']:
        claims.append('live provider evidence ready')
    claims.append('not broad paid SaaS ready')
    return claims


def _derive_prohibited_claims() -> list[str]:
    return [
        'broad paid SaaS production ready',
        'billing ready',
        'staging runtime fully ready',
        'staging database fully ready',
        'worker fully ready',
    ]


def write_summary(
    artifact_dir: Path,
    steps: list[ProofStep],
    live_evidence: dict,
    managed_pilot: dict,
    niw: dict,
    broad_saas: dict,
) -> None:
    required_steps_ok = all(step.status == 'pass' for step in steps if step.required)
    # ci_required_gates_ready is strict: requires both all required steps AND
    # all broad paid SaaS gates. It remains false if staging/billing/email are incomplete.
    ci_required_gates_ready = required_steps_ok and broad_saas['broad_paid_saas_ready']

    allowed_claims = _derive_allowed_claims(live_evidence, managed_pilot, niw)
    prohibited_claims = _derive_prohibited_claims()

    # readiness_categories: granular NIW-focused truth table
    readiness_categories = {
        'live_provider_evidence_ready': live_evidence['live_provider_evidence_ready'],
        'managed_pilot_ready': managed_pilot['managed_pilot_ready'],
        'niw_positioning_ready': niw['niw_positioning_ready'],
        'broad_paid_saas_ready': broad_saas['broad_paid_saas_ready'],
        'ci_required_gates_ready': ci_required_gates_ready,
    }

    # readiness: backward-compatible flat object read by validate_100_percent_readiness.py
    # provider_ready and live_evidence_ready are now derived from live-evidence-proof artifact
    readiness_compat = {
        'billing_ready': broad_saas['billing_configured'],
        'billing_webhook_ready': False,  # webhook requires separate billing validation
        'email_ready': broad_saas['email_configured'],
        'provider_ready': live_evidence.get('provider_ready', False),
        'live_evidence_ready': live_evidence.get('live_evidence_ready', False),
        'ci_required_gates_ready': ci_required_gates_ready,
    }

    # blockers: items preventing broad paid SaaS launch
    blockers: list[str] = []
    if not live_evidence['live_provider_evidence_ready']:
        blockers.append('live provider evidence not ready')
    if not broad_saas['billing_configured']:
        blockers.append('billing not ready')
    if not broad_saas['email_configured']:
        blockers.append('email not ready')
    if not broad_saas['staging_api_configured']:
        blockers.append('staging API not configured')
    if not broad_saas['staging_database_configured']:
        blockers.append('staging database not configured')
    if not broad_saas['staging_worker_enabled']:
        blockers.append('staging worker not enabled')
    if not ci_required_gates_ready:
        blockers.append('ci gates not ready')

    payload = {
        'schema_version': 2,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'repository': str(REPO_ROOT),
        # Granular readiness truth table
        'readiness_categories': readiness_categories,
        # Detailed source breakdowns
        'live_provider_evidence': live_evidence,
        'managed_pilot': managed_pilot,
        'niw_positioning': niw,
        'broad_paid_saas': broad_saas,
        # NIW evidence claims
        'allowed_claims': allowed_claims,
        'prohibited_claims': prohibited_claims,
        # Backward-compatible fields for validate_100_percent_readiness.py
        'launch_mode': 'pilot',
        'pilot_ready': False,
        'paid_launch_ready': False,
        'controlled_pilot_ready': managed_pilot['managed_pilot_ready'],
        'broad_paid_saas_ready': broad_saas['broad_paid_saas_ready'],
        'readiness': readiness_compat,
        'blockers': blockers,
        'warnings': [],
        'artifact_paths': {
            'ci_required_gates': 'artifacts/release-proof/latest/ci-required-gates.json',
            'release_summary': 'artifacts/release-proof/latest/summary.json',
            'live_evidence_proof': 'artifacts/live-evidence-proof/latest/summary.json',
            'sell_now_proof': 'artifacts/sell-now-proof/latest/summary.json',
        },
        # Step execution log
        'steps': [asdict(step) for step in steps],
    }

    (artifact_dir / 'summary.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    latest_dir = ARTIFACT_ROOT / 'latest'
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / 'summary.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')

    summary_lines = [
        '# No-billing launch proof run',
        '',
        f"- Generated: {payload['generated_at']}",
        '',
        '## Readiness Truth Table',
        '',
        '| Category | Status |',
        '|---|---|',
    ]
    for key, val in readiness_categories.items():
        label = key.replace('_', ' ')
        status_str = 'READY' if val else 'NOT READY'
        summary_lines.append(f'| {label} | {status_str} |')

    summary_lines.extend([
        '',
        '## Allowed Claims',
        '',
    ])
    for claim in allowed_claims:
        summary_lines.append(f'- {claim}')

    summary_lines.extend([
        '',
        '## Prohibited Claims',
        '',
    ])
    for claim in prohibited_claims:
        summary_lines.append(f'- {claim}')

    summary_lines.extend([
        '',
        '## Steps',
        '',
    ])
    for step in steps:
        summary_lines.append(
            f"- `{step.name}`: {step.status.upper()} (required={str(step.required).lower()}) — `{step.log_file}`"
        )
        if step.note:
            summary_lines.append(f"  - note: {step.note}")

    summary_lines.extend([
        '',
        '## Remediation hints',
        '- If `04_validate_no_billing_launch` fails, run `make validate-no-billing-launch` directly for per-check remediation.',
        '- Review runtime gate evidence at `runbook-evidence/runtime_status_pre_release_gate.json` before stakeholder demos/releases.',
        '- If browser runtime checks fail, run `make install-web-test-runtime` on a network-enabled runner.',
        '- Keep billing disabled for this launch tier by exporting `BILLING_PROVIDER=none`.',
    ])
    (artifact_dir / 'summary.md').write_text('\n'.join(summary_lines) + '\n', encoding='utf-8')
    (latest_dir / 'summary.md').write_text('\n'.join(summary_lines) + '\n', encoding='utf-8')


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    artifact_dir = ARTIFACT_ROOT / timestamp
    artifact_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env['BILLING_PROVIDER'] = env.get('BILLING_PROVIDER', NO_BILLING_PROVIDER)
    runbook_evidence_dir = artifact_dir / 'runbook-evidence'
    runtime_gate_env = env.copy()
    runtime_gate_env['RUNTIME_STATUS_GATE_EVIDENCE_PATH'] = str(
        runbook_evidence_dir / 'runtime_status_pre_release_gate.json'
    )

    npm_cmd = 'npm.cmd' if IS_WINDOWS else 'npm'

    if IS_WINDOWS:
        no_billing_env = env.copy()
        no_billing_env['BILLING_PROVIDER'] = 'none'
        no_billing_env['VALIDATION_MODE'] = 'no_billing_pilot'
        validate_no_billing_cmd = ['python', 'services/api/scripts/validate_staging.py']
        validate_production_cmd = ['python', 'services/api/scripts/validate_production_readiness.py']
        validate_no_billing_env = no_billing_env
    else:
        validate_no_billing_cmd = ['make', 'validate-no-billing-launch']
        validate_production_cmd = ['make', 'validate-production']
        validate_no_billing_env = env

    steps: list[ProofStep] = [
        run_step(
            name='00_assert_no_billing_mode',
            command=[
                'python',
                '-c',
                (
                    "import os,sys;"
                    "provider=os.getenv('BILLING_PROVIDER','').strip().lower();"
                    "print(f'BILLING_PROVIDER={provider or \"(unset)\"}');"
                    "sys.exit(0 if provider=='none' else 1)"
                ),
            ],
            artifact_dir=artifact_dir,
            env=env,
        ),
        run_step(name='01_npm_ci', command=[npm_cmd, 'ci'], artifact_dir=artifact_dir),
        run_step(name='02_build_web', command=[npm_cmd, 'run', 'build:web'], artifact_dir=artifact_dir),
        run_step(
            name='03_runtime_status_pre_release_gate',
            command=['python', 'services/api/scripts/check_monitoring_runtime_live_gate.py'],
            artifact_dir=artifact_dir,
            env=runtime_gate_env,
        ),
        run_step(
            name='04_validate_no_billing_launch',
            command=validate_no_billing_cmd,
            artifact_dir=artifact_dir,
            env=validate_no_billing_env,
        ),
        run_step(
            name='05_validate_production',
            command=validate_production_cmd,
            artifact_dir=artifact_dir,
            env=env,
            required=False,
        ),
    ]

    missing_staging = [name for name in REQUIRED_STAGING_ENV if not os.getenv(name, '').strip()]
    if missing_staging:
        steps.append(
            ProofStep(
                name='06_optional_staging_evidence',
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
        evidence_env['STAGING_EVIDENCE_OUTPUT_DIR'] = str(
            (artifact_dir / 'staging-evidence').relative_to(REPO_ROOT)
        )
        steps.append(
            run_step(
                name='06_optional_staging_evidence',
                command=['python', 'scripts/staging/run_evidence_flow.py'],
                artifact_dir=artifact_dir,
                required=False,
                env=evidence_env,
            )
        )

    # Compute granular readiness categories from artifacts and environment.
    # These are computed independently of build step pass/fail so that
    # live evidence and NIW readiness are always reported truthfully.
    live_evidence = _read_live_evidence_readiness()
    managed_pilot = _read_managed_pilot_readiness()
    niw = _check_niw_positioning_readiness()
    broad_saas = _check_broad_paid_saas_gates()

    write_summary(artifact_dir, steps, live_evidence, managed_pilot, niw, broad_saas)
    print(f'Launch proof artifacts written to {artifact_dir.relative_to(REPO_ROOT)}')

    required_failed = [step for step in steps if step.required and step.status != 'pass']
    return 1 if required_failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
