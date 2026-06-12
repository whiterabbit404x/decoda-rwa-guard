from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.monitoring_runner import list_target_telemetry

REPO_ROOT = Path(__file__).resolve().parents[3]

# --- Route spelling ---

def test_telemetry_route_is_spelled_correctly_in_main():
    content = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    assert "@app.get('/monitoring/targets/{target_id}/telemetry'" in content
    assert "telemtry" not in content


def test_telemetry_function_is_exported_from_monitoring_runner():
    import services.api.app.monitoring_runner as mr
    assert hasattr(mr, 'list_target_telemetry')
    assert callable(mr.list_target_telemetry)


# --- UUID validation ---

def test_invalid_uuid_raises_400():
    fake_request = MagicMock()
    fake_request.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        list_target_telemetry(fake_request, target_id='not-a-uuid')
    assert exc_info.value.status_code == 400
    assert 'UUID' in str(exc_info.value.detail)


def test_empty_string_uuid_raises_400():
    fake_request = MagicMock()
    fake_request.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        list_target_telemetry(fake_request, target_id='')
    assert exc_info.value.status_code == 400


def test_non_uuid_does_not_reach_database(monkeypatch):
    db_called = []

    def fake_pg_connection():
        db_called.append(True)
        return MagicMock()

    monkeypatch.setattr('services.api.app.monitoring_runner.pg_connection', fake_pg_connection)
    fake_request = MagicMock()
    fake_request.headers = {}
    with pytest.raises(HTTPException):
        list_target_telemetry(fake_request, target_id='invalid-uuid-value')
    assert not db_called, 'DB must not be called for invalid UUID'


# --- Empty state via TestClient ---

def _make_empty_telemetry_response(target_id: str) -> dict:
    return {
        'telemetry': [],
        'target_id': target_id,
        'workspace_id': str(uuid.uuid4()),
        'live_telemetry_ready': False,
        'message': 'No live telemetry has been persisted for this target yet.',
    }


def _make_telemetry_rows(target_id: str, workspace_id: str) -> dict:
    row_id = str(uuid.uuid4())
    return {
        'telemetry': [
            {
                'id': row_id,
                'workspace_id': workspace_id,
                'target_id': target_id,
                'provider_type': 'rpc',
                'source_type': 'target_event',
                'evidence_source': 'https://rpc.example.com',
                'chain_id': 'ethereum',
                'block_number': 19000000,
                'observed_at': '2026-05-26T10:00:00+00:00',
                'ingested_at': '2026-05-26T10:00:01+00:00',
                'payload_json': {'tx_hash': '0xabc', 'block_number': 19000000},
            }
        ],
        'target_id': target_id,
        'workspace_id': workspace_id,
        'live_telemetry_ready': True,
    }


def test_no_telemetry_returns_200_empty_state(monkeypatch):
    valid_id = str(uuid.uuid4())
    client = TestClient(api_main.app)
    monkeypatch.setattr(
        api_main,
        'list_target_telemetry',
        lambda request, target_id, limit=50, q=None: _make_empty_telemetry_response(target_id),
    )
    response = client.get(f'/monitoring/targets/{valid_id}/telemetry')
    assert response.status_code == 200
    data = response.json()
    assert data['telemetry'] == []
    assert data['live_telemetry_ready'] is False
    assert 'No live telemetry' in data['message']
    assert data['target_id'] == valid_id


def test_with_telemetry_returns_200_with_rows(monkeypatch):
    valid_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    client = TestClient(api_main.app)
    monkeypatch.setattr(
        api_main,
        'list_target_telemetry',
        lambda request, target_id, limit=50, q=None: _make_telemetry_rows(target_id, ws_id),
    )
    response = client.get(f'/monitoring/targets/{valid_id}/telemetry')
    assert response.status_code == 200
    data = response.json()
    assert len(data['telemetry']) == 1
    assert data['live_telemetry_ready'] is True
    row = data['telemetry'][0]
    for field in ('id', 'provider_type', 'source_type', 'evidence_source', 'chain_id', 'block_number', 'observed_at', 'payload_json'):
        assert field in row, f'Expected field {field!r} in telemetry row'


def test_invalid_uuid_via_http_returns_400(monkeypatch):
    client = TestClient(api_main.app)
    response = client.get('/monitoring/targets/not-a-uuid/telemetry')
    assert response.status_code == 400
