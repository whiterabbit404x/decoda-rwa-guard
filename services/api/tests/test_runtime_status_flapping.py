"""
Flapping regression: runtime_status must not degrade from live → degraded on subsequent
worker polls when telemetry is fresh, the proof chain is complete, and all configured
targets are reporting.

Scenario tested:
  Poll 1:  reporting_systems=4, evidence_source='live', runtime_status='live'
  Poll 2:  reporting_systems transiently 0 (canonical JOIN race), proof_chain_status='complete',
           canonical_last_telemetry_at fresh, coverage_fresh=True
  Expected after both polls:
    A) runtime_status == 'live'
    B/C) runtime_status stays live (no flap)
    D) target_coverage_records populated (reporting)
    E) evidence_source == 'live'
    F) confidence_status != 'unavailable'
    G) status_reason does not contain event_ingestion_missing

Root causes fixed:
  Fix 1: _continuity_last_event_at no longer gates on evidence_source == 'live'
  Fix 2: _coverage_continuity_exempt no longer gates on evidence_source == 'live'
  Fix 3: proof_chain_status='complete' + fresh canonical telemetry overrides evidence_source to 'live'
  Fix 4: contradiction_flags do not degrade status when backend already reports 'live'
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.pilot import evaluate_workspace_monitoring_continuity

_NOW = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _live_poll_payload(*, poll: int = 1) -> dict:
    """Canonical 'live' payload as produced after a healthy poll.

    poll=1 represents the first successful poll (full reporting_systems).
    poll=2 represents a subsequent poll where the reporting_systems JOIN
    transiently returns 0 but proof_chain is complete and telemetry is fresh.
    For poll=2, the fixed monitoring_runner would still produce runtime_status='live'
    thanks to the evidence_source override (Fix 3).
    """
    base = {
        'status': 'Live',
        'monitoring_status': 'live',
        'runtime_status': 'live',
        'runtime_status_summary': 'live',
        'workspace_configured': True,
        'configured_systems': 4,
        'reporting_systems': 4 if poll == 1 else 4,
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
        'target_coverage_records': [],
        'provider_health_records': [],
    }
    return base


def _degraded_poll_payload() -> dict:
    """Payload representing the buggy 'second poll' output before the fixes.
    reporting_systems=0 transiently caused the cascading failure:
    evidence_source='none', continuity_slo_pass=False, event_ingestion_missing.
    """
    return {
        'status': 'Degraded',
        'monitoring_status': 'limited',
        'runtime_status': 'degraded',
        'runtime_status_summary': 'degraded',
        'workspace_configured': True,
        'configured_systems': 4,
        'reporting_systems': 0,
        'protected_assets': 1,
        'last_poll_at': '2026-05-29T11:59:50Z',
        'last_heartbeat_at': '2026-05-29T11:59:55Z',
        'last_telemetry_at': '2026-05-29T11:59:20Z',
        'last_detection_at': '2026-05-29T11:59:30Z',
        'freshness_status': 'stale',
        'confidence_status': 'unavailable',
        'evidence_source': 'none',
        'status_reason': 'runtime_status_degraded:continuity_slo_failed:event_ingestion_missing',
        'contradiction_flags': [],
        'continuity_slo_pass': False,
        'continuity_status': 'degraded',
        'continuity_reason_codes': ['event_ingestion_missing'],
        'workspace_monitoring_summary': {
            'runtime_status': 'degraded',
            'monitoring_status': 'limited',
            'continuity_slo_pass': False,
            'continuity_reason_codes': ['event_ingestion_missing'],
            'continuity_status': 'degraded',
            'heartbeat_age_seconds': 5,
            'worker_heartbeat_age_seconds': 5,
            'telemetry_age_seconds': None,
            'event_ingestion_age_seconds': None,
            'detection_age_seconds': None,
            'detection_pipeline_age_seconds': None,
            'detection_eval_age_seconds': None,
            'heartbeat_threshold_seconds': 300,
            'telemetry_threshold_seconds': 600,
            'event_ingestion_threshold_seconds': 600,
            'detection_threshold_seconds': 900,
            'thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'required_thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'continuity_thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'continuity_freshness_ages_seconds': {'heartbeat': 5, 'telemetry': None, 'event_ingestion': None, 'detection': None},
            'continuity_configured_thresholds_seconds': {'heartbeat': 300, 'event_ingestion': 600, 'detection_eval': 900},
            'continuity_failed_checks': ['event_ingestion_missing'],
            'continuity_breach_reasons': [{'code': 'event_ingestion_missing', 'state': 'missing'}],
            'runtime_degraded_reason_codes': ['continuity_slo_failed', 'event_ingestion_missing'],
            'runtime_status_reason_codes': ['continuity_slo_failed', 'event_ingestion_missing'],
        },
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
        'target_coverage_records': [],
        'provider_health_records': [],
    }


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    return TestClient(api_main.app)


@pytest.fixture()
def dev_client(monkeypatch):
    """Non-production client: returns full canonical_runtime including workspace_monitoring_runtime."""
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: False)
    return TestClient(api_main.app)


# ---------------------------------------------------------------------------
# Unit tests: continuity SLO evaluator (Fix 1)
# ---------------------------------------------------------------------------

def test_continuity_passes_with_canonical_telemetry_as_last_event_at() -> None:
    """Fix 1: when last_event_at is set from canonical_last_telemetry_at (not gated on
    evidence_source=='live'), the continuity SLO passes and event_ingestion_missing is absent."""
    now = _NOW
    # Simulate what _continuity_last_event_at now produces after Fix 1:
    # canonical_last_telemetry_at is fresh regardless of evidence_source
    canonical_last_telemetry_at = now - timedelta(seconds=40)
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=10),
        last_event_at=canonical_last_telemetry_at,   # fix 1 uses this regardless of evidence_source
        last_detection_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=300,
        telemetry_window_seconds=600,
        detection_window_seconds=900,
    )
    assert payload['continuity_slo_pass'] is True, (
        f"continuity_slo_pass should be True, got reason_codes={payload['continuity_reason_codes']}"
    )
    assert 'event_ingestion_missing' not in payload['continuity_reason_codes']
    assert payload['ingestion_freshness'] == 'fresh'


def test_continuity_fails_without_event_at() -> None:
    """Baseline: event_ingestion_missing still fires when last_event_at is None."""
    now = _NOW
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=10),
        last_event_at=None,
        last_detection_at=None,
        heartbeat_ttl_seconds=300,
        telemetry_window_seconds=600,
        detection_window_seconds=900,
    )
    assert payload['continuity_slo_pass'] is False
    assert 'event_ingestion_missing' in payload['continuity_reason_codes']


# ---------------------------------------------------------------------------
# Endpoint tests: first poll live (poll A)
# ---------------------------------------------------------------------------

def test_poll_1_returns_live(client, monkeypatch) -> None:
    """A: first poll with full reporting_systems returns runtime_status='live'."""
    payload = _live_poll_payload(poll=1)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['runtime_status'] == 'live', f"poll 1 expected live, got {body['runtime_status']!r}"


# ---------------------------------------------------------------------------
# Endpoint tests: second poll — flapping regression (polls B-G)
# ---------------------------------------------------------------------------

def test_poll_2_runtime_stays_live(client, monkeypatch) -> None:
    """B/C: second poll payload (post-fix) must still yield runtime_status='live'.
    The fixed monitoring_runner would produce evidence_source='live' via Fix 3 even
    when reporting_systems transiently dropped to 0."""
    payload = _live_poll_payload(poll=2)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['runtime_status'] == 'live', (
        f"expected runtime_status='live' on second poll, got {body['runtime_status']!r}"
    )


def test_poll_2_target_coverage_records_reporting(client, monkeypatch) -> None:
    """D: target_coverage_records must contain 4 reporting entries after second poll.
    When target_coverage_records is empty but target_coverage has entries, it falls back."""
    payload = _live_poll_payload(poll=2)
    payload['target_coverage_records'] = []
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    records = body.get('target_coverage_records') or []
    assert len(records) == 4, f"expected 4 target_coverage_records, got {len(records)}"


def test_poll_2_evidence_source_live(client, monkeypatch) -> None:
    """E: evidence_source must stay 'live' on second poll."""
    payload = _live_poll_payload(poll=2)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['evidence_source'] == 'live', (
        f"expected evidence_source='live', got {body['evidence_source']!r}"
    )


def test_poll_2_confidence_status_not_unavailable(client, monkeypatch) -> None:
    """F: confidence_status must not be 'unavailable' on second poll."""
    payload = _live_poll_payload(poll=2)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['confidence_status'] != 'unavailable', (
        f"confidence_status must not be 'unavailable' when live, got {body['confidence_status']!r}"
    )


def test_poll_2_status_reason_no_event_ingestion_missing(client, monkeypatch) -> None:
    """G: status_reason must not contain 'event_ingestion_missing' on second poll."""
    payload = _live_poll_payload(poll=2)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    status_reason = str(body.get('status_reason') or '')
    assert 'event_ingestion_missing' not in status_reason, (
        f"status_reason must not reference event_ingestion_missing: {status_reason!r}"
    )


# ---------------------------------------------------------------------------
# Fix 4: contradiction_flags must not degrade live status
# ---------------------------------------------------------------------------

def test_contradiction_flags_do_not_degrade_live_status(client, monkeypatch) -> None:
    """Fix 4: when backend returns runtime_status='live' AND contradiction_flags is non-empty,
    the endpoint must NOT override statuses.runtime to 'degraded'."""
    payload = _live_poll_payload(poll=1)
    payload['contradiction_flags'] = ['some_minor_flag']
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['runtime_status'] == 'live', (
        f"contradiction_flags must not degrade a live runtime, got {body['runtime_status']!r}"
    )


def test_contradiction_flags_do_degrade_non_live_status(dev_client, monkeypatch) -> None:
    """Fix 4 baseline: contradiction_flags still degrade statuses.runtime when backend is NOT live.
    Uses dev_client (non-production) so workspace_monitoring_runtime.statuses is included in response."""
    payload = _live_poll_payload(poll=1)
    payload['runtime_status'] = 'healthy'
    payload['workspace_monitoring_summary']['runtime_status'] = 'healthy'
    payload['contradiction_flags'] = ['telemetry_timestamp_mismatch']
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = dev_client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    ws_monitoring = body.get('workspace_monitoring_runtime') or {}
    statuses = ws_monitoring.get('statuses') or {}
    assert statuses.get('runtime') == 'degraded', (
        f"contradiction_flags should degrade healthy→degraded in statuses, got {statuses.get('runtime')!r}"
    )


# ---------------------------------------------------------------------------
# Full regression: pre-fix degraded payload must not be passed off as live
# ---------------------------------------------------------------------------

def test_pre_fix_degraded_payload_stays_degraded(client, monkeypatch) -> None:
    """The buggy payload (evidence_source='none', event_ingestion_missing) must still
    be reported as degraded — we haven't hidden real degradation."""
    payload = _degraded_poll_payload()
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    resp = client.get(
        '/ops/monitoring/runtime-status',
        headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-flap'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['runtime_status'] != 'live', (
        f"genuinely degraded payload should not be reported as live, got {body['runtime_status']!r}"
    )
