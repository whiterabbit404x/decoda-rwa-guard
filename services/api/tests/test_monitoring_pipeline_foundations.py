from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from services.api.app import pilot
from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


def test_workspace_summary_keeps_telemetry_separate_from_heartbeat_and_poll() -> None:
    now = datetime.now(timezone.utc)
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='healthy',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        last_coverage_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
        active_alerts_count=0,
        active_incidents_count=0,
    )

    assert summary['last_telemetry_at'] is None
    assert summary['last_heartbeat_at'] is not None
    assert summary['last_poll_at'] is not None
    assert summary['telemetry_freshness'] == 'unavailable'
    assert 'heartbeat_without_telemetry_timestamp' in summary['contradiction_flags']
    assert 'poll_without_telemetry_timestamp' in summary['contradiction_flags']


def test_response_action_execute_writes_history_and_audit(monkeypatch):
    executed: list[tuple[str, object]] = []
    audits: list[dict[str, object]] = []

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result(
                    {
                        'id': 'act-1',
                        'workspace_id': 'ws-1',
                        'status': 'approved',
                        'mode': 'simulated',
                        'action_type': 'notify_team',
                        'execution_metadata': {'origin': 'unit-test'},
                    }
                )
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(
        pilot,
        'log_audit',
        lambda *_args, **kwargs: audits.append({'action': kwargs.get('action'), 'entity_type': kwargs.get('entity_type')}),
    )

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    payload = pilot.execute_enforcement_action('act-1', request)

    assert payload['status'] == 'executed'
    assert payload['dry_run'] is True
    assert any('UPDATE response_actions SET status = \'executed\'' in statement for statement, _ in executed)
    assert any('INSERT INTO action_history' in statement for statement, _ in executed)
    assert {'action': 'enforcement.action.execute', 'entity_type': 'enforcement_action'} in audits


def test_monitoring_pipeline_source_has_detection_alert_incident_run_and_guard_joins() -> None:
    runner_source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    pilot_source = open('services/api/app/pilot.py', encoding='utf-8').read()

    assert 'INSERT INTO monitoring_runs' in runner_source
    assert 'INSERT INTO detections' in runner_source
    assert 'COALESCE(%s::uuid, detection_id)' in runner_source
    assert 'INSERT INTO alerts' in runner_source
    assert 'incidents_created' in runner_source

    assert 'incident.created_from_alert' in pilot_source
    assert 'alert.escalated_to_incident' in pilot_source
    assert 'response_action.executed' in pilot_source
    assert 'enforcement.action.execute' in pilot_source


def test_seed_demo_monitoring_proof_includes_deterministic_detection_alert_incident_and_simulated_action() -> None:
    source = open('services/api/app/pilot.py', encoding='utf-8').read()

    assert "uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-detection:" in source
    assert "uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-alert:" in source
    assert "uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-incident:" in source
    assert "uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-response-action:" in source
    assert 'INSERT INTO response_actions' in source
    assert "'simulated'" in source
