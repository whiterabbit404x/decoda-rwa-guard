"""Screen 8 response-action approval — session-MFA step-up gate.

Approving (or rejecting) a response action is a step-up-protected command: it
requires the caller's SESSION to have completed an MFA challenge, enforced by the
workspace security policy. These tests pin the FINAL Screen 8 approval-gating pass:

  * a session that has not completed MFA can never persist an approval — no
    decision row, no quorum change, no summary-count change, and no SUCCESSFUL
    approval history / incident-timeline event is produced;
  * the block is CONTEXTUAL — a communication action is never called "destructive",
    while a fund-moving / contract-pausing / credential-rotating action always is;
  * the canonical presentation DTO exposes the block (``mfa_required`` +
    ``approval_blocked_reason_code`` + a contextual reason) so the UI renders it
    near the controls instead of surfacing a bare HTTP error;
  * once MFA is satisfied a one-of-one approval succeeds and moves the action out of
    Pending Approval, leaving the total recommendation count unchanged;
  * recommendation REVIEW stays fully separate from action APPROVAL;
  * workspace isolation is preserved.

The MFA policy itself is NEVER weakened. These follow the repo's fake-connection
unit style (no real DB / model / network).
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
    """Fake connection backing the ai_recommendations read and the SEPARATE
    response_action_approvals store, so a persisted approval (or its absence on a
    blocked attempt) is observable rather than assumed."""

    def __init__(self, *, ai_rows=None, approvals=None, schema_ready=True):
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self._ai_rows = {(str(r['id']), str(r['workspace_id'])): r for r in (ai_rows or [])}
        self.approvals: list[dict] = list(approvals or [])
        self._schema_ready = schema_ready

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'to_regclass' in n:
            return _Result(row={'present': self._schema_ready})
        if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in n and n.startswith('SELECT'):
            return _Result(row=None)  # force the AI-recommendation-backed path
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
        self.commits += 1
        return None


def _ai_rec(**over):
    row = {
        'id': REC_ID,
        'workspace_id': 'ws-1',
        'incident_id': INCIDENT,
        # A COMMUNICATION action (the exact Screen 8 scenario: "Notify security team")
        # — it must never be described with destructive-action wording.
        'action_type': 'notify_security_team',
        'runbook_id': 'notify_security_team_v1',
        'review_state': 'accepted',
        'requires_human_approval': True,
    }
    row.update(over)
    return row


def _patch(monkeypatch, conn, *, mfa_satisfied, user_id='approver-1', role='admin'):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': user_id}, {'workspace_id': 'ws-1', 'role': role}))
    monkeypatch.setattr(pilot, '_session_mfa_satisfied', lambda *_a, **_k: mfa_satisfied)
    events = {'history': [], 'timeline': []}
    monkeypatch.setattr(pilot, 'write_action_history',
                        lambda *a, **k: events['history'].append(k.get('action_type')))
    monkeypatch.setattr(pilot, 'append_incident_timeline_event',
                        lambda *a, **k: events['timeline'].append(k.get('event_type')))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    return events


def _req():
    return SimpleNamespace(headers={'x-workspace-id': 'ws-1'}, client=SimpleNamespace(host='127.0.0.1'), method='POST')


# 1. Approval WITHOUT session MFA is rejected (fail-closed, contextual code).
def test_approval_without_mfa_is_rejected(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn, mfa_satisfied=False)
    with pytest.raises(HTTPException) as exc:
        pilot.approve_enforcement_action(REC_ID, _req())
    assert exc.value.status_code == 403
    assert exc.value.detail['code'] == 'MFA_CHALLENGE_REQUIRED'
    assert exc.value.detail['reason_code'] == 'mfa_required'


# 2. A blocked approval creates NO approval decision.
def test_blocked_approval_creates_no_decision(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn, mfa_satisfied=False)
    with pytest.raises(HTTPException):
        pilot.approve_enforcement_action(REC_ID, _req())
    assert conn.approvals == []
    # And no approval row was ever INSERTed.
    assert not any(s.startswith('INSERT INTO response_action_approvals') for s, _ in conn.executed)


# 3. Approval progress (quorum) is unchanged after a blocked attempt.
def test_blocked_approval_leaves_progress_unchanged(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn, mfa_satisfied=False)
    with pytest.raises(HTTPException):
        pilot.approve_enforcement_action(REC_ID, _req())
    summary = pilot._action_approval_summary(
        conn, workspace_id='ws-1', subject_domain='ai_recommendation',
        subject_id=REC_ID, action_version=1, required_quorum=1, current_user_id='approver-1',
    )
    assert summary['approved_count'] == 0
    assert summary['current_user_decided'] is False


# 4. Summary counts are unchanged (the action is still Pending Approval).
def test_blocked_approval_leaves_summary_unchanged(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    _patch(monkeypatch, conn, mfa_satisfied=False)
    with pytest.raises(HTTPException):
        pilot.approve_enforcement_action(REC_ID, _req())
    # The record still reads pending because no decision persisted.
    pending = pilot._ai_recommendation_review_payload(
        _ai_rec(), approval_summary={'required_quorum': 1, 'approved_count': 0, 'rejected': False})
    assert pilot.build_response_actions_summary([pending])['pending_approval'] == 1


# 5. NO successful approval history event is created (only a distinct blocked event).
def test_blocked_approval_records_no_success_history(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    events = _patch(monkeypatch, conn, mfa_satisfied=False)
    with pytest.raises(HTTPException):
        pilot.approve_enforcement_action(REC_ID, _req())
    assert 'response_action.approved' not in events['history']
    # A distinct, audit-only "attempt blocked" event IS recorded (never a success).
    assert 'response_action.approval_attempt_blocked' in events['history']


# 6. NO successful incident-timeline approval event is created.
def test_blocked_approval_records_no_success_timeline(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    events = _patch(monkeypatch, conn, mfa_satisfied=False)
    with pytest.raises(HTTPException):
        pilot.approve_enforcement_action(REC_ID, _req())
    assert 'response_action.approved' not in events['timeline']
    assert 'response_action.approval_attempt_blocked' in events['timeline']


# 7. The canonical approval-gate DTO exposes mfa_required + mfa_satisfied.
def test_dto_exposes_mfa_required():
    gate = pilot.response_action_approval_gate(
        action_type='notify_security_team', profile=None,
        permission={'can_current_user_approve': True, 'approval_permission_reason': None},
        approval_summary={'required_quorum': 1, 'approved_count': 0, 'current_user_decided': False},
        mfa_satisfied=False, approvable=True,
    )
    assert gate['mfa_required'] is True
    assert gate['mfa_satisfied'] is False
    assert gate['can_current_user_approve'] is False
    # The caller HAS approval permission — only the MFA step-up is missing — so the
    # UI can show a disabled control + MFA prompt (not "you lack permission").
    assert gate['has_approval_permission'] is True
    assert gate['approval_blocked_reason_code'] == 'mfa_required'


# 8. The DTO's blocked reason is CONTEXTUAL, not a generic HTTP string.
def test_dto_exposes_contextual_blocked_reason():
    gate = pilot.response_action_approval_gate(
        action_type='notify_security_team', profile=None,
        permission={'can_current_user_approve': True, 'approval_permission_reason': None},
        approval_summary={'required_quorum': 1, 'approved_count': 0, 'current_user_decided': False},
        mfa_satisfied=False, approvable=True,
    )
    assert 'Complete MFA' in gate['approval_blocked_reason']
    assert gate['next_required_step'] == 'Complete MFA in this session, then approve the action.'
    assert gate['approval_security_classification'] == 'protected'


# 9. Recommendation REVIEW remains separate from action APPROVAL in the DTO.
def test_recommendation_review_separate_from_action_approval():
    payload = pilot._ai_recommendation_review_payload(
        _ai_rec(review_state='accepted'),
        approval_summary={'required_quorum': 1, 'approved_count': 0, 'rejected': False},
    )
    # Review says accepted; the APPROVAL is still awaiting — two independent gates.
    assert payload['review_state'] == 'accepted'
    assert payload['decision'] == 'accepted'
    assert payload['approval_status'] == 'pending'
    assert payload['lifecycle_state'] == 'awaiting_approval'


# 10. A communication action does NOT receive destructive-action wording.
@pytest.mark.parametrize('action_type', ['notify_security_team', 'notify_team', 'escalate_to_issuer', 'create_ticket'])
def test_communication_action_not_destructive(action_type):
    sec = pilot.response_action_approval_security(action_type)
    assert sec['approval_security_classification'] != 'destructive'
    assert 'destructive' not in sec['approval_security_message'].lower()


# 11. A high-impact action retains strong destructive-action wording.
@pytest.mark.parametrize('action_type', ['freeze_wallet', 'pause_mint_redeem', 'revoke_approval',
                                          'rotate_credential', 'block_transaction', 'pause_asset_transfers'])
def test_high_impact_action_stays_destructive(action_type):
    sec = pilot.response_action_approval_security(action_type)
    assert sec['approval_security_classification'] == 'destructive'
    assert 'destructive action' in sec['approval_security_message']


# 11b. A monitoring-configuration change is classified distinctly (not destructive).
@pytest.mark.parametrize('action_type', ['increase_monitoring', 'suppress_rule', 'disable_monitored_system'])
def test_config_change_action_classification(action_type):
    sec = pilot.response_action_approval_security(action_type)
    assert sec['approval_security_classification'] == 'configuration_change'
    assert 'configuration-changing action' in sec['approval_security_message']


# 12. Approval SUCCEEDS once MFA is satisfied.
def test_approval_succeeds_after_mfa(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    events = _patch(monkeypatch, conn, mfa_satisfied=True)
    result = pilot.approve_enforcement_action(REC_ID, _req())
    assert result['approval_status'] == 'approved'
    assert len(conn.approvals) == 1 and conn.approvals[0]['decision'] == 'approved'
    assert 'response_action.approved' in events['history']
    assert 'response_action.approved' in events['timeline']
    # And no blocked event was recorded on the successful path.
    assert 'response_action.approval_attempt_blocked' not in events['history']


# 13 & 14. One-of-one approval moves Pending Approval 4 -> 3 while Recommended
#          Actions stays 8 (the deployed Screen 8 numbers). An approved response
#          action stays an active Recommended row (now ready to execute) — it only
#          leaves the Pending Approval bucket.
def _pending_action(i):
    return {'record_type': 'response_action', 'approval_status': 'pending',
            'lifecycle_state': 'awaiting_approval', 'execution_status': 'not_started'}


def _approved_action(i):
    return {'record_type': 'response_action', 'approval_status': 'approved',
            'lifecycle_state': 'ready_to_execute', 'execution_status': 'not_started'}


def test_one_of_one_moves_pending_four_to_three_total_stays_eight():
    # 8 recommended actions, 4 awaiting approval (matches the deployed cards).
    before = [_pending_action(i) for i in range(4)] + [_approved_action(i) for i in range(4)]
    before_summary = pilot.build_response_actions_summary(before)
    assert before_summary['recommended'] == 8
    assert before_summary['pending_approval'] == 4
    assert before_summary['total'] == 8

    # Approve one of the four pending actions: it moves from pending -> approved but
    # remains an active recommended row.
    after = [_pending_action(i) for i in range(3)] + [_approved_action(i) for i in range(5)]
    after_summary = pilot.build_response_actions_summary(after)
    assert after_summary['pending_approval'] == 3
    assert after_summary['recommended'] == 8
    assert after_summary['total'] == 8


# 15. Workspace isolation: an action in another workspace is not found, even with MFA.
def test_workspace_isolation_enforced(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec(workspace_id='ws-OTHER')])
    _patch(monkeypatch, conn, mfa_satisfied=True)  # caller resolves to ws-1
    with pytest.raises(HTTPException) as exc:
        pilot.approve_enforcement_action(REC_ID, _req())
    assert exc.value.status_code == 404


# 16. Rejection is ALSO session-MFA gated, with its own contextual reason.
def test_rejection_without_mfa_is_rejected(monkeypatch):
    conn = _ApprovalConn(ai_rows=[_ai_rec()])
    events = _patch(monkeypatch, conn, mfa_satisfied=False)
    with pytest.raises(HTTPException) as exc:
        pilot.reject_enforcement_action(REC_ID, {'reason': 'Not warranted.'}, _req())
    assert exc.value.status_code == 403
    assert exc.value.detail['code'] == 'MFA_CHALLENGE_REQUIRED'
    # The reject message uses the reject verb, not "approving".
    assert 'rejecting' in exc.value.detail['message']
    assert conn.approvals == []
    assert 'response_action.rejected' not in events['history']


# 17. The session-MFA probe fails CLOSED when the session cannot be resolved.
def test_session_mfa_probe_fails_closed(monkeypatch):
    class _NoSessionConn:
        def execute(self, *_a, **_k):
            raise HTTPException(status_code=401, detail='Session is no longer active.')
    assert pilot._session_mfa_satisfied(_NoSessionConn(), _req()) is False
