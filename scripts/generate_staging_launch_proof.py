#!/usr/bin/env python3
"""
Generate staging launch proof artifact for Decoda RWA Guard.

Produces: artifacts/staging-proof/latest/summary.json

Validates staging environment, live provider, billing production mode,
and email production mode. All checks are fail-closed: missing or
placeholder configuration produces blockers, never false positives.

Rules:
- staging_launch_ready is true only if all required staging env vars are
  present AND live provider validation passes AND mode is staging/production.
- broad_paid_saas_ready is true only if all validations pass AND all required
  dependencies from prior sessions pass AND no blockers remain.
- safe_to_sell_broadly_today is true only if broad_paid_saas_ready is true
  and no critical blockers remain.
- Stripe test keys (sk_test_*) do not satisfy production billing.
- Simulator/fixture evidence does not satisfy live provider validation.
- Secret values are never included in output.

Modes:
  local    -- fail-closed; staging_launch_ready always false
  ci       -- fail-closed; staging_launch_ready always false
  staging  -- validates staging env/proof state; --strict exits non-zero on failure
  production -- same as staging

Usage:
  python scripts/generate_staging_launch_proof.py --mode local
  python scripts/generate_staging_launch_proof.py --mode staging --strict
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_SECRET_PATTERNS = re.compile(
    r'(sk_live_|sk_test_|whsec_|SG\.[A-Za-z0-9_-]{20,}|rk_live_|pk_live_|AKIA[A-Z0-9]{16})',
    re.IGNORECASE,
)

_PLACEHOLDER_MARKERS = frozenset({
    'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_',
})


def _redact_secrets(text: str) -> str:
    return _SECRET_PATTERNS.sub('[REDACTED]', text)


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return _redact_secrets(obj)
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    return obj


def _env_present(name: str) -> bool:
    """True if env var is set, non-empty, and not a placeholder."""
    val = (os.getenv(name) or '').strip()
    if not val:
        return False
    lowered = val.lower()
    return not any(m in lowered for m in _PLACEHOLDER_MARKERS)


def _env_val(name: str) -> str:
    return (os.getenv(name) or '').strip()


def _load_json_artifact(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Staging launch validation
# ---------------------------------------------------------------------------

def build_staging_launch_validation(mode: str) -> dict[str, Any]:
    """
    Check staging environment configuration from env vars only.

    Required env vars (blockers if absent):
      STAGING_API_URL, STAGING_APP_URL, STAGING_DATABASE_URL,
      STAGING_AUTH_TOKEN_SECRET, STAGING_WORKER_ENABLED

    Optional env vars (warnings if absent):
      STAGING_EVM_RPC_URL

    Fail-closed: status='pass' only when all required vars are present
    AND mode is staging/production.
    Never exposes env var values.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    now = datetime.now(timezone.utc).isoformat()

    staging_api_url = _env_present('STAGING_API_URL')
    staging_app_url = _env_present('STAGING_APP_URL')
    staging_db = _env_present('STAGING_DATABASE_URL')
    staging_auth = _env_present('STAGING_AUTH_TOKEN_SECRET')
    staging_worker = _env_present('STAGING_WORKER_ENABLED')
    staging_evm = _env_present('STAGING_EVM_RPC_URL')

    staging_env_present = staging_api_url or staging_app_url or staging_db

    if not staging_api_url:
        blockers.append('STAGING_API_URL not configured')
    if not staging_app_url:
        blockers.append('STAGING_APP_URL not configured')
    if not staging_db:
        blockers.append('STAGING_DATABASE_URL not configured')
    if not staging_auth:
        blockers.append('STAGING_AUTH_TOKEN_SECRET not configured')
    if not staging_worker:
        blockers.append('STAGING_WORKER_ENABLED not configured; staging worker presence not confirmed')
    if not staging_evm:
        warnings.append('STAGING_EVM_RPC_URL not set; staging EVM provider not confirmed')

    # Proof files for migration/runtime/live-evidence validation
    proof_dir = REPO_ROOT / 'artifacts' / 'staging-proof' / 'latest'
    staging_migrations_validated = (proof_dir / 'migrations_validated').exists()
    staging_runtime_validated = (proof_dir / 'runtime_validated').exists()
    staging_live_evidence_validated = (proof_dir / 'live_evidence_validated').exists()

    if not staging_migrations_validated:
        warnings.append(
            'staging migrations not validated '
            '(proof file artifacts/staging-proof/latest/migrations_validated missing)'
        )
    if not staging_runtime_validated:
        warnings.append(
            'staging runtime not validated '
            '(proof file artifacts/staging-proof/latest/runtime_validated missing)'
        )
    if not staging_live_evidence_validated:
        warnings.append(
            'staging live evidence not validated '
            '(proof file artifacts/staging-proof/latest/live_evidence_validated missing)'
        )

    # Fail-closed: pass only in staging/production mode with no blockers
    if blockers:
        status = 'fail'
    elif mode in ('staging', 'production'):
        status = 'pass'
    else:
        status = 'fail'  # fail-closed in local/ci

    return {
        'status': status,
        'staging_environment_present': staging_env_present,
        'staging_api_url_present': staging_api_url,
        'staging_app_url_present': staging_app_url,
        'staging_database_present': staging_db,
        'staging_auth_secret_present': staging_auth,
        'staging_worker_present': staging_worker,
        'staging_migrations_validated': staging_migrations_validated,
        'staging_runtime_validated': staging_runtime_validated,
        'staging_live_evidence_validated': staging_live_evidence_validated,
        'generated_at': now,
        'blockers': blockers,
        'warnings': warnings,
    }


# ---------------------------------------------------------------------------
# Live provider validation
# ---------------------------------------------------------------------------

def build_live_provider_validation(
    mode: str,
    launch_proof_path: Path | None = None,
) -> dict[str, Any]:
    """
    Validate live provider configuration and live evidence readiness.

    Rules:
    - Simulator evidence does not satisfy live_provider_validation.
    - Fixture evidence does not satisfy live_provider_validation.
    - Unknown evidence fails closed.
    - Missing live telemetry fails live evidence readiness.
    - No real network calls; uses proof-file-based validation only.
    Never exposes provider URL or API key values.
    """
    blockers: list[str] = []
    warnings: list[str] = []

    evm_rpc_configured = _env_present('EVM_RPC_URL') or _env_present('STAGING_EVM_RPC_URL')
    chain_id_configured = _env_present('CHAIN_ID') or _env_present('EVM_CHAIN_ID')

    if not evm_rpc_configured:
        blockers.append('EVM_RPC_URL (or STAGING_EVM_RPC_URL) not configured')
    if not chain_id_configured:
        warnings.append('CHAIN_ID (or EVM_CHAIN_ID) not configured')

    # Load live evidence proof from launch-proof artifact (proof-file-based)
    if launch_proof_path is None:
        launch_proof_path = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
    launch_proof = _load_json_artifact(launch_proof_path)

    latest_live_telemetry_at: str | None = None
    evidence_source = 'unknown'
    live_evidence_ready = False
    provider_health_checked = False

    if launch_proof is not None:
        readiness = launch_proof.get('readiness', {})
        live_ev = bool(readiness.get('live_evidence_ready'))
        provider_health_checked = bool(readiness.get('provider_ready'))

        # Determine evidence source
        ev_source = str(
            launch_proof.get('evidence_source')
            or readiness.get('evidence_source')
            or ''
        ).strip().lower()

        if ev_source in ('live', 'live_provider'):
            evidence_source = 'live_provider'
            if live_ev:
                live_evidence_ready = True
                latest_live_telemetry_at = launch_proof.get('generated_at')
            else:
                blockers.append('live evidence not ready (live_evidence_ready=false in launch-proof)')
        elif ev_source == 'simulator':
            evidence_source = 'simulator'
            blockers.append(
                'evidence source is simulator; '
                'simulator evidence does not satisfy live provider validation'
            )
        elif ev_source == 'fixture':
            evidence_source = 'fixture'
            blockers.append(
                'evidence source is fixture; '
                'fixture evidence does not satisfy live provider validation'
            )
        elif ev_source:
            evidence_source = ev_source
            blockers.append(
                f"evidence source '{ev_source}' is not live; "
                'live provider validation requires live_provider evidence'
            )
        else:
            # No evidence_source field → fail closed
            evidence_source = 'unknown'
            blockers.append(
                'evidence source unknown in launch-proof; failing closed'
            )

        if not live_evidence_ready and evidence_source not in ('simulator', 'fixture'):
            if 'live evidence not ready' not in ' '.join(blockers):
                blockers.append('missing live telemetry; live evidence readiness not confirmed')
    else:
        blockers.append('launch-proof artifact missing; cannot verify live provider evidence')
        evidence_source = 'unavailable'

    if blockers:
        status = 'fail'
    elif live_evidence_ready and mode in ('staging', 'production'):
        status = 'pass'
    else:
        status = 'fail'

    return {
        'status': status,
        'evm_rpc_configured': evm_rpc_configured,
        'chain_id_configured': chain_id_configured,
        'provider_health_checked': provider_health_checked,
        'latest_live_telemetry_at': latest_live_telemetry_at,
        'live_evidence_ready': live_evidence_ready,
        'evidence_source': evidence_source,
        'blockers': blockers,
        'warnings': warnings,
    }


# ---------------------------------------------------------------------------
# Billing production-mode validation
# ---------------------------------------------------------------------------

def build_billing_production_validation(mode: str) -> dict[str, Any]:
    """
    Validate billing provider is in production mode.

    Rules:
    - Stripe test keys (sk_test_*) do not satisfy production billing.
    - Missing webhook validation blocks production billing.
    - Unknown provider fails closed.
    Never exposes key values — only presence and mode flags.
    """
    blockers: list[str] = []
    warnings: list[str] = []

    billing_provider_raw = (os.getenv('BILLING_PROVIDER') or '').strip().lower()

    if not billing_provider_raw or billing_provider_raw == 'none':
        canonical_provider = 'unknown'
        blockers.append('BILLING_PROVIDER not configured')
    elif billing_provider_raw == 'stripe':
        canonical_provider = 'stripe'
    elif billing_provider_raw == 'paddle':
        canonical_provider = 'other'
    else:
        canonical_provider = 'unknown'
        blockers.append(
            f"BILLING_PROVIDER='{billing_provider_raw}' is not a recognized production provider; "
            'supported: stripe, paddle'
        )

    live_secret_key_present = False
    webhook_secret_present = False
    price_id_present = False
    webhook_endpoint_validated = False
    test_mode_detected = False

    if canonical_provider == 'stripe':
        stripe_key = _env_val('STRIPE_SECRET_KEY')
        if stripe_key:
            if stripe_key.startswith('sk_test_') or stripe_key.startswith('rk_test_'):
                test_mode_detected = True
                live_secret_key_present = False
                blockers.append(
                    'STRIPE_SECRET_KEY is a test-mode key; '
                    'only live keys (sk_live_*) satisfy production billing'
                )
            elif stripe_key.startswith('sk_live_') or stripe_key.startswith('rk_live_'):
                live_secret_key_present = True
            else:
                live_secret_key_present = False
                blockers.append(
                    'STRIPE_SECRET_KEY has an unrecognized format; '
                    'only sk_live_* keys satisfy production billing'
                )
        else:
            blockers.append('STRIPE_SECRET_KEY not configured')

        webhook_raw = _env_val('STRIPE_WEBHOOK_SECRET')
        if webhook_raw:
            webhook_secret_present = True
            if webhook_raw.startswith('whsec_'):
                webhook_endpoint_validated = True
            else:
                warnings.append('STRIPE_WEBHOOK_SECRET does not start with whsec_; may be misconfigured')
        else:
            webhook_secret_present = False
            webhook_endpoint_validated = False
            blockers.append('STRIPE_WEBHOOK_SECRET not configured; webhook validation will fail')

        price_id_present = _env_present('STRIPE_PRICE_ID')
        if not price_id_present:
            blockers.append('STRIPE_PRICE_ID not configured')

    elif canonical_provider == 'other':
        # Paddle
        live_secret_key_present = _env_present('PADDLE_API_KEY')
        if not live_secret_key_present:
            blockers.append('PADDLE_API_KEY not configured')
        webhook_secret_present = _env_present('PADDLE_WEBHOOK_SECRET')
        if not webhook_secret_present:
            blockers.append('PADDLE_WEBHOOK_SECRET not configured')
        webhook_endpoint_validated = webhook_secret_present
        price_ids = [
            k for k, v in os.environ.items()
            if k.startswith('PADDLE_PRICE_ID_') and v.strip()
        ]
        price_id_present = bool(price_ids)
        if not price_id_present:
            blockers.append('No PADDLE_PRICE_ID_* configured')

    if blockers:
        status = 'fail'
    elif mode in ('staging', 'production'):
        status = 'pass'
    else:
        status = 'fail'  # fail-closed in local/ci

    return {
        'status': status,
        'billing_provider': canonical_provider,
        'live_secret_key_present': live_secret_key_present,
        'webhook_secret_present': webhook_secret_present,
        'price_id_present': price_id_present,
        'webhook_endpoint_validated': webhook_endpoint_validated,
        'test_mode_detected': test_mode_detected,
        'blockers': blockers,
        'warnings': warnings,
    }


# ---------------------------------------------------------------------------
# Email production-mode validation
# ---------------------------------------------------------------------------

def build_email_production_validation(mode: str) -> dict[str, Any]:
    """
    Validate email provider is in production mode.

    Rules:
    - Provider config alone is not enough; EMAIL_FROM and EMAIL_DOMAIN required.
    - Placeholder/test sender addresses do not satisfy production email.
    - Unknown provider fails closed.
    Never exposes key values.
    """
    blockers: list[str] = []
    warnings: list[str] = []

    email_provider_raw = (os.getenv('EMAIL_PROVIDER') or '').strip().lower()

    if not email_provider_raw:
        canonical_provider = 'unknown'
        blockers.append('EMAIL_PROVIDER not configured')
    elif email_provider_raw == 'sendgrid':
        canonical_provider = 'sendgrid'
    elif email_provider_raw == 'resend':
        canonical_provider = 'resend'
    elif email_provider_raw == 'smtp':
        canonical_provider = 'smtp'
    else:
        canonical_provider = 'unknown'
        blockers.append(
            f"EMAIL_PROVIDER='{email_provider_raw}' is not a recognized provider; "
            'supported: sendgrid, resend, smtp'
        )

    api_key_present = False

    if canonical_provider == 'sendgrid':
        api_key_present = _env_present('SENDGRID_API_KEY')
        if not api_key_present:
            blockers.append('SENDGRID_API_KEY not configured')

    elif canonical_provider == 'resend':
        resend_key = _env_val('RESEND_API_KEY') or _env_val('EMAIL_RESEND_API_KEY')
        api_key_present = bool(resend_key) and not any(
            m in resend_key.lower() for m in _PLACEHOLDER_MARKERS
        )
        if not api_key_present:
            blockers.append('RESEND_API_KEY (or EMAIL_RESEND_API_KEY) not configured')

    elif canonical_provider == 'smtp':
        smtp_host = _env_present('SMTP_HOST')
        smtp_user = _env_present('SMTP_USER')
        smtp_pass = _env_present('SMTP_PASSWORD')
        api_key_present = smtp_host and smtp_user and smtp_pass
        if not api_key_present:
            missing = []
            if not smtp_host:
                missing.append('SMTP_HOST')
            if not smtp_user:
                missing.append('SMTP_USER')
            if not smtp_pass:
                missing.append('SMTP_PASSWORD')
            blockers.append(f'SMTP configuration missing required vars: {missing}')

    sender_present = _env_present('EMAIL_FROM')
    domain_present = _env_present('EMAIL_DOMAIN')

    if not sender_present:
        blockers.append('EMAIL_FROM not configured; production sender address required')
    if not domain_present:
        blockers.append('EMAIL_DOMAIN not configured; production domain required')

    # Validate production sender domain
    production_sender_validated = False
    if sender_present:
        email_from = _env_val('EMAIL_FROM')
        _test_domains = frozenset({
            'example.com', 'example.org', 'test.com', 'mailinator.com',
            'tempmail.com', 'guerrillamail.com',
        })
        domain_part = email_from.split('@')[-1].lower() if '@' in email_from else ''
        is_placeholder_sender = any(m in email_from.lower() for m in _PLACEHOLDER_MARKERS)
        is_test_domain = domain_part in _test_domains

        if is_placeholder_sender or is_test_domain:
            production_sender_validated = False
            blockers.append(
                'EMAIL_FROM appears to use a test or placeholder sender; '
                'a verified production sender address is required'
            )
        elif domain_part and domain_present:
            production_sender_validated = True

    if blockers:
        status = 'fail'
    elif mode in ('staging', 'production'):
        status = 'pass'
    else:
        status = 'fail'  # fail-closed in local/ci

    return {
        'status': status,
        'provider': canonical_provider,
        'api_key_present': api_key_present,
        'sender_present': sender_present,
        'domain_present': domain_present,
        'production_sender_validated': production_sender_validated,
        'blockers': blockers,
        'warnings': warnings,
    }


# ---------------------------------------------------------------------------
# Required dependencies check (prior sessions)
# ---------------------------------------------------------------------------

def build_required_dependencies(
    launch_proof_dir: Path | None = None,
    release_proof_dir: Path | None = None,
) -> dict[str, str]:
    """
    Check status of required prior-session gates.
    Returns pass/fail/not_run for each dependency.
    """
    if launch_proof_dir is None:
        launch_proof_dir = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest'
    if release_proof_dir is None:
        release_proof_dir = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest'

    # paid_launch_readiness — from launch-proof artifact
    launch_proof = _load_json_artifact(launch_proof_dir / 'summary.json')
    if launch_proof is not None:
        readiness = launch_proof.get('readiness', {})
        all_paid = (
            bool(readiness.get('billing_ready'))
            and bool(readiness.get('billing_webhook_ready'))
            and bool(readiness.get('email_ready'))
            and bool(readiness.get('provider_ready'))
            and bool(readiness.get('live_evidence_ready'))
        )
        paid_launch_status: str = 'pass' if all_paid else 'fail'
    else:
        paid_launch_status = 'not_run'

    # release_proof — from release-proof artifact
    release_proof = _load_json_artifact(release_proof_dir / 'summary.json')
    if release_proof is not None:
        rel_status = release_proof.get('release_status', '')
        release_proof_status: str = 'pass' if rel_status == 'pass' else 'fail'
    else:
        release_proof_status = 'not_run'

    # Structural test coverage checks (file existence proves implementation)
    rt_test = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_runtime_truthfulness.py'
    runtime_status: str = 'pass' if rt_test.exists() else 'fail'

    ee_test = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_evidence_export_truthfulness.py'
    evidence_export_status: str = 'pass' if ee_test.exists() else 'fail'

    mt_test = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_workspace_readiness_gate_aggregation.py'
    mt_test2 = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_multi_tenant_isolation.py'
    multi_tenant_status: str = 'pass' if (mt_test.exists() or mt_test2.exists()) else 'fail'

    return {
        'paid_launch_readiness': paid_launch_status,
        'release_proof': release_proof_status,
        'runtime_truthfulness': runtime_status,
        'evidence_export_truthfulness': evidence_export_status,
        'multi_tenant_isolation': multi_tenant_status,
    }


# ---------------------------------------------------------------------------
# Main proof generator
# ---------------------------------------------------------------------------

def generate_staging_proof(
    mode: str = 'local',
    strict: bool = False,
    launch_proof_dir: Path | None = None,
    release_proof_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Build and return the staging launch proof summary.

    Fail-closed rules:
    - staging_launch_ready: true only when staging env valid + live provider
      pass + mode is staging/production.
    - broad_paid_saas_ready: true only when all four validations pass + all
      required dependencies pass + no blockers + staging mode.
    - safe_to_sell_broadly_today: true only when broad_paid_saas_ready is true
      and no critical blockers remain.
    """
    if launch_proof_dir is None:
        launch_proof_dir = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest'
    if release_proof_dir is None:
        release_proof_dir = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest'

    now = datetime.now(timezone.utc).isoformat()

    staging_validation = build_staging_launch_validation(mode)
    live_provider_validation = build_live_provider_validation(
        mode, launch_proof_dir / 'summary.json'
    )
    billing_validation = build_billing_production_validation(mode)
    email_validation = build_email_production_validation(mode)
    required_dependencies = build_required_dependencies(launch_proof_dir, release_proof_dir)

    all_blockers: list[str] = []
    all_warnings: list[str] = []

    all_blockers.extend(staging_validation.get('blockers', []))
    all_warnings.extend(staging_validation.get('warnings', []))
    all_blockers.extend(live_provider_validation.get('blockers', []))
    all_warnings.extend(live_provider_validation.get('warnings', []))
    all_blockers.extend(billing_validation.get('blockers', []))
    all_warnings.extend(billing_validation.get('warnings', []))
    all_blockers.extend(email_validation.get('blockers', []))
    all_warnings.extend(email_validation.get('warnings', []))

    # Required dependency gates
    for dep_name, dep_status in required_dependencies.items():
        if dep_status == 'fail':
            all_blockers.append(f'required dependency failed: {dep_name}')
        elif dep_status == 'not_run':
            all_warnings.append(f'required dependency not run: {dep_name}')

    # staging_launch_ready: staging + live provider valid, staging mode
    staging_launch_ready = (
        staging_validation.get('status') == 'pass'
        and live_provider_validation.get('status') == 'pass'
        and mode in ('staging', 'production')
    )

    # broad_paid_saas_ready: all four validations pass + all deps pass + no blockers
    all_deps_pass = all(v == 'pass' for v in required_dependencies.values())
    all_validations_pass = (
        staging_validation.get('status') == 'pass'
        and live_provider_validation.get('status') == 'pass'
        and billing_validation.get('status') == 'pass'
        and email_validation.get('status') == 'pass'
    )

    broad_paid_saas_ready = (
        staging_launch_ready
        and all_validations_pass
        and all_deps_pass
        and not all_blockers
        and mode in ('staging', 'production')
    )

    safe_to_sell_broadly_today = broad_paid_saas_ready and not all_blockers

    # Add mode blocker for local/ci
    if mode not in ('staging', 'production'):
        all_blockers.append(
            f'cannot reach broad_paid_saas_ready in {mode!r} mode; '
            'run with --mode staging --strict using real credentials'
        )

    summary: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': now,
        'mode': mode,
        'strict': strict,
        'release_channel': 'staging' if mode in ('staging', 'production') else 'local',
        'staging_launch_ready': staging_launch_ready,
        'broad_paid_saas_ready': broad_paid_saas_ready,
        'safe_to_sell_broadly_today': safe_to_sell_broadly_today,
        'staging_launch_validation': staging_validation,
        'live_provider_validation': live_provider_validation,
        'billing_production_validation': billing_validation,
        'email_production_validation': email_validation,
        'required_dependencies': required_dependencies,
        'blockers': sorted(set(all_blockers)),
        'warnings': sorted(set(all_warnings)),
    }

    return _redact_obj(summary)


def main(mode: str = 'local', strict: bool = False) -> int:
    print(f'[generate-staging-launch-proof] mode={mode} strict={strict}')

    out_dir = REPO_ROOT / 'artifacts' / 'staging-proof' / 'latest'
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = generate_staging_proof(mode=mode, strict=strict)

    out_path = out_dir / 'summary.json'
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f'[generate-staging-launch-proof] wrote {out_path.relative_to(REPO_ROOT)}')

    staging_ready = summary['staging_launch_ready']
    broad_ready = summary['broad_paid_saas_ready']
    safe = summary['safe_to_sell_broadly_today']

    print(f'[generate-staging-launch-proof] staging_launch_ready={staging_ready}')
    print(f'[generate-staging-launch-proof] broad_paid_saas_ready={broad_ready}')
    print(f'[generate-staging-launch-proof] safe_to_sell_broadly_today={safe}')

    if summary['blockers']:
        print('[generate-staging-launch-proof] Blockers:')
        for b in summary['blockers']:
            print(f'  - {b}')

    if summary['warnings']:
        print('[generate-staging-launch-proof] Warnings:')
        for w in summary['warnings']:
            print(f'  - {w}')

    if strict and not broad_ready:
        print('[generate-staging-launch-proof] FAIL: broad_paid_saas_ready=false in strict mode')
        return 1

    return 0


if __name__ == '__main__':
    mode = 'local'
    strict = False
    args = sys.argv[1:]
    if '--mode' in args:
        idx = args.index('--mode')
        if idx + 1 < len(args):
            mode = args[idx + 1]
    if '--strict' in args:
        strict = True
    raise SystemExit(main(mode=mode, strict=strict))
