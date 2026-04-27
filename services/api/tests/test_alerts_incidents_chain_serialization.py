from __future__ import annotations

from contextlib import contextmanager

from fastapi import Request

from services.api.app import pilot


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        self.calls.append((normalized, params))
        if 'FROM alerts a' in normalized:
            return _Result(
                rows=[
                    {
                        'id': 'alert-1',
                        'detection_id': 'det-1',
                        'incident_id': 'inc-1',
                        'linked_action_id': 'action-1',
                        'linked_evidence_count': None,
                        'last_evidence_at': '2026-04-21T10:00:00Z',
                        'evidence_origin': 'live',
                        'tx_hash': '0xabc',
                        'block_number': 123,
                        'detector_kind': 'counterparty-anomaly',
                    }
                ]
            )
        if 'FROM incidents i' in normalized:
            return _Result(
                rows=[
                    {
                        'id': 'inc-1',
                        'linked_detection_id': 'det-1',
                        'source_alert_id': 'alert-1',
                        'linked_action_id': None,
                        'linked_evidence_count': None,
                        'last_evidence_at': '2026-04-21T10:00:00Z',
                        'evidence_origin': 'hybrid',
                        'tx_hash': '0xdef',
                        'block_number': 456,
                        'detector_kind': 'simulator-bridge',
                    }
                ]
            )
        return _Result()


@contextmanager
def _fake_pg(connection):
    yield connection


def _bootstrap(monkeypatch, connection):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda _connection, _user_id, _workspace_id: {'workspace_id': 'ws-1'},
    )


def test_list_alerts_serializes_chain_fields(monkeypatch):
    connection = _Conn()
    request = Request({'type': 'http', 'headers': []})
    _bootstrap(monkeypatch, connection)

    payload = pilot.list_alerts(request)

    row = payload['alerts'][0]
    assert row['linked_evidence_count'] == 0
    assert row['last_evidence_at'] == '2026-04-21T10:00:00Z'
    assert row['evidence_origin'] == 'live'
    assert row['tx_hash'] == '0xabc'
    assert row['block_number'] == 123
    assert row['detector_kind'] == 'counterparty-anomaly'
    assert row['chain_linked_ids'] == {
        'detection_id': 'det-1',
        'alert_id': 'alert-1',
        'incident_id': 'inc-1',
        'action_id': 'action-1',
    }
    alerts_query = next(statement for statement, _ in connection.calls if 'FROM alerts a' in statement)
    assert 'e.source_provider AS source_provider' in alerts_query
    assert 'WHERE a.workspace_id = %s' in alerts_query
    assert 'ORDER BY a.created_at DESC' in alerts_query


def test_list_incidents_serializes_chain_fields(monkeypatch):
    connection = _Conn()
    request = Request({'type': 'http', 'headers': []})
    _bootstrap(monkeypatch, connection)

    payload = pilot.list_incidents(request, status_value='open')

    row = payload['incidents'][0]
    assert row['linked_evidence_count'] == 0
    assert row['last_evidence_at'] == '2026-04-21T10:00:00Z'
    assert row['evidence_origin'] == 'hybrid'
    assert row['tx_hash'] == '0xdef'
    assert row['block_number'] == 456
    assert row['detector_kind'] == 'simulator-bridge'
    assert row['chain_linked_ids'] == {
        'detection_id': 'det-1',
        'alert_id': 'alert-1',
        'incident_id': 'inc-1',
        'action_id': None,
    }
    incidents_query, incidents_params = next((statement, params) for statement, params in connection.calls if 'FROM incidents i' in statement)
    assert 'WHERE i.workspace_id = %s' in incidents_query
    assert 'i.workflow_status = %s::text OR i.status = %s::text' in incidents_query
    assert incidents_params is not None
    assert incidents_params[5] == incidents_params[6] == incidents_params[7] == 'open'


def test_list_alerts_without_linked_evidence_stays_stable(monkeypatch):
    class _NoEvidenceConn(_Conn):
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            self.calls.append((normalized, params))
            if 'FROM alerts a' in normalized:
                return _Result(
                    rows=[
                        {
                            'id': 'alert-2',
                            'detection_id': None,
                            'incident_id': None,
                            'linked_action_id': None,
                            'linked_evidence_count': 0,
                            'last_evidence_at': None,
                            'evidence_origin': None,
                            'tx_hash': None,
                            'block_number': None,
                            'detector_kind': None,
                        }
                    ]
                )
            return _Result()

    connection = _NoEvidenceConn()
    request = Request({'type': 'http', 'headers': []})
    _bootstrap(monkeypatch, connection)

    payload = pilot.list_alerts(request, status_value='open')

    row = payload['alerts'][0]
    assert row['id'] == 'alert-2'
    assert row['linked_evidence_count'] == 0
    assert row['last_evidence_at'] is None
    assert row['evidence_origin'] is None


def test_list_incidents_preserves_chain_and_evidence_presence(monkeypatch):
    class _EvidenceConn(_Conn):
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            self.calls.append((normalized, params))
            if 'FROM incidents i' in normalized:
                return _Result(
                    rows=[
                        {
                            'id': 'inc-9',
                            'linked_detection_id': 'det-9',
                            'source_alert_id': 'alert-9',
                            'linked_action_id': 'action-9',
                            'linked_evidence_count': 3,
                            'last_evidence_at': '2026-04-23T10:00:00Z',
                            'evidence_origin': 'live',
                            'tx_hash': '0x999',
                            'block_number': 999,
                            'detector_kind': 'transfer-spike',
                        }
                    ]
                )
            return _Result()

    connection = _EvidenceConn()
    request = Request({'type': 'http', 'headers': []})
    _bootstrap(monkeypatch, connection)

    payload = pilot.list_incidents(request)

    row = payload['incidents'][0]
    assert row['chain_linked_ids'] == {
        'detection_id': 'det-9',
        'alert_id': 'alert-9',
        'incident_id': 'inc-9',
        'action_id': 'action-9',
    }
    assert row['linked_evidence_count'] == 3
    assert row['last_evidence_at'] == '2026-04-23T10:00:00Z'
    assert row['evidence_origin'] == 'live'
