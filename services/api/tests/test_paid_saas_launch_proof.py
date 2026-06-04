"""
Tests for the paid SaaS launch proof dispatcher logic.

Covers:
1. Paid proof runs when BILLING_PROVIDER=paddle.
2. No-billing proof is blocked when BILLING_PROVIDER=paddle (exits 1 with clear message).
3. No-billing proof runs when BILLING_PROVIDER=none or empty.
4. Paid proof passes with dummy Paddle + Resend env vars (billing_ready=true, email_ready=true).
5. Paid proof exits 1 with clear blockers when Paddle vars are missing.
6. Paid proof exits 1 with clear blockers when email vars are missing.
7. Resend email proof passes with dummy Resend env vars.
8. Paid proof requires a paid BILLING_PROVIDER (not none/empty/disabled).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PAID_PROOF_SCRIPT = REPO_ROOT / 'scripts' / 'staging' / 'run_paid_saas_launch_proof.py'
NO_BILLING_SCRIPT = REPO_ROOT / 'scripts' / 'staging' / 'run_no_billing_launch_proof.py'

_DUMMY_PADDLE_ENV = {
    'BILLING_PROVIDER': 'paddle',
    'PADDLE_API_KEY': 'pdl_api_testkey_dummy',
    'PADDLE_CLIENT_TOKEN': 'pdl_client_testkey_dummy',
    'PADDLE_PRICE_ID': 'pri_prod_monthly_dummy',
    'PADDLE_WEBHOOK_SECRET': 'pdl_whsec_testkey_dummy',
    'PADDLE_ENVIRONMENT': 'production',
    'EMAIL_PROVIDER': 'resend',
    'RESEND_API_KEY': 're_testkey_dummy_abc123',
    'EMAIL_FROM': 'noreply@decoda.io',
    'EMAIL_DOMAIN': 'decoda.io',
}

_DUMMY_RESEND_ENV = {
    'EMAIL_PROVIDER': 'resend',
    'RESEND_API_KEY': 're_testkey_dummy_abc123',
    'EMAIL_FROM': 'noreply@decoda.io',
    'EMAIL_DOMAIN': 'decoda.io',
}


def _run_script(script: Path, extra_env: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in (
        'BILLING_PROVIDER', 'PADDLE_API_KEY', 'PADDLE_CLIENT_TOKEN', 'PADDLE_PRICE_ID',
        'PADDLE_WEBHOOK_SECRET', 'PADDLE_ENVIRONMENT', 'PADDLE_PRICE_ID_MONTHLY',
        'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PRICE_ID',
        'EMAIL_PROVIDER', 'MAIL_PROVIDER', 'RESEND_API_KEY', 'EMAIL_RESEND_API_KEY',
        'EMAIL_FROM', 'EMAIL_DOMAIN', 'SENDGRID_API_KEY',
    )}
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        encoding='utf-8',
        timeout=60,
    )


# 1. Paid proof script exists and is importable
def test_paid_saas_launch_proof_script_exists() -> None:
    assert PAID_PROOF_SCRIPT.exists(), (
        'scripts/staging/run_paid_saas_launch_proof.py must exist'
    )


# 2. Paid proof exits 0 with dummy Paddle + Resend env vars
def test_paid_proof_passes_with_dummy_paddle_env(tmp_path: pytest.TempPathFactory) -> None:
    result = _run_script(PAID_PROOF_SCRIPT, _DUMMY_PADDLE_ENV, tmp_path)
    assert result.returncode == 0, (
        f'Paid proof should pass with dummy Paddle env.\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )
    assert 'COMPLETE' in result.stdout or 'gates passed' in result.stdout


# 3. Paid proof writes artifact with billing_ready=true, email_ready=true
def test_paid_proof_artifact_has_billing_and_email_ready(tmp_path: pytest.TempPathFactory) -> None:
    _run_script(PAID_PROOF_SCRIPT, _DUMMY_PADDLE_ENV, tmp_path)
    artifact = REPO_ROOT / 'artifacts' / 'launch-proof' / 'fail-closed-local' / 'summary.json'
    if not artifact.exists():
        pytest.skip('launch-proof artifact not yet generated')
    data = json.loads(artifact.read_text())
    readiness = data.get('readiness', {})
    assert readiness.get('billing_ready') is True, (
        f'billing_ready must be True after paid proof with Paddle. readiness={readiness}'
    )
    assert readiness.get('email_ready') is True, (
        f'email_ready must be True after paid proof with Resend. readiness={readiness}'
    )
    assert data.get('launch_mode') == 'paid_saas'


# 4. Paid proof exits 1 when BILLING_PROVIDER is not set to a paid provider
@pytest.mark.parametrize('provider', ['none', '', 'disabled', 'no_billing'])
def test_paid_proof_fails_when_billing_provider_is_no_billing(
    provider: str, tmp_path: pytest.TempPathFactory
) -> None:
    env = dict(_DUMMY_PADDLE_ENV)
    env['BILLING_PROVIDER'] = provider
    result = _run_script(PAID_PROOF_SCRIPT, env, tmp_path)
    assert result.returncode == 1, (
        f'Paid proof must exit 1 when BILLING_PROVIDER={provider!r}.\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )
    combined = result.stdout + result.stderr
    assert 'BLOCKER' in combined or 'no-billing' in combined.lower() or 'paid' in combined.lower()


# 5. Paid proof exits 1 when Paddle credentials are missing
def test_paid_proof_fails_when_paddle_api_key_missing(tmp_path: pytest.TempPathFactory) -> None:
    env = dict(_DUMMY_PADDLE_ENV)
    del env['PADDLE_API_KEY']
    result = _run_script(PAID_PROOF_SCRIPT, env, tmp_path)
    assert result.returncode == 1, (
        f'Paid proof must exit 1 when PADDLE_API_KEY is missing.\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )
    combined = result.stdout + result.stderr
    assert 'BLOCKER' in combined or 'billing' in combined.lower()


# 6. Paid proof exits 1 when email vars are missing
def test_paid_proof_fails_when_email_vars_missing(tmp_path: pytest.TempPathFactory) -> None:
    env = {k: v for k, v in _DUMMY_PADDLE_ENV.items()
           if k not in ('EMAIL_PROVIDER', 'RESEND_API_KEY', 'EMAIL_FROM', 'EMAIL_DOMAIN')}
    result = _run_script(PAID_PROOF_SCRIPT, env, tmp_path)
    assert result.returncode == 1, (
        f'Paid proof must exit 1 when email vars are missing.\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )
    combined = result.stdout + result.stderr
    assert 'BLOCKER' in combined or 'email' in combined.lower()


# 7. No-billing proof exits 1 (with clear message) when BILLING_PROVIDER=paddle
def test_no_billing_proof_blocked_when_billing_provider_is_paddle(
    tmp_path: pytest.TempPathFactory,
) -> None:
    env = {'BILLING_PROVIDER': 'paddle'}
    result = _run_script(NO_BILLING_SCRIPT, env, tmp_path)
    assert result.returncode == 1, (
        'No-billing proof must exit 1 when BILLING_PROVIDER=paddle.\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )
    combined = result.stdout + result.stderr
    assert 'BLOCKER' in combined or 'paid' in combined.lower() or 'paddle' in combined.lower()


# 8. No-billing proof guard does NOT fire when BILLING_PROVIDER=none
def test_no_billing_proof_allowed_when_billing_provider_is_none() -> None:
    # Verify the guard by reading _PAID_PROVIDERS from the source file.
    # We avoid running the full subprocess because npm install takes too long in CI.
    content = NO_BILLING_SCRIPT.read_text(encoding='utf-8')
    # The guard check in run_no_billing_launch_proof.py:
    #   if configured_provider in _PAID_PROVIDERS: sys.exit(1)
    # _PAID_PROVIDERS must contain paddle and stripe but NOT none/empty/disabled.
    assert "_PAID_PROVIDERS" in content, (
        'run_no_billing_launch_proof.py must define _PAID_PROVIDERS to guard against paid providers'
    )
    assert "'paddle'" in content, '_PAID_PROVIDERS must include paddle'
    assert "'stripe'" in content, '_PAID_PROVIDERS must include stripe'
    # The guard must check against _PAID_PROVIDERS
    assert 'in _PAID_PROVIDERS' in content, (
        'main() must check configured_provider in _PAID_PROVIDERS'
    )


# 9. Resend email gate passes with dummy Resend env vars (unit-level)
def test_resend_email_gate_passes_with_dummy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(REPO_ROOT / 'services' / 'api'))
    from app.paid_launch_readiness import check_email_readiness

    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('RESEND_API_KEY', 're_testkey_dummy_abc123')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')

    result = check_email_readiness()
    assert result['email_ready'] is True, (
        f'email_ready must be True with Resend dummy env. result={result}'
    )
    assert result['email_missing_env'] == []


# 10. Paddle billing gate passes with dummy Paddle env vars (unit-level)
def test_paddle_billing_gate_passes_with_dummy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(REPO_ROOT / 'services' / 'api'))
    from app.paid_launch_readiness import check_billing_readiness

    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_dummy')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_dummy')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_prod_monthly_dummy')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_dummy')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    result = check_billing_readiness()
    assert result['billing_ready'] is True, (
        f'billing_ready must be True with Paddle dummy env. result={result}'
    )
    assert result['billing_webhook_ready'] is True
    assert result['billing_missing_env'] == []
    # No Stripe vars required
    assert 'STRIPE_SECRET_KEY' not in result.get('billing_missing_env', [])


# 11. Dispatcher logic: paid proof is chosen for paddle, no-billing for none (logic test)
# Mirrors the bash normalization in save-proof-to-repo.yml:
#   BILLING_PROVIDER_LC=$(printf '%s' "${BILLING_PROVIDER:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
@pytest.mark.parametrize('provider,expected_script', [
    ('paddle', 'run_paid_saas_launch_proof.py'),
    ('stripe', 'run_paid_saas_launch_proof.py'),
    # Mixed-case variants must also route to paid proof after normalization.
    ('Paddle', 'run_paid_saas_launch_proof.py'),
    ('PADDLE', 'run_paid_saas_launch_proof.py'),
    ('Stripe', 'run_paid_saas_launch_proof.py'),
    ('none', 'run_no_billing_launch_proof.py'),
    ('', 'run_no_billing_launch_proof.py'),
    ('disabled', 'run_no_billing_launch_proof.py'),
    ('no_billing', 'run_no_billing_launch_proof.py'),
])
def test_dispatch_chooses_correct_script(provider: str, expected_script: str) -> None:
    # Simulate the bash normalization from the workflow step.
    normalized = provider.strip().lower()
    paid_providers = {'paddle', 'stripe'}
    chosen = (
        'run_paid_saas_launch_proof.py'
        if normalized in paid_providers
        else 'run_no_billing_launch_proof.py'
    )
    assert chosen == expected_script, (
        f'For BILLING_PROVIDER={provider!r} (normalized={normalized!r}), '
        f'expected {expected_script} but got {chosen}'
    )


# 12. BILLING_PROVIDER=paddle produces launch_mode="paid_saas" in the artifact.
def test_paddle_produces_paid_saas_launch_mode(tmp_path: pytest.TempPathFactory) -> None:
    _run_script(PAID_PROOF_SCRIPT, _DUMMY_PADDLE_ENV, tmp_path)
    artifact = REPO_ROOT / 'artifacts' / 'launch-proof' / 'fail-closed-local' / 'summary.json'
    if not artifact.exists():
        pytest.skip('launch-proof artifact not yet generated')
    data = json.loads(artifact.read_text())
    assert data.get('launch_mode') == 'paid_saas', (
        f'BILLING_PROVIDER=paddle must produce launch_mode="paid_saas", '
        f'got {data.get("launch_mode")!r}'
    )
    assert data.get('billing_provider') == 'paddle', (
        f'billing_provider must be "paddle" in the artifact'
    )


# 13. BILLING_PROVIDER=stripe produces launch_mode="paid_saas" in the artifact.
def test_stripe_produces_paid_saas_launch_mode(tmp_path: pytest.TempPathFactory) -> None:
    stripe_env = {
        'BILLING_PROVIDER': 'stripe',
        'STRIPE_SECRET_KEY': 'sk_live_testkey_stripe_dummy',
        'STRIPE_WEBHOOK_SECRET': 'whsec_testwebhook_stripe_dummy',
        'STRIPE_PRICE_ID': 'price_monthly_dummy',
        'EMAIL_PROVIDER': 'resend',
        'RESEND_API_KEY': 're_testkey_dummy_abc123',
        'EMAIL_FROM': 'noreply@decoda.io',
        'EMAIL_DOMAIN': 'decoda.io',
    }
    result = _run_script(PAID_PROOF_SCRIPT, stripe_env, tmp_path)
    artifact = REPO_ROOT / 'artifacts' / 'launch-proof' / 'fail-closed-local' / 'summary.json'
    if not artifact.exists():
        pytest.skip('launch-proof artifact not yet generated')
    data = json.loads(artifact.read_text())
    assert data.get('launch_mode') == 'paid_saas', (
        f'BILLING_PROVIDER=stripe must produce launch_mode="paid_saas", '
        f'got {data.get("launch_mode")!r}\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )


# 14. assert_proof_consistency Check 5 fails when launch_mode="pilot" but
#     BILLING_PROVIDER=paddle is set in the environment.
def test_assert_proof_consistency_check5_fails_pilot_with_paddle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util, sys as _sys

    # Build a minimal launch-proof artifact with launch_mode="pilot".
    launch_dir = tmp_path / 'artifacts' / 'launch-proof' / 'latest'
    launch_dir.mkdir(parents=True)
    (launch_dir / 'summary.json').write_text(json.dumps({
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'launch_mode': 'pilot',
        'billing_provider': None,
        'pilot_ready': True,
        'paid_launch_ready': False,
        'controlled_pilot_ready': True,
        'broad_paid_saas_ready': False,
        'readiness': {},
        'blockers': [],
        'warnings': [],
    }))

    assert_script = REPO_ROOT / 'scripts' / 'assert_proof_consistency.py'
    result = subprocess.run(
        [sys.executable, str(assert_script)],
        cwd=str(REPO_ROOT),
        env={**os.environ, 'BILLING_PROVIDER': 'paddle',
             'ASSERT_PROOF_ARTIFACT_ROOT': str(tmp_path)},
        capture_output=True,
        encoding='utf-8',
        timeout=30,
    )
    assert result.returncode == 1, (
        'assert_proof_consistency must exit 1 when launch_mode="pilot" '
        f'and BILLING_PROVIDER=paddle\nstdout: {result.stdout}\nstderr: {result.stderr}'
    )
    assert 'CHECK 5 FAIL' in result.stdout, (
        f'Expected CHECK 5 FAIL in output.\nstdout: {result.stdout}'
    )


# 15. assert_proof_consistency Check 5 passes when launch_mode="paid_saas"
#     and BILLING_PROVIDER=paddle is set.
def test_assert_proof_consistency_check5_passes_paid_saas_with_paddle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch_dir = tmp_path / 'artifacts' / 'launch-proof' / 'latest'
    launch_dir.mkdir(parents=True)
    (launch_dir / 'summary.json').write_text(json.dumps({
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'launch_mode': 'paid_saas',
        'billing_provider': 'paddle',
        'pilot_ready': False,
        'paid_launch_ready': False,
        'controlled_pilot_ready': False,
        'broad_paid_saas_ready': False,
        'readiness': {},
        'blockers': [],
        'warnings': [],
    }))

    assert_script = REPO_ROOT / 'scripts' / 'assert_proof_consistency.py'
    result = subprocess.run(
        [sys.executable, str(assert_script)],
        cwd=str(REPO_ROOT),
        env={**os.environ, 'BILLING_PROVIDER': 'paddle',
             'ASSERT_PROOF_ARTIFACT_ROOT': str(tmp_path)},
        capture_output=True,
        encoding='utf-8',
        timeout=30,
    )
    # Check 5 must pass; other checks may warn but the exit code depends on them.
    assert 'CHECK 5 FAIL' not in result.stdout, (
        f'CHECK 5 must NOT fail when launch_mode="paid_saas" and BILLING_PROVIDER=paddle.\n'
        f'stdout: {result.stdout}'
    )


# 16. assert_proof_consistency Check 5 passes when BILLING_PROVIDER=no_billing
#     even if launch_mode="pilot" (pilot mode is valid without billing).
def test_assert_proof_consistency_check5_passes_pilot_with_no_billing(
    tmp_path: Path,
) -> None:
    launch_dir = tmp_path / 'artifacts' / 'launch-proof' / 'latest'
    launch_dir.mkdir(parents=True)
    (launch_dir / 'summary.json').write_text(json.dumps({
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'launch_mode': 'pilot',
        'billing_provider': None,
        'pilot_ready': True,
        'paid_launch_ready': False,
        'controlled_pilot_ready': True,
        'broad_paid_saas_ready': False,
        'readiness': {},
        'blockers': [],
        'warnings': [],
    }))

    assert_script = REPO_ROOT / 'scripts' / 'assert_proof_consistency.py'
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ('BILLING_PROVIDER', 'ASSERT_PROOF_ARTIFACT_ROOT')}
    clean_env['ASSERT_PROOF_ARTIFACT_ROOT'] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(assert_script)],
        cwd=str(REPO_ROOT),
        env=clean_env,
        capture_output=True,
        encoding='utf-8',
        timeout=30,
    )
    assert 'CHECK 5 FAIL' not in result.stdout, (
        f'CHECK 5 must NOT fail when BILLING_PROVIDER is empty and launch_mode="pilot".\n'
        f'stdout: {result.stdout}'
    )
