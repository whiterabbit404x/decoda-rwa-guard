#!/usr/bin/env python3
"""
Validate staging launch proof artifact for Decoda RWA Guard.

Validates: artifacts/staging-proof/latest/summary.json

Fail-closed rules enforced:
- Required fields must be present.
- broad_paid_saas_ready cannot be true unless all four validation sections pass.
- safe_to_sell_broadly_today cannot be true unless broad_paid_saas_ready is true.
- Simulator/fixture evidence cannot coexist with live_provider_validation.status=pass.
- test_mode_detected=true cannot coexist with billing_production_validation.status=pass.
- Blockers present → broad/safe cannot be true.
- No secret-like values may appear in the artifact.

Usage:
  python scripts/validate_staging_launch_proof.py
  python scripts/validate_staging_launch_proof.py --artifact-path <path>

Exits non-zero if the artifact is invalid, missing, or overclaims readiness.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

_SECRET_PATTERNS = re.compile(
    r'(sk_live_[A-Za-z0-9]{10,}|sk_test_[A-Za-z0-9]{10,}'
    r'|whsec_[A-Za-z0-9]{10,}|SG\.[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})',
    re.IGNORECASE,
)

REQUIRED_TOP_LEVEL_FIELDS = [
    'schema_version',
    'generated_at',
    'release_channel',
    'staging_launch_ready',
    'broad_paid_saas_ready',
    'safe_to_sell_broadly_today',
    'staging_launch_validation',
    'live_provider_validation',
    'billing_production_validation',
    'email_production_validation',
    'required_dependencies',
    'blockers',
    'warnings',
]

REQUIRED_STAGING_VALIDATION_FIELDS = [
    'status',
    'staging_environment_present',
    'staging_api_url_present',
    'staging_app_url_present',
    'staging_database_present',
    'staging_auth_secret_present',
    'staging_worker_present',
    'staging_migrations_validated',
    'staging_runtime_validated',
    'staging_live_evidence_validated',
    'generated_at',
    'blockers',
    'warnings',
]

REQUIRED_LIVE_PROVIDER_FIELDS = [
    'status',
    'evm_rpc_configured',
    'chain_id_configured',
    'provider_health_checked',
    'provider_ready',
    'provider_mode',
    'live_evidence_ready',
    'evidence_source',
    'chain',
    'missing',
    'contradiction_flags',
    'blockers',
    'warnings',
]

REQUIRED_LIVE_PROVIDER_CHAIN_FIELDS = [
    'telemetry_event_id',
    'detection_id',
    'alert_id',
    'incident_id',
    'evidence_package_id',
]

REQUIRED_BILLING_FIELDS = [
    'status',
    'billing_provider',
    'live_secret_key_present',
    'webhook_secret_present',
    'price_id_present',
    'webhook_endpoint_validated',
    'test_mode_detected',
    'blockers',
    'warnings',
]

REQUIRED_EMAIL_FIELDS = [
    'status',
    'provider',
    'api_key_present',
    'sender_present',
    'domain_present',
    'production_sender_validated',
    'blockers',
    'warnings',
]

REQUIRED_DEPENDENCY_KEYS = [
    'paid_launch_readiness',
    'release_proof',
    'runtime_truthfulness',
    'evidence_export_truthfulness',
    'multi_tenant_isolation',
]


def _check_no_secrets(text: str) -> list[str]:
    matches = _SECRET_PATTERNS.findall(text)
    return [
        f'secret-like value found in artifact: {m[:12]}...'
        for m in matches
    ]


def validate_staging_proof(artifact_path: Path) -> tuple[bool, list[str], list[str]]:
    """
    Validate staging launch proof artifact.

    Returns (is_valid, errors, warnings).
    errors is non-empty when validation fails.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not artifact_path.exists():
        return False, [f'staging proof artifact missing: {artifact_path}'], []

    try:
        with open(artifact_path) as f:
            content = f.read()
        artifact: dict[str, Any] = json.loads(content)
    except Exception as e:
        return False, [f'staging proof artifact unreadable or not valid JSON: {e}'], []

    # Check for secret leakage
    errors.extend(_check_no_secrets(content))

    # Check required top-level fields
    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in artifact:
            errors.append(f'missing required top-level field: {field}')

    if errors:
        return False, errors, warnings

    # Validate sub-sections structure
    staging_val: dict[str, Any] = artifact.get('staging_launch_validation', {})
    for field in REQUIRED_STAGING_VALIDATION_FIELDS:
        if field not in staging_val:
            errors.append(f'staging_launch_validation missing field: {field}')

    live_val: dict[str, Any] = artifact.get('live_provider_validation', {})
    for field in REQUIRED_LIVE_PROVIDER_FIELDS:
        if field not in live_val:
            errors.append(f'live_provider_validation missing field: {field}')

    # Validate chain sub-fields when present
    live_chain = live_val.get('chain', {})
    if isinstance(live_chain, dict):
        for field in REQUIRED_LIVE_PROVIDER_CHAIN_FIELDS:
            if field not in live_chain:
                errors.append(f'live_provider_validation.chain missing field: {field}')

    billing_val: dict[str, Any] = artifact.get('billing_production_validation', {})
    for field in REQUIRED_BILLING_FIELDS:
        if field not in billing_val:
            errors.append(f'billing_production_validation missing field: {field}')

    email_val: dict[str, Any] = artifact.get('email_production_validation', {})
    for field in REQUIRED_EMAIL_FIELDS:
        if field not in email_val:
            errors.append(f'email_production_validation missing field: {field}')

    deps: dict[str, Any] = artifact.get('required_dependencies', {})
    for key in REQUIRED_DEPENDENCY_KEYS:
        if key not in deps:
            errors.append(f'required_dependencies missing key: {key}')

    if errors:
        return False, errors, warnings

    # Enforce fail-closed rules

    broad_ready = bool(artifact.get('broad_paid_saas_ready'))
    safe = bool(artifact.get('safe_to_sell_broadly_today'))

    # Rule 1: safe_to_sell cannot be true if broad_paid_saas_ready is false
    if safe and not broad_ready:
        errors.append(
            'OVERCLAIM: safe_to_sell_broadly_today=true '
            'but broad_paid_saas_ready=false'
        )

    # Rule 2: broad_paid_saas_ready requires all four validation sections to pass
    if broad_ready:
        sv_status = staging_val.get('status')
        lv_status = live_val.get('status')
        bv_status = billing_val.get('status')
        ev_status = email_val.get('status')

        if sv_status != 'pass':
            errors.append(
                f'OVERCLAIM: broad_paid_saas_ready=true '
                f'but staging_launch_validation.status={sv_status!r}'
            )
        if lv_status != 'pass':
            errors.append(
                f'OVERCLAIM: broad_paid_saas_ready=true '
                f'but live_provider_validation.status={lv_status!r}'
            )
        if bv_status != 'pass':
            errors.append(
                f'OVERCLAIM: broad_paid_saas_ready=true '
                f'but billing_production_validation.status={bv_status!r}'
            )
        if ev_status != 'pass':
            errors.append(
                f'OVERCLAIM: broad_paid_saas_ready=true '
                f'but email_production_validation.status={ev_status!r}'
            )

        # All required deps must pass
        for dep_key, dep_status in deps.items():
            if dep_status != 'pass':
                errors.append(
                    f'OVERCLAIM: broad_paid_saas_ready=true '
                    f'but required dependency {dep_key!r}={dep_status!r}'
                )

    # Rule 3: blockers present → broad/safe cannot be true
    blockers = artifact.get('blockers', [])
    if blockers and broad_ready:
        errors.append(
            f'OVERCLAIM: broad_paid_saas_ready=true '
            f'but artifact has {len(blockers)} blocker(s)'
        )
    if blockers and safe:
        errors.append(
            f'OVERCLAIM: safe_to_sell_broadly_today=true '
            f'but artifact has {len(blockers)} blocker(s)'
        )

    # Rule 4: Simulator/fixture evidence cannot satisfy live provider validation
    ev_source = str(live_val.get('evidence_source', 'unknown')).lower()
    if ev_source in ('simulator', 'fixture') and live_val.get('status') == 'pass':
        errors.append(
            f'OVERCLAIM: live_provider_validation.status=pass '
            f'but evidence_source={ev_source!r}'
        )

    # Rule 6: live_evidence_ready=true requires full chain IDs to be present
    if live_val.get('live_evidence_ready'):
        chain = live_val.get('chain', {}) if isinstance(live_val.get('chain'), dict) else {}
        for chain_field in ('telemetry_event_id', 'detection_id', 'alert_id', 'evidence_package_id'):
            if not chain.get(chain_field):
                errors.append(
                    f'OVERCLAIM: live_evidence_ready=true '
                    f'but chain.{chain_field} is missing or null'
                )
        if not (chain.get('incident_id') or chain.get('response_action_id')):
            errors.append(
                'OVERCLAIM: live_evidence_ready=true '
                'but chain has no incident_id or response_action_id'
            )

    # Rule 7: live_evidence_ready=true requires live evidence_source
    if live_val.get('live_evidence_ready') and ev_source not in ('live', 'live_provider', 'live_rpc'):
        errors.append(
            f'OVERCLAIM: live_evidence_ready=true '
            f'but evidence_source={ev_source!r} is not a live source'
        )

    # Rule 8: contradiction_flags present → live_evidence_ready must be false
    contradiction_flags = live_val.get('contradiction_flags', [])
    if contradiction_flags and live_val.get('live_evidence_ready'):
        errors.append(
            f'OVERCLAIM: live_evidence_ready=true '
            f'but contradiction_flags are present: {contradiction_flags[:3]}'
        )

    # Rule 9: staging_launch_ready=true while provider_ready=false
    staging_ready = bool(artifact.get('staging_launch_ready'))
    if staging_ready and not live_val.get('provider_ready'):
        errors.append(
            'OVERCLAIM: staging_launch_ready=true '
            'but live_provider_validation.provider_ready=false'
        )

    # Rule 10: live_evidence_ready=true while evm_rpc_configured=false
    if live_val.get('live_evidence_ready') and live_val.get('evm_rpc_configured') is False:
        errors.append(
            'OVERCLAIM: live_evidence_ready=true '
            'but evm_rpc_configured=false (no RPC URL configured)'
        )

    # Rule 11: freshness_status=current while last_telemetry_at is missing
    freshness_status = live_val.get('freshness_status')
    last_telemetry_at = live_val.get('latest_live_telemetry_at')
    if freshness_status == 'current' and not last_telemetry_at:
        errors.append(
            'OVERCLAIM: freshness_status=current '
            'but latest_live_telemetry_at is missing or null'
        )

    # Rule 12: monitoring_status=healthy while reporting_systems=0
    monitoring_status = live_val.get('monitoring_status')
    reporting_systems = live_val.get('reporting_systems')
    if monitoring_status == 'healthy' and reporting_systems == 0:
        errors.append(
            'OVERCLAIM: monitoring_status=healthy '
            'but reporting_systems=0 (no reporting systems active)'
        )

    # Rule 5: test_mode_detected=true cannot coexist with billing status=pass
    if billing_val.get('test_mode_detected') and billing_val.get('status') == 'pass':
        errors.append(
            'OVERCLAIM: billing_production_validation.status=pass '
            'but test_mode_detected=true'
        )

    # Informational warnings (not errors)
    if not broad_ready:
        warnings.append('broad_paid_saas_ready=false; broad paid SaaS launch not cleared')
    if not safe:
        warnings.append('safe_to_sell_broadly_today=false; do not sell broadly yet')
    if staging_val.get('status') == 'not_run':
        warnings.append('staging_launch_validation.status=not_run; staging was not evaluated')

    is_valid = not errors
    return is_valid, errors, warnings


def main(artifact_path: Path | None = None) -> int:
    if artifact_path is None:
        artifact_path = REPO_ROOT / 'artifacts' / 'staging-proof' / 'latest' / 'summary.json'

    try:
        display_path = artifact_path.relative_to(REPO_ROOT)
    except ValueError:
        display_path = artifact_path

    print(f'[validate-staging-launch-proof] validating: {display_path}')

    is_valid, errors, warnings = validate_staging_proof(artifact_path)

    if warnings:
        print('[validate-staging-launch-proof] Warnings:')
        for w in warnings:
            print(f'  - {w}')

    if errors:
        print('[validate-staging-launch-proof] FAIL — validation errors:')
        for e in errors:
            print(f'  - {e}')
        return 1

    print(
        '[validate-staging-launch-proof] PASS — '
        'artifact is structurally valid and fail-closed'
    )
    return 0


if __name__ == '__main__':
    artifact_path = None
    args = sys.argv[1:]
    if '--artifact-path' in args:
        idx = args.index('--artifact-path')
        if idx + 1 < len(args):
            artifact_path = Path(args[idx + 1])
    raise SystemExit(main(artifact_path=artifact_path))
