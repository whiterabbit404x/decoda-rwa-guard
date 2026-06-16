"""Endpoint test: POST /alerts/open-from-detection maps canonical statuses to HTTP codes.

Requirement 6:
  * created        -> 201
  * already_exists -> 409
  * no_detection   -> 200
  * backend error  -> 500 with the exact backend error in `detail`
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from services.api.app import main as api_main


client = TestClient(api_main.app)


def test_open_alert_route_returns_201_on_created(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'open_alert_from_detection',
        lambda _request: {'status': 'created', 'alert_id': 'alert-1', 'detection_id': 'det-1'},
    )

    response = client.post('/alerts/open-from-detection')

    assert response.status_code == 201
    payload = response.json()
    assert payload['status'] == 'created'
    assert payload['alert_id'] == 'alert-1'


def test_open_alert_route_returns_409_when_alert_already_exists(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'open_alert_from_detection',
        lambda _request: {'status': 'already_exists', 'alert_id': 'alert-existing', 'detection_id': 'det-1'},
    )

    response = client.post('/alerts/open-from-detection')

    assert response.status_code == 409
    payload = response.json()
    assert payload['status'] == 'already_exists'
    assert payload['alert_id'] == 'alert-existing'


def test_open_alert_route_returns_200_when_no_detection(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'open_alert_from_detection',
        lambda _request: {'status': 'no_detection', 'alert_id': None, 'detection_id': None},
    )

    response = client.post('/alerts/open-from-detection')

    assert response.status_code == 200
    assert response.json()['status'] == 'no_detection'


def test_open_alert_route_returns_500_with_exact_backend_error(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'open_alert_from_detection',
        lambda _request: (_ for _ in ()).throw(RuntimeError('boom: connection closed')),
    )

    response = client.post('/alerts/open-from-detection')

    assert response.status_code == 500
    detail = response.json()['detail']
    # The exact backend error must be surfaced (requirement 6), not masked.
    assert 'RuntimeError' in detail
    assert 'boom: connection closed' in detail
