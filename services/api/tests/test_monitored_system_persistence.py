from __future__ import annotations

from contextlib import contextmanager

from services.api.app import pilot


class _Result:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self):
        self.system = {
            'id': 'sys-1',
            'workspace_id': 'ws-1',
            'asset_id': 'asset-1',
            'target_id': 'target-1',
            'chain': 'ethereum-mainnet',
            'is_enabled': False,
            'runtime_status': 'disabled',
            'freshness_status': 'unavailable',
            'confidence_status': 'unavailable',
            'status': 'paused',
            'last_heartbeat': None,
            'last_event_at': None,
            'last_error_text': None,
            'coverage_reason': 'monitoring_disabled',
            'created_at': '2026-04-08T00:00:00+00:00',
            'asset_name': 'Treasury',
            'target_name': 'Hot wallet',
        }

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if q.startswith('SELECT id, is_enabled, runtime_status, freshness_status, confidence_status FROM monitored_systems'):
            return _Result(row={'id': self.system['id'], 'is_enabled': self.system['is_enabled'], 'runtime_status': self.system['runtime_status'], 'freshness_status': self.system['freshness_status'], 'confidence_status': self.system['confidence_status']})
        if q.startswith('UPDATE monitored_systems SET is_enabled'):
            self.system['is_enabled'] = bool(params[0])
            self.system['runtime_status'] = str(params[1])
            self.system['status'] = str(params[2])
            self.system['freshness_status'] = str(params[3])
            self.system['confidence_status'] = str(params[4])
            return _Result()
        if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
            return _Result(rows=[dict(self.system)])
        if q.startswith('SELECT id, workspace_id, asset_id, target_id, chain, is_enabled, runtime_status, status, freshness_status, confidence_status'):
            return _Result(row=dict(self.system))
        return _Result()

    def commit(self):
        return None


@contextmanager
def _fake_pg(conn):
    yield conn


def _fake_request():
    class _Request:
        headers = {}

    return _Request()


def test_enable_disable_persists_and_reloads(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    enabled = pilot.patch_monitored_system('sys-1', {'enabled': True}, _fake_request())
    assert enabled['system']['is_enabled'] is True

    listing = pilot.list_monitored_systems(_fake_request())
    assert listing['systems'][0]['is_enabled'] is True

    disabled = pilot.patch_monitored_system('sys-1', {'enabled': False}, _fake_request())
    assert disabled['system']['is_enabled'] is False

    listing_again = pilot.list_monitored_systems(_fake_request())
    assert listing_again['systems'][0]['is_enabled'] is False


def test_runtime_error_update_does_not_disable(monkeypatch):
    conn = _Conn()
    conn.system['is_enabled'] = True
    conn.system['runtime_status'] = 'healthy'
    conn.system['freshness_status'] = 'fresh'
    conn.system['confidence_status'] = 'medium'
    conn.system['status'] = 'active'

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    updated = pilot.patch_monitored_system('sys-1', {'runtime_status': 'failed'}, _fake_request())
    assert updated['system']['runtime_status'] == 'failed'
    assert updated['system']['is_enabled'] is True
