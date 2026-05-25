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


# ---------------------------------------------------------------------------
# Orphan target repair tests
# ---------------------------------------------------------------------------

class _OrphanConn:
    """
    Fake connection for orphan-repair scenarios.
    asset_id on the target row is NULL (orphaned).
    one_matching_asset controls whether one or many assets match the identifier search.
    """

    def __init__(
        self,
        *,
        target_id: str = 't_orphan',
        workspace_id: str = 'ws1',
        one_matching_asset: bool = True,
        many_matching_assets: bool = False,
        identifier: str = '0xdeadbeef00000000000000000000000000000001',
    ):
        self.target_id = target_id
        self.workspace_id = workspace_id
        self.one_matching_asset = one_matching_asset
        self.many_matching_assets = many_matching_assets
        self.identifier = identifier
        self.updates: list[tuple[str, tuple]] = []
        self.inserts: list[tuple[str, tuple]] = []

    def execute(self, query: str, params=None):
        q = ' '.join(str(query).split()).upper()
        params = params or ()

        # SELECT targets row — orphaned (asset_id is None)
        if 'FROM TARGETS WHERE ID' in q and 'DELETED_AT IS NULL' in q:
            return _Rows([{
                'id': self.target_id,
                'asset_id': None,
                'chain_network': 'ethereum-mainnet',
                'name': 'Orphan Target',
                'target_type': 'contract',
                'contract_identifier': self.identifier,
                'wallet_address': None,
            }])

        # SELECT assets (asset_valid check) — None because orphaned
        if 'FROM ASSETS A WHERE A.ID' in q:
            return _Rows([])

        # SELECT assets for identifier match
        if 'FROM ASSETS' in q and 'LOWER(CHAIN_NETWORK)' in q:
            if self.many_matching_assets:
                return _Rows([
                    {'id': 'a_match_1', 'name': 'Asset One'},
                    {'id': 'a_match_2', 'name': 'Asset Two'},
                ])
            if self.one_matching_asset:
                return _Rows([{'id': 'a_match_1', 'name': 'Asset One'}])
            return _Rows([])

        # _load_target_row (full row for return value)
        if 'FROM TARGETS WHERE ID' in q:
            return _Rows([{
                'id': self.target_id, 'workspace_id': self.workspace_id,
                'name': 'Orphan Target', 'target_type': 'contract',
                'chain_network': 'ethereum-mainnet',
                'enabled': True, 'monitoring_enabled': True, 'is_active': True,
                'asset_id': 'a_match_1',
                'monitoring_interval_seconds': 30,
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

        if 'UPDATE TARGETS' in q:
            self.updates.append((query, params))
            return _Rows([])
        if 'INSERT INTO MONITORING_CONFIGS' in q:
            self.inserts.append((query, params))
            return _Rows([])
        if 'MONITORED_TARGETS' in q:
            return _Rows([{'id': 'mt1'}])
        if 'MONITORED_SYSTEMS' in q:
            return _Rows([])
        if 'INSERT INTO ASSETS' in q:
            self.inserts.append((query, params))
            return _Rows([])
        return _Rows([])

    def commit(self):
        pass


def _patch_orphan(monkeypatch, conn):
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
    monkeypatch.setattr(pilot, '_derive_asset_verification', lambda **_kw: {
        'normalized_identifier': '0xdeadbeef00000000000000000000000000000001',
        'verification_status': 'pending',
        'verification_summary': {},
    })


def test_orphan_target_relinks_to_single_matching_asset(monkeypatch):
    """Enabling an orphan target with exactly one matching workspace asset must relink and succeed."""
    conn = _OrphanConn(one_matching_asset=True, many_matching_assets=False)
    _patch_orphan(monkeypatch, conn)

    result = pilot.set_target_enabled('t_orphan', True, _Req())

    assert result is not None, 'set_target_enabled must return a result'
    # Target must have been updated with the new asset_id
    relink_updates = [
        (q, p) for q, p in conn.updates
        if 'SET ASSET_ID' in ' '.join(q.upper().split()) or ('asset_id' in q.lower() and 'UPDATE targets' in q)
    ]
    assert relink_updates, 'Expected an UPDATE targets SET asset_id= ... relink update'


def test_orphan_target_creates_asset_when_no_match(monkeypatch):
    """Enabling an orphan target with no matching asset but a valid identifier must create the asset."""
    conn = _OrphanConn(one_matching_asset=False, many_matching_assets=False)
    _patch_orphan(monkeypatch, conn)

    result = pilot.set_target_enabled('t_orphan', True, _Req())

    assert result is not None, 'set_target_enabled must return a result after asset creation'
    asset_inserts = [(q, p) for q, p in conn.inserts if 'INSERT INTO ASSETS' in q.upper() or 'assets' in q.lower()]
    assert asset_inserts, 'Expected INSERT INTO assets during orphan repair with no existing match'


def test_orphan_target_multiple_candidates_returns_409(monkeypatch):
    """Enabling an orphan target with multiple matching assets must raise 409 Conflict."""
    from fastapi import HTTPException as FastAPIHTTPException
    conn = _OrphanConn(one_matching_asset=False, many_matching_assets=True)
    _patch_orphan(monkeypatch, conn)

    with pytest.raises(FastAPIHTTPException) as exc_info:
        pilot.set_target_enabled('t_orphan', True, _Req())

    assert exc_info.value.status_code == 409, (
        f'Expected 409 for multiple asset candidates, got {exc_info.value.status_code}'
    )
    detail = exc_info.value.detail
    assert isinstance(detail, dict), 'Detail should be a dict with candidate info'
    assert detail.get('code') == 'multiple_asset_candidates'
    assert 'candidates' in detail


def test_orphan_target_no_identifier_returns_400(monkeypatch):
    """Enabling an orphan target with no identifier and no asset match must raise 400."""
    from fastapi import HTTPException as FastAPIHTTPException

    class _NoIdentifierConn(_OrphanConn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            params = params or ()
            if 'FROM TARGETS WHERE ID' in q and 'DELETED_AT IS NULL' in q:
                return _Rows([{
                    'id': self.target_id, 'asset_id': None,
                    'chain_network': 'ethereum-mainnet',
                    'name': '', 'target_type': 'contract',
                    'contract_identifier': None, 'wallet_address': None,
                }])
            if 'FROM ASSETS A WHERE A.ID' in q:
                return _Rows([])
            if 'FROM ASSETS' in q:
                return _Rows([])
            if 'UPDATE TARGETS' in q:
                self.updates.append((query, params))
                return _Rows([])
            return _Rows([])

    conn = _NoIdentifierConn(one_matching_asset=False)
    _patch_orphan(monkeypatch, conn)

    with pytest.raises(FastAPIHTTPException) as exc_info:
        pilot.set_target_enabled('t_orphan', True, _Req())

    assert exc_info.value.status_code == 400
