"""
Response action executor interface and implementations.

Execution state machine (canonical states):
  simulated         - dry-run simulation completed; no on-chain effect
  proposal_created  - on-chain proposal created (Safe/multisig); awaiting multisig approval
  awaiting_approval - proposal submitted to multisig; waiting for required signers
  submitted         - transaction submitted to chain mempool
  confirmed         - transaction confirmed on-chain (tx_hash required)
  failed            - execution failed at any stage
  cancelled         - action cancelled before execution
  unsupported       - live execution not supported for this action/config

Legacy states (retained for backward compatibility):
  simulated_executed   - (legacy) equivalent to simulated
  recommended_approved - recommended mode approval recorded
  proposed             - (legacy) equivalent to proposal_created
  live_manual_required - live mode requires manual intervention

Production gating:
  LIVE_ACTION_EXECUTION_ENABLED must be 'true' to allow any live executor.
  RESPONSE_ACTION_EXECUTOR selects the executor ('simulation' default, 'safe' for Safe multisig).
"""
from __future__ import annotations

import abc
import functools
import time
import os
from typing import Any

from services.api.app.observability import increment, observe, report_error, span

LIVE_ACTION_EXECUTION_ENABLED_ENV = 'LIVE_ACTION_EXECUTION_ENABLED'
RESPONSE_ACTION_EXECUTOR_ENV = 'RESPONSE_ACTION_EXECUTOR'

CANONICAL_EXECUTION_STATES = frozenset({
    'simulated',
    'proposal_created',
    'awaiting_approval',
    'submitted',
    'confirmed',
    'failed',
    'cancelled',
    'unsupported',
})

LEGACY_EXECUTION_STATES = frozenset({
    'simulated_executed',
    'recommended_approved',
    'proposed',
    'live_manual_required',
})

VALID_EXECUTION_STATES = CANONICAL_EXECUTION_STATES | LEGACY_EXECUTION_STATES

TERMINAL_EXECUTION_STATES = frozenset({
    'simulated',
    'confirmed',
    'failed',
    'cancelled',
    'unsupported',
    'simulated_executed',
    'recommended_approved',
    'live_manual_required',
})

CONFIRMED_EXECUTION_STATES = frozenset({'submitted', 'confirmed'})

# Destructive action types that require explicit operator approval before execution
DESTRUCTIVE_ACTION_TYPES = frozenset({
    'freeze_wallet',
    'revoke_approval',
    'block_transaction',
    'pause_contract',
    'emergency_withdraw',
})


def is_live_execution_enabled() -> bool:
    """True only when LIVE_ACTION_EXECUTION_ENABLED=true."""
    return os.getenv(LIVE_ACTION_EXECUTION_ENABLED_ENV, '').strip().lower() in {'1', 'true', 'yes'}


def get_configured_executor_name() -> str:
    """Returns the configured executor name from environment."""
    return os.getenv(RESPONSE_ACTION_EXECUTOR_ENV, 'simulation').strip().lower()


class ExecutionResult:
    """Immutable result of an executor operation."""

    __slots__ = (
        'execution_state',
        'execution_mode',
        'result_summary',
        'metadata',
        'error_reason',
        'error_code',
        'result_code',
        'proposal_id',
        'tx_hash',
    )

    def __init__(
        self,
        *,
        execution_state: str,
        execution_mode: str,
        result_summary: str,
        metadata: dict[str, Any] | None = None,
        error_reason: str | None = None,
        error_code: str | None = None,
        result_code: int | None = None,
        proposal_id: str | None = None,
        tx_hash: str | None = None,
    ) -> None:
        if execution_state not in VALID_EXECUTION_STATES:
            raise ValueError(f'Invalid execution_state: {execution_state!r}. '
                             f'Must be one of: {sorted(VALID_EXECUTION_STATES)}')
        self.execution_state = execution_state
        self.execution_mode = execution_mode
        self.result_summary = result_summary
        self.metadata = metadata or {}
        self.error_reason = error_reason
        self.error_code = error_code
        self.result_code = result_code
        self.proposal_id = proposal_id
        self.tx_hash = tx_hash

    def is_error(self) -> bool:
        return self.execution_state in {'failed', 'unsupported', 'cancelled'}

    def requires_tx_hash_for_confirmation(self) -> bool:
        return self.execution_state in CONFIRMED_EXECUTION_STATES

    def as_dict(self) -> dict[str, Any]:
        return {
            'execution_state': self.execution_state,
            'execution_mode': self.execution_mode,
            'result_summary': self.result_summary,
            'error_reason': self.error_reason,
            'error_code': self.error_code,
            'result_code': self.result_code,
            'proposal_id': self.proposal_id,
            'tx_hash': self.tx_hash,
        }


class ResponseActionExecutor(abc.ABC):
    """
    Abstract base for response action executors.

    Implementations must be truthful about their capabilities:
    - SimulationExecutor: never claims live execution
    - SafeProposalExecutor: creates proposals only; never auto-submits destructive actions
    """

    @abc.abstractmethod
    def validate_action(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
    ) -> ExecutionResult | None:
        """Validate the action before execution. Returns error ExecutionResult or None if valid."""

    @abc.abstractmethod
    def simulate_action(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
    ) -> ExecutionResult:
        """
        Dry-run simulation. Must return execution_state='simulated'.
        Must never claim live execution or record a tx_hash.
        """

    @abc.abstractmethod
    def create_proposal(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
        workspace_context: dict[str, Any],
    ) -> ExecutionResult:
        """
        Create an on-chain proposal (Safe/multisig).
        Returns execution_state='proposal_created' on success.
        Returns execution_state='unsupported' if not capable.
        Must NOT auto-submit destructive actions.
        """

    @abc.abstractmethod
    def check_status(
        self,
        action: dict[str, Any],
    ) -> ExecutionResult:
        """Check current execution status for an in-progress action."""

    def is_live_capable(self) -> bool:
        """Returns True if this executor can submit live on-chain transactions."""
        return False

    def requires_approval_for(self, action_type: str) -> bool:
        """Returns True if the action type requires explicit operator approval."""
        return action_type in DESTRUCTIVE_ACTION_TYPES


class SimulationExecutor(ResponseActionExecutor):
    """
    Default executor. Always simulates; never submits on-chain transactions.

    - execution_state is always 'simulated'
    - execution_mode is always 'simulation'
    - Never records a tx_hash or proposal_id
    """

    def validate_action(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
    ) -> ExecutionResult | None:
        return None

    def simulate_action(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
    ) -> ExecutionResult:
        return ExecutionResult(
            execution_state='simulated',
            execution_mode='simulation',
            result_summary='Action simulated. No on-chain transaction was submitted.',
        )

    def create_proposal(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
        workspace_context: dict[str, Any],
    ) -> ExecutionResult:
        return ExecutionResult(
            execution_state='unsupported',
            execution_mode='simulation',
            result_summary='Live proposals are not available. Executor is in simulation mode.',
            error_reason='SimulationExecutor does not create on-chain proposals. '
                         'Set LIVE_ACTION_EXECUTION_ENABLED=true and configure a live executor.',
            error_code='SIMULATION_EXECUTOR_NO_PROPOSAL',
            result_code=409,
        )

    def check_status(
        self,
        action: dict[str, Any],
    ) -> ExecutionResult:
        return ExecutionResult(
            execution_state='simulated',
            execution_mode='simulation',
            result_summary='Simulation completed. No on-chain state to query.',
        )


class SafeProposalExecutor(ResponseActionExecutor):
    """
    Proposes transactions to a Gnosis Safe multisig wallet.

    Requirements:
    - LIVE_ACTION_EXECUTION_ENABLED=true
    - Workspace must have Safe address configured
    - Capability live_execution_path must be 'safe' or 'governance'

    This executor does NOT:
    - Hold or use raw private keys
    - Submit transactions automatically without approval
    - Auto-execute destructive actions

    Sets execution_state='proposal_created' on success.
    Stores safe_tx_hash as proposal_id for tracking.
    """

    def is_live_capable(self) -> bool:
        return True

    def validate_action(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
    ) -> ExecutionResult | None:
        if not is_live_execution_enabled():
            return ExecutionResult(
                execution_state='unsupported',
                execution_mode='safe_proposal',
                result_summary=(
                    'Live action execution is disabled. '
                    'Set LIVE_ACTION_EXECUTION_ENABLED=true to enable.'
                ),
                error_reason='Live action execution is disabled by configuration.',
                error_code='LIVE_EXECUTION_DISABLED',
                result_code=409,
            )
        live_path = capability.get('live_execution_path')
        if live_path not in ('safe', 'governance'):
            return ExecutionResult(
                execution_state='unsupported',
                execution_mode='safe_proposal',
                result_summary=f'Action is not configured for Safe proposal execution (path={live_path!r}).',
                error_reason='Unsupported live execution path for Safe executor.',
                error_code='RESPONSE_ACTION_UNSUPPORTED_CAPABILITY',
                result_code=409,
            )
        return None

    def simulate_action(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
    ) -> ExecutionResult:
        return ExecutionResult(
            execution_state='simulated',
            execution_mode='safe_proposal_simulated',
            result_summary='Safe proposal simulation completed. No transaction submitted.',
        )

    def create_proposal(
        self,
        action: dict[str, Any],
        capability: dict[str, Any],
        workspace_context: dict[str, Any],
    ) -> ExecutionResult:
        validation_error = self.validate_action(action, capability)
        if validation_error:
            return validation_error
        # Actual Safe API call is delegated to the caller (pilot.py) which has
        # access to _propose_safe_transaction(). This method signals readiness.
        return ExecutionResult(
            execution_state='proposal_created',
            execution_mode='safe_proposal',
            result_summary='Safe transaction proposal created. Awaiting multisig approval.',
        )

    def check_status(
        self,
        action: dict[str, Any],
    ) -> ExecutionResult:
        proposal_id = str(action.get('safe_tx_hash') or action.get('proposal_id') or '').strip()
        if not proposal_id:
            return ExecutionResult(
                execution_state='unsupported',
                execution_mode='safe_proposal',
                result_summary='No proposal ID available for status check.',
                error_reason='Missing proposal_id or safe_tx_hash in action record.',
                error_code='MISSING_PROPOSAL_ID',
            )
        return ExecutionResult(
            execution_state='awaiting_approval',
            execution_mode='safe_proposal',
            result_summary=f'Proposal {proposal_id[:20]}... is awaiting multisig approval.',
            proposal_id=proposal_id,
        )


def get_executor(executor_name: str | None = None) -> ResponseActionExecutor:
    """
    Factory function that returns the configured executor.

    Always returns SimulationExecutor when LIVE_ACTION_EXECUTION_ENABLED is false.
    When live execution is enabled:
      - 'safe' / 'safe_proposal' / 'gnosis_safe' → SafeProposalExecutor
      - anything else → SimulationExecutor

    This ensures no live execution occurs without explicit configuration.
    """
    if not is_live_execution_enabled():
        executor: ResponseActionExecutor = SimulationExecutor()
    else:
        name = (executor_name or get_configured_executor_name()).lower().strip()
        executor = SafeProposalExecutor() if name in ('safe', 'safe_proposal', 'gnosis_safe') else SimulationExecutor()
    for method_name in ('validate_action', 'simulate_action', 'create_proposal', 'check_status'):
        original = getattr(executor, method_name)
        @functools.wraps(original)
        def instrumented(*args: Any, __method=method_name, __original=original, **kwargs: Any):
            started = time.perf_counter()
            try:
                with span(f'response_action.{__method}', executor=type(executor).__name__):
                    result = __original(*args, **kwargs)
                outcome = getattr(result, 'execution_state', 'valid') if result is not None else 'valid'
                increment('decoda_response_action_outcomes_total', executor=type(executor).__name__, operation=__method, outcome=outcome)
                return result
            except Exception as exc:
                increment('decoda_response_action_outcomes_total', executor=type(executor).__name__, operation=__method, outcome='error')
                report_error(exc, operation=f'response_action.{__method}', executor=type(executor).__name__)
                raise
            finally:
                observe('decoda_response_action_duration_seconds', time.perf_counter() - started, executor=type(executor).__name__, operation=__method)
        setattr(executor, method_name, instrumented)
    return executor
