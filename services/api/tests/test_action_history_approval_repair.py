"""Idempotent repair of pre-enrichment (sparse) response-action APPROVAL history events
(Screen 8, audit-completeness pass).

The reported bug: the deployed "Response Action Approved" event was persisted by the
PRE-enrichment writer, so its ``details_json`` carried only
``{subject_domain, action_version, approved_count, required_quorum}``. The event
therefore rendered with a dash for Action ID, Incident, Previous/New state, Decision,
Approval progress, and Approval policy. These tests prove the self-healing repair enriches
that existing row IN PLACE from canonical related records (the approval decision, the
response action / recommendation, the incident, and the policy) — idempotently, workspace
-scoped, and WITHOUT ever creating a duplicate approval event. Repo fake-connection unit
style (no real DB / model / network).
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from services.api.app import pilot


WS = 'ws-1'
INCIDENT = 'c537b73f-1976-4a44-b589-946194794399'
REC_ID = 'a1111111-1111-4111-8111-111111111111'
DECISION_ID = 'd2222222-2222-4222-8222-222222222222'


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows or []


class _RepairConn:
    """Fake connection backing the ai_recommendations / response_actions subject read, the
    response_action_approvals decision read, and the action_history UPDATE."""

    def __init__(self, *, recs=None, actions=None, approvals=None):
        self.executed: list[tuple[str, object]] = []
        self._recs = {(str(r['id']), str(r['workspace_id'])): r for r in (recs or [])}
        self._actions = {(str(a['id']), str(a['workspace_id'])): a for a in (actions or [])}
        self._approvals = list(approvals or [])

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'FROM ai_recommendations WHERE id = %s::uuid AND workspace_id = %s' in n:
            return _Result(row=self._recs.get((str(params[0]), str(params[1]))))
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id = %s' in n:
            return _Result(row=self._actions.get((str(params[0]), str(params[1]))))
        if 'FROM response_action_approvals' in n and n.startswith('SELECT'):
            ws, domain, subject_id, version = params
            rows = [
                a for a in self._approvals
                if str(a['workspace_id']) == str(ws) and str(a['subject_domain']) == str(domain)
                and str(a['subject_id']) == str(subject_id) and int(a['action_version']) == int(version)
            ]
            return _Result(rows=rows)
        return _Result()

    def commit(self):
        return None


def _sparse_legacy_row(**over):
    """The exact shape the pre-enrichment writer persisted for a quorum-reached approval."""
    row = {
        'id': 'evt-legacy-approve-1',
        'actor_type': 'user',
        'actor_id': '11111111-1111-4111-8111-111111111111',
        'object_type': 'response_action',
        'object_id': REC_ID,
        'action_type': 'response_action.approved',
        'timestamp': '2026-08-05T10:15:00+00:00',
        'details_json': {
            'subject_domain': 'ai_recommendation',
            'action_version': 1,
            'approved_count': 1,
            'required_quorum': 1,
        },
    }
    row.update(over)
    return row


def _rec(**over):
    row = {'id': REC_ID, 'workspace_id': WS, 'incident_id': INCIDENT,
           'action_type': 'notify_team', 'runbook_id': 'notify_team_v1'}
    row.update(over)
    return row


def _approval(**over):
    row = {'id': DECISION_ID, 'workspace_id': WS, 'subject_domain': 'ai_recommendation',
           'subject_id': REC_ID, 'action_version': 1, 'decision': 'approved',
           'policy': 'owner_admin_single_approver', 'required_quorum': 1,
           'approver_user_id': 'approver-1'}
    row.update(over)
    return row


def _repair(conn, rows, workspace_id=WS):
    return pilot._repair_incomplete_approval_history_events(conn, workspace_id, rows)


# 1-8. The sparse legacy approval event is enriched from canonical records.
def test_repair_enriches_sparse_approval_from_canonical_records():
    conn = _RepairConn(recs=[_rec()], approvals=[_approval()])
    rows, repaired = _repair(conn, [_sparse_legacy_row()])
    assert repaired == 1
    d = rows[0]['details_json']
    assert d['event_type'] == 'response_action_approved'          # canonical event type
    assert d['action_id'] == REC_ID                                # (1) action id recovered
    assert d['incident_id'] == INCIDENT                            # (2) incident recovered
    assert d['previous_lifecycle'] == 'Awaiting Approval'          # (3) previous captured
    assert d['new_lifecycle'] == 'Approved'                        # (4) new captured
    assert d['decision'] == 'approved'                             # (5) decision Approved
    assert d['approval_progress_label'] == '1 of 1'               # (6) real 1 of 1 progress
    assert d['approval_policy_id'] == 'owner_admin_single_approver'  # (8) policy resolved
    assert d['approval_policy_label'] == 'Owner/Admin single approver'
    assert d['approval_decision_id'] == DECISION_ID               # linked to the decision
    assert d['action_key'] == 'notify_team'
    # The stored title is a clean human title (never a raw enum); the canonical DISPLAY
    # title is resolved from the action_key by the DTO (response_action_presentation).
    assert '_' not in str(d['action_title'])
    dto = pilot.build_action_history_event_dto(rows[0])
    assert dto['action_title'] == 'Notify Security Team'          # (5) readable action title
    assert dto['incident_short_id'] == 'INC-c537b73f'
    assert dto['decision_label'] == 'Approved'                    # table Decision shows Approved
    assert dto['previous_state_label'] == 'Awaiting Approval'
    assert dto['new_state_label'] == 'Approved'


# The repair enriches via an UPDATE only — it NEVER inserts a new approval event.
def test_repair_updates_in_place_and_creates_no_duplicate():
    conn = _RepairConn(recs=[_rec()], approvals=[_approval()])
    _repair(conn, [_sparse_legacy_row()])
    updates = [sql for sql, _ in conn.executed if sql.startswith('UPDATE action_history')]
    inserts = [sql for sql, _ in conn.executed if sql.startswith('INSERT INTO action_history')]
    assert len(updates) == 1
    assert inserts == []
    # The UPDATE targets the SAME row id and is workspace-scoped.
    update_sql, update_params = next((s, p) for s, p in conn.executed if s.startswith('UPDATE action_history'))
    assert 'WHERE id = %s AND workspace_id = %s' in update_sql
    assert update_params[1] == 'evt-legacy-approve-1'
    assert update_params[2] == WS


# 9-10. Running the repair again on the now-enriched row is a no-op (idempotent).
def test_repair_is_idempotent():
    conn = _RepairConn(recs=[_rec()], approvals=[_approval()])
    rows, first = _repair(conn, [_sparse_legacy_row()])
    conn.executed.clear()
    rows_again, second = _repair(conn, rows)
    assert first == 1
    assert second == 0
    assert not any(sql.startswith('UPDATE action_history') for sql, _ in conn.executed)
    # An already-enriched forward-path row is likewise never re-written.
    conn.executed.clear()
    enriched_row = _sparse_legacy_row(id='evt-fwd', details_json=pilot._response_action_approval_event_details(
        event_type='response_action_approved', display_label='Response Action Approved',
        action_id=REC_ID, action_title='Notify Security Team', incident_id=INCIDENT,
        actor='decoda.guard@gmail.com', decision='approved', previous_lifecycle='Awaiting Approval',
        new_lifecycle='Approved', approved_count=1, required_quorum=1,
        policy='owner_admin_single_approver', result_summary='Success',
        subject_domain='ai_recommendation', action_version=1,
    ))
    _rows, repaired = _repair(conn, [enriched_row])
    assert repaired == 0
    assert not any(sql.startswith('UPDATE action_history') for sql, _ in conn.executed)


# A multi-approver quorum keeps the REAL progress, never a hardcoded 1 of 1.
def test_repair_preserves_multi_approver_progress():
    conn = _RepairConn(
        recs=[_rec()],
        approvals=[
            _approval(id='dec-a', approver_user_id='approver-1', required_quorum=2),
            _approval(id='dec-b', approver_user_id='approver-2', required_quorum=2),
        ],
    )
    row = _sparse_legacy_row(details_json={'subject_domain': 'ai_recommendation', 'action_version': 1,
                                           'approved_count': 2, 'required_quorum': 2})
    rows, repaired = _repair(conn, [row])
    assert repaired == 1
    assert rows[0]['details_json']['approval_progress_label'] == '2 of 2'


# Workspace isolation: the subject + decision reads are workspace-scoped, so a row whose
# subject lives in ANOTHER workspace recovers NO incident/policy (never cross-tenant).
def test_repair_enforces_workspace_isolation():
    conn = _RepairConn(recs=[_rec(workspace_id='ws-OTHER')], approvals=[_approval(workspace_id='ws-OTHER')])
    rows, repaired = _repair(conn, [_sparse_legacy_row()], workspace_id=WS)
    d = rows[0]['details_json']
    # The canonical (provable) transition is still recorded, but no cross-tenant incident
    # or policy is ever fabricated from another workspace's records.
    assert d['previous_lifecycle'] == 'Awaiting Approval'
    assert d['new_lifecycle'] == 'Approved'
    assert d.get('incident_id') in (None, '')
    assert d.get('approval_policy_id') in (None, '')


# When the subject record is GONE, the previous state is still the domain invariant, but an
# unprovable incident/policy is left absent (a truthful fallback, never invented).
def test_repair_does_not_fabricate_when_related_records_missing():
    conn = _RepairConn(recs=[], approvals=[])
    rows, repaired = _repair(conn, [_sparse_legacy_row()])
    d = rows[0]['details_json']
    assert d['previous_lifecycle'] == 'Awaiting Approval'
    assert d['decision'] == 'approved'
    assert d['approval_progress_label'] == '1 of 1'   # from the persisted counts
    assert d.get('incident_id') in (None, '')
    assert d.get('approval_policy_id') in (None, '')


# A rejected legacy event repairs to the rejected transition + decision (not approved).
def test_repair_handles_rejected_event():
    conn = _RepairConn(recs=[_rec()], approvals=[_approval(id='dec-r', decision='rejected')])
    row = _sparse_legacy_row(id='evt-legacy-reject', action_type='response_action.rejected',
                             details_json={'subject_domain': 'ai_recommendation', 'action_version': 1})
    rows, repaired = _repair(conn, [row])
    assert repaired == 1
    d = rows[0]['details_json']
    assert d['event_type'] == 'response_action_rejected'
    assert d['decision'] == 'rejected'
    assert d['new_lifecycle'] == 'Rejected'
    assert d['approval_decision_id'] == 'dec-r'


# A non-approval history row is never touched by the approval repair.
def test_repair_ignores_non_approval_rows():
    conn = _RepairConn()
    created = _sparse_legacy_row(id='evt-created', action_type='response_action.created', details_json={})
    rows, repaired = _repair(conn, [created])
    assert repaired == 0
    assert rows[0]['details_json'] == {}


# ── list_action_history: self-healing read enriches + persists ────────────────────

class _ListConn(_RepairConn):
    """Extends the repair fake with the action_history SELECT, actor identity, and the
    batched action_type resolution so the full read path can be exercised."""

    def __init__(self, *, history_rows, users=None, **over):
        super().__init__(**over)
        self._history_rows = history_rows
        self._users = users or []
        self.committed = 0

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'FROM action_history' in n and n.startswith('SELECT'):
            self.executed.append((n, params))
            return _Result(rows=[dict(r) for r in self._history_rows])
        if 'FROM users WHERE id = ANY' in n:
            self.executed.append((n, params))
            return _Result(rows=[dict(u) for u in self._users])
        if 'FROM response_actions WHERE workspace_id' in n:
            self.executed.append((n, params))
            return _Result(rows=[])
        if 'FROM ai_recommendations WHERE workspace_id' in n:
            self.executed.append((n, params))
            return _Result(rows=[{'id': REC_ID, 'action_type': 'notify_team'}])
        return super().execute(statement, params)

    def commit(self):
        self.committed += 1
        return None


def _patch_list(monkeypatch, conn):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS})


def _req():
    return SimpleNamespace(headers={'x-workspace-id': WS}, client=SimpleNamespace(host='127.0.0.1'), method='GET')


# 9 (list) — a sparse legacy approval row is repaired + persisted during the read, so the
# returned DTO is complete and the enrichment is committed (survives a refresh).
def test_list_action_history_repairs_and_persists(monkeypatch):
    conn = _ListConn(history_rows=[_sparse_legacy_row()], recs=[_rec()], approvals=[_approval()],
                     users=[{'id': '11111111-1111-4111-8111-111111111111',
                             'email': 'decoda.guard@gmail.com', 'full_name': 'Decoda Guard'}])
    _patch_list(monkeypatch, conn)
    events = pilot.list_action_history(_req())['events']
    assert len(events) == 1
    e = events[0]
    assert e['event_type'] == 'response_action_approved'
    assert e['action_id'] == REC_ID
    assert e['incident_id'] == INCIDENT
    assert e['incident_short_id'] == 'INC-c537b73f'
    assert e['previous_state_label'] == 'Awaiting Approval'
    assert e['new_state_label'] == 'Approved'
    assert e['decision_label'] == 'Approved'
    assert e['approval_progress_label'] == '1 of 1'
    assert e['approval_policy_label'] == 'Owner/Admin single approver'
    # The enrichment was PERSISTED (an UPDATE + a commit), so a refresh keeps it.
    assert any(sql.startswith('UPDATE action_history') for sql, _ in conn.executed)
    assert conn.committed >= 1


# 11 (list) — a decision DTO for an approval event NEVER reads "Not applicable".
def test_list_action_history_decision_never_not_applicable(monkeypatch):
    conn = _ListConn(history_rows=[_sparse_legacy_row()], recs=[_rec()], approvals=[_approval()])
    _patch_list(monkeypatch, conn)
    e = pilot.list_action_history(_req())['events'][0]
    assert e['decision'] == 'approved'
    assert e['decision_label'] == 'Approved'
    assert e['decision_label'] != 'Not applicable'
