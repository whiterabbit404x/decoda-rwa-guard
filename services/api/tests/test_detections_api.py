from __future__ import annotations

from contextlib import contextmanager

from fastapi import Request

from services.api.app import pilot


class _Result:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        self.calls.append(normalized)
        if normalized.startswith('SELECT id, workspace_id, monitored_system_id, protected_asset_id, detection_type,') and 'WHERE id =' in normalized:
            return _Result(
                row={
                    'id': 'det-1',
                    'workspace_id': 'ws-1',
                    'monitored_system_id': 'sys-1',
                    'protected_asset_id': 'asset-1',
                    'detection_type': 'monitoring_transfer',
                    'severity': 'high',
                    'confidence': 0.92,
                    'title': 'Matched risky transfer',
                    'evidence_summary': 'Counterparty rule triggered.',
                    'evidence_source': 'live',
                    'source_rule': 'counterparty_allowlist_violation',
                    'status': 'open',
                    'detected_at': '2026-04-18T00:00:00Z',
                    'raw_evidence_json': {},
                    'monitoring_run_id': 'run-1',
                    'linked_alert_id': 'alert-1',
                    'created_at': '2026-04-18T00:00:00Z',
                    'updated_at': '2026-04-18T00:00:00Z',
                }
            )
        if normalized.startswith('SELECT d.id, d.workspace_id, COALESCE(de_latest.evidence_summary, d.evidence_summary) AS evidence_summary'):
            return _Result(
                row={
                    'id': 'det-1',
                    'workspace_id': 'ws-1',
                    'evidence_summary': 'Counterparty rule triggered.',
                    'raw_evidence_json': {'event': {'tx_hash': '0xabc'}},
                    'raw_reference': 'trace://abc',
                    'evidence_type': 'transfer_event',
                    'evidence_source': 'simulator',
                    'detection_evidence_created_at': '2026-04-18T00:05:00Z',
                    'linked_alert_id': 'alert-1',
                    'monitoring_run_id': 'run-1',
                }
            )
        if normalized.startswith('SELECT d.id AS id, d.workspace_id AS workspace_id, d.monitored_system_id AS monitored_system_id, d.protected_asset_id AS protected_asset_id, d.detection_type AS detection_type,'):
            return _Result(
                rows=[
                    {
                        'id': 'det-1',
                        'workspace_id': 'ws-1',
                        'monitored_system_id': 'sys-1',
                        'protected_asset_id': 'asset-1',
                        'detection_type': 'monitoring_transfer',
                        'severity': 'high',
                        'confidence': 0.92,
                        'title': 'Matched risky transfer',
                        'evidence_summary': 'Counterparty rule triggered.',
                        'evidence_source': 'simulator',
                        'raw_reference': 'trace://abc',
                        'evidence_type': 'transfer_event',
                        'detection_evidence_created_at': '2026-04-18T00:05:00Z',
                        'source_rule': 'counterparty_allowlist_violation',
                        'status': 'open',
                        'detected_at': '2026-04-18T00:00:00Z',
                        'raw_evidence_json': {},
                        'monitoring_run_id': 'run-1',
                        'linked_alert_id': 'alert-1',
                        'linked_incident_id': 'inc-1',
                        'linked_action_id': 'action-1',
                        'linked_evidence_count': 2,
                        'last_evidence_at': '2026-04-18T01:00:00Z',
                        'last_evidence_source': 'chain-indexer',
                        'last_evidence_origin': 'simulator',
                        'chain_tx_hash': '0xfeed',
                        'chain_block_number': 12345,
                        'chain_detector_kind': 'contract-call-anomaly',
                        'created_at': '2026-04-18T00:00:00Z',
                        'updated_at': '2026-04-18T00:00:00Z',
                    }
                ]
            )
        return _Result()


@contextmanager
def _fake_pg(connection):
    yield connection


def test_list_detections_returns_workspace_rows(monkeypatch):
    connection = _Conn()
    request = Request({'type': 'http', 'headers': []})

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda _connection, _user_id, _workspace_id: {'workspace_id': 'ws-1'},
    )

    payload = pilot.list_detections(request, limit=10)

    assert len(payload['detections']) == 1
    assert payload['detections'][0]['id'] == 'det-1'
    assert payload['detections'][0]['linked_alert_id'] == 'alert-1'
    assert payload['detections'][0]['linked_evidence_count'] == 2
    assert payload['detections'][0]['tx_hash'] == '0xfeed'
    assert payload['detections'][0]['block_number'] == 12345
    assert payload['detections'][0]['detector_kind'] == 'contract-call-anomaly'
    assert payload['detections'][0]['chain_linked_ids']['incident_id'] == 'inc-1'
    assert payload['detections'][0]['evidence_source'] == 'simulator'
    assert payload['detections'][0]['evidence_origin_label'] == 'SIMULATED EVIDENCE'
    query = next(call for call in connection.calls if call.startswith('SELECT d.id AS id'))
    assert 'FROM detections d' in query
    assert 'FROM detection_evidence de' in query
    assert 'WHERE d.workspace_id = %s' in query
    assert 'ORDER BY d.detected_at DESC' in query


def test_get_detection_returns_detail(monkeypatch):
    connection = _Conn()
    request = Request({'type': 'http', 'headers': []})

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda _connection, _user_id, _workspace_id: {'workspace_id': 'ws-1'},
    )

    payload = pilot.get_detection('det-1', request)

    assert payload['detection']['id'] == 'det-1'
    assert payload['detection']['evidence_source'] == 'live'


def test_get_detection_evidence_returns_persisted_payload(monkeypatch):
    connection = _Conn()
    request = Request({'type': 'http', 'headers': []})

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda _connection, _user_id, _workspace_id: {'workspace_id': 'ws-1'},
    )

    payload = pilot.get_detection_evidence('det-1', request)

    assert payload['detection_id'] == 'det-1'
    assert payload['linked_alert_id'] == 'alert-1'
    assert payload['raw_evidence_json']['event']['tx_hash'] == '0xabc'
    assert payload['raw_reference'] == 'trace://abc'
    assert payload['evidence_source'] == 'simulator'
    assert payload['evidence_origin_label'] == 'SIMULATED EVIDENCE'
