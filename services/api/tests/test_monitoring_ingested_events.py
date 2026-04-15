from __future__ import annotations

from datetime import datetime, timezone

from services.api.app.monitoring_runner import ActivityEvent, _persist_evidence, mark_receipt_removed, process_ingested_event


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
        if 'SELECT id, workspace_id\n        FROM monitoring_event_receipts' in query:
            return _Result({'id': 'receipt-1', 'workspace_id': 'w1'})
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


def test_mark_receipt_removed_marks_row_and_reorg_event():
    conn = FakeConnection(existing=False)
    mark_receipt_removed(
        conn,
        target_id='t1',
        event_cursor='100:0xtx:1',
        tx_hash='0xtx',
        log_index=1,
        metadata={'chain_network': 'ethereum', 'block_number': 100, 'removed': True},
    )
    assert any('UPDATE monitoring_event_receipts SET removed = TRUE' in query for query, _ in conn.calls)
    assert any('INSERT INTO monitoring_reorg_events' in query for query, _ in conn.calls)
    assert any('chain_reorg_invalidated_evidence' in str(params) for _, params in conn.calls if params)


def test_persist_evidence_records_detection_linkage_and_evidence_fields():
    conn = FakeConnection(existing=False)
    now = datetime.now(timezone.utc)
    target = {
        'id': 'target-1',
        'asset_id': 'asset-1',
        'chain_network': 'ethereum-mainnet',
        'monitored_system_id': 'sys-1',
    }
    event = ActivityEvent(
        event_id='evt-3',
        kind='transaction',
        observed_at=now,
        ingestion_source='evm_rpc',
        cursor='22:0xabc:0',
        payload={'tx_hash': '0xabc', 'block_number': 22, 'log_index': 0, 'event_type': 'transfer', 'metadata': {'provider_name': 'alchemy'}},
    )
    response = {'severity': 'high', 'score': 88, 'explanation': 'High-confidence anomaly from live telemetry.'}

    _persist_evidence(
        conn,
        workspace_id='ws-1',
        target=target,
        event=event,
        response=response,
        alert_id='alert-1',
    )

    evidence_calls = [(query, params) for query, params in conn.calls if 'INSERT INTO evidence' in query]
    assert len(evidence_calls) == 1
    _, params = evidence_calls[0]
    assert params[1] == 'ws-1'  # workspace_id
    assert params[2] == 'asset-1'  # asset_id
    assert params[3] == 'target-1'  # target_id
    assert params[4] == 'alert-1'  # alert linkage
    assert params[10] == 'sys-1'  # monitored_system_id
    assert params[12] == 88  # risk_score
    assert params[13] == 'High-confidence anomaly from live telemetry.'  # evidence summary
    assert params[18] == 'evm_rpc'  # evidence source
    assert params[20] == now  # timestamp / observed_at
