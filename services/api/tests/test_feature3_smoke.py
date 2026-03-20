from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
COMPLIANCE_DATA_DIR = REPO_ROOT / 'services' / 'compliance-service' / 'data'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope='module')
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_feature3_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for Feature 3 smoke tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client(api_main):
    return TestClient(api_main.app)


@pytest.fixture(scope='module')
def sample_payloads() -> dict[str, dict[str, Any]]:
    return {
        'transfer': json.loads((COMPLIANCE_DATA_DIR / 'compliant_transfer_approved.json').read_text()),
        'residency': json.loads((COMPLIANCE_DATA_DIR / 'denied_residency_restricted_region.json').read_text()),
        'governance': json.loads((COMPLIANCE_DATA_DIR / 'governance_freeze_wallet.json').read_text()),
    }


def test_feature3_live_gateway_shapes(client: TestClient, api_main, sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    live_dashboard = {
        'source': 'live',
        'degraded': False,
        'generated_at': '2026-03-18T11:00:00Z',
        'summary': {'allowlisted_wallet_count': 3, 'triggered_rule_count': 1},
        'cards': [{'label': 'Transfer decision', 'value': 'approved', 'detail': 'Live', 'tone': 'low'}],
        'transfer_screening': {'decision': 'approved', 'risk_level': 'low', 'reasons': ['Live pass'], 'triggered_rules': [], 'recommended_action': 'Proceed', 'wrapper_status': 'wrapper-clear', 'explainability_summary': 'Live', 'policy_snapshot': {}},
        'residency_screening': {'residency_decision': 'allowed', 'policy_violations': [], 'routing_recommendation': 'Live', 'governance_status': 'normal', 'explainability_summary': 'Live', 'allowed_region_outcome': 'us-east'},
        'policy_state': {'allowlisted_wallets': [], 'blocklisted_wallets': [], 'frozen_wallets': [], 'review_required_wallets': [], 'paused_assets': [], 'action_count': 0, 'latest_action_id': None},
        'latest_governance_actions': [],
        'asset_transfer_status': [],
        'sample_scenarios': {},
        'message': 'Live compliance response.',
    }
    live_transfer = {
        'decision': 'approved',
        'risk_level': 'low',
        'reasons': ['Live transfer passed.'],
        'triggered_rules': [],
        'recommended_action': 'Proceed',
        'wrapper_status': 'wrapper-clear',
        'explainability_summary': 'Live transfer response.',
        'policy_snapshot': {},
        'source': 'live',
        'degraded': False,
    }
    live_residency = {
        'residency_decision': 'allowed',
        'policy_violations': [],
        'routing_recommendation': 'Use us-east.',
        'governance_status': 'normal',
        'explainability_summary': 'Live residency response.',
        'allowed_region_outcome': 'us-east',
        'source': 'live',
        'degraded': False,
    }
    live_action = {
        'action_id': 'gov-0001',
        'created_at': '2026-03-18T11:01:00Z',
        'action_type': 'freeze_wallet',
        'target_type': 'wallet',
        'target_id': '0x1',
        'status': 'applied',
        'reason': 'Live governance response.',
        'actor': 'governance-multisig',
        'related_asset_id': 'USTB-2026',
        'metadata': {},
        'attestation_hash': 'abc123',
        'policy_effects': ['Wallet frozen'],
        'source': 'live',
        'degraded': False,
    }

    monkeypatch.setattr(api_main, 'fetch_compliance_dashboard', lambda: live_dashboard)
    monkeypatch.setattr(api_main, 'proxy_compliance', lambda path, body: live_action if path == 'governance/actions' else live_residency if path == 'screen/residency' else live_transfer)
    monkeypatch.setattr(api_main, 'fetch_compliance_policy_state', lambda: {'allowlisted_wallets': [], 'blocklisted_wallets': []})
    monkeypatch.setattr(api_main, 'fetch_compliance_governance_actions', lambda: [live_action])
    monkeypatch.setattr(api_main, 'fetch_compliance_governance_action', lambda action_id: live_action if action_id == 'gov-0001' else None)

    assert client.get('/compliance/dashboard').json()['source'] == 'live'
    assert client.post('/compliance/screen/transfer', json=sample_payloads['transfer']).json()['decision'] == 'approved'
    assert client.post('/compliance/screen/residency', json=sample_payloads['residency']).json()['residency_decision'] == 'allowed'
    assert client.post('/compliance/governance/actions', json=sample_payloads['governance']).json()['action_id'] == 'gov-0001'
    assert client.get('/compliance/policy/state').status_code == 200
    assert client.get('/compliance/governance/actions').json()[0]['action_id'] == 'gov-0001'
    assert client.get('/compliance/governance/actions/gov-0001').json()['action_id'] == 'gov-0001'


def test_feature3_gateway_fallback_works_when_compliance_service_is_unavailable(client: TestClient, api_main, sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, 'fetch_compliance_dashboard', lambda: None)
    monkeypatch.setattr(api_main, 'proxy_compliance', lambda path, body: None)
    monkeypatch.setattr(api_main, 'fetch_compliance_policy_state', lambda: None)
    monkeypatch.setattr(api_main, 'fetch_compliance_governance_actions', lambda: None)
    monkeypatch.setattr(api_main, 'fetch_compliance_governance_action', lambda action_id: None)

    dashboard = client.get('/compliance/dashboard')
    assert dashboard.status_code == 200
    assert dashboard.json()['source'] == 'fallback'

    transfer = client.post('/compliance/screen/transfer', json=sample_payloads['transfer'])
    assert transfer.status_code == 200
    assert transfer.json()['source'] == 'fallback'
    assert {'decision', 'reasons', 'triggered_rules'}.issubset(transfer.json().keys())

    residency = client.post('/compliance/screen/residency', json=sample_payloads['residency'])
    assert residency.status_code == 200
    assert residency.json()['source'] == 'fallback'

    action = client.post('/compliance/governance/actions', json=sample_payloads['governance'])
    assert action.status_code == 200
    assert action.json()['source'] == 'fallback'

    state = client.get('/compliance/policy/state')
    assert state.status_code == 200
    assert 'allowlisted_wallets' in state.json()


def test_feature3_embedded_local_dashboard_is_live_when_service_url_is_localhost(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmbeddedEngine:
        def dashboard(self) -> dict[str, Any]:
            return {
                'source': 'live',
                'degraded': False,
                'generated_at': '2026-03-18T11:00:00Z',
                'summary': {'allowlisted_wallet_count': 2, 'triggered_rule_count': 0},
                'cards': [{'label': 'Transfer decision', 'value': 'approved', 'detail': 'Embedded', 'tone': 'low'}],
                'transfer_screening': {'decision': 'approved'},
                'residency_screening': {'residency_decision': 'allowed'},
                'policy_state': {'allowlisted_wallets': [], 'blocklisted_wallets': [], 'frozen_wallets': [], 'review_required_wallets': [], 'paused_assets': [], 'action_count': 0, 'latest_action_id': None},
                'latest_governance_actions': [],
                'asset_transfer_status': [],
                'sample_scenarios': {},
                'message': 'Embedded compliance response.',
            }

    class _EmbeddedModule:
        engine = _EmbeddedEngine()

    monkeypatch.setattr(api_main, 'COMPLIANCE_SERVICE_URL_ENV', None)
    monkeypatch.setattr(api_main, 'COMPLIANCE_SERVICE_URL', 'http://localhost:8004')
    monkeypatch.setattr(api_main, 'request_json', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('remote proxy should not be used')))
    monkeypatch.setattr(api_main, 'load_embedded_service_main', lambda service_slug: _EmbeddedModule())

    response = client.get('/compliance/dashboard')

    assert response.status_code == 200
    body = response.json()
    assert body['source'] == 'live'
    assert body['degraded'] is False
    details = client.get('/health/details').json()['dependencies']['compliance_service']
    assert details['selected_mode'] == 'embedded_local'
    assert details['last_used_mode'] == 'embedded_local'
