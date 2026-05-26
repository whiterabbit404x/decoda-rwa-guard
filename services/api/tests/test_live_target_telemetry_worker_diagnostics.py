"""Source-level diagnostics tests for the live target telemetry worker.

These confirm structural invariants in monitoring_runner.py and
run_monitoring_worker.py that prove the live RPC polling path is wired:

- monitoring_runner.production_claim_validator uses effective_evm_rpc_url
- monitoring_runner imports the effective_* helpers
- monitoring_runner per-cycle repair handles chain_id=1 (not just chain_network)
- monitoring_runner emits skipped_target_reason for diagnosis
- run_monitoring_worker logs env_resolution at startup
- run_monitoring_worker exits early when effective_worker_enabled is false
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / 'services' / 'api' / 'app' / 'monitoring_runner.py'
WORKER_PATH = REPO_ROOT / 'services' / 'api' / 'app' / 'run_monitoring_worker.py'


@pytest.fixture(scope='module')
def runner_src() -> str:
    return RUNNER_PATH.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def worker_src() -> str:
    return WORKER_PATH.read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# monitoring_runner.py
# ---------------------------------------------------------------------------

def test_monitoring_runner_imports_effective_helpers(runner_src: str):
    for name in ('effective_evm_rpc_url', 'effective_evm_chain_id', 'effective_worker_enabled'):
        assert name in runner_src, (
            f'monitoring_runner must import {name} from activity_providers '
            f'(prefer STAGING_* env vars).'
        )


def test_production_claim_validator_uses_effective_rpc_url(runner_src: str):
    """The validator must not read EVM_RPC_URL directly bypassing STAGING_* override."""
    # The code under production_claim_validator is around effective_evm_rpc_url
    assert 'effective_evm_rpc_url()' in runner_src, (
        'production_claim_validator must call effective_evm_rpc_url() '
        'so STAGING_EVM_RPC_URL takes precedence in staging.'
    )
    # Ensure the bare os.getenv('EVM_RPC_URL') JsonRpcClient call was replaced.
    assert "JsonRpcClient((os.getenv('EVM_RPC_URL') or '').strip())" not in runner_src, (
        'production_claim_validator must not construct JsonRpcClient directly from '
        'EVM_RPC_URL; use effective_evm_rpc_url() instead.'
    )


def test_repair_update_covers_chain_id_one(runner_src: str):
    """The in-cycle repair UPDATE must repair targets that only persist chain_id=1."""
    # Find the repair UPDATE block
    assert 'COALESCE(t.chain_id, 0) = 1' in runner_src, (
        "In-cycle monitoring_configs repair must include 'COALESCE(t.chain_id, 0) = 1' "
        'so EVM mainnet targets created via the direct UI are repaired.'
    )


def test_repair_update_handles_empty_provider_type(runner_src: str):
    """The repair must consider empty provider_type strings in addition to 'default' / 'unknown'."""
    needle = "LOWER(COALESCE(mc.provider_type, '')) IN ('default', 'unknown', '')"
    assert needle in runner_src, (
        'In-cycle repair must include empty string '
        'in the provider_type filter so newly-created configs with empty values are repaired.'
    )


def test_skipped_target_reason_logged(runner_src: str):
    """Worker cycle must emit skipped_target_reason for diagnostic visibility."""
    assert 'skipped_target_reason' in runner_src, (
        'Worker cycle must emit skipped_target_reason logs for enabled-but-not-selected targets.'
    )


def test_candidate_targets_count_logged(runner_src: str):
    assert 'candidate_targets_count' in runner_src, (
        'Worker cycle must log candidate_targets_count for diagnostics.'
    )
    assert 'selected_live_targets_count' in runner_src, (
        'Worker cycle must log selected_live_targets_count for diagnostics.'
    )


# ---------------------------------------------------------------------------
# run_monitoring_worker.py
# ---------------------------------------------------------------------------

def test_worker_startup_logs_env_resolution(worker_src: str):
    assert 'env_resolution' in worker_src, (
        'Worker startup must log env_resolution to surface staging vs base env vars.'
    )
    assert 'evm_rpc_configured' in worker_src, (
        'Worker startup must log evm_rpc_configured=true/false.'
    )
    assert 'worker_enabled' in worker_src, (
        'Worker startup must log worker_enabled=true/false.'
    )
    assert 'provider_mode' in worker_src, (
        'Worker startup must log provider_mode=live|disabled.'
    )


def test_worker_startup_does_not_print_secret(worker_src: str):
    """Safety: must not print the actual RPC URL value."""
    for forbidden in (
        "os.getenv('STAGING_EVM_RPC_URL'),",
        "os.getenv('EVM_RPC_URL'),",
    ):
        # Bare unconditional logging of the env value would be a secret leak.
        # We allow `bool(...)` wrappers and `effective_*_url()` returns booleans
        # in the log statements.
        assert forbidden not in worker_src, (
            f'Worker startup must not log the raw env value via {forbidden!r}.'
        )


def test_worker_short_circuits_when_disabled(worker_src: str):
    assert 'effective_worker_enabled' in worker_src, (
        'Worker must consult effective_worker_enabled() before running cycles.'
    )
    assert 'disabled by env flag' in worker_src or 'STAGING_WORKER_ENABLED=true' in worker_src, (
        'Worker must log a clear remediation hint when disabled.'
    )
