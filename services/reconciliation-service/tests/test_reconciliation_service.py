from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_module():
    for module_name in ['app', 'app.engine', 'app.schemas', 'app.store']:
        sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location('phase1_reconciliation_main', SERVICE_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load reconciliation-service module for tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_main = load_module()
client = TestClient(api_main.app)


def load_json(name: str) -> dict[str, object]:
    return json.loads((DATA_DIR / name).read_text())


def test_reconciliation_returns_status_severity_and_mismatch_fields() -> None:
    response = client.post('/reconcile/state', json=load_json('mild_mismatch_warning.json'))
    body = response.json()

    assert response.status_code == 200
    assert {'reconciliation_status', 'severity_score', 'mismatch_amount', 'mismatch_percent', 'ledger_assessments'}.issubset(body.keys())
    assert body['reconciliation_status'] in {'matched', 'warning', 'critical'}
    assert isinstance(body['ledger_assessments'], list)


def test_stale_ledger_and_double_count_detection_work() -> None:
    stale = client.post('/reconcile/state', json=load_json('stale_private_ledger_data.json')).json()
    critical = client.post('/reconcile/state', json=load_json('critical_supply_divergence_double_count_risk.json')).json()

    assert stale['stale_ledger_count'] >= 1
    assert any(item['status'] in {'penalized', 'flagged'} for item in stale['ledger_assessments'])
    assert critical['duplicate_or_double_count_risk'] is True
    assert critical['reconciliation_status'] == 'critical'


def test_backstop_evaluation_returns_deterministic_safeguards() -> None:
    response = client.post('/backstop/evaluate', json=load_json('critical_mismatch_paused_bridge.json'))
    body = response.json()

    assert response.status_code == 200
    assert body['backstop_decision'] == 'paused'
    assert 'pause bridge / settlement lane' in body['triggered_safeguards']
    assert body['bridge_status'] == 'paused'
    assert body['settlement_status'] == 'paused'


def test_incident_record_creation_and_lookup_work(tmp_path: Path) -> None:
    api_main.engine.store.ledger_path = tmp_path / 'incidents.json'
    api_main.engine.store._ensure_file()

    create = client.post('/incidents/record', json=load_json('incident_record_reconciliation_failure.json'))
    created = create.json()
    lookup = client.get(f"/incidents/{created['event_id']}")
    listing = client.get('/incidents')

    assert create.status_code == 200
    assert created['event_id'].startswith('evt-')
    assert len(created['attestation_hash']) == 64
    assert lookup.status_code == 200
    assert lookup.json()['event_id'] == created['event_id']
    assert listing.status_code == 200
    assert listing.json()[0]['event_id'] == created['event_id']
