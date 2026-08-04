"""Response-action APPROVAL domain (Screen 8) — separate from recommendation review.

Screen 8's "Approve" is a RESPONSE-ACTION APPROVAL: an authorized approver permits
a mitigation action to proceed toward execution. It is recorded in the dedicated
``response_action_approvals`` table (migration 0137) with a uniqueness scope that
can never collide with an AI recommendation REVIEW (``ai_recommendations.review_state``).

These tests prove the two domains are fully separated: a prior recommendation review
never blocks a valid action approval, the approval persists on its own with its own
quorum, duplicate approvals are rejected, unauthorized approvals are rejected, and
the approval produces action-history + incident-timeline events and updates the
canonical summary counts. They follow the repo's fake-connection unit style
(no real DB / model / network).
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


INCIDENT = 'c537b73f-1976-4a44-b589-946194794399'
REC_ID = 'a1111111-1111-4111-8111-111111111111'
ACTION_ID = 'b2222222-2222-4222-8222-222222222222'


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _ApprovalConn:
    """Fake connection backing both the ai_recommendations read and the SEPARATE
    response_action_approvals store, so approval decisions actually persist and
    quorum/duplicate logic is exercised rather than assumed."""

    def __init__(self, *, ai_rows=None, response_rows=None, approvals=None,
                 schema_ready=True):
        self.executed: list[tuple[str, object]] = []
        self._ai_rows = {(str(r['id']), str(r['workspace_id'])): r for r in (ai_rows or [])}
        self._response_rows = {(str(r['id']), str(r['workspace_id'])): r for r in (response_rows or [])}
        self.approvals: list[dict] = list(approvals or [])
        self._schema_ready = schema_ready

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))

        if 'to_regclass' in n:
            return _Result(row={'present': self._schema_ready})

        if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in n and n.startswith('SELECT'):
            return _Result(row=self._response_rows.get((str(params[0]), str(params[1]))))

        if 'FROM ai_recommendations' in n and n.startswith('SELECT'):
            # params: (rec_id, workspace_id)
            return _Result(row=self._ai_rows.get((str(params[0]), str(params[1]))))

        if 'SELECT decision FROM response_action_approvals' in n:
            # duplicate check: (ws, domain, subject_id, version, approver)
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
            # summary: (ws, domain, subject_id, version)
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
            # (_id, ws, domain, subject_id, version, approver, role, decision, note, quorum, policy)
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
        # A recommendation REVIEW already exists and is ACCEPTED — this must NOT
        # block or satisfy the response-action approval.
        'review_state': 'accepted',
        'requires_human_approval': True,
    }
    row.update(over)
    return row


def _patch(monkeypatch, conn, *, user_id='approver-1', role='admin'):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': user_id}, {'workspace_id': 'ws-1', 'role': role}))
    # These tests exercise the approval DOMAIN, not the session-MFA step-up: run them
    # as an MFA-satisfied session so the (separately tested) gate is a no-op here.
    monkeypatch.setattr(pilot, '_session_mfa_satisfied', lambda *_a, **_k: True)
    events = {'history': [], 'timeline': []}
    monkeypatch.setattr(pilot, 'write_action_history',
                        lambda *a, **k: events['history'].append(k.get('action_type')))
    monkeypatch.setattr(pilot, 'append_incident_timeline_event',
                        lambda *a, **k: events['timeline'].append(k.get('event_type')))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    return events


def _req():
    return SimpleNamespace(headers={'x-workspace-id': 'ws-1'}, client=SimpleNamespace(host='127.0.0.1'), method='POST')


# 1. A prior recommendation review does NOT block a response-action approval.
def test_prior_recommendation_review_does_not_block_action_approval(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(review_state='accepted')])
    _patch(monkeypatch, conn)
    result = pilot.approve_enforcement_action(REC_ID, _req())
    # It did NOT raise "Recommendation has already been reviewed"; a real approval
    # decision was recorded in the SEPARATE domain.
    assert result['approval_status'] == 'approved'
    assert len(conn.approvals) == 1
    assert conn.approvals[0]['subject_domain'] == 'ai_recommendation'
    assert conn.approvals[0]['decision'] == 'approved'


# 2. Action approval persists separately from recommendation review (review_state untouched).
def test_action_approval_does_not_touch_review_state(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(review_state='accepted')])
    _patch(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    # No UPDATE of ai_recommendations.review_state was ever issued.
    assert not any('UPDATE ai_recommendations' in stmt for stmt, _ in conn.executed)


# 3. One-of-one approval reaches quorum.
def test_one_of_one_reaches_quorum(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn)
    result = pilot.approve_enforcement_action(REC_ID, _req())
    assert result['approval_status'] == 'approved'
    assert result['current_approval_count'] == 1
    assert result['required_approval_count'] == 1
    assert 'quorum reached' in result['message'].lower()


# 4. One-of-two approval updates progress WITHOUT reaching quorum.
def test_one_of_two_progresses_without_quorum(monkeypatch):
    conn = _ApprovalConn(
        ai_rows=[_ai_rec(execution_metadata={'required_approval_quorum': 2})],
    )
    # A required quorum of 2 comes from the recommendation policy; model it by
    # pre-seeding the quorum on the stored decisions.
    monkeypatch.setattr(pilot, '_required_approval_quorum', lambda *_a, **_k: 2)
    _patch(monkeypatch, conn)
    result = pilot.approve_enforcement_action(REC_ID, _req(), )
    assert result['approval_status'] == 'pending'
    assert result['current_approval_count'] == 1
    assert result['required_approval_count'] == 2
    assert 'still pending' in result['message'].lower()


# 5. Duplicate action approval by the same approver is rejected.
def test_duplicate_approval_same_approver_rejected(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn, user_id='approver-1')
    pilot.approve_enforcement_action(REC_ID, _req())
    with pytest.raises(HTTPException) as exc:
        pilot.approve_enforcement_action(REC_ID, _req())
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'ACTION_VERSION_ALREADY_DECIDED'


# 6. A recommendation review and an action approval use SEPARATE uniqueness scopes:
#    the same user can both (previously) review AND now approve without collision.
def test_review_and_approval_use_separate_uniqueness_scopes(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(review_state='accepted')])
    _patch(monkeypatch, conn, user_id='approver-1')
    result = pilot.approve_enforcement_action(REC_ID, _req())
    # The approval succeeded (separate scope), and it is stored in the approval domain.
    assert result['approval_status'] == 'approved'
    assert conn.approvals[0]['subject_domain'] == 'ai_recommendation'


# 7. Unauthorized (non owner/admin) approval is rejected.
def test_unauthorized_role_rejected(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn, role='analyst')
    with pytest.raises(HTTPException) as exc:
        pilot.approve_enforcement_action(REC_ID, _req())
    assert exc.value.status_code == 403
    assert exc.value.detail['code'] == 'APPROVAL_NOT_PERMITTED'
    assert conn.approvals == []


# 8. Approval creates an action-history event.
def test_approval_creates_action_history(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    events = _patch(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    assert 'response_action.approved' in events['history']


# 9. Approval creates an incident-timeline event.
def test_approval_creates_incident_timeline(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    events = _patch(monkeypatch, conn)
    pilot.approve_enforcement_action(REC_ID, _req())
    assert 'response_action.approved' in events['timeline']


# 10. Approval updates the canonical summary counts (pending -> 0 once approved).
def test_approval_updates_canonical_summary(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn)
    approved = pilot.approve_enforcement_action(REC_ID, _req())
    assert pilot.build_response_actions_summary([approved])['pending_approval'] == 0
    # Before approval the same action is pending.
    pending = pilot._ai_recommendation_review_payload(_ai_rec())
    assert pilot.build_response_actions_summary([pending])['pending_approval'] == 1


# 11. Rejection is separate from recommendation rejection (uses the approval domain).
def test_rejection_uses_approval_domain(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(review_state='accepted')])
    _patch(monkeypatch, conn)
    result = pilot.reject_enforcement_action(REC_ID, {'reason': 'Not warranted.'}, _req())
    assert result['approval_status'] == 'rejected'
    assert conn.approvals[0]['decision'] == 'rejected'
    assert conn.approvals[0]['note'] == 'Not warranted.'
    # review_state was never mutated.
    assert not any('UPDATE ai_recommendations' in stmt for stmt, _ in conn.executed)


# 12. Legacy mixed data: a recommendation with an accepted review but NO approval
#     decision is still Awaiting Approval (the repair never fabricates an approval).
def test_legacy_review_without_approval_is_still_awaiting(monkeypatch):
    payload = pilot._ai_recommendation_review_payload(
        _ai_rec(review_state='accepted'),
        approval_summary={'required_quorum': 1, 'approved_count': 0, 'rejected': False},
    )
    assert payload['approval_status'] == 'pending'
    assert payload['lifecycle_state'] == 'awaiting_approval'


# 13. Workspace isolation: an action in another workspace is not found.
def test_workspace_isolation_enforced(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(workspace_id='ws-OTHER')])
    _patch(monkeypatch, conn)  # caller resolves to ws-1
    with pytest.raises(HTTPException) as exc:
        pilot.approve_enforcement_action(REC_ID, _req())
    assert exc.value.status_code == 404


# 14. Action-version conflict: a decision on version 1 does not satisfy version 2,
#     and the same approver may approve the NEW version without a duplicate error.
def test_action_version_conflict_isolates_decisions(monkeypatch):
    conn = _ApprovalConn(
        ai_rows=[_ai_rec()],
        approvals=[{'workspace_id': 'ws-1', 'subject_domain': 'ai_recommendation', 'subject_id': REC_ID,
                    'action_version': 1, 'approver_user_id': 'approver-1', 'approver_role': 'admin',
                    'decision': 'approved', 'note': None, 'required_quorum': 1, 'policy': 'p'}],
    )
    _patch(monkeypatch, conn, user_id='approver-1')
    # The current action version is 2 (superseded), so the v1 decision does not count
    # and the same approver can record a fresh decision without a duplicate error.
    monkeypatch.setattr(pilot, '_action_version_for', lambda *_a, **_k: 2)
    result = pilot.approve_enforcement_action(REC_ID, _req())
    assert result['approval_status'] == 'approved'
    v2 = [a for a in conn.approvals if int(a['action_version']) == 2]
    assert len(v2) == 1
