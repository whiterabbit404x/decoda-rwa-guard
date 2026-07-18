"""RPC provider circuit breaker + bounded chain-head cache.

Covers the two storm-prevention fixes:

Part 1 — circuit breaker (evm_activity_provider):
  * An existing provider backoff window is NEVER extended by a skipped/re-observed
    call (the per-webhook chain-head storm must not push backoff_until forward).
  * A newly observed 429 (host not already benched) DOES arm a fresh window and
    logs rpc_provider_backoff_set.

Part 2 — bounded chain-head cache (quicknode_streams):
  * The live webhook lane reuses a cached chain head; it does NOT call
    eth_blockNumber for every incoming webhook batch.
  * The refresh is skipped entirely while every RPC provider is in backoff.
  * An UNKNOWN chain head is reported lag_status=unknown and never fabricates a
    latest_stream_block that would make the read path paint a false green "live".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from services.api.app import evm_activity_provider as eap
from services.api.app import quicknode_streams as qn

EAP_LOGGER = 'services.api.app.evm_activity_provider'
QN_LOGGER = 'services.api.app.quicknode_streams'
ALCHEMY_HOST = 'base-mainnet.g.alchemy.com'


# ===========================================================================
# Part 1 — circuit breaker: skipped calls must not extend backoff
# ===========================================================================

def _messages(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records)


def test_existing_backoff_not_extended_by_skipped_call(monkeypatch, caplog):
    """Re-recording a 429 for a host already in an active window keeps the exact same
    backoff_until (backoff_extended=false) and logs rpc_call_skipped_existing_backoff,
    NOT a second rpc_provider_backoff_set."""
    monkeypatch.setenv('APP_ENV', 'production')          # deterministic 600s floor
    monkeypatch.setenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '0')  # deterministic window
    eap.reset_rpc_provider_state()

    with caplog.at_level(logging.INFO, logger=EAP_LOGGER):
        eap.record_rpc_rate_limited(None, host=ALCHEMY_HOST)          # newly observed → arm
        first = eap.rpc_provider_backoff_status()['backoff_until']
        assert first is not None
        # Two more observations while the window is still open (the storm).
        eap.record_rpc_rate_limited(None, host=ALCHEMY_HOST)
        eap.record_rpc_rate_limited(999.0, host=ALCHEMY_HOST)         # even a bigger value
        second = eap.rpc_provider_backoff_status()['backoff_until']

    assert second == first, 'a skipped/re-observed call must NOT move backoff_until forward'
    text = _messages(caplog)
    # Exactly one arm event; the later observations are logged as skips.
    assert text.count('event=rpc_provider_backoff_set') == 1, text
    assert 'event=rpc_call_skipped_existing_backoff' in text
    assert 'backoff_extended=false' in text
    assert f'rpc_host={ALCHEMY_HOST}' in text


def test_new_429_failure_creates_backoff(monkeypatch, caplog):
    """A real 429 from the JSON-RPC client (host not already benched) arms a fresh
    window and logs rpc_provider_backoff_set with backoff_extended=true."""
    url = f'https://{ALCHEMY_HOST}/v2/secret-key'
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')       # no inline retry; go straight to arm
    eap.reset_rpc_provider_state()
    assert eap.rpc_provider_backoff_active() is False

    err = urllib.error.HTTPError(url, 429, 'Too Many Requests', {'Retry-After': '42'}, None)
    with caplog.at_level(logging.WARNING, logger=EAP_LOGGER):
        with patch.object(eap.request, 'urlopen', side_effect=err):
            try:
                eap.JsonRpcClient(url).call('eth_blockNumber', [])
            except Exception:
                pass

    assert eap.rpc_provider_backoff_active() is True
    text = _messages(caplog)
    assert 'event=rpc_provider_backoff_set' in text
    assert 'backoff_extended=true' in text
    assert 'retry_after_seconds=42' in text
    # No secret / credentialed path fragment ever leaks.
    assert 'secret-key' not in text and '/v2/' not in text
    # The status snapshot exposes the retry_after + original failure time for the
    # onboarding "retry disabled during backoff" affordance (part 6), no secret.
    status = eap.rpc_provider_backoff_status()
    assert status['retry_after_seconds'] == 42
    assert status['first_failure_at'] is not None
    assert 'secret-key' not in str(status)


def test_failover_skip_of_backed_off_host_does_not_extend_window(monkeypatch, caplog):
    """The failover client skipping an already-benched provider must not re-arm it:
    the window recorded when it was first benched is preserved untouched."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '0')
    eap.reset_rpc_provider_state()
    eap.record_rpc_rate_limited(None, host=ALCHEMY_HOST)
    before = eap.rpc_provider_backoff_status()['backoff_until']

    # A failover client with two providers where the benched host is skipped and the
    # other host TLS-fails. This must raise all_providers_unavailable WITHOUT arming
    # or extending any window (a skip is not a newly observed failure).
    quicknode = 'holy-proportionate-dust.base-mainnet.quiknode.pro'
    client = eap.FailoverJsonRpcClient([f'https://{ALCHEMY_HOST}/v2/k', f'https://{quicknode}/x'])

    def _tls_fail(url, *a, **k):
        raise urllib.error.URLError('ssl: TLSV1_ALERT_INTERNAL_ERROR')

    with caplog.at_level(logging.INFO, logger=EAP_LOGGER):
        with patch.object(eap.request, 'urlopen', side_effect=_tls_fail):
            for _ in range(4):                            # the "every webhook block" storm
                try:
                    client.call('eth_blockNumber', [])
                except Exception:
                    pass

    after = eap.rpc_provider_backoff_status()['backoff_until']
    assert after == before, 'skipping a benched provider must not extend its window'
    # The QuickNode TLS failure is not a 429, so it never arms a backoff either.
    assert eap.backoff_hosts() == [ALCHEMY_HOST]


# ===========================================================================
# Part 2 — bounded chain-head cache
# ===========================================================================

class _CountingRpc:
    """Fake RPC client counting eth_blockNumber calls; returns a fixed head."""

    def __init__(self, head: int):
        self.head = head
        self.calls = 0

    def call(self, method, params):
        if method == 'eth_blockNumber':
            self.calls += 1
            return hex(self.head)
        return None


def test_cached_chain_head_calls_rpc_at_most_once_per_ttl(monkeypatch):
    """Many chain-head reads inside the TTL make exactly ONE eth_blockNumber call —
    the fix for 'do not execute a new chain-head RPC call for every webhook batch'."""
    monkeypatch.delenv('QUICKNODE_CHAIN_HEAD_CACHE_SECONDS', raising=False)  # default 45s
    eap.reset_rpc_provider_state()
    qn.reset_chain_head_cache()
    rpc = _CountingRpc(head=50_000_000)

    heads = [qn.get_cached_base_chain_head(rpc) for _ in range(8)]

    assert rpc.calls == 1, f'expected one RPC call across 8 reads, got {rpc.calls}'
    assert all(h == 50_000_000 for h in heads)


def test_cached_chain_head_refreshes_after_ttl(monkeypatch):
    monkeypatch.setenv('QUICKNODE_CHAIN_HEAD_CACHE_SECONDS', '30')
    eap.reset_rpc_provider_state()
    qn.reset_chain_head_cache()
    rpc = _CountingRpc(head=50_000_000)

    assert qn.get_cached_base_chain_head(rpc) == 50_000_000
    assert rpc.calls == 1
    # Age the cache past its TTL, then a read refreshes exactly once more.
    with qn._CHAIN_HEAD_CACHE_LOCK:
        qn._CHAIN_HEAD_CACHE['at_monotonic'] = time.monotonic() - 31.0
    rpc.head = 50_000_005
    assert qn.get_cached_base_chain_head(rpc) == 50_000_005
    assert rpc.calls == 2


def test_cached_chain_head_skips_rpc_when_all_providers_backed_off(monkeypatch, caplog):
    """While every provider is benched, the head refresh makes NO network call and
    returns None (unknown) — the circuit breaker extends to the webhook path too."""
    monkeypatch.setenv('QUICKNODE_CHAIN_HEAD_CACHE_SECONDS', '0')  # force refresh attempt
    eap.reset_rpc_provider_state()
    qn.reset_chain_head_cache()
    eap.record_rpc_rate_limited(None)                 # arm global backoff (all providers)
    assert eap.rpc_provider_backoff_active() is True
    rpc = _CountingRpc(head=50_000_000)

    with caplog.at_level(logging.INFO, logger=QN_LOGGER):
        head = qn.get_cached_base_chain_head(rpc)

    assert head is None, 'must report unknown, not dial a benched provider'
    assert rpc.calls == 0
    assert 'event=quicknode_chain_head_refresh_skipped' in _messages(caplog)


def test_unknown_chain_head_classifies_degraded_not_live():
    """Requirement 4: a None chain head must NEVER classify the live lane as 'live'
    (i.e. degraded must not be false-by-default just because the head is unknown)."""
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    state, lag = qn.classify_quicknode_lane_state(
        chain_head=None,
        live_checkpoint_block=49_000_000,
        live_checkpoint_at=now,
        now=now,
        lag_threshold=10,
        stale_seconds=300,
    )
    assert state != 'live', 'unknown head must not be painted live'
    assert state == 'degraded'
    assert lag is None


class _RecordingConn:
    """Connection stub recording executed SQL + params (checkpoint write assertions)."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((' '.join(str(sql).split()), tuple(params or ())))

        class _R:
            def fetchone(self_inner):
                return None

            def fetchall(self_inner):
                return []
        return _R()

    def commit(self):
        pass


def test_unknown_head_advance_does_not_fabricate_latest_stream_block():
    """When the head is unknown the cursor advances but latest_stream_block is written
    as NULL (kept at last-known), never fabricated from the processed block."""
    conn = _RecordingConn()
    received = datetime(2026, 7, 18, tzinfo=timezone.utc)
    qn._advance_lane_checkpoint(
        conn, stream_key=qn.QUICKNODE_STREAM_KEY_BASE_LIVE, block=49_000_500,
        latest_block=None, latest_block_unknown=True, received_at=received,
    )
    inserts = [(s, p) for s, p in conn.executed if s.lower().startswith('insert into quicknode_stream_checkpoints')]
    assert inserts, 'a checkpoint upsert must have run'
    sql, params = inserts[-1]
    # The VALUES clause writes NULL for latest_stream_block (never the processed block).
    assert 'VALUES (%s, NULL,' in sql, sql
    # The fabricated head 49_000_500 must NOT be passed as a latest_stream_block param.
    # (It legitimately appears as last_processed_block / stream_started_at_block.)
    assert 'latest_stream_block = GREATEST' not in sql, 'unknown branch must not GREATEST the head'


def test_known_head_advance_stores_head_as_latest_stream_block():
    """Sanity: the normal (known-head) path still stores the head so lag can be read."""
    conn = _RecordingConn()
    received = datetime(2026, 7, 18, tzinfo=timezone.utc)
    qn._advance_lane_checkpoint(
        conn, stream_key=qn.QUICKNODE_STREAM_KEY_BASE_LIVE, block=49_000_500,
        latest_block=49_000_510, latest_block_unknown=False, received_at=received,
    )
    inserts = [(s, p) for s, p in conn.executed if s.lower().startswith('insert into quicknode_stream_checkpoints')]
    assert inserts
    sql, params = inserts[-1]
    assert 49_000_510 in params, 'the observed head must be stored as latest_stream_block'
    assert 'latest_stream_block = GREATEST' in sql


# ===========================================================================
# Part 2 — integration: a live webhook batch reuses the cached head
# ===========================================================================

SECRET = 'test-quicknode-secret'
NONCE = 'nonce-abc'


class _LaneConn:
    def __init__(self):
        self.checkpoints: dict[str, dict] = {}

    def execute(self, query, params=None):
        q = (query or '').strip().lower()

        class _Rows:
            def __init__(self_inner, rows):
                self_inner._rows = rows

            def fetchone(self_inner):
                return self_inner._rows[0] if self_inner._rows else None

            def fetchall(self_inner):
                return list(self_inner._rows)

        if q.startswith('create table') or 'advisory' in q:
            return _Rows([{'acquired': True}])
        if q.startswith('insert into quicknode_stream_checkpoints'):
            return _Rows([])
        if 'from quicknode_stream_checkpoints' in q:
            return _Rows([])
        if 'from targets' in q:
            return _Rows([])           # no targets → no matches, but the head is still read
        return _Rows([])

    def commit(self):
        pass

    def rollback(self):
        pass


@contextmanager
def _mock_pg(conn):
    yield conn


def _live_body(block: int) -> bytes:
    return json.dumps({
        'tx_hash': '0x' + 'a' * 64, 'from': '0x' + '1' * 40, 'to': '0x' + '2' * 40,
        'value': '1000000000000000000', 'block_number': block, 'chain_id': 8453,
    }).encode()


def test_live_webhook_batches_reuse_cached_head(monkeypatch):
    """Two consecutive live webhook batches trigger only ONE eth_blockNumber call:
    the second batch reuses the cached head (no per-webhook chain-head RPC)."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.delenv('QUICKNODE_CHAIN_HEAD_CACHE_SECONDS', raising=False)
    eap.reset_rpc_provider_state()
    qn.reset_chain_head_cache()
    conn = _LaneConn()
    rpc = _CountingRpc(head=50_000_000)

    with (
        patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)),
        patch.object(qn, 'ensure_pilot_schema', lambda _c: None),
        patch.object(qn, '_make_base_rpc_client', lambda: rpc),
    ):
        for block in (49_999_900, 49_999_901):
            body = _live_body(block)
            ts = str(int(time.time()))
            sig = hmac.new(SECRET.encode(), NONCE.encode() + ts.encode() + body, hashlib.sha256).hexdigest()
            qn.process_quicknode_base_stream_webhook(
                raw_body=body, signature_header=sig, nonce_header=NONCE,
                timestamp_header=ts, lane=qn.LANE_LIVE,
            )

    assert rpc.calls == 1, f'two webhook batches must share one chain-head call, got {rpc.calls}'
