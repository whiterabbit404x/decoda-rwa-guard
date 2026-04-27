from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row: dict[str, object]):
        self._row = row
        self.executed: list[tuple[str, object]] = []

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.executed.append((normalized, params))
        if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
            return _Result(self._row)
        return _Result()

    def commit(self):
        return None


def _fake_pg(connection: _Connection):
    @contextmanager
    def _inner():
        yield connection

    return _inner


def _base_live_action(**overrides):
    payload = {
        'id': 'act-live',
        'status': 'pending',
        'mode': 'live',
        'action_type': 'revoke_approval',
        'execution_metadata': {},
        'execution_artifacts': {},
        'provider_receipts': [],
        'incident_id': 'inc-1',
        'alert_id': 'alert-1',
        'token_contract': '0x1111111111111111111111111111111111111111',
        'calldata': '0x095ea7b3',
        'chain_network': 'ethereum',
        'approved_by_user_id': 'admin-2',
    }
    payload.update(overrides)
    return payload


def test_live_execute_denies_unauthorized_role(monkeypatch):
    connection = _Connection(_base_live_action())

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg(connection))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'viewer'}))

    with pytest.raises(HTTPException) as exc_info:
        pilot.execute_enforcement_action('act-live', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    assert exc_info.value.status_code == 403
    assert 'Owner or admin role is required' in str(exc_info.value.detail)


def test_live_execute_success_persists_provider_artifacts(monkeypatch):
    connection = _Connection(_base_live_action())
    history_events: list[str] = []
    timeline_events: list[dict[str, object]] = []

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg(connection))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **kwargs: history_events.append(str(kwargs.get('action_type'))))
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', lambda *_a, **kwargs: timeline_events.append(kwargs.get('metadata') or {}))
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
    monkeypatch.setattr(
        pilot,
        '_propose_safe_transaction',
        lambda *_a, **_k: {
            'safe_tx_hash': '0xsafehash',
            'external_request_id': 'safe-request-1',
            'response_code': 201,
            'provider_response': {'ok': True},
        },
    )

    request = SimpleNamespace(
        headers={
            'x-workspace-id': 'ws-1',
            'x-step-up-verified': 'true',
            'x-step-up-authenticated-at': '2026-04-27T00:00:00+00:00',
        }
    )
    response = pilot.execute_enforcement_action('act-live', request)

    assert response['status'] == 'pending'
    assert response['execution_state'] == 'proposed'
    assert response['safe_tx_hash'] == '0xsafehash'
    assert history_events
    assert timeline_events

    update_statement = next(params for statement, params in connection.executed if statement.startswith('UPDATE response_actions SET status ='))
    assert 'safe-request-1' in str(update_statement)
    assert '0xsafehash' in str(update_statement)
    assert 'response_payload_summary' in str(update_statement)
    assert 'final_status' in str(update_statement)


def test_audit_chain_integrity_links_incident_alert_and_action(monkeypatch):
    connection = _Connection(_base_live_action(mode='recommended', approved_by_user_id='admin-2', action_type='freeze_wallet'))
    timeline_events: list[dict[str, object]] = []

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg(connection))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', lambda *_a, **kwargs: timeline_events.append(kwargs.get('metadata') or {}))
    monkeypatch.setattr(
        pilot,
        'resolve_response_action_capability',
        lambda *_a, **_k: {
            'action_type': 'freeze_wallet',
            'supported_modes': ['simulated', 'recommended', 'live'],
            'live_execution_path': 'governance',
            'reason': None,
            'supports_mode': True,
            'mode': 'recommended',
        },
    )

    response = pilot.execute_enforcement_action('act-live', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    assert response['status'] == 'pending'
    assert response['execution_state'] == 'recommended_approved'
    timeline = timeline_events[-1]
    assert timeline['response_action_id'] == 'act-live'
    assert timeline['alert_id'] == 'alert-1'
    assert timeline['mode'] == 'recommended'
