from __future__ import annotations

from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.api.app import main as api_main


client = TestClient(api_main.app)


def test_monitoring_reconcile_route_returns_200_for_success(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'reconcile_workspace_monitored_systems',
        lambda _request: {
            'workspace': {'id': 'ws-1'},
            'reconcile': {'targets_scanned': 1, 'created_or_updated': 1},
            'systems': [{'id': 'ms-1'}],
            'monitored_systems_count': 1,
        },
    )

    response = client.post('/monitoring/systems/reconcile')

    assert response.status_code == 200
    payload = response.json()
    assert payload['reconcile']['created_or_updated'] == 1
    assert payload['monitored_systems_count'] == 1


def test_monitoring_reconcile_route_returns_structured_error_for_unexpected_exception(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setattr(
        api_main,
        'reconcile_workspace_monitored_systems',
        lambda _request: (_ for _ in ()).throw(RuntimeError('unexpected reconcile exception')),
    )

    response = client.post('/monitoring/systems/reconcile')

    assert response.status_code == 500
    assert response.json() == {
        'code': 'monitoring_reconcile_failed',
        'detail': 'Unexpected backend error during monitored systems reconcile.',
        'stage': 'unhandled_route_exception',
        'debug_error_type': 'RuntimeError',
        'debug_error_message': 'unexpected reconcile exception',
    }


def test_monitoring_reconcile_route_flattens_http_exception_dict_detail(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'reconcile_workspace_monitored_systems',
        lambda _request: (_ for _ in ()).throw(
            HTTPException(
                status_code=500,
                detail={
                    'code': 'monitoring_reconcile_failed',
                    'detail': 'Unexpected backend error during monitored systems reconcile.',
                    'stage': 'reconcile_targets',
                    'debug_error_type': 'ValueError',
                    'debug_error_message': 'upsert violated unique constraint',
                },
            )
        ),
    )

    response = client.post('/monitoring/systems/reconcile')

    assert response.status_code == 500
    assert response.json() == {
        'code': 'monitoring_reconcile_failed',
        'detail': 'Unexpected backend error during monitored systems reconcile.',
        'stage': 'reconcile_targets',
        'debug_error_type': 'ValueError',
        'debug_error_message': 'upsert violated unique constraint',
    }
