"""
Regression test: runtime_status must not degrade with event_ingestion_missing when
live telemetry is fresh and all configured targets are reporting.

Scenario:
  configured_systems=4, reporting_systems=4, protected_assets=1
  last_poll_at/heartbeat/telemetry/detection all present and fresh
  freshness_status=fresh, confidence_status=high, evidence_source=live
  contradiction_flags=[]
  provider_health_records=[], target_coverage_records=[]
  target_coverage has 4 entries: coverage_status=reporting, provider_status=live,
    telemetry_basis.kind=telemetry_event (inside metadata, not top-level)

Expected:
  runtime_status != 'degraded' with event_ingestion_missing reason
  target_coverage_status = 'reporting' (not 'partial')
  target_coverage_records falls back to target_coverage entries
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.pilot import evaluate_workspace_monitoring_continuity


# ---------------------------------------------------------------------------
# Unit test: continuity evaluator with coverage telemetry as event fallback
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


def test_coverage_telemetry_as_event_fallback_passes_slo() -> None:
    """When canonical_last_telemetry_at is used as last_event_at fallback,
    the continuity SLO passes and event_ingestion_missing is not emitted."""
    now = _now()
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=30),
        last_event_at=now - timedelta(seconds=60),   # canonical telemetry fallback
        last_detection_at=now - timedelta(seconds=90),
        heartbeat_ttl_seconds=300,
        telemetry_window_seconds=600,
        detection_window_seconds=900,
    )
    assert payload['continuity_slo_pass'] is True
    assert 'event_ingestion_missing' not in payload['continuity_reason_codes']
    assert payload['ingestion_freshness'] == 'fresh'
    assert payload['continuity_status'] == 'continuous_live'


def test_event_ingestion_missing_only_when_telemetry_null() -> None:
    """event_ingestion_missing is emitted only when last_event_at is None."""
    now = _now()
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=30),
        last_event_at=None,
        last_detection_at=None,
        heartbeat_ttl_seconds=300,
        telemetry_window_seconds=600,
        detection_window_seconds=900,
    )
    assert 'event_ingestion_missing' in payload['continuity_reason_codes']
    assert payload['continuity_slo_pass'] is False


# ---------------------------------------------------------------------------
# Endpoint test: target_coverage_status derived from coverage_status field
# (entries have coverage_status=reporting but no top-level provider_status)
# ---------------------------------------------------------------------------

def _four_targets_payload() -> dict:
    """Payload representing 4 reporting targets with coverage_status entries
    but provider_status only inside metadata (as monitoring_runner produces)."""
    return {
        'status': 'Live',
        'monitoring_status': 'live',
        'runtime_status': 'live',
        'runtime_status_summary': 'live',
        'workspace_configured': True,
        'configured_systems': 4,
        'reporting_systems': 4,
        'protected_assets': 1,
        'last_poll_at': '2026-05-29T11:59:00Z',
        'last_heartbeat_at': '2026-05-29T11:59:10Z',
        'last_telemetry_at': '2026-05-29T11:59:20Z',
        'last_detection_at': '2026-05-29T11:59:30Z',
        'freshness_status': 'fresh',
        'confidence_status': 'high',
        'evidence_source': 'live',
        'status_reason': 'live_runtime_verified',
        'contradiction_flags': [],
        'continuity_slo_pass': True,
        'continuity_status': 'continuous_live',
        'continuity_reason_codes': [],
        'workspace_monitoring_summary': {
            'runtime_status': 'live',
            'monitoring_status': 'live',
            'continuity_slo_pass': True,
            'continuity_reason_codes': [],
            'continuity_status': 'continuous_live',
            'heartbeat_age_seconds': 50,
            'worker_heartbeat_age_seconds': 50,
            'telemetry_age_seconds': 40,
            'event_ingestion_age_seconds': 40,
            'detection_age_seconds': 30,
            'detection_pipeline_age_seconds': 30,
            'detection_eval_age_seconds': 30,
            'heartbeat_threshold_seconds': 300,
            'telemetry_threshold_seconds': 600,
            'event_ingestion_threshold_seconds': 600,
            'detection_threshold_seconds': 900,
            'thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'required_thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'continuity_thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'continuity_freshness_ages_seconds': {'heartbeat': 50, 'telemetry': 40, 'event_ingestion': 40, 'detection': 30},
            'continuity_configured_thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'continuity_failed_checks': [],
            'continuity_breach_reasons': [],
            'runtime_degraded_reason_codes': [],
            'runtime_status_reason_codes': [],
        },
        # target_coverage entries as produced by monitoring_runner (no top-level provider_status)
        'target_coverage': [
            {
                'target_id': f'target-{i}',
                'coverage_status': 'reporting',
                'last_telemetry_at': '2026-05-29T11:59:20Z',
                'evidence_source': 'live',
                'computed_at': '2026-05-29T11:59:25Z',
                'metadata': {
                    'provider_status': 'live',
                    'telemetry_basis': {'kind': 'telemetry_event', 'event_id': f'te-{i}'},
                },
            }
            for i in range(1, 5)
        ],
        'provider_health': [],
        'provider_health_status': 'healthy',
        # target_coverage_records is empty (as in the reported scenario)
        'target_coverage_records': [],
        'provider_health_records': [],
    }


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    return TestClient(api_main.app)


def test_target_coverage_status_not_partial_when_all_reporting(client, monkeypatch):
    """target_coverage_status must be 'reporting' when all entries have
    coverage_status=reporting, even if provider_status is only in metadata."""
    payload = _four_targets_payload()
    # Remove top-level target_coverage_status so derivation logic runs
    payload.pop('target_coverage_status', None)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    response = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['target_coverage_status'] == 'reporting', (
        f"expected 'reporting' but got {body['target_coverage_status']!r}"
    )


def test_target_coverage_records_falls_back_to_target_coverage(client, monkeypatch):
    """target_coverage_records must be populated from target_coverage when the
    canonical records array is empty but target_coverage entries exist."""
    payload = _four_targets_payload()
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    response = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body['target_coverage_records'], list)
    assert len(body['target_coverage_records']) == 4


def test_runtime_status_live_when_continuity_slo_passes(client, monkeypatch):
    """When continuity_slo_pass=True and evidence is live, runtime_status must
    not be degraded with event_ingestion_missing."""
    payload = _four_targets_payload()
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    response = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['runtime_status'] == 'live', (
        f"expected 'live' but got {body['runtime_status']!r}"
    )
    status_reason = str(body.get('status_reason') or '')
    assert 'event_ingestion_missing' not in status_reason, (
        f"status_reason must not contain event_ingestion_missing: {status_reason!r}"
    )
    assert status_reason != 'runtime_status_degraded:continuity_slo_failed:event_ingestion_missing'


def test_runtime_status_not_degraded_for_full_scenario(client, monkeypatch):
    """Full regression: configured_systems=4, reporting_systems=4, protected_assets=1,
    all timestamps present, fresh, live evidence, empty canonical record arrays,
    4 reporting/live telemetry_event-backed target_coverage entries.
    Expected: runtime_status='live', no event_ingestion_missing, target_coverage_status='reporting'.
    """
    payload = _four_targets_payload()
    # Simulate the exact scenario: target_coverage_records=[], target_coverage_status not set
    payload['target_coverage_records'] = []
    payload.pop('target_coverage_status', None)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    response = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['runtime_status'] == 'live'
    assert body['target_coverage_status'] != 'partial'
    status_reason = str(body.get('status_reason') or '')
    assert status_reason != 'runtime_status_degraded:continuity_slo_failed:event_ingestion_missing'
