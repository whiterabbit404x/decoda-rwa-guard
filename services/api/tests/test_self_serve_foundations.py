from __future__ import annotations

import pytest

from services.api.app import pilot


def test_totp_generation_and_verification_roundtrip() -> None:
    secret = pilot._b64url(b'01234567890123456789')
    code = pilot._totp_code(secret)
    assert len(code) == 6
    assert pilot._verify_totp(secret, code) is True
    assert pilot._verify_totp(secret, '000000') is False


def test_validate_runtime_configuration_production_requires_core_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.delenv('AUTH_TOKEN_SECRET', raising=False)
    payload = pilot.validate_runtime_configuration()
    assert payload['errors']
    assert any('AUTH_TOKEN_SECRET' in item for item in payload['errors'])


def test_email_message_contains_frontend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_PUBLIC_URL', 'https://app.example.com')
    subject, body = pilot._email_message('email_verification', token='token-123')
    assert 'Verify your email' in subject
    assert 'https://app.example.com/verify-email?token=token-123' in body


def test_onboarding_state_merges_manual_and_automatic_steps() -> None:
    manual = pilot._parse_onboarding_state({'industry_profile': True})
    automatic = {
        'workspace_created': True,
        'industry_profile': False,
        'asset_added': True,
        'policy_configured': False,
        'integration_connected': False,
        'teammates_invited': False,
        'analysis_run': False,
    }
    merged = {step: bool(manual.get(step)) or bool(automatic.get(step)) for step in pilot.ONBOARDING_STEP_ORDER}
    assert merged['workspace_created'] is True
    assert merged['industry_profile'] is True
    assert merged['asset_added'] is True
    assert merged['analysis_run'] is False


def test_default_onboarding_state_contains_all_steps() -> None:
    state = pilot._default_onboarding_state()
    assert list(state.keys()) == pilot.ONBOARDING_STEP_ORDER
    assert all(value is False for value in state.values())
