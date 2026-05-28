"""
Tests for staging-production-proof scope (blocker 4).

Verifies that the blocker-4-only scope:
  - passes when staging env vars are present and worker is enabled
  - does NOT require BILLING_PROVIDER, EMAIL_*, EVM_RPC_URL
  - fails when any required STAGING_* var is missing
  - fails when STAGING_WORKER_ENABLED is false/disabled
  - fails if staging_launch_ready=True appears in an artifact
    that has staging_app_url_present=False (overclaim guard)
  - accepts STAGING_EVM_RPC_URL as a valid EVM provider alias

Full paid SaaS readiness tests confirm that billing/email checks are
untouched in the full scope (validate_100_percent path).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.generate_staging_launch_proof import (
    BLOCKER4_SCOPE,
    build_live_provider_validation,
    build_staging_launch_validation,
    generate_staging_proof,
)
from scripts.validate_staging_launch_proof import (
    _validate_strict,
    validate_staging_proof,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_staging_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Avoid 'example', 'changeme', etc. — those are _PLACEHOLDER_MARKERS and fail _env_present().
    monkeypatch.setenv('STAGING_API_URL', 'https://api.staging.decoda.io')
    monkeypatch.setenv('STAGING_APP_URL', 'https://staging.decoda.io')
    monkeypatch.setenv('STAGING_DATABASE_URL', 'postgresql://u:p@db.staging:5432/guard')
    monkeypatch.setenv('STAGING_AUTH_TOKEN_SECRET', 'a-sufficiently-long-secret-value')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')


def _write_proof(path: Path, overrides: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = {
        'schema_version': 1,
        'scope': BLOCKER4_SCOPE,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'mode': 'staging',
        'strict': True,
        'release_channel': 'staging',
        'staging_launch_ready': True,
        'broad_paid_saas_ready': False,
        'safe_to_sell_broadly_today': False,
        'staging_launch_validation': {
            'status': 'pass',
            'staging_environment_present': True,
            'staging_api_url_present': True,
            'staging_app_url_present': True,
            'staging_database_present': True,
            'staging_auth_secret_present': True,
            'staging_worker_present': True,
            'staging_migrations_validated': False,
            'staging_runtime_validated': False,
            'staging_live_evidence_validated': False,
            'generated_at': '2026-01-01T00:00:00+00:00',
            'blockers': [],
            'warnings': [],
        },
        'live_provider_validation': {
            'status': 'not_applicable',
            'evm_rpc_configured': False,
            'chain_id_configured': False,
            'chain_id_observed': None,
            'provider_health_checked': False,
            'provider_ready': False,
            'provider_mode': 'disabled',
            'live_evidence_ready': False,
            'evidence_source': 'not_applicable',
            'chain': {
                'telemetry_event_id': None,
                'detection_id': None,
                'alert_id': None,
                'incident_id': None,
                'response_action_id': None,
                'evidence_package_id': None,
            },
            'missing': [],
            'contradiction_flags': [],
            'blockers': [],
            'warnings': [],
        },
        'billing_production_validation': {
            'status': 'not_applicable',
            'billing_provider': 'unknown',
            'live_secret_key_present': False,
            'webhook_secret_present': False,
            'price_id_present': False,
            'webhook_endpoint_validated': False,
            'test_mode_detected': False,
            'blockers': [],
            'warnings': [],
        },
        'email_production_validation': {
            'status': 'not_applicable',
            'provider': 'unknown',
            'api_key_present': False,
            'sender_present': False,
            'domain_present': False,
            'production_sender_validated': False,
            'blockers': [],
            'warnings': [],
        },
        'required_dependencies': {
            'paid_launch_readiness': 'not_applicable',
            'release_proof': 'not_applicable',
            'runtime_truthfulness': 'not_applicable',
            'evidence_export_truthfulness': 'not_applicable',
            'multi_tenant_isolation': 'not_applicable',
        },
        'blockers': [],
        'warnings': [],
    }
    if overrides:
        base.update(overrides)
    path.write_text(json.dumps(base))


# ---------------------------------------------------------------------------
# 1. Blocker4-only scope passes when staging checks pass + billing/email absent
# ---------------------------------------------------------------------------
def test_blocker4_passes_without_billing_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """staging-production-proof scope passes even when BILLING_PROVIDER and EMAIL_* are absent."""
    _set_staging_env(monkeypatch)

    # No billing or email env vars set
    for var in ('BILLING_PROVIDER', 'EMAIL_PROVIDER', 'EMAIL_FROM', 'EMAIL_DOMAIN',
                'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'STRIPE_PRICE_ID'):
        monkeypatch.delenv(var, raising=False)

    proof = generate_staging_proof(mode='staging', strict=False, scope=BLOCKER4_SCOPE)

    assert proof['scope'] == BLOCKER4_SCOPE
    assert proof['staging_launch_ready'] is True
    assert proof['broad_paid_saas_ready'] is False
    assert proof['safe_to_sell_broadly_today'] is False
    assert proof['blockers'] == []


# ---------------------------------------------------------------------------
# 2. Blocker4-only scope passes without EVM_RPC_URL when STAGING_EVM_RPC_URL absent
# ---------------------------------------------------------------------------
def test_blocker4_does_not_require_evm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EVM_RPC_URL is not required in staging-production-proof scope."""
    _set_staging_env(monkeypatch)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    proof = generate_staging_proof(mode='staging', strict=False, scope=BLOCKER4_SCOPE)

    assert proof['staging_launch_ready'] is True
    assert proof['blockers'] == []
    # EVM blocker must NOT appear in blocker4 scope
    assert not any('EVM_RPC_URL' in b for b in proof['blockers'])


# ---------------------------------------------------------------------------
# 3. Full paid SaaS scope still fails when billing/email are missing
# ---------------------------------------------------------------------------
def test_full_scope_fails_without_billing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default (full) scope still requires billing to reach broad_paid_saas_ready=True."""
    _set_staging_env(monkeypatch)
    for var in ('BILLING_PROVIDER', 'EMAIL_PROVIDER', 'EMAIL_FROM', 'EMAIL_DOMAIN'):
        monkeypatch.delenv(var, raising=False)

    proof = generate_staging_proof(
        mode='staging',
        strict=False,
        scope='',
        launch_proof_dir=tmp_path / 'launch-proof' / 'latest',
        release_proof_dir=tmp_path / 'release-proof' / 'latest',
    )

    assert proof['broad_paid_saas_ready'] is False
    assert any('BILLING_PROVIDER' in b for b in proof['blockers'])


# ---------------------------------------------------------------------------
# 4. Blocker4 strict fails when STAGING_API_URL is missing
# ---------------------------------------------------------------------------
def test_blocker4_strict_fails_missing_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict blocker4 proof fails when STAGING_API_URL is absent."""
    _set_staging_env(monkeypatch)
    monkeypatch.delenv('STAGING_API_URL', raising=False)

    proof = generate_staging_proof(mode='staging', strict=True, scope=BLOCKER4_SCOPE)

    assert proof['staging_launch_ready'] is False
    assert any('STAGING_API_URL' in b for b in proof['blockers'])


# ---------------------------------------------------------------------------
# 5. Blocker4 strict fails when STAGING_WORKER_ENABLED is false/disabled
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('val', ['false', '0', 'no', 'disabled', ''])
def test_blocker4_strict_fails_worker_disabled(
    monkeypatch: pytest.MonkeyPatch, val: str
) -> None:
    """Strict blocker4 proof fails when STAGING_WORKER_ENABLED is not truthy."""
    _set_staging_env(monkeypatch)
    if val == '':
        monkeypatch.delenv('STAGING_WORKER_ENABLED', raising=False)
    else:
        monkeypatch.setenv('STAGING_WORKER_ENABLED', val)

    proof = generate_staging_proof(mode='staging', strict=True, scope=BLOCKER4_SCOPE)

    assert proof['staging_launch_ready'] is False
    assert any('STAGING_WORKER_ENABLED' in b for b in proof['blockers'])


# ---------------------------------------------------------------------------
# 6. Validator strict catches overclaim: staging_launch_ready=True with
#    staging_app_url_present=False
# ---------------------------------------------------------------------------
def test_blocker4_validator_catches_overclaim(tmp_path: Path) -> None:
    """Strict validator rejects artifact with staging_launch_ready=True but app URL absent."""
    proof_path = tmp_path / 'summary.json'
    _write_proof(proof_path, overrides={
        'staging_launch_ready': True,
        'staging_launch_validation': {
            'status': 'fail',
            'staging_environment_present': True,
            'staging_api_url_present': True,
            'staging_app_url_present': False,  # overclaim
            'staging_database_present': True,
            'staging_auth_secret_present': True,
            'staging_worker_present': True,
            'staging_migrations_validated': False,
            'staging_runtime_validated': False,
            'staging_live_evidence_validated': False,
            'generated_at': '2026-01-01T00:00:00+00:00',
            'blockers': [],
            'warnings': [],
        },
    })

    artifact = json.loads(proof_path.read_text())
    strict_errors = _validate_strict(artifact)

    assert any('staging_app_url_present' in e for e in strict_errors), (
        f"Expected overclaim error for staging_app_url_present; got: {strict_errors}"
    )


# ---------------------------------------------------------------------------
# 7. EVM_RPC_URL and STAGING_EVM_RPC_URL are both accepted in full scope
# ---------------------------------------------------------------------------
def test_evm_rpc_url_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """EVM_RPC_URL satisfies live provider evm_rpc_configured."""
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/fakeid')

    result = build_live_provider_validation(
        'staging',
        launch_proof_path=tmp_path / 'missing.json',
    )

    assert result['evm_rpc_configured'] is True


def test_staging_evm_rpc_url_accepted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """STAGING_EVM_RPC_URL also satisfies live provider evm_rpc_configured."""
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.infura.io/v3/fakeid')

    result = build_live_provider_validation(
        'staging',
        launch_proof_path=tmp_path / 'missing.json',
    )

    assert result['evm_rpc_configured'] is True


# ---------------------------------------------------------------------------
# 8. Validator structural check passes for blocker4 proof artifact
# ---------------------------------------------------------------------------
def test_blocker4_validator_structural_pass(tmp_path: Path) -> None:
    """validate_staging_proof accepts a well-formed blocker4-scope artifact."""
    proof_path = tmp_path / 'summary.json'
    _write_proof(proof_path)

    is_valid, errors, _warnings = validate_staging_proof(proof_path)

    assert is_valid, f"Expected structural pass; errors: {errors}"
    assert errors == []


# ---------------------------------------------------------------------------
# 9. Blocker4 proof is fail-closed in local/ci mode
# ---------------------------------------------------------------------------
def test_blocker4_fail_closed_in_local_mode() -> None:
    """Without staging env vars, blocker4 scope is fail-closed in local mode."""
    proof = generate_staging_proof(mode='local', strict=False, scope=BLOCKER4_SCOPE)

    assert proof['staging_launch_ready'] is False
    assert len(proof['blockers']) > 0


# ---------------------------------------------------------------------------
# 10. Blocker4 proof contains scope field in output JSON
# ---------------------------------------------------------------------------
def test_blocker4_proof_contains_scope_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated blocker4 proof must include scope=staging-production-proof."""
    _set_staging_env(monkeypatch)

    proof = generate_staging_proof(mode='staging', strict=False, scope=BLOCKER4_SCOPE)

    assert proof.get('scope') == BLOCKER4_SCOPE
    assert 'readiness' in proof
    assert proof['readiness']['staging_launch_ready'] is True
    assert proof['readiness']['broad_paid_saas_ready'] is False
