from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import HTTPException

from services.api.app import pilot


def test_incident_timeline_records_evidence_escalation_and_action_execution_path(monkeypatch):
    timeline_events: list[tuple[str, dict]] = []

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT id, target_id, analysis_run_id, title, severity, summary, detection_id, alert_type, findings FROM alerts' in normalized:
                return _Result({'id': 'alert-1', 'target_id': 'target-1', 'analysis_run_id': 'run-1', 'title': 'Escalate me', 'severity': 'high', 'summary': 'summary', 'detection_id': 'det-1'})
            if 'SELECT id, tx_hash, observed_at, raw_payload_json FROM evidence' in normalized:
                return _Result({'id': 'evidence-1', 'tx_hash': '0xabc', 'observed_at': '2026-04-21T10:01:00Z', 'raw_payload_json': {}})
            if 'WITH inserted_incident AS' in normalized:
                return _Result({'incident_id': 'inc-1'})
            if 'SELECT id, source_alert_id FROM incidents WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result({'id': 'inc-1', 'source_alert_id': 'alert-1'})
            if 'SELECT id, incident_id FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result({'id': 'alert-1', 'incident_id': 'inc-1'})
            if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                action_id = params[0]
                if action_id == 'act-execute-live':
                    return _Result(
                        {
                            'id': 'act-execute-live',
                            'status': 'pending',
                            'mode': 'live',
                            'action_type': 'revoke_approval',
                            'execution_metadata': {},
                            'incident_id': 'inc-1',
                            'alert_id': 'alert-1',
                            'token_contract': '0x1111111111111111111111111111111111111111',
                            'calldata': '0x095ea7b3',
                            'chain_network': 'ethereum',
                            'approved_by_user_id': 'admin-2',
                        }
                    )
                if action_id == 'act-execute-sim':
                    return _Result(
                        {
                            'id': 'act-execute-sim',
                            'status': 'pending',
                            'mode': 'simulated',
                            'action_type': 'notify_team',
                            'execution_metadata': {},
                            'incident_id': 'inc-1',
                            'alert_id': 'alert-1',
                        }
                    )
                if action_id == 'act-rollback':
                    return _Result(
                        {
                            'id': 'act-rollback',
                            'status': 'executed',
                            'mode': 'live',
                            'action_type': 'revoke_approval',
                            'execution_metadata': {'previous_allowance': '1000', 'external_governance_action_id': None, 'attestation_hash': None},
                            'incident_id': 'inc-1',
                            'alert_id': 'alert-1',
                            'spender': '0x2222222222222222222222222222222222222222',
                            'safe_tx_hash': '0xsafehash',
                            'approved_by_user_id': 'admin-2',
                        }
                    )
                if action_id == 'act-manual':
                    return _Result(
                        {
                            'id': 'act-manual',
                            'status': 'pending',
                            'mode': 'live',
                            'action_type': 'disable_monitored_system',
                            'execution_metadata': {},
                            'incident_id': 'inc-1',
                            'alert_id': 'alert-1',
                            'approved_by_user_id': 'admin-2',
                        }
                    )
                if action_id == 'act-unsupported':
                    return _Result(
                        {
                            'id': 'act-unsupported',
                            'status': 'pending',
                            'mode': 'live',
                            'action_type': 'block_transaction',
                            'execution_metadata': {},
                            'incident_id': 'inc-1',
                            'alert_id': 'alert-1',
                            'approved_by_user_id': 'admin-2',
                        }
                    )
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    def _capture_timeline(_connection, *, workspace_id, incident_id, event_type, message, actor_user_id, metadata=None):
        timeline_events.append((event_type, metadata or {}))

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1'}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot,
        '_propose_safe_transaction',
        lambda *_a, **_k: {'safe_tx_hash': '0xsafehash', 'external_request_id': 'safe-request-1', 'response_code': 201},
    )
    monkeypatch.setattr(
        pilot,
        'resolve_response_action_capability',
        lambda action_type, mode=None: {
            'action_type': action_type,
            'supported_modes': ['simulated', 'recommended', 'live'],
            'live_execution_path': 'safe'
            if str(action_type) == 'revoke_approval'
            else ('manual_only' if str(action_type) == 'disable_monitored_system' else ('unsupported' if str(action_type) == 'block_transaction' else 'governance')),
            'reason': None,
            'supports_mode': True,
            'mode': mode,
        },
    )
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', _capture_timeline)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})

    pilot.escalate_alert_to_incident('alert-1', {'title': 'Escalated alert'}, request)
    pilot.create_enforcement_action(
        {
            'action_type': 'revoke_approval',
            'mode': 'live',
            'incident_id': 'inc-1',
            'alert_id': 'alert-1',
            'params': {
                'token_contract': '0x1111111111111111111111111111111111111111',
                'spender': '0x2222222222222222222222222222222222222222',
            },
        },
        request,
    )
    pilot.execute_enforcement_action('act-execute-live', request)
    pilot.execute_enforcement_action('act-execute-sim', request)
    pilot.execute_enforcement_action('act-manual', request)
    try:
        pilot.execute_enforcement_action('act-unsupported', request)
        raise AssertionError('Expected unsupported action execution to raise HTTPException.')
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail.get('execution_state') == 'unsupported'
    pilot.rollback_enforcement_action('act-rollback', request)

    event_types = [event for event, _ in timeline_events]
    expected_order = [
        'alert.escalated',
        'evidence.linked',
        'response_action.created',
        'response_action.proposed',
        'response_action.executed',
        'response_action.manual_required',
        'response_action.executed',
        'response_action.unsupported',
        'response_action.rollback_created',
        'response_action.rollback_completed',
        'response_action.rolled_back',
    ]
    assert event_types == expected_order

    escalation_event = next(metadata for event, metadata in timeline_events if event == 'alert.escalated')
    assert escalation_event.get('evidence_reference', {}).get('evidence_id') == 'evidence-1'
    assert escalation_event.get('evidence_reference', {}).get('tx_hash') == '0xabc'
    linked_evidence_event = next(metadata for event, metadata in timeline_events if event == 'evidence.linked')
    assert linked_evidence_event.get('evidence_reference', {}).get('evidence_id') == 'evidence-1'
    assert linked_evidence_event.get('external_references', {}).get('safe_tx_hash') == '0xabc'
    proposed_event = next(metadata for event, metadata in timeline_events if event == 'response_action.proposed')
    assert proposed_event.get('external_references', {}).get('safe_tx_hash') == '0xsafehash'
    manual_required_event = next(metadata for event, metadata in timeline_events if event == 'response_action.manual_required')
    assert manual_required_event.get('external_references') == {
        'safe_tx_hash': None,
        'governance_action_id': None,
        'attestation': None,
        'attestation_hash': None,
    }
    manual_execution_event = next(
        metadata
        for event, metadata in timeline_events
        if event == 'response_action.executed' and metadata.get('execution_state') == 'live_manual_required'
    )
    assert manual_execution_event.get('status') == 'pending'
    unsupported_event = next(metadata for event, metadata in timeline_events if event == 'response_action.unsupported')
    assert unsupported_event.get('execution_state') == 'unsupported'
    rollback_event = next(metadata for event, metadata in timeline_events if event == 'response_action.rolled_back')
    assert rollback_event.get('external_references', {}).get('safe_tx_hash') == '0xsafehash'


def test_audit_chain_detection_alert_incident_action_ids_remain_linked(monkeypatch):
    timeline_events: list[tuple[str, dict]] = []

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
                        'id': 'act-sim',
                        'status': 'pending',
                        'mode': 'simulated',
                        'action_type': 'notify_team',
                        'execution_metadata': {},
                        'execution_artifacts': {},
                        'provider_receipts': [],
                        'incident_id': 'inc-1',
                        'alert_id': 'alert-1',
                        'approved_by_user_id': 'admin-2',
                    }
                )
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    def _capture_timeline(_connection, *, workspace_id, incident_id, event_type, message, actor_user_id, metadata=None):
        timeline_events.append((event_type, metadata or {}))

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1'}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', _capture_timeline)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    pilot.execute_enforcement_action('act-sim', request)

    executed_event = next(metadata for event, metadata in timeline_events if event == 'response_action.executed')
    assert executed_event['response_action_id'] == 'act-sim'
    assert executed_event['alert_id'] == 'alert-1'
    assert executed_event['status'] == 'executed'
