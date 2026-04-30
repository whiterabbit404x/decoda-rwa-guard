from datetime import datetime, timedelta, timezone

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _Conn:
    def execute(self, q, p=None):
        qn = ' '.join(q.split())
        now = datetime.now(timezone.utc)
        if 'FROM workspaces' in qn and 'slug' in qn:
            return _Result(row={'id': '00000000-0000-0000-0000-000000000001', 'slug': 'w'})
        if 'FROM monitored_systems' in qn:
            return _Result(rows=[{'id': 's1', 'target_id': 't1', 'asset_id': 'a1', 'is_enabled': True, 'last_coverage_telemetry_at': None}])
        if 'FROM target_coverage_records' in qn:
            return _Result(rows=[{
                'id': 'tcr-1',
                'workspace_id': '00000000-0000-0000-0000-000000000001',
                'asset_id': 'a1',
                'target_id': 't1',
                'coverage_status': 'reporting',
                'last_poll_at': now,
                'last_heartbeat_at': now,
                'last_telemetry_at': now - timedelta(seconds=10),
                'last_detection_at': None,
                'evidence_source': 'live',
                'computed_at': now,
                'metadata': {'telemetry_basis': {'kind': 'telemetry_event', 'event_id': 'te-1', 'observed_at': now.isoformat()}, 'reporting': True},
            }])
        if 'FROM provider_health_records' in qn:
            return _Result(rows=[{
                'id': 'phr-1',
                'workspace_id': '00000000-0000-0000-0000-000000000001',
                'provider_type': 'rpc',
                'target_id': 't1',
                'status': 'healthy',
                'checked_at': now,
                'latency_ms': 12,
                'error_message': None,
                'evidence_source': 'live',
                'metadata': {'provider': 'rpc'},
            }])
        if 'COUNT(*)' in qn:
            return _Result(row={'c': 0})
        return _Result(row={})


class _Ctx:
    workspace_id = '00000000-0000-0000-0000-000000000001'
    workspace_slug = 'w'


def test_runtime_status_includes_persisted_provider_health_and_target_coverage(monkeypatch):
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace_context_for_request', lambda *_a, **_k: ({'id': 'u'}, {'workspace_id': _Ctx.workspace_id, 'workspace': {'slug': _Ctx.workspace_slug}}, True))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _Conn())
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'worker_running': True, 'source_type': 'polling', 'ingestion_mode': 'live'})

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['provider_health'] in {'healthy', 'degraded', 'error'}
    assert isinstance(payload['workspace_monitoring_summary'], dict)
