"""Tests for the real-time telemetry path: publish-after-commit + live/backfill lanes.

Covers the "new monitored-wallet transfer only shows up after Stable RPC Polling"
fix (services/api/app/telemetry_realtime.py + quicknode_streams.py):

  * Telemetry rows publish to the workspace :telemetry Redis stream ONLY after the
    DB commit, and a Redis failure never loses the (already durable) row.
  * The QuickNode live chain-tip lane starts near the current head regardless of how
    far the historical backfill lane is behind, and the two lanes keep SEPARATE
    checkpoints that cannot overwrite each other.
  * The live-lane state (Live / Catching up / Degraded / Stale / Failed) is derived
    from lag = chain_head - live_checkpoint, so historical catch-up never paints a
    false green "live".
  * Multi-replica safety via the Postgres advisory lock.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.api.app import quicknode_streams as qn
from services.api.app import telemetry_realtime as tr
from services.api.app.domains import alert_stream

WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
UNRELATED = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
LIVE_KEY = qn.QUICKNODE_STREAM_KEY_BASE_LIVE
BACKFILL_KEY = qn.QUICKNODE_STREAM_KEY_BASE_BACKFILL


def _make_target(*, wallet: str = WALLET, workspace_id: str | None = None) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
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
    """Fake connection with per-stream_key checkpoints + telemetry insert/select.

    Records an ``events`` log (shared, optional) so a test can assert a publish
    happened strictly AFTER the commit.
    """

    def __init__(self, *, targets=None, existing_telemetry=None, events=None,
                 advisory_acquired=True):
        self.targets = targets or []
        self.existing_telemetry = existing_telemetry
        self.checkpoints: dict[str, dict] = {}
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0
        self.events = events if events is not None else []
        self.advisory_acquired = advisory_acquired

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if q.startswith('create table'):
            return _Rows([])
        if 'pg_try_advisory_lock' in q:
            return _Rows([{'acquired': self.advisory_acquired}])
        if 'pg_advisory_unlock' in q:
            return _Rows([{'pg_advisory_unlock': True}])
        if 'from quicknode_stream_checkpoints' in q:
            key = params[0]
            cp = self.checkpoints.get(key)
            return _Rows([cp] if cp else [])
        if q.startswith('insert into quicknode_stream_checkpoints'):
            p = list(params)
            key, latest, last_processed = p[0], p[1], p[2]
            # _advance_lane_checkpoint uses a literal 0 gap -> 5 params
            # (key, latest, block, block, received_at); the gap detector passes gap
            # as a param -> 6 params (key, latest, last_processed, gap, started, at).
            if len(p) == 6:
                started, received_at = p[4], p[5]
            else:
                started, received_at = p[3], p[4]
            prev = self.checkpoints.get(key) or {}
            self.checkpoints[key] = {
                'stream_key': key,
                'latest_stream_block': max(latest, prev.get('latest_stream_block') or -1),
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
        self.events.append('commit')

    def rollback(self):
        pass


class _FakeRedis:
    """Records xadd calls; optionally fails, or asserts a prior commit happened."""

    def __init__(self, *, fail=False, events=None):
        self.xadds: list[tuple] = []
        self.fail = fail
        self.events = events

    def xadd(self, key, fields, maxlen=None, approximate=None):
        if self.events is not None:
            self.events.append('publish')
        if self.fail:
            raise RuntimeError('redis unavailable')
        self.xadds.append((key, fields))
        return b'1-0'


@contextmanager
def _mock_pg(connection):
    yield connection


def _stream_tx(*, tx_hash: str, tx_from: str, tx_to: str = COUNTERPARTY, block: int = 100) -> dict:
    return {'tx_hash': tx_hash, 'from': tx_from, 'to': tx_to, 'value': '0xde0b6b3a7640000', 'block_number': block}


def _block(n: int, txs: list[dict]) -> dict:
    return {'number': hex(n), 'hash': '0x' + 'ab' * 32, 'transactions': txs}


def _rpc_tx(*, tx_hash: str, tx_from: str, tx_to: str = COUNTERPARTY, block: int) -> dict:
    return {'hash': tx_hash, 'from': tx_from, 'to': tx_to, 'value': '0xde0b6b3a7640000', 'blockNumber': hex(block)}


class _FakeRpc:
    def __init__(self, head: int, blocks: dict[int, dict]):
        self.head = head
        self.blocks = blocks

    def call(self, method, params):
        if method == 'eth_blockNumber':
            return hex(self.head)
        if method == 'eth_getBlockByNumber':
            return self.blocks.get(int(params[0], 16))
        return None


# ---------------------------------------------------------------------------
# build_telemetry_stream_event contract
# ---------------------------------------------------------------------------

def test_build_event_carries_required_fields_and_type():
    event = tr.build_telemetry_stream_event(
        telemetry_id='t1', workspace_id='w1', target_id='tg1',
        event_type='wallet_transfer_detected', detected_by='quicknode_stream',
        tx_hash='0xabc', from_address='0xfrom', to_address='0xto', amount=1000,
        chain_id=8453, block_number=42, observed_at='2026-07-10T15:50:00Z',
        evidence_source='live',
    )
    assert event['type'] == 'telemetry'
    for key in (
        'telemetry_id', 'target_id', 'workspace_id', 'event_type', 'detected_by',
        'tx_hash', 'from', 'to', 'amount', 'chain_id', 'block_number',
        'observed_at', 'ingested_at', 'evidence_source',
    ):
        assert key in event
    assert event['detected_by'] == 'quicknode_stream'
    assert event['chain_id'] == '8453'  # stringified for JSON stability
    assert event['amount'] == '1000'


# ---------------------------------------------------------------------------
# publish_telemetry_event fail-safe behavior (requirement 11)
# ---------------------------------------------------------------------------

def test_publish_noop_when_redis_not_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('REDIS_URL', raising=False)
    assert tr.publish_telemetry_event('w1', {'telemetry_id': 't1'}) is False


def test_publish_returns_false_and_swallows_redis_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    with patch.object(alert_stream, '_get_sync_client', lambda: _FakeRedis(fail=True)):
        # Must NOT raise — persistence already succeeded upstream.
        assert tr.publish_telemetry_event('w1', {'telemetry_id': 't1', 'target_id': 'tg1'}) is False


def test_publish_uses_workspace_scoped_telemetry_stream_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    fake = _FakeRedis()
    with patch.object(alert_stream, '_get_sync_client', lambda: fake):
        assert tr.publish_telemetry_event('ws-123', {'telemetry_id': 't1'}) is True
    assert len(fake.xadds) == 1
    key, _fields = fake.xadds[0]
    assert key == 'decoda:workspace:ws-123:telemetry'
    # Never the alerts stream.
    assert key != alert_stream.stream_key('ws-123')


def test_two_workspaces_get_isolated_streams():
    assert alert_stream.telemetry_stream_key('w1') != alert_stream.telemetry_stream_key('w2')


# ---------------------------------------------------------------------------
# QuickNode persist publishes AFTER commit; dedupe suppresses the publish.
# ---------------------------------------------------------------------------

def test_quicknode_persist_publishes_after_commit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    events: list[str] = []
    target = _make_target()
    conn = _LaneConn(targets=[target], events=events)
    fake = _FakeRedis(events=events)
    tx = qn.normalize_base_stream_tx(_stream_tx(tx_hash='0x' + '09' * 32, tx_from=WALLET, block=48431098))
    with patch.object(alert_stream, '_get_sync_client', lambda: fake):
        outcome = qn._persist_quicknode_wallet_transfer(conn, target=target, tx=tx)
    assert outcome['status'] == 'processed'
    assert len(conn.telemetry_inserts) == 1
    # Exactly one publish, to the telemetry stream, AFTER the commit.
    assert len(fake.xadds) == 1
    key, fields = fake.xadds[0]
    assert key == f"decoda:workspace:{target['workspace_id']}:telemetry"
    assert events.index('commit') < events.index('publish')
    payload = json.loads(fields['payload'])
    assert payload['type'] == 'telemetry'
    assert payload['detected_by'] == 'quicknode_stream'
    assert payload['tx_hash'] == tx['tx_hash']
    assert payload['target_id'] == str(target['id'])


def test_quicknode_duplicate_does_not_publish(monkeypatch: pytest.MonkeyPatch):
    """A tx Stable RPC Polling already recorded is suppressed — no row, no push."""
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    target = _make_target()
    existing = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _LaneConn(targets=[target], existing_telemetry=existing)
    fake = _FakeRedis()
    tx = qn.normalize_base_stream_tx(_stream_tx(tx_hash='0x' + '09' * 32, tx_from=WALLET, block=100))
    with patch.object(alert_stream, '_get_sync_client', lambda: fake):
        outcome = qn._persist_quicknode_wallet_transfer(conn, target=target, tx=tx)
    assert outcome['status'] == 'duplicate_suppressed'
    assert conn.telemetry_inserts == []
    assert fake.xadds == []  # nothing published for a suppressed duplicate


def test_quicknode_persist_survives_redis_failure(monkeypatch: pytest.MonkeyPatch):
    """Requirement 11: a Redis failure must not lose the persisted telemetry row."""
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    target = _make_target()
    conn = _LaneConn(targets=[target])
    tx = qn.normalize_base_stream_tx(_stream_tx(tx_hash='0x' + '09' * 32, tx_from=WALLET, block=100))
    with patch.object(alert_stream, '_get_sync_client', lambda: _FakeRedis(fail=True)):
        outcome = qn._persist_quicknode_wallet_transfer(conn, target=target, tx=tx)
    assert outcome['status'] == 'processed'
    assert len(conn.telemetry_inserts) == 1  # row is durable regardless of Redis


# ---------------------------------------------------------------------------
# Stable/realtime persist choke point publishes too (all live paths, not just QN).
# ---------------------------------------------------------------------------

def test_stable_persist_publishes_live_transfer(monkeypatch: pytest.MonkeyPatch):
    from services.api.app import monitoring_runner as mr

    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')

    class _StableConn:
        def execute(self, query, params=None):
            q = (query or '').strip().lower()
            if q.startswith('insert into telemetry_events'):
                return _Rows([])
            if 'select' in q and 'telemetry_events' in q:
                return _Rows([{'c': 1}])  # verify: row is durably present
            return _Rows([])

        def commit(self):
            pass

    fake = _FakeRedis()
    workspace_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    with patch.object(mr, 'pg_connection', lambda: _mock_pg(_StableConn())), \
         patch.object(alert_stream, '_get_sync_client', lambda: fake):
        persisted = mr._persist_raw_wallet_transfer_telemetry(
            _StableConn(),
            telemetry_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            asset_id=None,
            target_id=target_id,
            provider_type='stable_rpc_polling',
            event_type='native_transfer',
            observed_at=datetime.now(timezone.utc),
            evidence_source='live',
            payload={
                'tx_hash': '0x' + '09' * 32, 'from': WALLET, 'to': COUNTERPARTY,
                'amount': '1', 'chain_id': '8453', 'block_number': 100,
                'detected_by': 'stable_rpc_polling',
            },
            idempotency_key=f'{workspace_id}:{target_id}:x',
        )
    assert persisted is True
    assert len(fake.xadds) == 1
    key, fields = fake.xadds[0]
    assert key == f'decoda:workspace:{workspace_id}:telemetry'
    payload = json.loads(fields['payload'])
    assert payload['detected_by'] == 'stable_rpc_polling'
    assert payload['event_type'] == 'native_transfer'


# ---------------------------------------------------------------------------
# Live chain-tip lane vs historical backfill lane.
# ---------------------------------------------------------------------------

def _no_alert_chain():
    return patch.object(qn, '_create_wallet_transfer_alert_chain',
                        lambda **kw: {'smoke_alert_id': None, 'sig_alert_id': None})


def test_live_lane_starts_near_head_even_with_backfill_far_behind():
    """Requirement 1 & 2: the live lane begins at the safe head, not at the backfill cursor."""
    target = _make_target()
    conn = _LaneConn(targets=[target])
    # Backfill lane is 40k+ blocks behind; live lane has never run.
    conn.checkpoints[BACKFILL_KEY] = {
        'stream_key': BACKFILL_KEY, 'latest_stream_block': 48391000,
        'last_processed_block': 48391000, 'stream_started_at_block': 48391000,
        'webhook_received_at': datetime.now(timezone.utc),
    }
    head = 48431100
    matched = _rpc_tx(tx_hash='0x' + '09' * 32, tx_from=WALLET, block=head - 2)
    blocks = {head - 2: _block(head - 2, [matched])}
    with _no_alert_chain():
        stats = qn.run_live_tip_ingest(conn, rpc_client=_FakeRpc(head, blocks), targets=[target])
    # Started at the safe head (head - confirmations), NOT block 48391001.
    assert stats['checkpoint_after'] == head - qn.live_confirmations()
    assert stats['persisted'] == 1
    # Only the LIVE checkpoint moved; the backfill cursor is untouched.
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == head - qn.live_confirmations()
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] == 48391000


def test_live_lane_backlog_does_not_block_processing():
    """The live lane processes the tip regardless of how far backfill is behind."""
    target = _make_target()
    conn = _LaneConn(targets=[target])
    head = 500
    blocks = {498: _block(498, [])}
    with _no_alert_chain():
        stats = qn.run_live_tip_ingest(conn, rpc_client=_FakeRpc(head, blocks), targets=[target])
    assert stats['blocks_scanned'] == 1
    assert stats['lag_blocks'] == qn.live_confirmations()  # at the tip


def test_live_lane_second_tick_processes_new_blocks_forward():
    target = _make_target()
    conn = _LaneConn(targets=[target])
    conn.checkpoints[LIVE_KEY] = {
        'stream_key': LIVE_KEY, 'latest_stream_block': 100, 'last_processed_block': 100,
        'stream_started_at_block': 98, 'webhook_received_at': datetime.now(timezone.utc),
    }
    head = 105  # safe head 103
    blocks = {n: _block(n, []) for n in range(101, 104)}
    with _no_alert_chain():
        stats = qn.run_live_tip_ingest(conn, rpc_client=_FakeRpc(head, blocks), targets=[target])
    assert stats['checkpoint_after'] == 103  # head - 2 confirmations
    assert stats['blocks_scanned'] == 3      # 101, 102, 103


def test_live_lane_matches_to_address_case_insensitively():
    """Requirement 4 & 5: an inbound transfer TO the wallet (mixed case) still matches."""
    target = _make_target()
    conn = _LaneConn(targets=[target])
    head = 200
    # tx TO the monitored wallet, upper-cased on the wire.
    tx = _rpc_tx(tx_hash='0x' + '09' * 32, tx_from=COUNTERPARTY, tx_to=WALLET.upper(), block=198)
    blocks = {198: _block(198, [tx])}
    with _no_alert_chain():
        stats = qn.run_live_tip_ingest(conn, rpc_client=_FakeRpc(head, blocks), targets=[target])
    assert stats['matched'] == 1
    assert stats['persisted'] == 1


def test_live_lane_ignores_unrelated_transactions():
    target = _make_target()
    conn = _LaneConn(targets=[target])
    head = 200
    tx = _rpc_tx(tx_hash='0x' + '11' * 32, tx_from=UNRELATED, tx_to=COUNTERPARTY, block=198)
    blocks = {198: _block(198, [tx])}
    with _no_alert_chain():
        stats = qn.run_live_tip_ingest(conn, rpc_client=_FakeRpc(head, blocks), targets=[target])
    assert stats['matched'] == 0
    assert stats['persisted'] == 0
    assert conn.telemetry_inserts == []


def test_live_lane_chain_head_failure_reports_failed():
    target = _make_target()
    conn = _LaneConn(targets=[target])

    class _DeadRpc:
        def call(self, method, params):
            if method == 'eth_blockNumber':
                raise RuntimeError('provider down')
            return None

    stats = qn.run_live_tip_ingest(conn, rpc_client=_DeadRpc(), targets=[target])
    assert stats['failed'] is True
    assert LIVE_KEY not in conn.checkpoints  # nothing advanced on a failed tick


def test_backfill_lane_advances_only_its_own_checkpoint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_BACKFILL_MAX_BLOCKS_PER_TICK', '3')
    target = _make_target()
    conn = _LaneConn(targets=[target])
    conn.checkpoints[BACKFILL_KEY] = {
        'stream_key': BACKFILL_KEY, 'latest_stream_block': 100, 'last_processed_block': 100,
        'stream_started_at_block': 100, 'webhook_received_at': datetime.now(timezone.utc),
    }
    blocks = {n: _block(n, []) for n in range(101, 105)}
    with _no_alert_chain():
        stats = qn.run_backfill_step(conn, rpc_client=_FakeRpc(48431100, blocks), targets=[target],
                                     live_start_block=48431098)
    assert stats['checkpoint_after'] == 103  # 101..103 (bounded by max=3)
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] == 103
    assert LIVE_KEY not in conn.checkpoints  # backfill never touches the live cursor


def test_live_and_backfill_checkpoints_are_independent():
    """Requirement 9: separate live and backfill checkpoints cannot overwrite each other."""
    target = _make_target()
    conn = _LaneConn(targets=[target])
    head = 500
    with _no_alert_chain():
        qn.run_live_tip_ingest(conn, rpc_client=_FakeRpc(head, {498: _block(498, [])}), targets=[target])
    live_after = conn.checkpoints[LIVE_KEY]['last_processed_block']
    # Seed a backfill cursor far below and run one backfill step.
    conn.checkpoints[BACKFILL_KEY] = {
        'stream_key': BACKFILL_KEY, 'latest_stream_block': 100, 'last_processed_block': 100,
        'stream_started_at_block': 100, 'webhook_received_at': datetime.now(timezone.utc),
    }
    with _no_alert_chain():
        qn.run_backfill_step(conn, rpc_client=_FakeRpc(head, {n: _block(n, []) for n in range(101, 160)}),
                             targets=[target], live_start_block=live_after)
    # Live cursor unchanged by the backfill run; backfill cursor advanced independently.
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == live_after
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] > 100
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] < live_after


# ---------------------------------------------------------------------------
# Multi-replica lock (requirement 12).
# ---------------------------------------------------------------------------

def test_advisory_lock_acquired_and_released():
    conn = _LaneConn(advisory_acquired=True)
    assert qn.try_acquire_live_lane_lock(conn) is True
    qn.release_live_lane_lock(conn)  # must not raise


def test_advisory_lock_denied_when_another_replica_holds_it():
    conn = _LaneConn(advisory_acquired=False)
    assert qn.try_acquire_live_lane_lock(conn) is False


# ---------------------------------------------------------------------------
# Lane-state classification (requirement 14).
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime(2026, 7, 10, 15, 50, tzinfo=timezone.utc)


def test_lane_state_live_within_threshold():
    state, lag = qn.classify_quicknode_lane_state(
        chain_head=1000, live_checkpoint_block=998, live_checkpoint_at=_now() - timedelta(seconds=5),
        now=_now(), lag_threshold=10, stale_seconds=300,
    )
    assert state == 'live'
    assert lag == 2


def test_lane_state_degraded_beyond_threshold():
    state, lag = qn.classify_quicknode_lane_state(
        chain_head=1000, live_checkpoint_block=900, live_checkpoint_at=_now() - timedelta(seconds=5),
        now=_now(), lag_threshold=10, stale_seconds=300,
    )
    assert state == 'degraded'
    assert lag == 100


def test_lane_state_stale_when_checkpoint_not_moving():
    state, _lag = qn.classify_quicknode_lane_state(
        chain_head=1000, live_checkpoint_block=998, live_checkpoint_at=_now() - timedelta(seconds=600),
        now=_now(), lag_threshold=10, stale_seconds=300,
    )
    assert state == 'stale'


def test_lane_state_catching_up_when_no_live_checkpoint_but_backfill_advancing():
    state, _lag = qn.classify_quicknode_lane_state(
        chain_head=1000, live_checkpoint_block=None, live_checkpoint_at=None,
        now=_now(), lag_threshold=10, stale_seconds=300, backfill_advancing=True,
    )
    assert state == 'catching_up'


def test_lane_state_failed_flag_wins():
    state, _lag = qn.classify_quicknode_lane_state(
        chain_head=None, live_checkpoint_block=None, live_checkpoint_at=None,
        now=_now(), lag_threshold=10, stale_seconds=300, failed=True,
    )
    assert state == 'failed'


def test_build_lane_status_reports_live_from_checkpoints():
    conn = _LaneConn()
    conn.checkpoints[LIVE_KEY] = {
        'stream_key': LIVE_KEY, 'latest_stream_block': 1000, 'last_processed_block': 998,
        'stream_started_at_block': 990,
        'webhook_received_at': _now() - timedelta(seconds=3),
    }
    status = qn.build_quicknode_live_lane_status(conn, now=_now())
    assert status['state'] == 'live'
    assert status['lag_blocks'] == 2
    assert status['chain_head'] == 1000
    assert status['live_checkpoint_block'] == 998
