from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import monitoring_runner, pilot
from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.calls.append((normalized, params))
        if 'FROM alert_suppression_rules' in normalized:
            return _Result(None)
        if 'FROM alerts' in normalized and 'dedupe_signature' in normalized:
            return _Result(None)
        return _Result(None)


def test_requested_chain_coverage_telemetry_heartbeat_poll_and_contradictions() -> None:
    now = datetime.now(timezone.utc)
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='live',
        configured_systems=3,
        monitored_systems_count=3,
        reporting_systems=2,
        protected_assets=2,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        last_coverage_telemetry_at=now,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=2,
        valid_target_system_link_count=2,
        telemetry_window_seconds=300,
    )

    assert summary['last_telemetry_at'] is None
    assert summary['last_poll_at'] is not None
    assert summary['last_heartbeat_at'] is not None
    assert 'heartbeat_without_telemetry_timestamp' in summary['contradiction_flags']
    assert 'poll_without_telemetry_timestamp' in summary['contradiction_flags']
    assert 'telemetry_unavailable_with_high_confidence' in summary['contradiction_flags']


def test_requested_chain_coverage_detection_alert_incident_creation_and_monitoring_run_links(monkeypatch):
    connection = _Connection()
    now = datetime.now(timezone.utc)
    event = monitoring_runner.ActivityEvent(
        event_id='evt-requested-1',
        kind='transaction',
        observed_at=now,
        ingestion_source='websocket',
        cursor='1:0xaaa:0',
        payload={'tx_hash': '0xaaa', 'block_number': 12, 'log_index': 0, 'event_type': 'transfer'},
    )
    target = {
        'id': 'target-requested-1',
        'workspace_id': 'ws-requested-1',
        'name': 'Treasury wallet',
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': True,
        'asset_id': 'asset-requested-1',
        'monitored_system_id': 'system-requested-1',
    }

    monkeypatch.setattr(monitoring_runner, '_load_target_asset_context', lambda *_a, **_k: {'id': 'asset-requested-1', 'name': 'USTB'})
    monkeypatch.setattr(monitoring_runner, '_normalize_event', lambda *_a, **_k: ('transaction', {'metadata': {'event_id': 'evt-requested-1'}}))
    monkeypatch.setattr(monitoring_runner, 'monitoring_ingestion_runtime', lambda: {'mode': 'live', 'source': 'polling', 'degraded': False})
    monkeypatch.setattr(
        monitoring_runner,
        '_threat_call',
        lambda *_a, **_k: (
            {
                'severity': 'high',
                'source': 'live',
                'matched_patterns': [{'label': 'counterparty_allowlist_violation'}],
                'explanation': 'Suspicious transfer',
                'confidence': 0.94,
                'recommended_action': 'review',
                'metadata': {'ingestion_source': 'live'},
            },
            {},
        ),
    )
    monkeypatch.setattr(monitoring_runner, '_enforce_asset_detectors', lambda *_a, **_k: [])
    monkeypatch.setattr(
        monitoring_runner,
        '_asset_detection_summary',
        lambda *_a, **_k: {
            'detection_family': 'counterparty',
            'detector_status': 'anomaly_detected',
            'anomaly_basis': ['counterparty_allowlist_violation'],
            'confidence_basis': 'provider_evidence',
            'severity': 'high',
            'recommended_action': 'review',
            'protected_asset_context': {'asset_id': 'asset-requested-1'},
            'market_coverage_status': 'ok',
            'oracle_coverage_status': 'ok',
            'provider_coverage_status': {},
            'provider_coverage_summary': {},
            'enterprise_claim_eligibility': False,
            'claim_ineligibility_reasons': [],
            'claim_ineligibility_details': [],
            'baseline_reference': {'status': 'established'},
        },
    )
    monkeypatch.setattr(monitoring_runner, '_protected_asset_coverage_record', lambda **_k: {})
    monkeypatch.setattr(monitoring_runner, '_record_detection_metric', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, '_persist_evidence', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, 'persist_analysis_run', lambda *_a, **_k: 'analysis-requested-1')

    result = monitoring_runner._process_single_event(
        connection,
        target=target,
        workspace={'id': 'ws-requested-1', 'name': 'Workspace'},
        user_id='user-requested-1',
        monitoring_run_id='run-requested-1',
        event=event,
    )

    assert result['detection_id']
    assert result['alert_id']
    assert result['incident_id']
    assert any('INSERT INTO detections' in statement for statement, _ in connection.calls)
    assert any('INSERT INTO alerts' in statement for statement, _ in connection.calls)
    assert any('INSERT INTO incidents' in statement for statement, _ in connection.calls)
    assert any('UPDATE detections SET linked_alert_id = %s::uuid' in statement for statement, _ in connection.calls)


def test_requested_chain_coverage_action_history_and_execution_sources() -> None:
    runner_source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    pilot_source = open('services/api/app/pilot.py', encoding='utf-8').read()

    assert 'INSERT INTO monitoring_runs' in runner_source
    assert 'INSERT INTO detections' in runner_source
    assert 'INSERT INTO alerts' in runner_source
    assert 'INSERT INTO incidents' in runner_source
    assert "'incident.created_from_alert'" in pilot_source
    assert "'alert.escalated_to_incident'" in pilot_source
    assert "'response_action.executed'" in pilot_source
    assert 'INSERT INTO action_history' in pilot_source
    assert 'contradiction_flags' in open('services/api/app/workspace_monitoring_summary.py', encoding='utf-8').read()


def test_requested_chain_coverage_simulated_action_execution_history(monkeypatch):
    executed: list[tuple[str, object]] = []

    class _ExecConnection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result(
                    {
                        'id': 'act-requested-1',
                        'status': 'pending',
                        'mode': 'simulated',
                        'action_type': 'notify_team',
                        'execution_metadata': {},
                        'incident_id': 'inc-requested-1',
                        'alert_id': 'alert-requested-1',
                    }
                )
            return _Result()

        def commit(self):
            return None

    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def _fake_pg():
        yield _ExecConnection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    response = pilot.execute_enforcement_action('act-requested-1', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    assert response['status'] == 'executed'
    assert response['dry_run'] is True
    assert any('UPDATE response_actions SET status = \'executed\'' in statement for statement, _ in executed)
    history_rows = [params for statement, params in executed if 'INSERT INTO action_history' in statement]
    assert any(params[6] == 'response_action.executed' for params in history_rows)
    assert any(params[6] == 'incident.response_action_executed' for params in history_rows)
    assert any(params[6] == 'alert.response_action_executed' for params in history_rows)
