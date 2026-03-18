from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from app.engine import RiskEngine
from app.main import app
from app.schemas import RiskEvaluationRequest

DATA_DIR = SERVICE_ROOT / 'data'


class RiskEngineUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine()
        self.sample_request = RiskEvaluationRequest.model_validate(
            json.loads((DATA_DIR / 'sample_risk_request.json').read_text())
        )

    def test_high_risk_sample_blocks(self) -> None:
        suspicious_events = json.loads((DATA_DIR / 'suspicious_market_events.json').read_text())
        request_payload = self.sample_request.model_dump()
        request_payload['recent_market_events'] = suspicious_events
        response = self.engine.evaluate(RiskEvaluationRequest.model_validate(request_payload))

        self.assertEqual(response.recommendation, 'BLOCK')
        self.assertGreaterEqual(response.risk_score, 75)
        self.assertTrue(any(rule.category == 'runtime' for rule in response.triggered_rules))
        self.assertTrue(any(rule.category == 'market' for rule in response.triggered_rules))

    def test_benign_request_allows(self) -> None:
        benign_request = {
            'transaction_payload': {
                'from_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'to_address': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                'value': 12000.0,
                'chain_id': 1,
                'token_transfers': [{'token': 'USTB', 'amount': 12000}],
                'metadata': {},
            },
            'decoded_function_call': {
                'function_name': 'deposit',
                'contract_name': 'TreasuryVault',
                'arguments': {'amount': 12000},
                'selectors': ['0xdeadbeef'],
            },
            'wallet_reputation': {
                'score': 92,
                'prior_flags': 0,
                'account_age_days': 340,
                'kyc_verified': True,
                'sanctions_hits': 0,
                'known_safe': True,
            },
            'contract_metadata': {
                'verified_source': True,
                'proxy': False,
                'created_days_ago': 240,
                'audit_count': 3,
                'categories': ['treasury'],
                'static_flags': {},
            },
            'recent_market_events': json.loads((DATA_DIR / 'normal_market_events.json').read_text()),
        }
        response = self.engine.evaluate(RiskEvaluationRequest.model_validate(benign_request))

        self.assertEqual(response.recommendation, 'ALLOW')
        self.assertLess(response.risk_score, 45)
        self.assertEqual(response.triggered_rules, [])


class RiskEngineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_scenarios_endpoint_lists_seed_data(self) -> None:
        response = self.client.get('/v1/risk/scenarios')

        self.assertEqual(response.status_code, 200)
        self.assertEqual({item['scenario'] for item in response.json()}, {'normal', 'suspicious', 'sample-request'})

    def test_evaluate_endpoint_returns_review_or_block_shape(self) -> None:
        payload = json.loads((DATA_DIR / 'sample_risk_request.json').read_text())
        payload['recent_market_events'] = json.loads((DATA_DIR / 'suspicious_market_events.json').read_text())

        response = self.client.post('/v1/risk/evaluate', json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn(body['recommendation'], {'REVIEW', 'BLOCK'})
        self.assertIn('risk_score', body)
        self.assertIn('triggered_rules', body)
        self.assertIn('explanation', body)


if __name__ == '__main__':
    unittest.main()
