from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from services.api.app import data_retention


class Result:
    def __init__(self, *, rows=None, row=None, rowcount=0):
        self._rows = rows or []
        self._row = row
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class RecordingConnection:
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler

    def execute(self, sql, params=()):
        normalized = ' '.join(sql.split())
        self.calls.append((normalized, params))
        if self.handler:
            result = self.handler(normalized, params)
            if result is not None:
                return result
        return Result()


def request_row(**overrides):
    row = {
        'id': 'request-1',
        'workspace_id': 'workspace-a',
        'data_classes': ['telemetry'],
        'subject_user_id': None,
        'cutoff_at': datetime(2026, 1, 1, tzinfo=timezone.utc),
        'result': {'deletion_modes': {'telemetry': 'hard_delete'}},
    }
    row.update(overrides)
    return row


def test_hard_deletion_is_bounded_to_workspace_and_writes_detailed_event():
    def handler(sql, params):
        if 'FROM workspace_legal_holds' in sql:
            return Result(rows=[])
        if sql.startswith('DELETE FROM telemetry_events'):
            assert params[0] == 'workspace-a'
            return Result(rowcount=4)
        if 'FROM retention_external_artifacts' in sql:
            assert params[:2] == ('workspace-a', 'telemetry')
            return Result(rows=[])
        return Result()

    connection = RecordingConnection(handler)
    result = data_retention.execute_request(connection, request_row(), worker_name='worker-1')

    assert result['status'] == 'completed'
    assert result['operations']['telemetry']['records_affected'] == 4
    event = next(call for call in connection.calls if 'INSERT INTO data_deletion_events' in call[0])
    assert event[1][1:5] == ('request-1', 'workspace-a', 'telemetry', 'hard_delete')
    assert event[1][8].find('worker-1') >= 0


def test_anonymization_uses_policy_mode_without_deleting_rows():
    def handler(sql, params):
        if 'FROM workspace_legal_holds' in sql or 'FROM retention_external_artifacts' in sql:
            return Result(rows=[])
        if sql.startswith('UPDATE detections SET title'):
            return Result(rowcount=2)
        return Result()

    connection = RecordingConnection(handler)
    deletion = request_row(data_classes=['detections'], result={'deletion_modes': {'detections': 'anonymize'}})
    result = data_retention.execute_request(connection, deletion, worker_name='worker-1')

    assert result['operations']['detections'] == {
        'records_affected': 2, 'external_artifacts_deleted': 0, 'mode': 'anonymize'
    }
    assert not any(sql.startswith('DELETE FROM detections') for sql, _ in connection.calls)
    assert any(params[4] == 'anonymize' for sql, params in connection.calls if 'INSERT INTO data_deletion_events' in sql)


def test_applicable_legal_hold_blocks_all_operations_and_records_block_event():
    def handler(sql, params):
        if 'FROM workspace_legal_holds' in sql:
            return Result(rows=[{'id': 'hold-1', 'data_classes': ['telemetry', 'exports']}])
        return Result()

    connection = RecordingConnection(handler)
    result = data_retention.execute_request(connection, request_row(), worker_name='worker-1')

    assert result == {'id': 'request-1', 'status': 'blocked_by_legal_hold', 'blocking_legal_hold_ids': ['hold-1']}
    assert not any(sql.startswith('DELETE FROM telemetry_events') for sql, _ in connection.calls)
    assert any(params[4] == 'blocked' and 'hold-1' in params[8]
               for sql, params in connection.calls if 'INSERT INTO data_deletion_events' in sql)


def test_export_and_registered_object_storage_artifacts_are_idempotently_deleted(monkeypatch):
    deleted = []

    class Storage:
        def delete_bytes(self, *, object_key):
            deleted.append(object_key)

    monkeypatch.setattr(data_retention, 'load_export_storage', lambda: Storage())

    def handler(sql, params):
        if 'FROM workspace_legal_holds' in sql:
            return Result(rows=[])
        if 'FROM export_jobs' in sql:
            return Result(rows=[{'id': 'export-1', 'storage_backend': 's3', 'storage_object_key': 'w/export.zip'}])
        if 'FROM retention_external_artifacts' in sql:
            return Result(rows=[{'id': 'artifact-1', 'provider': 's3', 'object_key': 'w/evidence.json',
                                 'source_table': 'evidence', 'source_id': None}])
        return Result(rowcount=1)

    connection = RecordingConnection(handler)
    result = data_retention.execute_request(
        connection,
        request_row(data_classes=['exports'], result={'deletion_modes': {'exports': 'hard_delete'}}),
        worker_name='worker-1',
    )

    assert deleted == ['w/export.zip', 'w/evidence.json']
    assert result['operations']['exports']['external_artifacts_deleted'] == 1
    event_keys = [params[9] for sql, params in connection.calls if 'INSERT INTO data_deletion_events' in sql]
    assert 'request-1:exports:storage_delete:export:export-1' in event_keys
    assert 'request-1:exports:storage_delete:artifact:artifact-1' in event_keys


def test_retry_failure_requeues_then_terminally_fails(monkeypatch):
    statuses = iter(['approved', 'failed'])
    connections = []

    @contextmanager
    def fake_connection():
        connection = RecordingConnection(
            lambda sql, params: Result(row={'status': next(statuses)}) if 'UPDATE data_deletion_requests' in sql else Result()
        )
        connections.append(connection)
        yield connection

    monkeypatch.setattr('services.api.app.pilot.pg_connection', fake_connection)

    assert data_retention.record_failure('request-1', worker_name='worker-1', error=RuntimeError('temporary')) is False
    assert data_retention.record_failure('request-1', worker_name='worker-1', error=RuntimeError('still broken')) is True
    for connection in connections:
        sql, params = connection.calls[0]
        assert "attempt_count >= max_attempts" in sql
        assert 'power(2' in sql
        assert params[1:] == ('request-1', 'worker-1')


def test_user_anonymization_cannot_cross_tenant_boundary(monkeypatch):
    monkeypatch.setattr('services.api.app.pilot.hash_password', lambda value: 'hashed')

    def handler(sql, params):
        if 'FROM workspace_legal_holds' in sql or 'FROM retention_external_artifacts' in sql:
            return Result(rows=[])
        if sql.startswith('SELECT 1 AS present FROM workspace_members'):
            assert params == ('workspace-a', 'user-1')
            return Result(row=None)  # user exists, but not in workspace-a
        return Result()

    connection = RecordingConnection(handler)
    result = data_retention.execute_request(
        connection,
        request_row(data_classes=['user_data'], subject_user_id='user-1',
                    result={'deletion_modes': {'user_data': 'anonymize'}}),
        worker_name='worker-1',
    )
    assert result['operations']['user_data']['records_affected'] == 0
    assert not any(sql.startswith('DELETE FROM auth_sessions') or sql.startswith('UPDATE auth_tokens')
                   for sql, _ in connection.calls)


def test_worker_migration_and_operational_health_wiring_are_present():
    migration = Path('services/api/migrations/0096_durable_retention_worker.sql').read_text()
    main = Path('services/api/app/main.py').read_text()
    procfile = Path('Procfile').read_text()
    for term in ('attempt_count', 'lease_expires_at', 'idempotency_key', 'retention_external_artifacts', 'retention_worker_state'):
        assert term in migration
    assert "'retention_worker': retention_worker" in main
    assert 'retention-worker: python -m services.api.app.retention_worker' in procfile


def test_worker_health_exposes_freshness_failures_and_latest_completed_sweep(monkeypatch):
    completed = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

    @contextmanager
    def fake_connection():
        yield RecordingConnection(lambda sql, params: Result(rows=[{
            'worker_name': 'retention-1',
            'heartbeat_at': completed,
            'last_completed_sweep_at': completed,
            'last_failure_at': completed,
            'consecutive_failures': 2,
            'last_error': 'two jobs failed',
            'last_summary': {'failed': 2},
            'fresh': True,
        }]))

    monkeypatch.setattr('services.api.app.pilot.database_url', lambda: 'postgresql://test')
    monkeypatch.setattr('services.api.app.pilot.pg_connection', fake_connection)

    health = data_retention.worker_health(stale_after_seconds=600)
    assert health['status'] == 'degraded'
    assert health['fresh'] is True
    assert health['failures'] == 2
    assert health['most_recent_completed_sweep_at'] == completed.isoformat()
    assert health['workers'][0]['last_error'] == 'two jobs failed'
