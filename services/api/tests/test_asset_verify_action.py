from __future__ import annotations

from contextlib import contextmanager

from services.api.app import pilot


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Conn:
    def __init__(self):
        self.target_created = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'SELECT * FROM assets WHERE id =' in q and 'deleted_at IS NULL' in q:
            return _Rows([{'id': 'a1', 'workspace_id': 'ws1', 'name': 'A', 'identifier': '0x' + '1' * 40, 'chain_network': 'ethereum-mainnet', 'asset_type': 'smart-contract'}])
        if 'SELECT id FROM targets' in q:
            return _Rows([])
        if 'INSERT INTO targets' in q:
            self.target_created = True
            return _Rows([])
        if 'SELECT * FROM targets WHERE id =' in q:
            return _Rows([{'id': 't1', 'asset_id': 'a1', 'enabled': True, 'monitoring_enabled': True}])
        if 'SELECT * FROM monitored_systems WHERE id =' in q:
            return _Rows([{'id': 'ms1', 'asset_id': 'a1', 'target_id': 't1', 'is_enabled': True}])
        if 'SELECT * FROM assets WHERE id = %s::uuid' in q:
            return _Rows([{'id': 'a1', 'verification_status': 'verified'}])
        return _Rows([])

    def commit(self):
        return None


@contextmanager
def _pg(conn):
    yield conn


class _Req:
    headers = {'authorization': 'Bearer t', 'x-workspace-id': 'ws1'}


def test_verify_asset_updates_verification_and_creates_target(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'u1'}, {'workspace_id': 'ws1', 'workspace': {'id': 'ws1'}}))
    monkeypatch.setattr(pilot, '_derive_asset_verification', lambda **_k: {'normalized_identifier': '0x' + '1' * 40, 'verification_status': 'verified', 'verification_summary': {'reachable': True}})
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *_a, **_k: {'status': 'ok', 'monitored_system_id': 'ms1'})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    payload = pilot.verify_asset('a1', _Req())

    assert payload['verification']['verification_status'] == 'verified'
    assert payload['target'] is not None
    assert payload['monitored_system'] is not None
    assert conn.target_created is True
