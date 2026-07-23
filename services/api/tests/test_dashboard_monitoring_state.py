"""Monitoring operational state + evidence confidence derivation (Screen 2).

Proves the dashboard's "Live monitoring" claim and the Executive Brief's
confidence are derived from canonical backend evidence — telemetry freshness,
worker heartbeats, provider health, ingestion coverage, evidence completeness —
and never from the browser/SSE transport or an LLM.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import dashboard_summary as ds


NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def _agg(**over):
    base = {
        'required_worker_count': 1,
        'healthy_worker_count': 1,
        'configured_target_count': 2,
        'reporting_target_count': 2,
        'degraded_provider_count': 0,
        'providers': [{'name': 'p', 'primary_healthy': True, 'fallback_healthy': True, 'rate_limited': False}],
        'evidence_incomplete_incident_count': 0,
        'risk': {'evidence_quality': 'complete'},
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# Monitoring state
# --------------------------------------------------------------------------


def test_live_requires_fresh_telemetry_workers_and_ingestion():
    fresh = {'status': 'fresh', 'latest_event_at': '2026-07-23T11:59:00+00:00', 'age_seconds': 60}
    state = ds.derive_monitoring_state(_agg(), fresh)
    assert state['state'] == 'live'
    assert state['label'] == 'Live monitoring'


def test_stale_telemetry_is_degraded_not_live():
    stale = {'status': 'stale', 'latest_event_at': '2026-07-23T02:00:00+00:00', 'age_seconds': 36000}
    state = ds.derive_monitoring_state(_agg(), stale)
    assert state['state'] == 'degraded'
    assert state['label'] == 'Monitoring degraded'
    assert state['telemetry_fresh'] is False


def test_missing_worker_heartbeat_is_offline():
    fresh = {'status': 'fresh', 'latest_event_at': '2026-07-23T11:59:00+00:00', 'age_seconds': 60}
    state = ds.derive_monitoring_state(_agg(required_worker_count=1, healthy_worker_count=0), fresh)
    assert state['state'] == 'offline'


def test_no_reporting_targets_is_offline():
    fresh = {'status': 'fresh', 'latest_event_at': None, 'age_seconds': 60}
    state = ds.derive_monitoring_state(_agg(configured_target_count=2, reporting_target_count=0), fresh)
    assert state['state'] == 'offline'


def test_no_targets_configured_is_offline():
    unavailable = {'status': 'unavailable', 'latest_event_at': None, 'age_seconds': None}
    state = ds.derive_monitoring_state(_agg(configured_target_count=0, reporting_target_count=0), unavailable)
    assert state['state'] == 'offline'


def test_primary_down_fallback_up_is_degraded():
    fresh = {'status': 'fresh', 'latest_event_at': '2026-07-23T11:59:00+00:00', 'age_seconds': 60}
    providers = [{'name': 'p', 'primary_healthy': False, 'fallback_healthy': True, 'rate_limited': False}]
    state = ds.derive_monitoring_state(_agg(providers=providers, degraded_provider_count=1), fresh)
    assert state['state'] == 'degraded'


# --------------------------------------------------------------------------
# Evidence confidence
# --------------------------------------------------------------------------


def test_confidence_high_when_all_healthy_and_complete():
    fresh = {'status': 'fresh', 'age_seconds': 60}
    assert ds.derive_data_confidence(_agg(), fresh)['level'] == 'high'


def test_confidence_stale_telemetry_is_low_not_medium():
    # ~19h stale telemetry must never read as Medium.
    stale = {'status': 'stale', 'age_seconds': 68400}
    conf = ds.derive_data_confidence(_agg(), stale)
    assert conf['level'] == 'low'
    assert conf['level'] != 'medium'


def test_confidence_unavailable_when_telemetry_unavailable():
    unavailable = {'status': 'unavailable', 'age_seconds': None}
    conf = ds.derive_data_confidence(_agg(reporting_target_count=0), unavailable)
    assert conf['level'] == 'unavailable'
    assert conf['level'] != 'medium'


def test_confidence_medium_only_when_healthy_but_partial_evidence():
    fresh = {'status': 'fresh', 'age_seconds': 60}
    conf = ds.derive_data_confidence(_agg(risk={'evidence_quality': 'partial'}), fresh)
    assert conf['level'] == 'medium'


def test_confidence_low_when_provider_degraded():
    fresh = {'status': 'fresh', 'age_seconds': 60}
    conf = ds.derive_data_confidence(_agg(degraded_provider_count=1), fresh)
    assert conf['level'] == 'low'


# --------------------------------------------------------------------------
# Evidence-freshness block distinguishes generation time from evidence age
# --------------------------------------------------------------------------


def test_evidence_block_separates_generation_time_from_data_age():
    fresh = {'status': 'stale', 'latest_event_at': '2026-07-22T17:00:00+00:00', 'age_seconds': 68400}
    brief = {'generated_at': '2026-07-23T12:00:00+00:00', 'generation_mode': 'deterministic_fallback'}
    evidence = ds.build_evidence_freshness(_agg(), fresh, brief, NOW)
    # A brief freshly generated over 19h-old evidence: both timestamps distinct.
    assert evidence['generated_at'] == '2026-07-23T12:00:00+00:00'
    assert evidence['data_current_through'] == '2026-07-22T17:00:00+00:00'
    assert evidence['telemetry_age_seconds'] == 68400
    assert evidence['data_confidence'] == 'low'
    assert evidence['generation_mode'] == 'deterministic_fallback'
