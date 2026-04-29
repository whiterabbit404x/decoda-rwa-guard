from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


def test_live_action_policy_denies_unauthorized_workspace_member() -> None:
    with pytest.raises(HTTPException) as exc:
        pilot._enforce_action_policy_per_mode(
            mode='live',
            operation='create',
            action={'action_type': 'freeze_wallet'},
            workspace_context={'role': 'analyst'},
        )

    assert exc.value.status_code == 403
    assert 'Owner or admin role is required for live action execution' in str(exc.value.detail)


def test_live_execute_with_approval_returns_execution_artifacts_and_audit_metadata(monkeypatch):
    executed: list[tuple[str, object]] = []
    audits: list[dict[str, object]] = []

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result(
                    {
                        'id': 'act-live-1',
                        'workspace_id': 'ws-1',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'notify_team',
                        'approved_by_user_id': 'owner-2',
                        'incident_id': 'inc-1',
                        'alert_id': 'al-1',
                        'execution_metadata': {'origin': 'unit-test'},
                        'execution_artifacts': {},
                        'provider_receipts': [],
                    }
                )
            if 'SELECT role FROM workspace_members WHERE workspace_id = %s AND user_id = %s' in normalized:
                return _Result({'role': 'owner'})
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'owner-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'owner'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_args, **kwargs: audits.append(kwargs))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    payload = pilot.execute_enforcement_action('act-live-1', request)

    assert payload['mode'] == 'live'
    assert payload['status'] in {'executed', 'pending'}
    assert payload['execution_artifacts']['audit_snapshot']['mode'] == 'live'
    assert payload['execution_artifacts']['audit_snapshot']['result_status'] in {'executed', 'pending', 'failed'}
    assert any(item.get('action') == 'enforcement.action.execute' for item in audits)


def test_response_action_payload_keeps_chain_linked_audit_fields() -> None:
    payload = pilot._response_action_payload(
        {
            'id': 'act-chain-1',
            'mode': 'live',
            'status': 'executed',
            'execution_state': 'executed',
            'provider_request_id': 'provider-req-1',
            'provider_response_id': 'provider-resp-1',
            'tx_hash': '0xabc123',
            'safe_tx_hash': '0xsafe',
            'execution_artifacts': {
                'audit_snapshot': {
                    'mode': 'live',
                    'provider_request_id': 'provider-req-1',
                    'provider_response_id': 'provider-resp-1',
                    'tx_hash': '0xabc123',
                    'failure_reason': None,
                }
            },
            'provider_receipts': [{'id': 'r1'}],
        }
    )

    assert payload['execution_provenance']['mode'] == 'live'
    assert payload['execution_provenance']['provider_request_id'] == 'provider-req-1'
    assert payload['execution_provenance']['provider_response_id'] == 'provider-resp-1'
    assert payload['execution_provenance']['tx_hash'] == '0xabc123'
    assert payload['execution_provenance']['execution_artifacts']['audit_snapshot']['mode'] == 'live'
