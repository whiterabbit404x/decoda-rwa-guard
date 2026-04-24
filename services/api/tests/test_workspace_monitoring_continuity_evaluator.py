from datetime import datetime, timedelta, timezone

from services.api.app.pilot import evaluate_workspace_monitoring_continuity


def _now() -> datetime:
    return datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)


def test_continuity_continuous_live_at_freshness_boundary() -> None:
    now = _now()
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=180),
        last_event_at=now - timedelta(seconds=120),
        last_detection_at=now - timedelta(seconds=300),
        heartbeat_ttl_seconds=180,
        telemetry_window_seconds=120,
        detection_window_seconds=300,
    )
    assert payload['continuity_status'] == 'continuous_live'
    assert payload['continuity_reason_codes'] == []
    assert payload['ingestion_freshness'] == 'fresh'
    assert payload['detection_pipeline_freshness'] == 'fresh'
    assert payload['worker_heartbeat_freshness'] == 'fresh'
    assert payload['event_throughput_window'] == 'in_window'


def test_continuity_degraded_when_event_freshness_is_stale() -> None:
    now = _now()
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=60),
        last_event_at=now - timedelta(seconds=121),
        last_detection_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=180,
        telemetry_window_seconds=120,
        detection_window_seconds=300,
    )
    assert payload['continuity_status'] == 'degraded'
    assert 'event_ingestion_stale' in payload['continuity_reason_codes']
    assert payload['ingestion_freshness'] == 'stale'
    assert payload['detection_pipeline_freshness'] == 'fresh'
    assert payload['worker_heartbeat_freshness'] == 'fresh'
    assert payload['event_throughput_window'] == 'out_of_window'


def test_continuity_offline_when_worker_dead_and_all_signals_offline() -> None:
    now = _now()
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=False,
        last_heartbeat_at=now - timedelta(seconds=601),
        last_event_at=now - timedelta(seconds=361),
        last_detection_at=now - timedelta(seconds=901),
        heartbeat_ttl_seconds=180,
        telemetry_window_seconds=120,
        detection_window_seconds=300,
    )
    assert payload['continuity_status'] == 'offline'
    assert 'worker_not_live' in payload['continuity_reason_codes']
    assert payload['ingestion_freshness'] == 'offline'
    assert payload['detection_pipeline_freshness'] == 'offline'
    assert payload['worker_heartbeat_freshness'] == 'offline'
    assert payload['event_throughput_window'] == 'offline'


def test_continuity_idle_no_telemetry_when_no_runtime_timestamps_present() -> None:
    payload = evaluate_workspace_monitoring_continuity(
        now=_now(),
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=None,
        last_event_at=None,
        last_detection_at=None,
        heartbeat_ttl_seconds=180,
        telemetry_window_seconds=120,
        detection_window_seconds=300,
    )
    assert payload['continuity_status'] == 'idle_no_telemetry'
    assert 'event_ingestion_missing' in payload['continuity_reason_codes']
    assert payload['ingestion_freshness'] == 'missing'
    assert payload['detection_pipeline_freshness'] == 'missing'
    assert payload['worker_heartbeat_freshness'] == 'missing'
    assert payload['event_throughput_window'] == 'no_events'


def test_continuity_continuous_no_evidence_when_polling_is_live_without_events() -> None:
    now = _now()
    payload = evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=10),
        last_event_at=None,
        last_detection_at=None,
        heartbeat_ttl_seconds=180,
        telemetry_window_seconds=120,
        detection_window_seconds=300,
    )
    assert payload['continuity_status'] == 'continuous_no_evidence'
    assert payload['ingestion_freshness'] == 'missing'
    assert payload['detection_pipeline_freshness'] == 'missing'
    assert payload['worker_heartbeat_freshness'] == 'fresh'
    assert payload['event_throughput_window'] == 'no_events'


def test_continuity_boundary_transition_fresh_to_stale_to_offline() -> None:
    now = _now()
    common = {
        'workspace_configured': True,
        'worker_running': True,
        'last_heartbeat_at': now - timedelta(seconds=10),
        'last_detection_at': now - timedelta(seconds=10),
        'heartbeat_ttl_seconds': 180,
        'telemetry_window_seconds': 120,
        'detection_window_seconds': 300,
    }
    fresh = evaluate_workspace_monitoring_continuity(
        now=now,
        last_event_at=now - timedelta(seconds=120),
        **common,
    )
    stale = evaluate_workspace_monitoring_continuity(
        now=now,
        last_event_at=now - timedelta(seconds=121),
        **common,
    )
    offline = evaluate_workspace_monitoring_continuity(
        now=now,
        last_event_at=now - timedelta(seconds=361),
        **common,
    )
    assert fresh['ingestion_freshness'] == 'fresh'
    assert fresh['event_throughput_window'] == 'in_window'
    assert stale['ingestion_freshness'] == 'stale'
    assert stale['event_throughput_window'] == 'out_of_window'
    assert offline['ingestion_freshness'] == 'offline'
    assert offline['event_throughput_window'] == 'offline'
