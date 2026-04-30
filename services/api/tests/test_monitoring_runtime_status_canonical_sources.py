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
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

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


class _CanonicalConn(_Conn):
    def __init__(self, *, telemetry_events=False, coverage_with_resolved_basis=False, receipts=False, monitored_last_coverage=False, target_evaluations=False, legacy_detection=False):
        self.telemetry_events = telemetry_events
        self.coverage_with_resolved_basis = coverage_with_resolved_basis
        self.receipts = receipts
        self.monitored_last_coverage = monitored_last_coverage
        self.target_evaluations = target_evaluations
        self.legacy_detection = legacy_detection

    def execute(self, q, p=None):
        qn = ' '.join(str(q).split())
        now = datetime.now(timezone.utc)
        if 'FROM workspaces' in qn and 'slug' in qn:
            return _Result(row={'id': _Ctx.workspace_id, 'slug': _Ctx.workspace_slug})
        if 'FROM monitored_targets' in qn:
            return _Result(rows=[{'id': 't1', 'workspace_id': _Ctx.workspace_id, 'asset_id': 'a1', 'enabled': True}])
        if 'FROM monitored_systems' in qn:
            return _Result(rows=[{'id': 's1', 'target_id': 't1', 'asset_id': 'a1', 'is_enabled': True, 'last_event_at': None, 'last_heartbeat': now, 'last_coverage_telemetry_at': (now - timedelta(seconds=8)) if self.monitored_last_coverage else None}])
        if 'FROM monitoring_event_receipts' in qn:
            if self.receipts:
                return _Result(rows=[{'monitored_system_id': 's1', 'received_at': now - timedelta(seconds=5)}])
            return _Result(rows=[])
        if 'FROM telemetry_events' in qn and 'MAX(observed_at) AS ts' in qn:
            return _Result(row={'ts': (now - timedelta(seconds=4)) if self.telemetry_events else None})
        if 'FROM detection_events' in qn and 'MAX(created_at) AS ts' in qn:
            return _Result(row={'ts': None})
        if 'SELECT DISTINCT te.target_id' in qn and 'FROM telemetry_events te' in qn:
            return _Result(rows=[{'target_id': 't1'}] if self.telemetry_events else [])
        if 'WITH latest_coverage AS' in qn and 'JOIN telemetry_events te' in qn:
            return _Result(rows=[{'target_id': 't1'}] if self.coverage_with_resolved_basis else [])
        if 'FROM target_coverage_records' in qn:
            return _Result(rows=[{
                'target_id': 't1',
                'coverage_status': 'reporting',
                'last_telemetry_at': now - timedelta(seconds=7),
                'evidence_source': 'live',
                'computed_at': now,
                'metadata': {'telemetry_basis': {'kind': 'telemetry_event', 'event_id': 'te-1'}},
            }])
        if 'FROM target_evaluation' in qn:
            return _Result(rows=[{'target_id': 't1', 'evaluated_at': now}] if self.target_evaluations else [])
        if 'FROM detections d' in qn and 'JOIN detection_evidence' in qn:
            return _Result(row={'detected_at': now - timedelta(seconds=2)} if self.legacy_detection else {'detected_at': None})
        if 'FROM provider_health_records' in qn:
            return _Result(rows=[])
        if 'COUNT(*)' in qn:
            return _Result(row={'c': 0})
        if 'MAX(' in qn:
            return _Result(row={'ts': None})
        return _Result(rows=[] if 'SELECT' in qn else None, row={})


def _runtime_payload(monkeypatch, **conn_flags):
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace_context_for_request', lambda *_a, **_k: ({'id': 'u'}, {'workspace_id': _Ctx.workspace_id, 'workspace': {'slug': _Ctx.workspace_slug}}, True))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _CanonicalConn(**conn_flags))
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'worker_running': True, 'source_type': 'polling', 'ingestion_mode': 'live'})
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    return monitoring_runner.monitoring_runtime_status()


def _assert_canonical_guardrails(payload):
    summary = payload['workspace_monitoring_summary']
    assert payload['reporting_systems_count'] == 0
    assert payload['last_telemetry_at'] is None
    assert payload['last_detection_at'] is None
    assert summary['runtime_status'] == payload['runtime_status']
    assert payload.get('evidence_source') == 'none'
    assert payload.get('confidence_status') == 'unavailable'
    assert payload.get('contradiction_flags', []) == summary.get('contradiction_flags', [])


def test_runtime_status_receipts_only_do_not_drive_canonical_reporting(monkeypatch):
    payload = _runtime_payload(monkeypatch, receipts=True)
    _assert_canonical_guardrails(payload)


def test_runtime_status_monitored_last_coverage_only_does_not_drive_canonical_reporting(monkeypatch):
    payload = _runtime_payload(monkeypatch, monitored_last_coverage=True)
    _assert_canonical_guardrails(payload)


def test_runtime_status_target_evaluation_only_does_not_drive_canonical_reporting(monkeypatch):
    payload = _runtime_payload(monkeypatch, target_evaluations=True)
    _assert_canonical_guardrails(payload)


def test_runtime_status_legacy_detection_diagnostics_do_not_control_canonical_top_level(monkeypatch):
    payload = _runtime_payload(monkeypatch, legacy_detection=True)
    _assert_canonical_guardrails(payload)
    assert payload.get('workspace_monitoring_summary', {}).get('last_detection_at') is None
    assert payload['legacy_diagnostics']['last_detection_at'] is None
    assert isinstance(payload['legacy_diagnostics'], dict)
    for guarded_field in ('runtime_status', 'reporting_systems_count', 'last_telemetry_at', 'last_detection_at', 'evidence_source', 'confidence_status', 'contradiction_flags'):
        assert guarded_field in payload


def test_runtime_status_reporting_increases_with_canonical_telemetry_events(monkeypatch):
    payload = _runtime_payload(monkeypatch, telemetry_events=True)
    assert payload['reporting_systems_count'] >= 1


def test_runtime_status_reporting_increases_with_coverage_basis_resolved_to_real_telemetry_event(monkeypatch):
    payload = _runtime_payload(monkeypatch, coverage_with_resolved_basis=True)
    assert payload['reporting_systems_count'] >= 1


def test_runtime_status_reporting_does_not_increase_when_only_legacy_signals_exist(monkeypatch):
    payload = _runtime_payload(monkeypatch, receipts=True, monitored_last_coverage=True, target_evaluations=True)
    _assert_canonical_guardrails(payload)


def test_runtime_status_includes_persisted_provider_health_and_target_coverage(monkeypatch):
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace_context_for_request', lambda *_a, **_k: ({'id': 'u'}, {'workspace_id': _Ctx.workspace_id, 'workspace': {'slug': _Ctx.workspace_slug}}, True))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _Conn())
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'worker_running': True, 'source_type': 'polling', 'ingestion_mode': 'live'})
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['provider_health'] in {'healthy', 'degraded', 'error'}
    assert isinstance(payload['workspace_monitoring_summary'], dict)


def test_runtime_status_uses_canonical_timestamp_columns(monkeypatch):
    seen_queries = []

    class _TrackingConn(_Conn):
        def execute(self, q, p=None):
            seen_queries.append(' '.join(str(q).split()))
            return super().execute(q, p)

    monkeypatch.setattr(monitoring_runner, 'resolve_workspace_context_for_request', lambda *_a, **_k: ({'id': 'u'}, {'workspace_id': _Ctx.workspace_id, 'workspace': {'slug': _Ctx.workspace_slug}}, True))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _TrackingConn())
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'worker_running': True, 'source_type': 'polling', 'ingestion_mode': 'live'})
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)

    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    monitoring_runner.monitoring_runtime_status()
    joined = '\n'.join(seen_queries)
    assert 'detection_events.detected_at' not in joined
    assert 'de.detected_at' not in joined
    assert 'te.created_at' not in joined
    assert 'telemetry_events.created_at' not in joined
    if joined:
        assert 'MAX(created_at) AS ts FROM detection_events' in joined
        assert 'te.ingested_at' in joined
