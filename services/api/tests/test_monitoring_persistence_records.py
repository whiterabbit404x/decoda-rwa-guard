from datetime import datetime, timezone

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None):
        self._row = row
    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self):
        self.calls = []
    def execute(self, q, p=None):
        self.calls.append((q, p))
        if 'SELECT id, name FROM workspaces' in q:
            return _Result({'id': '00000000-0000-0000-0000-000000000001', 'name': 'W'})
        if 'SELECT MAX(observed_at) AS ts FROM telemetry_events' in q:
            return _Result({'ts': datetime.now(timezone.utc)})
        if 'SELECT MAX(created_at) AS ts FROM detection_events' in q:
            return _Result({'ts': datetime.now(timezone.utc)})
        return _Result(None)


def test_process_monitoring_target_persists_provider_health_and_target_coverage(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *a, **k: 0)
    monkeypatch.setattr(monitoring_runner, '_upsert_checkpoint', lambda *a, **k: None)
    monkeypatch.setattr(monitoring_runner, '_persist_detection_evaluation_checkpoint', lambda *a, **k: None)
    monkeypatch.setattr(monitoring_runner, '_process_single_event', lambda *a, **k: {'analysis_run_id': 'r', 'monitoring_state': 'ok'})
    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *a, **k: monitoring_runner.ActivityProviderResult(status='live', source_type='polling', provider_name='rpc', events=[], synthetic=False))

    target = {'id': '00000000-0000-0000-0000-000000000002', 'workspace_id': '00000000-0000-0000-0000-000000000001', 'asset_id': '00000000-0000-0000-0000-000000000003', 'target_type': 'contract'}
    monitoring_runner.process_monitoring_target(conn, target)

    sql = '\n'.join(q for q, _ in conn.calls)
    assert 'INSERT INTO provider_health_records' in sql
    assert 'INSERT INTO target_coverage_records' in sql
