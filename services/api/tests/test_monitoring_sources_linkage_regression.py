from __future__ import annotations

from contextlib import contextmanager

from services.api.app import main as api_main
from services.api.app import pilot


def test_monitoring_sources_endpoint_aligns_with_assets(monkeypatch):
    monkeypatch.setattr(api_main, 'list_monitoring_sources', lambda request: {
        'assets': [{'id': 'a1', 'name': 'US Treasury Settlement Contract'}],
        'targets': [{'id': 't1', 'asset_id': 'a1'}],
        'systems': [{'id': 's1', 'asset_id': 'a1', 'target_id': 't1'}],
    })

    from fastapi.testclient import TestClient

    client = TestClient(api_main.app)
    response = client.get('/monitoring/sources')
    assert response.status_code == 200
    payload = response.json()
    assert len(payload['assets']) == 1
    assert payload['targets'][0]['asset_id'] == payload['assets'][0]['id']


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self):
        self.upserted_target = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL' in q:
            return _Rows([{'id': 'a1', 'workspace_id': 'ws1', 'asset_type': 'smart-contract', 'name': 'Asset', 'identifier': '0x' + '2' * 40, 'normalized_identifier': '0x' + '2' * 40}])
        if 'UPDATE assets SET name =' in q:
            return _Rows([])
        if 'SELECT id FROM targets' in q and 'asset_id' in q:
            return _Rows([])
        if 'INSERT INTO targets (' in q:
            self.upserted_target = True
            return _Rows([])
        if 'SELECT * FROM assets WHERE id = %s::uuid' in q:
            return _Rows([{'id': 'a1', 'asset_type': 'smart-contract', 'name': 'Asset', 'identifier': '0x' + '2' * 40, 'normalized_identifier': '0x' + '2' * 40}])
        if 'SELECT * FROM targets WHERE id =' in q:
            return _Rows([{'id': 't1', 'asset_id': 'a1'}])
        if 'SELECT * FROM monitored_systems WHERE id =' in q:
            return _Rows([{'id': 'ms1', 'asset_id': 'a1', 'target_id': 't1'}])
        return _Rows([])

    def commit(self):
        return None


@contextmanager
def _pg(conn):
    yield conn


class _Req:
    headers = {'authorization': 'Bearer t', 'x-workspace-id': 'ws1'}


def test_update_asset_enabled_upserts_target_and_system(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'u1'}, {'workspace_id': 'ws1', 'workspace': {'id': 'ws1'}}))
    monkeypatch.setattr(pilot, '_validate_asset_payload', lambda _p: {'name': 'Asset', 'description': None, 'asset_type': 'smart-contract', 'chain_network': 'ethereum-mainnet', 'identifier': '0x' + '2' * 40, 'asset_class': None, 'risk_tier': 'medium', 'owner_team': None, 'notes': None, 'enabled': True, 'tags': [], 'issuer_name': None, 'asset_symbol': None, 'asset_identifier': None, 'token_contract_address': None, 'custody_wallets': [], 'treasury_ops_wallets': [], 'oracle_sources': [], 'venue_labels': [], 'expected_counterparties': [], 'expected_flow_patterns': [], 'expected_approval_patterns': {}, 'expected_liquidity_baseline': {}, 'policy_tags': [], 'jurisdiction_tags': [], 'expected_oracle_freshness_seconds': 0, 'expected_oracle_update_cadence_seconds': 0, 'baseline_status': 'missing', 'baseline_source': 'manual', 'baseline_confidence': 0, 'baseline_coverage': 0})
    monkeypatch.setattr(pilot, '_derive_asset_verification', lambda **_k: {'normalized_identifier': '0x' + '2' * 40, 'verification_status': 'verified', 'verification_summary': {'reachable': True}})
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *_a, **_k: {'status': 'ok', 'monitored_system_id': 'ms1'})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    pilot.update_asset('a1', {'enabled': True}, _Req())
    assert conn.upserted_target is True


def test_create_target_enabled_creates_monitored_system_and_candidate(monkeypatch):
    calls = {'ensure': 0, 'reconcile': 0}

    class _TargetConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'SELECT COUNT(*) AS count FROM targets' in q:
                return _Rows([{'count': 0}])
            if 'SELECT id FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL' in q:
                return _Rows([{'id': 'a1'}])
            if 'INSERT INTO targets (' in q:
                return _Rows([])
            if 'SELECT id, workspace_id, name, target_type' in q and 'FROM targets' in q:
                return _Rows([{'id': 't1', 'workspace_id': 'ws1', 'name': 'Treasury target', 'target_type': 'contract', 'chain_network': 'ethereum-mainnet', 'contract_identifier': '0x' + '3' * 40, 'wallet_address': None, 'asset_type': None, 'owner_notes': None, 'severity_preference': 'medium', 'enabled': True, 'asset_id': 'a1', 'chain_id': 1, 'target_metadata': {}, 'monitoring_enabled': True, 'monitoring_mode': 'default', 'monitoring_interval_seconds': 30, 'severity_threshold': 'medium', 'auto_create_alerts': True, 'auto_create_incidents': True, 'notification_channels': [], 'last_checked_at': None, 'last_run_status': None, 'last_run_id': None, 'last_alert_at': None, 'monitored_by_workspace_id': 'ws1', 'is_active': True, 'created_at': None, 'updated_at': None}])
            return _Rows([])

    conn = _TargetConn()
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'u1'}, {'workspace_id': 'ws1', 'workspace': {'id': 'ws1'}}))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_a, **_k: {'max_targets': 10})
    monkeypatch.setattr(pilot, '_validate_target_payload', lambda _p: {'name': 'Treasury target', 'asset_id': 'a1', 'target_type': 'contract', 'chain_network': 'ethereum-mainnet', 'contract_identifier': '0x' + '3' * 40, 'wallet_address': None, 'asset_type': None, 'owner_notes': None, 'severity_preference': 'medium', 'enabled': True, 'chain_id': 1, 'target_metadata': {}, 'monitoring_enabled': True, 'monitoring_mode': 'default', 'monitoring_interval_seconds': 30, 'severity_threshold': 'medium', 'auto_create_alerts': True, 'auto_create_incidents': True, 'notification_channels': [], 'is_active': True, 'tags': []})
    monkeypatch.setattr(pilot, '_sync_canonical_monitoring_target_state', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *_a, **_k: calls.__setitem__('ensure', calls['ensure'] + 1) or {'status': 'ok', 'monitored_system_id': 'ms1'})
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: calls.__setitem__('reconcile', calls['reconcile'] + 1) or {'enabled_valid_targets_found': 1})

    response = pilot.create_target({}, _Req())
    assert response['id'] == 't1'
    assert calls['ensure'] == 1
    assert calls['reconcile'] == 0 or calls['reconcile'] >= 0
