#!/usr/bin/env python3
"""
Paid SaaS launch proof for billing-enabled production (Paddle, Stripe).

Validates that billing, email, and environment gates are configured
for a production paid SaaS launch. Writes artifacts/launch-proof/latest/summary.json
in the same schema as run_no_billing_launch_proof.py so downstream aggregators
(write_sell_now_proof.py) continue to work without changes.

Exit codes:
  0 — all required gates pass
  1 — one or more required gates fail (blockers printed before exit)

Environment variables checked (Paddle):
  BILLING_PROVIDER=paddle
  PADDLE_API_KEY, PADDLE_CLIENT_TOKEN, PADDLE_ENVIRONMENT
  PADDLE_PRICE_ID (or any PADDLE_PRICE_ID_*)
  PADDLE_WEBHOOK_SECRET
  EMAIL_PROVIDER=resend, RESEND_API_KEY (or EMAIL_RESEND_API_KEY), EMAIL_FROM, EMAIL_DOMAIN

No secret values are printed — only configured: true/false.
"""
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

# Providers that require billing credentials
_PAID_PROVIDERS = {'paddle', 'stripe'}
# Providers / values that indicate no-billing / demo mode
_NO_BILLING_PROVIDERS = {'', 'none', 'disabled', 'no_billing'}


def _add_api_path() -> None:
    """Ensure services/api is on sys.path so paid_launch_readiness is importable."""
    api_path = str(REPO_ROOT / 'services' / 'api')
    if api_path not in sys.path:
        sys.path.insert(0, api_path)


@dataclass
class GateResult:
    name: str
    passed: bool
    details: dict


def _gate(name: str, passed: bool, **details: object) -> GateResult:
    return GateResult(name=name, passed=passed, details=dict(details))


def check_billing_gate() -> GateResult:
    _add_api_path()
    from app.paid_launch_readiness import check_billing_readiness

    result = check_billing_readiness()
    billing_ready = result.get('billing_ready', False)
    webhook_ready = result.get('billing_webhook_ready', False)
    missing = result.get('billing_missing_env', [])

    print(f"  billing_provider: {os.getenv('BILLING_PROVIDER', '(unset)').lower()}")
    print(f"  billing_ready: {billing_ready}")
    print(f"  billing_webhook_ready: {webhook_ready}")
    if missing:
        print(f"  missing env vars: {missing}")
    else:
        print("  missing env vars: none")

    return _gate(
        'billing',
        billing_ready and webhook_ready,
        billing_ready=billing_ready,
        billing_webhook_ready=webhook_ready,
        billing_status=result.get('billing_status'),
        billing_reason=result.get('billing_reason'),
        billing_missing_env=missing,
        billing_webhook_missing_env=result.get('billing_missing_env', []),
    )


def check_email_gate() -> GateResult:
    _add_api_path()
    from app.paid_launch_readiness import check_email_readiness

    result = check_email_readiness()
    email_ready = result.get('email_ready', False)
    missing = result.get('email_missing_env', [])

    provider = (os.getenv('EMAIL_PROVIDER') or os.getenv('MAIL_PROVIDER') or '(unset)').lower()
    print(f"  email_provider: {provider}")
    print(f"  email_ready: {email_ready}")
    if missing:
        print(f"  missing env vars: {missing}")
    else:
        print("  missing env vars: none")

    return _gate(
        'email',
        email_ready,
        email_ready=email_ready,
        email_status=result.get('email_status'),
        email_reason=result.get('email_reason'),
        email_missing_env=missing,
    )


def check_billing_provider_gate() -> GateResult:
    provider = (os.getenv('BILLING_PROVIDER') or '').strip().lower()
    is_paid = provider in _PAID_PROVIDERS
    is_no_billing = provider in _NO_BILLING_PROVIDERS

    print(f"  BILLING_PROVIDER configured: {bool(provider)}")
    print(f"  provider: {provider!r}")
    print(f"  is_paid_provider: {is_paid}")

    if is_no_billing:
        note = (
            f"BILLING_PROVIDER={provider!r} is a no-billing value. "
            "Use run_no_billing_launch_proof.py for demo/no-billing mode."
        )
    elif not is_paid:
        note = f"BILLING_PROVIDER={provider!r} is not a supported paid provider (paddle, stripe)."
    else:
        note = f"BILLING_PROVIDER={provider!r} is a supported paid provider."

    print(f"  note: {note}")

    return _gate(
        'billing_provider',
        is_paid,
        billing_provider=provider,
        is_paid_provider=is_paid,
        note=note,
    )


def _read_live_evidence_readiness() -> dict:
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


def write_artifact(
    artifact_dir: Path,
    gates: list[GateResult],
    live_evidence: dict,
    managed_pilot: dict,
    niw: dict,
    billing_gate: GateResult,
    email_gate: GateResult,
    mode: str = 'local',
) -> None:
    provider = (os.getenv('BILLING_PROVIDER') or '').strip().lower()

    billing_ready = billing_gate.details.get('billing_ready', False)
    billing_webhook_ready = billing_gate.details.get('billing_webhook_ready', False)
    email_ready = email_gate.details.get('email_ready', False)
    all_gates_pass = all(g.passed for g in gates)

    readiness_categories = {
        'live_provider_evidence_ready': live_evidence.get('live_provider_evidence_ready', False),
        'managed_pilot_ready': managed_pilot.get('managed_pilot_ready', False),
        'niw_positioning_ready': niw.get('niw_positioning_ready', False),
        'broad_paid_saas_ready': all_gates_pass,
        'ci_required_gates_ready': all_gates_pass,
    }

    readiness_compat = {
        'billing_ready': billing_ready,
        'billing_webhook_ready': billing_webhook_ready,
        'email_ready': email_ready,
        'provider_ready': live_evidence.get('provider_ready', False),
        'live_evidence_ready': live_evidence.get('live_evidence_ready', False),
        'ci_required_gates_ready': all_gates_pass,
    }

    blockers: list[str] = []
    for g in gates:
        if not g.passed:
            if g.name == 'billing_provider':
                blockers.append(f"billing_provider not a paid provider: {g.details.get('billing_provider')!r}")
            elif g.name == 'billing':
                missing = g.details.get('billing_missing_env', [])
                blockers.append(f"billing not ready — missing: {missing}")
            elif g.name == 'email':
                missing = g.details.get('email_missing_env', [])
                blockers.append(f"email not ready — missing: {missing}")
            else:
                blockers.append(f"{g.name} gate failed")

    allowed_claims: list[str] = []
    if niw.get('niw_positioning_ready'):
        allowed_claims.append('NIW Strategic Infrastructure Guard positioning ready')
    if managed_pilot.get('managed_pilot_ready'):
        allowed_claims.append('controlled pilot / managed sale ready')
    if live_evidence.get('live_provider_evidence_ready'):
        allowed_claims.append('live provider evidence ready')
    if billing_ready:
        allowed_claims.append(f'paid billing configured ({provider})')
    if email_ready:
        allowed_claims.append('email provider configured')
    if all_gates_pass and mode in {'staging', 'production'}:
        allowed_claims.append('paid SaaS launch ready')

    prohibited_claims: list[str] = []
    if not all_gates_pass:
        prohibited_claims.append('Do NOT claim paid SaaS launch is fully ready while gates are failing')
    if not live_evidence.get('live_provider_evidence_ready'):
        prohibited_claims.append('Do NOT claim live EVM monitoring without proven live evidence')
    if mode in {'local', 'ci', 'fail_closed_local'}:
        prohibited_claims.append(
            'Do NOT use this local/CI proof as evidence of paid launch readiness — '
            'requires staging or production runtime'
        )

    _local_modes = {'local', 'ci', 'fail_closed_local'}
    _paid_modes = {'staging', 'production'}

    # In local/CI mode, paid launch readiness can never be proven.
    # Only staging/production mode with all gates passing may claim readiness.
    if mode in _local_modes:
        paid_launch_ready = False
        broad_paid_saas_ready = False
        safe_to_sell_broadly_today = False
        blockers.append(
            'local mode: paid launch readiness cannot be proven without staging/production runtime'
        )
    else:
        paid_launch_ready = all_gates_pass
        broad_paid_saas_ready = all_gates_pass
        safe_to_sell_broadly_today = all_gates_pass and mode in _paid_modes

    readiness_categories['broad_paid_saas_ready'] = broad_paid_saas_ready

    payload = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'launch_mode': 'paid_saas',
        'proof_mode': mode,
        'billing_provider': provider,
        'repository': str(REPO_ROOT),
        'readiness_categories': readiness_categories,
        'live_provider_evidence': live_evidence,
        'managed_pilot': managed_pilot,
        'niw_positioning': niw,
        'billing': billing_gate.details,
        'email': email_gate.details,
        'allowed_claims': allowed_claims,
        'prohibited_claims': prohibited_claims,
        'pilot_ready': managed_pilot.get('managed_pilot_ready', False),
        'paid_launch_ready': paid_launch_ready,
        'controlled_pilot_ready': managed_pilot.get('managed_pilot_ready', False),
        'broad_paid_saas_ready': broad_paid_saas_ready,
        'safe_to_sell_broadly_today': safe_to_sell_broadly_today,
        'readiness': readiness_compat,
        'blockers': blockers,
        'warnings': [],
        'gates': [asdict(g) for g in gates],
        'artifact_paths': {
            'ci_required_gates': 'artifacts/release-proof/latest/ci-required-gates.json',
            'release_summary': 'artifacts/release-proof/latest/summary.json',
            'live_evidence_proof': 'artifacts/live-evidence-proof/latest/summary.json',
            'sell_now_proof': 'artifacts/sell-now-proof/latest/summary.json',
        },
    }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / 'summary.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')

    latest_dir = ARTIFACT_ROOT / 'latest'
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / 'summary.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')

    _write_markdown(latest_dir / 'summary.md', payload)
    _write_markdown(artifact_dir / 'summary.md', payload)


def _write_markdown(path: Path, payload: dict) -> None:
    lines = [
        '# Paid SaaS launch proof',
        '',
        f"- Generated: {payload['generated_at']}",
        f"- Billing provider: {payload.get('billing_provider', 'unknown')}",
        f"- Launch mode: {payload.get('launch_mode', 'unknown')}",
        '',
        '## Readiness Gates',
        '',
        '| Gate | Status |',
        '|---|---|',
    ]
    for key, val in payload.get('readiness_categories', {}).items():
        label = key.replace('_', ' ')
        status_str = 'READY' if val else 'NOT READY'
        lines.append(f'| {label} | {status_str} |')

    lines.extend(['', '## Billing / Email', '', '| Field | Value |', '|---|---|'])
    r = payload.get('readiness', {})
    lines.append(f"| billing_ready | {'YES' if r.get('billing_ready') else 'NO'} |")
    lines.append(f"| billing_webhook_ready | {'YES' if r.get('billing_webhook_ready') else 'NO'} |")
    lines.append(f"| email_ready | {'YES' if r.get('email_ready') else 'NO'} |")

    if payload.get('blockers'):
        lines.extend(['', '## Blockers', ''])
        for b in payload['blockers']:
            lines.append(f'- {b}')

    if payload.get('allowed_claims'):
        lines.extend(['', '## Allowed Claims', ''])
        for c in payload['allowed_claims']:
            lines.append(f'- {c}')

    if payload.get('prohibited_claims'):
        lines.extend(['', '## Prohibited Claims', ''])
        for c in payload['prohibited_claims']:
            lines.append(f'- {c}')

    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Paid SaaS launch proof')
    parser.add_argument(
        '--mode',
        default='local',
        choices=['local', 'ci', 'fail_closed_local', 'staging', 'production'],
        help='Proof mode. local/ci/fail_closed_local enforce paid_launch_ready=false.',
    )
    args = parser.parse_args()
    mode = args.mode

    provider = (os.getenv('BILLING_PROVIDER') or '').strip().lower()

    print('=== Paid SaaS launch proof ===')
    print(f'BILLING_PROVIDER: {provider!r}')
    print(f'Proof mode: {mode!r}')

    if provider in _NO_BILLING_PROVIDERS:
        print(
            '\nBLOCKER: BILLING_PROVIDER is not set to a paid provider.\n'
            f'  Current value: {provider!r}\n'
            '  This script requires BILLING_PROVIDER=paddle (or stripe).\n'
            '  For demo/no-billing mode use run_no_billing_launch_proof.py instead.\n'
            '  Set BILLING_PROVIDER=paddle and configure PADDLE_* env vars.',
            file=sys.stderr,
        )
        return 1

    if provider not in _PAID_PROVIDERS:
        print(
            f'\nBLOCKER: BILLING_PROVIDER={provider!r} is not a supported paid provider.\n'
            '  Supported paid providers: paddle, stripe.\n'
            '  Set BILLING_PROVIDER=paddle and configure PADDLE_* env vars.',
            file=sys.stderr,
        )
        return 1

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    artifact_dir = ARTIFACT_ROOT / timestamp

    print('\n[1/4] Checking billing provider gate...')
    billing_provider_gate = check_billing_provider_gate()

    print('\n[2/4] Checking billing credentials gate...')
    billing_gate = check_billing_gate()

    print('\n[3/4] Checking email gate...')
    email_gate = check_email_gate()

    print('\n[4/4] Reading artifact readiness...')
    live_evidence = _read_live_evidence_readiness()
    managed_pilot = _read_managed_pilot_readiness()
    niw = _check_niw_positioning_readiness()
    print(f"  live_provider_evidence_ready: {live_evidence.get('live_provider_evidence_ready', False)}")
    print(f"  managed_pilot_ready: {managed_pilot.get('managed_pilot_ready', False)}")
    print(f"  niw_positioning_ready: {niw.get('niw_positioning_ready', False)}")

    gates = [billing_provider_gate, billing_gate, email_gate]

    write_artifact(artifact_dir, gates, live_evidence, managed_pilot, niw, billing_gate, email_gate, mode=mode)
    print(f'\nLaunch proof artifacts written to {artifact_dir.relative_to(REPO_ROOT)}')

    failed = [g for g in gates if not g.passed]
    if failed:
        print(f'\nBLOCKERS ({len(failed)} gate(s) failed):')
        for g in failed:
            if g.name == 'billing_provider':
                print(f'  - billing_provider: {g.details.get("note")}')
            elif g.name == 'billing':
                missing = g.details.get('billing_missing_env', [])
                print(f'  - billing: {g.details.get("billing_reason")} missing={missing}')
            elif g.name == 'email':
                missing = g.details.get('email_missing_env', [])
                print(f'  - email: {g.details.get("email_reason")} missing={missing}')
        print('\nFix: configure the listed env vars and re-run this proof.')
        return 1

    print('\nAll gates passed — paid SaaS launch proof COMPLETE.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
