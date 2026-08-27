"""GET /ops/monitoring/runtime-status carries the canonical monitoring verdict and
the canonical realtime-ingestion facts to the web client.

The web client's top monitoring banner is derived from this response. Before this
passthrough it received no ``monitoring_status`` at all in production (the key was
filtered out of the canonical response), so every non-offline workspace defaulted
to 'limited' and the banner was pinned to LIMITED COVERAGE, and it had no
QuickNode Stream health facts, so it fell back to the intentionally-disabled
legacy realtime WebSocket worker for "is realtime running".

This is serialization only: the values are passed through verbatim from the
runtime builder, and nothing here derives, upgrades, or downgrades a status.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from services.api.app import main as api_main


_REALTIME_INGESTION_FACTS = {
    'streams_enabled': True,
    'status': 'healthy',
    'healthy': True,
    'live_evidence_fresh': True,
    'live_evidence_kind': 'coverage',
    'live_coverage_fresh': True,
    'live_security_telemetry_fresh': False,
    'lane_state': 'live',
    'lag_blocks': 1,
    'checkpoint_block': 41_000_000,
    'chain_head': 41_000_001,
    'checkpoint_age_seconds': 3,
    'last_live_telemetry_at': None,
    'live_telemetry_age_seconds': None,
    'last_live_coverage_at': '2026-08-27T11:59:40Z',
    'live_coverage_age_seconds': 20,
    'reason': 'stream_near_chain_tip_with_fresh_coverage',
}


def _runtime_payload(**overrides):
    payload = {
        'workspace_configured': True,
        'runtime_status': 'healthy',
        'monitoring_status': 'active',
        'configured_systems': 2,
        'reporting_systems': 2,
        'protected_assets': 1,
        'last_poll_at': '2026-08-27T11:59:00Z',
        'last_heartbeat_at': '2026-08-27T11:59:30Z',
        'last_telemetry_at': '2026-08-27T11:59:40Z',
        'last_detection_at': None,
        'freshness_status': 'fresh',
        'confidence_status': 'high',
        'evidence_source': 'live',
        'status_reason': None,
        'contradiction_flags': [],
        'summary_generated_at': '2026-08-27T12:00:00Z',
        'provider_health': [],
        'target_coverage': [],
        'provider_health_records': [],
        'target_coverage_records': [],
        'realtime_ingestion': dict(_REALTIME_INGESTION_FACTS),
    }
    payload.update(overrides)
    return payload


def _get(monkeypatch, payload, *, production_like=True):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: production_like)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(payload))
    response = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    return response.json()


def test_production_response_carries_monitoring_status(monkeypatch):
    body = _get(monkeypatch, _runtime_payload())
    assert 'monitoring_status' in body
    assert body['monitoring_status'] == 'active'


def test_production_response_carries_realtime_ingestion_verbatim(monkeypatch):
    body = _get(monkeypatch, _runtime_payload())
    assert body['realtime_ingestion'] == _REALTIME_INGESTION_FACTS


def test_realtime_ingestion_is_read_from_the_nested_summary_when_absent_top_level(monkeypatch):
    payload = _runtime_payload()
    payload.pop('realtime_ingestion')
    payload['workspace_monitoring_summary'] = {
        'monitoring_status': 'live',
        'realtime_ingestion': dict(_REALTIME_INGESTION_FACTS),
    }
    body = _get(monkeypatch, payload)
    assert body['realtime_ingestion'] == _REALTIME_INGESTION_FACTS
    # The nested summary keeps precedence for monitoring_status, unchanged.
    assert body['monitoring_status'] == 'live'


def test_realtime_ingestion_is_omitted_when_the_runtime_reports_none(monkeypatch):
    payload = _runtime_payload()
    payload.pop('realtime_ingestion')
    body = _get(monkeypatch, payload)
    # Omitted, never emitted as an empty/healthy-looking object: the client must be
    # able to tell "no canonical realtime verdict" apart from "verdict says healthy".
    assert 'realtime_ingestion' not in body


def test_degraded_realtime_ingestion_is_passed_through_unchanged(monkeypatch):
    degraded = dict(_REALTIME_INGESTION_FACTS)
    degraded.update(
        {
            'status': 'degraded',
            'healthy': False,
            'live_evidence_fresh': False,
            'live_coverage_fresh': False,
            'live_evidence_kind': 'none',
            'lane_state': 'catching_up',
            'lag_blocks': 480,
            'reason': 'stream_live_lane_not_established',
        }
    )
    body = _get(monkeypatch, _runtime_payload(realtime_ingestion=degraded))
    assert body['realtime_ingestion'] == degraded
    assert body['realtime_ingestion']['healthy'] is False


def test_non_production_response_also_carries_the_canonical_realtime_facts(monkeypatch):
    body = _get(monkeypatch, _runtime_payload(), production_like=False)
    assert body['realtime_ingestion'] == _REALTIME_INGESTION_FACTS
    assert body['workspace_monitoring_summary']['realtime_ingestion'] == _REALTIME_INGESTION_FACTS
