from services.api.app import activity_providers


def test_monitoring_mode_defaults_to_hybrid_without_env(monkeypatch):
    monkeypatch.delenv('MONITORING_INGESTION_MODE', raising=False)
    assert activity_providers.monitoring_ingestion_mode() == 'hybrid'


def test_live_monitoring_opt_in_validation_requires_rpc(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    runtime = activity_providers.monitoring_ingestion_runtime()
    assert runtime['degraded'] is True
    assert runtime['reason'] == 'EVM_RPC_URL missing'
