from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None):
        self._row = row or {}

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, evidence_at: datetime | None):
        self.evidence_at = evidence_at

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM alerts' in q:
            return _Result({'c': 1})
        if 'FROM incidents' in q:
            return _Result({'c': 1})
        if 'FROM targets' in q:
            return _Result({'c': 2})
        if 'FROM evidence' in q:
            return _Result({'observed_at': self.evidence_at, 'block_number': 123})
        return _Result({})


@contextmanager
def _fake_pg(conn):
    yield conn


def test_runtime_status_idle_when_worker_healthy_without_recent_evidence(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Idle'


def test_runtime_status_active_with_recent_evidence(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'websocket'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Active'


def test_runtime_status_degraded_on_stale_heartbeat(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': (now - timedelta(minutes=20)).isoformat(), 'last_cycle_at': (now - timedelta(minutes=20)).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Degraded'
