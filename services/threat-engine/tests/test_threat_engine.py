from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = SERVICE_ROOT / 'data'
PACKAGE_NAME = 'threat_engine_app'


def load_service_package(package_name: str):
    package_dir = SERVICE_ROOT / 'app'
    sys.modules.pop(package_name, None)
    for module_name in [name for name in sys.modules if name.startswith(f'{package_name}.')]:
        sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(
        package_name,
        package_dir / '__init__.py',
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load package {package_name} from {package_dir}.')

    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


load_service_package(PACKAGE_NAME)
ThreatEngine = importlib.import_module(f'{PACKAGE_NAME}.engine').ThreatEngine
app = importlib.import_module(f'{PACKAGE_NAME}.main').app
schemas = importlib.import_module(f'{PACKAGE_NAME}.schemas')
ContractAnalysisRequest = schemas.ContractAnalysisRequest
MarketAnalysisRequest = schemas.MarketAnalysisRequest
TransactionAnalysisRequest = schemas.TransactionAnalysisRequest


class ThreatEngineUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ThreatEngine()

    def test_contract_analysis_returns_score_severity_and_reasons(self) -> None:
        request = ContractAnalysisRequest.model_validate(json.loads((DATA_DIR / 'sample_contract.json').read_text()))

        response = self.engine.analyze_contract(request)

        self.assertGreaterEqual(response.score, 50)
        self.assertIn(response.severity, {'high', 'critical'})
        self.assertTrue(response.reasons)
        self.assertTrue(response.matched_patterns)

    def test_safe_transaction_is_allowed(self) -> None:
        request = TransactionAnalysisRequest.model_validate(json.loads((DATA_DIR / 'safe_transaction.json').read_text()))

        response = self.engine.analyze_transaction(request)

        self.assertEqual(response.recommended_action, 'allow')
        self.assertLess(response.score, 35)

    def test_market_analysis_returns_anomaly_types(self) -> None:
        request = MarketAnalysisRequest.model_validate(json.loads((DATA_DIR / 'wash_trading_market_behavior.json').read_text()))

        response = self.engine.analyze_market(request)

        self.assertGreaterEqual(response.score, 50)
        self.assertIn('anomaly_types', response.metadata)
        self.assertTrue(response.metadata['anomaly_types'])


class ThreatEngineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_endpoint_starts_cleanly(self) -> None:
        response = self.client.get('/health')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service'], 'threat-engine')

    def test_transaction_endpoint_returns_allow_review_or_block(self) -> None:
        payload = json.loads((DATA_DIR / 'flash_loan_transaction.json').read_text())

        response = self.client.post('/analyze/transaction', json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()['recommended_action'], {'allow', 'review', 'block'})

    def test_dashboard_endpoint_returns_cards_and_detections(self) -> None:
        response = self.client.get('/dashboard')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('cards', body)
        self.assertIn('active_alerts', body)
        self.assertIn('recent_detections', body)


if __name__ == '__main__':
    unittest.main()
