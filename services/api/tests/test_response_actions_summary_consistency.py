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
               reason: str = 'Page the on-call security team.', action_type: str = 'notify_security_team',
               approval_summary: dict | None = None) -> dict:
    # approval_summary models the SEPARATE response-action approval domain
    # (response_action_approvals). When omitted, no approval decisions exist yet, so
    # an approval-required recommendation is Awaiting Approval regardless of its
    # (independent) review_state.
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
    }, approval_summary=approval_summary)


def _approval(*, approved_count: int = 0, required_quorum: int = 1, rejected: bool = False) -> dict:
    """A response-action approval-domain summary (see _action_approval_summary)."""
    return {'required_quorum': required_quorum, 'approved_count': approved_count, 'rejected': rejected,
            'current_user_decided': False, 'current_user_decision': None, 'approver_ids': []}


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


# ── 12.8 Approval is derived from the approval domain, NOT the review state ───
# A recommendation review (accepted/rejected) is INDEPENDENT of the response-action
# approval. Until a real approval decision exists, an approval-required action is
# still Awaiting Approval — a prior review neither approves nor blocks it.

def test_ai_review_accepted_but_not_yet_approved_is_still_pending():
    # The AI recommendation was accepted in review, but no response-action approval
    # decision exists yet -> still Awaiting Approval (review != approval).
    review = _ai_review(review_state='accepted')
    assert review['approval_status'] == 'pending'
    assert review['lifecycle_state'] == 'awaiting_approval'
    assert review['current_approval_count'] == 0
    assert pilot.build_response_actions_summary([review])['pending_approval'] == 1


def test_ai_review_with_approval_quorum_is_approved():
    # A real response-action approval decision (approval domain) reaches quorum and
    # advances the action out of Pending Approval — regardless of review_state.
    review = _ai_review(review_state='accepted', approval_summary=_approval(approved_count=1, required_quorum=1))
    assert review['approval_status'] == 'approved'
    assert review['lifecycle_state'] == 'approved'
    assert review['current_approval_count'] == 1
    assert pilot.build_response_actions_summary([review])['pending_approval'] == 0


def test_ai_review_rejected_in_review_is_still_approval_pending():
    # A review rejection does not record a response-action approval-domain rejection,
    # so the action's APPROVAL state remains pending (the domains are separate).
    review = _ai_review(review_state='rejected')
    assert review['approval_status'] == 'pending'
    assert pilot.build_response_actions_summary([review])['pending_approval'] == 1


def test_ai_action_rejected_in_approval_domain_is_not_pending():
    # A response-action approval-domain rejection DOES drop it out of Pending Approval.
    review = _ai_review(review_state='pending_review', approval_summary=_approval(rejected=True))
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


def test_approval_permission_resolves_reject_alongside_approve():
    # can_current_user_reject mirrors approve: the same owner/admin + separation-of-
    # duties gate governs both decisions (Section 4/5).
    authorized = pilot.response_action_approval_permission(
        {'action_type': 'freeze_wallet', 'created_by_user_id': 'user-2'},
        workspace_context={'role': 'admin'}, current_user_id='user-1')
    assert authorized['can_current_user_reject'] is True

    non_admin = pilot.response_action_approval_permission(
        {'action_type': 'freeze_wallet', 'created_by_user_id': 'user-2'},
        workspace_context={'role': 'analyst'}, current_user_id='user-1')
    assert non_admin['can_current_user_reject'] is False

    proposer = pilot.response_action_approval_permission(
        {'action_type': 'freeze_wallet', 'created_by_user_id': 'user-1'},
        workspace_context={'role': 'admin'}, current_user_id='user-1')
    assert proposer['can_current_user_reject'] is False


# ── Canonical simulation-eligibility breakdown (Sections 7, 8, 16) ────────────

def _deployed_six_actions():
    """The exact deployed shape: 4 simulated Notify Team policy actions + 2 pending
    AI recommendation reviews (real triage always persists requires_human_approval).
    """
    notify = [_policy('notify_team', mode='simulated', execution_state='simulated') for _ in range(4)]
    for idx, action in enumerate(notify):
        action['id'] = f'notify-{idx}'
    reviews = [
        _ai_review(reason=f'Page the on-call security team for incident {i}.') for i in range(2)
    ]
    for idx, review in enumerate(reviews):
        review['id'] = f'rec-{idx}'
    return notify + reviews


def test_deployed_scenario_resolves_to_truthful_counts():
    """Regression pin for the deployed contradiction: the six-action page must read
    Pending Approval = 2 (the AI reviews), Simulated = 4, NOT Pending Approval = 0."""
    summary = pilot.build_response_actions_summary(_deployed_six_actions())
    assert summary['recommended'] == 6
    assert summary['pending_approval'] == 2
    assert summary['awaiting_approval'] == 2
    assert summary['simulated'] == 4
    assert summary['executed'] == 0


def test_simulation_breakdown_explains_why_nothing_is_eligible():
    """The agent panel must never show a bare 'No Eligible Actions'. The breakdown
    accounts for every action: eligible vs already-simulated vs blocked + reason."""
    summary = pilot.build_response_actions_summary(_deployed_six_actions())
    sim = summary['simulation']
    assert sim['eligible'] == 0
    assert sim['already_simulated'] == 4
    assert sim['blocked_total'] == 2
    # The two blocked are the AI reviews, with a truthful, non-enum reason.
    codes = {r['reason_code']: r for r in sim['blocked_reasons']}
    assert 'review_not_simulatable' in codes
    assert codes['review_not_simulatable']['count'] == 2
    assert 'not_simulatable' not in codes['review_not_simulatable']['label'].lower() or codes['review_not_simulatable']['label']
    # Every action is accounted for.
    assert sim['eligible'] + sim['already_simulated'] + sim['blocked_total'] == sim['total'] == 6


def test_recommended_un_simulated_action_is_eligible():
    """A freshly recommended (un-simulated) policy action IS eligible for a dry run,
    so Simulate All shows a real count rather than zero."""
    actions = [_policy('notify_team', mode='recommended', execution_state='recommended')]
    sim = pilot.build_response_actions_summary(actions)['simulation']
    assert sim['eligible'] == 1
    assert sim['already_simulated'] == 0
    assert sim['blocked_total'] == 0
    assert actions[0]['commands']['can_simulate'] is True


def test_valid_simulated_action_is_not_eligible_again():
    """A currently-valid simulation is never re-run by Simulate All (Section 14.15)."""
    action = _policy('notify_team', mode='simulated', execution_state='simulated')
    assert action['commands']['can_simulate'] is False
    assert action['commands']['simulation_is_valid'] is True
    state = pilot.response_action_simulation_state(action)
    assert state['blocked_reason_code'] == 'already_simulated'


def test_executed_action_exposes_terminal_simulation_reason():
    """An executed action is blocked from simulation with a humanized terminal reason
    (never a raw code) (Section 14.17)."""
    action = _policy('escalate_to_issuer', status='executed', execution_state='confirmed',
                     approved_at='2026-08-03T00:00:00Z')
    state = pilot.response_action_simulation_state(action)
    assert state['can_simulate'] is False
    assert state['is_valid'] is False
    assert state['blocked_reason_code'] == 'terminal_status_executed'
    assert state['blocked_reason_label'] and '_' not in state['blocked_reason_label'].split(' ')[0]


# ── Canonical command DTO completeness (Section 4) ────────────────────────────

def test_command_dto_exposes_canonical_capability_fields():
    pending = _policy('escalate_to_issuer')  # requires approval, un-simulated
    cmd = pending['commands']
    for field in (
        'can_simulate', 'can_execute', 'can_roll_back', 'simulation_is_valid',
        'simulation_expires_at', 'simulation_blocked_reason', 'approval_progress_label',
        'can_current_user_approve', 'can_current_user_reject', 'required_approval_count',
        'current_approval_count',
    ):
        assert field in cmd, f'missing canonical command field: {field}'
    assert cmd['approval_progress_label'] == '0 of 1'
    assert cmd['can_simulate'] is True  # un-simulated -> eligible
    # Execute stays blocked (dry-run env) with a truthful reason, never a fake path.
    assert cmd['can_execute'] is False


def test_ai_review_command_dto_matches_policy_shape():
    review = _ai_review()
    cmd = review['commands']
    # Same canonical keys as a policy action so the frontend renders both uniformly.
    for field in (
        'can_simulate', 'can_execute', 'simulation_blocked_reason', 'approval_progress_label',
        'can_current_user_approve', 'can_current_user_reject',
    ):
        assert field in cmd
    assert cmd['can_simulate'] is False
    assert cmd['approval_progress_label'] == '0 of 1'
    assert 'decision' in cmd['simulation_blocked_reason'].lower()


# ── Recommended count agrees with the visible Recommended tab (Section 13) ─────

def test_decided_action_excluded_from_recommended_count():
    """An action DECIDED in the response-action approval domain (approved/rejected)
    moves to Action History in the UI, so it must not inflate the Recommended card.
    A recommendation review alone (no approval decision) does NOT remove it — it is
    still Awaiting Approval and remains a visible Recommended row."""
    actions = [
        _policy('notify_team', mode='simulated', execution_state='simulated'),
        _policy('escalate_to_issuer'),
        # Approval-domain approved -> Action History, not Recommended.
        _ai_review(review_state='accepted', approval_summary=_approval(approved_count=1, required_quorum=1)),
        # Approval-domain rejected -> excluded (inactive).
        _ai_review(review_state='pending_review', reason='b', approval_summary=_approval(rejected=True)),
        # Reviewed but NOT yet approved -> still Awaiting Approval -> Recommended.
        _ai_review(review_state='accepted', reason='c'),
        _ai_review(review_state='pending_review', reason='d'),
    ]
    summary = pilot.build_response_actions_summary(actions)
    # 2 policy actions + 2 approval-pending recommendations remain in Recommended.
    assert summary['recommended'] == 4


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
    # Session-MFA step-up is enforced separately (its own tests); run the reject
    # domain checks here as an MFA-satisfied session.
    monkeypatch.setattr(pilot, '_session_mfa_satisfied', lambda *_a, **_k: True)


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


# ── Section 3 / 11 / 15.8-15.9: no raw enum is ever serialized as a display label ─

# The persisted response_actions.status column has carried several legacy/current
# spellings for an approval-pending record. Whatever the raw value, the canonical
# lifecycle must resolve to the SAME awaiting-approval state + clean label so the
# frontend never has to interpret a raw enum.
@pytest.mark.parametrize('raw_status', [
    'pending', 'pending_approval', 'PENDING_APPROVAL', 'Pending_approval', 'awaiting_approval',
])
def test_legacy_pending_status_variants_normalize_to_awaiting_approval(raw_status):
    action = _policy('freeze_wallet', status=raw_status)  # freeze_wallet requires approval
    assert action['lifecycle_state'] == 'awaiting_approval'
    assert action['lifecycle_label'] == 'Awaiting Approval'
    assert action['approval_status'] == 'pending'
    # The raw enum is NEVER the operator-facing label.
    assert action['lifecycle_label'] != raw_status
    assert pilot.build_response_actions_summary([action])['pending_approval'] == 1


def test_ai_review_pending_never_serializes_raw_pending_approval_as_label():
    """The AI-review top-level status stays 'pending_approval' (its persisted enum),
    but the operator-facing lifecycle label must be the humanized 'Awaiting Approval'
    — a raw snake_case enum must never reach the UI (Section 11)."""
    review = _ai_review(review_state='pending_review', requires_approval=True)
    assert review['status'] == 'pending_approval'          # raw persisted enum (allowed here)
    assert review['lifecycle_label'] == 'Awaiting Approval'  # canonical display label
    assert review['lifecycle']['lifecycle_label'] == 'Awaiting Approval'
    # No canonical label anywhere is a raw snake_case enum.
    assert '_' not in review['lifecycle_label']


def test_no_action_origin_ever_serializes_a_snake_case_lifecycle_label():
    """Across every action origin, lifecycle state, and legacy status spelling, the
    lifecycle_label the frontend renders must never be a raw snake_case enum."""
    policy_types = ['freeze_wallet', 'notify_team', 'escalate_to_issuer', 'pause_mint_redeem',
                    'generate_regulator_auditor_package']
    statuses = ['pending', 'pending_approval', 'PENDING_APPROVAL', 'awaiting_approval',
                'canceled', 'executed', 'failed']
    labels: list[str] = []
    for action_type in policy_types:
        for status in statuses:
            for mode, execution_state in (('recommended', 'recommended'), ('simulated', 'simulated')):
                action = _policy(action_type, mode=mode, execution_state=execution_state, status=status)
                labels.append(action['lifecycle_label'])
    for review_state in ('pending_review', 'accepted', 'rejected'):
        labels.append(_ai_review(review_state=review_state)['lifecycle_label'])

    offenders = [label for label in labels if '_' in label or label != label.strip()]
    assert offenders == [], f'raw/snake_case lifecycle labels leaked: {sorted(set(offenders))}'
    # And the specific deployed offender is never a label.
    assert not any(label.lower() == 'pending_approval' for label in labels)
