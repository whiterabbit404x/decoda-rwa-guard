"""Env resolution tests for the live target telemetry path.

These cover the helpers introduced for blocker 3: STAGING_* env vars must take
precedence over their base counterparts in:
    - effective_evm_rpc_url
    - effective_evm_chain_id
    - effective_worker_enabled

Plus a few fail-closed worker-startup behaviors:
    - Missing both RPC URLs => monitoring_ingestion_runtime degraded
    - effective_worker_enabled defaults True when neither env var set
    - effective_worker_enabled returns False when STAGING_WORKER_ENABLED=false
"""
from __future__ import annotations

import pytest

from services.api.app.activity_providers import (
    effective_evm_chain_id,
    effective_evm_rpc_url,
    effective_worker_enabled,
    monitoring_ingestion_runtime,
)

_ENV_VARS = [
    'EVM_RPC_URL',
    'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID',
    'STAGING_EVM_CHAIN_ID',
    'CHAIN_ID',
    'WORKER_ENABLED',
    'STAGING_WORKER_ENABLED',
    'LIVE_MONITORING_ENABLED',
    'LIVE_MONITORING_CHAINS',
    'EVM_WS_URL',
]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# effective_evm_rpc_url
# ---------------------------------------------------------------------------

def test_effective_evm_rpc_url_prefers_staging(clean_env, monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com')
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.example.com')
    assert effective_evm_rpc_url() == 'https://staging.example.com'


def test_effective_evm_rpc_url_falls_back_to_base(clean_env, monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com')
    assert effective_evm_rpc_url() == 'https://base.example.com'


def test_effective_evm_rpc_url_empty_when_unset(clean_env):
    assert effective_evm_rpc_url() == ''


def test_effective_evm_rpc_url_skips_empty_staging(clean_env, monkeypatch):
    monkeypatch.setenv('STAGING_EVM_RPC_URL', '')
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com')
    assert effective_evm_rpc_url() == 'https://base.example.com'


# ---------------------------------------------------------------------------
# effective_evm_chain_id
# ---------------------------------------------------------------------------

def test_effective_evm_chain_id_prefers_staging(clean_env, monkeypatch):
    monkeypatch.setenv('EVM_CHAIN_ID', '5')
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    assert effective_evm_chain_id() == '1'


def test_effective_evm_chain_id_falls_back_to_base(clean_env, monkeypatch):
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    assert effective_evm_chain_id() == '1'


def test_effective_evm_chain_id_falls_back_to_chain_id(clean_env, monkeypatch):
    monkeypatch.setenv('CHAIN_ID', '1')
    assert effective_evm_chain_id() == '1'


def test_effective_evm_chain_id_empty_when_unset(clean_env):
    assert effective_evm_chain_id() == ''


# ---------------------------------------------------------------------------
# effective_worker_enabled
# ---------------------------------------------------------------------------

def test_effective_worker_enabled_default_true_when_unset(clean_env):
    assert effective_worker_enabled() is True


def test_effective_worker_enabled_respects_staging_false(clean_env, monkeypatch):
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'false')
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    assert effective_worker_enabled() is False


def test_effective_worker_enabled_respects_staging_true(clean_env, monkeypatch):
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    assert effective_worker_enabled() is True


def test_effective_worker_enabled_falls_back_to_base(clean_env, monkeypatch):
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    assert effective_worker_enabled() is True


def test_effective_worker_enabled_base_false_when_staging_unset(clean_env, monkeypatch):
    monkeypatch.setenv('WORKER_ENABLED', 'false')
    assert effective_worker_enabled() is False


# ---------------------------------------------------------------------------
# monitoring_ingestion_runtime — staging override flows through
# ---------------------------------------------------------------------------

def test_monitoring_ingestion_runtime_uses_staging_rpc(clean_env, monkeypatch):
    """If only STAGING_EVM_RPC_URL is set, runtime must not report 'EVM_RPC_URL missing'."""
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.example.com')
    rt = monitoring_ingestion_runtime()
    assert rt['degraded'] is False
    assert rt['reason'] is None


def test_monitoring_ingestion_runtime_degraded_when_no_rpc(clean_env):
    rt = monitoring_ingestion_runtime()
    assert rt['degraded'] is True
    assert rt['reason'] == 'EVM_RPC_URL missing'


def test_monitoring_ingestion_runtime_degraded_when_live_disabled(clean_env, monkeypatch):
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'false')
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.example.com')
    rt = monitoring_ingestion_runtime()
    assert rt['degraded'] is True
    assert rt['reason'] == 'LIVE_MONITORING_ENABLED=false'


# ---------------------------------------------------------------------------
# fetch_evm_activity must read STAGING_EVM_RPC_URL too
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_uses_staging_rpc_url(clean_env, monkeypatch):
    """When STAGING_EVM_RPC_URL is set but EVM_RPC_URL is empty, the EVM
    provider must still attempt the RPC call (it would early-return [] only
    when no URL is configured at all)."""
    from services.api.app.evm_activity_provider import fetch_evm_activity

    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.example.com')

    target = {
        'id': 'tgt-1',
        'chain_network': 'ethereum',
        'wallet_address': '0xabcdefabcdefabcdefabcdefabcdefabcdefabcd',
        'target_type': 'wallet',
    }

    class _RpcStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def call(self, method: str, params):
            self.calls.append((method, params))
            if method == 'eth_blockNumber':
                return '0x12c4cca'
            return None

    stub = _RpcStub()
    fetch_evm_activity(target, since_ts=None, rpc_client=stub)
    methods_called = [m for m, _ in stub.calls]
    assert 'eth_blockNumber' in methods_called, (
        'fetch_evm_activity must use the configured RPC client when '
        'STAGING_EVM_RPC_URL is set (got calls: %r)' % methods_called
    )
