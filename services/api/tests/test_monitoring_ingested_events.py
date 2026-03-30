from __future__ import annotations

from datetime import datetime, timezone

from services.api.app.monitoring_runner import ActivityEvent, process_ingested_event


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, existing=False):
        self.existing = existing
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if 'SELECT id, name FROM workspaces' in query:
            return _Result({'id': params[0], 'name': 'ws'})
        if 'FROM monitoring_event_receipts' in query:
            return _Result({'id': 'existing'}) if self.existing else _Result(None)
        return _Result(None)


def test_process_ingested_event_duplicate(monkeypatch):
    conn = FakeConnection(existing=True)
    target = {
        'id': 't1',
        'workspace_id': 'w1',
        'updated_by_user_id': 'u1',
        'created_by_user_id': 'u1',
    }
    event = ActivityEvent(
        event_id='evt-1',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='websocket',
        cursor='1:0xabc:0',
        payload={'tx_hash': '0xabc', 'block_number': 1, 'log_index': 0},
    )
    result = process_ingested_event(conn, target=target, event=event)
    assert result['status'] == 'duplicate_suppressed'


def test_process_ingested_event_persists_receipt(monkeypatch):
    conn = FakeConnection(existing=False)
    target = {
        'id': 't1',
        'workspace_id': 'w1',
        'updated_by_user_id': 'u1',
        'created_by_user_id': 'u1',
        'severity_threshold': 'medium',
    }
    event = ActivityEvent(
        event_id='evt-2',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc_backfill',
        cursor='3:0xdef:2',
        payload={'tx_hash': '0xdef', 'block_number': 3, 'log_index': 2},
    )

    monkeypatch.setattr(
        'services.api.app.monitoring_runner._process_single_event',
        lambda *args, **kwargs: {'analysis_run_id': 'run-1', 'alert_id': None, 'incident_id': None, 'fallback_count': 0},
    )
    result = process_ingested_event(conn, target=target, event=event)
    assert result['status'] == 'processed'
    assert any('INSERT INTO monitoring_event_receipts' in query for query, _ in conn.calls)
    assert any('UPDATE targets' in query for query, _ in conn.calls)
