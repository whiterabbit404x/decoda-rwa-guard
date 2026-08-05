"""Action History AUDIT event for a response-action APPROVAL (Screen 8).

The reported bug: after a successful approval ("Approval recorded. Approval quorum
reached.") the Action History tab showed only the OLDER AI recommendation-REVIEW row
(Decision: Accepted, dated at the review time) — there was no clearly-identifiable
"Response Action Approved" event with the real approval time and progress.

These tests prove the persistence side of the fix: a successful approval writes exactly
ONE self-describing ``response_action.approved`` action-history event (and the matching
incident-timeline event) carrying the canonical event type, human labels, decision,
previous -> new lifecycle transition, and quorum progress — WITHOUT reusing or
overwriting the independent recommendation-review record. A blocked attempt is a
distinct event, and the event timestamp is always the DB's NOW() (the real approval
time). Repo fake-connection unit style (no real DB / model / network).
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


INCIDENT = 'c537b73f-1976-4a44-b589-946194794399'
REC_ID = 'a1111111-1111-4111-8111-111111111111'


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _ApprovalConn:
    """Fake connection backing the ai_recommendations read + the SEPARATE
    response_action_approvals store, so quorum progress is really computed."""

    def __init__(self, *, ai_rows=None, approvals=None, schema_ready=True):
        self.executed: list[tuple[str, object]] = []
        self._ai_rows = {(str(r['id']), str(r['workspace_id'])): r for r in (ai_rows or [])}
        self.approvals: list[dict] = list(approvals or [])
        self._schema_ready = schema_ready

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'to_regclass' in n:
            return _Result(row={'present': self._schema_ready})
        if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in n and n.startswith('SELECT'):
            return _Result(row=None)  # not a policy action -> AI-recommendation-backed path
        if 'FROM ai_recommendations' in n and n.startswith('SELECT'):
            return _Result(row=self._ai_rows.get((str(params[0]), str(params[1]))))
        if 'SELECT decision FROM response_action_approvals' in n:
            ws, domain, subject_id, version, approver = params
            match = next(
                (a for a in self.approvals
                 if str(a['workspace_id']) == str(ws) and str(a['subject_domain']) == str(domain)
                 and str(a['subject_id']) == str(subject_id) and int(a['action_version']) == int(version)
                 and str(a['approver_user_id']) == str(approver)),
                None,
            )
            return _Result(row={'decision': match['decision']} if match else None)
        if 'SELECT approver_user_id, decision, required_quorum' in n:
            ws, domain, subject_id, version = params
            rows = [
                {'approver_user_id': a['approver_user_id'], 'decision': a['decision'],
                 'required_quorum': a['required_quorum']}
                for a in self.approvals
                if str(a['workspace_id']) == str(ws) and str(a['subject_domain']) == str(domain)
                and str(a['subject_id']) == str(subject_id) and int(a['action_version']) == int(version)
            ]
            return _Result(rows=rows)
        if n.startswith('INSERT INTO response_action_approvals'):
            self.approvals.append({
                'workspace_id': params[1], 'subject_domain': params[2], 'subject_id': params[3],
                'action_version': params[4], 'approver_user_id': params[5], 'approver_role': params[6],
                'decision': params[7], 'note': params[8], 'required_quorum': params[9], 'policy': params[10],
            })
            return _Result()
        return _Result()

    def commit(self):
        return None


def _ai_rec(**over):
    row = {
        'id': REC_ID,
        'workspace_id': 'ws-1',
        'incident_id': INCIDENT,
        'action_type': 'increase_monitoring',
        'runbook_id': 'increase_monitoring_v1',
        # A recommendation REVIEW already exists and is ACCEPTED (the older event). It
        # must NOT be reused as, or overwritten by, the response-action approval event.
        'review_state': 'accepted',
        'requires_human_approval': True,
    }
    row.update(over)
    return row


def _patch(monkeypatch, conn, *, user_id='approver-1', role='admin', mfa=True):
    """Wire the fake connection and capture the FULL action-history / timeline calls."""
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': user_id}, {'workspace_id': 'ws-1', 'role': role}))
    monkeypatch.setattr(pilot, '_session_mfa_satisfied', lambda *_a, **_k: mfa)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    captured: dict[str, list[dict]] = {'history': [], 'timeline': []}
    monkeypatch.setattr(pilot, 'write_action_history',
                        lambda connection, **k: captured['history'].append(dict(k)))
    monkeypatch.setattr(pilot, 'append_incident_timeline_event',
                        lambda connection, **k: captured['timeline'].append(dict(k)))
    return captured


def _req():
    return SimpleNamespace(headers={'x-workspace-id': 'ws-1'}, client=SimpleNamespace(host='127.0.0.1'), method='POST')


def _events(captured, action_type):
    return [e for e in captured['history'] if e.get('action_type') == action_type]


# 1. Exactly ONE response-action approval history event is written on success — and it
#    is NOT a recommendation-review event.
def test_success_writes_exactly_one_approval_history_event(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    captured = _patch(monkeypatch, conn)
    result = pilot.approve_enforcement_action(REC_ID, _req())
    assert 'quorum reached' in result['message'].lower()

    approved = _events(captured, 'response_action.approved')
    assert len(approved) == 1
    # No recommendation-review event is emitted by the approval command.
    assert not any('recommendation' in str(e.get('action_type') or '') for e in captured['history'])
    details = approved[0]['details']
    assert details['event_type'] == 'response_action_approved'
    assert details['display_label'] == 'Response Action Approved'
    assert details['type_label'] == 'Action Approval'
    assert details['decision'] == 'approved'
    assert details['result_summary'] == 'Success'


# 2. The recommendation-review record is left untouched (review_state is never updated),
#    so the approval NEVER overwrites/reuses the older AI recommendation-review event.
def test_approval_does_not_touch_recommendation_review(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(review_state='accepted')])
    _patch(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    assert not any('UPDATE ai_recommendations' in stmt for stmt, _ in conn.executed)


# 3. The event uses the REAL current timestamp: write_action_history stamps NOW() in
#    SQL and never accepts a caller-supplied time.
def test_history_event_uses_db_now_timestamp():
    captured: dict = {}

    class _Conn:
        def execute(self, statement, params=None):
            captured['sql'] = ' '.join(str(statement).split())
            captured['params'] = params

    pilot.write_action_history(
        _Conn(), workspace_id='ws-1', actor_type='user', actor_id='approver-1',
        object_type='response_action', object_id=REC_ID,
        action_type='response_action.approved', details={'event_type': 'response_action_approved'},
    )
    assert 'NOW()' in captured['sql']
    # id, workspace, actor_type, actor_id, object_type, object_id, action_type, details_json
    # => 8 bound params; the timestamp is NOT one of them (it is the literal NOW()).
    assert len(captured['params']) == 8


# 4. Previous -> new lifecycle transition is persisted on the event.
def test_previous_and_new_lifecycle_are_stored(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    captured = _patch(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    details = _events(captured, 'response_action.approved')[0]['details']
    assert details['previous_lifecycle'] == 'Awaiting Approval'
    assert details['new_lifecycle'] == 'Approved'


# 5. Approval progress (quorum) is persisted on the event.
def test_approval_progress_is_stored(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    captured = _patch(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    details = _events(captured, 'response_action.approved')[0]['details']
    assert details['approved_count'] == 1
    assert details['required_quorum'] == 1
    assert details['approval_progress_label'] == '1 of 1'


# 6. Identity/provenance fields (action id, incident id, actor, title, policy) are stored,
#    so the row is a clearly-identifiable audit record.
def test_event_carries_identity_and_actor(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    captured = _patch(monkeypatch, conn, user_id='approver-1')
    pilot.approve_enforcement_action(REC_ID, _req())
    details = _events(captured, 'response_action.approved')[0]['details']
    assert details['action_id'] == REC_ID
    assert details['incident_id'] == INCIDENT
    assert details['actor'] == 'approver-1'
    assert details['action_title']  # non-empty human title
    assert details['policy'] == 'owner_admin_single_approver'


# 7. The incident timeline receives the corresponding approval event.
def test_incident_timeline_gets_the_approval_event(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    captured = _patch(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    timeline = [e for e in captured['timeline'] if e.get('event_type') == 'response_action.approved']
    assert len(timeline) == 1
    assert timeline[0]['metadata']['event_type'] == 'response_action_approved'
    assert timeline[0]['metadata']['response_action_id'] == REC_ID


# 8. A partial multi-approver quorum records an "approval recorded" event, NOT an
#    "approved" one — the two are distinct so a pending approval is never shown as done.
def test_partial_quorum_records_distinct_event(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    monkeypatch.setattr(pilot, '_required_approval_quorum', lambda *_a, **_k: 2)
    captured = _patch(monkeypatch, conn)
    result = pilot.approve_enforcement_action(REC_ID, _req())
    assert 'still pending' in result['message'].lower()
    assert _events(captured, 'response_action.approved') == []
    recorded = _events(captured, 'response_action.approval_recorded')
    assert len(recorded) == 1
    d = recorded[0]['details']
    assert d['event_type'] == 'response_action_approval_recorded'
    assert d['new_lifecycle'] == 'Awaiting Approval'
    assert d['approval_progress_label'] == '1 of 2'


# 9. A blocked (MFA-required) attempt is a SEPARATE, clearly-labelled event and produces
#    NO successful-approval event.
def test_blocked_attempt_is_a_distinct_event(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    captured = _patch(monkeypatch, conn, mfa=False)  # session MFA not satisfied -> block
    with pytest.raises(HTTPException) as exc:
        pilot.approve_enforcement_action(REC_ID, _req())
    assert exc.value.status_code == 403
    blocked = _events(captured, 'response_action.approval_attempt_blocked')
    assert len(blocked) == 1
    d = blocked[0]['details']
    assert d['event_type'] == 'response_action_approval_blocked'
    assert d['display_label'] == 'Approval Attempt Blocked'
    assert d['result_summary'] == 'MFA Required'
    # Legacy keys preserved for existing consumers.
    assert d['result'] == 'mfa_required'
    # No successful approval event was emitted on a blocked attempt.
    assert _events(captured, 'response_action.approved') == []


# 10. A rejection is recorded as its own event in the approval domain (distinct decision).
def test_rejection_writes_distinct_event(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(review_state='accepted')])
    captured = _patch(monkeypatch, conn)
    pilot.reject_enforcement_action(REC_ID, {'reason': 'Not warranted.'}, _req())
    rejected = _events(captured, 'response_action.rejected')
    assert len(rejected) == 1
    d = rejected[0]['details']
    assert d['event_type'] == 'response_action_rejected'
    assert d['decision'] == 'rejected'
    assert d['new_lifecycle'] == 'Rejected'
    assert d['note'] == 'Not warranted.'


# 11. Workspace isolation: an approval targeting another workspace's action is not found,
#     and NO history event is written.
def test_workspace_isolation_writes_no_event(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(workspace_id='ws-OTHER')])
    captured = _patch(monkeypatch, conn)  # caller resolves to ws-1
    with pytest.raises(HTTPException) as exc:
        pilot.approve_enforcement_action(REC_ID, _req())
    assert exc.value.status_code == 404
    assert captured['history'] == []


ACTION_ID = 'b2222222-2222-4222-8222-222222222222'


class _PolicyConn:
    """Fake connection backing a REAL response_actions row + the approval store, to
    exercise the policy-action (non-AI) approve/reject branch end to end."""

    def __init__(self, *, response_row, approvals=None, schema_ready=True):
        self.executed: list[tuple[str, object]] = []
        self._response_row = response_row
        self.approvals: list[dict] = list(approvals or [])
        self._schema_ready = schema_ready

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'to_regclass' in n:
            return _Result(row={'present': self._schema_ready})
        if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in n and n.startswith('SELECT'):
            if str(params[0]) == str(self._response_row['id']) and str(params[1]) == str(self._response_row['workspace_id']):
                return _Result(row=dict(self._response_row))
            return _Result(row=None)
        if 'SELECT decision FROM response_action_approvals' in n:
            ws, domain, subject_id, version, approver = params
            match = next(
                (a for a in self.approvals
                 if str(a['workspace_id']) == str(ws) and str(a['subject_domain']) == str(domain)
                 and str(a['subject_id']) == str(subject_id) and int(a['action_version']) == int(version)
                 and str(a['approver_user_id']) == str(approver)),
                None,
            )
            return _Result(row={'decision': match['decision']} if match else None)
        if 'SELECT approver_user_id, decision, required_quorum' in n:
            ws, domain, subject_id, version = params
            rows = [
                {'approver_user_id': a['approver_user_id'], 'decision': a['decision'],
                 'required_quorum': a['required_quorum']}
                for a in self.approvals
                if str(a['workspace_id']) == str(ws) and str(a['subject_domain']) == str(domain)
                and str(a['subject_id']) == str(subject_id) and int(a['action_version']) == int(version)
            ]
            return _Result(rows=rows)
        if n.startswith('INSERT INTO response_action_approvals'):
            self.approvals.append({
                'workspace_id': params[1], 'subject_domain': params[2], 'subject_id': params[3],
                'action_version': params[4], 'approver_user_id': params[5], 'approver_role': params[6],
                'decision': params[7], 'note': params[8], 'required_quorum': params[9], 'policy': params[10],
            })
            return _Result()
        return _Result()

    def commit(self):
        return None


def _policy_row(**over):
    row = {
        'id': ACTION_ID,
        'workspace_id': 'ws-1',
        'status': 'pending',
        'incident_id': INCIDENT,
        'alert_id': None,
        'action_type': 'freeze_wallet',
        'mode': 'recommended',
        'execution_state': 'proposed',
        'execution_metadata': {},
        'execution_artifacts': {},
        'provider_receipts': [],
        'safe_tx_hash': None,
        'approved_at': None,
        'failed_at': None,
        # A DIFFERENT proposer, so separation-of-duties allows this approver to approve.
        'created_by_user_id': 'proposer-9',
    }
    row.update(over)
    return row


def _patch_policy(monkeypatch, conn, *, user_id='approver-1', role='admin'):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': user_id}, {'workspace_id': 'ws-1', 'role': role}))
    monkeypatch.setattr(pilot, '_session_mfa_satisfied', lambda *_a, **_k: True)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    # The policy branch requires the action to be approvable; isolate the test to the
    # history-event details by stubbing the surrounding lifecycle/payload machinery.
    monkeypatch.setattr(pilot, 'response_action_lifecycle', lambda *_a, **_k: {'requires_approval': True})
    monkeypatch.setattr(pilot, '_enforce_action_policy_per_mode', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_append_status_transition', lambda artifacts, **k: artifacts)
    monkeypatch.setattr(pilot, '_execution_artifact_audit_snapshot', lambda *a, **k: {})
    monkeypatch.setattr(pilot, '_response_action_payload', lambda payload, **k: dict(payload))
    captured: dict[str, list[dict]] = {'history': [], 'timeline': []}
    monkeypatch.setattr(pilot, 'write_action_history',
                        lambda connection, **k: captured['history'].append(dict(k)))
    monkeypatch.setattr(pilot, 'append_incident_timeline_event',
                        lambda connection, **k: captured['timeline'].append(dict(k)))
    return captured


# 12. The POLICY (non-AI) approve branch writes the SAME enriched approval event.
def test_policy_action_approval_writes_enriched_event(monkeypatch):
    conn = _PolicyConn(response_row=_policy_row())
    captured = _patch_policy(monkeypatch, conn)
    pilot.approve_enforcement_action(ACTION_ID, _req())
    approved = _events(captured, 'response_action.approved')
    assert len(approved) == 1
    d = approved[0]['details']
    assert d['event_type'] == 'response_action_approved'
    assert d['display_label'] == 'Response Action Approved'
    assert d['type_label'] == 'Action Approval'
    assert d['decision'] == 'approved'
    assert d['previous_lifecycle'] == 'Awaiting Approval'
    assert d['new_lifecycle'] == 'Approved'
    assert d['approval_progress_label'] == '1 of 1'
    assert d['result_summary'] == 'Success'
    assert d['action_id'] == ACTION_ID


# 13. The POLICY (non-AI) reject branch writes the enriched rejection event.
def test_policy_action_rejection_writes_enriched_event(monkeypatch):
    conn = _PolicyConn(response_row=_policy_row())
    captured = _patch_policy(monkeypatch, conn)
    pilot.reject_enforcement_action(ACTION_ID, {'reason': 'Out of policy.'}, _req())
    rejected = _events(captured, 'response_action.rejected')
    assert len(rejected) == 1
    d = rejected[0]['details']
    assert d['event_type'] == 'response_action_rejected'
    assert d['decision'] == 'rejected'
    assert d['new_lifecycle'] == 'Rejected'
    assert d['note'] == 'Out of policy.'
