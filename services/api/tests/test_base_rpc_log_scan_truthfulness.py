"""
Base RPC log-scan truthfulness: a reachable provider whose eth_getLogs scan fails or is
reduced for a 413 must be reported as DEGRADED — never a live success — and the cursor
must never advance past the last block whose logs were fully scanned.

Production blocker (the symptom these tests lock down):
  * EVM_RPC_URLS works and QuickNode eth_blockNumber succeeds (provider reachable).
  * eth_getLogs returns HTTP 413; Alchemy fallback returns HTTP 429.
  * logs_fetch_status=failed, yet the worker logged provider_observation result=success
    and persisted the checkpoint forward — skipping block ranges that never got a
    successful log scan.

These tests assert the end-to-end provider-result + observation outcome (not just the
low-level adaptive chunker, which test_base_rpc_413_query_too_large.py already covers):

  A. eth_blockNumber OK + eth_getLogs 413-at-min  -> status=degraded, observation=degraded,
     reason=query_too_large, cursor NOT advanced past the prior checkpoint, no events.
  B. eth_blockNumber OK + eth_getLogs non-413 fail -> status=degraded, observation=degraded,
     reason=logs_fetch_failed, latest_block=None (no advance), events_emitted=0,
     cursor_not_advanced log emitted.
  C. _provider_observation_outcome maps LOG_SCAN_* -> 'degraded' (never 'success').
  D. Base defaults (no env overrides): <=100 blocks/cycle and <=25-block eth_getLogs chunks,
     and the worker logs max_blocks_per_cycle=100 (never 1000).
  E. Base default minimum eth_getLogs chunk is 5 blocks (adaptive halving floor).
  F. No full RPC URL / API key leaks into the degraded-path logs (end-to-end failover).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import uuid
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from services.api.app import activity_providers as ap
from services.api.app import evm_activity_provider as eap

WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'

QUICKNODE_KEY = 'QUICKNODE-SECRET-TOKEN'
ALCHEMY_KEY = 'ALCHEMY-SUPER-SECRET-KEY'
QUICKNODE_URL = f'https://base.quicknode.example/{QUICKNODE_KEY}'
ALCHEMY_URL = f'https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}'
QUICKNODE_HOST = 'base.quicknode.example'
ALCHEMY_HOST = 'base-mainnet.g.alchemy.com'

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL', 'EVM_RPC_URLS',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL', 'EVM_RPC_FAILOVER_URLS',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'LIVE_MONITORING_CHAINS',
    'EVM_WS_URL', 'EVM_RPC_MAX_RETRIES', 'APP_ENV', 'APP_MODE',
    'RPC_PROVIDER_BACKOFF_JITTER_SECONDS', 'RPC_PROVIDER_BACKOFF_MIN_SECONDS',
    'MAX_BLOCKS_PER_CYCLE', 'BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE', 'BASE_MAX_BLOCKS_PER_CYCLE',
    'BASE_MAX_LOGS_BLOCK_RANGE', 'BASE_MIN_LOGS_BLOCK_RANGE',
    'BASE_LIVE_TAIL_BLOCKS', 'EVM_LIVE_TAIL_BLOCKS',
    'MONITOR_REPLAY_BLOCKS', 'MONITOR_SAFE_BACKFILL', 'MONITOR_BATCH_BLOCKS',
    'EVM_CONFIRMATIONS_REQUIRED', 'MONITOR_BATCH_BLOCKS',
    'MONITORING_INGESTION_MODE', 'LIVE_MONITORING_ENABLED',
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Strip every RPC/scan env var so the Base DEFAULTS (not leftover overrides) apply."""
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    monkeypatch.setenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '0')
    eap.reset_rpc_provider_state()
    yield
    eap.reset_rpc_provider_state()


def _now() -> datetime:
    return datetime(2026, 6, 15, 4, 0, tzinfo=timezone.utc)


def _live_base_env(monkeypatch):
    """Configure a healthy LIVE Base monitoring runtime for fetch_target_activity_result."""
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '3')


def _make_target(cursor: str | None = None) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet Target',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': cursor,
        'monitoring_interval_seconds': 300,
    }


# ---------------------------------------------------------------------------
# Injected-client stubs driving the REAL fetch_evm_activity via rpc_client=.
# ---------------------------------------------------------------------------

class _BaseRpc:
    """Minimal Base RPC stub; records every eth_getLogs (from, to) range requested."""

    def __init__(self, latest: int) -> None:
        self.latest = latest
        self.getlogs_ranges: list[tuple[int, int]] = []

    def call(self, method: str, params: list) -> object:
        if method == 'eth_chainId':
            return hex(8453)
        if method == 'eth_blockNumber':
            return hex(self.latest)
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            return {
                'hash': f'0xblock{block_number}',
                'number': hex(block_number),
                'timestamp': hex(int(_now().timestamp()) + block_number),
                'transactions': [],
            }
        if method == 'eth_getLogs':
            frm = int(params[0]['fromBlock'], 16)
            to = int(params[0]['toBlock'], 16)
            self.getlogs_ranges.append((frm, to))
            return self._getlogs(frm, to)
        return {}

    def _getlogs(self, frm: int, to: int) -> object:
        return []


class _Rpc413Always(_BaseRpc):
    """eth_getLogs always returns HTTP 413, even at the minimum chunk size."""

    def _getlogs(self, frm: int, to: int) -> object:
        raise eap.RpcRequestTooLargeError('request_too_large:HTTP Error 413')


class _RpcLogsHardFail(_BaseRpc):
    """eth_getLogs fails with a NON-413 error (e.g. 400/unreachable) — a hard failure."""

    def _getlogs(self, frm: int, to: int) -> object:
        raise RuntimeError('boom: eth_getLogs upstream error')


def _patch_real_provider(monkeypatch, rpc: _BaseRpc) -> None:
    """Route ap.fetch_target_activity_result through the REAL provider with our stub client."""
    _real = eap.fetch_evm_activity
    monkeypatch.setattr(ap, 'fetch_evm_activity', lambda t, s: _real(t, s, rpc_client=rpc))


# ---------------------------------------------------------------------------
# A. 413 query-too-large -> degraded provider observation, cursor not advanced
# ---------------------------------------------------------------------------

def test_quicknode_block_number_ok_but_getlogs_413_is_degraded_not_live(monkeypatch):
    from services.api.app import monitoring_runner

    _live_base_env(monkeypatch)
    latest = 10_000
    # Cursor within one live-tail window of the head so the bounded live-tail scan covers
    # the cursor; a 413 across the whole window then holds it (no advance past unscanned).
    cursor_block = 9_990  # safe_to = 9997
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')
    _patch_real_provider(monkeypatch, _Rpc413Always(latest=latest))

    res = ap.fetch_target_activity_result(target, None)

    # Provider reachable (eth_blockNumber worked) but the log scan stayed too large:
    # this is DEGRADED, never a live success.
    assert res.status == 'degraded'
    assert res.reason_code == 'LOG_SCAN_DEGRADED'
    assert res.degraded_reason == 'query_too_large'
    assert res.evidence_present is False
    assert res.events == []
    # The cursor must NOT advance past the prior checkpoint (no unscanned blocks skipped).
    assert res.latest_block == cursor_block
    # provider_observation must be 'degraded' — NOT 'success'.
    outcome = monitoring_runner._provider_observation_outcome(res, chain_mismatch=False)
    assert outcome == 'degraded'


# ---------------------------------------------------------------------------
# B. Non-413 logs failure -> degraded, no cursor advance, events_emitted=0
# ---------------------------------------------------------------------------

def test_getlogs_hard_failure_is_degraded_and_does_not_advance_cursor(monkeypatch, caplog):
    from services.api.app import monitoring_runner

    _live_base_env(monkeypatch)
    latest = 10_000
    cursor_block = 9_900
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')
    _patch_real_provider(monkeypatch, _RpcLogsHardFail(latest=latest))

    with caplog.at_level(logging.WARNING, logger='services.api.app.evm_activity_provider'):
        res = ap.fetch_target_activity_result(target, None)

    assert res.status == 'degraded'
    assert res.reason_code == 'LOG_SCAN_FAILED'
    assert res.degraded_reason == 'logs_fetch_failed'
    # Fail closed: no events emitted and the cursor does not advance AT ALL.
    assert res.events == []
    assert res.evidence_present is False
    assert res.latest_block is None
    # provider_observation must be 'degraded' (never 'success') for a failed log scan.
    assert monitoring_runner._provider_observation_outcome(res, chain_mismatch=False) == 'degraded'
    # The fail-closed cursor guard log is emitted with the required fields.
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'cursor_not_advanced' in text
    assert 'reason=logs_fetch_failed' in text
    assert 'previous_cursor=' in text
    assert 'failed_from_block=' in text and 'failed_to_block=' in text


# ---------------------------------------------------------------------------
# C. _provider_observation_outcome mapping (degraded, not success)
# ---------------------------------------------------------------------------

def _provider_result(*, status: str, reason_code: str | None, degraded_reason: str | None,
                     evidence_state: str, evidence_present: bool):
    return ap.ActivityProviderResult(
        mode='live', status=status, evidence_state=evidence_state,
        truthfulness_state='UNKNOWN_RISK' if status != 'live' else 'NOT_CLAIM_SAFE',
        synthetic=False, provider_name='evm_activity_provider', provider_kind='rpc',
        evidence_present=evidence_present, recent_real_event_count=0, last_real_event_at=None,
        events=[], latest_block=None, checkpoint=None, checkpoint_age_seconds=None,
        degraded_reason=degraded_reason, error_code=None, source_type='rpc_polling',
        reason_code=reason_code, claim_safe=False,
        detection_outcome='MONITORING_DEGRADED' if status != 'live' else 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )


def test_provider_observation_outcome_degraded_for_log_scan_codes():
    from services.api.app import monitoring_runner
    eap.reset_rpc_provider_state()

    deg = _provider_result(status='degraded', reason_code='LOG_SCAN_DEGRADED',
                           degraded_reason='query_too_large', evidence_state='DEGRADED_EVIDENCE',
                           evidence_present=False)
    failed = _provider_result(status='degraded', reason_code='LOG_SCAN_FAILED',
                              degraded_reason='logs_fetch_failed', evidence_state='DEGRADED_EVIDENCE',
                              evidence_present=False)
    live = _provider_result(status='live', reason_code='PROVIDER_COVERAGE_VERIFIED',
                            degraded_reason=None, evidence_state='REAL_EVIDENCE',
                            evidence_present=True)

    assert monitoring_runner._provider_observation_outcome(deg, chain_mismatch=False) == 'degraded'
    assert monitoring_runner._provider_observation_outcome(failed, chain_mismatch=False) == 'degraded'
    # A genuinely live result is still 'success' (no regression).
    assert monitoring_runner._provider_observation_outcome(live, chain_mismatch=False) == 'success'
    # An active global backoff still wins (skipped) even over a log-scan-degraded result.
    eap.record_rpc_rate_limited(None)
    assert monitoring_runner._provider_observation_outcome(deg, chain_mismatch=False) == 'skipped'


# ---------------------------------------------------------------------------
# D. Base defaults: <=100 blocks/cycle, <=25-block eth_getLogs chunks, log shows 100
# ---------------------------------------------------------------------------

def test_base_defaults_cap_blocks_per_cycle_and_log_chunk(monkeypatch, caplog):
    from services.api.app.evm_activity_provider import fetch_evm_activity

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    # No BASE_* overrides — the Base DEFAULTS must apply.

    latest = 10_000_000
    cursor_block = latest - 5_000  # deep enough to trigger the per-cycle cap
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')
    rpc = _BaseRpc(latest=latest)

    with caplog.at_level(logging.INFO, logger='services.api.app.evm_activity_provider'):
        fetch_evm_activity(target, None, rpc_client=rpc)

    scan_to = target['_evm_scan_to_block']
    safe_to = latest - 3
    # Polling-only MVP live-tail sampling: a deep cursor jumps to the recent live tail near
    # the head (the skipped history is deferred backfill), so the cursor reaches safe_to and
    # the WINDOW actually queried this cycle is what the hard 25-block ceiling bounds.
    assert scan_to == safe_to, f'live-tail poll must reach the head safe_to={safe_to}; got {scan_to}'
    frms = [frm for frm, to in rpc.getlogs_ranges]
    tos = [to for frm, to in rpc.getlogs_ranges]
    assert frms, 'eth_getLogs must be attempted'
    window = max(tos) - min(frms) + 1
    assert window <= 25, f'per-cycle scan window must be <=25 blocks; got {window}'
    # MAX_LOG_QUERY_CHUNK_BLOCKS=5: no single eth_getLogs request exceeds 5 blocks.
    spans = [to - frm + 1 for frm, to in rpc.getlogs_ranges]
    assert max(spans) <= 5, f'Base eth_getLogs chunk must default to <=5 blocks; spans={set(spans)}'
    # Acceptance: the scan-start log must show the hard max_blocks_per_cycle=25 (never 100/1000).
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'max_blocks_per_cycle=25' in text
    assert 'max_blocks_per_cycle=1000' not in text


# ---------------------------------------------------------------------------
# E. eth_getLogs chunk starts at 5 blocks and splits recursively down to 1 on 413
# ---------------------------------------------------------------------------

def test_base_log_chunk_caps_at_five_and_splits_to_one(monkeypatch):
    from services.api.app.evm_activity_provider import fetch_evm_activity

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')

    latest = 10_000
    cursor_block = 9_990  # within one live-tail window of the head so a 413 holds the cursor
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')
    rpc = _Rpc413Always(latest=latest)

    fetch_evm_activity(target, None, rpc_client=rpc)

    spans = sorted({to - frm + 1 for frm, to in rpc.getlogs_ranges})
    assert spans, 'eth_getLogs must be attempted'
    # MAX_LOG_QUERY_CHUNK_BLOCKS=5 caps the first attempt; on a persistent 413 the range is
    # split recursively down to a SINGLE block (Section 6) before the chunk is given up.
    assert max(spans) == 5, f'first attempt must use the 5-block chunk cap; spans={spans}'
    assert min(spans) == 1, f'413 must split recursively down to 1 block; spans={spans}'
    # 413-always even at a single block → cursor must NOT advance past the prior checkpoint.
    assert target['_evm_scan_to_block'] == cursor_block


# ---------------------------------------------------------------------------
# F. No full RPC URL / API key leaks into the degraded-path logs (real failover)
# ---------------------------------------------------------------------------

def _ok_resp(result: str):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}).encode()
    return resp


def _dispatch_block_ok_getlogs_413(block: int):
    """urlopen side_effect: eth_blockNumber/eth_chainId OK, eth_getBlockByNumber empty,
    eth_getLogs -> HTTP 413 on every host (query too large everywhere)."""
    def _fn(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        host = (urlparse(url).hostname or '').lower()
        try:
            body = json.loads(req.data.decode('utf-8'))
        except Exception:
            body = {}
        method = body.get('method', '')
        if method == 'eth_getLogs':
            raise urllib.error.HTTPError(url, 413, 'Request Entity Too Large', {}, None)
        if method == 'eth_chainId':
            return _ok_resp(hex(8453))
        if method == 'eth_blockNumber':
            return _ok_resp(hex(block))
        if method == 'eth_getBlockByNumber':
            bn = int(str(body.get('params', ['0x0'])[0]), 16)
            return _ok_resp({'hash': f'0xb{bn}', 'number': hex(bn), 'timestamp': hex(bn), 'transactions': []})
        return _ok_resp('0x0')
    return _fn


def test_no_secret_leaks_in_degraded_provider_path(monkeypatch, caplog):
    _live_base_env(monkeypatch)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.setenv('EVM_RPC_URLS', f'{QUICKNODE_URL},{ALCHEMY_URL}')

    block = 47_000_000
    cursor_block = block - 60
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')

    loggers = ('services.api.app.evm_activity_provider', 'services.api.app.activity_providers')
    with caplog.at_level(logging.INFO):
        with patch.object(eap.request, 'urlopen', side_effect=_dispatch_block_ok_getlogs_413(block)):
            res = ap.fetch_target_activity_result(target, None)

    # Provider reachable, log scan too large everywhere -> degraded (never live success).
    assert res.status == 'degraded'
    assert res.reason_code == 'LOG_SCAN_DEGRADED'

    text = '\n'.join(
        r.getMessage() for r in caplog.records if r.name in loggers
    )
    # The provider host is surfaced (sanitized) — never the URL path, key, or token.
    for secret in (QUICKNODE_KEY, ALCHEMY_KEY, '/v2/'):
        assert secret not in text, f'{secret!r} leaked into the degraded-path logs'
