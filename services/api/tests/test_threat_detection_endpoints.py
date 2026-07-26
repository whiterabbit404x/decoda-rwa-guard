"""Threat Detection Engineer request endpoints — pagination, filters, workspace
isolation, and the telemetry list. pilot auth/DB are monkeypatched so the thin
request wrappers are covered without a live Postgres.
"""

from __future__ import annotations

import contextlib

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.domains.threat_detection import endpoints


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, *, tables=('threat_detections', 'telemetry_events', 'assets'), detection_rows=None, telemetry_rows=None, total=3):
        self.tables = set(tables)
        self.detection_rows = detection_rows or []
        self.telemetry_rows = telemetry_rows or []
        self.total = total
        self.select_params = None

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).lower()
        if 'to_regclass' in q:
            table = str((params or ('',))[0]).split('.')[-1]
            return _Result([{'ok': table in self.tables}])
        if 'count(*) as n from threat_detections' in q or 'count(*) as n from telemetry_events' in q:
            return _Result([{'n': self.total}])
        if 'from threat_detections td' in q:
            self.select_params = params
            return _Result(self.detection_rows)
        if 'from telemetry_events te' in q:
            self.select_params = params
            return _Result(self.telemetry_rows)
        return _Result([])

    def commit(self):
        pass


class _Request:
    def __init__(self, workspace='ws-1'):
        self.headers = {'x-workspace-id': workspace}


@pytest.fixture
def patched(monkeypatch):
    def _install(conn):
        monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
        monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
        monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'u-1'})
        monkeypatch.setattr(pilot, 'resolve_workspace', lambda c, uid, wsid: {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}})

        @contextlib.contextmanager
        def _pg():
            yield conn

        monkeypatch.setattr(pilot, 'pg_connection', _pg)
        return conn

    return _install


def _detection_row(**over):
    row = {
        'id': 'det-1', 'detection_type': 'unusual_transfer', 'title': 'Unusual fund movement', 'severity': 'high',
        'confidence': 0.7, 'status': 'open', 'chain_id': 8453, 'primary_asset_id': 'asset-1', 'asset_name': 'Bond',
        'evidence_source': 'live', 'evidence_quality': 'normalized_telemetry', 'baseline_window': '24h',
        'event_count': 3, 'actor_count': 1, 'transaction_count': 3, 'evidence_count': 3, 'explanation': 'x',
        'recommended_next_step': 'y', 'alert_eligible': True, 'ai_summary': 's', 'ai_summary_source': 'deterministic',
        'linked_alert_id': None, 'linked_incident_id': None, 'first_seen_at': None, 'last_seen_at': None, 'detected_at': None,
    }
    row.update(over)
    return row


def test_detections_pagination_and_workspace_scope(patched):
    conn = patched(FakeConn(detection_rows=[_detection_row()], total=42))
    result = endpoints.detections_endpoint(_Request(), limit=9999, offset=-5)
    assert result['total'] == 42
    assert result['limit'] == 200          # clamped
    assert result['offset'] == 0           # negative -> 0
    assert len(result['detections']) == 1
    # Workspace id is always the first bound parameter (isolation).
    assert conn.select_params[0] == 'ws-1'
    # limit/offset are the final two bound parameters.
    assert conn.select_params[-2] == 200
    assert conn.select_params[-1] == 0


def test_detections_invalid_severity_filter_rejected(patched):
    patched(FakeConn())
    with pytest.raises(HTTPException) as exc:
        endpoints.detections_endpoint(_Request(), severity='bogus')
    assert exc.value.status_code == 400


def test_detections_invalid_type_filter_rejected(patched):
    patched(FakeConn())
    with pytest.raises(HTTPException) as exc:
        endpoints.detections_endpoint(_Request(), detection_type='not_a_type')
    assert exc.value.status_code == 400


def test_detections_degraded_when_table_missing(patched):
    patched(FakeConn(tables=()))
    result = endpoints.detections_endpoint(_Request())
    assert result['degraded'] is True
    assert result['degraded_reason'] == 'detection_storage_provisioning'


def test_anomalies_endpoint_frames_not_promoted(patched):
    patched(FakeConn(detection_rows=[_detection_row(status='anomaly')], total=1))
    result = endpoints.anomalies_endpoint(_Request())
    assert 'anomalies' in result
    assert result['anomalies'][0]['not_promoted_reason']
    assert result['anomalies'][0]['promotion_state'] == 'below_threshold'


def test_telemetry_endpoint_pagination_and_quality(patched):
    telem = [{
        'id': 'tel-1', 'event_type': 'ownership_transferred', 'asset_id': 'asset-1', 'provider_type': 'quicknode',
        'evidence_source': 'live', 'observed_at': None, 'payload_json': {'tx_hash': '0xabc'}, 'asset_name': 'Bond',
    }]
    conn = patched(FakeConn(telemetry_rows=telem, total=1))
    result = endpoints.telemetry_endpoint(_Request(), limit=10, offset=0)
    assert result['total'] == 1
    row = result['telemetry'][0]
    assert row['tx_hash'] == '0xabc'
    # A decoded privileged event is labelled decoded_call, not normalized_telemetry.
    assert row['evidence_quality'] == 'decoded_call'
    assert conn.select_params[0] == 'ws-1'


def test_telemetry_endpoint_rejects_bad_source(patched):
    patched(FakeConn())
    with pytest.raises(HTTPException) as exc:
        endpoints.telemetry_endpoint(_Request(), evidence_source='bogus')
    assert exc.value.status_code == 400
