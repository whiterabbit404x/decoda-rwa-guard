from __future__ import annotations

import os
from typing import Any

_PLACEHOLDER_MARKERS = frozenset({
    'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_',
})


def _has_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _env_ok(name: str) -> bool:
    val = (os.getenv(name) or '').strip()
    return bool(val) and not _has_placeholder(val)


def _missing_from(names: list[str]) -> list[str]:
    return [n for n in names if not _env_ok(n)]


def check_billing_readiness() -> dict[str, Any]:
    """
    Check billing provider, API credentials, price configuration, and webhook secret.

    Returns separate billing_ready and billing_webhook_ready flags.
    Never exposes secret values — only boolean presence and missing env var names.
    Fail-closed: unknown or 'none' provider is not ready.
    """
    provider = (os.getenv('BILLING_PROVIDER') or '').strip().lower()

    if not provider or provider == 'none':
        return {
            'billing_ready': False,
            'billing_status': 'missing',
            'billing_reason': (
                "BILLING_PROVIDER is not configured or is set to 'none'; "
                "a live billing provider is required for paid launch."
            ),
            'billing_required_env': ['BILLING_PROVIDER'],
            'billing_missing_env': ['BILLING_PROVIDER'],
            'billing_webhook_ready': False,
            'billing_webhook_status': 'missing',
            'billing_webhook_reason': 'No billing provider configured; webhook check cannot proceed.',
        }

    if provider == 'stripe':
        billing_required = ['STRIPE_SECRET_KEY', 'STRIPE_PRICE_ID']
        webhook_required = ['STRIPE_WEBHOOK_SECRET']
        billing_missing = _missing_from(billing_required)
        webhook_missing = _missing_from(webhook_required)
        billing_ready = not billing_missing
        webhook_ready = not webhook_missing
        return {
            'billing_ready': billing_ready,
            'billing_status': 'ready' if billing_ready else 'missing',
            'billing_reason': (
                'Stripe billing configured with required credentials and price ID.'
                if billing_ready
                else f'Stripe billing missing required env vars: {billing_missing}'
            ),
            'billing_required_env': billing_required + webhook_required,
            'billing_missing_env': billing_missing + webhook_missing,
            'billing_webhook_ready': webhook_ready,
            'billing_webhook_status': 'ready' if webhook_ready else 'missing',
            'billing_webhook_reason': (
                'STRIPE_WEBHOOK_SECRET is configured.'
                if webhook_ready
                else 'STRIPE_WEBHOOK_SECRET is missing; webhook signature verification will fail.'
            ),
        }

    if provider == 'paddle':
        billing_required = ['PADDLE_API_KEY']
        webhook_required = ['PADDLE_WEBHOOK_SECRET']
        price_ids = [
            k for k, v in os.environ.items()
            if k.startswith('PADDLE_PRICE_ID_') and v.strip() and not _has_placeholder(v.strip())
        ]
        billing_missing = _missing_from(billing_required)
        if not price_ids:
            billing_missing.append('PADDLE_PRICE_ID_*')
        webhook_missing = _missing_from(webhook_required)
        billing_ready = not billing_missing
        webhook_ready = not webhook_missing
        return {
            'billing_ready': billing_ready,
            'billing_status': 'ready' if billing_ready else 'missing',
            'billing_reason': (
                'Paddle billing configured with required credentials and price IDs.'
                if billing_ready
                else f'Paddle billing missing required configuration: {billing_missing}'
            ),
            'billing_required_env': billing_required + ['PADDLE_PRICE_ID_*'] + webhook_required,
            'billing_missing_env': billing_missing + webhook_missing,
            'billing_webhook_ready': webhook_ready,
            'billing_webhook_status': 'ready' if webhook_ready else 'missing',
            'billing_webhook_reason': (
                'PADDLE_WEBHOOK_SECRET is configured.'
                if webhook_ready
                else 'PADDLE_WEBHOOK_SECRET is missing; webhook signature verification will fail.'
            ),
        }

    return {
        'billing_ready': False,
        'billing_status': 'misconfigured',
        'billing_reason': (
            f"Unsupported BILLING_PROVIDER='{provider}'. Supported providers: stripe, paddle."
        ),
        'billing_required_env': ['BILLING_PROVIDER'],
        'billing_missing_env': [],
        'billing_webhook_ready': False,
        'billing_webhook_status': 'unknown',
        'billing_webhook_reason': (
            f"Cannot determine webhook requirements for unknown provider '{provider}'."
        ),
    }


def _live_provider_proof_present(live_evidence: dict[str, Any] | None = None) -> tuple[bool, str]:
    # Accept explicit non-secret override or canonical live evidence signal.
    proof_flag = (os.getenv('LIVE_PROVIDER_PROOF_PRESENT') or '').strip().lower()
    if proof_flag in {'1', 'true', 'yes', 'on'}:
        return True, 'LIVE_PROVIDER_PROOF_PRESENT is set.'

    if isinstance(live_evidence, dict):
        source = str(live_evidence.get('evidence_source') or live_evidence.get('telemetry_evidence_source') or '').strip().lower()
        if source == 'live':
            return True, 'Canonical live evidence source is present.'
        if source:
            return False, f"Canonical evidence source is '{source}', not live."

    return False, 'No canonical live provider proof signal found.'


def check_email_readiness() -> dict[str, Any]:
    """
    Check email provider, API credentials, and sender address configuration.

    Never exposes secret values — only boolean presence and missing env var names.
    Fail-closed: missing or unrecognized provider is not ready.
    """
    provider = (os.getenv('EMAIL_PROVIDER') or '').strip().lower()

    if not provider:
        return {
            'email_ready': False,
            'email_status': 'missing',
            'email_reason': 'EMAIL_PROVIDER is not configured.',
            'email_required_env': ['EMAIL_PROVIDER', 'EMAIL_FROM', 'EMAIL_DOMAIN'],
            'email_missing_env': ['EMAIL_PROVIDER'],
        }

    if provider == 'sendgrid':
        required = ['SENDGRID_API_KEY', 'EMAIL_FROM', 'EMAIL_DOMAIN']
        missing = _missing_from(required)
        ready = not missing
        return {
            'email_ready': ready,
            'email_status': 'ready' if ready else 'missing',
            'email_reason': (
                'SendGrid email configured with API key and verified sender address.'
                if ready
                else f'SendGrid email missing required configuration: {missing}'
            ),
            'email_required_env': ['EMAIL_PROVIDER', 'SENDGRID_API_KEY', 'RESEND_API_KEY', 'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'EMAIL_FROM', 'EMAIL_DOMAIN'],
            'email_missing_env': missing,
        }

    if provider == 'resend':
        resend_key = (os.getenv('RESEND_API_KEY') or os.getenv('EMAIL_RESEND_API_KEY') or '').strip()
        api_key_ok = bool(resend_key) and not _has_placeholder(resend_key)
        missing: list[str] = []
        if not api_key_ok:
            missing.append('RESEND_API_KEY')
        if not _env_ok('EMAIL_FROM'):
            missing.append('EMAIL_FROM')
        if not _env_ok('EMAIL_DOMAIN'):
            missing.append('EMAIL_DOMAIN')
        ready = not missing
        return {
            'email_ready': ready,
            'email_status': 'ready' if ready else 'missing',
            'email_reason': (
                'Resend email configured with API key and verified sender address.'
                if ready
                else f'Resend email missing required configuration: {missing}'
            ),
            'email_required_env': ['EMAIL_PROVIDER', 'SENDGRID_API_KEY', 'RESEND_API_KEY', 'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'EMAIL_FROM', 'EMAIL_DOMAIN'],
            'email_missing_env': missing,
        }

    if provider == 'smtp':
        required = ['SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'EMAIL_FROM', 'EMAIL_DOMAIN']
        missing = _missing_from(required)
        ready = not missing
        return {
            'email_ready': ready,
            'email_status': 'ready' if ready else 'missing',
            'email_reason': (
                'SMTP email configured with host, credentials, and sender address.'
                if ready
                else f'SMTP email missing required configuration: {missing}'
            ),
            'email_required_env': ['EMAIL_PROVIDER', 'SENDGRID_API_KEY', 'RESEND_API_KEY', 'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'EMAIL_FROM', 'EMAIL_DOMAIN'],
            'email_missing_env': missing,
        }

    return {
        'email_ready': False,
        'email_status': 'misconfigured',
        'email_reason': (
            f"EMAIL_PROVIDER='{provider}' is not a recognized provider. "
            "Supported providers: sendgrid, resend, smtp."
        ),
        'email_required_env': ['EMAIL_PROVIDER', 'EMAIL_FROM', 'EMAIL_DOMAIN'],
        'email_missing_env': [],
    }


def check_provider_readiness() -> dict[str, Any]:
    """
    Check live chain provider configuration (EVM_RPC_URL).

    Placeholder values are rejected as not ready.
    Never exposes secret values — only boolean presence.
    """
    evm_rpc = (os.getenv('EVM_RPC_URL') or '').strip()
    required = ['EVM_RPC_URL']

    if not evm_rpc:
        return {
            'provider_ready': False,
            'provider_status': 'missing',
            'provider_reason': (
                'EVM_RPC_URL is not configured; '
                'live chain monitoring requires a real provider endpoint.'
            ),
            'provider_required_env': required,
            'provider_missing_env': ['EVM_RPC_URL'],
        }

    if _has_placeholder(evm_rpc):
        return {
            'provider_ready': False,
            'provider_status': 'misconfigured',
            'provider_reason': (
                'EVM_RPC_URL contains a placeholder value; '
                'set a real live provider endpoint before paid launch.'
            ),
            'provider_required_env': required,
            'provider_missing_env': ['EVM_RPC_URL'],
        }

    return {
        'provider_ready': True,
        'provider_status': 'ready',
        'provider_reason': 'EVM_RPC_URL is configured with a non-placeholder provider endpoint.',
        'provider_required_env': required,
        'provider_missing_env': [],
    }


def build_paid_launch_readiness(*, live_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Build canonical paid launch readiness status from the current environment.

    Fail-closed: paid_launch_ready=True only when ALL gates pass.
    Unknown or placeholder configuration is never treated as ready.
    Secret values are never included in output — only presence flags and var names.

    Pilot readiness (build_production_readiness) is independent and may pass
    while paid launch remains blocked (e.g., no-billing pilot mode).
    """
    billing = check_billing_readiness()
    email = check_email_readiness()
    provider = check_provider_readiness()

    blockers: list[str] = []

    if not billing['billing_ready']:
        status = billing['billing_status']
        if status == 'missing':
            blockers.append('billing provider is not configured')
        elif status == 'misconfigured':
            blockers.append('billing provider is misconfigured')
        else:
            blockers.append('billing provider configuration is incomplete')

    if not billing['billing_webhook_ready']:
        blockers.append('billing webhook secret is missing')

    if not email['email_ready']:
        status = email['email_status']
        if status == 'missing':
            blockers.append('email provider is not configured')
        elif status == 'misconfigured':
            blockers.append('email provider is misconfigured')
        else:
            blockers.append('email provider configuration is incomplete')

    if not provider['provider_ready']:
        blockers.append('live provider configuration is missing')

    live_proof_ready, live_proof_reason = _live_provider_proof_present(live_evidence)
    if not live_proof_ready:
        blockers.append('live provider proof is missing')

    paid_launch_ready = not blockers

    return {
        'billing_ready': billing['billing_ready'],
        'billing_status': billing['billing_status'],
        'billing_reason': billing['billing_reason'],
        'billing_required_env': billing['billing_required_env'],
        'billing_missing_env': billing['billing_missing_env'],
        'billing_webhook_ready': billing['billing_webhook_ready'],
        'billing_webhook_status': billing['billing_webhook_status'],
        'billing_webhook_reason': billing['billing_webhook_reason'],
        'email_ready': email['email_ready'],
        'email_status': email['email_status'],
        'email_reason': email['email_reason'],
        'email_required_env': email['email_required_env'],
        'email_missing_env': email['email_missing_env'],
        'provider_ready': provider['provider_ready'],
        'provider_status': provider['provider_status'],
        'provider_reason': provider['provider_reason'],
        'provider_required_env': provider['provider_required_env'],
        'provider_missing_env': provider['provider_missing_env'],
        'live_provider_proof_ready': live_proof_ready,
        'live_provider_proof_reason': live_proof_reason,
        'paid_launch_ready': paid_launch_ready,
        'paid_launch_status': 'ready' if paid_launch_ready else 'blocked',
        'paid_launch_blockers': blockers,
    }
