"""
Multi-RPC failover for Base reliability.

Production blocker: Alchemy returns HTTP 429 and the (single-provider) Base worker
stalls. These tests cover supporting multiple Base RPC providers with SAFE,
PER-HOST failover so that a single rate-limited provider does not stop polling:

  1. A single EVM_RPC_URL still resolves and serves (backward compatible).
  2. EVM_RPC_URLS parses a comma-separated multi-provider list (and wins over the
     single EVM_RPC_URL / STAGING_EVM_RPC_URL vars).
  3. When the first provider returns 429, the failover client benches ONLY that
     provider host and serves the call via the next provider.
  4. When every provider returns 429, the failover client raises
     all_rpc_providers_unavailable.
  5. Backoff is per provider host: Alchemy 429 ⇒ Alchemy benched while QuickNode is
     still allowed (rpc_provider_backoff_active stays False so the worker keeps polling).
  6. System Health reports Base RPC: Degraded when failover is serving via a healthy
     provider while another is rate-limited.
  7. No full RPC URL or API key ever appears in the failover logs, the structured
     log fields, or the System Health component — only the provider host.
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
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SH_MODULE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'system_health.py'
sys.path.insert(0, str(REPO_ROOT))

from services.api.app import evm_activity_provider as eap  # noqa: E402

ALCHEMY_KEY = 'ALCHEMY-SUPER-SECRET-KEY'
QUICKNODE_KEY = 'QUICKNODE-SECRET-TOKEN'
ALCHEMY_URL = f'https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}'
QUICKNODE_URL = f'https://base.quicknode.example/{QUICKNODE_KEY}'
ALCHEMY_HOST = 'base-mainnet.g.alchemy.com'
QUICKNODE_HOST = 'base.quicknode.example'

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL', 'EVM_RPC_URLS',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
    'ETHEREUM_EVM_RPC_URL', 'EVM_ETHEREUM_RPC_URL', 'ETH_EVM_RPC_URL',
    'EVM_RPC_FAILOVER_URLS', 'EVM_RPC_FAILOVER_URLS_8453',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'LIVE_MONITORING_CHAINS',
    'EVM_WS_URL', 'EVM_RPC_MAX_RETRIES', 'APP_ENV', 'APP_MODE',
    'RPC_PROVIDER_BACKOFF_JITTER_SECONDS', 'RPC_PROVIDER_BACKOFF_MIN_SECONDS',
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # No inline retries and deterministic backoff windows for the assertions.
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    monkeypatch.setenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '0')
    eap.reset_rpc_provider_state()
    yield
    eap.reset_rpc_provider_state()


# ---------------------------------------------------------------------------
# urlopen dispatcher: per-host 429 vs a healthy JSON-RPC response.
# ---------------------------------------------------------------------------

def _ok_resp(result: str):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}).encode()
    return resp


def _dispatch(*, rate_limited_hosts: set[str], chain_id: int = 8453, block: int = 47_000_000):
    """Return a urlopen side_effect that 429s for rate_limited_hosts, else answers."""
    def _fn(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        host = (urlparse(url).hostname or '').lower()
        if host in rate_limited_hosts:
            raise urllib.error.HTTPError(url, 429, 'Too Many Requests', {}, None)
        method = ''
        try:
            method = json.loads(req.data.decode('utf-8')).get('method', '')
        except Exception:
            pass
        if method == 'eth_chainId':
            return _ok_resp(hex(chain_id))
        if method == 'eth_blockNumber':
            return _ok_resp(hex(block))
        return _ok_resp('0x0')
    return _fn


def _load_sh():
    module_name = f'system_health_failover_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, SH_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Single EVM_RPC_URL still works (backward compatibility)
# ---------------------------------------------------------------------------

def test_single_evm_rpc_url_still_resolves_and_serves(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'https://only.example/rpc')

    assert eap._resolve_evm_rpc_urls() == ['https://only.example/rpc']
    assert eap._resolve_evm_rpc_url() == 'https://only.example/rpc'

    with patch.object(eap.request, 'urlopen', side_effect=_dispatch(rate_limited_hosts=set())):
        client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
        result = client.call('eth_blockNumber', [])

    assert int(result, 16) == 47_000_000
    assert client.active_host == 'only.example'
    assert eap.rpc_provider_backoff_active() is False


def test_missing_evm_rpc_urls_falls_back_to_staging_then_evm_rpc_url(monkeypatch):
    # EVM_RPC_URLS missing → STAGING_EVM_RPC_URL is used.
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.example/rpc')
    monkeypatch.setenv('EVM_RPC_URL', 'https://legacy.example/rpc')
    assert eap._resolve_evm_rpc_url() == 'https://staging.example/rpc'

    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    # Only EVM_RPC_URL remains.
    assert eap._resolve_evm_rpc_url() == 'https://legacy.example/rpc'
    assert eap._resolve_evm_rpc_urls() == ['https://legacy.example/rpc']


# ---------------------------------------------------------------------------
# 2. EVM_RPC_URLS parses multiple URLs (and wins over the single vars)
# ---------------------------------------------------------------------------

def test_evm_rpc_urls_parses_multiple_and_trims(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', ' https://a.example/rpc , https://b.example/rpc ,https://c.example/rpc ')
    assert eap._resolve_evm_rpc_urls() == [
        'https://a.example/rpc', 'https://b.example/rpc', 'https://c.example/rpc',
    ]
    # First entry is the primary single-URL resolution.
    assert eap._resolve_evm_rpc_url() == 'https://a.example/rpc'


def test_evm_rpc_urls_take_precedence_over_single_url(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', 'https://primary.example/rpc,https://backup.example/rpc')
    monkeypatch.setenv('EVM_RPC_URL', 'https://legacy-single.example/rpc')
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://legacy-staging.example/rpc')
    assert eap._resolve_evm_rpc_url() == 'https://primary.example/rpc'
    assert eap._resolve_evm_rpc_urls() == [
        'https://primary.example/rpc', 'https://backup.example/rpc',
    ]


def test_resolve_chain_rpc_base_uses_evm_rpc_urls(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', 'https://a.example/rpc,https://b.example/rpc')
    info = eap.resolve_chain_rpc('base')
    assert info['expected_chain_id'] == 8453
    assert info['rpc_url'] == 'https://a.example/rpc'
    assert info['rpc_url_env'] == 'EVM_RPC_URLS'
    assert info['rpc_urls'] == ['https://a.example/rpc', 'https://b.example/rpc']


def test_evm_rpc_urls_appends_legacy_failover_urls(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', 'https://a.example/rpc')
    monkeypatch.setenv('EVM_RPC_FAILOVER_URLS', 'https://b.example/rpc,https://a.example/rpc')
    # Failover URLs are appended and de-duplicated.
    assert eap._resolve_evm_rpc_urls() == ['https://a.example/rpc', 'https://b.example/rpc']


# ---------------------------------------------------------------------------
# 3. First provider 429 falls back to the second provider
# ---------------------------------------------------------------------------

def test_first_provider_429_falls_back_to_second(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')

    with patch.object(eap.request, 'urlopen', side_effect=_dispatch(rate_limited_hosts={ALCHEMY_HOST})):
        client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
        result = client.call('eth_blockNumber', [])

    assert int(result, 16) == 47_000_000
    assert client.active_host == QUICKNODE_HOST
    # Only Alchemy is benched; QuickNode keeps serving.
    assert eap.host_backoff_active(ALCHEMY_HOST) is True
    assert eap.host_backoff_active(QUICKNODE_HOST) is False
    # Not ALL providers are benched, so the worker must keep polling.
    assert eap.rpc_provider_backoff_active() is False

    fields = eap.rpc_provider_log_fields()
    assert fields['rpc_failover_used'] is True
    assert fields['active_rpc_host'] == QUICKNODE_HOST
    assert fields['rpc_provider_count'] == 2
    assert fields['failed_rpc_hosts'] == [ALCHEMY_HOST]
    assert fields['backoff_hosts'] == [ALCHEMY_HOST]


def test_failover_skips_already_benched_host_without_redialing(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')
    # Alchemy is already in backoff from a prior cycle.
    eap.record_rpc_rate_limited(None, host=ALCHEMY_HOST)

    dispatched_hosts: list[str] = []

    def _record(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        dispatched_hosts.append((urlparse(url).hostname or '').lower())
        return _ok_resp(hex(47_000_001))

    with patch.object(eap.request, 'urlopen', side_effect=_record):
        client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
        result = client.call('eth_blockNumber', [])

    assert int(result, 16) == 47_000_001
    assert client.active_host == QUICKNODE_HOST
    # The benched Alchemy host was skipped entirely — never re-dialed.
    assert ALCHEMY_HOST not in dispatched_hosts
    assert dispatched_hosts == [QUICKNODE_HOST]


# ---------------------------------------------------------------------------
# 4. All providers 429 → all_rpc_providers_unavailable
# ---------------------------------------------------------------------------

def test_all_providers_429_returns_all_rpc_providers_unavailable(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')

    with patch.object(eap.request, 'urlopen',
                      side_effect=_dispatch(rate_limited_hosts={ALCHEMY_HOST, QUICKNODE_HOST})):
        client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
        with pytest.raises(RuntimeError, match='all_rpc_providers_unavailable'):
            client.call('eth_blockNumber', [])

    # Every configured provider host is benched → the worker skips RPC this cycle.
    assert eap.host_backoff_active(ALCHEMY_HOST) is True
    assert eap.host_backoff_active(QUICKNODE_HOST) is True
    assert eap.rpc_provider_backoff_active() is True
    assert eap.backoff_hosts() == sorted([ALCHEMY_HOST, QUICKNODE_HOST])


# ---------------------------------------------------------------------------
# 5. Backoff is per provider host
# ---------------------------------------------------------------------------

def test_backoff_is_per_provider_host(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')

    # A 429 against Alchemy benches only Alchemy.
    eap.record_rpc_rate_limited(None, host=ALCHEMY_HOST)
    assert eap.host_backoff_active(ALCHEMY_HOST) is True
    assert eap.host_backoff_active(QUICKNODE_HOST) is False
    # One of two providers benched is NOT a global skip.
    assert eap.rpc_provider_backoff_active() is False
    assert eap.backoff_hosts() == [ALCHEMY_HOST]

    # Benching the second provider too flips the global skip on.
    eap.record_rpc_rate_limited(None, host=QUICKNODE_HOST)
    assert eap.rpc_provider_backoff_active() is True

    # Clearing one host is per-host, not global.
    eap.clear_rpc_provider_backoff(ALCHEMY_HOST)
    assert eap.host_backoff_active(ALCHEMY_HOST) is False
    assert eap.host_backoff_active(QUICKNODE_HOST) is True
    assert eap.rpc_provider_backoff_active() is False


def test_record_without_host_benches_all_configured_providers(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')
    # Legacy whole-provider call (no host) benches every configured provider.
    eap.record_rpc_rate_limited(None)
    assert eap.host_backoff_active(ALCHEMY_HOST) is True
    assert eap.host_backoff_active(QUICKNODE_HOST) is True
    assert eap.rpc_provider_backoff_active() is True


# ---------------------------------------------------------------------------
# 6. System Health shows Degraded when failover is serving via a healthy provider
# ---------------------------------------------------------------------------

def test_system_health_degraded_when_failover_active(monkeypatch):
    sh = _load_sh()
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    monkeypatch.setenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '0')
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')
    sh._reset_rpc_health_cache()
    eap.reset_rpc_provider_state()

    with patch.object(sh, 'urlopen', side_effect=_dispatch(rate_limited_hosts={ALCHEMY_HOST})):
        comp = sh._check_rpc()

    assert comp['status'] == 'degraded'
    msg = comp['message'].lower()
    assert 'failover' in msg
    assert QUICKNODE_HOST in comp['message']    # active provider named
    assert ALCHEMY_HOST in comp['message']      # failing provider named
    # Secret-free: only hosts, never the URL path or key.
    assert ALCHEMY_KEY not in str(comp)
    assert QUICKNODE_KEY not in str(comp)
    assert '/v2/' not in comp['message']


def test_system_health_operational_when_primary_healthy(monkeypatch):
    sh = _load_sh()
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')
    sh._reset_rpc_health_cache()
    eap.reset_rpc_provider_state()

    with patch.object(sh, 'urlopen', side_effect=_dispatch(rate_limited_hosts=set())):
        comp = sh._check_rpc()

    assert comp['status'] == 'healthy'
    assert ALCHEMY_HOST in comp['message']  # primary served
    assert ALCHEMY_KEY not in str(comp)


def test_system_health_failing_when_all_providers_rate_limited(monkeypatch):
    sh = _load_sh()
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    monkeypatch.setenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '0')
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')
    sh._reset_rpc_health_cache()
    eap.reset_rpc_provider_state()

    with patch.object(sh, 'urlopen',
                      side_effect=_dispatch(rate_limited_hosts={ALCHEMY_HOST, QUICKNODE_HOST})):
        comp = sh._check_rpc()

    assert comp['status'] == 'failing'
    assert 'all providers unavailable' in comp['message'].lower()
    assert ALCHEMY_KEY not in str(comp)
    assert QUICKNODE_KEY not in str(comp)


# ---------------------------------------------------------------------------
# 7. No full RPC URL / API key leaks into logs, log fields, or the response
# ---------------------------------------------------------------------------

def test_no_secret_in_failover_logs_or_fields(monkeypatch, caplog):
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')

    with caplog.at_level(logging.INFO, logger='services.api.app.evm_activity_provider'):
        with patch.object(eap.request, 'urlopen', side_effect=_dispatch(rate_limited_hosts={ALCHEMY_HOST})):
            client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
            client.call('eth_blockNumber', [])

    text = '\n'.join(r.getMessage() for r in caplog.records)
    # The failover event is logged, host-only.
    assert 'rpc_failover' in text
    assert f'active_rpc_host={QUICKNODE_HOST}' in text
    assert 'rpc_failover_used=true' in text
    assert ALCHEMY_HOST in text
    # Never the URL path, key, or credentialed fragment.
    for secret in (ALCHEMY_KEY, QUICKNODE_KEY, '/v2/'):
        assert secret not in text, f'{secret!r} leaked into failover logs'
    # The structured log fields are likewise secret-free.
    assert ALCHEMY_KEY not in str(eap.rpc_provider_log_fields())
    assert QUICKNODE_KEY not in str(eap.rpc_provider_backoff_status())


def test_all_unavailable_logs_are_host_only(monkeypatch, caplog):
    monkeypatch.setenv('EVM_RPC_URLS', f'{ALCHEMY_URL},{QUICKNODE_URL}')

    with caplog.at_level(logging.ERROR, logger='services.api.app.evm_activity_provider'):
        with patch.object(eap.request, 'urlopen',
                          side_effect=_dispatch(rate_limited_hosts={ALCHEMY_HOST, QUICKNODE_HOST})):
            client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
            with pytest.raises(RuntimeError):
                client.call('eth_blockNumber', [])

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'rpc_all_providers_unavailable' in text
    for secret in (ALCHEMY_KEY, QUICKNODE_KEY, '/v2/'):
        assert secret not in text
