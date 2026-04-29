from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import monitoring_runner
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


def test_target_enablement_persists_after_refresh_with_single_active_monitoring_config() -> None:
    source = open('services/api/app/pilot.py', encoding='utf-8').read()
    assert 'INSERT INTO monitoring_configs' in source
    assert 'SET enabled = FALSE' in source
    assert 'WHERE target_id = %s::uuid' in source


def test_heartbeat_and_poll_do_not_set_last_telemetry_without_telemetry_row() -> None:
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
        configured_systems=2,
        monitored_systems_count=2,
        reporting_systems=0,
        protected_assets=2,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        last_coverage_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=2,
        telemetry_window_seconds=300,
    )

    assert summary['last_telemetry_at'] is None
    assert 'heartbeat_without_telemetry_timestamp' in summary['contradiction_flags']
    assert 'poll_without_telemetry_timestamp' in summary['contradiction_flags']


def test_telemetry_promotes_reporting_systems_and_detection_alert_incident_chain(monkeypatch):
    connection = _Connection()
    now = datetime.now(timezone.utc)
    event = monitoring_runner.ActivityEvent(
        event_id='evt-req-1',
        kind='transaction',
        observed_at=now,
        ingestion_source='websocket',
        cursor='1:0xabc:0',
        payload={'tx_hash': '0xabc', 'block_number': 1, 'log_index': 0, 'event_type': 'transfer'},
    )
    target = {
        'id': 'target-1',
        'workspace_id': 'ws-1',
        'name': 'Treasury wallet',
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': True,
        'asset_id': 'asset-1',
        'monitored_system_id': 'sys-1',
    }

    monkeypatch.setattr(monitoring_runner, '_load_target_asset_context', lambda *_a, **_k: {'id': 'asset-1', 'name': 'USTB'})
    monkeypatch.setattr(monitoring_runner, '_normalize_event', lambda *_a, **_k: ('transaction', {'metadata': {'event_id': 'evt-req-1'}}))
    monkeypatch.setattr(monitoring_runner, 'monitoring_ingestion_runtime', lambda: {'mode': 'live', 'source': 'polling', 'degraded': False})
    monkeypatch.setattr(
        monitoring_runner,
        '_threat_call',
        lambda *_a, **_k: ({'severity': 'high', 'source': 'live', 'confidence': 0.95, 'recommended_action': 'review', 'matched_patterns': [{'label': 'counterparty_allowlist_violation'}]}, {}),
    )
    monkeypatch.setattr(monitoring_runner, '_enforce_asset_detectors', lambda *_a, **_k: [])
    monkeypatch.setattr(monitoring_runner, '_asset_detection_summary', lambda *_a, **_k: {'detection_family': 'counterparty', 'detector_status': 'anomaly_detected', 'anomaly_basis': ['counterparty_allowlist_violation'], 'confidence_basis': 'provider_evidence', 'severity': 'high', 'recommended_action': 'review', 'protected_asset_context': {'asset_id': 'asset-1'}, 'market_coverage_status': 'ok', 'oracle_coverage_status': 'ok', 'provider_coverage_status': {}, 'provider_coverage_summary': {}, 'enterprise_claim_eligibility': False, 'claim_ineligibility_reasons': [], 'claim_ineligibility_details': [], 'baseline_reference': {'status': 'established'}})
    monkeypatch.setattr(monitoring_runner, '_protected_asset_coverage_record', lambda **_k: {})
    monkeypatch.setattr(monitoring_runner, '_record_detection_metric', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, '_persist_evidence', lambda *_a, **_k: None)
    monkeypatch.setattr(monitoring_runner, 'persist_analysis_run', lambda *_a, **_k: 'analysis-1')

    result = monitoring_runner._process_single_event(connection, target=target, workspace={'id': 'ws-1', 'name': 'Workspace'}, user_id='user-1', monitoring_run_id='run-1', event=event)

    assert result['detection_id']
    assert result['alert_id']
    assert result['incident_id']
    assert any('INSERT INTO detections' in statement for statement, _ in connection.calls)
    assert any('INSERT INTO alerts' in statement for statement, _ in connection.calls)
    assert any('INSERT INTO incidents' in statement for statement, _ in connection.calls)


def test_governance_action_links_incident_and_alert_and_contradiction_guards_exist() -> None:
    source = open('services/api/app/pilot.py', encoding='utf-8').read()
    summary_source = open('services/api/app/workspace_monitoring_summary.py', encoding='utf-8').read()

    assert 'INSERT INTO governance_actions' in source
    assert 'incident_id' in source
    assert 'alert_id' in source
    assert 'contradiction_flags' in summary_source


def test_coverage_reporting_is_downgraded_without_real_telemetry_basis() -> None:
    coverage_status, last_telemetry_at, evidence_source, metadata = monitoring_runner._resolve_target_coverage_state(
        provider_status='live',
        telemetry_row=None,
        provider_evidence_source='websocket',
        source_status='healthy',
    )

    assert coverage_status == 'stale'
    assert last_telemetry_at is None
    assert evidence_source == 'none'
    assert metadata['telemetry_basis'] == {'kind': 'none'}


def test_coverage_reporting_requires_telemetry_event_id_and_timestamp() -> None:
    now = datetime.now(timezone.utc)
    coverage_status, last_telemetry_at, evidence_source, metadata = monitoring_runner._resolve_target_coverage_state(
        provider_status='live',
        telemetry_row={'id': None, 'observed_at': now},
        provider_evidence_source='polling',
        source_status='healthy',
    )

    assert coverage_status == 'stale'
    assert last_telemetry_at == now
    assert evidence_source == 'none'
    assert metadata['telemetry_basis'] == {'kind': 'none'}


def test_coverage_reporting_succeeds_with_real_telemetry_event_basis() -> None:
    now = datetime.now(timezone.utc)
    coverage_status, last_telemetry_at, evidence_source, metadata = monitoring_runner._resolve_target_coverage_state(
        provider_status='live',
        telemetry_row={'id': 'evt-123', 'observed_at': now},
        provider_evidence_source='websocket',
        source_status='healthy',
    )

    assert coverage_status == 'reporting'
    assert last_telemetry_at == now
    assert evidence_source == 'websocket'
    assert metadata['telemetry_basis'] == {'kind': 'telemetry_event', 'event_id': 'evt-123'}
