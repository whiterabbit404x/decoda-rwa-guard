from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'

sys.path.insert(0, str(REPO_ROOT))


def load_api_module():
    spec = importlib.util.spec_from_file_location('phase1_api_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_main = load_api_module()
app = api_main.app


class _UrlOpenResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._buffer = io.BytesIO(json.dumps(payload).encode('utf-8'))

    def read(self) -> bytes:
        return self._buffer.read()

    def __enter__(self) -> '_UrlOpenResponse':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class ApiRiskDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_endpoint_starts_cleanly(self) -> None:
        response = self.client.get('/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'api')

    def test_risk_dashboard_prefers_live_data_when_risk_engine_responds(self) -> None:
        live_response = {
            'risk_score': 61,
            'recommendation': 'REVIEW',
            'explanation': 'Live risk-engine response used for the dashboard queue.',
            'triggered_rules': [
                {
                    'rule_id': 'runtime:live-response',
                    'category': 'runtime',
                    'score_impact': 18,
                    'severity': 'high',
                    'summary': 'Live risk-engine path returned a runtime signal.',
                    'evidence': {'provider': 'smoke-test'},
                }
            ],
            'category_scores': {'pre_transaction': 12, 'static': 14, 'runtime': 18, 'market': 17},
        }

        with patch.object(api_main, 'evaluate_live_risk', return_value=live_response):
            response = self.client.get('/risk/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source'], 'live')
        self.assertFalse(body['degraded'])
        self.assertEqual(body['risk_engine']['fallback_items'], 0)
        self.assertTrue(all(item['source'] == 'live' for item in body['transaction_queue']))
        self.assertTrue(all(item['risk_score'] == 61 for item in body['transaction_queue']))

    def test_risk_dashboard_returns_frontend_safe_fallback_shape_when_backend_is_unavailable(self) -> None:
        with patch.object(api_main, 'urlopen', side_effect=URLError('risk-engine unavailable')):
            response = self.client.get('/risk/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source'], 'fallback')
        self.assertTrue(body['degraded'])
        self.assertEqual(body['risk_engine']['live_items'], 0)
        self.assertEqual(body['risk_engine']['fallback_items'], 4)
        self.assertTrue(all(item['source'] == 'fallback' for item in body['transaction_queue']))
        self.assertIn('message', body)
        self.assertIn('summary', body)
        self.assertIn('risk_alerts', body)
        self.assertIn('contract_scan_results', body)
        self.assertIn('decisions_log', body)


class ApiThreatGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_threat_dashboard_prefers_live_payload(self) -> None:
        live_payload = {
            'source': 'live',
            'generated_at': '2026-03-18T10:00:00Z',
            'summary': {'average_score': 70},
            'cards': [{'label': 'Threat score', 'value': '70', 'detail': 'Live', 'tone': 'high'}],
            'active_alerts': [],
            'recent_detections': [],
            'sample_scenarios': {},
            'message': 'Live threat-engine response.',
        }

        with patch.object(api_main, 'fetch_threat_dashboard', return_value={**live_payload, 'degraded': False}):
            response = self.client.get('/threat/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source'], 'live')
        self.assertFalse(body['degraded'])
        self.assertIn('cards', body)

    def test_threat_gateway_fallback_works_if_threat_engine_is_down(self) -> None:
        with patch.object(api_main, 'proxy_threat', return_value=None):
            response = self.client.post(
                '/threat/analyze/transaction',
                json={
                    'wallet': '0xdead',
                    'actor': 'bot',
                    'action_type': 'rebalance',
                    'protocol': 'Router',
                    'amount': 1500000,
                    'call_sequence': ['borrow', 'swap', 'repay'],
                    'flags': {'contains_flash_loan': True, 'rapid_drain_indicator': True},
                    'counterparty_reputation': 10,
                    'burst_actions_last_5m': 4,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['degraded'])
        self.assertEqual(body['source'], 'fallback')
        self.assertIn(body['recommended_action'], {'allow', 'review', 'block'})
        self.assertIn('reasons', body)


if __name__ == '__main__':
    unittest.main()
