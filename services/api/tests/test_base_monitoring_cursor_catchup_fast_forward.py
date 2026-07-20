"""
Tests for the deep-backlog cursor fast-forward on live Base wallet monitoring.

Reproduces the production incident where a Base wallet target was stuck in
``catchup_mode=True`` hundreds of thousands of blocks behind the chain head:

    latest_block_decimal ~ 48_873_376
    previous_cursor      ~ 48_587_204
    blocks_deferred      ~ 286_170

Gradual catch-up (BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE at a time) can never converge on
Base — the chain produces blocks faster than a capped catch-up cycle scans them — and
persisting the stale catch-up ceiling as the latest processed block kept Monitoring
Sources degraded/no-evidence even though the RPC was healthy.

These tests prove:
  A. A deep backlog fast-forwards the scan cursor to the live tail (chain head), so the
     block that flows into monitor_checkpoint / provider health / coverage telemetry is
     the REAL chain head, not the old checkpoint.
  B. Only the recent live tail is scanned (old backfill is deferred, not replayed).
  C. The raw observed chain head is exposed separately for provider-health persistence.
  D. The fast-forward is logged with the required diagnostic fields.
  E. A moderate backlog still catches up gradually (fast-forward is threshold-gated).
  F. The threshold is env-configurable, and fast-forward is disabled without a live tail.
  G. End to end: a stale cursor + healthy RPC yields a LIVE provider result whose
     latest_block is the chain head — i.e. Monitoring Sources is NOT degraded/no-evidence.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pytest


WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
BASE_CONFIRMATIONS = 3

# Deep backlog reproducing the failing Railway logs (~286k blocks behind).
DEEP_CHAIN_LATEST = 48_873_376
DEEP_CURSOR_BLOCK = 48_587_204
DEEP_CHAIN_SAFE_TO = DEEP_CHAIN_LATEST - BASE_CONFIRMATIONS  # 48_873_373

# Moderate backlog (~67k) that must still catch up gradually (below the fast-forward
# threshold); the live-tail window already covers new activity for this case.
MOD_CHAIN_LATEST = 47_353_613
MOD_CURSOR_BLOCK = 47_286_496
MOD_CHAIN_SAFE_TO = MOD_CHAIN_LATEST - BASE_CONFIRMATIONS


def _now() -> datetime:
    return datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc)


def _make_target(cursor_block: int | None) -> dict:
    cursor = f'{cursor_block}:checkpoint:-1' if cursor_block is not None else None
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


class _BaseRpc:
    """Minimal healthy Base RPC stub returning empty blocks (no wallet activity)."""

    def __init__(self, latest: int = DEEP_CHAIN_LATEST) -> None:
        self.latest = latest
        self.calls: list[tuple[str, list]] = []

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
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
            return []
        return {}


def _base_env(monkeypatch) -> None:
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '100')
    monkeypatch.setenv('EVM_LIVE_TAIL_BLOCKS', '100')
    # Deterministic default threshold regardless of any ambient override.
    monkeypatch.delenv('BASE_CATCHUP_FAST_FORWARD_THRESHOLD', raising=False)
    monkeypatch.delenv('EVM_CATCHUP_FAST_FORWARD_THRESHOLD', raising=False)


def _block_scan_calls(rpc: _BaseRpc) -> list[int]:
    return [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']


# ---------------------------------------------------------------------------
# A. Deep backlog fast-forwards the cursor to the live chain head
# ---------------------------------------------------------------------------

def test_deep_backlog_fast_forwards_cursor_to_live_head(monkeypatch):
    _base_env(monkeypatch)
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=DEEP_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=DEEP_CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    scan_to = target.get('_evm_scan_to_block')
    assert scan_to == DEEP_CHAIN_SAFE_TO, (
        f'A deep backlog must fast-forward the scan cursor to the live head '
        f'({DEEP_CHAIN_SAFE_TO}); got {scan_to} (old_cursor={DEEP_CURSOR_BLOCK}). '
        f'This is the block the runner persists to monitor_checkpoint / coverage.'
    )
    assert target.get('_evm_cursor_fast_forwarded') is True
    # The scan cursor must jump far past the old checkpoint (no longer stuck behind).
    assert scan_to > DEEP_CURSOR_BLOCK + 200_000


def test_deep_backlog_exposes_raw_observed_chain_head(monkeypatch):
    """provider_health_records.latest_block must be the REAL chain head. The provider
    exposes the raw eth_blockNumber head separately from the confirmed scan cursor."""
    _base_env(monkeypatch)
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=DEEP_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=DEEP_CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    assert target.get('_evm_observed_chain_head') == DEEP_CHAIN_LATEST
    # The confirmed scan cursor trails the raw head only by the confirmation depth.
    assert target['_evm_observed_chain_head'] - target['_evm_scan_to_block'] == BASE_CONFIRMATIONS


# ---------------------------------------------------------------------------
# B. Only the recent live tail is scanned (old backfill deferred, not replayed)
# ---------------------------------------------------------------------------

def test_fast_forward_scans_only_live_tail(monkeypatch):
    _base_env(monkeypatch)
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=DEEP_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=DEEP_CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = _block_scan_calls(rpc)
    assert block_calls, 'scan must call eth_getBlockByNumber'
    # Every scanned block is inside the live tail [safe_to - 100, safe_to]; the old
    # backlog region around the stale cursor is deferred, never replayed this cycle.
    assert min(block_calls) >= DEEP_CHAIN_SAFE_TO - 100
    assert max(block_calls) <= DEEP_CHAIN_SAFE_TO
    assert min(block_calls) > DEEP_CURSOR_BLOCK, (
        'The stale backlog region must NOT be scanned during a fast-forward cycle'
    )
    blocks_scanned = max(block_calls) - min(block_calls) + 1
    assert blocks_scanned <= 101


# ---------------------------------------------------------------------------
# D. The fast-forward is logged with the required diagnostic fields
# ---------------------------------------------------------------------------

def test_fast_forward_emits_diagnostic_log(monkeypatch, caplog):
    _base_env(monkeypatch)
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=DEEP_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=DEEP_CHAIN_LATEST)
    with caplog.at_level(logging.WARNING, logger='services.api.app.evm_activity_provider'):
        fetch_evm_activity(target, None, rpc_client=rpc)

    messages = [r.getMessage() for r in caplog.records]
    ff_lines = [m for m in messages if 'cursor_fast_forwarded=true' in m]
    assert len(ff_lines) == 1, f'expected exactly one fast-forward log, got {ff_lines}'
    line = ff_lines[0]
    for token in (
        'cursor_fast_forwarded=true',
        f'old_cursor={DEEP_CURSOR_BLOCK}',
        f'new_cursor={DEEP_CHAIN_SAFE_TO - 100}',
        f'latest_block={DEEP_CHAIN_LATEST}',
        f'live_tail_from={DEEP_CHAIN_SAFE_TO - 100}',
        f'live_tail_to={DEEP_CHAIN_SAFE_TO}',
    ):
        assert token in line, f'fast-forward log missing {token!r}; line={line}'


# ---------------------------------------------------------------------------
# E. A moderate backlog still catches up gradually (threshold-gated)
# ---------------------------------------------------------------------------

def test_moderate_backlog_does_not_fast_forward(monkeypatch):
    """A ~67k backlog is below the default fast-forward threshold, so catch-up stays
    gradual and the live-tail window (a separate range) covers new activity. The cursor
    must advance to the chunk ceiling, NOT the chain head."""
    _base_env(monkeypatch)
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '1000')
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=MOD_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=MOD_CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    scan_to = target.get('_evm_scan_to_block')
    assert target.get('_evm_cursor_fast_forwarded') is False
    assert scan_to < MOD_CHAIN_SAFE_TO, (
        f'A moderate backlog must NOT fast-forward; _evm_scan_to_block={scan_to} '
        f'should stay below chain head {MOD_CHAIN_SAFE_TO}'
    )
    # Chunk ceiling = (cursor - replay) + max_blocks_per_cycle - 1.
    assert scan_to == (MOD_CURSOR_BLOCK - 25) + 1000 - 1


# ---------------------------------------------------------------------------
# F. Threshold is env-configurable; disabled without a live tail
# ---------------------------------------------------------------------------

def test_fast_forward_threshold_env_configurable(monkeypatch):
    """Lowering BASE_CATCHUP_FAST_FORWARD_THRESHOLD makes a moderate backlog fast-forward."""
    _base_env(monkeypatch)
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '1000')
    monkeypatch.setenv('BASE_CATCHUP_FAST_FORWARD_THRESHOLD', '1000')
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=MOD_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=MOD_CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    assert target.get('_evm_cursor_fast_forwarded') is True
    assert target.get('_evm_scan_to_block') == MOD_CHAIN_SAFE_TO


def test_fast_forward_disabled_without_live_tail(monkeypatch):
    """With no live-tail window there is nothing to fast-forward TO, so even a deep
    backlog stays in gradual catch-up (preserves the RPC-friendly catch-up cap)."""
    _base_env(monkeypatch)
    monkeypatch.setenv('EVM_LIVE_TAIL_BLOCKS', '0')
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=DEEP_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=DEEP_CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    assert target.get('_evm_cursor_fast_forwarded') is False
    assert target.get('_evm_scan_to_block') < DEEP_CHAIN_SAFE_TO


def test_threshold_zero_disables_fast_forward(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv('EVM_CATCHUP_FAST_FORWARD_THRESHOLD', '0')
    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=DEEP_CURSOR_BLOCK)
    rpc = _BaseRpc(latest=DEEP_CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    assert target.get('_evm_cursor_fast_forwarded') is False
    assert target.get('_evm_scan_to_block') < DEEP_CHAIN_SAFE_TO


# ---------------------------------------------------------------------------
# G. End to end: stale cursor + healthy RPC -> LIVE result at chain head
#    (i.e. Monitoring Sources is NOT degraded/no-evidence).
# ---------------------------------------------------------------------------

def test_stale_cursor_healthy_rpc_yields_live_result_at_chain_head(monkeypatch):
    """The provider RESULT the runner consumes must be status='live' with latest_block at
    the live chain head. status='live' drives provider_health status=healthy AND coverage
    persistence in the runner; latest_block at the head keeps block lag ~0. Together these
    prove a stale cursor does not keep Monitoring Sources degraded when the RPC is healthy."""
    _base_env(monkeypatch)
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')

    import services.api.app.activity_providers as ap
    from services.api.app.evm_activity_provider import fetch_evm_activity as _real_fetch

    rpc = _BaseRpc(latest=DEEP_CHAIN_LATEST)
    # Drive the REAL provider (with our healthy stub RPC) through the result layer.
    monkeypatch.setattr(ap, 'evaluate_chain_mismatch', lambda *_a, **_k: (False, 8453, 8453))
    monkeypatch.setattr(ap, 'rpc_provider_backoff_active', lambda: False)
    monkeypatch.setattr(ap, 'fetch_evm_activity', lambda target, since_ts: _real_fetch(target, since_ts, rpc_client=rpc))

    target = _make_target(cursor_block=DEEP_CURSOR_BLOCK)
    result = ap.fetch_target_activity_result(target, None)

    assert result.status == 'live', (
        f'stale cursor + healthy RPC must yield a LIVE (coverage-verified) result, '
        f'not {result.status!r} — otherwise Monitoring Sources reads degraded/no-evidence'
    )
    assert result.latest_block == DEEP_CHAIN_SAFE_TO, (
        f'the coverage/checkpoint block must be the live chain head ({DEEP_CHAIN_SAFE_TO}), '
        f'not the old checkpoint; got {result.latest_block}'
    )
    assert target.get('_evm_cursor_fast_forwarded') is True
    assert target.get('_evm_observed_chain_head') == DEEP_CHAIN_LATEST
