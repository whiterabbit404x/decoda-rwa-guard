"""
RPC rate-limit backoff + chain-mismatch hard-skip guards.

Regression coverage for the production incident where a Base worker (chain_id=8453)
kept calling eth_blockNumber every cycle — including for Ethereum-mainnet targets it
cannot serve — and hammered Alchemy into HTTP 429s.

Covers:
  1. An Ethereum target (chain_id=1) under a Base worker (rpc_chain_id=8453) hard-skips
     with NO RPC call (no eth_chainId / eth_blockNumber / coverage probe).
  2. A Base target (chain_id=8453) under a Base worker IS polled.
  3. An HTTP 429 arms a process-wide provider backoff (>=120s, honors Retry-After).
  4. While the backoff is active, later cycles skip eth_blockNumber entirely.
  5. /system-health serves the backoff state without re-probing the provider.
  6. The coverage probe respects the same provider backoff.
  7. (interval cap lives in test_monitoring_worker_runtime.py)
  8. No RPC URL / API key leaks into the backoff logs or the served response.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import urllib.error
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SH_MODULE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'system_health.py'
sys.path.insert(0, str(REPO_ROOT))

from services.api.app import activity_providers as ap  # noqa: E402
from services.api.app import evm_activity_provider as eap  # noqa: E402

WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
    'ETHEREUM_EVM_RPC_URL', 'EVM_ETHEREUM_RPC_URL', 'ETH_EVM_RPC_URL',
    'EVM_RPC_FAILOVER_URLS', 'EVM_RPC_FAILOVER_URLS_8453',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'LIVE_MONITORING_CHAINS',
    'EVM_WS_URL', 'EVM_RPC_MAX_RETRIES', 'MONITORING_INGESTION_MODE',
    'LIVE_MONITORING_ENABLED', 'MARKET_TELEMETRY_SOURCE_URLS',
    'ORACLE_TELEMETRY_SOURCE_URLS',
)


def _clear(monkeypatch):
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _live(monkeypatch):
    """Live ingestion mode with an RPC URL configured (offline — never dialed)."""
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.rpc/v2/key')


def _load_sh():
    module_name = f'system_health_backoff_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, SH_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingRpc:
    """JSON-RPC stub that records every method called so a test can assert that
    NO RPC call was made (chain mismatch / backoff) or that polling did run."""

    def __init__(self, chain_id: int = 8453, block: int = 47_000_000) -> None:
        self._chain_id = chain_id
        self._block = block
        self.methods: list[str] = []

    def call(self, method: str, params: list) -> object:
        self.methods.append(method)
        if method == 'eth_chainId':
            return hex(self._chain_id)
        if method == 'eth_blockNumber':
            return hex(self._block)
        if method == 'eth_getLogs':
            return []
        if method == 'eth_getBlockByNumber':
            bn = int(str(params[0]), 16)
            return {'hash': f'0xblk{bn}', 'timestamp': hex(1_700_000_000 + bn), 'transactions': []}
        return {}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # Keep _build_cycle_telemetry offline during scans.
    monkeypatch.setattr(eap, '_fetch_market_observations', lambda target: [])
    monkeypatch.setattr(eap, '_fetch_oracle_observations', lambda target: [])


# ---------------------------------------------------------------------------
# 1. Chain mismatch hard skip — no RPC call at all
# ---------------------------------------------------------------------------

def test_eth_target_hard_skips_without_any_rpc_call(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')  # worker serves Base

    rec = _RecordingRpc(chain_id=8453)
    target = {'id': 't-eth', 'target_type': 'wallet', 'chain_network': 'ethereum', 'wallet_address': WALLET}
    events = eap.fetch_evm_activity(target, None, rpc_client=rec)

    assert events == []
    assert rec.methods == [], 'a chain-mismatched target must trigger zero RPC calls'
    assert target.get('_evm_chain_mismatch') is True
    assert 'chain_mismatch' in str(target.get('_evm_chain_mismatch_reason') or '')
    assert 'target_chain_id=1' in str(target.get('_evm_chain_mismatch_reason') or '')


def test_eth_target_provider_result_skips_fetch_and_coverage_probe(monkeypatch):
    _clear(monkeypatch)
    _live(monkeypatch)
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')

    called = {'fetch': 0, 'probe': 0}
    monkeypatch.setattr(ap, 'fetch_evm_activity', lambda *a, **k: called.__setitem__('fetch', called['fetch'] + 1) or [])
    monkeypatch.setattr(ap, 'probe_rpc_health', lambda *a, **k: called.__setitem__('probe', called['probe'] + 1) or {'ok': True})

    target = {'id': str(uuid.uuid4()), 'workspace_id': str(uuid.uuid4()),
              'chain_network': 'ethereum', 'target_type': 'wallet', 'wallet_address': WALLET}
    result = ap.fetch_target_activity_result(target, None)

    assert result.status == 'failed'
    assert result.reason_code == 'CHAIN_RPC_MISMATCH'
    assert result.evidence_present is False
    assert result.latest_block is None
    assert called['fetch'] == 0, 'fetch_evm_activity must not run for a chain-mismatched target'
    assert called['probe'] == 0, 'coverage probe must not run for a chain-mismatched target'


# ---------------------------------------------------------------------------
# 2. Base target IS selected for polling
# ---------------------------------------------------------------------------

def test_base_target_is_polled_when_chain_matches(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')

    skip, target_chain_id, rpc_chain_id = eap.evaluate_chain_mismatch('base-mainnet')
    assert skip is False
    assert target_chain_id == 8453 and rpc_chain_id == 8453

    rec = _RecordingRpc(chain_id=8453, block=47_000_000)
    target = {'id': 't-base', 'target_type': 'wallet', 'chain_network': 'base', 'wallet_address': WALLET}
    eap.fetch_evm_activity(target, None, rpc_client=rec)

    assert 'eth_blockNumber' in rec.methods, 'a matching Base target must be polled'
    assert target.get('_evm_chain_mismatch') is not True


# ---------------------------------------------------------------------------
# 3. HTTP 429 arms the provider backoff
# ---------------------------------------------------------------------------

def test_http_429_sets_provider_backoff(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')  # no inline retries — fail fast
    eap.reset_rpc_provider_state()
    assert eap.rpc_provider_backoff_active() is False

    err = urllib.error.HTTPError(
        'https://base-mainnet.g.alchemy.com/v2/secret', 429, 'Too Many Requests',
        {'Retry-After': '200'}, None,
    )
    with patch.object(eap.request, 'urlopen', side_effect=err):
        with pytest.raises(Exception):
            eap.JsonRpcClient('https://base-mainnet.g.alchemy.com/v2/secret').call('eth_blockNumber', [])

    assert eap.rpc_provider_backoff_active() is True
    status = eap.rpc_provider_backoff_status()
    assert status['error_class'] == 'rate_limited'
    # Retry-After=200 was honored (above the 120s minimum).
    assert status['remaining_seconds'] > 120


def test_record_rpc_rate_limited_minimum_120s(monkeypatch):
    eap.reset_rpc_provider_state()
    backoff = eap.record_rpc_rate_limited(None)
    assert backoff >= 120
    assert eap.rpc_provider_backoff_active() is True

    eap.reset_rpc_provider_state()
    backoff_retry = eap.record_rpc_rate_limited(300)
    assert backoff_retry >= 300, 'a larger Retry-After must extend the backoff'


# ---------------------------------------------------------------------------
# 4. Backoff prevents eth_blockNumber in later cycles
# ---------------------------------------------------------------------------

def test_backoff_prevents_eth_blocknumber_next_cycle(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')

    eap.record_rpc_rate_limited(None)  # a prior cycle hit 429

    rec = _RecordingRpc(chain_id=8453)
    target = {'id': 't-base', 'target_type': 'wallet', 'chain_network': 'base', 'wallet_address': WALLET}
    events = eap.fetch_evm_activity(target, None, rpc_client=rec)

    assert events == []
    assert rec.methods == [], 'no eth_blockNumber may be called while the provider backoff is active'
    assert target.get('_evm_provider_backoff') is True


def test_probe_rpc_health_skips_rpc_during_backoff(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')
    eap.record_rpc_rate_limited(None)

    def _boom(*_a, **_k):
        raise AssertionError('probe_rpc_health must not dial RPC during backoff')

    monkeypatch.setattr(eap.request, 'urlopen', _boom)
    result = eap.probe_rpc_health()
    assert result['ok'] is False
    assert result.get('provider_backoff_active') is True
    assert result.get('cache_hit') is True


# ---------------------------------------------------------------------------
# 5. System Health serves the backoff state without re-probing
# ---------------------------------------------------------------------------

def test_system_health_uses_backoff_without_probing(monkeypatch):
    sh = _load_sh()
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key')
    sh._reset_rpc_health_cache()
    eap.reset_rpc_provider_state()
    eap.record_rpc_rate_limited(None)  # worker armed the shared backoff

    calls = {'n': 0}
    monkeypatch.setattr(sh, '_check_rpc', lambda: calls.__setitem__('n', calls['n'] + 1) or sh._component('healthy', 'ok'))

    comp = sh._cached_base_rpc_health()
    assert calls['n'] == 0, 'system-health must not probe the provider during a backoff window'
    assert comp['status'] == 'failing'
    assert 'in backoff until' in comp['message'].lower()
    assert 'http 429' in comp['message'].lower()
    # secret-free
    assert 'secret-key' not in str(comp)
    assert '/v2/' not in comp['message']


# ---------------------------------------------------------------------------
# 6. Coverage probe respects the provider backoff
# ---------------------------------------------------------------------------

def test_coverage_probe_respects_provider_backoff(monkeypatch):
    _clear(monkeypatch)
    _live(monkeypatch)
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    eap.record_rpc_rate_limited(None)

    probe_calls = {'n': 0}
    monkeypatch.setattr(ap, 'probe_rpc_health', lambda *a, **k: probe_calls.__setitem__('n', probe_calls['n'] + 1) or {'ok': True})
    monkeypatch.setattr(ap, 'fetch_evm_activity', lambda *a, **k: [])

    target = {'id': str(uuid.uuid4()), 'workspace_id': str(uuid.uuid4()),
              'chain_network': 'base', 'target_type': 'wallet', 'wallet_address': WALLET}
    result = ap.fetch_target_activity_result(target, None)

    assert probe_calls['n'] == 0, 'the coverage probe must not call RPC during backoff'
    assert result.reason_code == 'PROVIDER_BACKOFF_ACTIVE'
    assert result.status == 'degraded'
    assert result.evidence_present is False


# ---------------------------------------------------------------------------
# 8. No RPC URL / API key leaks into the backoff logs or status
# ---------------------------------------------------------------------------

def test_no_secret_in_backoff_logs_or_status(monkeypatch, caplog):
    _clear(monkeypatch)
    secret = 'ultra-secret-key-zzz'
    url = f'https://base-mainnet.g.alchemy.com/v2/{secret}'
    monkeypatch.setenv('EVM_RPC_URL', url)
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    eap.reset_rpc_provider_state()

    err = urllib.error.HTTPError(url, 429, 'Too Many Requests', {}, None)
    with caplog.at_level(logging.WARNING, logger='services.api.app.evm_activity_provider'):
        with patch.object(eap.request, 'urlopen', side_effect=err):
            with pytest.raises(Exception):
                eap.JsonRpcClient(url).call('eth_blockNumber', [])

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'rpc_provider_backoff_set' in text, 'the backoff must be logged'
    assert secret not in text
    assert '/v2/' not in text
    assert secret not in str(eap.rpc_provider_backoff_status())
