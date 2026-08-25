"""Stream Latency task: profiling + regression coverage for the /base-live hot path.

Root-cause profiling for the "QuickNode Stream is not actually realtime" incident
(observed ~15 minute detection delay, lag_blocks up to 105 on a real Base Mainnet
transfer). This file:

  1. Generates a realistic synthetic QuickNode "Block with Receipts" payload
     (280 transactions, ~780 KB serialized — inside the observed 600 KB-1.3 MB /
     100-300+ tx production range) and drives it through the REAL, unmodified
     production code path (``process_quicknode_base_stream_webhook``), so the
     measured stage timings reflect actual code, not a guess.
  2. Asserts the new structured per-stage latency instrumentation
     (``event=quicknode_stream_latency`` / ``event=quicknode_stream_match_latency``
     / ``event=quicknode_stream_decode_latency``) is actually emitted with every
     required field, so "Add structured latency metrics/logging" is a tested
     contract, not just a log line nobody verifies.
  3. Proves the new Base-wallet-target cache removes the per-block DB round trip
     (test requirement 9: "ingestion path does not repeatedly hit DB unnecessarily")
     while still propagating a monitored-address change within one bounded TTL
     window (test requirement 8: "monitored address updates propagate safely").

DB-side stage timings measured here reflect an in-process fake connection (no
network), NOT real Railway Postgres round-trip latency — this sandbox has no route
to production Postgres/Redis. They still correctly prove relative call-COUNT
reduction from caching. The CPU-only stages (gunzip/json_parse/extract/normalize)
are real wall-clock measurements, independent of network conditions.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from services.api.app import quicknode_streams as qn

WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
SECRET = 'whsec_test_secret_123'
NONCE = 'test-nonce-abc123'
MATCHED_TX_HASH = '0x42eb6fb953a32dc80fef0f62b4eadfa0fed18c7129d68924cd65bdb37e25a51'
BLOCK_NUMBER = 50422646


@pytest.fixture(autouse=True)
def _enable_realtime_streams_mode(monkeypatch):
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)


def _sign(secret: str, *, nonce: str, timestamp: str, body: bytes) -> str:
    signing_input = nonce.encode('utf-8') + timestamp.encode('utf-8') + body
    return hmac.new(secret.encode('utf-8'), signing_input, hashlib.sha256).hexdigest()


def _now_ts() -> str:
    return str(int(time.time()))


def _make_target(*, target_id: str | None = None, wallet_address: str = WALLET_ADDR) -> dict:
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Treasury Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'chain_id': 8453,
        'wallet_address': wallet_address,
        'contract_identifier': None,
        'asset_id': None,
        'target_metadata': {},
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
        'updated_by_user_id': str(uuid.uuid4()),
        'created_by_user_id': None,
    }


def _make_realistic_base_block_payload(
    *,
    tx_count: int = 280,
    block_number: int = BLOCK_NUMBER,
    matched_index: int | None = 137,
    matched_wallet: str = WALLET_ADDR,
    matched_tx_hash: str = MATCHED_TX_HASH,
    seed: int = 42,
) -> bytes:
    """A synthetic "Block with Receipts" payload shaped like real QuickNode Streams output.

    Deterministic (fixed seed) so repeated calls are byte-identical, and sized (by
    default) to ~780 KB / 280 tx — the midpoint of the production-observed 600 KB-
    1.3 MB / 100-300+ tx range documented in the Stream Latency task. Set
    ``matched_index=None`` for an all-miss block (the common case: no monitored
    wallet appears anywhere in the batch).
    """
    rng = random.Random(seed)
    txs: list[dict] = []
    receipts: list[dict] = []
    for i in range(tx_count):
        is_match = matched_index is not None and i == matched_index
        tx_hash = matched_tx_hash if is_match else '0x' + rng.randbytes(32).hex()
        from_addr = matched_wallet if is_match else '0x' + rng.randbytes(20).hex()
        to_addr = '0x' + rng.randbytes(20).hex()
        input_data = '0x' + rng.randbytes(rng.randint(20, 400)).hex()
        txs.append({
            'hash': tx_hash,
            'from': from_addr,
            'to': to_addr,
            'value': hex(rng.randint(0, 10 ** 18)),
            'input': input_data,
            'gas': hex(rng.randint(21_000, 300_000)),
            'gasPrice': hex(rng.randint(10 ** 8, 10 ** 10)),
            'nonce': hex(rng.randint(0, 10_000)),
            'blockNumber': hex(block_number),
            'type': '0x2',
        })
        logs = []
        for _ in range(rng.randint(1, 5)):
            logs.append({
                'address': '0x' + rng.randbytes(20).hex(),
                'topics': ['0x' + rng.randbytes(32).hex() for _ in range(rng.randint(1, 3))],
                'data': '0x' + rng.randbytes(rng.randint(64, 384)).hex(),
            })
        receipts.append({
            'transactionHash': tx_hash,
            'status': '0x1',
            'gasUsed': hex(rng.randint(21_000, 250_000)),
            'logs': logs,
        })
    payload = [{
        'block': {
            'number': hex(block_number),
            'timestamp': hex(int(datetime.now(timezone.utc).timestamp())),
            'transactions': txs,
        },
        'receipts': receipts,
    }]
    return json.dumps(payload).encode('utf-8')


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CountingFakeConnection:
    """Same SQL-dispatch-by-text fake used across the QuickNode test suite, plus a
    per-query-kind call counter so a test can assert the target cache actually
    avoids repeat round trips (task requirement: "ingestion path does not
    repeatedly hit DB unnecessarily")."""

    def __init__(self, *, targets: list[dict]):
        self.targets = targets
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0
        self.query_counts: dict[str, int] = {}

    def _count(self, key: str) -> None:
        self.query_counts[key] = self.query_counts.get(key, 0) + 1

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if 'from targets' in q:
            self._count('load_targets')
            return _Rows(self.targets)
        if 'from assets' in q:
            self._count('load_asset_context')
            return _Rows([])
        if 'from telemetry_events' in q and 'select' in q:
            self._count('telemetry_dedupe_check')
            return _Rows([])  # never an existing row: every match takes the "processed" branch
        if q.startswith('insert into telemetry_events'):
            self._count('telemetry_insert')
            self.telemetry_inserts.append(tuple(params or ()))
            return _Rows([])
        if 'quicknode_stream_checkpoints' in q:
            self._count('checkpoint')
            return _Rows([])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        pass

    @contextmanager
    def transaction(self):
        yield


@contextmanager
def _mock_pg(connection):
    yield connection


def _call_live_webhook(body: bytes):
    """Drive the real /base-live handler with the two stable-polling alert-rule
    functions patched to no-ops (they normally open their own committed DB
    connection — see _run_webhook_with_patched_alert_rules in
    test_quicknode_streams_base_webhook.py for the same pattern), so alert_chain_ms
    measures this module's own call overhead, not unrelated fake-DB behavior."""
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=body)
    with patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         patch.object(qn, '_wallet_transfer_smoke_alert', lambda **_kw: 'smoke-alert-id'), \
         patch.object(qn, '_strategic_infrastructure_guard_alert', lambda **_kw: 'sig-alert-id'):
        return qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=timestamp, lane='live',
        )


# ---------------------------------------------------------------------------
# Payload realism sanity check
# ---------------------------------------------------------------------------

def test_synthetic_payload_matches_realistic_quicknode_size():
    """The benchmark payload lands inside the production-observed 600 KB-1.3 MB /
    100-300+ tx range, so timings measured against it are representative rather
    than a strawman."""
    body = _make_realistic_base_block_payload()
    assert 600_000 <= len(body) <= 1_300_000
    parsed = json.loads(body)
    assert 100 <= len(parsed[0]['block']['transactions']) <= 300


# ---------------------------------------------------------------------------
# Target requirement 9: ingestion path does not repeatedly hit DB unnecessarily
# ---------------------------------------------------------------------------

def test_target_cache_avoids_repeated_db_query_within_ttl(monkeypatch):
    qn.reset_base_wallet_target_cache()
    conn = _CountingFakeConnection(targets=[_make_target()])
    body = _make_realistic_base_block_payload(matched_index=None)  # common case: no match
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)):
        _call_live_webhook(body)
        _call_live_webhook(body)
        _call_live_webhook(body)
    # Three webhook calls (three simulated blocks), but the target list is only
    # queried ONCE — the other two reuse the cached, already-resolved result.
    assert conn.query_counts.get('load_targets', 0) == 1
    qn.reset_base_wallet_target_cache()


def test_target_cache_expires_and_repropagates_updated_targets(monkeypatch):
    """Requirement 8: monitored address updates propagate safely (within one
    bounded TTL window — never silently forever)."""
    qn.reset_base_wallet_target_cache()
    monkeypatch.setenv('QUICKNODE_TARGET_CACHE_SECONDS', '10')
    original_target = _make_target(wallet_address=WALLET_ADDR)
    conn = _CountingFakeConnection(targets=[original_target])
    body_for_original = _make_realistic_base_block_payload(
        tx_count=10, matched_index=3, matched_wallet=WALLET_ADDR,
    )
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)):
        result = _call_live_webhook(body_for_original)
        assert result['matched'] == 1

        # A brand-new monitored wallet is added; still well within the TTL window,
        # so it must NOT be visible yet — the cache is doing its job.
        new_wallet = '0x' + ('ab' * 20)
        conn.targets = [original_target, _make_target(wallet_address=new_wallet)]
        body_for_new = _make_realistic_base_block_payload(
            tx_count=10, matched_index=3, matched_wallet=new_wallet,
        )
        result = _call_live_webhook(body_for_new)
        assert result['matched'] == 0  # still using the stale cached target set

        # Force the cache stale (simulates the TTL elapsing) and retry: the new
        # wallet is now picked up — propagation is bounded, not silently forever.
        with qn._TARGET_CACHE_LOCK:
            qn._TARGET_CACHE['at_monotonic'] -= 3600
        result = _call_live_webhook(body_for_new)
        assert result['matched'] == 1
    qn.reset_base_wallet_target_cache()


# ---------------------------------------------------------------------------
# Task 1: structured latency instrumentation is real and asserted, not just logged
# ---------------------------------------------------------------------------

_LATENCY_FIELD_RE = re.compile(r'(\w+)=([^\s]+)')


def _parse_kv_log_line(text: str, event: str) -> dict[str, str] | None:
    for line in text.splitlines():
        if f'event={event} ' in line or line.strip().endswith(f'event={event}'):
            return dict(_LATENCY_FIELD_RE.findall(line))
    return None


def test_latency_instrumentation_logs_all_required_stages(monkeypatch, caplog):
    qn.reset_base_wallet_target_cache()
    caplog.set_level(logging.INFO, logger='services.api.app.quicknode_streams')
    conn = _CountingFakeConnection(targets=[_make_target()])
    body = _make_realistic_base_block_payload(matched_index=None)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)):
        _call_live_webhook(body)

    fields = _parse_kv_log_line(caplog.text, 'quicknode_stream_latency')
    assert fields is not None, 'event=quicknode_stream_latency was not logged'
    required = {
        'send_to_receive_ms', 'verify_ms', 'gunzip_ms', 'json_parse_ms', 'extract_ms',
        'normalize_ms', 'target_load_ms', 'match_loop_ms', 'checkpoint_ms', 'total_ms',
    }
    missing = required - fields.keys()
    assert not missing, f'quicknode_stream_latency missing fields: {missing}'
    # total_ms must be a real positive number and at least as large as any one
    # measured sub-stage (sanity check against a broken/mis-wired timer).
    total_ms = float(fields['total_ms'])
    assert total_ms > 0
    for stage in ('json_parse_ms', 'extract_ms', 'normalize_ms'):
        assert float(fields[stage]) <= total_ms
    qn.reset_base_wallet_target_cache()


def test_match_latency_logged_for_persisted_transfer(monkeypatch, caplog):
    qn.reset_base_wallet_target_cache()
    caplog.set_level(logging.INFO, logger='services.api.app.quicknode_streams')
    conn = _CountingFakeConnection(targets=[_make_target()])
    body = _make_realistic_base_block_payload(tx_count=20, matched_index=5)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)):
        result = _call_live_webhook(body)
    assert result['persisted'] == 1

    fields = _parse_kv_log_line(caplog.text, 'quicknode_stream_match_latency')
    assert fields is not None, 'event=quicknode_stream_match_latency was not logged for a persisted match'
    for key in ('persist_ms', 'alert_chain_ms', 'webhook_entry_to_match_ms', 'match_at', 'tx_hash'):
        assert key in fields
    assert fields['tx_hash'] == MATCHED_TX_HASH
    assert float(fields['persist_ms']) >= 0
    assert float(fields['alert_chain_ms']) >= 0
    qn.reset_base_wallet_target_cache()


# ---------------------------------------------------------------------------
# The actual profiling run: real per-stage wall-clock measurements against the
# realistic synthetic payload, driven through the unmodified production code path.
# ---------------------------------------------------------------------------

def test_profile_realistic_block_stage_timings(monkeypatch, capsys):
    """Not a strict pass/fail perf gate (this sandbox's CPU is not Railway's) — it
    exists to (a) produce real, reproducible measured numbers for the ROOT CAUSE
    write-up instead of guessed ones, and (b) catch a gross future regression via
    the generous upper bound at the end."""
    qn.reset_base_wallet_target_cache()
    qn.reset_quicknode_log_sampler_state()
    # This IS the "deep debugging" case QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS=0
    # documents: a profiling run wants every iteration's latency line, not the
    # production once-per-window summary the sampler otherwise applies.
    monkeypatch.setenv('QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS', '0')
    logging.getLogger('services.api.app.quicknode_streams').setLevel(logging.INFO)
    conn = _CountingFakeConnection(targets=[_make_target()])
    body = _make_realistic_base_block_payload(matched_index=None)  # the common, no-match block

    samples: list[dict[str, float]] = []
    iterations = 25
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(logging.getLogger('services.api.app.quicknode_streams'), 'info') as mock_info:
        for _ in range(iterations):
            _call_live_webhook(body)
        for call in mock_info.call_args_list:
            args = call.args
            if not args or 'event=quicknode_stream_latency' not in str(args[0]):
                continue
            fmt, *values = args
            keys = re.findall(r'(\w+)=%', fmt)
            sample = {}
            for key, value in zip(keys, values):
                try:
                    sample[key] = float(value)
                except (TypeError, ValueError):
                    pass
            if sample:
                samples.append(sample)

    assert len(samples) == iterations, f'expected {iterations} quicknode_stream_latency samples, got {len(samples)}'

    def _pctl(values: list[float], p: float) -> float:
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))
        return ordered[idx]

    stage_names = [
        'verify_ms', 'gunzip_ms', 'json_parse_ms', 'extract_ms', 'normalize_ms',
        'target_load_ms', 'match_loop_ms', 'checkpoint_ms', 'total_ms',
    ]
    report_lines = [
        f'\n--- quicknode /base-live stage timing profile ({iterations} iterations, '
        f'{len(body) / 1024:.0f} KB / {json.loads(body)[0]["block"]["transactions"].__len__()} tx synthetic block, '
        f'no monitored-address match) ---',
        f'{"stage":<16}{"p50_ms":>10}{"p95_ms":>10}{"max_ms":>10}',
    ]
    p50: dict[str, float] = {}
    for stage in stage_names:
        values = [s.get(stage, 0.0) for s in samples]
        p50[stage] = _pctl(values, 0.50)
        report_lines.append(
            f'{stage:<16}{_pctl(values, 0.50):>10.3f}{_pctl(values, 0.95):>10.3f}{max(values):>10.3f}'
        )
    report = '\n'.join(report_lines)
    print(report)

    # Generous regression guard (this is a shared, unpredictably-loaded sandbox CPU,
    # not a dedicated perf rig): a 280-tx/~780KB no-match block should not take
    # anywhere near a second of in-process CPU time to decode+normalize+match
    # against a warm (fake, zero-latency) connection. This bound exists to catch a
    # gross accidental slowdown, not to certify production latency.
    assert p50['total_ms'] < 1000, (
        f'p50 total_ms={p50["total_ms"]:.1f}ms exceeds the 1000ms regression guard\n{report}'
    )
    qn.reset_base_wallet_target_cache()
