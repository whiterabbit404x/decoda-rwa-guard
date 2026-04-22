from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from services.api.app import main as api_main


def test_monitoring_list_routes_return_200(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'list_detections', lambda request, **kwargs: {'detections': [{'id': 'det-1'}]})
    monkeypatch.setattr(api_main, 'list_incidents', lambda request, **kwargs: {'incidents': [{'id': 'inc-1', 'workflow_status': 'open'}]})
    monkeypatch.setattr(api_main, 'list_alerts', lambda request, **kwargs: {'alerts': [{'id': 'alert-1'}]})

    detections = client.get('/detections?limit=50')
    incidents = client.get('/incidents?status_value=open')
    alerts = client.get('/alerts?status_value=open')

    assert detections.status_code == 200
    assert incidents.status_code == 200
    assert alerts.status_code == 200
    assert detections.json()['detections'][0]['id'] == 'det-1'
    assert incidents.json()['incidents'][0]['workflow_status'] == 'open'
    assert alerts.json()['alerts'][0]['id'] == 'alert-1'


def test_monitoring_list_routes_log_once_per_failure(monkeypatch, caplog):
    client = TestClient(api_main.app)
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(api_main, 'list_alerts', lambda request, **kwargs: (_ for _ in ()).throw(ValueError('ambiguous column "id"')))

    response = client.get('/alerts?status_value=open')

    assert response.status_code == 500
    failure_logs = [record for record in caplog.records if record.message.startswith('monitoring_list_failed path=/alerts')]
    assert len(failure_logs) == 1
