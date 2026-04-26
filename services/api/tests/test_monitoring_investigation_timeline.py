from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row


class _TimelineConnection:
    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM detections d' in normalized and 'detection_type = \'monitoring_proof_chain\'' in normalized:
            return _FakeResult(
                {
                    'detection_id': 'det-1',
                    'monitoring_run_id': 'run-1',
                    'linked_alert_id': 'alert-1',
                    'evidence_source': 'simulator',
                    'detected_at': datetime(2026, 4, 25, 9, 0, tzinfo=timezone.utc),
                    'raw_evidence_json': {'correlation_id': 'corr-1'},
                    'incident_id': 'inc-1',
                    'response_action_id': 'act-1',
                }
            )
        if 'WITH selected_telemetry AS (' in normalized:
            return _FakeResult(
                [
                    {
                        'item_id': 'alert-1',
                        'item_timestamp': datetime(2026, 4, 25, 9, 10, tzinfo=timezone.utc),
                        'link_name': 'alert',
                        'table_name': 'alerts',
                        'evidence_source': 'simulator',
                    },
                    {
                        'item_id': 'det-1',
                        'item_timestamp': datetime(2026, 4, 25, 9, 10, tzinfo=timezone.utc),
                        'link_name': 'detection',
                        'table_name': 'detections',
                        'evidence_source': 'live',
                    },
                    {
                        'item_id': 'ev-1',
                        'item_timestamp': datetime(2026, 4, 25, 9, 5, tzinfo=timezone.utc),
                        'link_name': 'telemetry',
                        'table_name': 'evidence',
                        'evidence_source': 'simulator_runtime',
                    },
                ]
            )
        raise AssertionError(f'unexpected query: {query} / {params}')


class _NoAnchorConnection:
    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM detections d' in normalized and 'detection_type = \'monitoring_proof_chain\'' in normalized:
            return _FakeResult(None)
        raise AssertionError(f'unexpected query: {query}')


class _FakePgContext:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self._connection

    def __exit__(self, exc_type, exc, tb):
        return False


def _request(workspace_id: str | None = 'ws-1'):
    headers = {}
    if workspace_id is not None:
        headers['x-workspace-id'] = workspace_id
    return type('Req', (), {'headers': headers})()


def test_get_monitoring_investigation_timeline_returns_ordered_items_and_missing_links(monkeypatch):
    workspace_id = '11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _FakePgContext(_TimelineConnection()))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda connection, request: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda connection, user_id, workspace_id: {'workspace_id': workspace_id})

    payload = pilot.get_monitoring_investigation_timeline(_request(workspace_id))

    assert payload['ok'] is True
    assert payload['workspace_id'] == workspace_id
    assert payload['proof_chain_status'] == 'incomplete'
    assert payload['correlation_id'] == 'corr-1'
    assert [item['link_name'] for item in payload['items']] == ['telemetry', 'detection', 'alert']
    timestamps = [item['timestamp'] for item in payload['items']]
    assert timestamps == sorted(timestamps)
    assert payload['items'][0]['evidence_source'] == 'simulator'
    assert payload['items'][1]['evidence_source'] == 'live'
    assert payload['missing'] == ['evidence', 'incident', 'response_action']


def test_get_monitoring_investigation_timeline_returns_all_links_missing_when_anchor_absent(monkeypatch):
    workspace_id = '11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _FakePgContext(_NoAnchorConnection()))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda connection, request: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda connection, user_id, workspace_id: {'workspace_id': workspace_id})

    payload = pilot.get_monitoring_investigation_timeline(_request(workspace_id))

    assert payload['proof_chain_status'] == 'incomplete'
    assert payload['items'] == []
    assert payload['missing'] == ['telemetry', 'detection', 'evidence', 'alert', 'incident', 'response_action']


def test_get_monitoring_investigation_timeline_requires_workspace_header(monkeypatch):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)

    with pytest.raises(HTTPException) as exc:
        pilot.get_monitoring_investigation_timeline(_request(None))

    assert exc.value.status_code == 400
    assert exc.value.detail == 'x-workspace-id header is required.'


class _CompleteTimelineConnection:
    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM detections d' in normalized and 'detection_type = \'monitoring_proof_chain\'' in normalized:
            return _FakeResult(
                {
                    'detection_id': 'det-9',
                    'monitoring_run_id': 'run-9',
                    'linked_alert_id': 'alert-9',
                    'evidence_source': 'live',
                    'detected_at': datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
                    'raw_evidence_json': {'correlation_id': 'corr-9'},
                    'incident_id': 'inc-9',
                    'response_action_id': 'act-9',
                }
            )
        if 'WITH selected_telemetry AS (' in normalized:
            return _FakeResult(
                [
                    {'item_id': 'ev-9', 'item_timestamp': datetime(2026, 4, 25, 11, 58, tzinfo=timezone.utc), 'link_name': 'telemetry', 'table_name': 'evidence', 'evidence_source': 'live'},
                    {'item_id': 'det-9', 'item_timestamp': datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc), 'link_name': 'detection', 'table_name': 'detections', 'evidence_source': 'live'},
                    {'item_id': 'de-9', 'item_timestamp': datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc), 'link_name': 'evidence', 'table_name': 'detection_evidence', 'evidence_source': 'live'},
                    {'item_id': 'alert-9', 'item_timestamp': datetime(2026, 4, 25, 12, 1, tzinfo=timezone.utc), 'link_name': 'alert', 'table_name': 'alerts', 'evidence_source': 'live'},
                    {'item_id': 'inc-9', 'item_timestamp': datetime(2026, 4, 25, 12, 2, tzinfo=timezone.utc), 'link_name': 'incident', 'table_name': 'incidents', 'evidence_source': 'live'},
                    {'item_id': 'act-9', 'item_timestamp': datetime(2026, 4, 25, 12, 3, tzinfo=timezone.utc), 'link_name': 'response_action', 'table_name': 'response_actions', 'evidence_source': 'live'},
                ]
            )
        raise AssertionError(f'unexpected query: {query} / {params}')


def test_get_monitoring_investigation_timeline_complete_chain_has_no_missing_links(monkeypatch):
    workspace_id = '11111111-1111-1111-1111-111111111111'
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _FakePgContext(_CompleteTimelineConnection()))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda connection, request: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda connection, user_id, workspace_id: {'workspace_id': workspace_id})

    payload = pilot.get_monitoring_investigation_timeline(_request(workspace_id))

    assert payload['proof_chain_status'] == 'complete'
    assert payload.get('missing', []) == []
    assert [item['link_name'] for item in payload['items']] == [
        'telemetry',
        'detection',
        'evidence',
        'alert',
        'incident',
        'response_action',
    ]
