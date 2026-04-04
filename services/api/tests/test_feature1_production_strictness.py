from __future__ import annotations

from fastapi.testclient import TestClient

from services.api.app import main


def test_threat_analysis_endpoint_fails_closed_without_provider(monkeypatch) -> None:
    monkeypatch.setattr(main, 'proxy_threat', lambda *_args, **_kwargs: None)
    client = TestClient(main.app)
    response = client.post('/threat/analyze/transaction', json={'tx_hash': '0x1'})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'THREAT_ENGINE_UNAVAILABLE'
