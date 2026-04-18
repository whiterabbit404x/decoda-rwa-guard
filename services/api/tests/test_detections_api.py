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
    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
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
        if normalized.startswith('SELECT id, workspace_id, monitored_system_id, protected_asset_id, detection_type,'):
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
