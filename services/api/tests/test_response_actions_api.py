from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import HTTPException

from services.api.app import pilot


def test_create_response_action_translates_legacy_payload_and_writes_history(monkeypatch):
    executed: list[tuple[str, object]] = []

    class _Result:
        def __init__(self, row=None, rows=None):
            self._row = row
            self._rows = rows or []

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            executed.append((' '.join(str(statement).split()), params))
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    payload = {
        'action_type': 'revoke_erc20_approval',
        'dry_run': True,
        'incident_id': 'inc-1',
        'alert_id': 'alert-1',
        'params': {
            'token_contract': '0x1111111111111111111111111111111111111111',
            'spender': '0x2222222222222222222222222222222222222222',
            'safe_tx_hash': '0xseed-safe',
            'governance_action_id': 'gov-seed-1',
            'attestation_hash': 'att-seed-1',
        },
    }
    response = pilot.create_enforcement_action(payload, request)

    assert response['action_type'] == 'revoke_approval'
    assert response['dry_run'] is True
    insert_calls = [params for statement, params in executed if 'INSERT INTO response_actions' in statement]
    assert insert_calls
    assert insert_calls[0][4] == 'revoke_approval'
    assert insert_calls[0][5] == 'simulated'
    assert insert_calls[0][6] == 'pending'
    assert insert_calls[0][14] == 'simulated_executed'
    history_calls = [params for statement, params in executed if 'INSERT INTO action_history' in statement]
    assert history_calls
    assert any(params[6] == 'response_action.created' for params in history_calls)
    timeline_calls = [params for statement, params in executed if 'INSERT INTO incident_timeline' in statement]
    assert any(params[3] == 'response_action.created' for params in timeline_calls)
    assert any('0xseed-safe' in str(params[6]) for params in timeline_calls)
    assert any('gov-seed-1' in str(params[6]) for params in timeline_calls)
    assert any('att-seed-1' in str(params[6]) for params in timeline_calls)


def test_execute_response_action_returns_back_compat_dry_run_flag(monkeypatch):
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
                        'id': 'act-1',
                        'status': 'pending',
                        'mode': 'simulated',
                        'action_type': 'notify_team',
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

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.execute_enforcement_action('act-1', request)

    assert response['status'] == 'executed'
    assert response['dry_run'] is True
    assert any('UPDATE response_actions SET status = \'executed\', execution_state = %s' in statement for statement, _ in executed)
    history_calls = [params for statement, params in executed if 'INSERT INTO action_history' in statement]
    assert any(params[6] == 'response_action.executed' for params in history_calls)
    assert any(params[6] == 'incident.response_action_executed' for params in history_calls)
    assert any(params[6] == 'alert.response_action_executed' for params in history_calls)


def test_create_live_unsupported_action_returns_structured_422_and_does_not_insert(monkeypatch):
    executed: list[tuple[str, object]] = []

    class _Result:
        def fetchone(self):
            return None

    class _Connection:
        def execute(self, statement, params=None):
            executed.append((' '.join(str(statement).split()), params))
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    payload = {'action_type': 'block_transaction', 'mode': 'live', 'status': 'pending'}
    try:
        pilot.create_enforcement_action(payload, request)
        raise AssertionError('Expected HTTPException for unsupported live action creation.')
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail['code'] == 'RESPONSE_ACTION_UNSUPPORTED_CAPABILITY'
        assert exc.detail['status'] == 'failed'
        assert exc.detail['execution_state'] == 'unsupported'
        assert exc.detail['action_type'] == 'block_transaction'
        assert exc.detail['reason'] == 'Unsupported live action'

    assert not any('INSERT INTO response_actions' in statement for statement, _ in executed)


def test_list_response_actions_returns_supported_fields(monkeypatch):
    class _Result:
        def __init__(self, rows=None):
            self._rows = rows or []

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'FROM response_actions' in normalized:
                return _Result(rows=[{
                    'id': 'act-1',
                    'action_type': 'freeze_wallet',
                    'mode': 'simulated',
                    'status': 'pending',
                    'result_summary': 'Queued',
                    'operator_notes': 'note',
                    'created_at': '2026-01-01T00:00:00Z',
                    'executed_at': None,
                    'incident_id': 'inc-1',
                    'alert_id': 'alert-1',
                }])
            return _Result()

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'admin-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1'})

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.list_enforcement_actions(request, incident_id='inc-1')
    action = response['actions'][0]
    assert action['action_type'] == 'freeze_wallet'
    assert action['status'] == 'pending'
    assert action['mode'] == 'simulated'


def test_execute_live_unsupported_action_returns_structured_error_without_executed_state(monkeypatch):
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

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    try:
        pilot.execute_enforcement_action('act-unsupported', request)
        raise AssertionError('Expected HTTPException for unsupported live execution.')
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail['code'] == 'RESPONSE_ACTION_UNSUPPORTED_EXECUTOR'
        assert exc.detail['status'] == 'failed'
        assert exc.detail['execution_state'] == 'unsupported'
        assert exc.detail['reason'] == 'Unsupported live action'

    assert any(
        "UPDATE response_actions SET status = %s, execution_state = %s, execution_metadata = %s::jsonb, execution_artifacts = %s::jsonb, provider_receipts = %s::jsonb, result_summary = %s WHERE id = %s" in statement
        and params[0] == 'failed'
        and params[1] == 'unsupported'
        for statement, params in executed
    )
    timeline_calls = [params for statement, params in executed if 'INSERT INTO incident_timeline' in statement]
    assert any(params[3] == 'response_action.unsupported' for params in timeline_calls)
    assert any('external_references' in str(params[6]) for params in timeline_calls)
    assert not any("SET status = 'executed'" in statement for statement, _ in executed)


def test_execute_live_revoke_approval_returns_proposed_state_with_safe_tx_hash_and_history(monkeypatch):
    executed: list[tuple[str, object]] = []
    audit_events: list[str] = []

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
                        'approved_by_user_id': 'admin-2',
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
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(
        pilot,
        '_propose_safe_transaction',
        lambda *_a, **_k: {
            'safe_tx_hash': '0xsafehash',
            'external_request_id': 'safe-req-1',
            'response_code': 201,
            'provider_response': {'id': 'safe-req-1'},
        },
    )
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
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **kwargs: audit_events.append(str(kwargs.get('action'))))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.execute_enforcement_action('act-safe', request)

    assert response['status'] == 'pending'
    assert response['execution_state'] == 'proposed'
    assert response['safe_tx_hash'] == '0xsafehash'
    assert response['live_execution_path'] == 'safe'
    assert any(
        "SET status = 'pending', execution_state = %s, safe_tx_hash = COALESCE(%s, safe_tx_hash), execution_metadata = %s::jsonb, execution_artifacts = %s::jsonb, provider_receipts = %s::jsonb" in statement
        and params[0] == 'proposed'
        and params[1] == '0xsafehash'
        for statement, params in executed
    )
    history_calls = [params for statement, params in executed if 'INSERT INTO action_history' in statement]
    assert any(params[6] == 'response_action.executed' for params in history_calls)
    assert any(params[6] == 'incident.response_action_executed' for params in history_calls)
    timeline_calls = [params for statement, params in executed if 'INSERT INTO incident_timeline' in statement]
    assert any(params[3] == 'response_action.proposed' for params in timeline_calls)
    assert any('0xsafehash' in str(params[6]) for params in timeline_calls)
    assert 'enforcement.action.execute' in audit_events


def test_execute_live_freeze_wallet_writes_governance_metadata_and_timeline(monkeypatch):
    executed: list[tuple[str, object]] = []
    audit_events: list[str] = []

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
                        'approved_by_user_id': 'admin-2',
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
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(
        pilot,
        '_submit_freeze_wallet_governance_action',
        lambda *_a, **_k: {
            'action_id': 'gov-123',
            'attestation_hash': 'attest-123',
            'policy_effects': ['Wallet frozen'],
        },
    )
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **kwargs: audit_events.append(str(kwargs.get('action'))))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.execute_enforcement_action('act-governance', request)

    assert response['status'] == 'pending'
    assert response['execution_state'] == 'proposed'
    assert response['live_execution_path'] == 'governance'
    update_calls = [params for statement, params in executed if 'SET status = \'pending\', execution_state = %s' in statement and 'execution_metadata' in statement]
    assert update_calls
    assert update_calls[0][0] == 'proposed'
    assert 'gov-123' in str(update_calls[0][2])
    assert 'attest-123' in str(update_calls[0][2])
    assert 'Wallet frozen' in str(update_calls[0][2])
    timeline_calls = [params for statement, params in executed if 'INSERT INTO incident_timeline' in statement]
    assert any(params[3] == 'response_action.proposed' for params in timeline_calls)
    assert any('governance_action_id' in str(params[6]) for params in timeline_calls)
    assert any('gov-123' in str(params[6]) for params in timeline_calls)
    assert any('attestation_hash' in str(params[6]) for params in timeline_calls)
    assert any('attest-123' in str(params[6]) for params in timeline_calls)
    assert 'enforcement.action.execute' in audit_events


def test_execute_live_manual_only_action_returns_manual_required_state(monkeypatch):
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
                        'id': 'act-manual',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'disable_monitored_system',
                        'execution_metadata': {},
                        'incident_id': 'inc-3',
                        'alert_id': 'alert-3',
                        'approved_by_user_id': 'admin-2',
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
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.execute_enforcement_action('act-manual', request)

    assert response['status'] == 'pending'
    assert response['execution_state'] == 'live_manual_required'
    assert response['live_execution_path'] == 'manual_only'
    assert response['reason'] == 'Manual-only in live mode'
    assert any(
        "SET status = 'pending', execution_state = %s" in statement and params[0] == 'live_manual_required'
        for statement, params in executed
    )
    timeline_calls = [params for statement, params in executed if 'INSERT INTO incident_timeline' in statement]
    assert any(params[3] == 'response_action.manual_required' for params in timeline_calls)
    assert not any("SET status = 'executed'" in statement for statement, _ in executed)


def test_execute_live_action_denies_same_user_as_approver(monkeypatch):
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
                        'id': 'act-live-self-approved',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'revoke_approval',
                        'execution_metadata': {},
                        'incident_id': 'inc-1',
                        'alert_id': 'alert-1',
                        'approved_by_user_id': 'admin-1',
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
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    try:
        pilot.execute_enforcement_action('act-live-self-approved', request)
        raise AssertionError('Expected HTTPException when executor matches approver.')
    except HTTPException as exc:
        assert exc.status_code == 403
        assert 'separate approver and executor' in str(exc.detail)


def test_execute_live_action_success_includes_execution_evidence_fields(monkeypatch):
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
                        'id': 'act-live-success',
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
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1', 'mfa_enabled': False}, {'workspace_id': 'ws-1', 'role': 'admin'}))
    monkeypatch.setattr(
        pilot,
        '_propose_safe_transaction',
        lambda *_a, **_k: {'safe_tx_hash': '0xsafehash', 'external_request_id': 'safe-req-44', 'response_code': 201},
    )
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
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    payload = pilot.execute_enforcement_action('act-live-success', request)

    assert payload['mode'] == 'live'
    assert payload['execution_evidence']['safe_tx_hash'] == '0xsafehash'
    assert payload['execution_evidence']['provider_request_id'] == 'safe-req-44'
    assert payload['execution_evidence']['execution_state'] == 'proposed'


def test_list_response_action_capabilities_returns_workspace_scoped_payload(monkeypatch):
    @contextmanager
    def _fake_pg():
        class _Connection:
            pass
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'admin-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1'})
    monkeypatch.setattr(pilot, '_safe_execution_configured', lambda: True)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.list_response_action_capabilities(request)

    assert response['workspace_id'] == 'ws-1'
    capabilities = {row['action_type']: row for row in response['actions']}
    assert capabilities['block_transaction']['live_execution_path'] == 'unsupported'
    assert capabilities['block_transaction']['reason'] == 'Unsupported live action'
    assert capabilities['disable_monitored_system']['live_execution_path'] == 'manual_only'
    assert capabilities['disable_monitored_system']['reason'] == 'Manual-only in live mode'
    assert capabilities['revoke_approval']['live_execution_path'] == 'safe'
    assert capabilities['freeze_wallet']['live_execution_path'] == 'governance'


def test_execute_live_action_requires_explicit_approval(monkeypatch):
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
                        'id': 'act-live',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'freeze_wallet',
                        'execution_metadata': {},
                        'incident_id': 'inc-1',
                        'alert_id': 'alert-1',
                        'approved_by_user_id': None,
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
    monkeypatch.setattr(
        pilot,
        '_require_workspace_admin',
        lambda *_: (
            {'id': 'admin-1', 'mfa_enabled': True},
            {'workspace_id': 'ws-1', 'role': 'admin'},
        ),
    )
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    try:
        pilot.execute_enforcement_action('act-live', request)
        raise AssertionError('Expected HTTPException for missing approval.')
    except HTTPException as exc:
        assert exc.status_code == 409
        assert 'requires explicit approval' in str(exc.detail)


def test_execute_live_action_requires_step_up_when_available(monkeypatch):
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
                        'id': 'act-live',
                        'status': 'pending',
                        'mode': 'live',
                        'action_type': 'freeze_wallet',
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

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_admin',
        lambda *_: (
            {'id': 'admin-1', 'mfa_enabled': True},
            {'workspace_id': 'ws-1', 'role': 'admin'},
        ),
    )
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    try:
        pilot.execute_enforcement_action('act-live', request)
        raise AssertionError('Expected HTTPException for missing step-up auth.')
    except HTTPException as exc:
        assert exc.status_code == 403
        assert 'Step-up authentication is required' in str(exc.detail)
