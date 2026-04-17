from __future__ import annotations

import logging

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


def test_monitoring_reconcile_route_returns_structured_error_for_unexpected_exception(monkeypatch, caplog):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setattr(
        api_main,
        'reconcile_workspace_monitored_systems',
        lambda _request: (_ for _ in ()).throw(RuntimeError('unexpected reconcile exception')),
    )

    with caplog.at_level(logging.ERROR):
        response = client.post('/monitoring/systems/reconcile')

    assert response.status_code == 500
    assert response.json() == {
        'code': 'monitoring_reconcile_failed',
        'detail': 'Unexpected backend error during monitored systems reconcile.',
        'stage': 'reconcile_workspace_monitored_systems',
        'debug_error_type': 'RuntimeError',
        'debug_error_message': 'unexpected reconcile exception',
    }
    assert 'monitoring_reconcile_unexpected_error method=POST path=/monitoring/systems/reconcile' in caplog.text


def test_monitoring_reconcile_route_returns_structured_error_when_runtime_attachment_fails(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setattr(
        api_main,
        'reconcile_workspace_monitored_systems',
        lambda _request: {'workspace': {'id': 'ws-1'}, 'reconcile': {'created_or_updated': 1}, 'systems': [], 'monitored_systems_count': 0},
    )
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request: (_ for _ in ()).throw(TypeError('runtime summary attachment failed')),
    )

    response = client.post('/monitoring/systems/reconcile')

    assert response.status_code == 200
    payload = response.json()
    assert payload['diagnostics']['runtime_status_after_repair'] is None
    assert payload['diagnostics']['runtime_status_after_repair_error']['error_type'] == 'TypeError'


def test_monitoring_reconcile_route_flattens_http_exception_dict_detail(monkeypatch, caplog):
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

    with caplog.at_level(logging.ERROR):
        response = client.post('/monitoring/systems/reconcile')

    assert response.status_code == 500
    assert response.json() == {
        'code': 'monitoring_reconcile_failed',
        'detail': 'Unexpected backend error during monitored systems reconcile.',
        'stage': 'reconcile_targets',
        'debug_error_type': 'ValueError',
        'debug_error_message': 'upsert violated unique constraint',
    }
    assert 'monitoring_reconcile_http_exception method=POST path=/monitoring/systems/reconcile' in caplog.text


def test_monitoring_systems_list_attaches_workspace_monitoring_summary(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'list_monitored_systems',
        lambda _request: {'systems': [{'id': 'sys-1'}], 'workspace': {'id': 'ws-1'}},
    )
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request: {'workspace_monitoring_summary': {'runtime_status': 'idle', 'coverage_state': {'configured_systems': 1, 'reporting_systems': 0, 'protected_assets': 1}}},
    )

    response = client.get('/monitoring/systems')

    assert response.status_code == 200
    payload = response.json()
    assert payload['systems'][0]['id'] == 'sys-1'
    assert payload['workspace_monitoring_summary']['runtime_status'] == 'idle'


def test_monitoring_systems_list_handles_runtime_summary_attachment_failure(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'list_monitored_systems',
        lambda _request: {'systems': [{'id': 'sys-1'}], 'workspace': {'id': 'ws-1'}},
    )
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request: (_ for _ in ()).throw(RuntimeError('runtime summary error')),
    )

    response = client.get('/monitoring/systems')

    assert response.status_code == 200
    payload = response.json()
    assert payload['systems'][0]['id'] == 'sys-1'
    assert payload['workspace_monitoring_summary'] is None


def test_monitoring_systems_list_returns_degraded_payload_when_list_raises(monkeypatch):
    call_count = {'value': 0}

    def _with_auth_passthrough_then_fail(handler):
        call_count['value'] += 1
        if call_count['value'] == 1:
            raise RuntimeError('upstream gateway failure')
        return handler()

    monkeypatch.setattr(
        api_main,
        'with_auth_schema_json',
        _with_auth_passthrough_then_fail,
    )
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request: {'workspace_monitoring_summary': {'runtime_status': 'offline'}},
    )

    response = client.get('/monitoring/systems')

    assert response.status_code == 200
    payload = response.json()
    assert payload['systems'] == []
    assert payload['error']['code'] == 'monitoring_systems_route_failed'
    assert payload['workspace_monitoring_summary']['runtime_status'] == 'offline'


def test_ops_monitoring_runtime_status_returns_degraded_payload_when_route_raises(monkeypatch):
    monkeypatch.setattr(
        api_main,
        'with_auth_schema_json',
        lambda handler: (_ for _ in ()).throw(RuntimeError('runtime route crashed')),
    )

    response = client.get('/ops/monitoring/runtime-status')

    assert response.status_code == 200
    payload = response.json()
    assert payload['monitoring_status'] == 'offline'
    assert payload['error']['code'] == 'runtime_status_route_failed'
    assert payload['workspace_monitoring_summary']['runtime_status'] == 'offline'
