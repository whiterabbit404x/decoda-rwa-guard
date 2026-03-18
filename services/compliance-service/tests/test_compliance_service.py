from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'

sys.path.insert(0, str(REPO_ROOT))


def load_module(tmp_path: Path):
    for module_name in ['app', 'app.engine', 'app.schemas', 'app.store']:
        sys.modules.pop(module_name, None)
    os.environ['COMPLIANCE_LEDGER_PATH'] = str(tmp_path / 'ledger.json')
    os.environ['COMPLIANCE_POLICY_PATH'] = str(tmp_path / 'policy.json')
    sys.path.insert(0, str(MAIN_PATH.parents[1]))
    spec = importlib.util.spec_from_file_location('phase1_compliance_main', MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load compliance-service module for tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module(tmp_path: Path):
    return load_module(tmp_path)


@pytest.fixture()
def client(module):
    return TestClient(module.app)


@pytest.fixture(scope='module')
def payloads() -> dict[str, dict[str, object]]:
    return {
        path.stem: json.loads(path.read_text())
        for path in DATA_DIR.glob('*.json')
    }


def test_transfer_screening_returns_decision_reasons_and_triggered_rules(client: TestClient, payloads: dict[str, dict[str, object]]) -> None:
    response = client.post('/screen/transfer', json=payloads['compliant_transfer_approved'])
    assert response.status_code == 200
    body = response.json()
    assert body['decision'] == 'approved'
    assert body['risk_level'] == 'low'
    assert body['reasons']
    assert body['triggered_rules']
    assert body['wrapper_status'] == 'wrapper-clear'


def test_sanctions_and_blocklist_detection_work(client: TestClient, payloads: dict[str, dict[str, object]]) -> None:
    sanctions_response = client.post('/screen/transfer', json=payloads['blocked_transfer_sanctions'])
    assert sanctions_response.status_code == 200
    sanctions_body = sanctions_response.json()
    assert sanctions_body['decision'] == 'blocked'
    assert any(rule['rule_id'] == 'sanctions-screen' and rule['outcome'] == 'block' for rule in sanctions_body['triggered_rules'])

    blocklist_response = client.post('/screen/transfer', json=payloads['blocked_transfer_blocklist'])
    assert blocklist_response.status_code == 200
    blocklist_body = blocklist_response.json()
    assert blocklist_body['decision'] == 'blocked'
    assert any(rule['rule_id'] == 'wallet-blocklist' and rule['outcome'] == 'block' for rule in blocklist_body['triggered_rules'])


def test_kyc_and_jurisdiction_review_logic_work(client: TestClient, payloads: dict[str, dict[str, object]]) -> None:
    kyc_response = client.post('/screen/transfer', json=payloads['review_transfer_incomplete_kyc'])
    assert kyc_response.status_code == 200
    assert kyc_response.json()['decision'] == 'review'

    jurisdiction_response = client.post('/screen/transfer', json=payloads['review_transfer_restricted_jurisdiction'])
    assert jurisdiction_response.status_code == 200
    body = jurisdiction_response.json()
    assert body['decision'] == 'review'
    assert any(rule['rule_id'] == 'jurisdiction-policy' and rule['outcome'] == 'review' for rule in body['triggered_rules'])


def test_residency_decision_logic_works(client: TestClient, payloads: dict[str, dict[str, object]]) -> None:
    response = client.post('/screen/residency', json=payloads['denied_residency_restricted_region'])
    assert response.status_code == 200
    body = response.json()
    assert body['residency_decision'] == 'denied'
    assert body['policy_violations']
    assert body['allowed_region_outcome'] == 'eu-west'


def test_governance_action_creation_and_policy_state_changes(client: TestClient, payloads: dict[str, dict[str, object]]) -> None:
    freeze_response = client.post('/governance/actions', json=payloads['governance_freeze_wallet'])
    assert freeze_response.status_code == 200
    freeze_body = freeze_response.json()
    assert freeze_body['action_id'].startswith('gov-')
    assert freeze_body['attestation_hash']

    allowlist_response = client.post('/governance/actions', json=payloads['governance_allowlist_wallet'])
    assert allowlist_response.status_code == 200

    pause_response = client.post('/governance/actions', json=payloads['governance_pause_asset'])
    assert pause_response.status_code == 200

    policy_state = client.get('/policy/state')
    assert policy_state.status_code == 200
    policy_body = policy_state.json()
    assert payloads['governance_freeze_wallet']['target_id'] in policy_body['frozen_wallets']
    assert payloads['governance_allowlist_wallet']['target_id'] in policy_body['allowlisted_wallets']
    assert payloads['governance_pause_asset']['related_asset_id'] in policy_body['paused_assets']

    paused_transfer = client.post('/screen/transfer', json=payloads['transfer_blocked_asset_paused'])
    assert paused_transfer.status_code == 200
    assert paused_transfer.json()['decision'] == 'blocked'

    actions = client.get('/governance/actions')
    assert actions.status_code == 200
    assert len(actions.json()) == 3
