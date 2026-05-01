from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
        body = response.json()
        self.assertEqual(body['service'], 'api')
        self.assertIn('backend_build_id', body)
        self.assertEqual(body['backend_build_id'], api_main.HARD_CODED_BACKEND_BUILD_MARKER)
        self.assertIn('backend_git_commit', body)

    def test_health_details_exposes_runtime_marker_and_fixture_checks(self) -> None:
        response = self.client.get('/health/details')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['service'], 'api')
        self.assertIn('version_marker', body)
        self.assertEqual(body['backend_build_id'], api_main.HARD_CODED_BACKEND_BUILD_MARKER)
        self.assertIn('backend_git_commit', body)
        self.assertIn('directories', body)
        self.assertIn('files', body)
        self.assertIn('modes', body)
        self.assertIn('dependencies', body)
        self.assertIn('sample_risk_request.json', body['files']['risk_engine'])

    def test_debug_fixtures_exposes_backend_build_and_fixture_paths(self) -> None:
        response = self.client.get('/debug/fixtures')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['service'], 'api')
        self.assertIn('backend_build_id', body)
        self.assertEqual(body['backend_build_id'], api_main.BACKEND_BUILD_ID)
        self.assertEqual(body['backend_build_id'], api_main.HARD_CODED_BACKEND_BUILD_MARKER)
        self.assertIn('backend_git_commit', body)
        self.assertIn('sample_risk_request.json', body['files']['risk_engine'])
        self.assertIn('critical_supply_divergence_double_count_risk.json', body['files']['reconciliation'])

    def test_load_json_file_returns_default_when_fixture_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            default_payload = {'fallback': True}

            result = api_main.load_json_file(Path(tmpdir), 'missing.json', default_payload)

        self.assertEqual(result, default_payload)
        self.assertIsNot(result, default_payload)

    def test_load_json_file_only_warns_once_for_same_missing_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(api_main.logger, 'warning') as warning_mock:
                api_main.OPTIONAL_FIXTURE_WARNINGS_EMITTED.clear()
                api_main.load_json_file(Path(tmpdir), 'missing.json', {'fallback': True})
                api_main.load_json_file(Path(tmpdir), 'missing.json', {'fallback': True})

        self.assertEqual(warning_mock.call_count, 1)

    def test_risk_dashboard_uses_embedded_engine_when_service_url_is_unset_or_localhost(self) -> None:
        with patch.object(api_main, 'RISK_ENGINE_URL_ENV', None), patch.object(api_main, 'RISK_ENGINE_URL', 'http://localhost:8001'):
            api_main.DEPENDENCY_RUNTIME_STATUS.clear()
            response = self.client.get('/risk/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source'], 'live')
        self.assertFalse(body['degraded'])
        self.assertEqual(body['risk_engine']['mode'], 'embedded_local')
        self.assertEqual(body['risk_engine']['fallback_items'], 0)
        self.assertTrue(all(item['source'] == 'live' for item in body['transaction_queue']))
        self.assertTrue(all('normalized_risk' in item for item in body['transaction_queue']))
        self.assertTrue(all(item['normalized_risk']['contagion_risk_label'] != 'guarded_due_to_stale_telemetry' for item in body['transaction_queue']))
        self.assertEqual(self.client.get('/health/details').json()['dependencies']['risk_engine']['last_used_mode'], 'embedded_local')

    def test_risk_dashboard_uses_remote_proxy_when_real_service_url_is_configured(self) -> None:
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

        with patch.object(api_main, 'RISK_ENGINE_URL_ENV', 'https://risk.example.com'), patch.object(api_main, 'RISK_ENGINE_URL', 'https://risk.example.com'), patch.object(api_main, 'request_json', return_value=live_response) as request_json_mock, patch.object(api_main, 'load_embedded_service_main', side_effect=AssertionError('embedded engine should not be used')):
            api_main.DEPENDENCY_RUNTIME_STATUS.clear()
            response = self.client.get('/risk/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source'], 'live')
        self.assertFalse(body['degraded'])
        self.assertEqual(body['risk_engine']['mode'], 'remote_proxy')
        self.assertEqual(request_json_mock.call_count, 4)
        self.assertEqual(self.client.get('/health/details').json()['dependencies']['risk_engine']['last_used_mode'], 'remote_proxy')

    def test_risk_dashboard_returns_frontend_safe_fallback_shape_when_embedded_engine_raises(self) -> None:
        with patch.object(api_main, 'RISK_ENGINE_URL_ENV', None), patch.object(api_main, 'RISK_ENGINE_URL', 'http://localhost:8001'), patch.object(api_main, 'load_embedded_service_main', side_effect=RuntimeError('risk-engine unavailable')):
            api_main.DEPENDENCY_RUNTIME_STATUS.clear()
            response = self.client.get('/risk/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['degraded'])
        self.assertEqual(body['risk_engine']['live_items'], 0)
        self.assertEqual(body['risk_engine']['fallback_items'], 4)
        self.assertTrue(all(item['source'] == 'fallback' for item in body['transaction_queue']))
        self.assertTrue(all(item['normalized_risk']['contagion_risk_label'] == 'guarded_due_to_stale_telemetry' for item in body['transaction_queue']))
        self.assertTrue(all(item['normalized_risk']['regulatory_evidence_priority'] == 'high' for item in body['transaction_queue']))
        self.assertIn('message', body)
        self.assertIn('summary', body)
        self.assertIn('risk_alerts', body)
        self.assertIn('contract_scan_results', body)
        self.assertIn('decisions_log', body)
        self.assertEqual(self.client.get('/health/details').json()['dependencies']['risk_engine']['last_used_mode'], 'fallback')

    def test_risk_dashboard_stays_up_when_sample_files_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(api_main, 'RISK_ENGINE_DATA_DIR', Path(tmpdir)), patch.object(api_main, 'RISK_ENGINE_URL_ENV', None), patch.object(api_main, 'RISK_ENGINE_URL', 'http://localhost:8001'), patch.object(api_main, 'load_embedded_service_main', side_effect=RuntimeError('risk-engine unavailable')):
                response = self.client.get('/risk/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['source'], 'fallback')
        self.assertEqual(len(body['transaction_queue']), 4)
        self.assertTrue(all(item['tx_hash'] for item in body['transaction_queue']))

    def test_dashboard_registry_seeds_embedded_services_with_non_empty_cards(self) -> None:
        response = self.client.get('/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        service_names = {service['service_name'] for service in body['services']}
        self.assertTrue({'api', 'risk-engine', 'threat-engine', 'compliance-service', 'reconciliation-service'}.issubset(service_names))
        self.assertTrue(any(card['service'] == 'risk-engine' for card in body['cards']))
        self.assertTrue(any(card['service'] == 'threat-engine' for card in body['cards']))

    def test_downstream_debug_endpoint_reports_registry_status_and_payload_truth(self) -> None:
        self.client.get('/risk/dashboard')

        response = self.client.get('/debug/downstream-status')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('dependencies', body)
        risk = body['dependencies']['risk_engine']
        self.assertEqual(risk['selected_mode'], 'embedded_local')
        self.assertEqual(risk['registry_status'], 'ok')
        self.assertEqual(risk['payload_source'], 'live')
        self.assertFalse(risk['degraded'])


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

        self.assertEqual(response.status_code, 503)


class ApiResilienceGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_resilience_dashboard_incidents_include_normalized_risk_fields(self) -> None:
        payload = api_main.fallback_resilience_dashboard()
        payload['latest_incidents'] = [
            {
                'event_id': 'evt-1',
                'created_at': '2026-03-19T10:00:00Z',
                'event_type': 'supply_divergence',
                'trigger_source': 'telemetry',
                'related_asset_id': 'asset_1',
                'affected_assets': ['asset_1'],
                'affected_ledgers': ['ethereum'],
                'severity': 'critical',
                'status': 'open',
                'summary': 'Deterministic stale telemetry incident.',
                'metadata': {},
                'attestation_hash': 'hash',
                'fingerprint': 'fingerprint',
                'source': 'fallback',
                'degraded': True,
            }
        ]
        with patch.object(api_main, 'fetch_resilience_dashboard', return_value=payload):
            response = self.client.get('/resilience/dashboard')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('latest_incidents', body)
        self.assertTrue(body['latest_incidents'])
        normalized = body['latest_incidents'][0]['normalized_risk']
        self.assertEqual(
            sorted(normalized.keys()),
            sorted(['asset_criticality_score', 'exposure_severity', 'market_confidence_impact', 'redemption_liquidity_stress', 'contagion_risk_label', 'regulatory_evidence_priority']),
        )

    def test_canonical_risk_response_stays_type_stable(self) -> None:
        normalized = api_main.build_normalized_risk({'risk_score': 91, 'recommendation': 'BLOCK'}, degraded=False)
        self.assertIsInstance(normalized['asset_criticality_score'], int)
        self.assertIsInstance(normalized['market_confidence_impact'], int)
        self.assertIsInstance(normalized['redemption_liquidity_stress'], int)
        self.assertIn(normalized['exposure_severity'], {'low', 'medium', 'high', 'critical'})
        self.assertIn(normalized['regulatory_evidence_priority'], {'low', 'medium', 'high'})

    def test_contradiction_safe_guard_overrides_allow_confidence(self) -> None:
        normalized = api_main.build_normalized_risk(
            {'risk_score': 12, 'recommendation': 'ALLOW', 'telemetry_guard_disagrees': True},
            degraded=False,
        )
        self.assertEqual(normalized['contagion_risk_label'], 'guarded_due_to_stale_telemetry')
        self.assertEqual(normalized['regulatory_evidence_priority'], 'high')


if __name__ == '__main__':
    unittest.main()
