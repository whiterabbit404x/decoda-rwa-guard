from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import monitoring_runner, pilot


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
                'any_asset_id': 'asset-1',
                'any_asset_workspace_id': 'ws-1',
            },
            'target-missing-asset': {
                'id': 'target-missing-asset',
                'workspace_id': 'ws-1',
                'asset_id': 'asset-missing',
                'chain_network': 'ethereum-mainnet',
                'enabled': True,
                'monitoring_enabled': True,
                'resolved_asset_id': None,
                'any_asset_id': None,
                'any_asset_workspace_id': None,
            },
            'target-monitoring-disabled': {
                'id': 'target-monitoring-disabled',
                'workspace_id': 'ws-1',
                'asset_id': 'asset-1',
                'chain_network': 'ethereum-mainnet',
                'enabled': True,
                'monitoring_enabled': False,
                'resolved_asset_id': 'asset-1',
                'any_asset_id': 'asset-1',
                'any_asset_workspace_id': 'ws-1',
            },
            'target-disabled': {
                'id': 'target-disabled',
                'workspace_id': 'ws-1',
                'asset_id': 'asset-1',
                'chain_network': 'ethereum-mainnet',
                'enabled': False,
                'monitoring_enabled': False,
                'resolved_asset_id': 'asset-1',
                'any_asset_id': 'asset-1',
                'any_asset_workspace_id': 'ws-1',
            },
        }
        self.monitored_systems: dict[str, dict] = {}
        self.invalid_marked: list[tuple[str, str]] = []

    def _monitored_rows(self):
        now = datetime.now(timezone.utc)
        return [
            {
                'id': row['id'],
                'workspace_id': 'ws-1',
                'asset_id': row['asset_id'],
                'target_id': target_id,
                'chain': 'ethereum-mainnet',
                'is_enabled': True,
                'runtime_status': 'active',
                'status': 'active',
                'last_heartbeat': now,
                'monitoring_interval_seconds': 30,
                'created_at': now,
            }
            for target_id, row in self.monitored_systems.items()
        ]

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        params = params or ()
        if 'FROM targets t LEFT JOIN assets a' in q and 'WHERE t.id' in q:
            target = self.targets.get(str(params[0]))
            return _Result(dict(target) if target else None)
        if 'INSERT INTO monitored_systems' in q:
            target_id = str(params[3])
            existing = self.monitored_systems.get(target_id, {})
            runtime_status = 'active' if existing.get('runtime_status') == 'active' else 'idle'
            row = {'id': f'ms-{target_id}'}
            self.monitored_systems[target_id] = {
                'id': row['id'],
                'target_id': target_id,
                'asset_id': params[2],
                'runtime_status': runtime_status,
                'status': 'active',
                'last_error_text': None,
            }
            return _Result(row)
        if "SET last_run_status = 'invalid_missing_asset'" in q:
            reason = str(params[0])
            target_id = str(params[1])
            self.invalid_marked.append((target_id, reason))
            return _Result()
        if 'DELETE FROM monitored_systems WHERE target_id' in q:
            self.monitored_systems.pop(str(params[0]), None)
            return _Result()
        if "SET last_run_status = 'ready'" in q:
            return _Result()
        if 'SELECT id FROM targets WHERE deleted_at IS NULL' in q:
            rows = [{'id': target_id} for target_id in self.targets.keys()]
            return _Result(rows=rows)
        if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
            return _Result(rows=self._monitored_rows())
        if 'SELECT id FROM monitored_systems WHERE workspace_id =' in q and 'AND target_id =' in q:
            workspace_id = str(params[0])
            target_id = str(params[1])
            row = self.monitored_systems.get(target_id)
            if row and workspace_id == 'ws-1':
                return _Result(row={'id': row['id']})
            return _Result(row=None)
        if "SELECT COUNT(*) AS c FROM alerts" in q:
            return _Result(row={'c': 0})
        if "SELECT COUNT(*) AS c FROM incidents" in q:
            return _Result(row={'c': 0})
        if 'LEFT JOIN assets a' in q and 'FROM targets t' in q and 'COUNT(*) AS c' in q:
            return _Result(row={'c': 0})
        if 'SELECT observed_at, block_number FROM evidence e' in q:
            return _Result(row=None)
        return _Result()


class _ConnCtx:
    def __init__(self, conn: _Conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


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
    assert ('target-missing-asset', 'linked_asset_missing') in conn.invalid_marked
    assert 'target-missing-asset' not in conn.monitored_systems


def test_ensure_monitored_system_skips_monitoring_disabled_targets_by_default():
    conn = _Conn()
    result = pilot.ensure_monitored_system_for_target(conn, target_id='target-monitoring-disabled')
    assert result['status'] == 'target_not_enabled'
    assert result['reason'] == 'monitoring_disabled'
    assert 'target-monitoring-disabled' not in conn.monitored_systems


def test_reconcile_enabled_targets_backfills_and_reports_invalid_and_skipped_reasons():
    conn = _Conn()
    result = pilot.reconcile_enabled_targets_monitored_systems(conn)
    assert result['targets_scanned'] == 4
    assert result['enabled_targets_scanned'] == 2
    assert result['eligible_targets'] == 1
    assert result['created_or_updated'] == 1
    assert result['invalid_targets'] == ['target-missing-asset']
    assert result['invalid_reasons'] == {'linked_asset_missing': 1}
    assert result['skipped_reasons'] == {'monitoring_disabled': 1, 'target_not_enabled': 1}
    assert 'target-valid' in conn.monitored_systems


def test_reconcile_enabled_targets_is_idempotent_for_existing_rows():
    conn = _Conn()
    first = pilot.reconcile_enabled_targets_monitored_systems(conn)
    second = pilot.reconcile_enabled_targets_monitored_systems(conn)
    assert first['created_or_updated'] == 1
    assert second['created_or_updated'] == 1
    assert len(conn.monitored_systems) == 1
    assert conn.monitored_systems['target-valid']['id'] == 'ms-target-valid'


def test_reconcile_uses_legacy_status_values_allowed_by_constraint():
    conn = _Conn()
    pilot.reconcile_enabled_targets_monitored_systems(conn)
    monitored = conn.monitored_systems['target-valid']
    assert monitored['status'] in {'active', 'paused', 'error'}


def test_repair_reconcile_clears_stale_monitored_system_error_state():
    conn = _Conn()
    conn.monitored_systems['target-valid'] = {
        'id': 'ms-target-valid',
        'target_id': 'target-valid',
        'asset_id': 'asset-1',
        'runtime_status': 'error',
        'status': 'error',
        'last_error_text': 'No events ingested in cycle',
    }

    result = pilot.ensure_monitored_system_for_target(conn, target_id='target-valid')

    assert result['status'] == 'ok'
    repaired = conn.monitored_systems['target-valid']
    assert repaired['runtime_status'] == 'idle'
    assert repaired['status'] == 'active'
    assert repaired['last_error_text'] is None


def test_repair_reconcile_never_writes_idle_legacy_status():
    source = open('services/api/app/pilot.py', encoding='utf-8').read()
    assert "VALUES (%s, %s, %s::uuid, %s::uuid, %s, TRUE, 'idle', 'active')" in source
    assert "status = 'active'" in source
    assert "'idle', 'idle'" not in source


def test_normalize_reconcile_result_provides_render_safe_fields():
    result = pilot._normalize_reconcile_result({})
    assert result['targets_scanned'] == 0
    assert result['created_or_updated'] == 0
    assert result['invalid_reasons'] == {}
    assert result['skipped_reasons'] == {}
    assert result['repaired_monitored_system_ids'] == []


def test_runtime_status_count_reflects_backfilled_monitored_system_rows(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _ConnCtx(conn))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'status': 'running', 'last_cycle_at': None, 'last_heartbeat_at': None})

    before = monitoring_runner.monitoring_runtime_status()
    assert before['monitored_systems'] == 0

    pilot.reconcile_enabled_targets_monitored_systems(conn)
    after = monitoring_runner.monitoring_runtime_status()
    assert after['monitored_systems'] == 1
    assert after['monitored_systems_count'] == 1


def test_reconcile_does_not_claim_success_for_non_visible_rows():
    conn = _Conn()

    original = conn.execute

    def execute(query, params=None):
        q = ' '.join(str(query).split())
        if 'SELECT id FROM monitored_systems WHERE workspace_id =' in q and 'AND target_id =' in q and str((params or [None, None])[1]) == 'target-valid':
            return _Result(row=None)
        return original(query, params)

    conn.execute = execute  # type: ignore[method-assign]

    result = pilot.reconcile_enabled_targets_monitored_systems(conn)
    assert result['created_or_updated'] == 0
    assert result['repaired_monitored_system_ids'] == []
    assert result['skipped_reasons']['post_upsert_not_visible'] == 1
