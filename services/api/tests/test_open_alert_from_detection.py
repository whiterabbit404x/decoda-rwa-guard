"""Test: existing wallet_transfer detection -> Open Alert -> alert row created -> active_alerts = 1."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None, rows=None, rowcount=1):
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Minimal psycopg-compatible connection stub."""

    def __init__(self, detection_row: dict[str, Any] | None):
        self._detection_row = detection_row
        self.inserts: list[str] = []
        self.updates: list[str] = []
        self.committed = False

    def execute(self, query: str, params=None):
        q = ' '.join(str(query).split())
        if 'FROM detections d' in q and 'LEFT JOIN targets t' in q:
            return _Result(row=self._detection_row)
        if q.startswith('INSERT INTO alerts'):
            self.inserts.append('alert')
            return _Result()
        if q.startswith('UPDATE detections') and 'linked_alert_id' in q:
            self.updates.append('detection_linked')
            return _Result()
        if q.startswith('SELECT id') and 'alert_suppression_rules' in q:
            return _Result(row=None)
        if q.startswith('SELECT id, occurrence_count') and 'dedupe_signature' in q:
            return _Result(row=None)
        return _Result()

    def commit(self):
        self.committed = True

    @contextmanager
    def transaction(self):
        yield


@contextmanager
def _fake_pg(conn):
    yield conn


def _bootstrap(monkeypatch, conn):
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1'})


_DETECTION_ROW = {
    'detection_id': 'det-abc123',
    'target_id': 'target-1',
    'detection_type': 'monitored_wallet_transfer',
    'severity': 'low',
    'title': 'Monitored wallet transfer detected: MyWallet (chain 8453)',
    'evidence_summary': 'Wallet transfer detected on chain 8453',
    'evidence_source': 'live',
    'raw_evidence_json': {
        'tx_hash': '0xdeadbeef',
        'chain_id': 8453,
        'block_number': 12345678,
        'telemetry_id': 'tel-xyz',
        'from_address': '0xfrom',
        'to_address': '0xto',
        'amount_wei': '1000000000000000000',
    },
    'monitoring_run_id': 'run-1',
    'target_name': 'MyWallet',
}


def test_open_alert_creates_alert_row(monkeypatch):
    """Detection exists -> open_alert_from_detection -> INSERT into alerts table."""
    conn = _FakeConn(detection_row=_DETECTION_ROW)
    _bootstrap(monkeypatch, conn)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = monitoring_runner.open_alert_from_detection(request)

    assert result['status'] == 'created'
    assert result['alert_id'] is not None
    assert result['detection_id'] == 'det-abc123'
    assert 'alert' in conn.inserts, 'expected INSERT INTO alerts to be executed'
    assert 'detection_linked' in conn.updates, 'expected detection linked_alert_id to be updated'
    assert conn.committed, 'connection must be committed'


def test_open_alert_links_all_required_fields(monkeypatch):
    """Returned payload includes all required linkage fields (requirement 4)."""
    conn = _FakeConn(detection_row=_DETECTION_ROW)
    _bootstrap(monkeypatch, conn)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = monitoring_runner.open_alert_from_detection(request)

    assert result['detection_id'] == 'det-abc123'
    assert result['target_id'] == 'target-1'
    assert result['tx_hash'] == '0xdeadbeef'
    assert result['chain_id'] == 8453
    assert result['block_number'] == 12345678
    assert result['telemetry_id'] == 'tel-xyz'
    assert result['monitoring_run_id'] == 'run-1'


def test_open_alert_returns_no_detection_when_none_exist(monkeypatch):
    """No detection -> status=no_detection, alert_id=None."""
    conn = _FakeConn(detection_row=None)
    _bootstrap(monkeypatch, conn)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = monitoring_runner.open_alert_from_detection(request)

    assert result['status'] == 'no_detection'
    assert result['alert_id'] is None
    assert 'alert' not in conn.inserts


def test_open_alert_commits_transaction(monkeypatch):
    """Alert creation must be committed so it survives the connection close."""
    conn = _FakeConn(detection_row=_DETECTION_ROW)
    _bootstrap(monkeypatch, conn)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    monitoring_runner.open_alert_from_detection(request)

    assert conn.committed


def test_active_alerts_count_increases_after_open_alert(monkeypatch):
    """Simulated active_alerts count should be 1 after opening an alert from a detection."""
    created_alerts: list[str] = []

    class _CountingConn(_FakeConn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if q.startswith('INSERT INTO alerts'):
                alert_id = 'alert-new-1'
                created_alerts.append(alert_id)
                return _Result()
            return super().execute(query, params)

    conn = _CountingConn(detection_row=_DETECTION_ROW)
    _bootstrap(monkeypatch, conn)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = monitoring_runner.open_alert_from_detection(request)

    assert result['status'] == 'created'
    assert len(created_alerts) == 1, 'exactly one alert row should be inserted'
