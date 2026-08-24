"""Pre-QuickNode-Stream audit proofs: a stale live checkpoint is safe.

Production evidence that motivated these tests (API startup readiness marker):

    event=quicknode_live_lane_started stream_key=base-live
    checkpoint_identity=quicknode:base:live chain_head=50387766
    checkpoint_block=48771299 lag_blocks=1616467

The live checkpoint (``quicknode:base:live``) is a legacy persisted value written by a
live-lane writer at an EARLIER deploy when the Base tip was ~48771301, then frozen when
that writer stopped advancing it. The chain head has since moved to 50387766, so the
readiness marker reports a ~1.6M-block lag. The concern for a brand-new QuickNode Stream
started at the CURRENT tip: could that stale checkpoint drag the live lane into a
1.6M-block historical replay / RPC storm?

These tests prove it cannot, for BOTH live-lane writers:

  * The WEBHOOK live lane (POST /api/integrations/quicknode/streams/base-live, the
    demo's real-time path) advances its checkpoint with a monotonic GREATEST jump and
    makes NO per-block RPC calls — a stale checkpoint self-heals to the tip on the first
    batch, with zero historical replay.
  * The RPC-poller live WORKER (run_live_tip_ingest, disabled for the demo via
    QUICKNODE_LIVE_ENABLED=false) now SNAPS to the tip when its checkpoint is stale
    (beyond QUICKNODE_LIVE_MAX_CATCHUP_BLOCKS) instead of walking the gap block-by-block,
    so even if it is ever enabled the catch-up stays strictly bounded.

Also proves the BLOCKER 2 property: Base RPC provider resolution is deterministic and,
under the recommended QuickNode-only environment, never selects an Alchemy URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from services.api.app import quicknode_streams as qn
from services.api.app import evm_activity_provider as evm

WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
UNRELATED = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
SECRET = 'whsec_test_secret_123'
NONCE = 'test-nonce-audit'
LIVE_KEY = qn.QUICKNODE_STREAM_KEY_BASE_LIVE
BACKFILL_KEY = qn.QUICKNODE_STREAM_KEY_BASE_BACKFILL
BASE_KEY = qn.QUICKNODE_STREAM_KEY_BASE

# The production incident numbers.
STALE_LIVE_BLOCK = 48_771_299
CHAIN_HEAD = 50_387_766

# QuickNode-only demo endpoints (hosts only matter for the assertions).
QUICKNODE_URL = 'https://frequent-greatest-cherry.base-mainnet.quiknode.pro/abc123def456/'
QUICKNODE_HOST = 'frequent-greatest-cherry.base-mainnet.quiknode.pro'
ALCHEMY_URL = 'https://base-mainnet.g.alchemy.com/v2/myalchemykey999'
ALCHEMY_HOST = 'base-mainnet.g.alchemy.com'


@pytest.fixture(autouse=True)
def _clean_process_local_state(monkeypatch):
    """Real-time mode on; reset the process-local chain-head cache + log samplers."""
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    qn.reset_chain_head_cache()
    qn.reset_quicknode_log_sampler_state()
    yield
    qn.reset_chain_head_cache()
    qn.reset_quicknode_log_sampler_state()


def _make_target(*, wallet: str = WALLET) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Treasury Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'chain_id': 8453,
        'wallet_address': wallet,
        'contract_identifier': None,
        'asset_id': None,
        'target_metadata': {},
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
    }


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _LaneConn:
    """Fake connection with per-stream_key checkpoints, targets, and telemetry."""

    def __init__(self, *, targets=None, existing_telemetry=None, checkpoints=None):
        self.targets = targets or []
        self.existing_telemetry = existing_telemetry
        self.checkpoints: dict[str, dict] = dict(checkpoints or {})
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if q.startswith('create table'):
            return _Rows([])
        if 'pg_try_advisory_lock' in q:
            return _Rows([{'acquired': True}])
        if 'pg_advisory_unlock' in q:
            return _Rows([{'pg_advisory_unlock': True}])
        if 'from quicknode_stream_checkpoints' in q:
            key = params[0]
            cp = self.checkpoints.get(key)
            return _Rows([cp] if cp else [])
        if q.startswith('insert into quicknode_stream_checkpoints'):
            p = list(params)
            key, latest, last_processed = p[0], p[1], p[2]
            if len(p) == 6:
                started, received_at = p[4], p[5]
            elif len(p) == 5:
                started, received_at = p[3], p[4]
            else:
                started, received_at = p[3], None
            prev = self.checkpoints.get(key) or {}
            # Mirror the GREATEST semantics of the real ON CONFLICT upsert. A NULL
            # incoming latest (the live "head unknown" path) leaves the stored head.
            prev_latest = prev.get('latest_stream_block')
            new_latest = prev_latest if latest is None else max(latest, prev_latest or -1)
            self.checkpoints[key] = {
                'stream_key': key,
                'latest_stream_block': new_latest,
                'last_processed_block': max(last_processed, prev.get('last_processed_block') or -1),
                'stream_started_at_block': prev.get('stream_started_at_block') or started,
                'webhook_received_at': received_at,
            }
            return _Rows([])
        if 'from targets' in q:
            return _Rows(self.targets)
        if 'from assets' in q:
            return _Rows([])
        if 'from telemetry_events' in q and 'select' in q:
            return _Rows([self.existing_telemetry] if self.existing_telemetry else [])
        if q.startswith('insert into telemetry_events'):
            self.telemetry_inserts.append(tuple(params or ()))
            return _Rows([])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        pass


class _CountingRpc:
    """Fake Base RPC that COUNTS calls, so a test can prove the scan stayed bounded."""

    def __init__(self, head: int):
        self.head = head
        self.block_fetches = 0          # eth_getBlockByNumber (the per-block replay cost)
        self.block_number_calls = 0     # eth_blockNumber (head reads)

    def call(self, method, params):
        if method == 'eth_blockNumber':
            self.block_number_calls += 1
            return hex(self.head)
        if method == 'eth_getBlockByNumber':
            self.block_fetches += 1
            return {'number': params[0], 'transactions': []}
        return None


@contextmanager
def _mock_pg(connection):
    yield connection


def _sign(secret: str, *, nonce: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), nonce.encode() + timestamp.encode() + body, hashlib.sha256).hexdigest()


def _now_ts() -> str:
    return str(int(time.time()))


def _tx_body(*, tx_hash: str, tx_from: str, tx_to: str, block: int) -> bytes:
    return json.dumps({
        'tx_hash': tx_hash, 'from': tx_from, 'to': tx_to,
        'value': '1000000000000000000', 'block_number': block, 'chain_id': 8453,
    }).encode()


def _call_live_webhook(*, body: bytes, conn: _LaneConn, rpc: _CountingRpc | None, monkeypatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=body)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
            patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
            patch.object(qn, '_make_base_rpc_client', lambda: rpc):
        return qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=timestamp, lane='live',
        )


def _now() -> datetime:
    return datetime(2026, 8, 24, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. A new live lane can safely BEGIN at the current tip (cold start).
# ---------------------------------------------------------------------------

def test_new_live_lane_begins_at_tip_not_history():
    """No live checkpoint -> the worker's first tick starts AT the safe head, scanning a
    single block, never replaying history."""
    conn = _LaneConn(targets=[])  # no live checkpoint yet
    rpc = _CountingRpc(CHAIN_HEAD)
    stats = qn.run_live_tip_ingest(conn, rpc_client=rpc, targets=[], now=_now())
    safe_head = qn.compute_live_start_block(CHAIN_HEAD, qn.live_confirmations())
    assert stats['checkpoint_before'] is None
    assert stats['checkpoint_after'] == safe_head
    # Exactly one block fetched (the tip) — not a historical walk.
    assert rpc.block_fetches == 1
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == safe_head


# ---------------------------------------------------------------------------
# 2. A stale legacy/base checkpoint cannot drag the live lane BACKWARDS.
# ---------------------------------------------------------------------------

def test_stale_live_checkpoint_never_regresses_on_old_batch(monkeypatch):
    """The live checkpoint sits near the tip; an out-of-order OLD block on the live route
    must not move it backwards (monotonic GREATEST)."""
    near_tip = CHAIN_HEAD - 2
    conn = _LaneConn(targets=[_make_target()], checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': near_tip,
                   'latest_stream_block': CHAIN_HEAD, 'stream_started_at_block': near_tip,
                   'webhook_received_at': _now()},
    })
    body = _tx_body(tx_hash='0x' + 'a1' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=STALE_LIVE_BLOCK)
    rpc = _CountingRpc(CHAIN_HEAD)
    _call_live_webhook(body=body, conn=conn, rpc=rpc, monkeypatch=monkeypatch)
    # The checkpoint did NOT regress to the old block.
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == near_tip


def test_legacy_base_checkpoint_never_seeds_or_drives_live():
    """A checkpoint on stream_key='base' (the legacy historical delivery lane) is NEVER
    treated as the live cursor and is NEVER copied into the live lane."""
    conn = _LaneConn(checkpoints={
        BASE_KEY: {'stream_key': BASE_KEY, 'last_processed_block': 48_391_739,
                   'latest_stream_block': 48_391_739, 'stream_started_at_block': 48_391_739,
                   'webhook_received_at': _now()},
    })
    # Migrating the legacy base cursor seeds ONLY the backfill lane, never live.
    assert qn.seed_backfill_from_base_checkpoint(conn) is True
    assert LIVE_KEY not in conn.checkpoints
    # And live health derived from a base-only checkpoint is "no live activity", not green.
    status = qn.build_quicknode_live_lane_status(conn, now=_now())
    assert status['state'] is None
    assert status['live_checkpoint_block'] is None


# ---------------------------------------------------------------------------
# 3. An old live checkpoint + a first current-tip PAYLOAD cannot create an
#    unbounded RPC backfill (the demo webhook path).
# ---------------------------------------------------------------------------

def test_first_tip_webhook_over_stale_checkpoint_makes_no_block_rpc(monkeypatch):
    """The live WEBHOOK lane advances the stale checkpoint straight to the tip via a
    monotonic jump and fetches ZERO blocks over RPC — no 1.6M-block replay."""
    conn = _LaneConn(targets=[_make_target()], checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': STALE_LIVE_BLOCK,
                   'latest_stream_block': STALE_LIVE_BLOCK, 'stream_started_at_block': STALE_LIVE_BLOCK,
                   'webhook_received_at': _now()},
    })
    tip_block = CHAIN_HEAD - 2
    body = _tx_body(tx_hash='0x' + 'b2' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=tip_block)
    rpc = _CountingRpc(CHAIN_HEAD)
    result = _call_live_webhook(body=body, conn=conn, rpc=rpc, monkeypatch=monkeypatch)
    # No per-block replay: the webhook path never calls eth_getBlockByNumber.
    assert rpc.block_fetches == 0
    # At most one head read (for lag), served from the bounded cache thereafter.
    assert rpc.block_number_calls <= 1
    # The stale checkpoint jumped forward to the delivered tip block in one step.
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == tip_block
    # And the delivered transfer was still detected at the tip.
    assert result['persisted'] == 1
    assert result['results'][0]['detected_by'] == 'quicknode_stream'


# ---------------------------------------------------------------------------
# 4. Any permitted gap recovery remains STRICTLY BOUNDED (the RPC-poller worker).
# ---------------------------------------------------------------------------

def test_stale_checkpoint_worker_snaps_to_tip_bounded(monkeypatch, caplog):
    """The RPC-poller live worker with a 1.6M-block-stale checkpoint must SNAP to the tip
    and scan at most live_max_blocks_per_tick blocks — never walk the whole gap."""
    conn = _LaneConn(targets=[], checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': STALE_LIVE_BLOCK,
                   'latest_stream_block': STALE_LIVE_BLOCK, 'stream_started_at_block': STALE_LIVE_BLOCK,
                   'webhook_received_at': _now()},
    })
    rpc = _CountingRpc(CHAIN_HEAD)
    with caplog.at_level('WARNING', logger='services.api.app.quicknode_streams'):
        stats = qn.run_live_tip_ingest(conn, rpc_client=rpc, targets=[], now=_now())
    per_tick = qn.live_max_blocks_per_tick()
    safe_head = qn.compute_live_start_block(CHAIN_HEAD, qn.live_confirmations())
    # Bounded scan: at most one tick's worth of blocks, NOT ~1.6M.
    assert rpc.block_fetches <= per_tick
    assert rpc.block_fetches == per_tick  # a full tip window here
    # Snapped to the tip: the cursor lands at the safe head, lag ~ confirmations only.
    assert stats['checkpoint_after'] == safe_head
    assert stats['lag_blocks'] <= qn.live_confirmations()
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == safe_head
    # The snap is provable from logs and names the skipped range (never silent).
    assert 'event=quicknode_live_checkpoint_snapped_to_tip' in caplog.text
    assert f'stale_checkpoint_block={STALE_LIVE_BLOCK}' in caplog.text


def test_small_gap_within_catchup_is_not_snapped():
    """A SMALL gap (a brief restart, within QUICKNODE_LIVE_MAX_CATCHUP_BLOCKS) is caught up
    normally — the snap guard only triggers for a genuinely stale checkpoint."""
    head = 1_000_000
    prev = head - 10  # 10 blocks behind, well within the 1800-block catch-up window
    conn = _LaneConn(targets=[], checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': prev,
                   'latest_stream_block': head, 'stream_started_at_block': prev,
                   'webhook_received_at': _now()},
    })
    rpc = _CountingRpc(head)
    stats = qn.run_live_tip_ingest(conn, rpc_client=rpc, targets=[], now=_now())
    safe_head = qn.compute_live_start_block(head, qn.live_confirmations())
    # Normal forward walk from prev+1 (bounded by per-tick), no snap.
    assert rpc.block_fetches == safe_head - prev
    assert stats['checkpoint_after'] == safe_head


def test_catchup_bound_is_configurable(monkeypatch):
    """QUICKNODE_LIVE_MAX_CATCHUP_BLOCKS tunes the snap threshold; 0 disables the snap."""
    monkeypatch.setenv('QUICKNODE_LIVE_MAX_CATCHUP_BLOCKS', '500')
    assert qn.live_max_catchup_blocks() == 500
    monkeypatch.setenv('QUICKNODE_LIVE_MAX_CATCHUP_BLOCKS', '0')
    assert qn.live_max_catchup_blocks() == 0


# ---------------------------------------------------------------------------
# 5. Live and backfill checkpoint identities remain INDEPENDENT.
# ---------------------------------------------------------------------------

def test_live_and_backfill_checkpoints_are_independent():
    """Advancing the live lane never touches the backfill cursor, and vice versa."""
    conn = _LaneConn(targets=[], checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': CHAIN_HEAD - 5,
                   'latest_stream_block': CHAIN_HEAD, 'stream_started_at_block': CHAIN_HEAD - 5,
                   'webhook_received_at': _now()},
        BACKFILL_KEY: {'stream_key': BACKFILL_KEY, 'last_processed_block': 48_400_000,
                       'latest_stream_block': 48_400_000, 'stream_started_at_block': 48_391_739,
                       'webhook_received_at': _now()},
    })
    # Advance LIVE via the worker tip lane.
    rpc = _CountingRpc(CHAIN_HEAD)
    qn.run_live_tip_ingest(conn, rpc_client=rpc, targets=[], now=_now())
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] == 48_400_000  # untouched
    live_after = conn.checkpoints[LIVE_KEY]['last_processed_block']
    # Advance BACKFILL one step; the live cursor is byte-for-byte unchanged.
    qn.run_backfill_step(conn, rpc_client=rpc, targets=[], live_start_block=None, now=_now())
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] > 48_400_000  # advanced
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == live_after     # untouched


# ---------------------------------------------------------------------------
# 6. API Base RPC provider resolution is DETERMINISTIC.
# ---------------------------------------------------------------------------

def _clear_rpc_env(monkeypatch):
    for name in (
        'EVM_RPC_URL_8453', 'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL', 'EVM_RPC_URLS',
        'EVM_RPC_FAILOVER_URLS', 'EVM_RPC_FAILOVER_URLS_8453',
        'STAGING_EVM_RPC_URL', 'EVM_RPC_URL', 'STAGING_EVM_CHAIN_ID', 'EVM_CHAIN_ID',
    ):
        monkeypatch.delenv(name, raising=False)


def test_base_rpc_resolution_is_deterministic_and_prefers_chain_specific(monkeypatch):
    """EVM_RPC_URL_8453 is the most specific Base override and wins over the legacy
    globals; resolution is stable across repeated calls."""
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_8453', QUICKNODE_URL)
    # A legacy global still pointing at Alchemy must NOT win for Base.
    monkeypatch.setenv('EVM_RPC_URL', ALCHEMY_URL)
    from urllib.parse import urlparse
    hosts = {urlparse(evm.resolve_chain_rpc('base')['rpc_url']).hostname for _ in range(5)}
    assert hosts == {QUICKNODE_HOST}
    assert evm.resolve_chain_rpc('base')['rpc_url_env'] == 'EVM_RPC_URL_8453'


# ---------------------------------------------------------------------------
# 7. Under the recommended QuickNode-only environment, NO active Base RPC path
#    selects an Alchemy URL.
# ---------------------------------------------------------------------------

def test_recommended_quicknode_only_env_never_resolves_alchemy(monkeypatch):
    """With the final recommended env (every Base RPC variable = QuickNode, Alchemy
    nowhere), every resolver the API can reach returns only the QuickNode host."""
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('EVM_RPC_URL_8453', QUICKNODE_URL)
    monkeypatch.setenv('BASE_EVM_RPC_URL', QUICKNODE_URL)
    monkeypatch.setenv('EVM_BASE_RPC_URL', QUICKNODE_URL)
    monkeypatch.setenv('EVM_RPC_URLS', QUICKNODE_URL)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', QUICKNODE_URL)
    monkeypatch.setenv('EVM_RPC_URL', QUICKNODE_URL)
    from urllib.parse import urlparse

    def _hosts(urls):
        return {urlparse(u).hostname for u in urls}

    # Per-chain resolver (the detection path), the global single resolver (health
    # probe), and the full failover list must all be QuickNode-only.
    chain = evm.resolve_chain_rpc('base')
    assert urlparse(chain['rpc_url']).hostname == QUICKNODE_HOST
    assert _hosts(chain['rpc_urls']) == {QUICKNODE_HOST}
    assert urlparse(evm._resolve_evm_rpc_url()).hostname == QUICKNODE_HOST
    assert _hosts(evm._resolve_evm_rpc_urls()) == {QUICKNODE_HOST}
    # Belt-and-suspenders: the Alchemy host appears in none of them.
    assert ALCHEMY_HOST not in _hosts(evm._resolve_evm_rpc_urls())


def test_stale_globals_do_not_leak_alchemy_into_base_detection(monkeypatch):
    """Reproduces the reported split: legacy globals still Alchemy, but the Base-specific
    override is QuickNode. The detection path (resolve_chain_rpc) must stay QuickNode."""
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('EVM_RPC_URL_8453', QUICKNODE_URL)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', ALCHEMY_URL)  # the misleading startup value
    from urllib.parse import urlparse
    chain = evm.resolve_chain_rpc('base')
    assert urlparse(chain['rpc_url']).hostname == QUICKNODE_HOST
    assert chain['rpc_url_env'] == 'EVM_RPC_URL_8453'
    # The failover client the detection path dials contains no Alchemy host.
    assert ALCHEMY_HOST not in {urlparse(u).hostname for u in chain['rpc_urls']}
