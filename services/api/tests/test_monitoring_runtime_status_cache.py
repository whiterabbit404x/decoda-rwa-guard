from types import SimpleNamespace

from services.api.app import monitoring_runner


def test_monitoring_runtime_status_workspace_cache_short_ttl(monkeypatch):
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()

    calls = {'health': 0}

    def fake_health() -> dict:
        calls['health'] += 1
        return {
            'operational_mode': 'LIVE',
            'mode': 'LIVE',
            'ingestion_mode': 'live',
            'last_cycle_at': '2026-04-23T00:00:00+00:00',
        }

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', fake_health)
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {
            'recent_evidence_state': 'real',
            'recent_truthfulness_state': 'credible',
            'recent_real_event_count': 1,
        },
    )

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-cache'}, state=SimpleNamespace())

    first = monitoring_runner.monitoring_runtime_status(request)
    second = monitoring_runner.monitoring_runtime_status(request)

    assert calls['health'] == 1
    assert first == second
