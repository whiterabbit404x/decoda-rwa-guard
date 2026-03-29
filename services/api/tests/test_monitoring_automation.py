from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Request

from services.api.app import pilot
from services.api.app.activity_providers import fetch_contract_activity, fetch_market_activity, fetch_wallet_activity
from services.api.app import monitoring_runner
from services.api.app.monitoring_runner import _fallback_response, _normalize_event


def test_target_validation_persists_monitoring_fields() -> None:
    payload = {
        'name': 'Treasury Wallet',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'monitoring_enabled': True,
        'monitoring_mode': 'poll',
        'monitoring_interval_seconds': 120,
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': True,
        'notification_channels': ['dashboard'],
        'monitoring_demo_scenario': 'flash_loan_like',
    }
    validated = pilot._validate_target_payload(payload)
    assert validated['monitoring_enabled'] is True
    assert validated['monitoring_interval_seconds'] == 120
    assert validated['severity_threshold'] == 'high'
    assert validated['auto_create_incidents'] is True
    assert validated['monitoring_demo_scenario'] == 'flash_loan_like'


def test_activity_providers_are_deterministic() -> None:
    target = {
        'id': 'target-1',
        'name': 'Target 1',
        'target_type': 'wallet',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'chain_network': 'ethereum',
        'asset_type': 'USDC',
    }
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    first = fetch_wallet_activity(target, since)
    second = fetch_wallet_activity(target, since)
    assert len(first) == len(second) == 1
    assert first[0].event_id == second[0].event_id

    contract_target = {**target, 'target_type': 'contract'}
    assert fetch_contract_activity(contract_target, since)[0].payload['contract_name']
    market_target = {**target, 'target_type': 'oracle'}
    assert fetch_market_activity(market_target, since)[0].payload['asset']


def test_activity_provider_honors_demo_scenario_profile() -> None:
    target = {
        'id': 'target-2',
        'name': 'Treasury Ops Hot Wallet',
        'target_type': 'wallet',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'chain_network': 'ethereum',
        'asset_type': 'USDC',
        'monitoring_demo_scenario': 'flash_loan_like',
    }
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    event = fetch_wallet_activity(target, since)[0]
    assert event.payload['metadata']['monitoring_demo_scenario'] == 'flash_loan_like'
    assert event.payload['flags']['contains_flash_loan'] is True
    assert event.payload['flags']['rapid_drain_indicator'] is True
    assert event.payload['burst_actions_last_5m'] >= 10


def test_monitoring_normalization_and_fallback_shape() -> None:
    target = {
        'id': '8d8eb228-42ba-4f11-a5d6-3c90166a4d70',
        'workspace_id': '5f230104-1481-4f7f-b176-4022fba95c4f',
        'name': 'Treasury Contract',
        'target_type': 'contract',
        'chain_network': 'ethereum',
        'contract_identifier': '0x2222222222222222222222222222222222222222',
        'severity_preference': 'medium',
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
    }
    event = fetch_contract_activity(target, datetime.now(timezone.utc) - timedelta(hours=1))[0]
    kind, payload = _normalize_event(target, event, 'run-id', {'name': 'Workspace'})
    assert kind == 'contract'
    assert payload['metadata']['monitoring_run_id'] == 'run-id'
    assert payload['metadata']['ingestion_source'] == 'demo'

    fallback = _fallback_response(
        'contract',
        payload,
        diagnostics={
            'fallback_reason': 'live_engine_exception',
            'fallback_exception_type': 'TimeoutError',
            'fallback_exception_message': 'timed out',
        },
    )
    assert fallback['analysis_type'] == 'contract'
    assert fallback['source'] == 'fallback'
    assert 'severity' in fallback
    assert fallback['metadata']['fallback_reason'] == 'live_engine_exception'
    assert fallback['metadata']['fallback_exception_type'] == 'TimeoutError'


def test_monitoring_threat_call_reuses_manual_proxy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        'wallet': '0x1111111111111111111111111111111111111111',
        'actor': 'Treasury Ops',
        'action_type': 'transfer',
        'protocol': 'erc20',
        'amount': 25000,
        'asset': 'USDC',
        'call_sequence': ['transfer'],
        'flags': {},
        'counterparty_reputation': 75,
        'actor_role': 'wallet',
        'expected_actor_roles': ['wallet'],
        'burst_actions_last_5m': 0,
        'metadata': {'event_id': 'evt-1'},
    }
    calls: list[tuple[str, dict[str, object]]] = []

    def _proxy(kind: str, body: dict[str, object]) -> dict[str, object]:
        calls.append((kind, body))
        return {
            'analysis_type': kind,
            'score': 18,
            'severity': 'low',
            'matched_patterns': [],
            'explanation': 'live',
            'recommended_action': 'allow',
            'reasons': [],
            'source': 'live',
            'degraded': False,
            'metadata': {'source': 'live'},
        }

    monkeypatch.setattr('services.api.app.main.proxy_threat', _proxy)
    response, diagnostics = monitoring_runner._threat_call('transaction', payload, target_id='target-1')
    assert response is not None
    assert response['source'] == 'live'
    assert diagnostics['live_invocation'] == 'proxy_threat'
    assert diagnostics['live_invocation_succeeded'] is True
    assert calls and calls[0][0] == 'transaction'


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self):
        self.alert_rows: dict[str, dict[str, object]] = {}
        self.analysis_runs: list[dict[str, object]] = []

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'SELECT id, name FROM workspaces' in normalized:
            return _Result({'id': 'workspace-1', 'name': 'Workspace 1'})
        if 'SELECT id, occurrence_count FROM alerts' in normalized:
            key = f"{params[1]}:{params[2]}"
            return _Result(self.alert_rows.get(key))
        if normalized.startswith('INSERT INTO alerts'):
            alert_id = str(params[0])
            dedupe_signature = str(params[16])
            key = f"{params[4]}:{dedupe_signature}"
            self.alert_rows[key] = {'id': alert_id, 'occurrence_count': 1}
            return _Result()
        if normalized.startswith('UPDATE alerts'):
            alert_id = str(params[5])
            for key, item in self.alert_rows.items():
                if str(item['id']) == alert_id:
                    item['occurrence_count'] = int(item.get('occurrence_count', 1)) + 1
                    self.alert_rows[key] = item
                    break
            return _Result()
        return _Result()


def _build_target(scenario: str) -> dict[str, object]:
    return {
        'id': 'target-1',
        'workspace_id': 'workspace-1',
        'name': 'Treasury Ops Hot Wallet',
        'target_type': 'wallet',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'chain_network': 'ethereum',
        'asset_type': 'USDC',
        'enabled': True,
        'monitoring_enabled': True,
        'monitoring_interval_seconds': 300,
        'severity_threshold': 'medium',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
        'created_by_user_id': 'user-1',
        'updated_by_user_id': 'user-1',
        'monitoring_demo_scenario': scenario,
    }


@pytest.mark.parametrize(
    ('scenario', 'severity', 'expect_alert'),
    [
        ('safe', 'low', False),
        ('medium_risk', 'medium', True),
        ('flash_loan_like', 'critical', True),
    ],
)
def test_process_monitoring_target_demo_scenarios(monkeypatch: pytest.MonkeyPatch, scenario: str, severity: str, expect_alert: bool) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(monitoring_runner, 'persist_analysis_run', lambda *args, **kwargs: 'run-1')
    monkeypatch.setattr(monitoring_runner, '_threat_call', lambda *args, **kwargs: ({'analysis_type': 'transaction', 'score': 88, 'severity': severity, 'matched_patterns': [], 'explanation': 'demo', 'recommended_action': 'review', 'reasons': ['demo'], 'source': 'live', 'degraded': False, 'metadata': {}}, {'live_invocation_succeeded': True}))
    result = monitoring_runner.process_monitoring_target(connection, _build_target(scenario))
    assert result['status'] == 'completed'
    assert (result['alerts_generated'] > 0) is expect_alert


def test_monitoring_alert_dedupe_for_repeated_demo_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(monitoring_runner, 'persist_analysis_run', lambda *args, **kwargs: 'run-1')
    monkeypatch.setattr(monitoring_runner, '_threat_call', lambda *args, **kwargs: ({'analysis_type': 'transaction', 'score': 91, 'severity': 'high', 'matched_patterns': [{'label': 'flash-loan'}], 'explanation': 'demo', 'recommended_action': 'review', 'reasons': ['demo'], 'source': 'live', 'degraded': False, 'metadata': {}}, {'live_invocation_succeeded': True}))
    first = monitoring_runner.process_monitoring_target(connection, _build_target('flash_loan_like'))
    second = monitoring_runner.process_monitoring_target(connection, _build_target('flash_loan_like'))
    assert first['alerts_generated'] == 1
    assert second['alerts_generated'] == 1
    assert len(connection.alert_rows) == 1


def test_patch_monitoring_target_preserves_scenario_on_unrelated_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    class _PatchResult:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _PatchConnection:
        def __init__(self):
            self.row: dict[str, object] = {
                'id': 'target-1',
                'monitoring_enabled': True,
                'monitoring_mode': 'poll',
                'monitoring_interval_seconds': 300,
                'severity_threshold': 'medium',
                'auto_create_alerts': True,
                'auto_create_incidents': False,
                'notification_channels': [],
                'monitoring_demo_scenario': 'flash_loan_like',
                'is_active': True,
            }

        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if normalized.startswith('SELECT id, monitoring_enabled'):
                return _PatchResult(self.row.copy())
            if normalized.startswith('UPDATE targets SET monitoring_enabled'):
                self.row.update(
                    {
                        'monitoring_enabled': bool(params[0]),
                        'monitoring_mode': str(params[1]),
                        'monitoring_interval_seconds': int(params[2]),
                        'severity_threshold': str(params[3]),
                        'auto_create_alerts': bool(params[4]),
                        'auto_create_incidents': bool(params[5]),
                        'monitoring_demo_scenario': params[7],
                        'is_active': bool(params[9]),
                    }
                )
                return _PatchResult()
            if normalized.startswith('SELECT * FROM targets WHERE id = %s'):
                return _PatchResult(self.row.copy())
            raise AssertionError(f'Unexpected query: {normalized}')

        def commit(self):
            return None

    class _ConnCtx:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    connection = _PatchConnection()
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _ConnCtx(connection))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        monitoring_runner,
        '_require_workspace_admin',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': 'workspace-1'}),
    )
    monkeypatch.setattr(monitoring_runner, 'log_audit', lambda *args, **kwargs: None)

    request = Request({'type': 'http', 'headers': []})
    response = monitoring_runner.patch_monitoring_target(
        'target-1',
        {'monitoring_interval_seconds': 90, 'severity_threshold': 'high'},
        request,
    )
    assert response['target']['monitoring_demo_scenario'] == 'flash_loan_like'
    assert response['target']['monitoring_scenario'] == 'flash_loan_like'
    assert response['target']['monitoring_interval_seconds'] == 90
    assert response['target']['severity_threshold'] == 'high'


def test_patch_monitoring_target_accepts_monitoring_profile_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    class _PatchResult:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

        def fetchall(self):
            if self._row is None:
                return []
            return [self._row]

    class _PatchConnection:
        def __init__(self):
            self.row: dict[str, object] = {
                'id': 'target-1',
                'workspace_id': 'workspace-1',
                'name': 'Treasury Ops Hot Wallet',
                'target_type': 'wallet',
                'chain_network': 'ethereum',
                'enabled': True,
                'monitoring_enabled': True,
                'monitoring_mode': 'poll',
                'monitoring_interval_seconds': 300,
                'severity_threshold': 'medium',
                'auto_create_alerts': True,
                'auto_create_incidents': False,
                'notification_channels': [],
                'monitoring_demo_scenario': 'safe',
                'last_checked_at': None,
                'last_run_status': None,
                'last_run_id': None,
                'last_alert_at': None,
                'is_active': True,
            }

        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if normalized.startswith('SELECT id, monitoring_enabled'):
                return _PatchResult(self.row.copy())
            if normalized.startswith('UPDATE targets SET monitoring_enabled'):
                self.row.update({'monitoring_demo_scenario': params[7]})
                return _PatchResult()
            if normalized.startswith('SELECT * FROM targets WHERE id = %s'):
                return _PatchResult(self.row.copy())
            if normalized.startswith('SELECT id, workspace_id, name, target_type'):
                return _PatchResult(self.row.copy())
            raise AssertionError(f'Unexpected query: {normalized}')

        def commit(self):
            return None

    class _ConnCtx:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    connection = _PatchConnection()
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _ConnCtx(connection))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        monitoring_runner,
        '_require_workspace_admin',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': 'workspace-1'}),
    )
    monkeypatch.setattr(monitoring_runner, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        monitoring_runner,
        'authenticate_with_connection',
        lambda _connection, _request: {'id': 'user-1'},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace',
        lambda _connection, _user_id, _workspace_header: {'workspace_id': 'workspace-1', 'workspace': {'id': 'workspace-1'}},
    )

    request = Request({'type': 'http', 'headers': []})
    response = monitoring_runner.patch_monitoring_target(
        'target-1',
        {'monitoring_profile': 'high_risk'},
        request,
    )
    assert response['target']['monitoring_demo_scenario'] == 'high_risk'
    assert response['target']['monitoring_scenario'] == 'high_risk'
    listed = monitoring_runner.list_monitoring_targets(request)
    assert listed['targets'][0]['monitoring_demo_scenario'] == 'high_risk'
    assert listed['targets'][0]['monitoring_scenario'] == 'high_risk'


def test_patch_monitoring_target_accepts_monitoring_scenario_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    class _PatchResult:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _PatchConnection:
        def __init__(self):
            self.row: dict[str, object] = {
                'id': 'target-1',
                'monitoring_enabled': True,
                'monitoring_mode': 'poll',
                'monitoring_interval_seconds': 300,
                'severity_threshold': 'medium',
                'auto_create_alerts': True,
                'auto_create_incidents': False,
                'notification_channels': [],
                'monitoring_demo_scenario': 'safe',
                'is_active': True,
            }

        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if normalized.startswith('SELECT id, monitoring_enabled'):
                return _PatchResult(self.row.copy())
            if normalized.startswith('UPDATE targets SET monitoring_enabled'):
                self.row.update({'monitoring_demo_scenario': params[7]})
                return _PatchResult()
            if normalized.startswith('SELECT * FROM targets WHERE id = %s'):
                return _PatchResult(self.row.copy())
            raise AssertionError(f'Unexpected query: {normalized}')

        def commit(self):
            return None

    class _ConnCtx:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    connection = _PatchConnection()
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _ConnCtx(connection))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        monitoring_runner,
        '_require_workspace_admin',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': 'workspace-1'}),
    )
    monkeypatch.setattr(monitoring_runner, 'log_audit', lambda *args, **kwargs: None)

    request = Request({'type': 'http', 'headers': []})
    response = monitoring_runner.patch_monitoring_target(
        'target-1',
        {'monitoring_scenario': 'medium_risk'},
        request,
    )
    assert response['target']['monitoring_demo_scenario'] == 'medium_risk'
    assert response['target']['monitoring_scenario'] == 'medium_risk'


def test_update_target_preserves_existing_monitoring_scenario_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def __init__(self):
            self.updated = False

        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if normalized.startswith('SELECT * FROM targets WHERE id = %s'):
                return _Result(
                    {
                        'id': 'target-1',
                        'workspace_id': 'workspace-1',
                        'monitoring_demo_scenario': 'flash_loan_like',
                    }
                )
            if normalized.startswith('UPDATE targets SET name = %s'):
                self.updated = True
                return _Result()
            if normalized.startswith('DELETE FROM target_tags WHERE target_id = %s'):
                return _Result()
            raise AssertionError(f'Unexpected query: {normalized}')

        def commit(self):
            return None

    class _ConnCtx:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    captured_payload: dict[str, object] = {}

    def _fake_validate(payload: dict[str, object]) -> dict[str, object]:
        captured_payload.update(payload)
        return {
            'name': 'Treasury Ops Hot Wallet',
            'target_type': 'wallet',
            'chain_network': 'ethereum',
            'contract_identifier': None,
            'wallet_address': '0x1111111111111111111111111111111111111111',
            'asset_type': None,
            'owner_notes': None,
            'severity_preference': 'medium',
            'enabled': True,
            'monitoring_enabled': True,
            'monitoring_mode': 'poll',
            'monitoring_interval_seconds': 300,
            'severity_threshold': 'high',
            'auto_create_alerts': True,
            'auto_create_incidents': False,
            'notification_channels': [],
            'monitoring_demo_scenario': str(payload.get('monitoring_demo_scenario') or ''),
            'is_active': True,
            'tags': [],
        }

    connection = _Connection()
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _ConnCtx(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_admin',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': 'workspace-1'}),
    )
    monkeypatch.setattr(pilot, '_validate_target_payload', _fake_validate)
    monkeypatch.setattr(pilot, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)

    request = Request({'type': 'http', 'headers': []})
    response = pilot.update_target(
        'target-1',
        {
            'name': 'Treasury Ops Hot Wallet',
            'target_type': 'wallet',
            'chain_network': 'ethereum',
            'wallet_address': '0x1111111111111111111111111111111111111111',
        },
        request,
    )
    assert captured_payload['monitoring_demo_scenario'] == 'flash_loan_like'
    assert response['monitoring_demo_scenario'] == 'flash_loan_like'
    assert response['monitoring_scenario'] == 'flash_loan_like'
    assert connection.updated is True


def test_flash_loan_like_fallback_scores_higher_than_safe() -> None:
    safe_target = _build_target('safe')
    flash_target = _build_target('flash_loan_like')
    workspace = {'name': 'Workspace'}
    run_id = 'run-1'
    safe_event = fetch_wallet_activity(safe_target, datetime.now(timezone.utc) - timedelta(hours=1))[0]
    flash_event = fetch_wallet_activity(flash_target, datetime.now(timezone.utc) - timedelta(hours=1))[0]

    _, safe_payload = _normalize_event(safe_target, safe_event, run_id, workspace)
    _, flash_payload = _normalize_event(flash_target, flash_event, run_id, workspace)

    safe_result = _fallback_response('transaction', safe_payload)
    flash_result = _fallback_response('transaction', flash_payload)
    assert flash_result['score'] > safe_result['score']
    assert flash_result['severity'] in {'high', 'critical'}
    assert safe_result['severity'] == 'low'


def test_medium_risk_provider_payload_contains_elevated_signals() -> None:
    medium_target = _build_target('medium_risk')
    medium_event = fetch_wallet_activity(medium_target, datetime.now(timezone.utc) - timedelta(hours=1))[0]
    assert medium_event.payload['amount'] >= 200000
    assert medium_event.payload['burst_actions_last_5m'] >= 8
    assert medium_event.payload['flags']['untrusted_contract'] is True


def test_process_monitoring_target_logs_selected_scenario(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(monitoring_runner, 'persist_analysis_run', lambda *args, **kwargs: 'run-1')
    monkeypatch.setattr(
        monitoring_runner,
        '_threat_call',
        lambda *args, **kwargs: (
            {
                'analysis_type': 'transaction',
                'score': 66,
                'severity': 'high',
                'matched_patterns': [],
                'explanation': 'demo',
                'recommended_action': 'review',
                'reasons': ['demo'],
                'source': 'live',
                'degraded': False,
                'metadata': {},
            },
            {'live_invocation_succeeded': True},
        ),
    )
    with caplog.at_level('INFO'):
        monitoring_runner.process_monitoring_target(connection, _build_target('flash_loan_like'))
    assert 'monitoring target fetched' in caplog.text
    assert 'monitoring scenario selected' in caplog.text
