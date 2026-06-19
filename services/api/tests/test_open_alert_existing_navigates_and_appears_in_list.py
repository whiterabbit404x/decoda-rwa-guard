"""Regression: Open Alert on a detection that already has an alert must:
  - not create a duplicate alert row,
  - return the existing alert_id (not just an error message),
  - have that alert appear in /alerts list with status='open' and severity='critical',
  - be counted by the active-alerts / critical / high-confidence count cards.

This locks the end-to-end contract for task 7 (acceptance criteria):
  already_exists -> alert_id returned -> /alerts includes it -> count cards reflect it.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from services.api.app import monitoring_runner


# ── Shared fixtures ──────────────────────────────────────────────────────────

WS_ID = 'aaaabbbb-0000-4000-8000-111122223333'
TARGET_ID = 'ddddeeee-0000-4000-8000-555566667777'
EXISTING_ALERT_ID = 'e39485a5-2652-4950-9141-2aa6fe79bea1'
TX_HASH = '0x' + 'c' * 62 + '90c7'


def _make_request_mr(workspace_id: str = WS_ID) -> Any:
    from types import SimpleNamespace
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


def _make_request_pilot(workspace_id: str = WS_ID) -> Any:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/alerts',
        'query_string': b'',
        'headers': [(b'x-workspace-id', workspace_id.encode())],
        'client': ('127.0.0.1', 9000),
    }
    from fastapi import Request
    return Request(scope)


# ── Fake DB connections ──────────────────────────────────────────────────────

class _Result:
    def __init__(self, row=None, rows=None, rowcount=1):
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _AlreadyLinkedConn:
    """Detection is already linked to EXISTING_ALERT_ID; Open Alert must not insert."""

    def __init__(self):
        self.inserts: list[str] = []
        self.committed = False

    def execute(self, query: str, params=None):
        q = ' '.join(str(query).split())
        # First query: detection missing an alert → None (it IS linked)
        if 'FROM detections d' in q and 'LEFT JOIN targets t' in q:
            return _Result(row=None)
        # Second query: detection with existing linked alert → existing record
        if 'FROM detections d' in q and 'JOIN alerts a' in q:
            return _Result(row={
                'detection_id': str(uuid.uuid4()),
                'target_id': TARGET_ID,
                'alert_id': EXISTING_ALERT_ID,
            })
        if q.startswith('INSERT INTO alerts'):
            self.inserts.append('alert')
            return _Result()
        return _Result()

    def commit(self):
        self.committed = True

    @contextmanager
    def transaction(self):
        yield


DETECTION_ID_FOR_SUPPRESSED = str(uuid.uuid4())


class _SuppressedButExistingConn:
    """Detection found (linked_alert_id IS NULL) but a suppression rule fires.
    An alert linked to this detection already exists in the DB from a prior backfill run.
    Open Alert must surface the existing alert_id instead of returning alert_id=None."""

    def __init__(self):
        self.inserts: list[str] = []
        self.committed = False

    def execute(self, query: str, params=None):
        q = ' '.join(str(query).split())
        # Detection query (linked_alert_id IS NULL found) → detection exists without link
        if 'FROM detections d' in q and 'LEFT JOIN targets t' in q:
            return _Result(row={
                'detection_id': DETECTION_ID_FOR_SUPPRESSED,
                'target_id': TARGET_ID,
                'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
                'severity': 'critical',
                'title': 'SIG detection',
                'evidence_summary': 'Outbound movement',
                'evidence_source': 'live',
                'raw_evidence_json': {'tx_hash': TX_HASH, 'chain_id': 8453, 'block_number': 100},
                'monitoring_run_id': None,
                'target_name': 'Test Target',
            })
        # Suppression check in _upsert_alert → rule matched, suppress the insert
        if 'FROM alert_suppression_rules' in q:
            return _Result(row={'id': 'suppression-rule-id'})
        # Post-suppression lookup by detection_id → existing alert found
        if 'FROM alerts' in q and 'detection_id' in q and not q.startswith('UPDATE') and not q.startswith('INSERT'):
            return _Result(row={'id': EXISTING_ALERT_ID})
        if q.startswith('INSERT INTO alerts'):
            self.inserts.append('alert')
            return _Result()
        return _Result()

    def commit(self):
        self.committed = True

    @contextmanager
    def transaction(self):
        yield


@contextmanager
def _fake_pg_mr(conn):
    yield conn


def _bootstrap_mr(monkeypatch, conn):
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg_mr(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WS_ID})
    monkeypatch.setattr(monitoring_runner, 'promote_wallet_transfer_alerts', lambda *_a, **_k: 0)


# ── Test 1: already_exists path returns alert_id (no duplicate) ──────────────

def test_already_exists_returns_alert_id_not_error(monkeypatch):
    """When the detection already has a linked alert, the response contains alert_id."""
    conn = _AlreadyLinkedConn()
    _bootstrap_mr(monkeypatch, conn)

    result = monitoring_runner.open_alert_from_detection(_make_request_mr())

    assert result['status'] == 'already_exists'
    assert result['alert_id'] == EXISTING_ALERT_ID
    assert 'alert' not in conn.inserts, 'no duplicate alert must be inserted'


def test_already_exists_includes_detection_id(monkeypatch):
    """The already_exists response carries detection_id for frontend navigation."""
    conn = _AlreadyLinkedConn()
    _bootstrap_mr(monkeypatch, conn)

    result = monitoring_runner.open_alert_from_detection(_make_request_mr())

    assert result['status'] == 'already_exists'
    assert result['alert_id'] is not None
    assert result['detection_id'] is not None


# ── Test 2: /alerts list includes the existing alert with correct normalisation ─

def _existing_alert_row(workspace_id: str = WS_ID) -> dict[str, Any]:
    """The alert row as stored in DB: status='new', severity='CRITICAL', rule_key set in payload."""
    return {
        'id': EXISTING_ALERT_ID,
        'alert_type': 'threat_monitoring',
        'title': 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
        'severity': 'CRITICAL',
        'status': 'new',
        'summary': 'Outbound movement detected.',
        'module_key': 'strategic_infrastructure_guard',
        'target_id': TARGET_ID,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'live',
        'source_service': 'threat-engine',
        'created_at': '2026-06-13T00:00:00Z',
        'updated_at': None,
        'opened_at': None,
        'payload': {
            'rule_key': 'strategic_infrastructure_guard_wallet_outbound_transfer',
            'evidence_source': 'live',
            'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
            'tx_hash': TX_HASH,
            'confidence': 'high',
        },
        'linked_evidence_count': 0,
        'evidence_source': 'live',
        'tx_hash': TX_HASH,
        'block_number': '100',
        'from_address': '0xabc',
        'to_address': '0xdef',
        'amount_wei': '1000',
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
        'detector_kind': None,
        'evidence_origin': 'live',
        'linked_action_id': None,
        'response_action_mode': None,
        'workspace_id': workspace_id,
    }


class _ListConn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def execute(self, sql, params=None):
        class R:
            def fetchall(inner):
                return [dict(r) for r in self.rows]
        return R()


def _run_list_alerts(rows: list[dict[str, Any]], ws_id: str = WS_ID, **kw):
    from services.api.app.pilot import list_alerts
    conn = _ListConn(rows)
    with (
        patch('services.api.app.pilot.require_live_mode'),
        patch('services.api.app.pilot.pg_connection') as mock_pg,
        patch('services.api.app.pilot.ensure_pilot_schema'),
        patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': 'u1'}),
        patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': ws_id}),
    ):
        mock_pg.return_value.__enter__ = lambda s: conn
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        return list_alerts(_make_request_pilot(ws_id), **kw)


def test_existing_alert_appears_in_list_no_status_filter():
    """After already_exists, the existing alert is returned by /alerts with no filter."""
    result = _run_list_alerts([_existing_alert_row()], limit=50, offset=0)
    alerts = result['alerts']
    assert len(alerts) == 1
    assert alerts[0]['id'] == EXISTING_ALERT_ID


def test_existing_alert_normalised_to_open_critical():
    """The existing alert (status='new', severity='CRITICAL') is normalised to open/critical."""
    alerts = _run_list_alerts([_existing_alert_row()], limit=50, offset=0)['alerts']
    assert alerts[0]['status'] == 'open'
    assert alerts[0]['severity'] == 'critical'


def test_existing_alert_appears_under_open_status_filter():
    """Status='open' quick filter must surface the existing alert stored as status='new'."""
    alerts = _run_list_alerts([_existing_alert_row()], status_value='open', limit=50, offset=0)['alerts']
    assert len(alerts) == 1


def test_count_cards_include_existing_alert():
    """Count cards must count the existing alert: active=1, critical=1, high_confidence=1, linked=0."""
    alerts = _run_list_alerts([_existing_alert_row()], limit=50, offset=0)['alerts']
    active = sum(1 for a in alerts if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'})
    critical = sum(1 for a in alerts if str(a.get('severity') or '').lower() == 'critical')
    high_conf = sum(
        1 for a in alerts
        if str(a.get('evidence_source') or '').lower() in {'live', 'live_provider'} and a.get('tx_hash')
    )
    linked = sum(1 for a in alerts if a.get('incident_id'))
    assert active == 1
    assert critical == 1
    assert high_conf == 1
    assert linked == 0


def test_no_duplicate_on_second_open_alert(monkeypatch):
    """Calling Open Alert twice for the same detection never inserts a second alert row."""
    conn = _AlreadyLinkedConn()
    _bootstrap_mr(monkeypatch, conn)

    r1 = monitoring_runner.open_alert_from_detection(_make_request_mr())
    r2 = monitoring_runner.open_alert_from_detection(_make_request_mr())

    assert r1['status'] == 'already_exists'
    assert r2['status'] == 'already_exists'
    assert r1['alert_id'] == r2['alert_id'] == EXISTING_ALERT_ID
    assert 'alert' not in conn.inserts, 'no alert rows must be inserted on repeated Open Alert'


# ── Test 3: suppressed-but-existing path returns alert_id (Fix 4) ─────────────

def test_suppressed_returns_existing_alert_id(monkeypatch):
    """When _upsert_alert returns '' (suppression rule matched), the response must
    contain the existing alert_id found in the DB — not null — so the frontend can
    navigate to the existing alert rather than showing a dead-end toast."""
    conn = _SuppressedButExistingConn()
    _bootstrap_mr(monkeypatch, conn)

    result = monitoring_runner.open_alert_from_detection(_make_request_mr())

    assert result['alert_id'] == EXISTING_ALERT_ID, (
        'suppressed path must surface the existing alert_id, not None'
    )
    assert result['status'] in ('already_exists', 'created'), (
        f"expected already_exists/created, got {result['status']!r}"
    )
    assert 'alert' not in conn.inserts, 'suppressed path must not insert a new alert row'


def test_suppressed_no_insert_on_suppression_rule(monkeypatch):
    """A suppression rule must never cause a new alert row to be inserted."""
    conn = _SuppressedButExistingConn()
    _bootstrap_mr(monkeypatch, conn)

    monitoring_runner.open_alert_from_detection(_make_request_mr())

    assert conn.inserts == [], 'no INSERT INTO alerts must occur when suppression rule fires'


# ── Test 4: suppressed-but-linked normalization in list_alerts (Fix 2/3) ──────

def _existing_suppressed_alert_row(workspace_id: str = WS_ID) -> dict:
    """Alert with status='suppressed' but detection_id set — should normalise to open/critical."""
    row = _existing_alert_row(workspace_id)
    row['status'] = 'suppressed'
    return row


def test_suppressed_linked_alert_normalised_to_open():
    """A wallet-transfer alert with status='suppressed' and a detection_id must be
    normalised to status='open' for display (it was suppressed as a duplicate, not
    because a human resolved it)."""
    alerts = _run_list_alerts([_existing_suppressed_alert_row()], limit=50, offset=0)['alerts']
    assert len(alerts) == 1
    assert alerts[0]['status'] == 'open', (
        f"suppressed+linked wallet-transfer alert must normalise to open, got {alerts[0]['status']!r}"
    )
    assert alerts[0]['severity'] == 'critical'


def test_suppressed_linked_alert_appears_under_open_filter():
    """The 'open' status quick-filter must surface suppressed-but-linked wallet-transfer alerts."""
    alerts = _run_list_alerts(
        [_existing_suppressed_alert_row()], status_value='open', limit=50, offset=0
    )['alerts']
    assert len(alerts) == 1, (
        'suppressed-but-linked alert must appear under status_value=open filter'
    )


def test_suppressed_linked_alert_counted_as_active():
    """Count cards must include suppressed-but-linked wallet-transfer alerts."""
    alerts = _run_list_alerts([_existing_suppressed_alert_row()], limit=50, offset=0)['alerts']
    active = sum(
        1 for a in alerts
        if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'}
    )
    assert active == 1
