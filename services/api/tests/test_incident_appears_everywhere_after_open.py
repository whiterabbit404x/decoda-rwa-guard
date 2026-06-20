"""
Alert → Incident consistency: an alert-linked incident must appear on /incidents and the
incident detail route, not just on the Alerts page.

This reproduces and locks the reported mismatch ("Alerts says Linked Incidents = 1, but
/incidents shows nothing") at the source-of-truth level using a small stateful in-memory DB so
the *same persisted incident row* is exercised by escalate → list → detail.

Coverage (maps to the task's required assertions):
1. Seed workspace + target + alert, then call Open Incident (POST /alerts/{id}/escalate).
2. The incidents table holds exactly one row afterwards.
3. The alert is linked to that incident (alerts.incident_id).
4. list_incidents (/incidents) returns that incident.
5. get_incident (/incidents/{id}) loads that incident.
6. Calling Open Incident twice does not duplicate the incident.
7. A resolved linked incident still appears when no status filter ("All Statuses") is applied.
8. The Linked-Incidents truth (alerts with incident_id) equals the incidents the backend returns.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from services.api.app import pilot


WS_ID = str(uuid.uuid4())
OTHER_WS_ID = str(uuid.uuid4())
TARGET_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
ALERT_ID = str(uuid.uuid4())


# ── Stateful in-memory DB ────────────────────────────────────────────────────


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _StatefulDB:
    """Holds alerts + incidents in dicts and answers the exact SQL escalate/list/detail run."""

    def __init__(self):
        self.alerts: dict[str, dict[str, Any]] = {}
        self.incidents: dict[str, dict[str, Any]] = {}
        self._clock = datetime(2026, 6, 20, 10, 0, 0, tzinfo=timezone.utc)

    def _now(self) -> datetime:
        self._clock += timedelta(seconds=1)
        return self._clock

    def seed_alert(self, *, alert_id=ALERT_ID, workspace_id=WS_ID, status='open', incident_id=None):
        self.alerts[alert_id] = {
            'id': alert_id,
            'workspace_id': workspace_id,
            'status': status,
            'incident_id': incident_id,
            'target_id': TARGET_ID,
            'analysis_run_id': None,
            'title': 'Monitored wallet transfer detected',
            'severity': 'critical',
            'summary': 'Large outbound transfer from monitored wallet.',
            'detection_id': str(uuid.uuid4()),
            'alert_type': 'threat_monitoring',
            'findings': {'chain_id': 8453},
        }

    def seed_incident(self, *, incident_id, workspace_id=WS_ID, source_alert_id=ALERT_ID,
                      status='open', workflow_status='open', severity='critical'):
        self.incidents[incident_id] = {
            'id': incident_id,
            'workspace_id': workspace_id,
            'event_type': 'alert_escalation',
            'title': 'Escalated alert',
            'severity': severity,
            'status': status,
            'workflow_status': workflow_status,
            'target_id': TARGET_ID,
            'source_alert_id': source_alert_id,
            'linked_alert_ids': [source_alert_id] if source_alert_id else [],
            'owner': USER_ID,
            'owner_user_id': USER_ID,
            'assignee_user_id': None,
            'summary': 'Escalated from alert',
            'resolution_note': None,
            'resolution_notes': None,
            'timeline': [],
            'created_at': self._now().isoformat(),
            'updated_at': self._now().isoformat(),
        }

    # -- query engine ---------------------------------------------------------

    def execute(self, statement: str, params=None):
        norm = ' '.join(str(statement).split())
        params = params or ()

        # Incident INSERT (+ alert link) in one CTE.
        if 'WITH inserted_incident AS' in norm:
            incident_id = params[0]
            ws = params[1]
            target_id = params[4]
            title = params[5]
            severity = params[6]
            source_alert_id = params[7]
            summary = params[9]
            where_alert_id = params[13]
            now = self._now().isoformat()
            self.incidents[incident_id] = {
                'id': incident_id,
                'workspace_id': ws,
                'event_type': 'alert_escalation',
                'title': title,
                'severity': severity,
                'status': 'open',
                'workflow_status': 'open',
                'target_id': target_id,
                'source_alert_id': source_alert_id,
                'linked_alert_ids': [source_alert_id],
                'owner': USER_ID,
                'owner_user_id': USER_ID,
                'assignee_user_id': None,
                'summary': summary,
                'resolution_note': None,
                'resolution_notes': None,
                'timeline': [],
                'created_at': now,
                'updated_at': now,
            }
            alert = self.alerts.get(where_alert_id)
            if alert is not None and alert['workspace_id'] == ws:
                alert['incident_id'] = incident_id
            return _Result({'incident_id': incident_id})

        # Existing-incident-by-alert idempotency probe.
        if 'FROM incidents' in norm and 'source_alert_id = %s::uuid OR linked_alert_ids' in norm:
            ws, alert_id, _alert_id2 = params[0], params[1], params[2]
            matches = [
                inc for inc in self.incidents.values()
                if inc['workspace_id'] == ws
                and (inc.get('source_alert_id') == alert_id or alert_id in (inc.get('linked_alert_ids') or []))
            ]
            matches.sort(key=lambda inc: inc['created_at'])
            return _Result(matches[0] if matches else None)

        # list_incidents / get_incident SELECT (aliased "incidents i").
        if 'FROM incidents i' in norm:
            ws = params[0]
            severity = params[1]
            target_id = params[3]
            status_value = params[5]
            assignee = params[8]
            incident_id = params[10]
            limit = params[12]
            offset = params[13]
            rows = []
            for inc in self.incidents.values():
                if inc['workspace_id'] != ws:
                    continue
                if severity is not None and inc.get('severity') != severity:
                    continue
                if target_id is not None and inc.get('target_id') != target_id:
                    continue
                if status_value is not None and not (
                    inc.get('workflow_status') == status_value or inc.get('status') == status_value
                ):
                    continue
                if assignee is not None and inc.get('assignee_user_id') != assignee:
                    continue
                if incident_id is not None and inc.get('id') != incident_id:
                    continue
                rows.append(self._incident_select_row(inc))
            rows.sort(key=lambda r: r['created_at'], reverse=True)
            return _Result(rows=rows[offset:offset + limit])

        # Alert SELECT in escalate (suppressed rows are filtered out).
        if 'FROM alerts' in norm and "status != 'suppressed'" in norm:
            alert_id, ws = params[0], params[1]
            alert = self.alerts.get(alert_id)
            if alert is None or alert['workspace_id'] != ws or alert['status'] == 'suppressed':
                return _Result(None)
            return _Result(dict(alert))

        # Evidence lookup → none.
        if 'FROM evidence' in norm:
            return _Result(None)

        # Idempotency-2 relink: UPDATE alerts SET incident_id ... WHERE ... incident_id IS NULL.
        if norm.startswith('UPDATE alerts SET incident_id'):
            incident_id, alert_id, ws = params[0], params[1], params[2]
            alert = self.alerts.get(alert_id)
            if alert is not None and alert['workspace_id'] == ws and alert.get('incident_id') is None:
                alert['incident_id'] = incident_id
            return _Result()

        return _Result()

    def _incident_select_row(self, inc: dict[str, Any]) -> dict[str, Any]:
        return {
            'id': inc['id'],
            'event_type': inc.get('event_type'),
            'title': inc.get('title'),
            'severity': inc.get('severity'),
            'status': inc.get('status'),
            'workflow_status': inc.get('workflow_status'),
            'target_id': inc.get('target_id'),
            'source_alert_id': inc.get('source_alert_id'),
            'linked_alert_ids': inc.get('linked_alert_ids') or [],
            'owner': inc.get('owner'),
            'owner_user_id': inc.get('owner_user_id'),
            'assignee_user_id': inc.get('assignee_user_id'),
            'summary': inc.get('summary'),
            'resolution_note': inc.get('resolution_note'),
            'resolution_notes': inc.get('resolution_notes'),
            'timeline': inc.get('timeline') or [],
            'created_at': inc.get('created_at'),
            'updated_at': inc.get('updated_at'),
            'linked_detection_id': None,
            'linked_evidence_count': 0,
            'last_evidence_at': None,
            'evidence_source': None,
            'tx_hash': None,
            'block_number': None,
            'detector_kind': None,
            'evidence_origin': None,
            'linked_action_id': None,
            'response_action_mode': None,
        }

    def commit(self):
        pass


def _make_request(workspace_id: str = WS_ID):
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


@pytest.fixture
def db(monkeypatch):
    state = _StatefulDB()

    @contextmanager
    def _fake_pg():
        yield state

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin',
                        lambda *_: ({'id': USER_ID}, {'workspace_id': WS_ID, 'workspace': {'id': WS_ID}}))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda _conn, _user, requested=None: {'workspace_id': requested or WS_ID, 'workspace': {'id': requested or WS_ID}})
    # Isolate the side-effect writers — they only emit audit/timeline/outbox rows.
    monkeypatch.setattr(pilot, 'write_action_history', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'enqueue_alert_event', lambda *a, **k: None)
    return state


# ── Tests ────────────────────────────────────────────────────────────────────


def test_open_incident_creates_single_row_and_links_alert(db):
    """Steps 1–4: escalate creates exactly one incident, links the alert, list returns it."""
    db.seed_alert(status='open')

    result = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert result['created'] is True
    incident_id = result['incident_id']
    assert incident_id and result['alert_id'] == ALERT_ID

    # Exactly one incident row exists.
    assert len(db.incidents) == 1, f'expected exactly one incident; got {len(db.incidents)}'

    # Alert is linked to that incident.
    assert db.alerts[ALERT_ID]['incident_id'] == incident_id

    # /incidents returns that incident.
    listed = pilot.list_incidents(_make_request())['incidents']
    assert [i['id'] for i in listed] == [incident_id]
    assert listed[0]['source_alert_id'] == ALERT_ID


def test_incident_detail_route_loads(db):
    """Step 5: get_incident (/incidents/{id}) loads the same persisted incident."""
    db.seed_alert(status='open')
    incident_id = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())['incident_id']

    detail = pilot.get_incident(incident_id, _make_request())

    assert detail['id'] == incident_id
    assert detail['incident']['id'] == incident_id
    assert detail['source_alert_id'] == ALERT_ID


def test_incident_detail_unknown_id_is_404(db):
    """Detail route must fail-closed (404), never invent a placeholder incident."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        pilot.get_incident(str(uuid.uuid4()), _make_request())
    assert exc.value.status_code == 404


def test_open_incident_twice_does_not_duplicate(db):
    """Step 6: a second Open Incident reuses the same row (idempotent)."""
    db.seed_alert(status='open')

    first = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())
    second = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert first['incident_id'] == second['incident_id']
    assert first['created'] is True
    assert second['created'] is False
    assert len(db.incidents) == 1, 'second escalation must not create a duplicate incident'

    listed = pilot.list_incidents(_make_request())['incidents']
    assert len(listed) == 1


def test_orphan_link_self_heals_without_duplicate(db):
    """If an incident already references the alert but alerts.incident_id was never set, the
    escalate path re-links the alert to that existing incident instead of creating a new one."""
    existing_incident_id = str(uuid.uuid4())
    db.seed_alert(status='open', incident_id=None)
    db.seed_incident(incident_id=existing_incident_id, source_alert_id=ALERT_ID)

    result = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    assert result['incident_id'] == existing_incident_id
    assert result['created'] is False
    assert len(db.incidents) == 1
    assert db.alerts[ALERT_ID]['incident_id'] == existing_incident_id


def test_resolved_linked_incident_still_listed_under_all_statuses(db):
    """Step 7: a resolved incident must still appear when no status filter is applied, even
    though it no longer counts toward Open Incidents."""
    db.seed_alert(status='resolved')
    incident_id = pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())['incident_id']
    # Move the incident to resolved (as a workflow transition would).
    db.incidents[incident_id]['status'] = 'resolved'
    db.incidents[incident_id]['workflow_status'] = 'resolved'

    # "All Statuses" → no status_value filter.
    all_statuses = pilot.list_incidents(_make_request())['incidents']
    assert [i['id'] for i in all_statuses] == [incident_id]

    # Filtering explicitly to open excludes it (so Open Incidents can read 0)...
    open_only = pilot.list_incidents(_make_request(), status_value='open')['incidents']
    assert open_only == []
    # ...but the resolved filter still returns it.
    resolved_only = pilot.list_incidents(_make_request(), status_value='resolved')['incidents']
    assert [i['id'] for i in resolved_only] == [incident_id]


def test_linked_incident_count_matches_backend_incidents(db):
    """Step 8: the Alerts "Linked Incidents" truth (alerts carrying incident_id) equals the
    incidents the backend actually returns — never a frontend-only count."""
    db.seed_alert(status='open')
    pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    linked_alert_count = sum(1 for a in db.alerts.values()
                             if a['workspace_id'] == WS_ID and a.get('incident_id'))
    backend_incident_ids = {i['id'] for i in pilot.list_incidents(_make_request())['incidents']}

    assert linked_alert_count == len(backend_incident_ids) == 1
    # Every alert-linked incident_id resolves to a real listed incident row.
    for alert in db.alerts.values():
        if alert.get('incident_id'):
            assert alert['incident_id'] in backend_incident_ids


def test_incident_is_workspace_scoped(db):
    """A different workspace must not see the incident (no cross-tenant leakage)."""
    db.seed_alert(status='open')
    pilot.escalate_alert_to_incident(ALERT_ID, {}, _make_request())

    other = pilot.list_incidents(_make_request(OTHER_WS_ID))['incidents']
    assert other == []
