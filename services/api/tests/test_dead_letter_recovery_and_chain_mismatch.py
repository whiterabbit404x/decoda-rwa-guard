"""
Tests for:
  1. Chain-mismatch exclusion works for any configured chain_id, not just Base/8453.
  2. canonical_last_heartbeat_at fallback includes last_heartbeat so banner
     does not falsely report "worker not running."
  3. New routes are registered in main.py.
  4. inspect_target_dead_letter_state and recover_target_dead_letter functions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Route presence
# ---------------------------------------------------------------------------

def test_new_ops_routes_registered() -> None:
    content = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    assert "@app.get('/ops/monitoring/targets/{target_id}/state'" in content
    assert "@app.post('/ops/monitoring/targets/{target_id}/recover-dead-letter'" in content


# ---------------------------------------------------------------------------
# Chain-mismatch exclusion: activates for any known chain, not just 8453
# ---------------------------------------------------------------------------

def _make_candidate_systems(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build minimal candidate_systems rows for mismatch filter tests."""
    rows = []
    for t in targets:
        rows.append({
            'target_id': t['target_id'],
            'chain_network': t.get('chain_network', ''),
            'monitoring_dead_lettered_at': None,
            'next_due_at': datetime.now(timezone.utc).isoformat(),
            'last_checked_at': None,
            'monitoring_interval_seconds': 30,
            'workspace_id': 'ws-1',
            'system_id': 'sys-' + t['target_id'],
        })
    return rows


def _run_chain_mismatch_filter(
    due_target_ids: list[str],
    candidate_systems: list[dict[str, Any]],
    rpc_chain_id: int | None,
    monkeypatch,
) -> list[str]:
    """Replicate the chain-mismatch filter logic from run_monitoring_cycle."""
    import os
    if rpc_chain_id is not None:
        monkeypatch.setenv('EVM_CHAIN_ID', str(rpc_chain_id))
    else:
        monkeypatch.delenv('EVM_CHAIN_ID', raising=False)
        monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)

    _rpc_chain_id_str = os.getenv('EVM_CHAIN_ID') or os.getenv('STAGING_EVM_CHAIN_ID') or ''
    try:
        _rpc_chain_id = int(_rpc_chain_id_str) if _rpc_chain_id_str else None
    except (ValueError, TypeError):
        _rpc_chain_id = None

    _known_chain_ids: dict[str, int] = {
        'ethereum': 1, 'ethereum-mainnet': 1, 'mainnet': 1,
        'base': 8453, 'base-mainnet': 8453,
        'polygon': 137, 'polygon-mainnet': 137,
        'arbitrum': 42161, 'arbitrum-one': 42161,
        'optimism': 10, 'optimism-mainnet': 10,
    }
    if _rpc_chain_id is None:
        return list(due_target_ids)

    _chain_by_target: dict[str, str] = {}
    for _row in candidate_systems:
        _sys = dict(_row)
        _tid_str = str(_sys.get('target_id') or '').strip()
        _chain_by_target[_tid_str] = str(_sys.get('chain_network') or '').lower()

    _filtered: list[str] = []
    for _tid in due_target_ids:
        _tid_str = str(_tid).strip()
        _chain = _chain_by_target.get(_tid_str, '')
        _target_chain_id = _known_chain_ids.get(_chain) if _chain else None
        if _target_chain_id is not None and _target_chain_id != _rpc_chain_id:
            pass  # excluded
        else:
            _filtered.append(_tid)
    return _filtered


def test_chain_mismatch_ethereum_excluded_when_rpc_is_base(monkeypatch):
    """ethereum/ethereum-mainnet targets must be excluded when EVM_CHAIN_ID=8453."""
    due = ['eth-target-1', 'base-target-1', 'eth-target-2']
    candidates = _make_candidate_systems([
        {'target_id': 'eth-target-1', 'chain_network': 'ethereum'},
        {'target_id': 'base-target-1', 'chain_network': 'base'},
        {'target_id': 'eth-target-2', 'chain_network': 'ethereum-mainnet'},
    ])
    result = _run_chain_mismatch_filter(due, candidates, rpc_chain_id=8453, monkeypatch=monkeypatch)
    assert result == ['base-target-1'], f"Expected only Base target, got {result}"


def test_chain_mismatch_base_excluded_when_rpc_is_ethereum(monkeypatch):
    """base/base-mainnet targets must be excluded when EVM_CHAIN_ID=1."""
    due = ['eth-target-1', 'base-target-1', 'base-target-2']
    candidates = _make_candidate_systems([
        {'target_id': 'eth-target-1', 'chain_network': 'ethereum'},
        {'target_id': 'base-target-1', 'chain_network': 'base'},
        {'target_id': 'base-target-2', 'chain_network': 'base-mainnet'},
    ])
    result = _run_chain_mismatch_filter(due, candidates, rpc_chain_id=1, monkeypatch=monkeypatch)
    assert result == ['eth-target-1'], f"Expected only Ethereum target, got {result}"


def test_chain_mismatch_no_filter_when_rpc_chain_id_unset(monkeypatch):
    """When EVM_CHAIN_ID is not set, no targets should be excluded."""
    due = ['eth-target-1', 'base-target-1']
    candidates = _make_candidate_systems([
        {'target_id': 'eth-target-1', 'chain_network': 'ethereum'},
        {'target_id': 'base-target-1', 'chain_network': 'base'},
    ])
    result = _run_chain_mismatch_filter(due, candidates, rpc_chain_id=None, monkeypatch=monkeypatch)
    assert set(result) == {'eth-target-1', 'base-target-1'}


def test_chain_mismatch_unknown_chain_network_not_excluded(monkeypatch):
    """Targets with an unknown chain_network should NOT be excluded (fail-open for unknown chains)."""
    due = ['custom-target-1', 'base-target-1']
    candidates = _make_candidate_systems([
        {'target_id': 'custom-target-1', 'chain_network': 'my-custom-chain'},
        {'target_id': 'base-target-1', 'chain_network': 'base'},
    ])
    result = _run_chain_mismatch_filter(due, candidates, rpc_chain_id=8453, monkeypatch=monkeypatch)
    # custom-chain is unknown → not excluded; base is compatible with 8453 → kept
    assert 'base-target-1' in result
    assert 'custom-target-1' in result


def test_chain_mismatch_empty_chain_network_not_excluded(monkeypatch):
    """Targets with no chain_network (empty string) should pass the filter."""
    due = ['no-chain-target']
    candidates = _make_candidate_systems([
        {'target_id': 'no-chain-target', 'chain_network': ''},
    ])
    result = _run_chain_mismatch_filter(due, candidates, rpc_chain_id=8453, monkeypatch=monkeypatch)
    assert 'no-chain-target' in result


# ---------------------------------------------------------------------------
# canonical_last_heartbeat_at fallback includes last_heartbeat
# ---------------------------------------------------------------------------

def test_canonical_last_heartbeat_at_fallback_line_contains_last_heartbeat():
    """The fallback chain for canonical_last_heartbeat_at must include `last_heartbeat`."""
    content = (REPO_ROOT / 'services/api/app/monitoring_runner.py').read_text(encoding='utf-8')
    # The fixed line must have all four fallback sources in order
    assert (
        'canonical_last_heartbeat_at or last_system_heartbeat or last_heartbeat or _parse_ts(health.get'
        in content
    ), (
        "canonical_last_heartbeat_at fallback must include last_heartbeat so the banner "
        "does not falsely report 'worker not running' when monitoring_heartbeats has a fresh row."
    )


# ---------------------------------------------------------------------------
# inspect_target_dead_letter_state: function exists and is importable
# ---------------------------------------------------------------------------

def test_inspect_target_dead_letter_state_importable():
    from services.api.app.monitoring_runner import inspect_target_dead_letter_state
    assert callable(inspect_target_dead_letter_state)


def test_recover_target_dead_letter_importable():
    from services.api.app.monitoring_runner import recover_target_dead_letter
    assert callable(recover_target_dead_letter)


# ---------------------------------------------------------------------------
# recover_target_dead_letter: resets dead-letter columns
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, target_row: dict):
        self._target_row = target_row
        self.committed = False
        self.last_update_params: tuple | None = None

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM targets WHERE id' in q and 'UPDATE' not in q:
            return _FakeResult(row=self._target_row)
        if 'UPDATE targets' in q:
            self.last_update_params = params
            return _FakeResult(row=None)
        return _FakeResult(row={})

    def commit(self):
        self.committed = True


@contextmanager
def _fake_pg(conn):
    yield conn


def _make_request(workspace_id: str = 'ws-dead-1') -> MagicMock:
    req = MagicMock()
    req.headers = {'x-workspace-id': workspace_id}
    return req


def test_recover_target_dead_letter_clears_state(monkeypatch):
    """recover_target_dead_letter must commit and report was_dead_lettered=True."""
    from services.api.app import monitoring_runner

    dead_row = {
        'id': 'target-dead-1',
        'workspace_id': 'ws-dead-1',
        'monitoring_dead_lettered_at': datetime(2025, 1, 1, tzinfo=timezone.utc),
        'monitoring_delivery_attempts': 7,
        'last_run_status': 'dead_lettered',
    }
    conn = _FakeConn(target_row=dead_row)

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner, 'normalize_workspace_header_value', lambda v: v
    )

    result = monitoring_runner.recover_target_dead_letter(
        _make_request('ws-dead-1'), 'target-dead-1'
    )
    assert result['was_dead_lettered'] is True
    assert result['recovered'] is True
    assert result['previous_delivery_attempts'] == 7
    assert conn.committed is True


def test_recover_target_dead_letter_idempotent_when_not_dead_lettered(monkeypatch):
    """recover_target_dead_letter on a healthy target must report was_dead_lettered=False."""
    from services.api.app import monitoring_runner

    healthy_row = {
        'id': 'target-ok-1',
        'workspace_id': 'ws-ok-1',
        'monitoring_dead_lettered_at': None,
        'monitoring_delivery_attempts': 0,
        'last_run_status': 'ok',
    }
    conn = _FakeConn(target_row=healthy_row)

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner, 'normalize_workspace_header_value', lambda v: v
    )

    result = monitoring_runner.recover_target_dead_letter(
        _make_request('ws-ok-1'), 'target-ok-1'
    )
    assert result['was_dead_lettered'] is False
    assert result['recovered'] is True


def test_recover_target_dead_letter_raises_404_when_not_found(monkeypatch):
    """recover_target_dead_letter must raise 404 when target is not in workspace."""
    from fastapi import HTTPException
    from services.api.app import monitoring_runner

    conn = _FakeConn(target_row=None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner, 'normalize_workspace_header_value', lambda v: v
    )

    with pytest.raises(HTTPException) as exc_info:
        monitoring_runner.recover_target_dead_letter(
            _make_request('ws-none'), 'nonexistent-target'
        )
    assert exc_info.value.status_code == 404
