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


def test_oracle_service_response_includes_detector_status_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setenv('ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    monkeypatch.delenv('ORACLE_SOURCE_URLS', raising=False)
    module = _oracle_module()
    body = module.oracle_observations(asset_identifier='USTB')
    assert body['status'] == 'insufficient_real_evidence'
    assert body['detector_status'] == 'insufficient_real_evidence'
    assert body['provider_configured'] is False
    assert body['oracle_coverage_status'] == 'no_provider_configured'
    assert body['oracle_claim_eligible'] is False
    assert 'oracle_provider_not_configured' in body['oracle_claim_ineligibility_reasons']


def test_oracle_service_marks_unavailable_when_configured_provider_unreachable(monkeypatch) -> None:
    monkeypatch.setenv('ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    monkeypatch.setenv('ORACLE_SOURCE_URLS', 'oracle-a=http://unreachable.local/oracle')
    module = _oracle_module()
    monkeypatch.setattr(module.request, 'urlopen', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('down')))
    body = module.oracle_observations(asset_identifier='USTB')
    assert body['status'] == 'unavailable'
    assert body['detector_status'] == 'insufficient_real_evidence'
    assert body['reason'] == 'configured_provider_unreachable'
    assert body['oracle_coverage_status'] == 'provider_configured_but_unreachable'
    assert body['oracle_claim_eligible'] is False
    assert 'oracle_provider_unreachable' in body['oracle_claim_ineligibility_reasons']
