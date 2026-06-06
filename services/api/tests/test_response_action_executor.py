"""
Tests for response_action_executor — P0-1: Truthful execution state machine.

Verifies:
- SimulationExecutor never reports live execution
- SafeProposalExecutor returns unsupported when LIVE_ACTION_EXECUTION_ENABLED is false
- SafeProposalExecutor validates capability path
- ExecutionResult rejects invalid states
- get_executor() returns SimulationExecutor when live execution disabled
- Destructive actions are correctly identified
- Execution states are correctly labeled as terminal vs non-terminal
"""
from __future__ import annotations

import os
import pytest

from services.api.app.response_action_executor import (
    CANONICAL_EXECUTION_STATES,
    LEGACY_EXECUTION_STATES,
    TERMINAL_EXECUTION_STATES,
    VALID_EXECUTION_STATES,
    ExecutionResult,
    ResponseActionExecutor,
    SafeProposalExecutor,
    SimulationExecutor,
    get_executor,
    is_live_execution_enabled,
    DESTRUCTIVE_ACTION_TYPES,
    LIVE_ACTION_EXECUTION_ENABLED_ENV,
    RESPONSE_ACTION_EXECUTOR_ENV,
)


# ---------------------------------------------------------------------------
# ExecutionResult validation
# ---------------------------------------------------------------------------

def test_execution_result_rejects_invalid_state():
    with pytest.raises(ValueError, match='Invalid execution_state'):
        ExecutionResult(
            execution_state='completely_invalid_state',
            execution_mode='test',
            result_summary='test',
        )


def test_execution_result_accepts_all_valid_states():
    for state in VALID_EXECUTION_STATES:
        result = ExecutionResult(
            execution_state=state,
            execution_mode='test',
            result_summary='test',
        )
        assert result.execution_state == state


def test_execution_result_is_error_for_error_states():
    for state in ('failed', 'unsupported', 'cancelled'):
        result = ExecutionResult(execution_state=state, execution_mode='t', result_summary='t')
        assert result.is_error()


def test_execution_result_not_error_for_success_states():
    for state in ('simulated', 'proposal_created', 'confirmed'):
        result = ExecutionResult(execution_state=state, execution_mode='t', result_summary='t')
        assert not result.is_error()


def test_execution_result_as_dict_contains_required_keys():
    result = ExecutionResult(
        execution_state='simulated',
        execution_mode='simulation',
        result_summary='Test simulation.',
        error_code='TEST_CODE',
        proposal_id='0xabc',
    )
    d = result.as_dict()
    assert d['execution_state'] == 'simulated'
    assert d['execution_mode'] == 'simulation'
    assert d['error_code'] == 'TEST_CODE'
    assert d['proposal_id'] == '0xabc'


# ---------------------------------------------------------------------------
# SimulationExecutor
# ---------------------------------------------------------------------------

def test_simulation_executor_simulate_returns_simulated_state():
    executor = SimulationExecutor()
    result = executor.simulate_action({}, {})
    assert result.execution_state == 'simulated'
    assert result.execution_mode == 'simulation'
    assert result.tx_hash is None
    assert result.proposal_id is None


def test_simulation_executor_never_claims_live_execution():
    executor = SimulationExecutor()
    result = executor.simulate_action({'action_type': 'freeze_wallet'}, {'live_execution_path': 'safe'})
    assert result.execution_state == 'simulated'
    assert result.tx_hash is None
    assert 'live' not in result.result_summary.lower()
    assert 'on-chain transaction' in result.result_summary.lower() or 'no on-chain' in result.result_summary.lower()


def test_simulation_executor_create_proposal_returns_unsupported():
    executor = SimulationExecutor()
    result = executor.create_proposal({}, {}, {})
    assert result.execution_state == 'unsupported'
    assert result.error_code == 'SIMULATION_EXECUTOR_NO_PROPOSAL'
    assert result.result_code == 409


def test_simulation_executor_is_not_live_capable():
    executor = SimulationExecutor()
    assert not executor.is_live_capable()


def test_simulation_executor_validate_returns_none():
    executor = SimulationExecutor()
    result = executor.validate_action({'action_type': 'freeze_wallet'}, {'live_execution_path': 'safe'})
    assert result is None


def test_simulation_executor_check_status_returns_simulated():
    executor = SimulationExecutor()
    result = executor.check_status({'execution_state': 'simulated'})
    assert result.execution_state == 'simulated'


# ---------------------------------------------------------------------------
# SafeProposalExecutor — gating
# ---------------------------------------------------------------------------

def test_safe_executor_returns_unsupported_when_live_execution_disabled(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'false')
    executor = SafeProposalExecutor()
    result = executor.validate_action({}, {'live_execution_path': 'safe'})
    assert result is not None
    assert result.execution_state == 'unsupported'
    assert result.error_code == 'LIVE_EXECUTION_DISABLED'
    assert result.result_code == 409


def test_safe_executor_returns_unsupported_for_wrong_path(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    executor = SafeProposalExecutor()
    result = executor.validate_action({}, {'live_execution_path': 'manual_only'})
    assert result is not None
    assert result.execution_state == 'unsupported'
    assert result.error_code == 'RESPONSE_ACTION_UNSUPPORTED_CAPABILITY'


def test_safe_executor_validate_passes_for_safe_path(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    executor = SafeProposalExecutor()
    result = executor.validate_action({}, {'live_execution_path': 'safe'})
    assert result is None


def test_safe_executor_validate_passes_for_governance_path(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    executor = SafeProposalExecutor()
    result = executor.validate_action({}, {'live_execution_path': 'governance'})
    assert result is None


def test_safe_executor_create_proposal_returns_proposal_created(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    executor = SafeProposalExecutor()
    result = executor.create_proposal({}, {'live_execution_path': 'safe'}, {})
    assert result.execution_state == 'proposal_created'
    assert result.execution_mode == 'safe_proposal'


def test_safe_executor_is_live_capable():
    executor = SafeProposalExecutor()
    assert executor.is_live_capable()


def test_safe_executor_simulate_returns_simulated(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    executor = SafeProposalExecutor()
    result = executor.simulate_action({}, {})
    assert result.execution_state == 'simulated'
    assert result.tx_hash is None


def test_safe_executor_check_status_returns_awaiting_approval_with_proposal_id(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    executor = SafeProposalExecutor()
    result = executor.check_status({'safe_tx_hash': '0xabc123'})
    assert result.execution_state == 'awaiting_approval'
    assert result.proposal_id == '0xabc123'


def test_safe_executor_check_status_missing_proposal_id_returns_unsupported(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    executor = SafeProposalExecutor()
    result = executor.check_status({})
    assert result.execution_state == 'unsupported'
    assert result.error_code == 'MISSING_PROPOSAL_ID'


# ---------------------------------------------------------------------------
# get_executor factory
# ---------------------------------------------------------------------------

def test_get_executor_returns_simulation_when_live_disabled(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'false')
    executor = get_executor()
    assert isinstance(executor, SimulationExecutor)


def test_get_executor_returns_simulation_by_default_even_if_live_enabled(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    monkeypatch.setenv(RESPONSE_ACTION_EXECUTOR_ENV, 'simulation')
    executor = get_executor()
    assert isinstance(executor, SimulationExecutor)


def test_get_executor_returns_safe_when_configured(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    monkeypatch.setenv(RESPONSE_ACTION_EXECUTOR_ENV, 'safe')
    executor = get_executor('safe')
    assert isinstance(executor, SafeProposalExecutor)


def test_get_executor_returns_simulation_for_unknown_executor(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    monkeypatch.setenv(RESPONSE_ACTION_EXECUTOR_ENV, 'unknown_provider')
    executor = get_executor()
    assert isinstance(executor, SimulationExecutor)


# ---------------------------------------------------------------------------
# Destructive action types
# ---------------------------------------------------------------------------

def test_freeze_wallet_is_destructive():
    executor = SimulationExecutor()
    assert executor.requires_approval_for('freeze_wallet')


def test_revoke_approval_is_destructive():
    executor = SimulationExecutor()
    assert executor.requires_approval_for('revoke_approval')


def test_notify_team_is_not_destructive():
    executor = SimulationExecutor()
    assert not executor.requires_approval_for('notify_team')


# ---------------------------------------------------------------------------
# State machine integrity
# ---------------------------------------------------------------------------

def test_canonical_states_are_subset_of_valid_states():
    assert CANONICAL_EXECUTION_STATES.issubset(VALID_EXECUTION_STATES)
    assert LEGACY_EXECUTION_STATES.issubset(VALID_EXECUTION_STATES)


def test_terminal_states_are_valid_states():
    assert TERMINAL_EXECUTION_STATES.issubset(VALID_EXECUTION_STATES)


def test_simulated_is_terminal():
    assert 'simulated' in TERMINAL_EXECUTION_STATES


def test_confirmed_is_terminal():
    assert 'confirmed' in TERMINAL_EXECUTION_STATES


def test_proposal_created_is_not_terminal():
    assert 'proposal_created' not in TERMINAL_EXECUTION_STATES


def test_awaiting_approval_is_not_terminal():
    assert 'awaiting_approval' not in TERMINAL_EXECUTION_STATES


# ---------------------------------------------------------------------------
# is_live_execution_enabled
# ---------------------------------------------------------------------------

def test_live_execution_disabled_by_default(monkeypatch):
    monkeypatch.delenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, raising=False)
    assert not is_live_execution_enabled()


def test_live_execution_enabled_with_true(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'true')
    assert is_live_execution_enabled()


def test_live_execution_enabled_with_1(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, '1')
    assert is_live_execution_enabled()


def test_live_execution_disabled_with_false(monkeypatch):
    monkeypatch.setenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, 'false')
    assert not is_live_execution_enabled()
