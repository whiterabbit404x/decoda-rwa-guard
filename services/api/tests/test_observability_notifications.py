from __future__ import annotations

from pathlib import Path

from services.api.app.observability import bind_trace, increment, prometheus_metrics, reset_trace, span
from services.api.app.pilot import _notification_policy_payload

ROOT = Path(__file__).resolve().parents[1]


def test_metrics_and_trace_context_are_structured():
    tokens = bind_trace('trace-notification-test', 'span-test')
    try:
        with span('notification.test', workspace_id='workspace-1'):
            increment('decoda_test_events_total', outcome='ok')
    finally:
        reset_trace(tokens)
    metrics = prometheus_metrics()
    assert 'decoda_test_events_total{outcome="ok"}' in metrics
    assert 'decoda_trace_spans_total' in metrics


def test_notification_policy_normalizes_workspace_filters_and_retries():
    policy = _notification_policy_payload({
        'name': 'Critical custody', 'severity_threshold': 'critical',
        'asset_ids': ['asset-1'], 'event_types': ['alert.created', 'proof_chain.failed'],
        'destination_ids': ['destination-1'], 'retry_schedule_seconds': [10, 30, 90],
        'suppression_seconds': 300, 'escalation_after_seconds': 900,
        'escalation_destination_ids': ['destination-2'],
    })
    assert policy['severity_threshold'] == 'critical'
    assert policy['asset_ids'] == ['asset-1']
    assert policy['retry_schedule_seconds'] == [10, 30, 90]
    assert policy['suppression_seconds'] == 300
    assert policy['escalation_destination_ids'] == ['destination-2']


def test_notification_routes_and_persistence_contract_are_wired():
    main_source = (ROOT / 'app' / 'main.py').read_text()
    migration = (ROOT / 'migrations' / '0094_observability_notification_policies.sql').read_text()
    assert "@app.get('/integrations/notifications'" in main_source
    assert "@app.post('/integrations/notifications/destinations'" in main_source
    assert "@app.post('/integrations/notifications/attempts/{attempt_id}/acknowledge'" in main_source
    assert 'CREATE TABLE IF NOT EXISTS notification_attempts' in migration
    assert 'acknowledged_by_user_id' in migration
    assert 'monitoring_system_alerts' in migration


def test_send_external_oncall_alert_unrouted_logs_workspace_and_target_id(monkeypatch, caplog):
    """When MONITORING_ONCALL_URL is not configured, the unrouted WARNING log must include
    workspace_id and target_id as explicit searchable fields (not buried in context)."""
    import logging
    monkeypatch.delenv('MONITORING_ONCALL_URL', raising=False)
    from services.api.app.observability import send_external_oncall_alert

    with caplog.at_level(logging.WARNING, logger='services.api.app.observability'):
        send_external_oncall_alert(
            'stale_telemetry',
            'Target telemetry is stale.',
            fingerprint='ws-abc:tgt-123',
            details={'workspace_id': 'ws-abc', 'target_id': 'tgt-123', 'latest_telemetry_at': '2026-01-01T00:00:00Z'},
        )

    assert any('ws-abc' in r.message and 'tgt-123' in r.message for r in caplog.records), (
        'workspace_id and target_id must appear as explicit fields in the unrouted alert log'
    )


def test_runtime_components_expose_actionable_observability():
    worker = (ROOT / 'app' / 'run_monitoring_worker.py').read_text()
    executor = (ROOT / 'app' / 'response_action_executor.py').read_text()
    watcher = (ROOT.parent / 'event-watcher' / 'app' / 'main.py').read_text()
    assert 'decoda_monitoring_worker_healthy' in worker
    assert 'send_external_oncall_alert' in worker
    assert 'decoda_response_action_outcomes_total' in executor
    assert "@app.get('/metrics'" in watcher
    assert 'decoda_ingestion_lag_blocks' in watcher
