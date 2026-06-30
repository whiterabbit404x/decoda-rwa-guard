"""get_monitoring_health must keep the realtime WebSocket worker separate from the
stable RPC polling source.

monitoring_watcher_state is written ONLY by the realtime worker. When realtime is
disabled (BASE_REALTIME_ENABLED!=true) a stale/degraded watcher row must NOT mark
the independent stable polling source degraded — otherwise the UI shows a generic
"worker heartbeat is stale" even though stable polling is alive. The row is still
exposed as `realtime_watcher` so the runtime status can render a distinct realtime
/ provider section.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


_NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)

_RATE_LIMITED_WATCHER = {
    'watcher_name': 'base-realtime-worker-abc',
    'source_status': 'provider_rate_limited',
    'degraded': True,
    'degraded_reason': 'provider_rate_limited',
    'last_heartbeat_at': _NOW.isoformat(),
    'last_processed_block': 999,
    'metrics': {'rate_limited': True, 'next_retry_at': _NOW.isoformat(), 'active_provider_host': 'wss.example.com'},
}


class _HealthConn:
    """Dispatches the handful of queries get_monitoring_health runs."""

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM monitoring_worker_state' in q:
            # Stable polling worker: alive, fresh, NOT degraded.
            return _Result({
                'worker_name': 'monitoring-worker',
                'running': True,
                'status': 'running',
                'last_started_at': _NOW.isoformat(),
                'last_heartbeat_at': _NOW.isoformat(),
                'last_cycle_at': _NOW.isoformat(),
                'last_cycle_due_targets': 0,
                'last_cycle_targets_checked': 0,
                'last_cycle_alerts_generated': 0,
                'last_error': None,
                'updated_at': _NOW.isoformat(),
            })
        if 'overdue_count' in q:
            return _Result({'overdue_count': 0})
        if 'FROM background_jobs' in q:
            return _Result({'queued': 0, 'running': 0, 'failed': 0})
        if 'FROM monitoring_watcher_state' in q:
            # Realtime worker row: degraded + rate-limited.
            return _Result(dict(_RATE_LIMITED_WATCHER))
        if 'watcher_last_observed_block' in q:
            return _Result({'latest_processed_block': 1000, 'max_checkpoint_lag_blocks': 0, 'latest_checkpoint_at': _NOW.isoformat(), 'degraded_targets': 0, 'active_targets': 1})
        if 'FROM monitoring_event_receipts' in q:
            return _Result({'event_count': 3})
        return _Result({})


@contextmanager
def _fake_pg(conn):
    yield conn


def _patch_common(monkeypatch):
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HealthConn()))
    monkeypatch.setattr(
        monitoring_runner,
        'monitoring_ingestion_runtime',
        lambda: {'source': 'polling', 'degraded': False, 'reason': None, 'mode': 'live'},
    )
    monkeypatch.setattr(monitoring_runner, 'monitoring_operational_mode', lambda *a, **k: 'LIVE')
    monkeypatch.setattr(monitoring_runner, 'get_background_loop_health', lambda: {'loop_running': True})
    monkeypatch.setattr(monitoring_runner, 'monitoring_slo_snapshot', lambda _c: {})


def test_disabled_realtime_watcher_does_not_degrade_stable_source(monkeypatch):
    monkeypatch.delenv('BASE_REALTIME_ENABLED', raising=False)
    _patch_common(monkeypatch)

    health = monitoring_runner.get_monitoring_health()

    # Realtime facts are still exposed, separately.
    assert health['realtime_watcher'] is not None
    assert health['realtime_watcher']['source_status'] == 'provider_rate_limited'
    # ...but the stable polling source is NOT marked degraded by the realtime row.
    assert not health.get('degraded')
    assert health.get('degraded_reason') in (None, '')
    # Source type stays the stable polling source, not the stale realtime status.
    assert health['source_type'] == 'polling'


def test_enabled_realtime_watcher_still_propagates_degraded(monkeypatch):
    """When realtime IS enabled, a degraded realtime worker degrades the live source
    as before (no behavior regression for the enabled path)."""
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    _patch_common(monkeypatch)

    health = monitoring_runner.get_monitoring_health()

    assert health['realtime_watcher'] is not None
    assert health.get('degraded') is True
    assert health.get('degraded_reason') == 'provider_rate_limited'
