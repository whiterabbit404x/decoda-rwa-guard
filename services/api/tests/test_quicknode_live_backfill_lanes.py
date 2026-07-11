"""Tests for the explicit QuickNode live + backfill webhook lanes (real-time fix).

The production incident: the single QuickNode Stream replays sequentially from an old
block (stream_started_at_block=48391739), posting to
POST /api/integrations/quicknode/streams/base — so a freshly-confirmed monitored-wallet
transfer only appears after Stable RPC Polling. The fix adds two INDEPENDENT lanes with
their own routes, checkpoint identities, and detected_by tags, so a QuickNode stream
configured to start at the CURRENT block can detect at the chain tip regardless of the
historical backlog (services/api/app/quicknode_streams.py + main.py):

  POST /api/integrations/quicknode/streams/base-live      lane='live'     -> quicknode:base:live
  POST /api/integrations/quicknode/streams/base-backfill  lane='backfill' -> quicknode:base:backfill

Covers the task's required proofs:
  1.  Existing `base` checkpoint migrates to the backfill lane.
  2.  Live lane starts near the chain head (small lag -> "live").
  3.  Backfill cannot overwrite the live checkpoint (and vice versa).
  4.  A historical backlog does not delay live processing.
  5.  From-address match works.
  6.  To-address match works.
  7.  Address matching is case-insensitive.
  8.  QuickNode-first detection persists detected_by=quicknode_stream.
  9.  Stable-RPC-first detection is not duplicated.
  10. Redis publish happens only after the DB commit.
  11. Live health uses ONLY the live checkpoint.
  12. A generic stream_key=base checkpoint cannot be treated as live.
  13. Missing provider live configuration reports degraded / invalid.
  14. Multiple replicas / duplicate deliveries do not double-persist.
  15. Signature + replay protection still run for the live lane.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from services.api.app import quicknode_streams as qn
from services.api.app.domains import alert_stream

WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
UNRELATED = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
SECRET = 'whsec_test_secret_123'
NONCE = 'test-nonce-abc123'
LIVE_KEY = qn.QUICKNODE_STREAM_KEY_BASE_LIVE
BACKFILL_KEY = qn.QUICKNODE_STREAM_KEY_BASE_BACKFILL
BASE_KEY = qn.QUICKNODE_STREAM_KEY_BASE


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
    """Fake connection: per-stream_key checkpoints + telemetry insert/select + targets.

    Records an ordered ``events`` log so a test can assert a publish happened strictly
    AFTER the commit.
    """

    def __init__(self, *, targets=None, existing_telemetry=None, events=None,
                 checkpoints=None):
        self.targets = targets or []
        self.existing_telemetry = existing_telemetry
        self.checkpoints: dict[str, dict] = dict(checkpoints or {})
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0
        self.events = events if events is not None else []

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


class _FakeRpc:
    def __init__(self, head: int):
        self.head = head

    def call(self, method, params):
        if method == 'eth_blockNumber':
            return hex(self.head)
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


def _call_lane_webhook(*, lane: str, body: bytes, conn: _LaneConn, rpc_head: int | None,
                       monkeypatch, redis=None):
    """Drive process_quicknode_base_stream_webhook end-to-end for a lane."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=body)
    rpc = _FakeRpc(rpc_head) if rpc_head is not None else None
    ctx = [
        patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)),
        patch.object(qn, 'ensure_pilot_schema', lambda _c: None),
        patch.object(qn, '_make_base_rpc_client', lambda: rpc),
    ]
    if redis is not None:
        ctx.append(patch.object(alert_stream, '_get_sync_client', lambda: redis))
    with ctx[0], ctx[1], ctx[2]:
        if redis is not None:
            with ctx[3]:
                return qn.process_quicknode_base_stream_webhook(
                    raw_body=body, signature_header=signature, nonce_header=NONCE,
                    timestamp_header=timestamp, lane=lane,
                )
        return qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=timestamp, lane=lane,
        )


def _now() -> datetime:
    return datetime(2026, 7, 10, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. base checkpoint migrates to backfill (and never seeds live)
# ---------------------------------------------------------------------------

def test_base_checkpoint_migrates_to_backfill_lane():
    conn = _LaneConn(checkpoints={
        BASE_KEY: {
            'stream_key': BASE_KEY, 'latest_stream_block': 48391739,
            'last_processed_block': 48391739, 'stream_started_at_block': 48391739,
            'webhook_received_at': _now(),
        },
    })
    assert qn.seed_backfill_from_base_checkpoint(conn) is True
    backfill = conn.checkpoints[BACKFILL_KEY]
    assert backfill['last_processed_block'] == 48391739
    # It must NOT have seeded the live lane from that old historical block.
    assert LIVE_KEY not in conn.checkpoints


def test_backfill_seed_from_base_is_idempotent_and_never_regresses():
    conn = _LaneConn(checkpoints={
        BASE_KEY: {'stream_key': BASE_KEY, 'last_processed_block': 48391739,
                   'latest_stream_block': 48391739, 'stream_started_at_block': 48391739},
        BACKFILL_KEY: {'stream_key': BACKFILL_KEY, 'last_processed_block': 48500000,
                       'latest_stream_block': 48500000, 'stream_started_at_block': 48391739},
    })
    # Backfill already advancing past base -> a re-run must not drag it back.
    assert qn.seed_backfill_from_base_checkpoint(conn) is False
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] == 48500000


def test_backfill_seed_noop_when_base_never_delivered():
    conn = _LaneConn(checkpoints={})
    assert qn.seed_backfill_from_base_checkpoint(conn) is False
    assert BACKFILL_KEY not in conn.checkpoints


# ---------------------------------------------------------------------------
# 2 & 4. Live lane starts near the head; a backlog never delays it
# ---------------------------------------------------------------------------

def test_live_lane_processes_batch_near_head_and_advances_only_live(monkeypatch):
    head = 48_500_000
    target = _make_target()
    # A huge historical backlog sits on the backfill checkpoint; it must not matter.
    conn = _LaneConn(targets=[target], checkpoints={
        BACKFILL_KEY: {'stream_key': BACKFILL_KEY, 'last_processed_block': 48_391_739,
                       'latest_stream_block': 48_391_739, 'stream_started_at_block': 48_391_739},
    })
    body = _tx_body(tx_hash='0x' + '11' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=head - 2)
    result = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=head, monkeypatch=monkeypatch)
    assert result['persisted'] == 1
    assert result['results'][0]['detected_by'] == 'quicknode_stream'
    # Live checkpoint advanced to the batch's block; backfill untouched by the live batch.
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == head - 2
    assert conn.checkpoints[LIVE_KEY]['latest_stream_block'] == head
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] == 48_391_739


def test_live_lane_batch_log_carries_lane_identity_and_lag(monkeypatch, caplog):
    head = 48_500_000
    target = _make_target()
    conn = _LaneConn(targets=[target])
    body = _tx_body(tx_hash='0x' + '12' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=head - 3)
    with caplog.at_level('INFO', logger='services.api.app.quicknode_streams'):
        _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=head, monkeypatch=monkeypatch)
    assert 'event=quicknode_stream_batch' in caplog.text
    assert 'stream_lane=live' in caplog.text
    assert f'checkpoint_identity={LIVE_KEY}' in caplog.text
    assert f'chain_head={head}' in caplog.text
    assert 'lag_blocks=3' in caplog.text
    assert 'event=quicknode_live_match' in caplog.text


# ---------------------------------------------------------------------------
# 3. Backfill cannot overwrite live checkpoint
# ---------------------------------------------------------------------------

def test_backfill_lane_does_not_touch_live_checkpoint(monkeypatch):
    target = _make_target()
    conn = _LaneConn(targets=[target], checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': 48_499_998,
                   'latest_stream_block': 48_500_000, 'stream_started_at_block': 48_499_998,
                   'webhook_received_at': _now()},
    })
    body = _tx_body(tx_hash='0x' + '13' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=48_400_000)
    result = _call_lane_webhook(lane='backfill', body=body, conn=conn, rpc_head=None, monkeypatch=monkeypatch)
    assert result['results'][0]['detected_by'] == 'quicknode_stream_backfill'
    # Backfill advanced only its own key; the live checkpoint is byte-for-byte unchanged.
    assert conn.checkpoints[BACKFILL_KEY]['last_processed_block'] == 48_400_000
    assert conn.checkpoints[LIVE_KEY]['last_processed_block'] == 48_499_998
    assert conn.checkpoints[LIVE_KEY]['latest_stream_block'] == 48_500_000


# ---------------------------------------------------------------------------
# 5, 6, 7. from / to / case-insensitive matching (live lane)
# ---------------------------------------------------------------------------

def test_live_lane_matches_from_address(monkeypatch):
    conn = _LaneConn(targets=[_make_target()])
    body = _tx_body(tx_hash='0x' + '21' * 32, tx_from=WALLET, tx_to=UNRELATED, block=100)
    result = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=110, monkeypatch=monkeypatch)
    assert result['persisted'] == 1
    assert result['results'][0]['wallet_transfer_direction'] == 'outbound'


def test_live_lane_matches_to_address(monkeypatch):
    conn = _LaneConn(targets=[_make_target()])
    body = _tx_body(tx_hash='0x' + '22' * 32, tx_from=UNRELATED, tx_to=WALLET, block=100)
    result = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=110, monkeypatch=monkeypatch)
    assert result['persisted'] == 1
    assert result['results'][0]['wallet_transfer_direction'] == 'inbound'


def test_live_lane_matching_is_case_insensitive(monkeypatch):
    # Target configured with a checksummed/upper wallet; stream delivers lowercase.
    conn = _LaneConn(targets=[_make_target(wallet=WALLET.upper())])
    body = _tx_body(tx_hash='0x' + '23' * 32, tx_from=WALLET.lower(), tx_to=UNRELATED, block=100)
    result = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=110, monkeypatch=monkeypatch)
    assert result['persisted'] == 1


def test_live_lane_ignores_unrelated_transaction(monkeypatch):
    conn = _LaneConn(targets=[_make_target()])
    body = _tx_body(tx_hash='0x' + '24' * 32, tx_from=UNRELATED, tx_to=COUNTERPARTY, block=100)
    result = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=110, monkeypatch=monkeypatch)
    assert result['persisted'] == 0
    assert result['matched'] == 0
    assert conn.telemetry_inserts == []


# ---------------------------------------------------------------------------
# 8 & 9. QuickNode-first persists; Stable-RPC-first is deduped
# ---------------------------------------------------------------------------

def test_quicknode_first_detection_persists(monkeypatch):
    conn = _LaneConn(targets=[_make_target()])
    body = _tx_body(tx_hash='0x' + '31' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=100)
    result = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=105, monkeypatch=monkeypatch)
    assert result['persisted'] == 1
    assert len(conn.telemetry_inserts) == 1
    assert result['results'][0]['detected_by'] == 'quicknode_stream'


def test_stable_rpc_first_detection_is_not_duplicated(monkeypatch):
    existing = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _LaneConn(targets=[_make_target()], existing_telemetry=existing)
    body = _tx_body(tx_hash='0x' + '32' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=100)
    result = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=105, monkeypatch=monkeypatch)
    assert result['duplicates'] == 1
    assert result['persisted'] == 0
    assert conn.telemetry_inserts == []


def test_duplicate_delivery_across_replicas_does_not_double_persist(monkeypatch):
    """Two replicas both handed the same tx: the first persists, the second (seeing the
    now-existing row) is suppressed — no second customer-visible row."""
    tx_hash = '0x' + '33' * 32
    conn = _LaneConn(targets=[_make_target()])
    body = _tx_body(tx_hash=tx_hash, tx_from=WALLET, tx_to=COUNTERPARTY, block=100)
    first = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=105, monkeypatch=monkeypatch)
    assert first['persisted'] == 1
    # Second replica: the telemetry row now exists for this tx.
    conn.existing_telemetry = {'id': str(uuid.uuid4()), 'event_type': 'wallet_transfer_detected',
                               'detected_by': 'quicknode_stream'}
    second = _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=105, monkeypatch=monkeypatch)
    assert second['persisted'] == 0
    assert second['duplicates'] == 1
    assert len(conn.telemetry_inserts) == 1


# ---------------------------------------------------------------------------
# 10. Redis publish only after commit
# ---------------------------------------------------------------------------

def test_live_lane_publishes_only_after_commit(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    events: list[str] = []
    conn = _LaneConn(targets=[_make_target()], events=events)
    redis = _FakeRedis(events=events)
    body = _tx_body(tx_hash='0x' + '41' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=100)
    _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=105, monkeypatch=monkeypatch, redis=redis)
    assert 'commit' in events and 'publish' in events
    assert events.index('commit') < events.index('publish')
    assert len(redis.xadds) == 1


def test_live_match_log_reports_redis_publish_success(monkeypatch, caplog):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    conn = _LaneConn(targets=[_make_target()])
    redis = _FakeRedis()
    body = _tx_body(tx_hash='0x' + '42' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=100)
    with caplog.at_level('INFO', logger='services.api.app.quicknode_streams'):
        _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=105, monkeypatch=monkeypatch, redis=redis)
    assert 'event=quicknode_live_match' in caplog.text
    assert 'redis_publish_success=true' in caplog.text
    assert 'persisted=true' in caplog.text


# ---------------------------------------------------------------------------
# 11 & 12. Live health uses ONLY the live checkpoint
# ---------------------------------------------------------------------------

def test_live_health_uses_only_live_checkpoint():
    conn = _LaneConn(checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': 48_499_997,
                   'latest_stream_block': 48_500_000, 'stream_started_at_block': 48_499_990,
                   'webhook_received_at': _now()},
    })
    status = qn.build_quicknode_live_lane_status(conn, now=_now())
    assert status['state'] == 'live'
    assert status['lag_blocks'] == 3
    assert status['live_checkpoint_block'] == 48_499_997


def test_generic_base_checkpoint_alone_is_not_live():
    """A checkpoint on stream_key='base' (the historical delivery lane) must NEVER be
    reported as live — only the dedicated live checkpoint drives live health."""
    conn = _LaneConn(checkpoints={
        BASE_KEY: {'stream_key': BASE_KEY, 'last_processed_block': 48_391_739,
                   'latest_stream_block': 48_391_739, 'stream_started_at_block': 48_391_739,
                   'webhook_received_at': _now()},
    })
    status = qn.build_quicknode_live_lane_status(conn, now=_now())
    assert status['state'] is None
    assert status['live_checkpoint_block'] is None


def test_advancing_backfill_shows_catching_up_not_live():
    conn = _LaneConn(checkpoints={
        BACKFILL_KEY: {'stream_key': BACKFILL_KEY, 'last_processed_block': 48_400_000,
                       'latest_stream_block': 48_400_000, 'stream_started_at_block': 48_391_739,
                       'webhook_received_at': _now()},
    })
    status = qn.build_quicknode_live_lane_status(conn, now=_now())
    # Historical catch-up is visible, but it is NOT painted green "live".
    assert status['state'] == 'catching_up'


# ---------------------------------------------------------------------------
# 13. Missing / degraded live configuration
# ---------------------------------------------------------------------------

def test_live_configuration_invalid_without_secret(monkeypatch):
    monkeypatch.delenv('QUICKNODE_STREAMS_SECRET', raising=False)
    assert qn.quicknode_live_configuration_valid() is False
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    assert qn.quicknode_live_configuration_valid() is True


def test_live_lane_far_behind_head_reports_degraded(monkeypatch, caplog):
    head = 48_500_000
    conn = _LaneConn(targets=[_make_target()])
    # A live batch whose blocks are tens of thousands behind the head -> not at the tip.
    body = _tx_body(tx_hash='0x' + '51' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=48_391_739)
    with caplog.at_level('INFO', logger='services.api.app.quicknode_streams'):
        _call_lane_webhook(lane='live', body=body, conn=conn, rpc_head=head, monkeypatch=monkeypatch)
    assert 'degraded=true' in caplog.text
    assert 'event=quicknode_live_lane_degraded' in caplog.text
    # And the read-path derives Degraded from the same lag.
    status = qn.build_quicknode_live_lane_status(conn, now=datetime.now(timezone.utc))
    assert status['state'] == 'degraded'


def test_lane_state_degraded_when_stale():
    stale_at = _now() - timedelta(seconds=10_000)
    conn = _LaneConn(checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': 48_499_999,
                   'latest_stream_block': 48_500_000, 'stream_started_at_block': 48_499_990,
                   'webhook_received_at': stale_at},
    })
    status = qn.build_quicknode_live_lane_status(conn, now=_now())
    assert status['state'] == 'stale'


# ---------------------------------------------------------------------------
# 15. Signature + replay protection still run for the live lane
# ---------------------------------------------------------------------------

def test_live_lane_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    body = _tx_body(tx_hash='0x' + '61' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=100)
    with pytest.raises(HTTPException) as exc:
        qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header='deadbeef', nonce_header=NONCE,
            timestamp_header=_now_ts(), lane='live',
        )
    assert exc.value.status_code == 401


def test_live_lane_rejects_stale_timestamp(monkeypatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    body = _tx_body(tx_hash='0x' + '62' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=100)
    stale = str(int(time.time()) - 3600)
    signature = _sign(SECRET, nonce=NONCE, timestamp=stale, body=body)
    with pytest.raises(HTTPException) as exc:
        qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=stale, lane='backfill',
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Lane normalization + readiness marker
# ---------------------------------------------------------------------------

def test_normalize_lane_is_fail_closed_to_base():
    assert qn._normalize_lane('LIVE') == 'live'
    assert qn._normalize_lane('backfill') == 'backfill'
    assert qn._normalize_lane('base') == 'base'
    assert qn._normalize_lane(None) == 'base'
    assert qn._normalize_lane('bogus-injected') == 'base'


# ---------------------------------------------------------------------------
# Dedicated production routes exist and pass the EXPLICIT lane (task step 3)
# ---------------------------------------------------------------------------

def test_base_live_route_passes_explicit_live_lane(monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    captured: dict = {}

    def _fake_handler(**kw):
        captured.update(kw)
        return {'received': True, 'results': []}

    monkeypatch.setattr(api_main, 'process_quicknode_base_stream_webhook', _fake_handler)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = json.dumps({'tx_hash': '0x' + '71' * 32}).encode()
    with caplog.at_level('INFO', logger='services.api.app.main'):
        response = client.post(
            '/api/integrations/quicknode/streams/base-live',
            content=body,
            headers={'content-type': 'application/json', 'x-qn-signature': 'x',
                     'x-qn-nonce': NONCE, 'x-qn-timestamp': _now_ts()},
        )
    assert response.status_code == 200
    assert captured.get('lane') == 'live'
    assert 'quicknode_stream_route_hit stream_lane=live stream_key=base-live' in caplog.text


def test_base_backfill_route_passes_explicit_backfill_lane(monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    captured: dict = {}
    monkeypatch.setattr(api_main, 'process_quicknode_base_stream_webhook',
                        lambda **kw: captured.update(kw) or {'received': True, 'results': []})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.post(
        '/api/integrations/quicknode/streams/base-backfill',
        content=json.dumps({'tx_hash': '0x' + '72' * 32}).encode(),
        headers={'content-type': 'application/json', 'x-qn-signature': 'x',
                 'x-qn-nonce': NONCE, 'x-qn-timestamp': _now_ts()},
    )
    assert response.status_code == 200
    assert captured.get('lane') == 'backfill'


def test_base_live_and_backfill_health_endpoints_ready():
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    client = TestClient(api_main.app, raise_server_exceptions=False)
    assert client.get('/api/integrations/quicknode/streams/base-live').json()['status'] == \
        'quicknode_streams_base_live_endpoint_ready'
    assert client.get('/api/integrations/quicknode/streams/base-backfill').json()['status'] == \
        'quicknode_streams_base_backfill_endpoint_ready'


def test_legacy_base_route_still_defaults_to_base_lane(monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    captured: dict = {}
    monkeypatch.setattr(api_main, 'process_quicknode_base_stream_webhook',
                        lambda **kw: captured.update(kw) or {'received': True, 'results': []})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.post(
        '/api/integrations/quicknode/streams/base',
        content=json.dumps({'tx_hash': '0x' + '73' * 32}).encode(),
        headers={'content-type': 'application/json', 'x-qn-signature': 'x',
                 'x-qn-nonce': NONCE, 'x-qn-timestamp': _now_ts()},
    )
    assert response.status_code == 200
    # The legacy route must NOT pass a lane (defaults to 'base'), preserving behavior.
    assert 'lane' not in captured


def test_live_lane_started_marker_reports_required_fields(monkeypatch, caplog):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    conn = _LaneConn(checkpoints={
        LIVE_KEY: {'stream_key': LIVE_KEY, 'last_processed_block': 48_499_998,
                   'latest_stream_block': 48_500_000, 'stream_started_at_block': 48_499_990,
                   'webhook_received_at': _now()},
    })
    with caplog.at_level('INFO', logger='services.api.app.quicknode_streams'):
        out = qn.emit_quicknode_live_lane_started(
            conn, rpc_client=_FakeRpc(48_500_000), deployment_commit_sha='abc123',
        )
    assert out['chain_head'] == 48_500_000
    assert out['checkpoint_block'] == 48_499_998
    assert out['lag_blocks'] == 2
    assert out['configuration_valid'] is True
    assert 'event=quicknode_live_lane_started' in caplog.text
    assert 'stream_key=base-live' in caplog.text
    assert f'checkpoint_identity={LIVE_KEY}' in caplog.text
    assert 'deployment_commit_sha=abc123' in caplog.text
    assert 'lag_blocks=2' in caplog.text
    assert 'configuration_valid=true' in caplog.text
