from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'

sys.path.insert(0, str(REPO_ROOT))


def load_api_module():
    spec = importlib.util.spec_from_file_location('phase1_api_embedded_live_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for embedded dashboard tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _embedded_mode(api_main) -> None:
    api_main.load_embedded_service_main.cache_clear()
    api_main.DEPENDENCY_RUNTIME_STATUS.clear()
    api_main.RISK_ENGINE_URL_ENV = None
    api_main.RISK_ENGINE_URL = 'http://localhost:8001'
    api_main.THREAT_ENGINE_URL_ENV = None
    api_main.THREAT_ENGINE_URL = 'http://localhost:8002'
    api_main.COMPLIANCE_SERVICE_URL_ENV = None
    api_main.COMPLIANCE_SERVICE_URL = 'http://localhost:8004'
    api_main.RECONCILIATION_SERVICE_URL_ENV = None
    api_main.RECONCILIATION_SERVICE_URL = 'http://localhost:8005'


def test_embedded_live_dashboards_use_real_service_modules() -> None:
    api_main = load_api_module()
    _embedded_mode(api_main)
    client = TestClient(api_main.app)

    risk = client.get('/risk/dashboard')
    threat = client.get('/threat/dashboard')
    compliance = client.get('/compliance/dashboard')
    resilience = client.get('/resilience/dashboard')

    assert risk.status_code == 200
    assert risk.json()['source'] == 'live'
    assert risk.json()['degraded'] is False
    assert risk.json()['risk_engine']['fallback_items'] == 0

    assert threat.status_code == 200
    assert threat.json()['source'] == 'live'
    assert threat.json()['degraded'] is False

    assert compliance.status_code == 200
    assert compliance.json()['source'] == 'live'
    assert compliance.json()['degraded'] is False

    assert resilience.status_code == 200
    assert resilience.json()['source'] == 'live'
    assert resilience.json()['degraded'] is False

    details = client.get('/health/details').json()['dependencies']
    assert details['risk_engine']['last_used_mode'] == 'embedded_local'
    assert details['threat_engine']['last_used_mode'] == 'embedded_local'
    assert details['compliance_service']['last_used_mode'] == 'embedded_local'
    assert details['reconciliation_service']['last_used_mode'] == 'embedded_local'


def test_dashboard_registry_reports_live_when_all_embedded_services_are_available() -> None:
    api_main = load_api_module()
    _embedded_mode(api_main)
    client = TestClient(api_main.app)

    for endpoint in (
        '/risk/dashboard',
        '/threat/dashboard',
        '/compliance/dashboard',
        '/resilience/dashboard',
    ):
        response = client.get(endpoint)
        assert response.status_code == 200
        assert response.json()['source'] == 'live'

    dashboard = client.get('/dashboard')

    assert dashboard.status_code == 200
    services = {service['service_name']: service for service in dashboard.json()['services']}
    assert services['risk-engine']['status'] == 'ok'
    assert services['threat-engine']['status'] == 'ok'
    assert services['compliance-service']['status'] == 'ok'
    assert services['reconciliation-service']['status'] == 'ok'

    metrics = {
        service_name: {metric['metric_key']: metric for metric in service['metrics']}
        for service_name, service in services.items()
        if service_name in {
            'risk-engine',
            'threat-engine',
            'compliance-service',
            'reconciliation-service',
        }
    }
    assert metrics['risk-engine']['payload_source']['value'] == 'live'
    assert metrics['threat-engine']['payload_source']['value'] == 'live'
    assert metrics['compliance-service']['payload_source']['value'] == 'live'
    assert metrics['reconciliation-service']['payload_source']['value'] == 'live'
