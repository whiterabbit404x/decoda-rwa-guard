"""Datto USDC contract scanner bounds — production RPC-safety regression suite.

The first restored Datto (Base USDC) poll scanned a 2,001-block range and then issued
~1 eth_getTransactionByHash per Transfer log (119,163 logs → an eth_getTransactionByHash
storm), spiking Alchemy CU. These tests lock down the fix so the runaway is structurally
impossible:

  * a hard per-poll budget (blocks / logs / tx-enrichments / RPC calls / seconds),
  * a bounded live-tail start for new targets,
  * a defensive range invariant checked immediately before every eth_getLogs,
  * local ERC-20 decoding (no per-log tx lookup),
  * a process-wide RPC circuit breaker,
  * historical backfill disabled by default in the polling-only MVP.

The trailing numbered comments map to the task's required test list (1–20 + regression).
"""
from __future__ import annotations

import time
import uuid

import pytest

from services.api.app import evm_activity_provider as eap
from services.api.app.evm_activity_provider import (
    APPROVAL_TOPIC,
    TRANSFER_TOPIC,
    PollBudget,
    PollBudgetExhausted,
    RpcCircuitBreakerTripped,
    ScanRangeInvariantError,
    _assert_getlogs_range_within_budget,
    _decode_transfer_log,
    _dedupe_decoded_logs,
    _plan_tx_enrichments,
    fetch_evm_activity,
    initial_live_tail_blocks,
    historical_backfill_enabled,
    load_poll_budget,
    reset_rpc_circuit_breaker,
    rpc_circuit_breaker_snapshot,
)

# Base mainnet USDC — the production monitored Datto contract (task PRODUCTION FACTS).
USDC = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
WORKSPACE_ID = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'


def _topic_addr(addr: str) -> str:
    return '0x' + ('0' * 24) + addr[2:]


HOLDER_A = '0x1111111111111111111111111111111111111111'
HOLDER_B = '0x2222222222222222222222222222222222222222'


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    """A healthy Base RPC runtime with the production-default poll budget."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://base-rpc.example')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '3')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '2')
    monkeypatch.setenv('MONITOR_BATCH_BLOCKS', '25')
    monkeypatch.delenv('EVM_WS_URL', raising=False)
    # Production-default budgets (Section 5) unless a test overrides them.
    for name in (
        'MAX_BLOCKS_PER_TARGET_PER_CYCLE', 'MAX_LOGS_PER_TARGET_PER_CYCLE',
        'MAX_TX_ENRICHMENTS_PER_TARGET_PER_CYCLE', 'MAX_RPC_CALLS_PER_TARGET_PER_CYCLE',
        'MAX_POLL_DURATION_SECONDS', 'MAX_LOG_QUERY_CHUNK_BLOCKS',
        'MONITORING_RPC_MAX_CALLS_PER_MINUTE', 'INITIAL_LIVE_TAIL_BLOCKS',
        'HISTORICAL_BACKFILL_ENABLED', 'EVM_TX_ENRICHMENT_ENABLED',
    ):
        monkeypatch.delenv(name, raising=False)
    reset_rpc_circuit_breaker()
    yield
    reset_rpc_circuit_breaker()


def _contract_target(cursor: str | None = None) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': WORKSPACE_ID,
        'name': 'Datto USDC Monitor',
        'target_type': 'contract',
        'chain_network': 'base',
        'contract_identifier': USDC,
        'wallet_address': None,
        'monitoring_checkpoint_cursor': cursor,
        'monitoring_interval_seconds': 900,
    }


class _UsdcRpc:
    """RPC stub for a busy USDC contract: every block emits ``logs_per_block`` Transfer
    logs, and every eth_getBlockByNumber returns a block whose txs route through a DEX
    (contract is never tx.to). Records every call for assertions."""

    def __init__(self, latest: int, *, logs_per_block: int = 60) -> None:
        self.latest = latest
        self.logs_per_block = logs_per_block
        self.calls: list[tuple[str, list]] = []
        self.getlogs_ranges: list[tuple[int, int]] = []

    def _log(self, block: int, i: int) -> dict:
        return {
            'transactionHash': f'0x{block:x}{i:04x}',
            'logIndex': hex(i),
            'transactionIndex': hex(i),
            'blockNumber': hex(block),
            'blockHash': f'0xblk{block}',
            'address': USDC,
            'topics': [TRANSFER_TOPIC, _topic_addr(HOLDER_A), _topic_addr(HOLDER_B)],
            'data': hex(5_000_000 + i),
        }

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == 'eth_chainId':
            return hex(8453)
        if method == 'eth_blockNumber':
            return hex(self.latest)
        if method == 'eth_getLogs':
            spec = params[0] if params else {}
            frm = int(str(spec.get('fromBlock')), 16)
            to = int(str(spec.get('toBlock')), 16)
            self.getlogs_ranges.append((frm, to))
            if str(spec.get('address') or '').lower() != USDC:
                return []
            out = []
            for b in range(frm, to + 1):
                out.extend(self._log(b, i) for i in range(self.logs_per_block))
            return out
        if method == 'eth_getBlockByNumber':
            b = int(str(params[0]), 16)
            return {
                'hash': f'0xblk{b}', 'number': hex(b),
                'timestamp': hex(1_700_000_000 + b),
                'transactions': [{
                    'hash': f'0xtx{b}', 'from': HOLDER_A,
                    'to': '0x9999999999999999999999999999999999999999',
                    'value': hex(0), 'input': '0x38ed1739',
                    'blockNumber': hex(b), 'blockHash': f'0xblk{b}',
                }],
            }
        if method == 'eth_getBlockByHash':
            return {'timestamp': hex(1_700_000_000)}
        if method == 'eth_getTransactionByHash':
            return {'hash': params[0], 'from': HOLDER_A, 'to': USDC}
        return {}


def _count(rpc: _UsdcRpc, method: str) -> int:
    return sum(1 for m, _ in rpc.calls if m == method)


# ===========================================================================
# 1. max_blocks_per_cycle=25 cannot query 26+ blocks (defensive invariant)
# ===========================================================================

def test_getlogs_range_invariant_rejects_26_blocks():
    with pytest.raises(ScanRangeInvariantError):
        _assert_getlogs_range_within_budget(1000, 1025, 25, target_id='t')  # 26 blocks


def test_getlogs_range_invariant_allows_exactly_25_blocks():
    _assert_getlogs_range_within_budget(1000, 1024, 25, target_id='t')  # 25 blocks — ok


# ===========================================================================
# 2. The final eth_getLogs arguments use the bounded range
# ===========================================================================

def test_final_getlogs_arguments_never_exceed_chunk_cap():
    rpc = _UsdcRpc(latest=48_960_245)
    fetch_evm_activity(_contract_target(), None, rpc_client=rpc)
    assert rpc.getlogs_ranges, 'eth_getLogs must be issued'
    for frm, to in rpc.getlogs_ranges:
        assert to - frm + 1 <= 5, f'eth_getLogs queried {to - frm + 1} blocks (>5): [{frm},{to}]'


# ===========================================================================
# 3. A target without a cursor starts within 10–25 blocks of the safe head
# ===========================================================================

def test_no_cursor_target_starts_within_live_tail_of_head():
    rpc = _UsdcRpc(latest=48_960_245)  # safe_to = 48_960_242
    fetch_evm_activity(_contract_target(cursor=None), None, rpc_client=rpc)
    frms = [f for f, _ in rpc.getlogs_ranges]
    tos = [t for _, t in rpc.getlogs_ranges]
    safe_to = 48_960_245 - 3
    assert max(tos) == safe_to
    window = max(tos) - min(frms) + 1
    assert 10 <= window <= 25, f'no-cursor start window must be 10–25 blocks; got {window}'


# ===========================================================================
# 4. Normal scheduled polling never starts a 2,000-block backfill
# ===========================================================================

def test_scheduled_poll_never_starts_2000_block_backfill():
    rpc = _UsdcRpc(latest=48_960_242)
    fetch_evm_activity(_contract_target(cursor=None), None, rpc_client=rpc)
    frms = [f for f, _ in rpc.getlogs_ranges]
    tos = [t for _, t in rpc.getlogs_ranges]
    assert max(tos) - min(frms) + 1 <= 25, 'a scheduled poll must never scan a wide backfill'
    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert max(block_calls) - min(block_calls) + 1 <= 25


# ===========================================================================
# 5. ERC-20 Transfer is decoded without eth_getTransactionByHash
# ===========================================================================

def test_erc20_transfer_decoded_locally():
    log = {
        'transactionHash': '0xabc', 'logIndex': hex(3), 'transactionIndex': hex(1),
        'blockNumber': hex(100), 'blockHash': '0xblk', 'address': USDC,
        'topics': [TRANSFER_TOPIC, _topic_addr(HOLDER_A), _topic_addr(HOLDER_B)],
        'data': hex(5_000_000),
    }
    decoded = _decode_transfer_log(log)
    assert decoded is not None
    assert decoded['event_type'] == 'transfer'
    assert decoded['from_address'] == HOLDER_A
    assert decoded['to_address'] == HOLDER_B
    assert decoded['amount'] == str(5_000_000)
    assert decoded['transaction_hash'] == '0xabc'
    assert decoded['log_index'] == 3


def test_full_contract_poll_makes_no_transaction_lookups():
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=60)
    events = fetch_evm_activity(_contract_target(), None, rpc_client=rpc)
    assert _count(rpc, 'eth_getTransactionByHash') == 0, (
        'ordinary USDC Transfer events must be decoded locally — no eth_getTransactionByHash'
    )
    # Transfers were still detected from the receipt logs.
    assert any(e.payload.get('kind_hint') == 'erc20_transfer' for e in events)


# ===========================================================================
# 6. 100,000 Transfer logs do not cause 100,000 transaction requests
# ===========================================================================

def test_100k_logs_do_not_cause_100k_tx_requests(monkeypatch):
    # A single 5-block chunk alone yields 100k+ logs (20k/block); the log budget stops the
    # poll long before anything like 100k tx lookups could occur.
    monkeypatch.setenv('MAX_LOGS_PER_TARGET_PER_CYCLE', '2000')
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=20_000)
    fetch_evm_activity(_contract_target(), None, rpc_client=rpc)
    assert _count(rpc, 'eth_getTransactionByHash') == 0
    assert _count(rpc, 'eth_getLogs') < 100  # never one request per log


# ===========================================================================
# 7. Duplicate transaction hashes are enriched once
# ===========================================================================

def test_duplicate_tx_hashes_enriched_once():
    decoded = [
        {'transaction_hash': '0xaa', 'requires_enrichment': True},
        {'transaction_hash': '0xaa', 'requires_enrichment': True},  # same tx, different log
        {'transaction_hash': '0xbb', 'requires_enrichment': True},
    ]
    plan = _plan_tx_enrichments(decoded, max_enrichments=25)
    assert plan == ['0xaa', '0xbb'], 'each transaction hash must be enriched at most once'


# ===========================================================================
# 8. Non-rule-matching logs receive no transaction enrichment
# ===========================================================================

def test_non_rule_matching_logs_are_not_enriched():
    decoded = [
        {'transaction_hash': '0xaa', 'requires_enrichment': False},
        {'transaction_hash': '0xbb', 'requires_enrichment': False},
    ]
    assert _plan_tx_enrichments(decoded, max_enrichments=25) == []


# ===========================================================================
# 9. Transaction enrichment stops at the configured cap
# ===========================================================================

def test_enrichment_stops_at_configured_cap():
    decoded = [{'transaction_hash': f'0x{i:02x}', 'requires_enrichment': True} for i in range(100)]
    plan = _plan_tx_enrichments(decoded, max_enrichments=25)
    assert len(plan) == 25, 'enrichment must stop at the configured cap'


# ===========================================================================
# 10. Total RPC calls stop at the configured cap
# ===========================================================================

def test_total_rpc_calls_stop_at_cap(monkeypatch):
    monkeypatch.setenv('MAX_RPC_CALLS_PER_TARGET_PER_CYCLE', '10')
    # Force a large window so the block scan would exceed 10 calls without the cap.
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'true')
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '2000')
    monkeypatch.setenv('MAX_BLOCKS_PER_TARGET_PER_CYCLE', '25')
    target = _contract_target(cursor=None)
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=1)
    fetch_evm_activity(target, None, rpc_client=rpc)
    total = len(rpc.calls)
    # eth_blockNumber (provider check) is not budget-gated; every scan-phase call is. The
    # budget stops issuing once 10 scan-phase calls are used.
    assert total <= 12, f'scan-phase RPC calls must stop at the cap; issued {total}'
    assert target['_evm_poll_terminal_status'] == 'partial'
    assert target['_evm_poll_stopped_reason'] == 'rpc_budget'


# ===========================================================================
# 11. Poll duration stops at the configured timeout
# ===========================================================================

def test_poll_duration_budget_stops_poll():
    budget = PollBudget(max_duration_seconds=0.0, started_at=time.monotonic() - 100)
    with pytest.raises(PollBudgetExhausted) as exc:
        budget.before_rpc(target_id='t')
    assert exc.value.event == 'monitoring_poll_time_budget_exhausted'


# ===========================================================================
# 12. Large log ranges are split into smaller chunks
# ===========================================================================

def test_large_log_ranges_split_into_chunks():
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=60)
    fetch_evm_activity(_contract_target(), None, rpc_client=rpc)
    # The 25-block cycle is queried as multiple <=5-block eth_getLogs requests, never one.
    assert len(rpc.getlogs_ranges) >= 2
    assert all(to - frm + 1 <= 5 for frm, to in rpc.getlogs_ranges)


# ===========================================================================
# 13. Cursor advances only after successful persistence (never past unscanned)
# ===========================================================================

def test_cursor_holds_when_log_scan_fails():
    class _FailLogsRpc(_UsdcRpc):
        def call(self, method, params):
            if method == 'eth_getLogs':
                self.calls.append((method, params))
                raise RuntimeError('HTTP Error 429: rate limited')
            return super().call(method, params)

    # Cursor near the head so the failed live-tail holds it (no advance past unscanned).
    latest = 10_000
    cursor = latest - 3 - 5  # within the live-tail window
    rpc = _FailLogsRpc(latest=latest)
    target = _contract_target(cursor=f'{cursor}:checkpoint:-1')
    fetch_evm_activity(target, None, rpc_client=rpc)
    assert target['_evm_scan_to_block'] == cursor, 'cursor must not advance when the log scan failed'


# ===========================================================================
# 14. Provider health is probed before event scanning
# ===========================================================================

def test_provider_check_precedes_event_scan():
    rpc = _UsdcRpc(latest=48_960_245)
    fetch_evm_activity(_contract_target(), None, rpc_client=rpc)
    methods = [m for m, _ in rpc.calls]
    assert 'eth_blockNumber' in methods and 'eth_getLogs' in methods
    assert methods.index('eth_blockNumber') < methods.index('eth_getLogs'), (
        'the bounded provider-health check (eth_blockNumber) must run before the event scan'
    )


# ===========================================================================
# 16. Runtime freshness follows the 900-second interval (canonical, not 300)
# ===========================================================================

def test_freshness_window_follows_canonical_900s(monkeypatch):
    from services.api.app import monitoring_runner as mr
    monkeypatch.delenv('MONITOR_POLL_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('EVM_POLLING_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('MONITORING_WORKER_INTERVAL_SECONDS', raising=False)
    # The canonical cadence is 900s; the derived stable-poll stale threshold is two full
    # cycles (1800s), never the legacy 300s window.
    assert mr.canonical_polling_interval_seconds() == 900
    assert mr.canonical_stable_poll_stale_threshold_seconds() >= 1800


# ===========================================================================
# 18. Process-wide RPC circuit breaker stops runaway requests
# ===========================================================================

def test_circuit_breaker_stops_runaway(monkeypatch):
    monkeypatch.setenv('MONITORING_RPC_MAX_CALLS_PER_MINUTE', '5')
    monkeypatch.setenv('MAX_RPC_CALLS_PER_TARGET_PER_CYCLE', '10000')
    reset_rpc_circuit_breaker()
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=1)
    target = _contract_target()
    fetch_evm_activity(target, None, rpc_client=rpc)
    snap = rpc_circuit_breaker_snapshot()
    assert snap['tripped'] is True, 'the process-wide breaker must trip at the per-minute ceiling'
    # Scan-phase calls stopped at the ceiling (a couple of ungated provider calls aside).
    assert len([m for m, _ in rpc.calls if m != 'eth_blockNumber']) <= 6
    assert target['_evm_poll_stopped_reason'] == 'circuit_breaker'


def test_circuit_breaker_disabled_by_default(monkeypatch):
    reset_rpc_circuit_breaker()
    assert rpc_circuit_breaker_snapshot()['enabled'] is False
    # With the breaker off, a normal bounded poll still completes.
    rpc = _UsdcRpc(latest=48_960_245)
    target = _contract_target()
    fetch_evm_activity(target, None, rpc_client=rpc)
    assert target['_evm_poll_terminal_status'] in {'complete', 'degraded', 'partial'}


# ===========================================================================
# 19. Historical backfill is disabled in polling-only MVP mode
# ===========================================================================

def test_historical_backfill_disabled_by_default():
    assert historical_backfill_enabled() is False


def test_backfill_flag_enables_wide_window(monkeypatch):
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'true')
    assert historical_backfill_enabled() is True


def test_initial_live_tail_default_and_cap(monkeypatch):
    assert initial_live_tail_blocks() == 10
    monkeypatch.setenv('INITIAL_LIVE_TAIL_BLOCKS', '9999')
    assert initial_live_tail_blocks() == 25  # hard-capped at 25


# ===========================================================================
# 20. A poll always finishes with a terminal persisted status
# ===========================================================================

def test_poll_finishes_with_terminal_status():
    rpc = _UsdcRpc(latest=48_960_245)
    target = _contract_target()
    fetch_evm_activity(target, None, rpc_client=rpc)
    assert target['_evm_poll_terminal_status'] == 'complete'
    budget = target['_evm_poll_budget']
    assert budget['rpc_calls_used'] <= 100
    assert budget['transaction_enrichments'] == 0


# ===========================================================================
# Regression: the production-shaped failed Datto poll is now bounded and safe.
#   Base USDC contract, NO cursor, latest ~48.9M, 2,000-block prior backfill policy,
#   high Transfer-log volume. Assert the fixed poll:
#     * queries at most 25 blocks,
#     * makes at most 100 RPC requests,
#     * makes at most 25 transaction enrichments,
#     * completes or stops safely within 45 seconds.
# ===========================================================================

def test_production_shaped_datto_poll_is_bounded():
    # from_block=48958242 to_block=48960242 (2,001 blocks) with logs_found=119163 was the
    # incident. Reproduce the shape: ~48.9M head, no cursor, ~60 Transfer logs/block.
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=60)
    target = _contract_target(cursor=None)

    started = time.monotonic()
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    elapsed = time.monotonic() - started

    # At most 25 blocks queried (log scan + block scan).
    frms = [f for f, _ in rpc.getlogs_ranges]
    tos = [t for _, t in rpc.getlogs_ranges]
    assert max(tos) - min(frms) + 1 <= 25
    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert max(block_calls) - min(block_calls) + 1 <= 25

    # At most 100 RPC requests and ZERO of the incident's per-log tx lookups.
    assert len(rpc.calls) <= 100, f'poll made {len(rpc.calls)} RPC calls (>100)'
    assert _count(rpc, 'eth_getTransactionByHash') == 0

    # At most 25 transaction enrichments.
    assert target['_evm_poll_budget']['transaction_enrichments'] <= 25

    # Completes (or stops safely) well within the 45-second budget.
    assert elapsed < 45
    assert target['_evm_poll_terminal_status'] in {'complete', 'partial', 'degraded'}
    # Real Transfer telemetry was produced from the receipt logs (no runaway).
    assert any(e.payload.get('event_type') == 'transfer' for e in events)


# ===========================================================================
# Strict production canary budget + terminal poll-safety summary.
# (task PRODUCTION SAFETY 4 + 5; task tests 7, 8, 9, 10, 11.)
# ===========================================================================
import re as _re


def _canary_budget(monkeypatch) -> None:
    """The strict production-canary budget from the task (5 / 500 / 5 / 30 / 30s)."""
    monkeypatch.setenv('MAX_BLOCKS_PER_TARGET_PER_CYCLE', '5')
    monkeypatch.setenv('MAX_LOGS_PER_TARGET_PER_CYCLE', '500')
    monkeypatch.setenv('MAX_TX_ENRICHMENTS_PER_TARGET_PER_CYCLE', '5')
    monkeypatch.setenv('MAX_RPC_CALLS_PER_TARGET_PER_CYCLE', '30')
    monkeypatch.setenv('MAX_POLL_DURATION_SECONDS', '30')
    monkeypatch.setenv('MONITORING_RPC_MAX_CALLS_PER_MINUTE', '60')
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'false')


def _parse_safety_summary(text: str) -> dict:
    m = _re.search(r'event=monitoring_poll_safety_summary (.+)', text)
    assert m is not None, 'every poll must emit a terminal safety summary'
    return dict(kv.split('=', 1) for kv in m.group(1).split() if '=' in kv)


def test_canary_budget_resolves_five_thirty_five(monkeypatch):
    """Task tests 7/8/9: the canary env yields max_blocks=5, max_rpc_calls=30, max_tx=5."""
    _canary_budget(monkeypatch)
    budget = load_poll_budget()
    assert budget.max_blocks == 5
    assert budget.max_rpc_calls == 30
    assert budget.max_tx_enrichments == 5
    assert budget.max_duration_seconds == 30
    limits = eap.resolved_scanner_limits()
    assert limits['max_blocks_per_target_per_cycle'] == 5
    assert limits['max_rpc_calls_per_target_per_cycle'] == 30
    assert limits['max_tx_enrichments_per_target_per_cycle'] == 5
    assert limits['historical_backfill_enabled'] is False


def test_canary_block_limit_five_enforced(monkeypatch):
    """Task test 7: the scanner never queries more than 5 blocks under the canary budget."""
    _canary_budget(monkeypatch)
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=40)
    target = _contract_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc)
    for frm, to in rpc.getlogs_ranges:
        assert to - frm + 1 <= 5, f'canary must query <=5 blocks, got {to - frm + 1}'


def test_canary_poll_emits_bounded_terminal_safety_summary(monkeypatch, caplog):
    """Task tests 8/9/10: one terminal safety summary, within the canary acceptance limits."""
    _canary_budget(monkeypatch)
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=40)
    target = _contract_target(cursor=None)
    with caplog.at_level('INFO'):
        fetch_evm_activity(target, None, rpc_client=rpc)
    f = _parse_safety_summary(caplog.text)
    assert f['workspace_id'] == WORKSPACE_ID
    assert f['target_id'] == target['id']
    assert int(f['blocks_queried']) <= 5           # acceptance: blocks_queried <= 5
    assert int(f['rpc_calls_total']) <= 30          # acceptance: rpc_calls_total <= 30
    assert int(f['transaction_enrichments']) <= 5   # acceptance: enrichments <= 5
    assert float(f['poll_duration_seconds']) <= 30  # acceptance: duration <= 30s
    assert f['terminal_status'] in {'completed', 'partial', 'failed'}
    assert f['cursor_after'] != 'unknown', 'the terminal cursor must be persisted'


def test_every_poll_emits_exactly_one_safety_summary(monkeypatch, caplog):
    """Task test 10: exactly one terminal safety summary per poll (production defaults)."""
    rpc = _UsdcRpc(latest=48_960_245, logs_per_block=40)
    with caplog.at_level('INFO'):
        fetch_evm_activity(_contract_target(cursor=None), None, rpc_client=rpc)
    assert caplog.text.count('event=monitoring_poll_safety_summary') == 1


def test_backoff_skip_summary_is_not_a_completed_canary(caplog):
    """Task test 11: a provider-backoff skip emits terminal_status=skipped with zero work —
    never 'completed', so a skip can never be counted as a successful canary."""
    with caplog.at_level('INFO'):
        eap.emit_poll_safety_summary(
            workspace_id=WORKSPACE_ID,
            target_id='9c6ecabb-cd52-404f-9859-40567b09dbb4',
            terminal_status='skipped', blocks_queried=0, log_query_count=0, logs_received=0,
            logs_processed=0, transaction_enrichments=0, rpc_calls_total=0,
            poll_duration_seconds=0, cursor_before=100, cursor_after=100,
            reason='provider_backoff_active',
        )
    f = _parse_safety_summary(caplog.text)
    assert f['terminal_status'] == 'skipped'
    assert f['terminal_status'] != 'completed'
    assert f['blocks_queried'] == '0' and f['rpc_calls_total'] == '0'
    assert f['reason'] == 'provider_backoff_active'
