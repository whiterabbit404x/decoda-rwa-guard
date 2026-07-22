"""Canary target-allowlist resolution + scanner-limits/canary startup logs.

The first production canary is scoped to a SINGLE allowlisted target (Datto) via env
configuration only — no target id is hard-coded into production logic. These tests lock
the allowlist semantics (task test 6), the "normal mode when disabled" behaviour (task
test 12), the fail-closed empty-allowlist case, and the greppable startup log lines that
prove the resolved scanner limits and canary posture without leaking secrets.
"""
from __future__ import annotations

import pytest

from services.api.app import evm_activity_provider as eap
from services.api.app.monitoring_canary import (
    CANARY_ENABLED_ENV,
    CANARY_TARGET_ALLOWLIST_ENV,
    canary_mode_enabled,
    log_canary_mode_resolved,
    resolve_canary_config,
)

DATTO = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
RABBIT = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'


def _clear(monkeypatch) -> None:
    monkeypatch.delenv(CANARY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(CANARY_TARGET_ALLOWLIST_ENV, raising=False)


# --- Allowlist semantics ---------------------------------------------------
def test_allowlist_excludes_other_targets(monkeypatch):
    """Task test 6: with only Datto allowlisted, every other target is excluded."""
    _clear(monkeypatch)
    monkeypatch.setenv(CANARY_TARGET_ALLOWLIST_ENV, DATTO)
    cfg = resolve_canary_config()
    assert cfg.enabled is True
    assert cfg.allowed_target_count == 1
    assert cfg.is_target_allowed(DATTO) is True
    assert cfg.is_target_allowed(RABBIT) is False
    # Case- and whitespace-insensitive so a copy/pasted id still matches.
    assert cfg.is_target_allowed(f'  {DATTO.upper()}  ') is True


def test_canary_disabled_allows_all_targets(monkeypatch):
    """Task test 12: with canary mode off, normal production polls every target."""
    _clear(monkeypatch)
    assert canary_mode_enabled() is False
    cfg = resolve_canary_config()
    assert cfg.is_target_allowed(DATTO) is True
    assert cfg.is_target_allowed(RABBIT) is True


def test_canary_enabled_empty_allowlist_polls_nothing(monkeypatch):
    """Fail-closed: canary ON with no allowlist polls nothing (never the full fleet)."""
    _clear(monkeypatch)
    monkeypatch.setenv(CANARY_ENABLED_ENV, 'true')
    cfg = resolve_canary_config()
    assert cfg.enabled is True
    assert cfg.allowed_target_count == 0
    assert cfg.is_target_allowed(DATTO) is False


def test_multi_target_allowlist(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(CANARY_TARGET_ALLOWLIST_ENV, f'{DATTO}, {RABBIT}')
    cfg = resolve_canary_config()
    assert cfg.allowed_target_count == 2
    assert cfg.is_target_allowed(DATTO) is True
    assert cfg.is_target_allowed(RABBIT) is True


# --- Startup logs ----------------------------------------------------------
def test_canary_mode_resolved_log_line(monkeypatch, caplog):
    _clear(monkeypatch)
    monkeypatch.setenv(CANARY_TARGET_ALLOWLIST_ENV, DATTO)
    with caplog.at_level('INFO'):
        log_canary_mode_resolved()
    assert 'event=monitoring_canary_mode_resolved enabled=true allowed_target_count=1' in caplog.text
    # No target ids in the line — only the count (workspace data is never logged).
    assert DATTO not in caplog.text


def test_scanner_limits_startup_log_shows_defaults_without_secrets(monkeypatch, caplog):
    for name in (
        'MAX_BLOCKS_PER_TARGET_PER_CYCLE', 'MAX_RPC_CALLS_PER_TARGET_PER_CYCLE',
        'MAX_TX_ENRICHMENTS_PER_TARGET_PER_CYCLE', 'MAX_LOGS_PER_TARGET_PER_CYCLE',
        'MAX_POLL_DURATION_SECONDS', 'HISTORICAL_BACKFILL_ENABLED',
        'MONITORING_RPC_MAX_CALLS_PER_MINUTE', 'INITIAL_LIVE_TAIL_BLOCKS',
    ):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level('INFO'):
        eap.log_scanner_limits_resolved()
    text = caplog.text
    assert 'event=monitoring_scanner_limits_resolved' in text
    assert 'max_blocks_per_target_per_cycle=25' in text
    assert 'max_logs_per_target_per_cycle=2000' in text
    assert 'max_tx_enrichments_per_target_per_cycle=25' in text
    assert 'max_rpc_calls_per_target_per_cycle=100' in text
    assert 'max_poll_duration_seconds=45' in text
    assert 'historical_backfill_enabled=false' in text
    # No secrets: never an RPC URL, host, or key in the limits line.
    assert 'http' not in text.lower()
    assert 'rpc_url' not in text.lower()


def test_canary_limits_startup_log(monkeypatch, caplog):
    monkeypatch.setenv('MAX_BLOCKS_PER_TARGET_PER_CYCLE', '5')
    monkeypatch.setenv('MAX_RPC_CALLS_PER_TARGET_PER_CYCLE', '30')
    monkeypatch.setenv('MAX_TX_ENRICHMENTS_PER_TARGET_PER_CYCLE', '5')
    with caplog.at_level('INFO'):
        limits = eap.log_scanner_limits_resolved()
    assert limits['max_blocks_per_target_per_cycle'] == 5
    assert 'max_blocks_per_target_per_cycle=5 ' in caplog.text
    assert 'max_rpc_calls_per_target_per_cycle=30 ' in caplog.text


# --- terminal-status vocabulary -------------------------------------------
def test_normalize_terminal_status_skip_is_not_completed():
    assert eap._normalize_terminal_status('complete') == 'completed'
    assert eap._normalize_terminal_status('partial') == 'partial'
    assert eap._normalize_terminal_status('degraded') == 'failed'
    assert eap._normalize_terminal_status('skipped') == 'skipped'
    assert eap._normalize_terminal_status('skipped') != 'completed'
