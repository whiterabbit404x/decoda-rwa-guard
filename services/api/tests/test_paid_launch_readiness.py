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
    check_live_evidence_chain,
    check_provider_readiness,
)

_LAUNCH_ENV_VARS = [
    'BILLING_PROVIDER',
    'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PRICE_ID',
    'PADDLE_API_KEY', 'PADDLE_CLIENT_TOKEN', 'PADDLE_PRICE_ID', 'PADDLE_WEBHOOK_SECRET', 'PADDLE_ENVIRONMENT',
    'EMAIL_PROVIDER', 'EMAIL_FROM', 'EMAIL_DOMAIN',
    'SENDGRID_API_KEY', 'RESEND_API_KEY', 'EMAIL_RESEND_API_KEY',
    'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD',
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL', 'STAGING_EVM_CHAIN_ID', 'LIVE_PROVIDER_PROOF_PRESENT',
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
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')


# A. Paid launch blocked when billing provider is missing.
def test_paid_launch_blocked_when_billing_provider_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

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
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

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
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

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
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

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
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_abc')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_pro_monthly_abc')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_webhook_testkey_abc')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_abc')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is True
    assert out['billing_ready'] is True
    assert out['billing_webhook_ready'] is True


def test_paddle_billing_blocked_when_webhook_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_abc')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_abc')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_pro_monthly_abc')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')
    # No PADDLE_WEBHOOK_SECRET

    out = build_paid_launch_readiness()

    assert out['billing_webhook_ready'] is False
    assert out['billing_webhook_status'] == 'missing'
    assert out['paid_launch_ready'] is False


def test_paddle_proof_accepts_all_required_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """BILLING_PROVIDER=paddle is accepted when all required Paddle vars are configured."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_prod_monthly_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')
    monkeypatch.setenv('EMAIL_PROVIDER', 'sendgrid')
    monkeypatch.setenv('SENDGRID_API_KEY', 'SG.testApiKey_xyz')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_xyz')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is True
    assert out['billing_ready'] is True
    assert out['billing_webhook_ready'] is True
    assert out['billing_status'] == 'ready'
    assert out['billing_missing_env'] == []
    # Stripe vars must NOT be required
    assert 'STRIPE_SECRET_KEY' not in out.get('billing_missing_env', [])
    assert 'STRIPE_WEBHOOK_SECRET' not in out.get('billing_missing_env', [])


def test_paddle_billing_blocked_when_client_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_abc')
    # No PADDLE_CLIENT_TOKEN
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_pro_monthly_abc')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_webhook_testkey_abc')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    out = check_billing_readiness()

    assert out['billing_ready'] is False
    assert 'PADDLE_CLIENT_TOKEN' in out['billing_missing_env']


def test_paddle_billing_blocked_when_environment_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_abc')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_abc')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_pro_monthly_abc')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_webhook_testkey_abc')
    # No PADDLE_ENVIRONMENT

    out = check_billing_readiness()

    assert out['billing_ready'] is False
    assert 'PADDLE_ENVIRONMENT' in out['billing_missing_env']


def test_paddle_billing_blocked_when_price_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_abc')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_abc')
    # No PADDLE_PRICE_ID or PADDLE_PRICE_ID_*
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_webhook_testkey_abc')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    out = check_billing_readiness()

    assert out['billing_ready'] is False
    assert 'PADDLE_PRICE_ID' in out['billing_missing_env']


def test_paddle_price_id_variant_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """PADDLE_PRICE_ID_PRO (variant form) is also accepted when plain PADDLE_PRICE_ID is absent."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_abc')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_abc')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRO', 'pri_pro_monthly_abc')  # variant form
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_webhook_testkey_abc')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    out = check_billing_readiness()

    assert out['billing_ready'] is True


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
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

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


def test_paid_launch_blocked_when_email_domain_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('EMAIL_DOMAIN', raising=False)

    out = build_paid_launch_readiness()

    assert out['email_ready'] is False
    assert 'EMAIL_DOMAIN' in out['email_missing_env']
    assert out['paid_launch_ready'] is False


def test_paid_launch_blocked_when_live_provider_proof_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('LIVE_PROVIDER_PROOF_PRESENT', raising=False)

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is False
    assert out['live_provider_proof_ready'] is False
    assert 'live provider proof is missing' in out['paid_launch_blockers']


def test_simulator_evidence_does_not_satisfy_live_provider_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv('LIVE_PROVIDER_PROOF_PRESENT', raising=False)

    out = build_paid_launch_readiness(live_evidence={'evidence_source': 'guided_simulator'})

    assert out['live_provider_proof_ready'] is False
    assert out['paid_launch_ready'] is False


# ---------------------------------------------------------------------------
# Evidence chain validation tests (Task E)
# ---------------------------------------------------------------------------

_FULL_CHAIN_EVIDENCE: dict = {
    'evidence_source': 'live',
    'last_heartbeat_at': '2026-01-01T00:00:00Z',
    'latest_poll_at': '2026-01-01T00:00:30Z',
    'last_telemetry_at': '2026-01-01T00:01:00Z',
    'detections_count': 1,
    'detection_telemetry_linked': True,
    'alerts_count': 1,
    'alert_detection_linked': True,
    'incidents_count': 1,
    'incident_alert_linked': True,
    'export_capability': 'pass',
    'export_source_label': 'live',
    'contradiction_flags': [],
}


def test_missing_evm_rpc_keeps_live_evidence_ready_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing EVM_RPC_URL must block live_provider_proof_ready regardless of live_evidence."""
    _base_env(monkeypatch)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.delenv('LIVE_PROVIDER_PROOF_PRESENT', raising=False)

    out = build_paid_launch_readiness(live_evidence={'evidence_source': 'live'})

    assert out['provider_ready'] is False
    assert out['live_provider_proof_ready'] is False
    assert out['paid_launch_ready'] is False
    assert any('provider' in b for b in out['paid_launch_blockers'])


def test_heartbeat_only_does_not_count_as_live_telemetry() -> None:
    """Heartbeat alone must not satisfy live telemetry requirement."""
    result = check_live_evidence_chain({
        'evidence_source': 'live',
        'last_heartbeat_at': '2026-01-01T00:00:00Z',
        # No last_telemetry_at
    })
    assert result['live_evidence_chain_ready'] is False
    assert result['telemetry_ok'] is False
    assert any('heartbeat' in b for b in result['chain_blockers'])


def test_poll_only_does_not_count_as_live_telemetry() -> None:
    """Poll loop alone must not satisfy live telemetry requirement."""
    result = check_live_evidence_chain({
        'evidence_source': 'live',
        'latest_poll_at': '2026-01-01T00:00:30Z',
        # No last_telemetry_at
    })
    assert result['live_evidence_chain_ready'] is False
    assert result['telemetry_ok'] is False
    assert any('poll' in b for b in result['chain_blockers'])


def test_heartbeat_and_poll_without_telemetry_does_not_count() -> None:
    """Both heartbeat and poll without telemetry must still be rejected."""
    result = check_live_evidence_chain({
        'evidence_source': 'live',
        'last_heartbeat_at': '2026-01-01T00:00:00Z',
        'latest_poll_at': '2026-01-01T00:00:30Z',
        # No last_telemetry_at
    })
    assert result['live_evidence_chain_ready'] is False
    assert result['telemetry_ok'] is False


def test_simulator_evidence_does_not_count_as_live_chain_evidence() -> None:
    """Simulator/demo evidence source must be rejected in chain validation."""
    for simulator_source in ('simulator', 'demo', 'guided_simulator', 'fixture'):
        result = check_live_evidence_chain({**_FULL_CHAIN_EVIDENCE, 'evidence_source': simulator_source})
        assert result['live_evidence_chain_ready'] is False, f'Expected failure for source={simulator_source!r}'
        assert result['evidence_source_ok'] is False
        assert any('simulator' in b or 'demo' in b or 'not live' in b for b in result['chain_blockers'])


def test_unknown_evidence_source_fails_closed() -> None:
    """Unknown evidence source must fail closed."""
    result = check_live_evidence_chain({**_FULL_CHAIN_EVIDENCE, 'evidence_source': 'unknown'})
    assert result['live_evidence_chain_ready'] is False
    assert result['evidence_source_ok'] is False


def test_full_live_chain_sets_live_evidence_chain_ready() -> None:
    """Full chain (live telemetry → detection → alert → incident → export) must succeed."""
    result = check_live_evidence_chain(_FULL_CHAIN_EVIDENCE)
    assert result['live_evidence_chain_ready'] is True
    assert result['evidence_source_ok'] is True
    assert result['telemetry_ok'] is True
    assert result['detection_ok'] is True
    assert result['alert_ok'] is True
    assert result['incident_ok'] is True
    assert result['export_ok'] is True
    assert result['chain_blockers'] == []


def test_full_chain_with_response_action_instead_of_incident() -> None:
    """Response action may substitute for incident in the chain."""
    evidence = {**_FULL_CHAIN_EVIDENCE, 'incidents_count': 0, 'response_actions_count': 1}
    result = check_live_evidence_chain(evidence)
    assert result['live_evidence_chain_ready'] is True
    assert result['incident_ok'] is True


def test_chain_blocked_when_no_detection() -> None:
    """Missing detection must block the chain."""
    result = check_live_evidence_chain({**_FULL_CHAIN_EVIDENCE, 'detections_count': 0})
    assert result['live_evidence_chain_ready'] is False
    assert result['detection_ok'] is False


def test_chain_blocked_when_no_alert() -> None:
    """Missing alert must block the chain."""
    result = check_live_evidence_chain({**_FULL_CHAIN_EVIDENCE, 'alerts_count': 0})
    assert result['live_evidence_chain_ready'] is False
    assert result['alert_ok'] is False


def test_chain_blocked_when_no_incident_or_response() -> None:
    """Missing both incident and response_action must block the chain."""
    result = check_live_evidence_chain({
        **_FULL_CHAIN_EVIDENCE,
        'incidents_count': 0,
        'response_actions_count': 0,
    })
    assert result['live_evidence_chain_ready'] is False
    assert result['incident_ok'] is False


def test_provider_mode_is_live_when_evm_rpc_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider_mode must be 'live' when EVM_RPC_URL is configured and non-placeholder."""
    _base_env(monkeypatch)
    out = check_provider_readiness()
    assert out['provider_mode'] == 'live'
    assert out['provider_ready'] is True


def test_provider_mode_is_disabled_when_evm_rpc_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider_mode must be 'disabled' when EVM_RPC_URL is absent."""
    _base_env(monkeypatch)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    out = check_provider_readiness()
    assert out['provider_mode'] == 'disabled'
    assert out['provider_ready'] is False


def test_chain_id_configured_flag_present_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """chain_id_configured flag must be present in provider readiness output."""
    _base_env(monkeypatch)
    out = check_provider_readiness()
    assert 'chain_id_configured' in out


def test_evm_chain_id_reported_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """chain_id_configured must be True when EVM_CHAIN_ID is set."""
    _base_env(monkeypatch)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    out = check_provider_readiness()
    assert out['chain_id_configured'] is True


def test_production_build_does_not_require_live_secrets() -> None:
    """
    Production build config must not require staging/live secrets at build time.
    Validates that the build script uses defaults (:-) for required env vars.
    """
    import re
    from pathlib import Path
    web_pkg = Path(__file__).resolve().parents[3] / 'apps' / 'web' / 'package.json'
    import json
    pkg = json.loads(web_pkg.read_text())
    build_script = pkg.get('scripts', {}).get('build', '')
    # Build script must supply defaults for env vars that might be missing at build time
    assert ':-' in build_script or 'NEXT_TELEMETRY_DISABLED' in build_script, (
        "Build script should use shell defaults (:-) so it doesn't require live secrets"
    )
    # Build script must not hard-require live secrets (no sk_live_, whsec_, SG. patterns)
    assert not re.search(r'sk_live_|whsec_|SG\.[A-Za-z0-9_-]{20,}', build_script), (
        'Build script must not embed live secret values'
    )


def test_dependency_audit_gate() -> None:
    """
    Dependency audit gate: postcss must be >=8.5.10 OR a formal risk acceptance
    document must exist at docs/SECURITY_DEPENDENCY_RISK_ACCEPTANCE.md.
    """
    import subprocess
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[3]
    risk_doc = repo_root / 'docs' / 'SECURITY_DEPENDENCY_RISK_ACCEPTANCE.md'

    # Try to find installed postcss version
    postcss_pkg = repo_root / 'node_modules' / 'postcss' / 'package.json'
    if postcss_pkg.exists():
        import json
        from packaging.version import Version
        postcss_data = json.loads(postcss_pkg.read_text())
        postcss_version = postcss_data.get('version', '0.0.0')
        try:
            postcss_ok = Version(postcss_version) >= Version('8.5.10')
        except Exception:
            postcss_ok = False
        if postcss_ok:
            return  # Audit gate passes

    # If postcss is not at >=8.5.10, risk acceptance doc must exist
    assert risk_doc.exists(), (
        'postcss <8.5.10 is installed and no risk acceptance doc exists. '
        'Either upgrade postcss to >=8.5.10 or create docs/SECURITY_DEPENDENCY_RISK_ACCEPTANCE.md'
    )


# MAIL_PROVIDER alias tests
def test_mail_provider_alias_accepted_for_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAIL_PROVIDER=resend is accepted as alias for EMAIL_PROVIDER=resend."""
    _clear_launch_env(monkeypatch)
    monkeypatch.delenv('EMAIL_PROVIDER', raising=False)
    monkeypatch.setenv('MAIL_PROVIDER', 'resend')
    monkeypatch.setenv('RESEND_API_KEY', 're_testApiKey_alias_xyz')
    monkeypatch.setenv('EMAIL_FROM', 'alerts@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')

    out = check_email_readiness()

    assert out['email_ready'] is True
    assert out['email_status'] == 'ready'
    assert out['email_missing_env'] == []


def test_mail_provider_alias_blocked_when_no_provider_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing both EMAIL_PROVIDER and MAIL_PROVIDER fails closed."""
    _clear_launch_env(monkeypatch)
    monkeypatch.delenv('EMAIL_PROVIDER', raising=False)
    monkeypatch.delenv('MAIL_PROVIDER', raising=False)

    out = check_email_readiness()

    assert out['email_ready'] is False
    assert out['email_status'] == 'missing'


def test_paid_launch_passes_with_paddle_billing_and_resend_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full Paddle + Resend combination satisfies paid launch readiness."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_combo_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_combo_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_prod_monthly_combo_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_combo_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('RESEND_API_KEY', 're_testApiKey_combo_xyz')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_combo_xyz')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

    out = build_paid_launch_readiness()

    assert out['paid_launch_ready'] is True
    assert out['billing_ready'] is True
    assert out['billing_webhook_ready'] is True
    assert out['email_ready'] is True
    assert out['billing_missing_env'] == []
    assert out['email_missing_env'] == []
    assert 'STRIPE_SECRET_KEY' not in out.get('billing_missing_env', [])


def test_paddle_price_id_production_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """PADDLE_PRICE_ID_PRODUCTION is accepted when plain PADDLE_PRICE_ID is absent."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_prod_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_prod_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRODUCTION', 'pri_production_monthly_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_prod_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    out = check_billing_readiness()

    assert out['billing_ready'] is True
    assert out['billing_missing_env'] == []
    assert 'STRIPE_SECRET_KEY' not in out.get('billing_missing_env', [])


def test_paddle_price_id_monthly_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """PADDLE_PRICE_ID_MONTHLY is accepted when plain PADDLE_PRICE_ID is absent."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_monthly_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_monthly_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID_MONTHLY', 'pri_monthly_plan_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_monthly_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    out = check_billing_readiness()

    assert out['billing_ready'] is True
    assert out['billing_missing_env'] == []


def test_paddle_price_id_yearly_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """PADDLE_PRICE_ID_YEARLY is accepted when plain PADDLE_PRICE_ID is absent."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_yearly_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_yearly_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID_YEARLY', 'pri_yearly_plan_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_yearly_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    out = check_billing_readiness()

    assert out['billing_ready'] is True
    assert out['billing_missing_env'] == []


def test_resend_readiness_passes_with_email_resend_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """EMAIL_RESEND_API_KEY is accepted as an alias for RESEND_API_KEY."""
    _clear_launch_env(monkeypatch)
    monkeypatch.delenv('RESEND_API_KEY', raising=False)
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_alias_testkey_xyz')
    monkeypatch.setenv('EMAIL_FROM', 'alerts@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')

    out = check_email_readiness()

    assert out['email_ready'] is True
    assert out['email_status'] == 'ready'
    assert out['email_missing_env'] == []


def test_stripe_vars_not_required_when_billing_provider_is_paddle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stripe env vars must not appear in billing_missing_env when BILLING_PROVIDER=paddle."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_nostripe_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_nostripe_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_nostripe_plan_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_nostripe_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    out = check_billing_readiness()

    assert out['billing_ready'] is True
    stripe_vars = ['STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PRICE_ID']
    for var in stripe_vars:
        assert var not in out['billing_missing_env'], (
            f'{var} must not be required when BILLING_PROVIDER=paddle'
        )


def test_mail_provider_alias_with_paddle_billing_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAIL_PROVIDER alias is accepted in full paid launch readiness check with Paddle billing."""
    _clear_launch_env(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_alias_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_alias_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_prod_monthly_alias_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_alias_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')
    monkeypatch.delenv('EMAIL_PROVIDER', raising=False)
    monkeypatch.setenv('MAIL_PROVIDER', 'resend')
    monkeypatch.setenv('RESEND_API_KEY', 're_testApiKey_alias_xyz')
    monkeypatch.setenv('EMAIL_FROM', 'noreply@decoda.io')
    monkeypatch.setenv('EMAIL_DOMAIN', 'decoda.io')
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_alias_xyz')
    monkeypatch.setenv('LIVE_PROVIDER_PROOF_PRESENT', 'true')

    out = build_paid_launch_readiness()

    assert out['email_ready'] is True
    assert out['paid_launch_ready'] is True
