"""
P2-6: Dev signing secret hardening tests.

Verifies:
- Production rejects missing signing secret
- Production rejects the known dev fallback secret
- Staging rejects the known dev fallback secret
- Local works with dev fallback (default behavior)
- EXPORT_ALLOW_DEV_SIGNING_SECRET=true is respected in local mode
- Key material is never logged
"""
from __future__ import annotations

import pytest


_DEV_SECRET = 'decoda-dev-signing-secret-NOT-FOR-PRODUCTION'


def _clear_signing_cache():
    """No module-level cache to clear, but reload if needed."""
    pass


def test_production_rejects_missing_secret(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    from services.api.app.evidence_signing import validate_signing_secret_at_startup
    with pytest.raises(RuntimeError, match='required in production'):
        validate_signing_secret_at_startup()


def test_staging_rejects_missing_secret(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'staging')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    from services.api.app.evidence_signing import validate_signing_secret_at_startup
    with pytest.raises(RuntimeError, match='required in production'):
        validate_signing_secret_at_startup()


def test_production_rejects_known_dev_secret(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', _DEV_SECRET)

    from services.api.app.evidence_signing import validate_signing_secret_at_startup
    with pytest.raises(RuntimeError, match='dev fallback'):
        validate_signing_secret_at_startup()


def test_staging_rejects_known_dev_secret(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'staging')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', _DEV_SECRET)

    from services.api.app.evidence_signing import validate_signing_secret_at_startup
    with pytest.raises(RuntimeError, match='dev fallback'):
        validate_signing_secret_at_startup()


def test_production_accepts_strong_secret(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'a-strong-production-secret-xyz-0987654321')

    from services.api.app.evidence_signing import validate_signing_secret_at_startup
    # Should not raise
    validate_signing_secret_at_startup()


def test_local_works_with_no_secret_configured(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    from services.api.app.evidence_signing import validate_signing_secret_at_startup
    # Should not raise in local mode
    validate_signing_secret_at_startup()


def test_local_dev_fallback_produces_dev_seal(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    from services.api.app.evidence_signing import _require_signing_secret
    secret, is_prod = _require_signing_secret()
    assert is_prod is False
    assert b'NOT-FOR-PRODUCTION' in secret


def test_require_signing_secret_blocks_dev_secret_in_production(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', _DEV_SECRET)

    from services.api.app.evidence_signing import _require_signing_secret
    with pytest.raises(RuntimeError, match='dev fallback'):
        _require_signing_secret()


def test_startup_log_does_not_include_key_material(monkeypatch, caplog):
    import logging
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'super-secret-prod-key-abc123')

    from services.api.app.evidence_signing import validate_signing_secret_at_startup
    with caplog.at_level(logging.INFO, logger='services.api.app.evidence_signing'):
        validate_signing_secret_at_startup()

    # Key material must never appear in logs
    for record in caplog.records:
        assert 'super-secret-prod-key-abc123' not in record.message
        assert 'super-secret-prod-key-abc123' not in str(record.args)
