#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PLACEHOLDER_MARKERS = {'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_'}


def classify(name: str, status: str, detail: str, remediation: list[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'name': name,
        'status': status,
        'detail': detail,
        'remediation': remediation or [],
        'metadata': metadata or {},
    }


def has_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def safe_preview(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 6:
        return '*' * len(value)
    return f"{value[:3]}...{value[-3:]}"


def check_email_provider() -> dict[str, Any]:
    provider = (os.getenv('EMAIL_PROVIDER', 'console') or 'console').strip().lower()
    sender = os.getenv('EMAIL_FROM', '').strip()
    resend_key = os.getenv('EMAIL_RESEND_API_KEY', '').strip()

    if provider != 'resend':
        return classify(
            'email_provider',
            'not_configured',
            f'EMAIL_PROVIDER={provider}; live verification email evidence requires resend.',
            ['Set EMAIL_PROVIDER=resend, EMAIL_FROM, and EMAIL_RESEND_API_KEY for launch evidence.'],
        )
    if not sender or has_placeholder(sender):
        return classify('email_provider', 'fail', 'EMAIL_FROM is missing or placeholder-like.', ['Set EMAIL_FROM to a verified sender domain.'])
    if not resend_key:
        return classify('email_provider', 'fail', 'EMAIL_RESEND_API_KEY is missing.', ['Set EMAIL_RESEND_API_KEY in Railway env.'])
    if has_placeholder(resend_key):
        return classify('email_provider', 'fail', 'EMAIL_RESEND_API_KEY appears placeholder-like.', ['Use real resend API key (keep secret).'])

    return classify('email_provider', 'verified', f'EMAIL_PROVIDER=resend and sender {sender} configured.', metadata={'api_key_preview': safe_preview(resend_key)})


def check_billing_provider() -> dict[str, Any]:
    provider = (os.getenv('BILLING_PROVIDER', 'paddle') or 'paddle').strip().lower()
    if provider == 'none':
        return classify('billing_provider', 'not_configured', 'BILLING_PROVIDER=none; acceptable for pilot, not broad sale.', ['Use paddle or stripe before broad launch.'])
    if provider == 'paddle':
        key = os.getenv('PADDLE_API_KEY', '').strip()
        webhook = os.getenv('PADDLE_WEBHOOK_SECRET', '').strip()
        price_ids = [k for k, v in os.environ.items() if k.startswith('PADDLE_PRICE_ID_') and v.strip()]
        if not key or not webhook:
            return classify('billing_provider', 'fail', 'Paddle selected but key/webhook secret missing.', ['Set PADDLE_API_KEY and PADDLE_WEBHOOK_SECRET.'])
        if has_placeholder(key) or has_placeholder(webhook):
            return classify('billing_provider', 'fail', 'Paddle credentials appear placeholder-like.', ['Replace with real Paddle secrets.'])
        if not price_ids:
            return classify('billing_provider', 'configured_unverified', 'Paddle credentials present but no PADDLE_PRICE_ID_* configured.', ['Configure at least one plan price id.'])
        return classify('billing_provider', 'verified', 'Paddle credentials and price IDs configured.', metadata={'provider': provider, 'price_id_count': len(price_ids)})

    if provider == 'stripe':
        key = os.getenv('STRIPE_SECRET_KEY', '').strip()
        webhook = os.getenv('STRIPE_WEBHOOK_SECRET', '').strip()
        if not key or not webhook:
            return classify('billing_provider', 'fail', 'Stripe selected but secret key/webhook secret missing.', ['Set STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET.'])
        if has_placeholder(key) or has_placeholder(webhook):
            return classify('billing_provider', 'fail', 'Stripe credentials appear placeholder-like.', ['Replace with real Stripe secrets.'])
        return classify('billing_provider', 'verified', 'Stripe credentials configured.', metadata={'provider': provider})

    return classify('billing_provider', 'fail', f'Unsupported BILLING_PROVIDER value: {provider}', ['Set BILLING_PROVIDER to paddle or stripe.'])


def check_redis_requirement() -> dict[str, Any]:
    app_env = (os.getenv('APP_ENV', 'development') or 'development').strip().lower()
    redis = os.getenv('REDIS_URL', '').strip()
    if app_env == 'production' and not redis:
        return classify('redis_requirement', 'fail', 'APP_ENV=production requires REDIS_URL for distributed auth controls.', ['Set REDIS_URL before production launch.'])
    if not redis:
        return classify('redis_requirement', 'not_configured', 'REDIS_URL is not set (allowed only outside production).', ['Set REDIS_URL in staging/production to mirror launch topology.'])
    parsed = urllib.parse.urlparse(redis)
    if parsed.scheme not in {'redis', 'rediss'} or not parsed.hostname:
        return classify('redis_requirement', 'fail', 'REDIS_URL format is invalid.', ['Use redis:// or rediss:// with host.'])
    return classify('redis_requirement', 'verified', f'REDIS_URL configured with scheme {parsed.scheme}.', metadata={'host': parsed.hostname, 'port': parsed.port or 6379})


def check_api_readiness() -> dict[str, Any]:
    api_url = os.getenv('STAGING_API_URL', '').strip().rstrip('/')
    if not api_url:
        return classify('staging_api_readiness', 'not_configured', 'STAGING_API_URL is not set.', ['Set STAGING_API_URL to run readiness probe.'])
    target = f'{api_url}/health/readiness'
    try:
        with urllib.request.urlopen(target, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8'))
        status = str(payload.get('status', '')).lower()
        if status in {'healthy', 'degraded'}:
            return classify('staging_api_readiness', 'verified', f'/health/readiness returned status={status}.', metadata={'status': status})
        return classify('staging_api_readiness', 'fail', f'/health/readiness returned unexpected status={status}.', ['Review Railway env and readiness diagnostics.'], {'status': status})
    except urllib.error.HTTPError as exc:
        return classify('staging_api_readiness', 'fail', f'/health/readiness HTTP {exc.code}.', ['Check deployment health and auth/network policy.'])
    except Exception as exc:  # pragma: no cover
        return classify('staging_api_readiness', 'fail', f'Unable to call /health/readiness: {exc}', ['Confirm STAGING_API_URL and network access.'])


def check_chain_monitoring() -> dict[str, Any]:
    rpc = os.getenv('EVM_RPC_URL', '').strip()
    if not rpc:
        return classify('live_chain_monitoring', 'not_configured', 'EVM_RPC_URL not set; chain monitoring checks skipped.', ['Set EVM_RPC_URL for live chain monitoring evidence.'])
    parsed = urllib.parse.urlparse(rpc)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return classify('live_chain_monitoring', 'fail', 'EVM_RPC_URL must be a valid http(s) URL.', ['Set EVM_RPC_URL to a reachable provider endpoint.'])
    try:
        socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    except socket.gaierror:
        return classify('live_chain_monitoring', 'fail', 'EVM_RPC_URL hostname did not resolve.', ['Fix DNS/network for EVM RPC host.'])

    try:
        body = json.dumps({'jsonrpc': '2.0', 'method': 'eth_chainId', 'params': [], 'id': 1}).encode('utf-8')
        req = urllib.request.Request(rpc, method='POST', data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8'))
        chain_id = payload.get('result')
        if isinstance(chain_id, str) and chain_id.startswith('0x'):
            return classify('live_chain_monitoring', 'verified', f'EVM RPC responded to eth_chainId ({chain_id}).', metadata={'chain_id': chain_id})
        return classify('live_chain_monitoring', 'configured_unverified', f'RPC reachable but eth_chainId result unexpected: {payload}', ['Verify endpoint compatibility and auth requirements.'])
    except Exception as exc:  # pragma: no cover
        return classify('live_chain_monitoring', 'configured_unverified', f'EVM RPC configured but probe failed: {exc}', ['Confirm provider allows eth_chainId from this environment.'])


def main() -> int:
    checks = [
        check_email_provider(),
        check_billing_provider(),
        check_redis_requirement(),
        check_api_readiness(),
        check_chain_monitoring(),
    ]
    statuses = [check['status'] for check in checks]
    ok = all(status in {'verified', 'configured_unverified'} for status in statuses)
    summary = {
        'ok': ok,
        'checks': checks,
        'status_counts': {status: statuses.count(status) for status in sorted(set(statuses))},
    }
    print(json.dumps(summary, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
