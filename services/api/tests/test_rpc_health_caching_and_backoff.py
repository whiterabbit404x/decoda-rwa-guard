"""
RPC reliability: classification, response-time/last-check metadata, short-TTL
caching of the Base RPC probe, configurable worker polling interval, and the
worker's exponential RPC recheck backoff.

These guard the fix for "Base RPC failing because the provider is rate-limiting":
- /ops/system-health must not fire a live eth_blockNumber call on every refresh
  when a fresh cached probe exists.
- Failures must be classified clearly (429 / timeout / missing / unauthorized).
- No secrets may leak into the response or the structured logs.
- The worker poll cadence is configurable (default 60s) and the redundant RPC
  health recheck backs off exponentially so it never compounds a 429.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import socket
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

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
    'ETHEREUM_EVM_RPC_URL', 'EVM_ETHEREUM_RPC_URL', 'ETH_EVM_RPC_URL',
    'EVM_RPC_FAILOVER_URLS', 'EVM_RPC_FAILOVER_URLS_8453',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID',
)


def _load_sh():
    module_name = f'system_health_cache_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, SH_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def sh():
    # Fresh module per test so the module-level cache never bleeds between tests.
    return _load_sh()


def _clear_rpc_env(monkeypatch):
    for var in _RPC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _ok_response(block_hex: str = '0x2d16800'):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': block_hex}).encode()
    return resp


# ---------------------------------------------------------------------------
# 1. RPC 429 → failing with the mandated rate-limit guidance
# ---------------------------------------------------------------------------

def test_rpc_429_returns_failing_with_rate_limit_message(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key-abc')

    err = urllib.error.HTTPError(
        'https://base-mainnet.g.alchemy.com/v2/secret-key-abc', 429, 'Too Many Requests', {}, None,
    )
    with patch.object(sh, 'urlopen', side_effect=err):
        result = sh._check_rpc()

    assert result['status'] == 'failing'
    assert '429' in result['message'] or 'rate' in result['message'].lower()
    # The mandated operator sentence is surfaced as the action.
    assert result['action'] == 'Provider is rate-limiting. Increase RPC quota or reduce polling frequency.'
    # Secret-free.
    assert 'secret-key-abc' not in str(result)


# ---------------------------------------------------------------------------
# 2. RPC timeout → failing with a sanitized timeout message
# ---------------------------------------------------------------------------

def test_rpc_timeout_returns_failing_sanitized(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com/v2/super-secret-key')

    with patch.object(sh, 'urlopen', side_effect=socket.timeout('timed out')):
        result = sh._check_rpc()

    assert result['status'] == 'failing'
    assert 'timeout' in result['message'].lower()
    assert 'did not respond' in (result.get('action') or '').lower()
    # No URL path / key leaks anywhere.
    assert 'super-secret-key' not in str(result)
    assert '/v2/' not in result['message']


# ---------------------------------------------------------------------------
# 3. Missing RPC URL → unavailable / not configured
# ---------------------------------------------------------------------------

def test_missing_rpc_url_returns_unavailable(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    result = sh._check_rpc()
    assert result['status'] == 'unavailable'
    assert result['message'] == (
        'Base RPC URL is missing in worker service. Set EVM_RPC_URL or STAGING_EVM_RPC_URL.'
    )


# ---------------------------------------------------------------------------
# 4. RPC success → operational with block number, response time, last check
# ---------------------------------------------------------------------------

def test_rpc_success_returns_operational_with_block_and_timing(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/super-secret-key')

    with patch.object(sh, 'urlopen', return_value=_ok_response('0x2d16800')):
        result = sh._check_rpc()

    assert result['status'] == 'healthy'
    # latest block number is present
    assert 'block #' in (result.get('metric') or '')
    assert str(int('0x2d16800', 16)) in result['metric']
    # response time is present
    assert 'ms' in result['metric']
    # last successful check timestamp is present
    assert result.get('last_event'), 'healthy probe must record the last successful check time'
    # secret-free
    assert 'super-secret-key' not in str(result)
    assert '/v2/' not in result['message']


# ---------------------------------------------------------------------------
# 5. System Health reuses the cached RPC health within the TTL
# ---------------------------------------------------------------------------

def test_cached_rpc_reused_within_ttl(sh, monkeypatch):
    sh._reset_rpc_health_cache()
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com/v2/key')
    assert sh.RPC_HEALTH_CACHE_TTL_SECONDS > 0, 'default TTL must enable caching'

    calls = {'n': 0}

    def _probe():
        calls['n'] += 1
        return sh._component('healthy', 'ok', metric='block #1 · 5ms')

    monkeypatch.setattr(sh, '_check_rpc', _probe)

    first = sh._cached_base_rpc_health()
    second = sh._cached_base_rpc_health()

    assert calls['n'] == 1, 'within TTL the probe must run exactly once'
    assert first == second


def test_cached_rpc_force_bypasses_cache(sh, monkeypatch):
    sh._reset_rpc_health_cache()
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com/v2/key')

    calls = {'n': 0}
    monkeypatch.setattr(sh, '_check_rpc', lambda: calls.__setitem__('n', calls['n'] + 1) or sh._component('healthy', 'ok'))

    sh._cached_base_rpc_health()
    sh._cached_base_rpc_health(force=True)
    assert calls['n'] == 2, 'force=True must bypass the cache and re-probe'


def test_cached_rpc_ttl_zero_disables_cache(sh, monkeypatch):
    sh._reset_rpc_health_cache()
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com/v2/key')
    monkeypatch.setattr(sh, 'RPC_HEALTH_CACHE_TTL_SECONDS', 0)

    calls = {'n': 0}
    monkeypatch.setattr(sh, '_check_rpc', lambda: calls.__setitem__('n', calls['n'] + 1) or sh._component('healthy', 'ok'))

    sh._cached_base_rpc_health()
    sh._cached_base_rpc_health()
    assert calls['n'] == 2, 'TTL=0 must disable caching'


def test_cache_invalidated_when_rpc_url_changes(sh, monkeypatch):
    sh._reset_rpc_health_cache()
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-a.example.com/v2/key')

    calls = {'n': 0}
    monkeypatch.setattr(sh, '_check_rpc', lambda: calls.__setitem__('n', calls['n'] + 1) or sh._component('healthy', 'ok'))

    sh._cached_base_rpc_health()
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-b.example.com/v2/key')
    sh._cached_base_rpc_health()
    assert calls['n'] == 2, 'changing the resolved RPC URL must invalidate the cache'


# ---------------------------------------------------------------------------
# 6. Worker polling interval is configurable (default = canonical MVP 300s)
# ---------------------------------------------------------------------------

def test_worker_interval_default_is_canonical(monkeypatch):
    # The worker loop default now resolves through the single canonical polling interval
    # (900s), shared with the per-target default + startup report so they never drift.
    from services.api.app import monitoring_runner as _mr
    monkeypatch.delenv('MONITORING_WORKER_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('EVM_POLLING_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('MIN_EVM_POLLING_INTERVAL_SECONDS', raising=False)
    monkeypatch.setattr(sys, 'argv', ['run_monitoring_worker'])
    args = run_monitoring_worker.parse_args()
    assert args.interval_seconds == float(_mr.DEFAULT_CANONICAL_POLLING_INTERVAL_SECONDS) == 900.0


def test_worker_interval_is_configurable(monkeypatch):
    monkeypatch.setenv('MONITORING_WORKER_INTERVAL_SECONDS', '120')
    monkeypatch.setattr(sys, 'argv', ['run_monitoring_worker'])
    args = run_monitoring_worker.parse_args()
    assert args.interval_seconds == 120.0


# ---------------------------------------------------------------------------
# 7. No secrets in the structured RPC probe logs
# ---------------------------------------------------------------------------

def test_no_secret_in_rpc_probe_logs(sh, monkeypatch, caplog):
    _clear_rpc_env(monkeypatch)
    secret = 'ultra-secret-key-xyz'
    monkeypatch.setenv('EVM_RPC_URL', f'https://base-mainnet.g.alchemy.com/v2/{secret}')

    with caplog.at_level(logging.INFO):
        with patch.object(sh, 'urlopen', return_value=_ok_response()):
            sh._check_rpc()

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'rpc_probe' in text, 'a structured rpc_probe log line must be emitted'
    assert 'rpc_status=healthy' in text
    assert 'rpc_host=base-mainnet.g.alchemy.com' in text
    assert 'response_time_ms=' in text
    assert 'chain_id=8453' in text
    assert 'polling_interval_seconds=' in text
    # the key/path must never appear in logs
    assert secret not in text
    assert '/v2/' not in text


def test_rate_limited_probe_logs_rate_limited_status(sh, monkeypatch, caplog):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')
    err = urllib.error.HTTPError('https://base-mainnet.g.alchemy.com/v2/key', 429, 'Too Many', {}, None)
    with caplog.at_level(logging.INFO):
        with patch.object(sh, 'urlopen', side_effect=err):
            sh._check_rpc()
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'rpc_status=rate_limited' in text


# ---------------------------------------------------------------------------
# 8. Worker RPC recheck backoff (pure helpers)
# ---------------------------------------------------------------------------

def test_rpc_recheck_due_respects_backoff():
    assert run_monitoring_worker._rpc_recheck_due(0.0, 60.0) is False
    assert run_monitoring_worker._rpc_recheck_due(59.9, 60.0) is False
    assert run_monitoring_worker._rpc_recheck_due(60.0, 60.0) is True
    assert run_monitoring_worker._rpc_recheck_due(120.0, 60.0) is True


def test_next_rpc_recheck_backoff_grows_and_caps():
    assert run_monitoring_worker._next_rpc_recheck_backoff(60.0, 600.0) == 120.0
    assert run_monitoring_worker._next_rpc_recheck_backoff(120.0, 600.0) == 240.0
    # caps at max
    assert run_monitoring_worker._next_rpc_recheck_backoff(400.0, 600.0) == 600.0
    assert run_monitoring_worker._next_rpc_recheck_backoff(600.0, 600.0) == 600.0


def test_rpc_recheck_backoff_seconds_configurable(monkeypatch):
    monkeypatch.delenv('MONITORING_RPC_RECHECK_BACKOFF_SECONDS', raising=False)
    assert run_monitoring_worker._rpc_recheck_backoff_seconds() == 60.0
    monkeypatch.setenv('MONITORING_RPC_RECHECK_BACKOFF_SECONDS', '30')
    assert run_monitoring_worker._rpc_recheck_backoff_seconds() == 30.0


def test_rpc_recheck_max_backoff_never_below_initial(monkeypatch):
    monkeypatch.setenv('MONITORING_RPC_RECHECK_BACKOFF_SECONDS', '120')
    monkeypatch.setenv('MONITORING_RPC_RECHECK_MAX_BACKOFF_SECONDS', '60')
    # max must not drop below the initial backoff
    assert run_monitoring_worker._rpc_recheck_max_backoff_seconds() == 120.0


# ---------------------------------------------------------------------------
# 9. Retry-After is respected on a 429 (status page backs off, never hammers)
# ---------------------------------------------------------------------------

def test_rpc_429_with_retry_after_header_records_retry_after(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key')
    err = urllib.error.HTTPError(
        'https://base-mainnet.g.alchemy.com/v2/secret-key', 429, 'Too Many Requests',
        {'Retry-After': '30'}, None,
    )
    with patch.object(sh, 'urlopen', side_effect=err):
        result = sh._check_rpc()
    assert result['status'] == 'failing'
    assert result.get('retry_after') == 30.0
    # The Retry-After value is metadata, not a rendered secret.
    assert 'secret-key' not in str(result)


def test_retry_after_extends_cache_beyond_base_ttl(sh, monkeypatch):
    """A rate-limited probe with Retry-After must hold the cache for at least that
    long, even past the base TTL — so refreshes honor the provider's backoff."""
    sh._reset_rpc_health_cache()
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com/v2/key')
    monkeypatch.setattr(sh, 'RPC_HEALTH_CACHE_TTL_SECONDS', 10)

    clock = {'t': 1000.0}
    monkeypatch.setattr(sh.time, 'monotonic', lambda: clock['t'])

    calls = {'n': 0}

    def _probe():
        calls['n'] += 1
        if calls['n'] == 1:
            r = sh._component('failing', 'rate_limited (HTTP 429)')
            r['retry_after'] = 120.0
            return r
        return sh._component('healthy', 'ok', metric='block #2')

    monkeypatch.setattr(sh, '_check_rpc', _probe)

    sh._cached_base_rpc_health()             # probe #1: 429, retry_after=120 → cached until t+120
    clock['t'] += 11.0                        # past the 10s base TTL, before the 120s Retry-After
    sh._cached_base_rpc_health()
    assert calls['n'] == 1, 'Retry-After must extend the cache beyond the base TTL'

    clock['t'] += 110.0                       # now past the Retry-After window
    sh._cached_base_rpc_health()
    assert calls['n'] == 2, 'after Retry-After elapses, the probe must run again'
