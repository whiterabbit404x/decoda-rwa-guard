from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient


def _oracle_module():
    path = Path(__file__).resolve().parents[2] / 'oracle-service' / 'app' / 'main.py'
    spec = importlib.util.spec_from_file_location('oracle_service_main_test', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_service_returns_insufficient_real_evidence_without_observations(monkeypatch) -> None:
    monkeypatch.delenv('ORACLE_SOURCE_OBSERVATIONS_JSON', raising=False)
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    module = _oracle_module()
    client = TestClient(module.app)
    response = client.get('/oracle/observations')
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'insufficient_real_evidence'
    assert body['observations'] == []
