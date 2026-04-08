from __future__ import annotations

from services.api.app import pilot


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self):
        self.targets = {
            'target-valid': {
                'id': 'target-valid',
                'workspace_id': 'ws-1',
                'asset_id': 'asset-1',
                'chain_network': 'ethereum-mainnet',
                'enabled': True,
                'monitoring_enabled': True,
                'resolved_asset_id': 'asset-1',
            },
            'target-missing-asset': {
                'id': 'target-missing-asset',
                'workspace_id': 'ws-1',
                'asset_id': 'asset-missing',
                'chain_network': 'ethereum-mainnet',
                'enabled': True,
                'monitoring_enabled': True,
                'resolved_asset_id': None,
            },
            'target-disabled': {
                'id': 'target-disabled',
                'workspace_id': 'ws-1',
                'asset_id': 'asset-1',
                'chain_network': 'ethereum-mainnet',
                'enabled': False,
                'monitoring_enabled': False,
                'resolved_asset_id': 'asset-1',
            },
        }
        self.monitored_systems: dict[str, dict] = {}
        self.invalid_marked: list[str] = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        params = params or ()
        if 'FROM targets t LEFT JOIN assets a' in q and 'WHERE t.id' in q:
            target = self.targets.get(str(params[0]))
            return _Result(dict(target) if target else None)
        if 'INSERT INTO monitored_systems' in q:
            target_id = str(params[3])
            row = {'id': f'ms-{target_id}'}
            self.monitored_systems[target_id] = {'id': row['id'], 'target_id': target_id, 'asset_id': params[2]}
            return _Result(row)
        if "SET last_run_status = 'invalid_missing_asset'" in q:
            self.invalid_marked.append(str(params[0]))
            return _Result()
        if 'DELETE FROM monitored_systems WHERE target_id' in q:
            self.monitored_systems.pop(str(params[0]), None)
            return _Result()
        if "SET last_run_status = 'ready'" in q:
            return _Result()
        if 'SELECT id FROM targets WHERE deleted_at IS NULL AND enabled = TRUE AND monitoring_enabled = TRUE' in q:
            rows = [{'id': 'target-valid'}, {'id': 'target-missing-asset'}]
            return _Result(rows=rows)
        return _Result()


def test_ensure_monitored_system_for_enabled_target_creates_row():
    conn = _Conn()
    result = pilot.ensure_monitored_system_for_target(conn, target_id='target-valid')
    assert result['status'] == 'ok'
    assert result['monitored_system_id'] == 'ms-target-valid'
    assert 'target-valid' in conn.monitored_systems


def test_ensure_monitored_system_rejects_target_with_missing_asset():
    conn = _Conn()
    result = pilot.ensure_monitored_system_for_target(conn, target_id='target-missing-asset')
    assert result['status'] == 'invalid_target'
    assert result['reason'] == 'linked_asset_missing'
    assert 'target-missing-asset' in conn.invalid_marked
    assert 'target-missing-asset' not in conn.monitored_systems


def test_ensure_monitored_system_skips_disabled_targets_by_default():
    conn = _Conn()
    result = pilot.ensure_monitored_system_for_target(conn, target_id='target-disabled')
    assert result['status'] == 'target_not_enabled'
    assert 'target-disabled' not in conn.monitored_systems


def test_reconcile_enabled_targets_backfills_and_reports_invalid():
    conn = _Conn()
    result = pilot.reconcile_enabled_targets_monitored_systems(conn)
    assert result['enabled_targets_scanned'] == 2
    assert result['created_or_updated'] == 1
    assert result['invalid_targets'] == ['target-missing-asset']
    assert 'target-valid' in conn.monitored_systems
