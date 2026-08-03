"""Canonical response-action presentation / lifecycle / provenance / commands.

These are pure derivations over persisted facts (no DB, no randomness). They are
the single source of truth Screen 7 and Screen 8 consume, so they must:
- never surface a raw snake_case action_key as the operator title
- derive ONE primary lifecycle state (never concatenated enums)
- report truthful provenance (never 'none' as the primary label)
- expose allowed_commands / blocked_reasons so button visibility is not authority
"""
from __future__ import annotations

from services.api.app import pilot


# ── display title / description ──────────────────────────────────────────────

def test_raw_action_keys_never_used_as_title():
    for action_type in pilot.RESPONSE_ACTION_DEFINITIONS:
        pres = pilot.response_action_presentation(action_type)
        assert pres['display_title']
        # The human title is never the raw snake_case key.
        assert pres['display_title'] != action_type
        assert '_' not in pres['display_title']


def test_known_titles_match_domain_language():
    assert pilot.response_action_presentation('notify_team')['display_title'] == 'Notify Security Team'
    assert pilot.response_action_presentation('increase_monitoring')['display_title'] == 'Increase Monitoring Sensitivity'
    assert pilot.response_action_presentation('preserve_evidence')['display_title'] == 'Preserve Incident Evidence'
    assert pilot.response_action_presentation('isolate_provider')['display_title'] == 'Isolate Degraded Provider'


def test_unknown_action_type_gets_readable_title_not_raw_key():
    pres = pilot.response_action_presentation('some_new_ai_action')
    assert pres['display_title'] == 'Some New Ai Action'
    assert pres['action_key'] == 'some_new_ai_action'


# ── lifecycle: one primary state, separate sub-statuses ──────────────────────

def _action(**over):
    base = {'status': 'pending', 'mode': 'recommended', 'execution_state': 'recommended',
            'action_type': 'notify_team'}
    base.update(over)
    return base


def test_fresh_recommendation_is_recommended_not_simulated():
    lc = pilot.response_action_lifecycle(_action())
    assert lc['lifecycle_state'] == 'recommended'
    assert lc['simulation_status'] == 'not_started'


def test_simulated_low_risk_action_reads_simulation_passed():
    # notify_team requires no approval, so a simulated one is "Simulation Passed".
    lc = pilot.response_action_lifecycle(_action(mode='simulated', execution_state='simulated'))
    assert lc['lifecycle_state'] == 'simulation_passed'
    assert lc['simulation_status'] == 'passed'
    assert lc['approval_status'] == 'not_required'


def test_awaiting_approval_takes_precedence_over_passed_simulation():
    # A destructive action that is simulated but NOT approved must still read
    # "Awaiting Approval" (the next operator step), with simulation shown
    # separately — never concatenated.
    lc = pilot.response_action_lifecycle(
        _action(action_type='freeze_wallet', mode='simulated', execution_state='simulated'))
    assert lc['lifecycle_state'] == 'awaiting_approval'
    assert lc['approval_status'] == 'pending'
    assert lc['simulation_status'] == 'passed'  # separately available


def test_approved_but_not_executed_is_ready_to_execute():
    lc = pilot.response_action_lifecycle(
        _action(action_type='freeze_wallet', mode='simulated', execution_state='simulated',
                approved_at='2026-01-01T00:00:00Z'))
    assert lc['lifecycle_state'] == 'ready_to_execute'
    assert lc['approval_status'] == 'approved'


def test_executed_action_reports_executed_and_rollback_availability():
    lc = pilot.response_action_lifecycle(
        _action(action_type='freeze_wallet', status='executed', mode='live',
                execution_state='confirmed', approved_at='2026-01-01T00:00:00Z'))
    assert lc['lifecycle_state'] == 'executed'
    assert lc['execution_status'] == 'executed'
    # freeze_wallet is reversible → rollback becomes available once executed.
    assert lc['rollback_status'] == 'available'


def test_failed_execution_reads_execution_failed():
    lc = pilot.response_action_lifecycle(_action(status='failed', execution_state='failed'))
    assert lc['lifecycle_state'] == 'execution_failed'
    assert lc['execution_status'] == 'failed'


# ── provenance: truthful, never 'none' as the primary label ──────────────────

def test_incident_only_provenance_is_truthful():
    prov = pilot.response_action_provenance({
        'incident_id': 'inc-1',
        'execution_metadata': {'evidence_source': 'incident_context',
                               'chain_linked_ids': {'incident_id': 'inc-1'}},
    })
    assert prov['primary_source_label'] == 'Incident context only'
    assert prov['has_evidence_package'] is False
    assert any(r['source_type'] == 'incident' for r in prov['records'])


def test_missing_source_is_source_unavailable_not_none():
    prov = pilot.response_action_provenance({'execution_metadata': {}})
    assert prov['primary_source_label'] == 'Source unavailable'
    assert prov['primary_source_label'].lower() != 'none'
    assert prov['records'] == []


def test_evidence_package_provenance_is_reported():
    prov = pilot.response_action_provenance({
        'incident_id': 'inc-1',
        'execution_metadata': {'evidence_package_id': 'pkg-9',
                               'chain_linked_ids': {'incident_id': 'inc-1'}},
    })
    assert prov['has_evidence_package'] is True
    assert prov['primary_source_label'] == 'Evidence Package'
    assert prov['evidence_package_id'] == 'pkg-9'


def test_alert_and_detection_provenance_ranks_above_incident_only():
    prov = pilot.response_action_provenance({
        'incident_id': 'inc-1', 'alert_id': 'al-1',
        'execution_metadata': {'chain_linked_ids': {'incident_id': 'inc-1', 'alert_id': 'al-1',
                                                    'detection_id': 'det-1'}},
    })
    assert prov['primary_source_label'] in {'Threat Detection', 'Alert Evidence'}
    types = {r['source_type'] for r in prov['records']}
    assert {'incident', 'alert', 'detection'} <= types


# ── commands: backend is the authority on eligibility ────────────────────────

def test_execute_blocked_with_specific_reason_when_not_live_configured(monkeypatch):
    monkeypatch.delenv('LIVE_ACTION_EXECUTION_ENABLED', raising=False)
    cmds = pilot.response_action_command_eligibility(
        _action(action_type='notify_team', mode='simulated', execution_state='simulated'))
    assert 'execute' not in cmds['allowed_commands']
    blocked = {b['command']: b['reason'] for b in cmds['blocked_reasons']}
    assert 'execute' in blocked
    assert blocked['execute']  # a specific, non-empty reason
    assert 'dry-run' in blocked['execute'].lower() or 'not configured' in blocked['execute'].lower()


def test_approve_and_reject_offered_only_when_approval_pending():
    pending = pilot.response_action_command_eligibility(_action(action_type='freeze_wallet'))
    assert 'approve' in pending['allowed_commands']
    assert 'reject' in pending['allowed_commands']

    no_approval = pilot.response_action_command_eligibility(_action(action_type='notify_team'))
    assert 'approve' not in no_approval['allowed_commands']


def test_simulate_offered_for_unsimulated_and_next_step_is_simulate():
    cmds = pilot.response_action_command_eligibility(_action(action_type='notify_team'))
    assert 'simulate' in cmds['allowed_commands']
    assert cmds['next_required_step'] in {'simulate', 'approve'}


def test_terminal_action_offers_no_execute_or_approve():
    cmds = pilot.response_action_command_eligibility(
        _action(status='executed', execution_state='confirmed'))
    assert 'execute' not in cmds['allowed_commands']
    assert 'approve' not in cmds['allowed_commands']
    assert all(b['command'] != 'execute' for b in cmds['blocked_reasons'])


def test_destructive_action_requires_confirmation():
    cmds = pilot.response_action_command_eligibility(_action(action_type='freeze_wallet'))
    assert cmds['requires_confirmation'] is True
    cmds2 = pilot.response_action_command_eligibility(_action(action_type='notify_team'))
    assert cmds2['requires_confirmation'] is False


# ── payload enrichment wires it all together ─────────────────────────────────

def test_payload_carries_canonical_presentation_fields():
    payload = pilot._response_action_payload({
        'id': 'ra-1', 'action_type': 'notify_team', 'mode': 'recommended',
        'status': 'pending', 'execution_state': 'recommended', 'incident_id': 'inc-1',
        'execution_metadata': {'evidence_source': 'incident_context',
                               'chain_linked_ids': {'incident_id': 'inc-1'}},
    })
    assert payload['display_title'] == 'Notify Security Team'
    assert payload['lifecycle_state'] == 'recommended'
    assert payload['lifecycle_label'] == 'Recommended'
    assert payload['provenance']['primary_source_label'] == 'Incident context only'
    assert 'allowed_commands' in payload['commands']
    # The raw key is still preserved for persistence/commands.
    assert payload['action_type'] == 'notify_team'
