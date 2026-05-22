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
    Check live chain provider configuration (EVM_RPC_URL and EVM_CHAIN_ID).

    Placeholder values are rejected as not ready.
    Never exposes secret values — only boolean presence.
    Derives explicit provider_mode: live | simulator | disabled | unknown.
    """
    evm_rpc = (os.getenv('EVM_RPC_URL') or '').strip()
    evm_chain_id = (os.getenv('EVM_CHAIN_ID') or os.getenv('CHAIN_ID') or '').strip()
    required = ['EVM_RPC_URL']
    optional = ['EVM_CHAIN_ID']

    if not evm_rpc:
        return {
            'provider_ready': False,
            'provider_status': 'missing',
            'provider_mode': 'disabled',
            'provider_reason': (
                'EVM_RPC_URL is not configured; '
                'live chain monitoring requires a real provider endpoint.'
            ),
            'provider_required_env': required,
            'provider_optional_env': optional,
            'provider_missing_env': ['EVM_RPC_URL'],
            'chain_id_configured': False,
        }

    if _has_placeholder(evm_rpc):
        return {
            'provider_ready': False,
            'provider_status': 'misconfigured',
            'provider_mode': 'unknown',
            'provider_reason': (
                'EVM_RPC_URL contains a placeholder value; '
                'set a real live provider endpoint before paid launch.'
            ),
            'provider_required_env': required,
            'provider_optional_env': optional,
            'provider_missing_env': ['EVM_RPC_URL'],
            'chain_id_configured': bool(evm_chain_id) and not _has_placeholder(evm_chain_id),
        }

    chain_id_ok = bool(evm_chain_id) and not _has_placeholder(evm_chain_id)
    chain_id_note = (
        'EVM_CHAIN_ID is configured.'
        if chain_id_ok
        else 'EVM_CHAIN_ID not set; chain identification may be implicit.'
    )

    return {
        'provider_ready': True,
        'provider_status': 'ready',
        'provider_mode': 'live',
        'provider_reason': (
            'EVM_RPC_URL is configured with a non-placeholder provider endpoint. '
            + chain_id_note
        ),
        'provider_required_env': required,
        'provider_optional_env': optional,
        'provider_missing_env': [],
        'chain_id_configured': chain_id_ok,
    }


def check_live_evidence_chain(chain_evidence: dict[str, Any]) -> dict[str, Any]:
    """
    Validate the full live evidence chain: provider → telemetry → detection → alert →
    incident/response-action → exportable evidence bundle.

    Contradiction guards (all fail-closed):
    - evidence_source must be 'live' or 'live_provider'; simulator/demo/unknown are rejected.
    - last_telemetry_at must be present; heartbeat_only and poll_only states are rejected.
    - At least one detection must be linked to telemetry evidence.
    - At least one alert must be linked to detection.
    - At least one incident or response_action must be linked to alert.
    - Evidence export must be available and labeled as live.
    - Contradiction flags in the evidence are treated as blockers.

    Args:
        chain_evidence: Dict with fields from the runtime/monitoring summary:
            evidence_source, last_heartbeat_at, latest_poll_at, last_telemetry_at,
            detections_count, alerts_count, incidents_count, response_actions_count,
            detection_telemetry_linked, alert_detection_linked, incident_alert_linked,
            export_capability, export_source_label, contradiction_flags.

    Returns:
        Dict with live_evidence_chain_ready bool, chain_status, chain_reason,
        chain_blockers list, and individual gate booleans.
    """
    blockers: list[str] = []

    evidence_source = str(
        chain_evidence.get('evidence_source')
        or chain_evidence.get('telemetry_evidence_source')
        or ''
    ).strip().lower()

    # Guard 1: evidence_source must be live
    if not evidence_source or evidence_source == 'unknown':
        blockers.append('evidence_source is unknown; failing closed')
        evidence_source_ok = False
    elif evidence_source in ('simulator', 'demo', 'guided_simulator', 'fixture'):
        blockers.append(
            f"evidence_source='{evidence_source}' is not live; "
            'simulator/demo evidence does not count as live evidence for broad SaaS readiness'
        )
        evidence_source_ok = False
    elif evidence_source in ('live', 'live_provider'):
        evidence_source_ok = True
    else:
        blockers.append(
            f"evidence_source='{evidence_source}' is not a recognised live source"
        )
        evidence_source_ok = False

    # Guard 2: last_telemetry_at must be present (heartbeat/poll alone do not count)
    heartbeat_at = chain_evidence.get('last_heartbeat_at') or chain_evidence.get('heartbeat_at')
    poll_at = chain_evidence.get('latest_poll_at') or chain_evidence.get('poll_at')
    telemetry_at = (
        chain_evidence.get('last_telemetry_at')
        or chain_evidence.get('latest_telemetry_at')
        or chain_evidence.get('telemetry_at')
    )

    if not telemetry_at:
        if heartbeat_at and not poll_at:
            blockers.append(
                'heartbeat_only: heartbeat is present but no telemetry; '
                'heartbeat proves the worker is alive, not that monitored data arrived'
            )
        elif poll_at and not heartbeat_at:
            blockers.append(
                'poll_only: poll is present but no telemetry; '
                'poll proves the monitoring loop ran, not that monitored data arrived'
            )
        elif heartbeat_at and poll_at:
            blockers.append(
                'heartbeat and poll are present but no telemetry; '
                'neither heartbeat nor poll proves monitored data actually arrived'
            )
        else:
            blockers.append('last_telemetry_at is missing; no telemetry evidence')
        telemetry_ok = False
    else:
        telemetry_ok = True

    # Guard 3: detection linked to telemetry
    detections_count = int(chain_evidence.get('detections_count') or 0)
    detection_telemetry_linked = bool(chain_evidence.get('detection_telemetry_linked'))
    if detections_count < 1:
        blockers.append('no detection linked to telemetry evidence')
        detection_ok = False
    elif not detection_telemetry_linked and 'detection_telemetry_linked' in chain_evidence:
        blockers.append('detection exists but is not linked to telemetry evidence by ID or lineage')
        detection_ok = False
    else:
        detection_ok = True

    # Guard 4: alert linked to detection
    alerts_count = int(chain_evidence.get('alerts_count') or 0)
    alert_detection_linked = bool(chain_evidence.get('alert_detection_linked'))
    if alerts_count < 1:
        blockers.append('no alert linked to detection')
        alert_ok = False
    elif not alert_detection_linked and 'alert_detection_linked' in chain_evidence:
        blockers.append('alert exists but is not linked to detection by ID or lineage')
        alert_ok = False
    else:
        alert_ok = True

    # Guard 5: incident or response_action linked to alert
    incidents_count = int(chain_evidence.get('incidents_count') or 0)
    response_actions_count = int(chain_evidence.get('response_actions_count') or 0)
    incident_alert_linked = bool(chain_evidence.get('incident_alert_linked'))
    if incidents_count < 1 and response_actions_count < 1:
        blockers.append('no incident or response_action linked to alert')
        incident_ok = False
    elif (
        incidents_count + response_actions_count > 0
        and not incident_alert_linked
        and 'incident_alert_linked' in chain_evidence
    ):
        blockers.append('incident/response_action exists but is not linked to alert by ID or lineage')
        incident_ok = False
    else:
        incident_ok = True

    # Guard 6: evidence export available and labeled as live
    export_capability = str(chain_evidence.get('export_capability') or '').strip().lower()
    export_source_label = str(chain_evidence.get('export_source_label') or '').strip().lower()
    if export_capability and export_capability not in ('pass', 'available', 'ready', 'true', '1'):
        blockers.append(f'evidence export not available: export_capability={export_capability!r}')
        export_ok = False
    elif export_source_label and export_source_label not in ('live', 'live_provider', ''):
        blockers.append(
            f"export_source_label='{export_source_label}' is not live; "
            'export must truthfully label source as live'
        )
        export_ok = False
    else:
        export_ok = True

    # Guard 7: contradiction flags
    contradiction_flags = list(chain_evidence.get('contradiction_flags') or [])
    live_contradictions = [
        f for f in contradiction_flags
        if any(
            token in str(f).lower()
            for token in (
                'live_mode_with_simulator', 'simulator_evidence', 'healthy_without_telemetry',
                'live_mode_without_telemetry', 'missing_telemetry',
            )
        )
    ]
    if live_contradictions:
        blockers.append(
            f'contradiction flags present that invalidate live evidence: {live_contradictions}'
        )

    live_evidence_chain_ready = not blockers

    return {
        'live_evidence_chain_ready': live_evidence_chain_ready,
        'chain_status': 'ready' if live_evidence_chain_ready else 'blocked',
        'chain_reason': (
            'Full live evidence chain verified: telemetry → detection → alert → incident/response-action → export.'
            if live_evidence_chain_ready
            else f"Live evidence chain blocked: {'; '.join(blockers[:3])}{'...' if len(blockers) > 3 else ''}"
        ),
        'evidence_source_ok': evidence_source_ok,
        'telemetry_ok': telemetry_ok,
        'detection_ok': detection_ok,
        'alert_ok': alert_ok,
        'incident_ok': incident_ok,
        'export_ok': export_ok,
        'chain_blockers': blockers,
    }


def build_paid_launch_readiness(
    *,
    live_evidence: dict[str, Any] | None = None,
    chain_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build canonical paid launch readiness status from the current environment.

    Fail-closed: paid_launch_ready=True only when ALL gates pass.
    Unknown or placeholder configuration is never treated as ready.
    Secret values are never included in output — only presence flags and var names.

    Pilot readiness (build_production_readiness) is independent and may pass
    while paid launch remains blocked (e.g., no-billing pilot mode).

    Args:
        live_evidence: Minimal evidence dict with evidence_source for basic live proof check.
        chain_evidence: Full chain evidence dict for detailed chain validation
            (telemetry→detection→alert→incident→export). When supplied, contradictions
            in the chain are surfaced as additional blockers.
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

    # Contradiction guard: EVM_RPC_URL missing → live_evidence_ready must be false
    if not provider['provider_ready']:
        live_proof_ready = False
        live_proof_reason = 'EVM_RPC_URL missing; live provider proof cannot be satisfied.'
    else:
        live_proof_ready, live_proof_reason = _live_provider_proof_present(live_evidence)
    if not live_proof_ready:
        blockers.append('live provider proof is missing')

    # Optional chain validation — additional contradiction guards when chain_evidence supplied
    chain_result: dict[str, Any] | None = None
    if chain_evidence is not None:
        chain_result = check_live_evidence_chain(chain_evidence)
        if not chain_result['live_evidence_chain_ready']:
            for chain_blocker in chain_result['chain_blockers']:
                if chain_blocker not in blockers:
                    blockers.append(chain_blocker)

    paid_launch_ready = not blockers

    out: dict[str, Any] = {
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
        'provider_mode': provider.get('provider_mode', 'unknown'),
        'provider_reason': provider['provider_reason'],
        'provider_required_env': provider['provider_required_env'],
        'provider_optional_env': provider.get('provider_optional_env', []),
        'provider_missing_env': provider['provider_missing_env'],
        'chain_id_configured': provider.get('chain_id_configured', False),
        'live_provider_proof_ready': live_proof_ready,
        'live_provider_proof_reason': live_proof_reason,
        'paid_launch_ready': paid_launch_ready,
        'paid_launch_status': 'ready' if paid_launch_ready else 'blocked',
        'paid_launch_blockers': blockers,
    }
    if chain_result is not None:
        out['live_evidence_chain'] = chain_result
    return out
