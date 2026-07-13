"""Worker startup-state tests: disabled vs configuration_error vs enabled.

A Procfile entry alone does not create a running Railway service; the worker runs
as its own dedicated service (railway-ai-triage-worker.json). These tests pin the
three startup states so the disabled state is clearly observable and an enabled
-but-misconfigured worker fails loudly (non-zero exit) instead of idling.
"""
from __future__ import annotations

import logging

from services.api.app import ai_triage
from services.api.app import run_ai_triage_worker as worker

# A syntactically-valid but non-routable Postgres URL. NOT a real credential — it
# only exercises resolve_db_backend()'s "is this Postgres?" classification.
FAKE_PG_URL = 'postgresql://u:p@db.invalid.example:5432/app'


def _cfg(**over):
    base = {'enabled': False, 'provider': '', 'has_api_key': False, 'model': ''}
    base.update(over)
    return base


def _enabled_mock_env(monkeypatch):
    """Baseline env for an enabled mock worker; individual tests override DB/live vars."""
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'true')
    monkeypatch.setenv('AI_PROVIDER', 'mock')
    for var in ('AI_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'AI_MODEL_TRIAGE'):
        monkeypatch.delenv(var, raising=False)


def test_disabled_state_when_flag_off():
    state, detail = worker.resolve_startup_state(_cfg(enabled=False, provider='openai'))
    assert state == 'disabled'
    assert detail == []


def test_configuration_error_when_openai_without_key():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='openai', has_api_key=False, model='m'))
    assert state == 'configuration_error'
    assert detail


def test_configuration_error_when_openai_without_model():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='openai', has_api_key=True, model=''))
    assert state == 'configuration_error'
    assert detail


def test_configuration_error_when_unknown_provider():
    state, _ = worker.resolve_startup_state(_cfg(enabled=True, provider='mystery', has_api_key=True, model='m'))
    assert state == 'configuration_error'


def test_enabled_state_when_openai_fully_configured():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='openai', has_api_key=True, model='gpt-x'))
    assert state == 'enabled'
    assert detail == []


def test_enabled_state_with_mock_needs_no_key():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='mock', has_api_key=False, model=''))
    assert state == 'enabled'
    assert detail == []


def test_worker_once_disabled_does_not_crash(monkeypatch):
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'false')
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])
    assert worker.main() == 0


def test_worker_exits_nonzero_on_configuration_error(monkeypatch):
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'true')
    monkeypatch.setenv('AI_PROVIDER', 'openai')
    monkeypatch.delenv('AI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setenv('AI_MODEL_TRIAGE', '')
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])
    assert worker.main() == 1


# --------------------------------------------------------------------------
# Database / live-mode configuration validation (the pg_connection() root cause)
# --------------------------------------------------------------------------
def test_database_configuration_errors_flags_missing_database_url(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    errors = ai_triage.database_configuration_errors(_cfg(enabled=True, provider='mock'))
    assert errors
    assert any('DATABASE_URL' in e for e in errors)
    # Never leaks a connection string / credential — only the variable name.
    assert not any('://' in e for e in errors)


def test_database_configuration_errors_flags_missing_live_mode_flag(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', FAKE_PG_URL)
    monkeypatch.delenv('LIVE_MODE_ENABLED', raising=False)
    monkeypatch.delenv('APP_MODE', raising=False)
    errors = ai_triage.database_configuration_errors(_cfg(enabled=True, provider='mock'))
    assert errors
    assert any('LIVE_MODE_ENABLED' in e for e in errors)


def test_database_configuration_errors_empty_for_valid_mock(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', FAKE_PG_URL)
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    assert ai_triage.database_configuration_errors(_cfg(enabled=True, provider='mock')) == []


def test_database_configuration_errors_empty_when_disabled(monkeypatch):
    # A disabled worker needs no database, regardless of DATABASE_URL / live mode.
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert ai_triage.database_configuration_errors(_cfg(enabled=False)) == []


def test_worker_fails_startup_once_when_database_url_missing(monkeypatch, caplog):
    """Missing DATABASE_URL: fail startup ONCE, never enter the 5s cycle loop."""
    _enabled_mock_env(monkeypatch)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])

    called = {'count': 0}
    monkeypatch.setattr(ai_triage, 'run_ai_triage_worker_once', lambda *a, **k: called.__setitem__('count', called['count'] + 1))

    with caplog.at_level(logging.ERROR):
        rc = worker.main()

    assert rc == 1
    # The worker must NOT have run a single processing cycle (no endless loop).
    assert called['count'] == 0
    messages = [r.getMessage() for r in caplog.records]
    assert any('event=ai_triage_worker_configuration_error' in m and 'DATABASE_URL' in m for m in messages)
    assert not any('event=ai_triage_worker_cycle_failed' in m for m in messages)


def test_worker_fails_startup_when_live_mode_flag_missing(monkeypatch, caplog):
    _enabled_mock_env(monkeypatch)
    monkeypatch.setenv('DATABASE_URL', FAKE_PG_URL)
    monkeypatch.delenv('LIVE_MODE_ENABLED', raising=False)
    monkeypatch.delenv('APP_MODE', raising=False)
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])

    called = {'count': 0}
    monkeypatch.setattr(ai_triage, 'run_ai_triage_worker_once', lambda *a, **k: called.__setitem__('count', called['count'] + 1))

    with caplog.at_level(logging.ERROR):
        rc = worker.main()

    assert rc == 1
    assert called['count'] == 0
    assert any('LIVE_MODE_ENABLED' in r.getMessage() for r in caplog.records)


def test_worker_starts_with_valid_mock_configuration(monkeypatch, caplog):
    """Valid mock config: startup validation passes and the loop actually runs."""
    _enabled_mock_env(monkeypatch)
    monkeypatch.setenv('DATABASE_URL', FAKE_PG_URL)
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])

    called = {'count': 0}
    monkeypatch.setattr(ai_triage, 'run_ai_triage_worker_once', lambda *a, **k: (called.__setitem__('count', called['count'] + 1) or {'processed': 0}))

    with caplog.at_level(logging.INFO):
        rc = worker.main()

    assert rc == 0
    assert called['count'] == 1  # passed startup validation and ran one cycle
    messages = [r.getMessage() for r in caplog.records]
    assert any('event=ai_triage_worker_started' in m and 'state=enabled' in m and 'provider=mock' in m for m in messages)


def test_disabled_worker_stays_idle_and_healthy(monkeypatch, caplog):
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'false')
    monkeypatch.delenv('DATABASE_URL', raising=False)  # disabled needs no DB
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])
    with caplog.at_level(logging.INFO):
        rc = worker.main()
    assert rc == 0
    messages = [r.getMessage() for r in caplog.records]
    assert any('event=ai_triage_worker_started' in m and 'state=disabled' in m for m in messages)
    assert not any('event=ai_triage_worker_configuration_error' in m for m in messages)
