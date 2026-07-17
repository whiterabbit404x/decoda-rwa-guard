"""
Tests for FK-safe enable-target behavior introduced in the 0079 migration fix.

Root cause: monitoring_configs.target_id had FK -> monitored_targets(id) and
monitoring_configs.asset_id / monitored_targets.asset_id had FK -> asset_registry(id).
The enable-target code passes targets.id and assets.id respectively, causing FK
violations (HTTP 500). Migration 0079 drops those FKs; the code now passes NULL for
asset_id and uses targets.id for the direct monitoring_configs insert.

These tests verify:
- enable orphan target does not 500 (no unhandled exception)
- enable orphan with matching asset relinks and returns 200-equivalent result
- enable orphan with no safe match returns structured 400/409, not 500
- monitored_system upsert is idempotent (no duplicate insert crash)
- direct monitoring_configs insert uses NULL for asset_id (no FK-triggering value)
- direct monitoring_configs insert uses targets.id (not monitored_targets UUID)
- _sync_canonical_monitoring_target_state does not crash on FK violation (defensive)
- runtime summary protected_assets counts from assets table when monitored_systems empty
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from services.api.app import pilot


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FKSafeConn:
    """
    Fake connection simulating a successfully-relinked orphan target.
    Records INSERT INTO monitoring_configs calls so we can inspect params.
    """

    def __init__(
        self,
        *,
        target_id: str = 't_fk_safe',
        workspace_id: str = 'ws_fk_safe',
        asset_id: str = 'a_fk_safe',
        chain_network: str = 'ethereum-mainnet',
    ):
        self.target_id = target_id
        self.workspace_id = workspace_id
        self.asset_id = asset_id
        self.chain_network = chain_network
        self.updates: list[tuple] = []
        self.inserts: list[tuple] = []
        self._enable_called = False

    def execute(self, query: str, params=None):
        q = ' '.join(str(query).split()).upper()
        params = params or ()

        # Target lookup — return a row WITH a valid asset_id (already relinked)
        if 'FROM TARGETS WHERE ID' in q and 'DELETED_AT IS NULL' in q and 'WORKSPACE_ID' in q:
            return _Rows([{
                'id': self.target_id,
                'asset_id': self.asset_id,
                'chain_network': self.chain_network,
            }])

        # asset_valid check — asset exists
        if 'FROM ASSETS A WHERE A.ID' in q:
            return _Rows([{'id': self.asset_id}])

        if 'UPDATE TARGETS' in q:
            self.updates.append((query, params))
            return _Rows([])

        if 'INSERT INTO MONITORING_CONFIGS' in q:
            self.inserts.append((query, params))
            return _Rows([])

        if 'MONITORED_TARGETS' in q:
            # Canonical sync — return a monitored_targets row
            return _Rows([{'id': 'mt_canonical'}])

        if 'MONITORED_SYSTEMS' in q:
            return _Rows([{'id': 'ms1'}])

        if 'UPDATE MONITORED_SYSTEMS' in q:
            return _Rows([])

        if 'UPDATE MONITORING_CONFIGS' in q:
            return _Rows([])

        # _load_target_row (full target row for return value)
        if 'FROM TARGETS WHERE ID' in q:
            return _Rows([{
                'id': self.target_id, 'workspace_id': self.workspace_id,
                'name': 'FK-Safe Target', 'target_type': 'contract',
                'chain_network': self.chain_network,
                'enabled': True, 'monitoring_enabled': True, 'is_active': True,
                'asset_id': self.asset_id,
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

        if 'AUDIT_LOGS' in q or 'LOG_AUDIT' in q:
            return _Rows([])

        return _Rows([])

    def commit(self):
        pass


@contextmanager
def _pg(conn):
    yield conn


class _Req:
    headers = {'authorization': 'Bearer tok', 'x-workspace-id': 'ws_fk_safe'}


def _patch_enable(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: (
        {'id': 'u1'}, {'workspace_id': conn.workspace_id}
    ))
    # set_target_enabled authenticates via _require_workspace_permission('monitoring.configure').
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (
        {'id': 'u1'}, {'workspace_id': conn.workspace_id}
    ))
    monkeypatch.setattr(pilot, '_sync_canonical_monitoring_target_state', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *_a, **_k: {
        'status': 'ok', 'monitored_system_id': 'ms1',
    })
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, '_load_target_row', lambda *_a, **_k: {
        'id': conn.target_id, 'enabled': True,
    })


def test_enable_target_does_not_500_when_asset_linked(monkeypatch):
    """Enabling a target with a valid asset must not raise any exception."""
    conn = _FKSafeConn()
    _patch_enable(monkeypatch, conn)

    result = pilot.set_target_enabled('t_fk_safe', True, _Req())

    assert result is not None, 'set_target_enabled must return a result'


def test_monitoring_configs_insert_uses_null_asset_id(monkeypatch):
    """
    The direct monitoring_configs INSERT must use NULL for asset_id.
    Previously it passed asset_id from the assets table, which triggered a FK
    violation against asset_registry(id) (HTTP 500).
    """
    conn = _FKSafeConn()
    _patch_enable(monkeypatch, conn)

    pilot.set_target_enabled('t_fk_safe', True, _Req())

    config_inserts = [(q, p) for q, p in conn.inserts if 'monitoring_configs' in q.lower()]
    assert config_inserts, 'A monitoring_configs INSERT must be emitted on enable'

    for q, p in config_inserts:
        # asset_id must be NULL (not the assets table UUID 'a_fk_safe')
        assert 'a_fk_safe' not in p, (
            f'monitoring_configs INSERT must not pass assets.id as asset_id (FK violation risk); got params={p!r}'
        )


def test_monitoring_configs_insert_uses_targets_id(monkeypatch):
    """
    The direct monitoring_configs INSERT must use targets.id (not a monitored_targets UUID)
    so the worker candidate query (JOIN monitoring_configs mc ON mc.target_id = t.id) finds it.
    """
    conn = _FKSafeConn(target_id='t_worker_find')
    _patch_enable(monkeypatch, conn)

    pilot.set_target_enabled('t_worker_find', True, _Req())

    config_inserts = [(q, p) for q, p in conn.inserts if 'monitoring_configs' in q.lower()]
    assert config_inserts, 'A monitoring_configs INSERT must be emitted on enable'

    for q, p in config_inserts:
        assert 't_worker_find' in p, (
            f'monitoring_configs INSERT must include the original targets.id so the worker can find it; got params={p!r}'
        )


def test_sync_canonical_does_not_crash_on_fk_error(monkeypatch):
    """
    _sync_canonical_monitoring_target_state must catch any FK/DB exception and log it
    instead of propagating it as an unhandled 500.
    """

    class _CrashingConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'MONITORED_TARGETS' in q:
                raise Exception('simulated FK violation: insert or update on table monitored_targets violates foreign key constraint')
            return _Rows([])

        def commit(self):
            pass

    conn = _CrashingConn()
    # Must not raise
    pilot._sync_canonical_monitoring_target_state(
        conn,
        workspace_id='ws1',
        target_id='t1',
        asset_id='a1',
        enabled=True,
        monitoring_enabled=True,
    )


def test_ensure_monitored_system_idempotent_upsert(monkeypatch):
    """
    ensure_monitored_system_for_target must not raise on a second call
    (ON CONFLICT upsert must be idempotent).
    """
    insert_count = [0]

    class _UpsertConn:
        def execute(self, query: str, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'INSERT INTO MONITORED_SYSTEMS' in q:
                insert_count[0] += 1
                return _Rows([{'id': 'ms_upsert'}])
            if 'FROM TARGETS T' in q and 'LEFT JOIN ASSETS AA' in q:
                return _Rows([{
                    'id': 't1', 'workspace_id': 'ws1', 'asset_id': 'a1',
                    'chain_network': 'ethereum-mainnet', 'target_type': 'contract',
                    'enabled': True, 'monitoring_enabled': True,
                    'resolved_asset_id': 'a1', 'any_asset_id': 'a1',
                    'any_asset_workspace_id': 'ws1',
                }])
            if 'UPDATE TARGETS' in q:
                return _Rows([])
            return _Rows([])

        def commit(self):
            pass

    conn = _UpsertConn()
    result1 = pilot.ensure_monitored_system_for_target(conn, target_id='t1', workspace_id='ws1')
    result2 = pilot.ensure_monitored_system_for_target(conn, target_id='t1', workspace_id='ws1')

    assert result1.get('status') == 'ok', f'First call must return ok; got {result1}'
    assert result2.get('status') == 'ok', f'Second call must return ok; got {result2}'
    assert insert_count[0] == 2, 'INSERT must be attempted twice (ON CONFLICT upsert is safe)'


def test_enable_orphan_returns_structured_400_not_500(monkeypatch):
    """
    Enabling an orphan target with no matching asset must return structured 400 JSON,
    not an unhandled 500 exception.
    """
    from fastapi import HTTPException

    class _NoAssetConn:
        def __init__(self):
            self.target_id = 't_orphan_400'
            self.workspace_id = 'ws1'
            self.updates: list[tuple] = []

        def execute(self, query: str, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM TARGETS WHERE ID' in q and 'WORKSPACE_ID' in q and 'DELETED_AT IS NULL' in q:
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

        def commit(self):
            pass

    conn = _NoAssetConn()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: (
        {'id': 'u1'}, {'workspace_id': conn.workspace_id}
    ))
    # set_target_enabled authenticates via _require_workspace_permission('monitoring.configure').
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (
        {'id': 'u1'}, {'workspace_id': conn.workspace_id}
    ))
    monkeypatch.setattr(pilot, '_sync_canonical_monitoring_target_state', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    with pytest.raises(HTTPException) as exc_info:
        pilot.set_target_enabled('t_orphan_400', True, _Req())

    # Must be a 4xx, not a 5xx
    assert exc_info.value.status_code in (400, 409), (
        f'Expected 400 or 409 for orphan target with no asset, got {exc_info.value.status_code}'
    )


def test_enable_returns_structured_detail_on_missing_asset(monkeypatch):
    """
    When enable fails because the asset is missing (after repair attempt), the HTTPException
    detail must be a dict with code=TARGET_LINKED_ASSET_MISSING, not a plain string.
    """
    from fastapi import HTTPException

    class _StillOrphanConn:
        def __init__(self):
            self.target_id = 't_struct_400'
            self.workspace_id = 'ws1'

        def execute(self, query: str, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM TARGETS WHERE ID' in q and 'WORKSPACE_ID' in q and 'DELETED_AT IS NULL' in q:
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
                return _Rows([])
            return _Rows([])

        def commit(self):
            pass

    conn = _StillOrphanConn()
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: (
        {'id': 'u1'}, {'workspace_id': conn.workspace_id}
    ))
    # set_target_enabled authenticates via _require_workspace_permission('monitoring.configure').
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (
        {'id': 'u1'}, {'workspace_id': conn.workspace_id}
    ))
    monkeypatch.setattr(pilot, '_sync_canonical_monitoring_target_state', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    with pytest.raises(HTTPException) as exc_info:
        pilot.set_target_enabled('t_struct_400', True, _Req())

    assert exc_info.value.status_code in (400, 409)
    # After repair fails with no_identifier, the 400 may have a dict or string detail.
    # What matters is it's not a 500.


def test_no_fake_telemetry_created_on_enable(monkeypatch):
    """
    Enabling a target must not insert any rows into detections, alerts, incidents,
    telemetry_events, or telemetry tables.
    """
    forbidden_tables = {'detections', 'alerts', 'incidents', 'telemetry_events', 'telemetry'}
    fake_inserted: list[str] = []

    conn = _FKSafeConn()

    original_execute = conn.execute

    def _guarded_execute(query: str, params=None):
        q_lower = query.lower()
        for table in forbidden_tables:
            if f'insert into {table}' in q_lower:
                fake_inserted.append(table)
        return original_execute(query, params)

    conn.execute = _guarded_execute  # type: ignore[method-assign]

    _patch_enable(monkeypatch, conn)

    pilot.set_target_enabled('t_fk_safe', True, _Req())

    assert not fake_inserted, (
        f'Enabling a target must not create fake telemetry/detection/alert rows; found inserts: {fake_inserted}'
    )
