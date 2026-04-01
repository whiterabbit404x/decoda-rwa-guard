#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.request


def check(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {'name': name, 'ok': ok, 'detail': detail}


def main() -> int:
    results: list[dict[str, object]] = []

    email_provider = os.getenv('EMAIL_PROVIDER', 'console').strip().lower()
    email_from = os.getenv('EMAIL_FROM', '').strip()
    if email_provider == 'resend' and email_from and os.getenv('EMAIL_RESEND_API_KEY', '').strip():
        results.append(check('email', True, f'EMAIL_PROVIDER={email_provider} and EMAIL_FROM configured'))
    else:
        results.append(check('email', False, 'Set EMAIL_PROVIDER=resend, EMAIL_FROM, and EMAIL_RESEND_API_KEY for live smoke'))

    provider = os.getenv('BILLING_PROVIDER', 'paddle').strip().lower() or 'paddle'
    if provider == 'paddle':
        paddle_ready = bool(os.getenv('PADDLE_API_KEY', '').strip() and os.getenv('PADDLE_WEBHOOK_SECRET', '').strip())
        results.append(check('billing_paddle', paddle_ready, 'Paddle API + webhook secrets are configured' if paddle_ready else 'Missing PADDLE_API_KEY or PADDLE_WEBHOOK_SECRET'))
    else:
        stripe_ready = bool(os.getenv('STRIPE_SECRET_KEY', '').strip() and os.getenv('STRIPE_WEBHOOK_SECRET', '').strip())
        results.append(check('billing_stripe', stripe_ready, 'Stripe keys present for webhook and checkout checks' if stripe_ready else 'Missing STRIPE_SECRET_KEY or STRIPE_WEBHOOK_SECRET'))

    redis_ready = bool(os.getenv('REDIS_URL', '').strip())
    results.append(check('redis', redis_ready, 'REDIS_URL configured for distributed throttling' if redis_ready else 'Missing REDIS_URL'))

    evm_rpc = os.getenv('EVM_RPC_URL', '').strip()
    if evm_rpc:
        results.append(check('live_chain_monitoring', True, 'EVM_RPC_URL configured; run monitoring worker cycle to verify ingestion path'))
    else:
        results.append(check('live_chain_monitoring', True, 'Skipped: EVM_RPC_URL not configured'))

    api_url = os.getenv('STAGING_API_URL', '').strip()
    if api_url:
        try:
            with urllib.request.urlopen(f"{api_url.rstrip('/')}/health/readiness", timeout=8) as response:
                payload = json.loads(response.read().decode('utf-8'))
            results.append(check('api_readiness_endpoint', bool(payload.get('status') in {'healthy', 'degraded'}), f"status={payload.get('status')}"))
        except Exception as exc:  # pragma: no cover - network path
            results.append(check('api_readiness_endpoint', False, f'Failed to call /health/readiness: {exc}'))

    ok = all(bool(item['ok']) for item in results)
    summary = {'ok': ok, 'checks': results}
    print(json.dumps(summary, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
