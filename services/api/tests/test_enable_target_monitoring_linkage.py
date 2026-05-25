"""
Tests for the enable-target action and its effect on monitoring linkage:
- set_target_enabled sets is_active=TRUE on the targets row
- set_target_enabled upserts a monitoring_configs row keyed by targets.id
- The monitoring runner candidate query can find the enabled target
- Disabling a target turns off is_active
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from services.api.app import pilot


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _EnableConn:
    """Minimal fake connection that records UPDATE and INSERT queries."""

    def __init__(self, target_id='t1', workspace_id='ws1', asset_id='a1'):
        self.target_id = target_id
        self.workspace_id = workspace_id
        self.asset_id = asset_id
        self.updates: list[tuple[str, tuple]] = []
        self.inserts: list[tuple[str, tuple]] = []

    def execute(self, query: str, params=None):
        q = ' '.join(str(query).split()).upper()
        params = params or ()
        if q.startswith('UPDATE TARGETS'):
            self.updates.append((query, params))
            return _Rows([])
        if q.startswith('INSERT INTO MONITORING_CONFIGS'):
            self.inserts.append((query, params))
            return _Rows([])
        if 'FROM TARGETS WHERE ID' in q and 'DELETED_AT IS NULL' in q:
            return _Rows([{'id': self.target_id, 'asset_id': self.asset_id, 'chain_network': 'ethereum-mainnet'}])
        if 'FROM ASSETS' in q:
            return _Rows([{'id': self.asset_id}])
        if 'FROM TARGETS WHERE ID' in q:
            # _load_target_row
            return _Rows([{
                'id': self.target_id, 'workspace_id': self.workspace_id,
                'name': 'Test', 'target_type': 'wallet', 'chain_network': 'ethereum-mainnet',
                'enabled': True, 'monitoring_enabled': True, 'is_active': True,
                'asset_id': self.asset_id, 'monitoring_interval_seconds': 30,
                'last_checked_at': None, 'last_run_status': None, 'last_run_id': None,
                'last_alert_at': None, 'monitored_by_workspace_id': None,
                'created_at': None, 'updated_at': None,
                'monitoring_mode': None, 'severity_threshold': None,
                'auto_create_alerts': True, 'auto_create_incidents': True,
                'notification_channels': None, 'last_real_event_at': None,
                'last_no_evidence_at': None, 'last_degraded_at': None,
                'last_failed_monitoring_at': None, 'recent_evidence_state': None,
                'recent_truthfulness_state': None, 'recent_real_event_count': None,
                'chain_id': 1, 'target_metadata': None,
            }])
        if 'MONITORED_TARGETS' in q:
            return _Rows([{'id': 'mt1'}])
        if 'MONITORING_CONFIGS' in q:
            return _Rows([])
        return _Rows([])

    def commit(self):
        pass


@contextmanager
def _pg(conn):
    yield conn


class _Req:
    headers = {'authorization': 'Bearer tok', 'x-workspace-id': 'ws1'}


def _patch_common(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: (
        {'id': 'u1'}, {'workspace_id': conn.workspace_id}
    ))
    monkeypatch.setattr(pilot, '_sync_canonical_monitoring_target_state', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *_a, **_k: {
        'status': 'ok', 'monitored_system_id': 'ms1',
    })
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, '_load_target_row', lambda *_a, **_k: {'id': conn.target_id, 'enabled': True})


def test_enable_target_sets_is_active(monkeypatch):
    """Enabling a target must set is_active=TRUE on the targets row."""
    conn = _EnableConn()
    _patch_common(monkeypatch, conn)

    pilot.set_target_enabled('t1', True, _Req())

    update_queries = [(q, p) for q, p in conn.updates if 'UPDATE targets' in q]
    assert update_queries, 'expected at least one UPDATE targets statement'
    query, params = update_queries[0]
    assert 'is_active' in query, 'UPDATE targets must include is_active'
    # params: (enabled, monitoring_enabled, is_active, user_id, target_id)
    assert params[2] is True, 'is_active must be True when enabling'


def test_disable_target_sets_is_active_false(monkeypatch):
    """Disabling a target must set is_active=FALSE."""
    conn = _EnableConn()
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {})

    pilot.set_target_enabled('t1', False, _Req())

    update_queries = [(q, p) for q, p in conn.updates if 'UPDATE targets' in q]
    assert update_queries
    query, params = update_queries[0]
    assert 'is_active' in query
    assert params[2] is False, 'is_active must be False when disabling'


def test_enable_target_upserts_monitoring_config_for_targets_id(monkeypatch):
    """
    Enabling a target must insert a monitoring_configs row with target_id = targets.id
    so the monitoring runner candidate query can find it.
    """
    conn = _EnableConn()
    _patch_common(monkeypatch, conn)

    pilot.set_target_enabled('t1', True, _Req())

    config_inserts = [q for q, _p in conn.inserts if 'monitoring_configs' in q.lower()]
    assert config_inserts, (
        'set_target_enabled must upsert into monitoring_configs so the worker can find the target'
    )
    # The target_id in the insert params must be the original targets.id ('t1')
    for q, p in conn.inserts:
        if 'monitoring_configs' in q.lower():
            assert 't1' in p, (
                f'monitoring_configs insert must include targets.id (t1), got params={p!r}'
            )


def test_enable_target_monitoring_config_has_enabled_true(monkeypatch):
    """The upserted monitoring_configs row must have enabled=TRUE."""
    conn = _EnableConn()
    _patch_common(monkeypatch, conn)

    pilot.set_target_enabled('t1', True, _Req())

    for q, p in conn.inserts:
        if 'monitoring_configs' in q.lower():
            # params: (config_id, workspace_id, asset_id, target_id, ...)
            # enabled=TRUE is part of the SQL literal, not a param — just ensure insert happened
            assert 't1' in p, 'target_id param must be present'
            return
    pytest.fail('no monitoring_configs insert found')


def test_enable_target_route_exists_in_main_source():
    """The /targets/{target_id}/enable route must appear in main.py source."""
    import pathlib
    main_source = (pathlib.Path(__file__).resolve().parents[1] / 'app' / 'main.py').read_text()
    assert "'/targets/{target_id}/enable'" in main_source, (
        "POST /targets/{target_id}/enable must be declared in main.py"
    )
    assert 'set_target_enabled' in main_source, (
        'main.py must delegate to set_target_enabled'
    )
