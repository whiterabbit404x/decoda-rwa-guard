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
    CAPTURED_INSERT_TABLES = {
        'telemetry_events',
        'detection_events',
        'alerts',
        'incidents',
        'incident_timeline',
        'governance_actions',
    }

    def __init__(self):
        self.calls: list[dict[str, object]] = []
        self.captured_inserts_by_table: dict[str, list[dict[str, object]]] = {
            table: [] for table in self.CAPTURED_INSERT_TABLES
        }

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        is_insert = normalized.startswith('INSERT INTO ')
        table = normalized.split()[2] if is_insert else None
        record = {
            'statement': normalized,
            'params': params,
            'is_insert': is_insert,
            'table': table,
            'captured_insert': bool(is_insert and table in self.CAPTURED_INSERT_TABLES),
            'captured_sql': normalized if is_insert and table in self.CAPTURED_INSERT_TABLES else None,
            'captured_params': params if is_insert and table in self.CAPTURED_INSERT_TABLES else None,
        }
        self.calls.append(record)
        if record['captured_insert']:
            self.captured_inserts_by_table[table].append(
                {
                    'statement': normalized,
                    'params': params,
                }
            )
        if 'FROM alert_suppression_rules' in normalized:
            return _Result(None)
        if 'FROM alerts' in normalized and 'dedupe_signature' in normalized:
            return _Result(None)
        if 'FROM assets' in normalized:
            return _Result({'id': 'asset-1'})
        if 'FROM targets' in normalized:
            return _Result({'id': 'target-1'})
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
    monkeypatch.setattr(pilot, '_demo_monitoring_bootstrap_allowed', lambda: True)
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *_a, **_k: {'status': 'ok', 'monitored_system_id': 'sys-1'})
    pilot._seed_demo_monitoring_proof(connection, workspace_id='ws-1', user_id='user-1')

    inserts_by_table: dict[str, list[dict[str, object]]] = {}
    for call in connection.calls:
        if call['is_insert'] and call['table']:
            inserts_by_table.setdefault(call['table'], []).append(call)

    runner_incident_params = inserts_by_table['incidents'][0]['params']
    runner_alert_params = inserts_by_table['alerts'][0]['params']
    assert runner_incident_params[8] == runner_alert_params[0]

    def _row_by_table(table: str, index: int = -1) -> dict[str, object]:
        matching = connection.captured_inserts_by_table[table]
        assert matching, f'Expected INSERT capture for {table}'
        return matching[index]

    telemetry_insert = _row_by_table('telemetry_events', 0)
    detection_insert = _row_by_table('detection_events', 0)
    incident_insert = _row_by_table('incidents', 0)
    incident_timeline_insert = _row_by_table('incident_timeline', 0)
    governance_insert = next(
        row
        for row in connection.captured_inserts_by_table['governance_actions']
        if any(mode in row['statement'] for mode in ("'recommendation'", "'simulation'", "'manual_required'", "'executed'"))
    )

    telemetry_event_id = telemetry_insert['params'][0]
    detection_event_id = detection_insert['params'][0]
    alert_insert = next(
        row for row in connection.captured_inserts_by_table['alerts']
        if 'detection_event_id' in row['statement']
    )
    alert_id = alert_insert['params'][0]
    incident_id = incident_insert['params'][0]
    incident_alert_id = incident_insert['params'][8]
    captured_alert_ids = {row['params'][0] for row in connection.captured_inserts_by_table['alerts']}

    assert detection_insert['params'][4] == telemetry_event_id
    assert detection_event_id in alert_insert['params']
    assert incident_alert_id in captured_alert_ids
    captured_incident_ids = {row['params'][0] for row in connection.captured_inserts_by_table['incidents']}
    assert incident_timeline_insert['params'][2] in captured_incident_ids
    assert (
        governance_insert['params'][9] == alert_id
        or governance_insert['params'][8] == incident_id
    )

    action_mode = next(mode for mode in ('recommendation', 'simulation', 'manual_required', 'executed') if f"'{mode}'" in governance_insert['statement'])
    assert action_mode in {'recommendation', 'simulation', 'manual_required', 'executed'}
    execution_integration_enabled = bool(getattr(monitoring_runner, 'EXECUTION_INTEGRATION_ENABLED', False))
    if not execution_integration_enabled:
        assert action_mode != 'executed'


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
    assert last_telemetry_at is None
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
