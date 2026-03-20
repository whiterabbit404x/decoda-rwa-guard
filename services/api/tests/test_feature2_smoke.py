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
THREAT_DATA_DIR = REPO_ROOT / 'services' / 'threat-engine' / 'data'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope='module')
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_feature2_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for Feature 2 smoke tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client(api_main):
    return TestClient(api_main.app)


@pytest.fixture(scope='module')
def sample_payloads() -> dict[str, dict[str, Any]]:
    return {
        'contract': json.loads((THREAT_DATA_DIR / 'sample_contract.json').read_text()),
        'transaction': json.loads((THREAT_DATA_DIR / 'flash_loan_transaction.json').read_text()),
        'market': json.loads((THREAT_DATA_DIR / 'spoofing_market_behavior.json').read_text()),
    }


@pytest.mark.parametrize(
    ('endpoint', 'expected_fields'),
    [
        ('/threat/dashboard', {'source', 'degraded', 'generated_at', 'summary', 'cards', 'active_alerts', 'recent_detections', 'message'}),
        ('/threat/analyze/contract', {'analysis_type', 'score', 'severity', 'matched_patterns', 'recommended_action', 'source', 'degraded'}),
        ('/threat/analyze/transaction', {'analysis_type', 'score', 'severity', 'matched_patterns', 'recommended_action', 'source', 'degraded'}),
        ('/threat/analyze/market', {'analysis_type', 'score', 'severity', 'matched_patterns', 'recommended_action', 'source', 'degraded'}),
    ],
)
def test_feature2_endpoints_return_live_safe_smoke_shapes(client: TestClient, api_main, endpoint: str, expected_fields: set[str], sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    live_analysis = {
        'analysis_type': 'transaction',
        'score': 78,
        'severity': 'high',
        'matched_patterns': [
            {
                'pattern_id': 'smoke:live:1',
                'label': 'Smoke pattern',
                'weight': 20,
                'severity': 'high',
                'reason': 'Live threat-engine payload used for smoke verification.',
                'evidence': {'smoke_test': True},
            }
        ],
        'explanation': 'Smoke-test live payload.',
        'recommended_action': 'review',
        'reasons': ['Live threat-engine payload used for smoke verification.'],
        'metadata': {'source': 'live'},
        'source': 'live',
        'degraded': False,
    }
    live_dashboard = {
        'source': 'live',
        'degraded': False,
        'generated_at': '2026-03-18T10:00:00Z',
        'summary': {
            'average_score': 78,
            'critical_or_high_alerts': 1,
            'blocked_actions': 0,
            'review_actions': 1,
            'market_anomaly_types': ['Smoke anomaly'],
        },
        'cards': [{'label': 'Threat score', 'value': '78', 'detail': 'Smoke test live card.', 'tone': 'high'}],
        'active_alerts': [],
        'recent_detections': [],
        'sample_scenarios': {'flash_loan_transaction': 'Suspicious flash-loan-like transaction'},
        'message': 'Live threat-engine response.',
    }

    if endpoint == '/threat/dashboard':
        monkeypatch.setattr(api_main, 'fetch_threat_dashboard', lambda: live_dashboard)
        response = client.get(endpoint)
    else:
        analysis_type = endpoint.rsplit('/', 1)[-1]
        payload = sample_payloads[analysis_type]
        monkeypatch.setattr(api_main, 'proxy_threat', lambda kind, body: {**live_analysis, 'analysis_type': kind})
        response = client.post(endpoint, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert expected_fields.issubset(body.keys())
    assert body['source'] == 'live'
    assert body['degraded'] is False

    if endpoint == '/threat/dashboard':
        assert body['cards']
        assert body['summary']['market_anomaly_types']
    else:
        assert body['score'] >= 0
        assert body['severity'] in {'low', 'medium', 'high', 'critical'}
        assert isinstance(body['matched_patterns'], list)
        assert body['recommended_action'] in {'allow', 'review', 'block'}


@pytest.mark.parametrize(
    ('endpoint', 'payload_key'),
    [
        ('/threat/dashboard', None),
        ('/threat/analyze/contract', 'contract'),
        ('/threat/analyze/transaction', 'transaction'),
        ('/threat/analyze/market', 'market'),
    ],
)
def test_feature2_endpoints_return_safe_fallback_shapes_when_threat_engine_is_unavailable(client: TestClient, api_main, endpoint: str, payload_key: str | None, sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    if endpoint == '/threat/dashboard':
        monkeypatch.setattr(api_main, 'fetch_threat_dashboard', lambda: None)
        response = client.get(endpoint)
    else:
        monkeypatch.setattr(api_main, 'proxy_threat', lambda kind, body: None)
        response = client.post(endpoint, json=sample_payloads[payload_key])

    assert response.status_code == 200
    body = response.json()
    assert body['source'] == 'fallback'
    assert body['degraded'] is True

    if endpoint == '/threat/dashboard':
        assert body['cards']
        assert body['active_alerts']
        assert body['recent_detections']
        assert body['summary']['market_anomaly_types']
    else:
        assert body['analysis_type'] == endpoint.rsplit('/', 1)[-1]
        assert body['score'] >= 0
        assert body['severity'] in {'low', 'medium', 'high', 'critical'}
        assert 'matched_patterns' in body
        assert isinstance(body['matched_patterns'], list)
        assert body['recommended_action'] in {'allow', 'review', 'block'}
        assert 'metadata' in body
        assert body['metadata']['source'] == 'fallback'


def test_feature2_embedded_local_dashboard_is_live_when_service_url_is_localhost(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmbeddedDashboard:
        def model_dump(self) -> dict[str, Any]:
            return {
                'source': 'live',
                'degraded': False,
                'generated_at': '2026-03-18T10:00:00Z',
                'summary': {'average_score': 71, 'critical_or_high_alerts': 1, 'blocked_actions': 1, 'review_actions': 0, 'market_anomaly_types': ['Embedded anomaly']},
                'cards': [{'label': 'Threat score', 'value': '71', 'detail': 'Embedded', 'tone': 'high'}],
                'active_alerts': [],
                'recent_detections': [],
                'sample_scenarios': {},
                'message': 'Embedded threat response.',
            }

    class _EmbeddedEngine:
        def build_dashboard(self, scenarios: dict[str, Any]) -> _EmbeddedDashboard:
            return _EmbeddedDashboard()

    class _EmbeddedModule:
        engine = _EmbeddedEngine()

        @staticmethod
        def load_demo_requests() -> dict[str, Any]:
            return {'safe_transaction': {}}

    monkeypatch.setattr(api_main, 'THREAT_ENGINE_URL_ENV', None)
    monkeypatch.setattr(api_main, 'THREAT_ENGINE_URL', 'http://localhost:8002')
    monkeypatch.setattr(api_main, 'request_json', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('remote proxy should not be used')))
    monkeypatch.setattr(api_main, 'load_embedded_service_main', lambda service_slug: _EmbeddedModule())

    response = client.get('/threat/dashboard')

    assert response.status_code == 200
    body = response.json()
    assert body['source'] == 'live'
    assert body['degraded'] is False
    details = client.get('/health/details').json()['dependencies']['threat_engine']
    assert details['selected_mode'] == 'embedded_local'
    assert details['last_used_mode'] == 'embedded_local'
