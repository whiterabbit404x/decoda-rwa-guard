from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from services.api.app import activity_providers, pilot


def test_pilot_target_runtime_queries_do_not_use_monitoring_demo_scenario() -> None:
    source = Path(pilot.__file__).read_text()
    assert 'monitoring_demo_scenario, monitored_by_workspace_id' not in source
    assert 'monitoring_demo_scenario = NULL' not in source


def test_live_and_hybrid_do_not_import_demo_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {'count': 0}

    def _boom(name: str):
        called['count'] += 1
        raise AssertionError('demo provider import attempted outside demo mode')

    monkeypatch.setattr(activity_providers.importlib, 'import_module', _boom)
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', '')

    for mode in ('live', 'hybrid'):
        monkeypatch.setenv('MONITORING_MODE', mode)
        result = activity_providers.fetch_target_activity_result({'id': 't1', 'target_type': 'wallet'}, None)
        assert result.synthetic is False

    assert called['count'] == 0


def _oracle_module():
    path = Path(__file__).resolve().parents[2] / 'oracle-service' / 'app' / 'main.py'
    spec = importlib.util.spec_from_file_location('oracle_service_main_enterprise_test', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_observations_fail_closed_without_real_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    monkeypatch.delenv('ORACLE_SOURCE_URLS', raising=False)
    monkeypatch.setenv('ORACLE_SOURCE_OBSERVATIONS_JSON', '[{"source_name":"demo","observed_value":1}]')
    module = _oracle_module()
    response = module.oracle_observations(asset_identifier='USTB')
    assert response['status'] == 'insufficient_real_evidence'
    assert response['reason'] == 'real_oracle_providers_not_configured'


def test_run_once_is_debug_only_not_enterprise_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = {
        'analysis_runs': [
            {'id': 'r1', 'response_payload': {'monitoring_path': 'manual_run_once'}},
        ],
        'alerts': [
            {'id': 'a1', 'severity': 'high', 'payload': {'monitoring_path': 'manual_run_once', 'detector_status': 'anomaly_detected'}},
        ],
    }
    worker_runs = [item for item in summary['analysis_runs'] if str(((item.get('response_payload') or {}).get('monitoring_path') or '')).lower() == 'worker']
    assert worker_runs == []
