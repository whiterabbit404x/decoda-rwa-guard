"""Canonical Pending-Approval / summary consistency for Screen 8 (Response Actions).

These tests pin the fix for the observed contradiction where the top summary card
showed "Pending Approval: 0" while the table rendered actions with "Requires
Approval: Yes". The root cause was that AI recommendation reviews carried
requires_approval=True but no canonical lifecycle/approval_status, so they were
rendered as approval-required yet excluded from the Pending Approval count.

The canonical definition (one place, one meaning) is:

    An action is "pending approval" when it requires approval and its required
    approval quorum has not been satisfied and it is not rejected/cancelled/
    executed. A successful simulation does NOT clear the approval requirement.

Every count consumed by the cards, the agent panel, and the approval banner comes
from build_response_actions_summary over the same per-record lifecycle, so they can
never disagree.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


def _policy(action_type: str, *, mode: str = 'recommended', execution_state: str = 'recommended',
            status: str = 'pending', approved_at: str | None = None,
            execution_metadata: dict | None = None, created_by_user_id: str | None = None) -> dict:
    """Build a policy response-action DTO exactly as list_enforcement_actions does."""
    action = {
        'id': f'{action_type}-{mode}',
        'action_type': action_type,
        'record_type': 'response_action',
        'mode': mode,
        'status': status,
        'execution_state': execution_state,
        'approved_at': approved_at,
        'created_by_user_id': created_by_user_id,
        'incident_id': 'inc-1',
        'execution_metadata': execution_metadata or {'chain_linked_ids': {'incident_id': 'inc-1'}},
    }
    return pilot._response_action_payload(action)


def _ai_review(*, review_state: str = 'pending_review', requires_approval: bool = True,
               reason: str = 'Page the on-call security team.', action_type: str = 'notify_security_team') -> dict:
    return pilot._ai_recommendation_review_payload({
        'recommendation_id': f'rec-{review_state}-{reason[:6]}',
        'incident_id': 'inc-1',
        'action_type': action_type,
        'runbook_id': 'notify_security_team_v1',
        'requires_human_approval': requires_approval,
        'review_state': review_state,
        'reason': reason,
        'evidence_refs': ['telemetry:x'],
        'triage_job_id': 'job-1',
        'triage_result_id': 'res-1',
    })


# ── 12.1 Requires-approval action counts as Pending Approval ──────────────────

def test_requires_approval_policy_action_is_pending():
    action = _policy('escalate_to_issuer')
    assert action['requires_approval'] is True
    assert action['approval_status'] == 'pending'
    assert action['lifecycle_state'] == 'awaiting_approval'
    summary = pilot.build_response_actions_summary([action])
    assert summary['pending_approval'] == 1


# ── 12.2 Simulated action can still count as Pending Approval ─────────────────

def test_simulated_action_still_counts_as_pending_approval():
    # A successful dry-run must NOT remove an approval-required action from Pending
    # Approval. It counts in BOTH Simulated and Pending Approval (overlapping dims).
    action = _policy('escalate_multisig', mode='simulated', execution_state='simulated')
    assert action['approval_status'] == 'pending'
    assert action['lifecycle']['simulation_status'] == 'passed'
    summary = pilot.build_response_actions_summary([action])
    assert summary['pending_approval'] == 1
    assert summary['simulated'] == 1


# ── 12.3 Approval-complete action no longer counts as Pending Approval ────────

def test_approved_action_not_pending():
    action = _policy('escalate_to_issuer', approved_at='2026-08-03T00:00:00Z')
    assert action['approval_status'] == 'approved'
    assert action['current_approval_count'] == 1
    summary = pilot.build_response_actions_summary([action])
    assert summary['pending_approval'] == 0


# ── 12.4 Rejected action no longer counts as Pending Approval ─────────────────

def test_rejected_action_not_pending():
    action = _policy(
        'escalate_to_issuer',
        status='canceled',
        execution_metadata={'chain_linked_ids': {'incident_id': 'inc-1'},
                            'rejected_at': '2026-08-03T00:00:00Z', 'rejection_reason': 'not applicable'},
    )
    assert action['approval_status'] == 'rejected'
    assert action['lifecycle_state'] == 'rejected'
    summary = pilot.build_response_actions_summary([action])
    assert summary['pending_approval'] == 0
    # Rejected is not an active recommendation either.
    assert summary['recommended'] == 0


# ── 12.5 Executed action no longer counts as Pending Approval ─────────────────

def test_executed_action_not_pending():
    action = _policy('escalate_to_issuer', status='executed', execution_state='confirmed',
                     approved_at='2026-08-03T00:00:00Z')
    assert action['execution_status'] == 'executed'
    summary = pilot.build_response_actions_summary([action])
    assert summary['pending_approval'] == 0
    assert summary['executed'] == 1


# ── 12.6 Cards and agent summary use the SAME definitions ─────────────────────

def test_summary_matches_recomputed_row_counts():
    actions = [
        _policy('preserve_evidence', mode='simulated', execution_state='simulated'),
        _policy('escalate_multisig', mode='simulated', execution_state='simulated'),   # pending + simulated
        _policy('escalate_to_issuer'),                                                  # pending
        _policy('notify_team', mode='simulated', execution_state='simulated'),
        _policy('increase_monitoring', mode='simulated', execution_state='simulated'),
        _policy('snapshot_chain_state'),
        _ai_review(),                                                                   # pending (AI)
    ]
    summary = pilot.build_response_actions_summary(actions)

    # Recompute the exact same way the frontend derives its fallback counts.
    row_pending = sum(1 for a in actions if a['approval_status'] == 'pending')
    row_simulated = sum(1 for a in actions if (a.get('lifecycle') or {}).get('simulation_status') == 'passed')
    row_executed = sum(1 for a in actions if a['execution_status'] == 'executed')

    assert summary['pending_approval'] == row_pending == 3
    assert summary['simulated'] == row_simulated == 4
    assert summary['executed'] == row_executed == 0
    assert summary['awaiting_approval'] == summary['pending_approval']


# ── 12.7 AI review pending + requires approval counts as pending ──────────────

def test_ai_review_pending_requires_approval_is_awaiting_approval():
    review = _ai_review(review_state='pending_review', requires_approval=True)
    assert review['requires_approval'] is True
    assert review['approval_status'] == 'pending'
    assert review['lifecycle_state'] == 'awaiting_approval'
    assert review['current_approval_count'] == 0
    assert review['required_approval_count'] == 1
    summary = pilot.build_response_actions_summary([review])
    assert summary['pending_approval'] == 1
    # A review is never a simulation/execution.
    assert summary['simulated'] == 0
    assert summary['executed'] == 0


def test_ai_review_without_required_approval_is_not_pending():
    review = _ai_review(review_state='pending_review', requires_approval=False)
    assert review['approval_status'] == 'not_required'
    assert pilot.build_response_actions_summary([review])['pending_approval'] == 0


# ── 12.8 Accepted/rejected AI review no longer pending ────────────────────────

def test_ai_review_accepted_is_approved_not_pending():
    review = _ai_review(review_state='accepted')
    assert review['approval_status'] == 'approved'
    assert review['lifecycle_state'] == 'approved'
    assert pilot.build_response_actions_summary([review])['pending_approval'] == 0


def test_ai_review_rejected_is_not_pending():
    review = _ai_review(review_state='rejected')
    assert review['approval_status'] == 'rejected'
    assert pilot.build_response_actions_summary([review])['pending_approval'] == 0


# ── 12.15 Different Notify Team targets stay distinct ─────────────────────────

def test_ai_reviews_with_different_targets_are_distinguishable():
    a = _ai_review(reason='Notify the incident-response team for containment.')
    b = _ai_review(reason='Notify the treasury operations desk for reconciliation.')
    # Same operator title, but the distinct target subtitle disambiguates them.
    assert a['title'] == b['title']
    assert a['target_label'] and b['target_label']
    assert a['target_label'] != b['target_label']


# ── 12.17 Incident-only provenance for an AI review ───────────────────────────

def test_ai_review_incident_provenance_is_truthful():
    review = _ai_review()
    prov = review['provenance']
    assert prov['primary_source_label'] == 'AI investigation'
    assert prov['incident_id'] == 'inc-1'
    assert prov['has_evidence_package'] is False
    # The single provenance record links to the incident source route.
    assert any(r['source_type'] == 'incident' and r['source_route'] == '/incidents/inc-1' for r in prov['records'])


# ── Approval quorum progress ──────────────────────────────────────────────────

@pytest.mark.parametrize('approved_at,expected', [(None, 0), ('2026-08-03T00:00:00Z', 1)])
def test_approval_quorum_progress_counts(approved_at, expected):
    action = _policy('freeze_wallet', approved_at=approved_at)
    assert action['required_approval_count'] == 1
    assert action['current_approval_count'] == expected


# ── Command eligibility surfaces approve/reject only while pending ────────────

def test_commands_offer_approve_reject_only_when_pending():
    pending = _policy('escalate_to_issuer')
    assert 'approve' in pending['commands']['allowed_commands']
    assert 'reject' in pending['commands']['allowed_commands']

    approved = _policy('escalate_to_issuer', approved_at='2026-08-03T00:00:00Z')
    assert 'approve' not in approved['commands']['allowed_commands']
    assert 'reject' not in approved['commands']['allowed_commands']

    no_approval = _policy('notify_team')
    assert 'approve' not in no_approval['commands']['allowed_commands']


# ── Approval permission (role + separation of duties) ─────────────────────────

def test_approval_permission_denies_non_admin_role():
    action = {'action_type': 'freeze_wallet', 'created_by_user_id': 'user-2'}
    perm = pilot.response_action_approval_permission(
        action, workspace_context={'role': 'analyst'}, current_user_id='user-1')
    assert perm['can_current_user_approve'] is False
    assert 'owner' in perm['approval_permission_reason'].lower() or 'admin' in perm['approval_permission_reason'].lower()


def test_approval_permission_denies_proposer_separation_of_duties():
    action = {'action_type': 'freeze_wallet', 'created_by_user_id': 'user-1'}
    perm = pilot.response_action_approval_permission(
        action, workspace_context={'role': 'admin'}, current_user_id='user-1')
    assert perm['can_current_user_approve'] is False
    assert 'proposer' in perm['approval_permission_reason'].lower()


def test_approval_permission_allows_authorized_approver():
    action = {'action_type': 'freeze_wallet', 'created_by_user_id': 'user-2'}
    perm = pilot.response_action_approval_permission(
        action, workspace_context={'role': 'admin'}, current_user_id='user-1')
    assert perm['can_current_user_approve'] is True
    assert perm['approval_permission_reason'] is None


# ── Reject endpoint: reason required, requires-approval, persistence ──────────

class _RejectResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


def _reject_connection(row: dict, executed: list):
    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized and normalized.startswith('SELECT'):
                return _RejectResult(dict(row))
            return _RejectResult()

        def commit(self):
            return None

    return _Connection()


def _patch_reject(monkeypatch, connection, *, role='admin', user_id='approver-1'):
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: ({'id': user_id}, {'workspace_id': 'ws-1', 'role': role}))
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


def test_reject_requires_a_reason(monkeypatch):
    executed: list = []
    connection = _reject_connection(
        {'id': 'act-1', 'status': 'pending', 'action_type': 'freeze_wallet', 'mode': 'recommended',
         'execution_state': 'recommended', 'execution_metadata': {}, 'created_by_user_id': 'proposer-1',
         'incident_id': 'inc-1'},
        executed,
    )
    _patch_reject(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc:
        pilot.reject_enforcement_action('act-1', {'reason': '   '}, SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert exc.value.status_code == 400
    # No UPDATE was issued for a reason-less reject.
    assert not any(stmt.startswith('UPDATE response_actions') for stmt, _ in executed)


def test_reject_persists_rejection_and_drops_from_pending(monkeypatch):
    executed: list = []
    connection = _reject_connection(
        {'id': 'act-1', 'status': 'pending', 'action_type': 'freeze_wallet', 'mode': 'recommended',
         'execution_state': 'recommended', 'execution_metadata': {}, 'created_by_user_id': 'proposer-1',
         'incident_id': 'inc-1', 'provider_receipts': []},
        executed,
    )
    _patch_reject(monkeypatch, connection)
    result = pilot.reject_enforcement_action('act-1', {'reason': 'Duplicate of a prior action.'}, SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    # Persisted as canceled + rejection metadata; canonical lifecycle reads rejected.
    assert result['approval_status'] == 'rejected'
    assert result['lifecycle_state'] == 'rejected'
    update = next((params for stmt, params in executed if stmt.startswith('UPDATE response_actions')), None)
    assert update is not None and update[0] == 'canceled'
    assert pilot.build_response_actions_summary([result])['pending_approval'] == 0


def test_reject_blocks_action_that_does_not_require_approval(monkeypatch):
    executed: list = []
    connection = _reject_connection(
        {'id': 'act-1', 'status': 'pending', 'action_type': 'notify_team', 'mode': 'recommended',
         'execution_state': 'recommended', 'execution_metadata': {}, 'created_by_user_id': 'proposer-1',
         'incident_id': 'inc-1'},
        executed,
    )
    _patch_reject(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc:
        pilot.reject_enforcement_action('act-1', {'reason': 'n/a'}, SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert exc.value.status_code == 409


# ── Approve eligibility follows the lifecycle, not the raw mode ───────────────

def test_simulated_approval_required_action_stays_approvable():
    # The approve guard is `if not lifecycle['requires_approval']: 409`. A simulated
    # action that still requires approval keeps requires_approval=True, so approval
    # is NOT blocked merely because it was dry-run simulated.
    row = {'id': 'act-1', 'action_type': 'escalate_multisig', 'mode': 'simulated',
           'status': 'pending', 'execution_state': 'simulated'}
    assert pilot.response_action_lifecycle(row)['requires_approval'] is True


def test_non_approval_action_is_not_approvable_by_lifecycle():
    row = {'id': 'act-1', 'action_type': 'notify_team', 'mode': 'recommended',
           'status': 'pending', 'execution_state': 'recommended'}
    assert pilot.response_action_lifecycle(row)['requires_approval'] is False


# ── 12.13 Simulate-All excludes an already-valid simulation ───────────────────

def test_simulate_eligibility_excludes_already_simulated():
    eligible, reason = pilot._response_action_simulate_eligibility(
        {'action_type': 'freeze_wallet', 'mode': 'simulated', 'status': 'pending', 'execution_state': 'simulated'})
    assert eligible is False
    assert reason == 'already_simulated'

    eligible, reason = pilot._response_action_simulate_eligibility(
        {'action_type': 'freeze_wallet', 'mode': 'recommended', 'status': 'pending', 'execution_state': 'recommended'})
    assert eligible is True
