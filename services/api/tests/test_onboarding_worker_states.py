"""Startup-state tests for the dedicated Onboarding Agent worker.

A Procfile entry alone does not create a running Railway service; the worker runs
as its own dedicated service (railway-onboarding-worker.json). These tests pin the
two startup states so a misconfigured worker fails loudly (non-zero exit) instead of
idling on 503s, and a correctly configured worker logs the
``onboarding_worker_registered=true`` marker that distinguishes worker health from
API health.

They also guard the entry point that the new Railway service's startCommand
(``python -m services.api.app.run_onboarding_worker``) depends on: the module must
import cleanly and its ``--once`` path must be exercisable without a real DB/network.
"""
from __future__ import annotations

import logging

from services.api.app import onboarding_agent
from services.api.app import run_onboarding_worker as worker

# A syntactically-valid but non-routable Postgres URL. NOT a real credential — it only
# exercises resolve_db_backend()'s "is this Postgres?" classification for live mode.
FAKE_PG_URL = 'postgresql://u:p@db.invalid.example:5432/app'


def _live_env(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', FAKE_PG_URL)
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')


# --------------------------------------------------------------------------
# Database / live-mode configuration validation (the pg_connection() root cause)
# --------------------------------------------------------------------------
def test_config_errors_flag_missing_database_url(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    errors = worker.database_configuration_errors()
    assert errors
    assert any('DATABASE_URL' in e for e in errors)
    # Never leaks a connection string / credential — only the variable name.
    assert not any('://' in e for e in errors)


def test_config_errors_flag_disabled_live_mode(monkeypatch):
    # Postgres present but live mode not enabled -> not authoritative for production.
    monkeypatch.setenv('DATABASE_URL', FAKE_PG_URL)
    monkeypatch.delenv('LIVE_MODE_ENABLED', raising=False)
    monkeypatch.delenv('APP_MODE', raising=False)
    errors = worker.database_configuration_errors()
    assert errors
    assert any('live' in e.lower() for e in errors)


def test_config_errors_empty_for_valid_live_config(monkeypatch):
    _live_env(monkeypatch)
    assert worker.database_configuration_errors() == []


def test_worker_exits_nonzero_on_configuration_error(monkeypatch, caplog):
    """Missing DATABASE_URL: fail startup ONCE, never enter the polling loop."""
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr('sys.argv', ['run_onboarding_worker', '--once'])

    called = {'count': 0}
    monkeypatch.setattr(onboarding_agent, 'claim_and_run_once',
                        lambda *a, **k: called.__setitem__('count', called['count'] + 1) or {'processed': 0})

    with caplog.at_level(logging.ERROR):
        rc = worker.main()

    assert rc == 1
    # The worker must NOT have claimed a single run (no endless loop on a broken config).
    assert called['count'] == 0
    messages = [r.getMessage() for r in caplog.records]
    assert any('event=onboarding_worker_configuration_error' in m and 'DATABASE_URL' in m for m in messages)


def test_worker_registers_and_runs_one_cycle_when_configured(monkeypatch, caplog):
    """Valid live config: startup validation passes, the registered marker is logged,
    and exactly one claim cycle runs under --once."""
    _live_env(monkeypatch)
    monkeypatch.setattr('sys.argv', ['run_onboarding_worker', '--once'])

    called = {'count': 0}
    monkeypatch.setattr(onboarding_agent, 'claim_and_run_once',
                        lambda *a, **k: (called.__setitem__('count', called['count'] + 1) or {'processed': 0}))

    with caplog.at_level(logging.INFO):
        rc = worker.main()

    assert rc == 0
    assert called['count'] == 1  # passed startup validation and ran one cycle
    messages = [r.getMessage() for r in caplog.records]
    assert any('event=onboarding_worker_started' in m and 'onboarding_worker_registered=true' in m
               for m in messages)


def test_worker_cycle_failure_does_not_crash_loop(monkeypatch, caplog):
    """A claim/processing exception is logged but never crashes the worker process."""
    _live_env(monkeypatch)
    monkeypatch.setattr('sys.argv', ['run_onboarding_worker', '--once'])

    def _boom(*_a, **_k):
        raise RuntimeError('transient claim failure')

    monkeypatch.setattr(onboarding_agent, 'claim_and_run_once', _boom)

    with caplog.at_level(logging.ERROR):
        rc = worker.main()

    assert rc == 0  # --once still returns cleanly; the platform keeps the service alive
    assert any('event=onboarding_worker_cycle_failed' in r.getMessage() for r in caplog.records)
