from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
RECONCILIATION_DATA_DIR = REPO_ROOT / 'services' / 'reconciliation-service' / 'data'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope='module')
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_feature4_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for Feature 4 smoke tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client(api_main):
    return TestClient(api_main.app)


@pytest.fixture(scope='module')
def sample_payloads() -> dict[str, dict[str, Any]]:
    return {
        'reconcile': json.loads((RECONCILIATION_DATA_DIR / 'critical_supply_divergence_double_count_risk.json').read_text()),
        'backstop': json.loads((RECONCILIATION_DATA_DIR / 'critical_mismatch_paused_bridge.json').read_text()),
        'incident': json.loads((RECONCILIATION_DATA_DIR / 'incident_record_reconciliation_failure.json').read_text()),
    }


def test_feature4_live_gateway_shapes(client: TestClient, api_main, sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    live_dashboard = {
        'source': 'live',
        'degraded': False,
        'generated_at': '2026-03-18T12:00:00Z',
        'summary': {'reconciliation_status': 'warning', 'severity_score': 42, 'mismatch_amount': 23000, 'stale_ledger_count': 1, 'backstop_decision': 'restricted', 'incident_count': 1},
        'cards': [{'label': 'Reconciliation', 'value': 'warning', 'detail': 'Live', 'tone': 'warning'}],
        'reconciliation_result': {'reconciliation_status': 'warning'},
        'backstop_result': {'backstop_decision': 'restricted'},
        'latest_incidents': [],
        'sample_scenarios': {},
        'message': 'Live resilience response.',
    }
    live_reconcile = {
        'asset_id': 'USTB-2026', 'reconciliation_status': 'critical', 'expected_total_supply': 1000000, 'observed_total_supply': 1460000,
        'normalized_effective_supply': 1191400, 'mismatch_amount': 191400, 'mismatch_percent': 19.14, 'severity_score': 82,
        'duplicate_or_double_count_risk': True, 'stale_ledger_count': 1, 'settlement_lag_ledgers': ['ethereum'],
        'mismatch_summary': ['Live mismatch'], 'recommendations': ['Pause bridge'], 'explainability_summary': 'Live reconcile response.',
        'per_ledger_balances': [], 'ledger_assessments': [], 'source': 'live', 'degraded': False,
    }
    live_backstop = {
        'asset_id': 'USTB-2026', 'backstop_decision': 'paused', 'triggered_safeguards': ['pause bridge / settlement lane'],
        'recommended_actions': ['Pause bridge'], 'operational_status': 'paused', 'trading_status': 'paused', 'bridge_status': 'paused',
        'settlement_status': 'paused', 'explainability_summary': 'Live backstop response.', 'source': 'live', 'degraded': False,
    }
    live_incident = {
        'event_id': 'evt-0099', 'created_at': '2026-03-18T12:01:00Z', 'event_type': 'reconciliation-failure', 'trigger_source': 'reconciliation-engine',
        'related_asset_id': 'USTB-2026', 'affected_assets': ['USTB-2026'], 'affected_ledgers': ['ethereum'], 'severity': 'critical', 'status': 'open',
        'summary': 'Live incident', 'metadata': {}, 'attestation_hash': 'a' * 64, 'fingerprint': 'aaaaaaaaaaaaaaaa', 'source': 'live', 'degraded': False,
    }

    monkeypatch.setattr(api_main, 'fetch_resilience_dashboard', lambda: live_dashboard)
    monkeypatch.setattr(api_main, 'proxy_resilience_post', lambda path, body: live_incident if path == 'incidents/record' else live_backstop if path == 'backstop/evaluate' else live_reconcile)
    monkeypatch.setattr(api_main, 'proxy_resilience_get', lambda path: [live_incident] if path == 'incidents' else live_incident if path == 'incidents/evt-0099' else None)

    assert client.get('/resilience/dashboard').json()['source'] == 'live'
    assert client.post('/resilience/reconcile/state', json=sample_payloads['reconcile']).json()['reconciliation_status'] == 'critical'
    assert client.post('/resilience/backstop/evaluate', json=sample_payloads['backstop']).json()['backstop_decision'] == 'paused'
    assert client.post('/resilience/incidents/record', json=sample_payloads['incident']).json()['event_id'] == 'evt-0099'
    assert client.get('/resilience/incidents').json()[0]['event_id'] == 'evt-0099'
    assert client.get('/resilience/incidents/evt-0099').json()['event_id'] == 'evt-0099'


def test_feature4_gateway_fallback_works_when_reconciliation_service_is_unavailable(client: TestClient, api_main, sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, 'fetch_resilience_dashboard', lambda: None)
    monkeypatch.setattr(api_main, 'proxy_resilience_post', lambda path, body: None)
    monkeypatch.setattr(api_main, 'proxy_resilience_get', lambda path: None)

    dashboard = client.get('/resilience/dashboard')
    reconcile = client.post('/resilience/reconcile/state', json=sample_payloads['reconcile'])
    backstop = client.post('/resilience/backstop/evaluate', json=sample_payloads['backstop'])
    incident = client.post('/resilience/incidents/record', json=sample_payloads['incident'])

    assert dashboard.status_code == 200
    assert dashboard.json()['source'] == 'fallback'
    assert reconcile.status_code == 200
    assert reconcile.json()['source'] == 'fallback'
    assert {'reconciliation_status', 'severity_score', 'mismatch_amount'}.issubset(reconcile.json().keys())
    assert backstop.status_code == 200
    assert backstop.json()['source'] == 'fallback'
    assert incident.status_code == 200
    assert incident.json()['source'] == 'fallback'


def test_feature4_dashboard_fallback_stays_up_when_sample_files_are_missing(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, 'fetch_resilience_dashboard', lambda: None)

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(api_main, 'RECONCILIATION_DATA_DIR', Path(tmpdir))
        dashboard = client.get('/resilience/dashboard')

    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body['source'] == 'fallback'
    assert body['reconciliation_result']['source'] == 'fallback'
    assert body['backstop_result']['source'] == 'fallback'


def test_feature4_embedded_local_dashboard_is_live_when_service_url_is_localhost(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmbeddedEngine:
        def dashboard(self) -> dict[str, Any]:
            return {
                'source': 'live',
                'degraded': False,
                'generated_at': '2026-03-18T12:00:00Z',
                'summary': {'reconciliation_status': 'matched', 'severity_score': 0, 'mismatch_amount': 0, 'stale_ledger_count': 0, 'backstop_decision': 'normal', 'incident_count': 0},
                'cards': [{'label': 'Reconciliation', 'value': 'matched', 'detail': 'Embedded', 'tone': 'matched'}],
                'reconciliation_result': {'reconciliation_status': 'matched'},
                'backstop_result': {'backstop_decision': 'normal'},
                'latest_incidents': [],
                'sample_scenarios': {},
                'message': 'Embedded resilience response.',
            }

    class _EmbeddedModule:
        engine = _EmbeddedEngine()

    monkeypatch.setattr(api_main, 'RECONCILIATION_SERVICE_URL_ENV', None)
    monkeypatch.setattr(api_main, 'RECONCILIATION_SERVICE_URL', 'http://localhost:8005')
    monkeypatch.setattr(api_main, 'request_json', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('remote proxy should not be used')))
    monkeypatch.setattr(api_main, 'load_embedded_service_main', lambda service_slug: _EmbeddedModule())

    response = client.get('/resilience/dashboard')

    assert response.status_code == 200
    body = response.json()
    assert body['source'] == 'live'
    assert body['degraded'] is False
    details = client.get('/health/details').json()['dependencies']['reconciliation_service']
    assert details['selected_mode'] == 'embedded_local'
    assert details['last_used_mode'] == 'embedded_local'
