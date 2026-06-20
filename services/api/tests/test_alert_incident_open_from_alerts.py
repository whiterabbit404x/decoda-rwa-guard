"""
Tests for the Alert → Incident workflow via POST /alerts/{alert_id}/escalate.

Requirements verified:
1. Escalating a non-resolved (open/acknowledged) alert creates an incident and links it.
2. Escalating returns incident_id and created=True on first call.
3. Escalating when alert.incident_id is already set returns the existing incident (idempotent).
4. Suppressed alerts cannot be escalated (404).
5. list_incidents returns the newly created incident.
6. Alert is linked to the incident via alerts.incident_id after escalation.
7. Second escalation of the same alert reuses the same incident (no duplicate).
8. Escalation carries target_id, severity, and summary into the incident row.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from services.api.app import pilot


# ── Shared fixtures ──────────────────────────────────────────────────────────

WS_ID = str(uuid.uuid4())
TARGET_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
ALERT_ID = str(uuid.uuid4())
INCIDENT_ID = str(uuid.uuid4())
TX_HASH = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'


def _make_request(workspace_id: str = WS_ID):
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


def _alert_row(*, incident_id=None, status='open') -> dict[str, Any]:
    return {
        'id': ALERT_ID,
        'workspace_id': WS_ID,
        'status': status,
        'incident_id': incident_id,
        'target_id': TARGET_ID,
        'analysis_run_id': None,
        'title': 'Monitored wallet transfer detected',
        'severity': 'critical',
        'summary': 'Large outbound transfer from monitored wallet.',
        'detection_id': str(uuid.uuid4()),
        'alert_type': 'threat_monitoring',
        'findings': {'tx_hash': TX_HASH, 'chain_id': 8453},
    }


# ── Minimal DB stub ──────────────────────────────────────────────────────────

class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _EscalateConn:
    """Tracks calls; responds realistically to the escalation SQL path."""

    def __init__(self, alert_row: dict | None, evidence_row: dict | None = None):
        self._alert_row = alert_row
        self._evidence_row = evidence_row
        self.executed: list[tuple[str, Any]] = []

    def execute(self, statement: str, params=None):
        norm = ' '.join(str(statement).split())
        self.executed.append((norm, params))

        # Alert SELECT
        if 'FROM alerts' in norm and 'status !=\'suppressed\'' in norm.replace(' ', '').replace('"', '\''):
            return _Result(self._alert_row)
        if 'FROM alerts' in norm and "status != 'suppressed'" in norm:
            return _Result(self._alert_row)
        # Fall-through: any SELECT from alerts
        if 'SELECT' in norm and 'FROM alerts' in norm:
            return _Result(self._alert_row)

        # Evidence SELECT
        if 'FROM evidence' in norm:
            return _Result(self._evidence_row)

        # Incident + alert UPDATE (WITH inserted_incident AS)
        if 'WITH inserted_incident AS' in norm:
            return _Result({'incident_id': INCIDENT_ID})

        # INSERT INTO alert_event_outbox / alert_events
        if 'INSERT INTO alert_event' in norm:
            return _Result({'id': str(uuid.uuid4())})

        return _Result()

    def commit(self):
        pass


def _bootstrap(monkeypatch, conn):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_: ({'id': USER_ID}, {'workspace_id': WS_ID}))


# ── Test 1: open alert can be escalated (no resolved-only restriction) ───────

def test_open_alert_can_be_escalated_to_incident(monkeypatch):
    """An alert with status='open' must be escalatable — the old 'resolved' guard is removed."""
    conn = _EscalateConn(_alert_row(status='open'))
    _bootstrap(monkeypatch, conn)

    result = pilot.escalate_alert_to_incident(
        ALERT_ID, {'title': 'Test escalation', 'summary': 'test'}, _make_request()
    )

    assert result['incident_id'], 'must return a non-empty incident_id'
    assert result['alert_id'] == ALERT_ID
    assert result.get('created') is True


def test_acknowledged_alert_can_be_escalated(monkeypatch):
    """An alert with status='acknowledged' must also be escalatable."""
    conn = _EscalateConn(_alert_row(status='acknowledged'))
    _bootstrap(monkeypatch, conn)

    result = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert result['incident_id']
    assert result.get('created') is True


# ── Test 2: incident created and linked to alert ─────────────────────────────

def test_escalation_creates_incident_row(monkeypatch):
    """Escalation must INSERT an incident row with source_alert_id=alert_id."""
    conn = _EscalateConn(_alert_row(status='open'))
    _bootstrap(monkeypatch, conn)

    pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    incident_statements = [
        stmt for stmt, _ in conn.executed if 'WITH inserted_incident AS' in stmt
    ]
    assert incident_statements, 'INSERT into incidents must be executed'
    # The CTE must update alerts.incident_id in the same statement
    assert any('UPDATE alerts SET incident_id' in s for s, _ in conn.executed
               if 'inserted_incident' in s or 'UPDATE alerts' in s), (
        'alerts.incident_id must be set in the same transaction as incident INSERT'
    )


def test_escalation_links_alert_to_incident(monkeypatch):
    """After escalation the WITH...INSERT+UPDATE pattern must link alerts.incident_id."""
    conn = _EscalateConn(_alert_row(status='open'))
    _bootstrap(monkeypatch, conn)

    result = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    # The CTE UPDATE must reference the alert_id in its WHERE clause
    cte_params = [
        params for stmt, params in conn.executed if 'WITH inserted_incident AS' in stmt
    ]
    assert cte_params, 'CTE must have been executed'
    params = cte_params[0]
    assert ALERT_ID in params, (
        f'ALERT_ID must appear in the CTE params so alerts.incident_id is set; params={params}'
    )
    assert result['incident_id'], 'result must contain a non-empty incident_id'
    uuid.UUID(result['incident_id'])  # must be a valid UUID


def test_escalation_carries_target_id_into_incident(monkeypatch):
    """Incident INSERT must carry the alert's target_id."""
    conn = _EscalateConn(_alert_row(status='open'))
    _bootstrap(monkeypatch, conn)

    pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    cte_params = [
        params for stmt, params in conn.executed if 'WITH inserted_incident AS' in stmt
    ]
    assert cte_params
    params = cte_params[0]
    assert TARGET_ID in params, (
        f'target_id {TARGET_ID} must be in incident INSERT params; got {params}'
    )


def test_escalation_carries_severity_into_incident(monkeypatch):
    """Incident INSERT must carry the alert's severity."""
    conn = _EscalateConn(_alert_row(status='open'))
    _bootstrap(monkeypatch, conn)

    pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    cte_params = [
        params for stmt, params in conn.executed if 'WITH inserted_incident AS' in stmt
    ]
    assert cte_params
    params = cte_params[0]
    assert 'critical' in params, (
        f'severity=critical must be in incident INSERT params; got {params}'
    )


# ── Test 3: idempotency — second escalation returns existing incident ─────────

def test_escalation_idempotent_returns_existing_incident(monkeypatch):
    """If alert.incident_id is already set, escalation must return it without creating a new one."""
    existing_incident_id = str(uuid.uuid4())
    conn = _EscalateConn(_alert_row(status='open', incident_id=existing_incident_id))
    _bootstrap(monkeypatch, conn)

    result = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert result['incident_id'] == existing_incident_id, (
        'must return the already-linked incident_id, not create a new one'
    )
    assert result.get('created') is False


def test_escalation_idempotent_does_not_insert_duplicate_incident(monkeypatch):
    """If alert.incident_id is already set, no INSERT INTO incidents must occur."""
    existing_incident_id = str(uuid.uuid4())
    conn = _EscalateConn(_alert_row(status='open', incident_id=existing_incident_id))
    _bootstrap(monkeypatch, conn)

    pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    incident_inserts = [
        stmt for stmt, _ in conn.executed if 'WITH inserted_incident AS' in stmt
    ]
    assert not incident_inserts, (
        'no INSERT into incidents must happen when alert.incident_id is already set'
    )


# ── Test 4: suppressed alert cannot be escalated ─────────────────────────────

def test_suppressed_alert_returns_404(monkeypatch):
    """A suppressed alert must not be escalatable — escalate must raise 404."""
    from fastapi import HTTPException

    # Suppressed alert is filtered out by the WHERE clause, so fetchone() returns None
    conn = _EscalateConn(None)
    _bootstrap(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert exc_info.value.status_code == 404


# ── Test 5: list_incidents returns newly escalated incident ───────────────────

def test_list_incidents_returns_escalated_incident(monkeypatch):
    """After escalation, list_incidents must include the created incident row."""

    class _ListConn:
        def execute(self, stmt, params=None):
            norm = ' '.join(str(stmt).split())
            if 'FROM incidents i' in norm:
                return _Result(rows=[{
                    'id': INCIDENT_ID,
                    'event_type': 'alert_escalation',
                    'title': 'Escalated alert: Monitored wallet transfer detected',
                    'severity': 'critical',
                    'status': 'open',
                    'workflow_status': 'open',
                    'target_id': TARGET_ID,
                    'source_alert_id': ALERT_ID,
                    'linked_alert_ids': json.dumps([ALERT_ID]),
                    'owner': None,
                    'owner_user_id': None,
                    'assignee_user_id': None,
                    'summary': 'Large outbound transfer.',
                    'resolution_note': None,
                    'resolution_notes': None,
                    'timeline': json.dumps([]),
                    'created_at': '2026-06-20T10:00:00Z',
                    'updated_at': '2026-06-20T10:00:00Z',
                    'linked_detection_id': None,
                    'linked_evidence_count': 0,
                    'last_evidence_at': None,
                    'evidence_source': 'live',
                    'tx_hash': TX_HASH,
                    'block_number': None,
                    'detector_kind': None,
                    'evidence_origin': 'live',
                    'linked_action_id': None,
                    'response_action_mode': None,
                }])
            return _Result()

    @contextmanager
    def _fake_pg():
        yield _ListConn()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda *_: {'workspace_id': WS_ID, 'workspace': {'id': WS_ID}})

    scope = {
        'type': 'http', 'method': 'GET', 'path': '/incidents',
        'query_string': b'',
        'headers': [(b'x-workspace-id', WS_ID.encode())],
        'client': ('127.0.0.1', 9000),
    }
    from fastapi import Request
    request = Request(scope)
    result = pilot.list_incidents(request)

    incidents = result['incidents']
    assert len(incidents) == 1, f'list_incidents must return the escalated incident; got {len(incidents)}'
    inc = incidents[0]
    assert inc['id'] == INCIDENT_ID
    assert inc['source_alert_id'] == ALERT_ID
    assert inc['severity'] == 'critical'
    assert inc['workflow_status'] == 'open'


# ── Test 6: response fields are complete ─────────────────────────────────────

def test_escalation_response_includes_all_required_fields(monkeypatch):
    """escalate_alert_to_incident must return incident_id, alert_id, status, created."""
    conn = _EscalateConn(_alert_row(status='open'))
    _bootstrap(monkeypatch, conn)

    result = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert 'incident_id' in result, 'response must include incident_id'
    assert 'alert_id' in result, 'response must include alert_id'
    assert 'status' in result, 'response must include status'
    assert 'created' in result, 'response must include created flag'
    assert result['alert_id'] == ALERT_ID


# ── Test 7: action history written on successful escalation ───────────────────

def test_escalation_writes_action_history(monkeypatch):
    """escalate_alert_to_incident must write alert.escalated_to_incident action history."""
    conn = _EscalateConn(_alert_row(status='open'))
    _bootstrap(monkeypatch, conn)

    pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    history_stmts = [
        params for stmt, params in conn.executed if 'INSERT INTO action_history' in stmt
    ]
    assert history_stmts, 'action_history entries must be written'
    action_types = [p[6] for p in history_stmts if len(p) > 6]
    assert 'alert.escalated_to_incident' in action_types, (
        'alert.escalated_to_incident action must be recorded'
    )
    assert 'incident.created_from_alert' in action_types, (
        'incident.created_from_alert action must be recorded'
    )


# ── Test 8: existing test compatibility — resolved alert still works ──────────

def test_resolved_alert_can_still_be_escalated(monkeypatch):
    """Previously resolved alerts must still be escalatable (backward compatibility)."""
    conn = _EscalateConn(_alert_row(status='resolved'))
    _bootstrap(monkeypatch, conn)

    result = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert result['incident_id'], 'resolved alert must still produce an incident_id'
    assert result.get('created') is True
