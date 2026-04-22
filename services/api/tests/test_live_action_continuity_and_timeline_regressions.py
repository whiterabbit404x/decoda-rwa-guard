from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from services.api.app import pilot


def test_continuity_status_transitions_cover_live_to_degraded_to_offline() -> None:
    now = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)

    continuous = pilot.evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(minutes=2),
        last_event_at=now - timedelta(minutes=2),
        last_detection_at=now - timedelta(minutes=2),
        heartbeat_ttl_seconds=300,
        telemetry_window_seconds=300,
        detection_window_seconds=300,
    )
    degraded = pilot.evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(minutes=2),
        last_event_at=now - timedelta(minutes=9),
        last_detection_at=now - timedelta(minutes=2),
        heartbeat_ttl_seconds=300,
        telemetry_window_seconds=300,
        detection_window_seconds=300,
    )
    offline = pilot.evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=False,
        last_heartbeat_at=now - timedelta(minutes=20),
        last_event_at=now - timedelta(minutes=20),
        last_detection_at=now - timedelta(minutes=20),
        heartbeat_ttl_seconds=300,
        telemetry_window_seconds=300,
        detection_window_seconds=300,
    )

    assert [continuous['continuity_status'], degraded['continuity_status'], offline['continuity_status']] == [
        'continuous_live',
        'degraded',
        'offline',
    ]


def test_live_revoke_records_safe_proposal_metadata_and_hash(monkeypatch):
    executed: list[tuple[str, object]] = []

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
                        'id': 'act-safe',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'revoke_approval',
                        'execution_metadata': {},
                        'incident_id': 'inc-1',
                        'alert_id': 'alert-1',
                        'token_contract': '0x1111111111111111111111111111111111111111',
                        'calldata': '0x095ea7b3',
                        'chain_network': 'ethereum',
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
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot,
        'resolve_response_action_capability',
        lambda *_a, **_k: {
            'action_type': 'revoke_approval',
            'supported_modes': ['simulated', 'recommended', 'live'],
            'live_execution_path': 'safe',
            'reason': None,
            'supports_mode': True,
            'mode': 'live',
        },
    )
    monkeypatch.setattr(pilot, '_propose_safe_transaction', lambda *_a, **_k: '0xsafehash-123')

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.execute_enforcement_action('act-safe', request)

    assert response['execution_state'] == 'proposed'
    assert response['safe_tx_hash'] == '0xsafehash-123'
    assert any('0xsafehash-123' in str(params) for _, params in executed)


def test_live_freeze_submission_writes_governance_metadata(monkeypatch):
    executed: list[tuple[str, object]] = []

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
                        'id': 'act-governance',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'freeze_wallet',
                        'execution_metadata': {},
                        'incident_id': 'inc-2',
                        'alert_id': 'alert-2',
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
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot,
        '_submit_freeze_wallet_governance_action',
        lambda *_a, **_k: {'action_id': 'gov-123', 'attestation_hash': 'att-123', 'policy_effects': ['Wallet frozen']},
    )

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.execute_enforcement_action('act-governance', request)

    assert response['execution_state'] == 'proposed'
    assert any('gov-123' in str(params) for _, params in executed)
    assert any('att-123' in str(params) for _, params in executed)


def test_unsupported_live_action_returns_non_success(monkeypatch):
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result(
                    {
                        'id': 'act-unsupported',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'block_transaction',
                        'execution_metadata': {},
                        'incident_id': 'inc-1',
                        'alert_id': 'alert-1',
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

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    try:
        pilot.execute_enforcement_action('act-unsupported', request)
        raise AssertionError('Expected unsupported live action to fail.')
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail['status'] == 'failed'
        assert exc.detail['execution_state'] == 'unsupported'


def test_incident_timeline_includes_required_lifecycle_events(monkeypatch):
    timeline_events: list[str] = []

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT id, target_id, analysis_run_id, title, severity, summary, detection_id FROM alerts' in normalized:
                return _Result({'id': 'alert-1', 'target_id': 'target-1', 'analysis_run_id': 'run-1', 'title': 'Escalate me', 'severity': 'high', 'summary': 'summary', 'detection_id': 'det-1'})
            if 'SELECT id, tx_hash, observed_at FROM evidence' in normalized:
                return _Result({'id': 'evidence-1', 'tx_hash': '0xabc', 'observed_at': '2026-04-21T10:01:00Z'})
            if 'WITH inserted_incident AS' in normalized:
                return _Result({'incident_id': 'inc-1'})
            if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result({'id': 'act-unsupported', 'status': 'pending', 'mode': 'live', 'action_type': 'block_transaction', 'execution_metadata': {}, 'incident_id': 'inc-1', 'alert_id': 'alert-1'})
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    def _capture_timeline(_connection, *, workspace_id, incident_id, event_type, message, actor_user_id, metadata=None):
        timeline_events.append(event_type)

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', _capture_timeline)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    pilot.escalate_alert_to_incident('alert-1', {'title': 'Escalated alert'}, request)
    try:
        pilot.execute_enforcement_action('act-unsupported', request)
    except HTTPException:
        pass

    assert 'alert.escalated' in timeline_events
    assert 'evidence.linked' in timeline_events
    assert 'response_action.unsupported' in timeline_events
