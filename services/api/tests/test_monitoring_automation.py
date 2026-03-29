from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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
