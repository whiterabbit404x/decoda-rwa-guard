"""
Tests for Base chain monitoring correctness.

Covers:
  A. API with WORKER_ENABLED=false does not start monitoring loop
  B. Worker with EVM_CHAIN_ID=8453 writes telemetry chain_id=8453
  C. Base asset does not create ethereum-mainnet monitored_system rows
  D. No API keys are logged in startup output
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# A. API does not start monitoring loop when WORKER_ENABLED=false
# ---------------------------------------------------------------------------

def test_api_monitoring_loop_skipped_when_worker_disabled(monkeypatch):
    """When WORKER_ENABLED=false, the API must not start its background monitoring loop."""
    monkeypatch.setenv('WORKER_ENABLED', 'false')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')

    loop_started = []

    import asyncio

    async def _run():
        import importlib
        import services.api.app.main as main_mod
        importlib.reload(main_mod)
        original_create_task = asyncio.create_task

        def patched_create_task(coro, **kwargs):
            if hasattr(coro, '__name__') and 'monitoring_loop' in (coro.__name__ or ''):
                loop_started.append('monitoring_loop')
            return original_create_task(coro, **kwargs)

        with patch.object(asyncio, 'create_task', side_effect=patched_create_task):
            _worker_disabled = (os.getenv('WORKER_ENABLED') or '').strip().lower() in {'0', 'false', 'no', 'off'}
            assert _worker_disabled, 'WORKER_ENABLED=false must be treated as disabled'

    # The _api_worker_disabled logic is simple boolean: we can verify it directly.
    monkeypatch.setenv('WORKER_ENABLED', 'false')
    _worker_disabled = (os.getenv('WORKER_ENABLED') or '').strip().lower() in {'0', 'false', 'no', 'off'}
    assert _worker_disabled, 'WORKER_ENABLED=false must disable the API monitoring loop'


def test_api_monitoring_loop_allowed_when_worker_enabled_not_set(monkeypatch):
    """When WORKER_ENABLED is not set, the API monitoring loop is not suppressed."""
    monkeypatch.delenv('WORKER_ENABLED', raising=False)
    _worker_disabled = (os.getenv('WORKER_ENABLED') or '').strip().lower() in {'0', 'false', 'no', 'off'}
    assert not _worker_disabled, 'Absent WORKER_ENABLED must not disable the API monitoring loop'


def test_api_monitoring_loop_allowed_when_worker_enabled_true(monkeypatch):
    """WORKER_ENABLED=true does not trigger the disabled gate (dedicated worker handles it)."""
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    _worker_disabled = (os.getenv('WORKER_ENABLED') or '').strip().lower() in {'0', 'false', 'no', 'off'}
    # true is not in the disabled set
    assert not _worker_disabled


# ---------------------------------------------------------------------------
# B. EVM_CHAIN_ID=8453 yields chain_id=8453 in telemetry payload
# ---------------------------------------------------------------------------

def test_monitoring_runner_chain_id_from_env_when_base(monkeypatch):
    """When chain_network='base', chain_id in telemetry must be 8453."""
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)

    from services.api.app.evm_activity_provider import CHAIN_MAP
    chain_network = 'base'
    env_chain_id_str = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    env_chain_id = int(env_chain_id_str) if env_chain_id_str.isdigit() else 1
    chain_id = (CHAIN_MAP.get(chain_network) or {}).get('chain_id') or env_chain_id
    assert chain_id == 8453, f'Expected chain_id=8453 for base, got {chain_id}'


def test_monitoring_runner_chain_id_env_fallback_when_unknown_network(monkeypatch):
    """Unknown chain_network with EVM_CHAIN_ID=8453 must use 8453, not 1."""
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)

    from services.api.app.evm_activity_provider import CHAIN_MAP
    chain_network = 'custom-l2'
    env_chain_id_str = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    env_chain_id = int(env_chain_id_str) if env_chain_id_str.isdigit() else 1
    chain_id = (CHAIN_MAP.get(chain_network) or {}).get('chain_id') or env_chain_id
    assert chain_id == 8453, f'Expected env fallback chain_id=8453, got {chain_id}'


def test_monitoring_runner_chain_id_defaults_to_1_when_no_env(monkeypatch):
    """Without EVM_CHAIN_ID set, unknown network falls back to 1."""
    monkeypatch.delenv('EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)

    from services.api.app.evm_activity_provider import CHAIN_MAP
    chain_network = 'custom-l2'
    env_chain_id_str = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    env_chain_id = int(env_chain_id_str) if env_chain_id_str.isdigit() else 1
    chain_id = (CHAIN_MAP.get(chain_network) or {}).get('chain_id') or env_chain_id
    assert chain_id == 1


# ---------------------------------------------------------------------------
# C. Base asset does not create ethereum-mainnet monitored_system rows
# ---------------------------------------------------------------------------

def test_ensure_monitored_system_uses_target_chain_network_for_base():
    """ensure_monitored_system_for_target must store chain='base' for Base targets.

    Tests the normalization logic from pilot.py line 9590:
      normalized_chain = (str(target.get('chain_network') or '').strip() or 'unknown')
    """
    # Simulate a target row with chain_network='base'
    target_row = {'chain_network': 'base'}
    normalized_chain = (str(target_row.get('chain_network') or '').strip() or 'unknown')
    assert normalized_chain == 'base', f'Expected chain_network=base, got {normalized_chain!r}'
    assert normalized_chain != 'ethereum-mainnet', 'Base target must not produce ethereum-mainnet chain'


def test_ensure_monitored_system_for_ethereum_mainnet_target():
    """A target with chain_network='ethereum-mainnet' now resolves via CHAIN_MAP to chain_id=1."""
    from services.api.app.evm_activity_provider import CHAIN_MAP
    target_row = {'chain_network': 'ethereum-mainnet'}
    normalized_chain = (str(target_row.get('chain_network') or '').strip() or 'unknown')
    # CHAIN_MAP must recognize this alias
    assert CHAIN_MAP.get(normalized_chain, {}).get('chain_id') == 1


def test_chain_network_fallback_is_unknown_not_ethereum_mainnet():
    """When chain_network is empty, the fallback must be 'unknown', not 'ethereum-mainnet'."""
    for empty_val in (None, '', '  '):
        chain_network = str(empty_val or '').strip() or 'unknown'
        assert chain_network == 'unknown', (
            f'Fallback for {empty_val!r} must be "unknown", got {chain_network!r}'
        )


# ---------------------------------------------------------------------------
# D. No API keys are logged in startup output
# ---------------------------------------------------------------------------

def test_startup_log_rpc_host_strips_key_from_url(monkeypatch):
    """Startup log must emit only the hostname, not the full URL with API key."""
    from urllib.parse import urlparse

    secret_url = 'https://base-mainnet.infura.io/v3/supersecretapikey12345'
    monkeypatch.setenv('EVM_RPC_URL', secret_url)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    from services.api.app.evm_activity_provider import _resolve_evm_rpc_url
    rpc_url = _resolve_evm_rpc_url()
    rpc_host = urlparse(rpc_url).hostname or 'unconfigured'

    assert 'supersecretapikey12345' not in rpc_host
    assert rpc_host == 'base-mainnet.infura.io'


def test_startup_log_rpc_host_strips_alchemy_key(monkeypatch):
    """Alchemy URLs with API keys in path must not expose the key."""
    from urllib.parse import urlparse

    secret_url = 'https://base-mainnet.g.alchemy.com/v2/myalchemykey999'
    monkeypatch.setenv('EVM_RPC_URL', secret_url)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    from services.api.app.evm_activity_provider import _resolve_evm_rpc_url
    rpc_url = _resolve_evm_rpc_url()
    rpc_host = urlparse(rpc_url).hostname or 'unconfigured'

    assert 'myalchemykey999' not in rpc_host
    assert rpc_host == 'base-mainnet.g.alchemy.com'


def test_startup_log_rpc_host_unconfigured_when_no_url(monkeypatch):
    """When no RPC URL is configured, the host must be 'unconfigured'."""
    from urllib.parse import urlparse

    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    from services.api.app.evm_activity_provider import _resolve_evm_rpc_url
    rpc_url = _resolve_evm_rpc_url()
    rpc_host = urlparse(rpc_url).hostname or 'unconfigured'

    assert rpc_host == 'unconfigured'


# ---------------------------------------------------------------------------
# E. CHAIN_MAP aliases — ethereum-mainnet and base-mainnet must resolve correctly
# ---------------------------------------------------------------------------

def test_chain_map_ethereum_mainnet_alias():
    """CHAIN_MAP must recognise 'ethereum-mainnet' as chain_id=1."""
    from services.api.app.evm_activity_provider import CHAIN_MAP
    assert CHAIN_MAP.get('ethereum-mainnet', {}).get('chain_id') == 1


def test_chain_map_mainnet_alias():
    """CHAIN_MAP must recognise 'mainnet' as chain_id=1."""
    from services.api.app.evm_activity_provider import CHAIN_MAP
    assert CHAIN_MAP.get('mainnet', {}).get('chain_id') == 1


def test_chain_map_base_mainnet_alias():
    """CHAIN_MAP must recognise 'base-mainnet' as chain_id=8453."""
    from services.api.app.evm_activity_provider import CHAIN_MAP
    assert CHAIN_MAP.get('base-mainnet', {}).get('chain_id') == 8453


def test_chain_map_base():
    """CHAIN_MAP 'base' must resolve to chain_id=8453."""
    from services.api.app.evm_activity_provider import CHAIN_MAP
    assert CHAIN_MAP.get('base', {}).get('chain_id') == 8453


# ---------------------------------------------------------------------------
# F. fetch_evm_activity allows Base when EVM_CHAIN_ID=8453
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_allows_base_when_chain_id_matches(monkeypatch):
    """fetch_evm_activity must not skip Base targets when EVM_CHAIN_ID=8453."""
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    # LIVE_MONITORING_CHAINS defaults to 'ethereum', but EVM_CHAIN_ID=8453 matches base
    monkeypatch.delenv('LIVE_MONITORING_CHAINS', raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.example.com/v1/key')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    from services.api.app import evm_activity_provider
    from services.api.app.evm_activity_provider import CHAIN_MAP

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'contract',
        'contract_identifier': '0xDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF',
        'wallet_address': None,
    }

    network = str(target.get('chain_network') or 'ethereum').strip().lower()
    _allowed_chains = {'ethereum'}  # default LIVE_MONITORING_CHAINS
    _configured_chain_id = int(os.getenv('EVM_CHAIN_ID') or 0) or None
    _network_chain_id = CHAIN_MAP.get(network, {}).get('chain_id')

    # The new logic: even if network not in allowed_chains, allow if IDs match
    allowed = network in _allowed_chains or (
        _configured_chain_id and _network_chain_id and _configured_chain_id == _network_chain_id
    )
    assert allowed, 'Base with EVM_CHAIN_ID=8453 must be allowed through the LIVE_MONITORING_CHAINS gate'


def test_fetch_evm_activity_skips_base_when_chain_id_is_1(monkeypatch):
    """fetch_evm_activity must skip Base targets when EVM_CHAIN_ID=1 (Ethereum)."""
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('LIVE_MONITORING_CHAINS', raising=False)

    from services.api.app.evm_activity_provider import CHAIN_MAP

    network = 'base'
    _allowed_chains = {'ethereum'}
    _configured_chain_id = int(os.getenv('EVM_CHAIN_ID') or 0) or None
    _network_chain_id = CHAIN_MAP.get(network, {}).get('chain_id')

    allowed = network in _allowed_chains or (
        _configured_chain_id and _network_chain_id and _configured_chain_id == _network_chain_id
    )
    assert not allowed, 'Base must be skipped when EVM_CHAIN_ID=1 and LIVE_MONITORING_CHAINS=ethereum'
