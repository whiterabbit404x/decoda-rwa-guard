"""
Base RPC pressure-reduction guards (extends test_rpc_health_caching_and_backoff).

These cover the deltas added to reduce RPC pressure and make rate limiting clear:
- Structured probe logs carry cache_hit and retry_after_seconds.
- A cached /system-health response replays the probe log marked cache_hit=true
  WITHOUT firing a second live RPC call.
- Timeout / invalid-key failures surface the mandated operator sentences.
- Worker poll cadence honors EVM_POLLING_INTERVAL_SECONDS (default 60s), with
  MONITORING_WORKER_INTERVAL_SECONDS kept as a legacy alias.
- No RPC URL / API key ever leaks into the served component or the logs.
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
_INTERVAL_ENV_VARS = ('EVM_POLLING_INTERVAL_SECONDS', 'MONITORING_WORKER_INTERVAL_SECONDS')


def _load_sh():
    module_name = f'system_health_pressure_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, SH_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def sh():
    # Fresh module per test so the module-level probe cache never bleeds across tests.
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
# cache_hit is logged: false on the live probe, true on a served cache hit,
# and the hit must NOT fire a second live RPC call.
# ---------------------------------------------------------------------------

def test_cache_hit_logged_true_on_hit_false_on_live_probe(sh, monkeypatch, caplog):
    sh._reset_rpc_health_cache()
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key')
    assert sh.RPC_HEALTH_CACHE_TTL_SECONDS > 0, 'default TTL must enable caching'

    mock_urlopen = MagicMock(return_value=_ok_response())
    with caplog.at_level(logging.INFO):
        with patch.object(sh, 'urlopen', mock_urlopen):
            sh._cached_base_rpc_health()            # miss → one live probe
            after_first = mock_urlopen.call_count
            sh._cached_base_rpc_health()            # hit → served from cache
            after_second = mock_urlopen.call_count

    assert after_first == 1, 'first call must run exactly one live probe'
    assert after_second == 1, 'a cache hit must not fire a second live RPC call'

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'cache_hit=false' in text, 'the live probe must log cache_hit=false'
    assert 'cache_hit=true' in text, 'the cached hit must replay a log line marked cache_hit=true'
    assert 'secret-key' not in text
    assert '/v2/' not in text


# ---------------------------------------------------------------------------
# retry_after_seconds is logged on a 429 carrying a Retry-After header.
# ---------------------------------------------------------------------------

def test_retry_after_seconds_appears_in_logs_on_429(sh, monkeypatch, caplog):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')
    err = urllib.error.HTTPError(
        'https://base-mainnet.g.alchemy.com/v2/key', 429, 'Too Many Requests',
        {'Retry-After': '42'}, None,
    )
    with caplog.at_level(logging.INFO):
        with patch.object(sh, 'urlopen', side_effect=err):
            sh._check_rpc()

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'rpc_status=rate_limited' in text
    assert 'retry_after_seconds=42' in text, f'Retry-After must be logged: {text}'


# ---------------------------------------------------------------------------
# Mandated operator sentences: timeout and invalid key / forbidden.
# ---------------------------------------------------------------------------

def test_timeout_message_uses_mandated_sentence(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base.example.com/v2/super-secret-key')
    with patch.object(sh, 'urlopen', side_effect=socket.timeout('timed out')):
        result = sh._check_rpc()

    assert result['status'] == 'failing'
    assert 'Base RPC request timed out.' in result['message']
    # Secret-free.
    assert 'super-secret-key' not in str(result)
    assert '/v2/' not in result['message']


def test_invalid_key_message_uses_mandated_sentence(sh, monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key-abc')
    err = urllib.error.HTTPError(
        'https://base-mainnet.g.alchemy.com/v2/secret-key-abc', 403, 'Forbidden', {}, None,
    )
    with patch.object(sh, 'urlopen', side_effect=err):
        result = sh._check_rpc()

    assert result['status'] == 'failing'
    # The mandated invalid-key sentence is surfaced as the operator action; the
    # message keeps the stable generic lead plus the categorized reason.
    assert result['action'] == 'RPC provider rejected the request. Check provider key or endpoint.'
    assert '403' in result['message'] or 'unauthorized' in result['message'].lower()
    assert 'secret-key-abc' not in str(result)


def test_invalid_key_jsonrpc_error_uses_mandated_sentence(sh, monkeypatch):
    """A JSON-RPC body error (not an HTTP status) for an unauthorized key must also
    surface the mandated sentence."""
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key-abc')

    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(
        {'jsonrpc': '2.0', 'id': 1, 'error': {'code': -32000, 'message': 'unauthorized: invalid key'}}
    ).encode()

    with patch.object(sh, 'urlopen', return_value=resp):
        result = sh._check_rpc()

    assert result['status'] == 'failing'
    assert result['action'] == 'RPC provider rejected the request. Check provider key or endpoint.'
    assert 'secret-key-abc' not in str(result)


# ---------------------------------------------------------------------------
# No RPC URL / key leaks into the served component or the replayed cache log.
# ---------------------------------------------------------------------------

def test_no_secret_in_cached_result_or_replayed_log(sh, monkeypatch, caplog):
    sh._reset_rpc_health_cache()
    _clear_rpc_env(monkeypatch)
    secret = 'cached-ultra-secret-key'
    monkeypatch.setenv('EVM_RPC_URL', f'https://base-mainnet.g.alchemy.com/v2/{secret}')

    with caplog.at_level(logging.INFO):
        with patch.object(sh, 'urlopen', return_value=_ok_response()):
            first = sh._cached_base_rpc_health()
            second = sh._cached_base_rpc_health()   # cache hit → replay log line

    assert secret not in str(first)
    assert secret not in str(second)
    # The served component must never carry the resolved URL or internal hints.
    assert '/v2/' not in str(second)
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert secret not in text
    assert '/v2/' not in text


# ---------------------------------------------------------------------------
# Worker poll cadence: EVM_POLLING_INTERVAL_SECONDS resolves through the SINGLE
# canonical polling interval (default 300s MVP) shared with the per-target default and
# the startup report, so the worker can never report one interval while polling at
# another. 300s >= the previous 60s default, so RPC pressure is not increased.
# ---------------------------------------------------------------------------

def test_polling_interval_defaults_to_canonical(monkeypatch):
    from services.api.app import monitoring_runner as _mr
    for var in _INTERVAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv('MIN_EVM_POLLING_INTERVAL_SECONDS', raising=False)
    expected = float(_mr.DEFAULT_CANONICAL_POLLING_INTERVAL_SECONDS)
    assert expected == 300.0
    # The worker loop default and the canonical target default resolve to the SAME value.
    assert run_monitoring_worker._resolve_polling_interval_seconds() == expected
    assert float(_mr.canonical_polling_interval_seconds()) == expected


def test_evm_polling_interval_overrides_default(monkeypatch):
    monkeypatch.delenv('MONITORING_WORKER_INTERVAL_SECONDS', raising=False)
    monkeypatch.setenv('EVM_POLLING_INTERVAL_SECONDS', '90')
    assert run_monitoring_worker._resolve_polling_interval_seconds() == 90.0
    # parse_args() must pick the resolved value up as the --interval-seconds default.
    monkeypatch.setattr(sys, 'argv', ['run_monitoring_worker'])
    assert run_monitoring_worker.parse_args().interval_seconds == 90.0


def test_evm_polling_interval_takes_precedence_over_legacy_alias(monkeypatch):
    monkeypatch.setenv('EVM_POLLING_INTERVAL_SECONDS', '75')
    monkeypatch.setenv('MONITORING_WORKER_INTERVAL_SECONDS', '120')
    assert run_monitoring_worker._resolve_polling_interval_seconds() == 75.0


def test_legacy_interval_alias_still_works(monkeypatch):
    monkeypatch.delenv('EVM_POLLING_INTERVAL_SECONDS', raising=False)
    monkeypatch.setenv('MONITORING_WORKER_INTERVAL_SECONDS', '30')
    assert run_monitoring_worker._resolve_polling_interval_seconds() == 30.0


def test_invalid_interval_falls_back_to_next_source(monkeypatch):
    # A non-numeric primary override must not crash the worker; fall through.
    monkeypatch.setenv('EVM_POLLING_INTERVAL_SECONDS', 'not-a-number')
    monkeypatch.setenv('MONITORING_WORKER_INTERVAL_SECONDS', '45')
    assert run_monitoring_worker._resolve_polling_interval_seconds() == 45.0


# ---------------------------------------------------------------------------
# Production worker sleep floor: never sleep 1s or 30s when the interval is 60s.
# ---------------------------------------------------------------------------

def test_min_worker_sleep_is_60_in_production(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('MIN_WORKER_SLEEP_SECONDS', raising=False)
    assert run_monitoring_worker._min_worker_sleep_seconds() == 60.0


def test_min_worker_sleep_is_1_in_development(monkeypatch):
    monkeypatch.delenv('APP_ENV', raising=False)
    monkeypatch.delenv('APP_MODE', raising=False)
    monkeypatch.delenv('MIN_WORKER_SLEEP_SECONDS', raising=False)
    assert run_monitoring_worker._min_worker_sleep_seconds() == 1.0


def test_production_sleep_never_below_60_when_no_due_work():
    # No due work and the next target is "due in 1s" — production must NOT sleep 1s.
    next_sleep = run_monitoring_worker._compute_next_sleep_seconds(
        worker_interval_seconds=60,
        effective_due_count=0,
        soonest_due_in_seconds=1,
        min_sleep_seconds=60.0,
    )
    assert next_sleep == 60.0


def test_production_sleep_is_full_interval_not_30s_cap_with_due_work():
    # Due work present under a 60s interval — production must sleep 60s, not the 30s cap.
    next_sleep = run_monitoring_worker._compute_next_sleep_seconds(
        worker_interval_seconds=60,
        effective_due_count=3,
        soonest_due_in_seconds=None,
        min_sleep_seconds=60.0,
    )
    assert next_sleep == 60.0


def test_min_worker_sleep_rises_to_min_evm_polling_interval_in_production(monkeypatch):
    # Production must never wake more often than the per-target minimum poll interval:
    # MIN_EVM_POLLING_INTERVAL_SECONDS=120 raises the worker sleep floor to 120s.
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('MIN_WORKER_SLEEP_SECONDS', raising=False)
    monkeypatch.setenv('MIN_EVM_POLLING_INTERVAL_SECONDS', '120')
    assert run_monitoring_worker._min_worker_sleep_seconds() == 120.0


def test_min_evm_polling_interval_floor_not_applied_in_development(monkeypatch):
    # Local/dev keeps the fast 1s cadence (for --once runs / quick iteration) even when
    # MIN_EVM_POLLING_INTERVAL_SECONDS is high — the floor is production-only.
    monkeypatch.delenv('APP_ENV', raising=False)
    monkeypatch.delenv('APP_MODE', raising=False)
    monkeypatch.delenv('MIN_WORKER_SLEEP_SECONDS', raising=False)
    monkeypatch.setenv('MIN_EVM_POLLING_INTERVAL_SECONDS', '120')
    assert run_monitoring_worker._min_worker_sleep_seconds() == 1.0


def test_production_next_sleep_never_below_min_evm_polling_interval(monkeypatch):
    # End to end: a 60s worker interval with a 120s per-target minimum must sleep >= 120s.
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('MIN_WORKER_SLEEP_SECONDS', raising=False)
    monkeypatch.setenv('MIN_EVM_POLLING_INTERVAL_SECONDS', '120')
    next_sleep = run_monitoring_worker._compute_next_sleep_seconds(
        worker_interval_seconds=60,
        effective_due_count=3,
        soonest_due_in_seconds=None,
        min_sleep_seconds=run_monitoring_worker._min_worker_sleep_seconds(),
    )
    assert next_sleep == 120.0


def test_startup_logs_effective_polling_interval_and_source(monkeypatch, caplog):
    """The worker must log the effective poll cadence and its source at startup,
    secret-free, so operators can verify it from logs after deploy."""
    for var in _RPC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in _INTERVAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base-mainnet.example.com/v2/secret-key-xyz')
    monkeypatch.setenv('EVM_POLLING_INTERVAL_SECONDS', '90')
    run_monitoring_worker._resolve_worker_enabled_env()
    # Keep the startup RPC self-check off the network.
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda *a, **k: {'ok': True, 'chain_id_int': 8453, 'block_number_int': 1, 'error': None},
    )

    logger = logging.getLogger('test_startup_interval')
    with caplog.at_level(logging.INFO, logger='test_startup_interval'):
        run_monitoring_worker._log_startup_provider_status(logger)

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'startup_polling_interval polling_interval_seconds=90' in text
    assert 'source=EVM_POLLING_INTERVAL_SECONDS' in text
    # No secret / path ever printed.
    assert 'secret-key-xyz' not in text
    assert '/v2/' not in text
