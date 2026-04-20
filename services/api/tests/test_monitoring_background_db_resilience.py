from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_background_monitoring_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for monitoring background resilience tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _lifespan_test_client(api_main, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setattr(api_main, 'bootstrap_live_pilot', lambda: {'enabled': False, 'ran': False, 'applied_versions': []})
    monkeypatch.setattr(api_main, 'emit_startup_fixture_diagnostics', lambda: None)
    monkeypatch.setattr(api_main, 'seed_service', lambda *args, **kwargs: None)
    monkeypatch.setattr(api_main, 'seed_embedded_dependency_registry', lambda: None)
    with TestClient(api_main.app) as client:
        yield client


def test_loop_survives_db_error_and_marks_degraded_state(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    snapshots: list[dict[str, object]] = []
    attempts = {'value': 0}

    def _run_cycle(*_args, **_kwargs):
        attempts['value'] += 1
        raise RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable')

    async def _fake_sleep(seconds: float):
        sleep_calls.append(float(seconds))
        snapshots.append(dict(api_main.MONITORING_LOOP_RUNTIME_STATE))
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()
        return None

    monkeypatch.setattr(api_main, 'run_monitoring_cycle', _run_cycle)
    monkeypatch.setattr(api_main.asyncio, 'sleep', _fake_sleep)
    with _lifespan_test_client(api_main, monkeypatch):
        pass

    assert attempts['value'] >= 2
    assert sleep_calls == [10.0, 20.0]
    assert snapshots[0]['degraded'] is True
    assert snapshots[0]['classification'] == 'network_unreachable'
    assert snapshots[0]['reason'] == 'Database network unreachable'
    assert snapshots[0]['backoff_seconds'] == 10
    assert snapshots[0]['next_retry_at'] is not None
    assert snapshots[1]['state_downgraded'] is False


def test_db_backoff_progression_caps_and_quota_backoff_is_slower_than_network(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MONITOR_DB_RETRY_NETWORK_BASE_SECONDS', '10')
    monkeypatch.setenv('MONITOR_DB_RETRY_NETWORK_CAP_SECONDS', '120')
    monkeypatch.setenv('MONITOR_DB_RETRY_QUOTA_BASE_SECONDS', '60')
    monkeypatch.setenv('MONITOR_DB_RETRY_QUOTA_CAP_SECONDS', '900')

    # network progression should cap at 120
    network_sleep_calls: list[float] = []
    network_side_effects: Iterator[object] = iter([
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
    ])
    network_sleep_count = {'value': 0}

    def _network_run_cycle(*_args, **_kwargs):
        effect = next(network_side_effects)
        if isinstance(effect, Exception):
            raise effect
        return effect

    async def _network_fake_sleep(seconds: float):
        network_sleep_calls.append(float(seconds))
        network_sleep_count['value'] += 1
        if network_sleep_count['value'] >= 6:
            raise asyncio.CancelledError()
        return None

    monkeypatch.setattr(api_main, 'run_monitoring_cycle', _network_run_cycle)
    monkeypatch.setattr(api_main.asyncio, 'sleep', _network_fake_sleep)
    with _lifespan_test_client(api_main, monkeypatch):
        pass
    assert network_sleep_calls == [10.0, 20.0, 40.0, 80.0, 120.0, 120.0]

    # quota progression should be slower and larger on first retry.
    quota_sleep_calls: list[float] = []
    quota_side_effects: Iterator[object] = iter([
        RuntimeError('ERROR: Your account or project has exceeded the compute time quota.'),
        RuntimeError('ERROR: Your account or project has exceeded the compute time quota.'),
        RuntimeError('ERROR: Your account or project has exceeded the compute time quota.'),
        RuntimeError('ERROR: Your account or project has exceeded the compute time quota.'),
    ])
    quota_sleep_count = {'value': 0}

    def _quota_run_cycle(*_args, **_kwargs):
        effect = next(quota_side_effects)
        if isinstance(effect, Exception):
            raise effect
        return effect

    async def _quota_fake_sleep(seconds: float):
        quota_sleep_calls.append(float(seconds))
        quota_sleep_count['value'] += 1
        if quota_sleep_count['value'] >= 4:
            raise asyncio.CancelledError()
        return None

    monkeypatch.setattr(api_main, 'run_monitoring_cycle', _quota_run_cycle)
    monkeypatch.setattr(api_main.asyncio, 'sleep', _quota_fake_sleep)
    with _lifespan_test_client(api_main, monkeypatch):
        pass
    assert quota_sleep_calls == [60.0, 120.0, 240.0, 480.0]
    assert quota_sleep_calls[0] > network_sleep_calls[0]


def test_db_degraded_warning_is_suppressed_when_backoff_is_unchanged(
    api_main, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv('MONITOR_DB_RETRY_NETWORK_BASE_SECONDS', '10')
    monkeypatch.setenv('MONITOR_DB_RETRY_NETWORK_CAP_SECONDS', '120')

    side_effects: Iterator[object] = iter([
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
        RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable'),
    ])
    sleep_count = {'value': 0}

    def _run_cycle(*_args, **_kwargs):
        effect = next(side_effects)
        if isinstance(effect, Exception):
            raise effect
        return effect

    async def _fake_sleep(_seconds: float):
        sleep_count['value'] += 1
        if sleep_count['value'] >= 6:
            raise asyncio.CancelledError()
        return None

    monkeypatch.setattr(api_main, 'run_monitoring_cycle', _run_cycle)
    monkeypatch.setattr(api_main.asyncio, 'sleep', _fake_sleep)
    with caplog.at_level('WARNING'):
        with _lifespan_test_client(api_main, monkeypatch):
            pass

    degraded_logs = [r for r in caplog.records if 'event=background_monitoring_db_degraded classification=network_unreachable' in r.message]
    assert len(degraded_logs) == 5


def test_db_outage_never_reports_live_fresh_or_high_confidence_in_truth_summary() -> None:
    from datetime import datetime, timedelta, timezone

    from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary

    now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=None,
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='live',
        configured_systems=2,
        monitored_systems_count=2,
        reporting_systems=2,
        protected_assets=2,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=10),
        last_coverage_telemetry_at=now - timedelta(seconds=10),
        telemetry_kind='target_event',
        last_detection_at=now,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=2,
        valid_target_system_link_count=2,
        telemetry_window_seconds=300,
        active_alerts_count=0,
        active_incidents_count=0,
        db_persistence_available=False,
        db_persistence_reason='Monitoring persistence unavailable',
    )

    assert summary['runtime_status'] != 'live'
    assert summary['monitoring_status'] != 'live'
    assert summary['telemetry_freshness'] != 'fresh'
    assert summary['confidence'] != 'high'
    assert summary['db_failure_classification'] == 'persistence_unavailable'
