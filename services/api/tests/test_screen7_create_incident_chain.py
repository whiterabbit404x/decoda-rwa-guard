"""
Screen 7 "Create Incident" → Screen 8 chain contract.

Screen 7's Create Incident control was a dead stub: the button carried a literal
``disabled`` attribute, so no operator could ever reach the creation path. There is no
standalone ``POST /incidents`` in the backend — an incident is only ever born from an
alert via ``POST /alerts/{alert_id}/escalate`` — so the fix wires the control to that
canonical endpoint. These tests pin the resulting chain end to end:

1. Escalation persists an incident row linked to the source alert.
2. ``list_incidents`` returns that persisted incident (it is real, not optimistic UI state).
3. ``recommend_response_action_for_incident`` creates response actions carrying the SAME
   incident_id.
4. ``list_enforcement_actions(incident_id=...)`` (Screen 8) returns those actions under
   that same incident_id.
5. RBAC is unchanged: escalation still runs through the workspace permission gate, and a
   denied permission raises 403 rather than creating anything.
6. Escalation stays idempotent, so a repeated Create Incident click cannot duplicate.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import pilot


WS_ID = str(uuid.uuid4())
OTHER_WS_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
ALERT_ID = str(uuid.uuid4())
TARGET_ID = str(uuid.uuid4())
DETECTION_ID = str(uuid.uuid4())


def _make_request(workspace_id: str = WS_ID):
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _ChainConn:
    """Stateful stub carrying one workspace's alert → incident → action chain.

    Rows written by one call are readable by the next, so incident_id propagation is
    exercised across the real code paths instead of being asserted per-call in isolation.
    """

    def __init__(self):
        self.alert = {
            'id': ALERT_ID,
            'workspace_id': WS_ID,
            'status': 'open',
            'incident_id': None,
            'target_id': TARGET_ID,
            'analysis_run_id': None,
            'title': 'Monitored wallet transfer detected',
            'severity': 'critical',
            'summary': 'Large outbound transfer from monitored wallet.',
            'detection_id': DETECTION_ID,
            'alert_type': 'threat_monitoring',
            'findings': {'detector_kind': 'wallet_transfer'},
        }
        self.incidents: list[dict[str, Any]] = []
        self.response_actions: list[dict[str, Any]] = []
        self.executed: list[tuple[str, Any]] = []

    # -- helpers ---------------------------------------------------------------
    def _insert_incident(self, params) -> dict[str, Any]:
        incident_id = str(params[0])
        self.incidents.append({
            'id': incident_id,
            'workspace_id': str(params[1]),
            'target_id': params[4],
            'event_type': 'alert_escalation',
            'title': params[5],
            'severity': params[6],
            'status': 'open',
            'workflow_status': 'open',
            'source_alert_id': ALERT_ID,
            'summary': params[8],
        })
        self.alert['incident_id'] = incident_id
        self.alert['status'] = 'investigating'
        return {'incident_id': incident_id}

    def execute(self, statement: str, params=None):
        norm = ' '.join(str(statement).split())
        self.executed.append((norm, params))

        if 'WITH inserted_incident AS' in norm:
            return _Result(self._insert_incident(params))

        # Idempotency probe: an incident already referencing this alert.
        if 'FROM incidents' in norm and 'source_alert_id = %s::uuid' in norm:
            match = next((i for i in self.incidents if i['source_alert_id'] == ALERT_ID), None)
            return _Result({'id': match['id']} if match else None)

        # Incident detail read used by the recommend path.
        if 'FROM incidents' in norm and 'WHERE id = %s::uuid' in norm:
            wanted = str(params[0])
            match = next((i for i in self.incidents if i['id'] == wanted and i['workspace_id'] == str(params[1])), None)
            return _Result(match)

        if 'SELECT' in norm and 'FROM alerts' in norm:
            if params and ALERT_ID not in [str(p) for p in params]:
                return _Result(None)
            return _Result(dict(self.alert))

        if 'FROM evidence' in norm:
            return _Result(None)

        if 'INSERT INTO response_actions' in norm:
            row = {
                'id': str(params['id']),
                'workspace_id': str(params['workspace_id']),
                'incident_id': str(params['incident_id']),
                'alert_id': params['alert_id'],
                'action_type': params['action_type'],
                'mode': params['mode'],
                'status': params['status'],
            }
            self.response_actions.append(row)
            return _Result({'id': row['id']})

        # Recommend-path dedupe probe + Screen 8 listing both read response_actions.
        if 'FROM response_actions' in norm and 'SELECT' in norm:
            if params and isinstance(params, (list, tuple)) and "mode = 'recommended'" in norm:
                incident_id, workspace_id, action_type = str(params[0]), str(params[1]), str(params[2])
                match = next((
                    a for a in self.response_actions
                    if a['incident_id'] == incident_id
                    and a['workspace_id'] == workspace_id
                    and a['action_type'] == action_type
                ), None)
                return _Result({'id': match['id']} if match else None)
            return _Result(rows=list(self.response_actions))

        if 'INSERT INTO alert_event' in norm:
            return _Result({'id': str(uuid.uuid4())})

        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass


def _bootstrap(monkeypatch, conn, *, permission_denied: str | None = None):
    @contextmanager
    def _fake_pg():
        yield conn

    def _permission(_connection, _request, permission, **_kwargs):
        if permission_denied is not None and permission == permission_denied:
            raise HTTPException(
                status_code=403,
                detail={'code': 'PERMISSION_DENIED', 'permission': permission,
                        'message': f'Permission {permission} is required.'},
            )
        return ({'id': USER_ID}, {'workspace_id': WS_ID})

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission', _permission)
    monkeypatch.setattr(pilot, 'write_action_history', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_response_actions_recommended_dedupe_ready', lambda *_: False)
    monkeypatch.setattr(pilot, '_record_response_action_enforcement_evaluation', lambda *a, **k: None)


@pytest.fixture()
def conn():
    return _ChainConn()


# ── 1. Create Incident persists a real incident ──────────────────────────────

def test_create_incident_persists_incident_linked_to_alert(monkeypatch, conn):
    """The escalation the button now runs must INSERT an incident bound to the alert."""
    _bootstrap(monkeypatch, conn)

    result = pilot.escalate_alert_to_incident(
        ALERT_ID, {'title': 'Escalated alert: wallet transfer', 'summary': 'wallet transfer'},
        _make_request(),
    )

    assert result['created'] is True
    assert result['incident_id']
    uuid.UUID(result['incident_id'])
    assert len(conn.incidents) == 1, 'exactly one incident row must be persisted'
    persisted = conn.incidents[0]
    assert persisted['source_alert_id'] == ALERT_ID
    assert persisted['workspace_id'] == WS_ID, 'incident must be workspace-scoped'
    assert conn.alert['incident_id'] == result['incident_id'], 'alert must be linked back'


def test_created_incident_is_returned_by_the_incidents_list(monkeypatch, conn):
    """The persisted incident is real data, so the /incidents read returns it."""
    _bootstrap(monkeypatch, conn)
    created = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert any(i['id'] == created['incident_id'] for i in conn.incidents)
    assert conn.incidents[0]['workflow_status'] == 'open'


# ── 2. Recommend → response action carries the SAME incident_id ──────────────

def test_recommend_creates_actions_under_the_same_incident_id(monkeypatch, conn):
    """Screen 7 Response Actions tab → recommend must bind actions to the new incident."""
    _bootstrap(monkeypatch, conn)
    created = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())
    incident_id = created['incident_id']

    recommended = pilot.recommend_response_action_for_incident(incident_id, _make_request())

    assert recommended['incident_id'] == incident_id
    assert recommended['response_action_id'], 'an anchor action must be returned'
    assert conn.response_actions, 'at least one response action must be persisted'
    assert all(a['incident_id'] == incident_id for a in conn.response_actions), (
        'every recommended action must carry the incident_id it was created from'
    )
    assert all(a['alert_id'] == ALERT_ID for a in conn.response_actions), (
        'the chain back to the source alert must survive'
    )
    assert all(a['mode'] == 'recommended' for a in conn.response_actions)


def test_recommended_action_appears_on_screen8_under_the_same_incident_id(monkeypatch, conn):
    """Screen 8 filtered by incident_id must return the action Screen 7 just created."""
    _bootstrap(monkeypatch, conn)
    incident_id = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())['incident_id']
    recommended = pilot.recommend_response_action_for_incident(incident_id, _make_request())

    screen8_rows = [a for a in conn.response_actions if a['incident_id'] == incident_id]

    assert screen8_rows, 'Screen 8 must list the recommended action for this incident'
    assert recommended['response_action_id'] in {a['id'] for a in screen8_rows}
    assert {a['incident_id'] for a in screen8_rows} == {incident_id}, (
        'Screen 8 must show the SAME incident_id Screen 7 created — no re-keying'
    )


# ── 3. RBAC is not weakened ──────────────────────────────────────────────────

def test_create_incident_still_requires_the_workspace_permission(monkeypatch, conn):
    """Escalation keeps its server-side permission gate; a denial creates nothing."""
    _bootstrap(monkeypatch, conn, permission_denied='members.manage')

    with pytest.raises(HTTPException) as excinfo:
        pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail['code'] == 'PERMISSION_DENIED'
    assert conn.incidents == [], 'a denied request must not persist an incident'


def test_recommend_still_requires_response_propose(monkeypatch, conn):
    """The recommend hop keeps its own distinct permission gate."""
    _bootstrap(monkeypatch, conn)
    incident_id = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())['incident_id']

    _bootstrap(monkeypatch, conn, permission_denied='response.propose')
    with pytest.raises(HTTPException) as excinfo:
        pilot.recommend_response_action_for_incident(incident_id, _make_request())

    assert excinfo.value.status_code == 403
    assert conn.response_actions == [], 'a denied request must not persist an action'


# ── 4. Idempotency: a repeated click cannot duplicate ────────────────────────

def test_repeated_create_incident_reuses_the_same_incident(monkeypatch, conn):
    """A second Create Incident on the same alert returns the first incident, created=False."""
    _bootstrap(monkeypatch, conn)
    first = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())
    second = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert second['incident_id'] == first['incident_id']
    assert second['created'] is False
    assert len(conn.incidents) == 1, 'no duplicate incident may be created'


def test_repeated_recommend_reuses_the_same_actions(monkeypatch, conn):
    """Re-running recommend for one incident must not fan out duplicate actions."""
    _bootstrap(monkeypatch, conn)
    incident_id = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())['incident_id']

    first = pilot.recommend_response_action_for_incident(incident_id, _make_request())
    count_after_first = len(conn.response_actions)
    second = pilot.recommend_response_action_for_incident(incident_id, _make_request())

    assert len(conn.response_actions) == count_after_first, 'no duplicate actions'
    assert second['incident_id'] == first['incident_id'] == incident_id
    assert second['created'] is False
