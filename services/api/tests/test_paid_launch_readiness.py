"""
Session 10 — Paid Launch Billing/Email/Provider Readiness Tests.

Tests that broad paid SaaS launch is blocked unless all required gates pass.
Pilot readiness is independent and may pass while paid launch remains blocked.
"""
from __future__ import annotations

import pytest

from services.api.app.paid_launch_readiness import (
    build_paid_launch_readiness,
    check_billing_readiness,
    check_email_readiness,
    check_provider_readiness,
)

_LAUNCH_ENV_VARS = [
    'BILLING_PROVIDER',
    'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PRICE_ID',
    'PADDLE_API_KEY', 'PADDLE_WEBHOOK_SECRET',
    'EMAIL_PROVIDER', 'EMAIL_FROM',
    'SENDGRID_API_KEY', 'RESEND_API_KEY', 'EMAIL_RESEND_API_KEY',
    'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD',
    'EVM_RPC_URL',
]


def _clear_launch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _LAUNCH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set all required env vars so paid launch passes. Tests remove specific vars to exercise fail-closed gates."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'stripe')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_live_testkey_abc')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_testwebhook_abc')
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_pro_monthly')
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')


# A. Paid launch blocked when billing provider is missing.
def test_paid_launch_blocked_when_billing_provider_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['paid_launch_status'] == 'blocked'
    assert out['billing_ready'] is False
    assert out['billing_status'] == 'missing'
    assert any('billing' in b for b in out['paid_launch_blockers'])


# A2. Paid launch blocked when BILLING_PROVIDER=none (explicit pilot mode).
def test_paid_launch_blocked_when_billing_provider_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'none')
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['billing_ready'] is False
    assert out['billing_status'] == 'missing'


# B. Paid launch blocked when Stripe secret key is missing.
def test_paid_launch_blocked_when_stripe_secret_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('STRIPE_SECRET_KEY', raising=False)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['billing_ready'] is False
    assert 'STRIPE_SECRET_KEY' in out['billing_missing_env']


# C. Paid launch blocked when Stripe webhook secret is missing.
def test_paid_launch_blocked_when_stripe_webhook_secret_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('STRIPE_WEBHOOK_SECRET', raising=False)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['billing_webhook_ready'] is False
    assert out['billing_webhook_status'] == 'missing'
    assert any('webhook' in b for b in out['paid_launch_blockers'])
    assert 'STRIPE_WEBHOOK_SECRET' in out['billing_missing_env']


# C2. billing_ready can be True while billing_webhook_ready is False.
def test_billing_ready_true_but_webhook_ready_false_when_only_webhook_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('STRIPE_WEBHOOK_SECRET', raising=False)

    out = build_paid_launch_readiness()

    assert out['billing_ready'] is True
    assert out['billing_webhook_ready'] is False
    assert out['paid_launch_ready'] is False


# D. Paid launch blocked when Stripe price ID is missing.
def test_paid_launch_blocked_when_stripe_price_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('STRIPE_PRICE_ID', raising=False)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['billing_ready'] is False
    assert 'STRIPE_PRICE_ID' in out['billing_missing_env']


# E. Paid launch blocked when email provider is missing.
def test_paid_launch_blocked_when_email_provider_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('EMAIL_PROVIDER', raising=False)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['email_ready'] is False
    assert out['email_status'] == 'missing'
    assert any('email' in b for b in out['paid_launch_blockers'])


# F. Paid launch blocked when sender/domain config is missing.
def test_paid_launch_blocked_when_sender_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('EMAIL_FROM', raising=False)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['email_ready'] is False
    assert 'EMAIL_FROM' in out['email_missing_env']


# G. Paid launch blocked when live provider configuration is missing.
def test_paid_launch_blocked_when_provider_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['provider_ready'] is False
    assert out['provider_status'] == 'missing'
    assert any('provider' in b for b in out['paid_launch_blockers'])


# H. Paid launch passes only when billing, webhook, email, and provider config are all present.
def test_paid_launch_passes_when_all_gates_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is True
    assert out['paid_launch_status'] == 'ready'
    assert out['paid_launch_blockers'] == []
    assert out['billing_ready'] is True
    assert out['billing_webhook_ready'] is True
    assert out['email_ready'] is True
    assert out['provider_ready'] is True


# I. Secret values are never returned in readiness API or proof output.
def test_secret_values_never_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_key = 'sk_live_veryuniquesecret_xyz777'
    webhook_secret = 'whsec_veryuniquehook_xyz777'
    sendgrid_key = 'SG.veryuniqueapikey_xyz777'

    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'stripe')
    monkeypatch.setenv('STRIPE_SECRET_KEY', secret_key)
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', webhook_secret)
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_pro_monthly')
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', sendgrid_key)
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    out = build_paid_launch_readiness()
    output_str = str(out)

    assert secret_key not in output_str, 'Stripe secret key must not appear in output'
    assert webhook_secret not in output_str, 'Stripe webhook secret must not appear in output'
    assert sendgrid_key not in output_str, 'SendGrid API key must not appear in output'


# J. Pilot readiness can still pass while paid launch is blocked.
def test_pilot_readiness_passes_while_paid_launch_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.app.production_readiness import build_production_readiness

    _clear_launch_env(monkeypatch)
    # No billing configured — paid launch must be blocked
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    paid_launch = build_paid_launch_readiness()
    assert paid_launch['paid_launch_ready'] is False
    assert paid_launch['billing_ready'] is False

    # Pilot readiness is independent — passes with paid_ui_disabled=True
    pilot = build_production_readiness(
        env_checks={
            'database_reachable': True,
            'auth_session_configured': True,
            'required_env_vars_present': True,
            'redis_required': False,
            'redis_configured': False,
            'billing_required': False,
            'billing_configured': False,
            'paid_ui_disabled': True,
            'email_required': True,
            'email_configured': True,
            'app_base_url_configured': True,
            'api_url_configured': True,
        },
        runtime={
            'last_heartbeat_at': '2026-01-01T00:00:00Z',
            'latest_poll_at': '2026-01-01T00:00:30Z',
            'last_telemetry_at': '2026-01-01T00:01:00Z',
            'evidence_source': 'live',
            'workspace_evaluated': True,
            'workspace_scoped': True,
            'protected_assets_count': 1,
            'reporting_systems_count': 1,
            'enabled_monitoring_configs_count': 1,
            'target_coverage_status': 'covered',
            'provider_health_status': 'healthy',
            'freshness_status': 'fresh',
            'confidence_status': 'high',
            'contradiction_flags': [],
        },
        workflow={
            'detections': 1, 'alerts': 1, 'incidents': 1, 'response_actions': 1,
            'linkage_status': 'pass', 'linkage_reason': 'ok',
        },
        integrations={
            'slack_integration_status': 'pass',
            'webhook_integration_status': 'pass',
            'delivery_logs_status': 'pass',
            'api_key_support_status': 'pass',
        },
        exports={
            'evidence_source': 'live',
            'export_capability_status': 'pass',
            'latest_export_job_status': 'pass',
            'audit_log_availability': 'pass',
            'proof_bundle_capability': 'pass',
        },
        security={'readiness_access_control': 'pass', 'admin_workspace_scope': True},
    )
    assert pilot['ready_for_pilot'] is True


# K. Simulator/placeholder evidence must not satisfy live provider readiness.
def test_placeholder_evm_rpc_does_not_satisfy_provider_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://replace-me.changeme.io/rpc')

    out = build_paid_launch_readiness()

    assert out['provider_ready'] is False
    assert out['provider_status'] == 'misconfigured'
    assert out['paid_launch_ready'] is False
    assert any('provider' in b for b in out['paid_launch_blockers'])


def test_placeholder_stripe_key_does_not_satisfy_billing_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test-key_placeholder_value')

    out = build_paid_launch_readiness()

    assert out['billing_ready'] is False
    assert out['paid_launch_ready'] is False


# L. Readiness proof output includes paid launch blockers.
def test_readiness_proof_output_includes_paid_launch_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['paid_launch_status'] == 'blocked'
    assert isinstance(out['paid_launch_blockers'], list)
    assert len(out['paid_launch_blockers']) >= 3
    assert 'billing_ready' in out
    assert 'billing_webhook_ready' in out
    assert 'email_ready' in out
    assert 'provider_ready' in out


# Extra: Paddle billing provider support.
def test_paid_launch_passes_with_paddle_billing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_abc')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_webhook_testkey_abc')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRO', 'pri_pro_monthly_abc')
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is True
    assert out['billing_ready'] is True
    assert out['billing_webhook_ready'] is True


def test_paddle_billing_blocked_when_webhook_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_abc')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRO', 'pri_pro_monthly_abc')
    # No PADDLE_WEBHOOK_SECRET

    out = build_paid_launch_readiness()

    assert out['billing_webhook_ready'] is False
    assert out['billing_webhook_status'] == 'missing'
    assert out['paid_launch_ready'] is False


# Extra: Resend email provider support.
def test_paid_launch_passes_with_resend_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'stripe')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_live_testkey_abc')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_testwebhook_abc')
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_pro_monthly')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('RESEND_API_KEY', 're_testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    out = build_paid_launch_readiness()

    assert out['email_ready'] is True
    assert out['paid_launch_ready'] is True


# Extra: All fields present in output.
def test_all_required_fields_present_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)

    out = build_paid_launch_readiness()

    required_fields = [
        'billing_ready', 'billing_status', 'billing_reason',
        'billing_required_env', 'billing_missing_env',
        'billing_webhook_ready', 'billing_webhook_status', 'billing_webhook_reason',
        'email_ready', 'email_status', 'email_reason',
        'email_required_env', 'email_missing_env',
        'provider_ready', 'provider_status', 'provider_reason',
        'provider_required_env', 'provider_missing_env',
        'paid_launch_ready', 'paid_launch_status', 'paid_launch_blockers',
    ]
    for field in required_fields:
        assert field in out, f'Missing required output field: {field}'


# Extra: Unknown/unsupported billing provider is misconfigured, not ready.
def test_unknown_billing_provider_is_misconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'unknown_provider_xyz')

    out = build_paid_launch_readiness()

    assert out['billing_ready'] is False
    assert out['billing_status'] == 'misconfigured'
    assert out['paid_launch_ready'] is False
