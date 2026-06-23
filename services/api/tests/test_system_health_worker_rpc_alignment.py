"""
Worker polling and System Health must use the SAME effective Base RPC config.

The real production symptom this guards against: API, Database, Redis, and Worker
all report Operational while System Health shows "Base RPC failing". That happened
because the worker polls Base targets via ``resolve_chain_rpc('base')``
(``EVM_RPC_URL_8453`` → ``BASE_EVM_RPC_URL`` → global), but System Health's RPC
check used the legacy global resolver, which keyed Base lookups off
``EVM_CHAIN_ID``/``STAGING_EVM_CHAIN_ID``. With the staging default
``STAGING_EVM_CHAIN_ID=1`` (Ethereum), System Health never saw ``EVM_RPC_URL_8453``
and reported Base as failing even though polling was healthy.

Covers:
1. System Health resolves Base the same way the worker does (env-by-env equality).
2. ``_check_rpc`` probes the Base endpoint even when the global chain id is
   Ethereum / unset (the bug).
3. Exact operator-facing messages for missing and failing Base RPC.
4. No secret (key / path / credentials) is ever surfaced — host only.
5. Worker startup logs confirm Base chain 8453 polling is active, host only.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SH_MODULE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'system_health.py'
sys.path.insert(0, str(REPO_ROOT))

from services.api.app import run_monitoring_worker  # noqa: E402
from services.api.app.evm_activity_provider import resolve_chain_rpc  # noqa: E402

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
    'ETHEREUM_EVM_RPC_URL', 'EVM_ETHEREUM_RPC_URL', 'ETH_EVM_RPC_URL',
    'EVM_RPC_FAILOVER_URLS', 'EVM_RPC_FAILOVER_URLS_8453',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID',
)

_MISSING_MESSAGE = 'Base RPC URL is missing in worker service. Set EVM_RPC_URL or STAGING_EVM_RPC_URL.'
_FAILED_LEAD = 'Base RPC request failed. Check provider key, network, or rate limit.'


def _load_sh():
    module_name = f'system_health_align_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, SH_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def sh():
    return _load_sh()


def _clear_rpc_env(monkeypatch):
    for var in _RPC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in ('STAGING_WORKER_ENABLED', 'WORKER_ENABLED', 'MONITORING_WORKER_ENABLED', 'LIVE_MODE_ENABLED'):
        monkeypatch.delenv(var, raising=False)


def _ok_response(block_hex: str = '0x2d16800'):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': block_hex}).encode()
    return resp


# ---------------------------------------------------------------------------
# 1. Effective configuration is identical to the worker's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('env', [
    {'EVM_RPC_URL_8453': 'https://base.example.com/v2/key'},
    {'BASE_EVM_RPC_URL': 'https://base-alias.example.com/v2/key'},
    {'EVM_BASE_RPC_URL': 'https://base-alias2.example.com/v2/key'},
    {'EVM_RPC_URL': 'https://global.example.com/v2/key'},
    {'STAGING_EVM_RPC_URL': 'https://staging.example.com/v2/key'},
    # The production bug: Ethereum global chain id, Base via per-chain var.
    {'STAGING_EVM_CHAIN_ID': '1',
     'EVM_RPC_URL_8453': 'https://base.example.com/v2/key',
     'EVM_RPC_URL': 'https://eth.example.com/v2/key'},
])
def test_system_health_base_resolution_matches_worker(sh, monkeypatch, env):
    _clear_rpc_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    worker_url = (resolve_chain_rpc('base').get('rpc_url') or '').strip()
    assert sh._resolve_base_rpc_url() == worker_url, (
        'System Health must resolve the same Base RPC URL the worker polls with.'
    )


def test_check_rpc_uses_base_routing_when_global_chain_id_is_ethereum(sh, monkeypatch):
    """The headline bug: worker polls Base via EVM_RPC_URL_8453 while the global
    chain id is Ethereum. System Health must probe that SAME Base endpoint."""
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')  # Ethereum global (staging default)
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base-mainnet.example.com/v2/key')
    # Intentionally NO global EVM_RPC_URL / STAGING_EVM_RPC_URL.

    with patch.object(sh, 'urlopen', return_value=_ok_response()):
        result = sh._check_rpc()

    assert result['status'] == 'healthy', (
        f'Base routing (EVM_RPC_URL_8453) must drive _check_rpc, got: {result}'
    )
    assert 'base-mainnet.example.com' in result['message']


def test_check_rpc_uses_base_alias_without_chain_id(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('BASE_EVM_RPC_URL', 'https://base-alias.example.com/v2/key')

    with patch.object(sh, 'urlopen', return_value=_ok_response()):
        result = sh._check_rpc()

    assert result['status'] == 'healthy', f'BASE_EVM_RPC_URL must be honored: {result}'
    assert 'base-alias.example.com' in result['message']


# ---------------------------------------------------------------------------
# 2. Exact operator-facing messages
# ---------------------------------------------------------------------------

def test_missing_base_rpc_exact_message(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    result = sh._check_rpc()
    assert result['status'] == 'unavailable'
    assert result['message'] == _MISSING_MESSAGE


def test_failing_base_rpc_leads_with_mandated_sentence(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base.example.com/v2/super-secret-key')

    err = urllib.error.HTTPError(
        'https://base.example.com/v2/super-secret-key', 500, 'Server Error', {}, None,
    )
    with patch.object(sh, 'urlopen', side_effect=err):
        result = sh._check_rpc()

    assert result['status'] == 'failing'
    assert result['message'].startswith(_FAILED_LEAD), (
        f'Failing message must lead with the mandated sentence: {result["message"]}'
    )
    # Secret-free: only the host is surfaced anywhere in the result.
    assert 'super-secret-key' not in str(result)
    assert '/v2/' not in result['message']


def test_failing_base_rpc_unauthorized_keeps_reason(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base.example.com/v2/secret-key-abc')

    err = urllib.error.HTTPError(
        'https://base.example.com/v2/secret-key-abc', 401, 'Unauthorized', {}, None,
    )
    with patch.object(sh, 'urlopen', side_effect=err):
        result = sh._check_rpc()

    assert result['status'] == 'failing'
    assert result['message'].startswith(_FAILED_LEAD)
    # The categorized reason is still present for operators, key is not.
    msg = result['message'].lower()
    assert 'unauthorized' in msg or '401' in msg
    assert 'secret-key-abc' not in str(result)


# ---------------------------------------------------------------------------
# 3. Worker startup logs confirm Base chain 8453 polling is active
# ---------------------------------------------------------------------------

def test_worker_startup_logs_base_rpc_active_host_only(monkeypatch, caplog):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')  # Ethereum global; Base via per-chain
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base-mainnet.example.com/v2/secret-key-xyz')
    run_monitoring_worker._resolve_worker_enabled_env()

    # Keep both the global self-check and the per-chain validation off the network.
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda *a, **k: {
            'ok': True, 'chain_id_hex': '0x2105', 'chain_id_int': 8453,
            'block_number_hex': '0x1', 'block_number_int': 1, 'error': None,
        },
    )

    logger = logging.getLogger('test_base_startup_active')
    with caplog.at_level(logging.INFO, logger='test_base_startup_active'):
        run_monitoring_worker._log_startup_provider_status(logger)

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'startup_base_rpc' in text
    assert 'rpc_configured=True' in text
    assert 'chain_id=8453' in text
    assert 'worker_enabled=True' in text
    assert 'polling_interval_seconds=' in text
    assert 'rpc_host=base-mainnet.example.com' in text
    assert 'startup_base_polling_active' in text
    # No secret / path ever printed.
    assert 'secret-key-xyz' not in text
    assert '/v2/' not in text


def test_worker_startup_logs_base_rpc_missing_exact_sentence(monkeypatch, caplog):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    run_monitoring_worker._resolve_worker_enabled_env()

    logger = logging.getLogger('test_base_startup_missing')
    with caplog.at_level(logging.INFO, logger='test_base_startup_missing'):
        run_monitoring_worker._log_startup_provider_status(logger)

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'startup_base_rpc' in text
    assert 'rpc_configured=False' in text
    assert 'worker_startup_base_rpc_missing' in text
    assert _MISSING_MESSAGE in text
