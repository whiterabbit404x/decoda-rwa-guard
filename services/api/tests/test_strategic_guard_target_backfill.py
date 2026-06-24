"""Tests for backfill_strategic_guard_alerts_for_target.

This is the scheduler-independent, worker-callable per-target Strategic Infrastructure
Guard backfill. It takes (workspace_id, target_id) directly — no Request/auth — so the
monitoring worker can run it after a successful rpc_polling heartbeat for a Base wallet
that is live-polled but never selected_for_backfill, whose older wallet_transfer_detected
rows would otherwise stay hidden behind the telemetry "Alerts only" filter.

Coverage:
  1. Live Base wallet-transfer rows with no alert -> smoke + SIG alerts created
  2. Create-only / no monitoring_runs leak: when the alert already exists (dedupe key
     pre-loaded) the alert rules are NOT invoked, only counted as deduped
  3. Different tx_hash -> different alert (no cross-tx dedupe), no LIMIT 1
  4. Simulator rows are scanned (visible) but never create alerts (truthfulness)
  5. Missing tx_hash and non-8453 chain rows are skipped with an explicit reason
  6. Invalid ids return a status dict instead of raising (worker-safe)
  7. The exact debug log taxonomy is emitted, including the a517 skip reason
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager


WORKSPACE_ID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
USER_ID = str(uuid.uuid4())
WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
TX_90C7 = '0x' + 'a' * 60 + '90c7'
TX_A517 = '0x' + 'b' * 60 + 'a517'


class _Result:
    def __init__(self, rows=None, rowcount=None):
        self._rows = list(rows or [])
        self.rowcount = rowcount if rowcount is not None else len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@contextmanager
def _fake_pg(connection):
    yield connection


class _FakeConn:
    """Returns telemetry rows for the scan and alert signatures for the preload query."""

    def __init__(self, telemetry_rows, alert_sig_rows=None):
        self._telemetry_rows = telemetry_rows
        self._alert_sig_rows = alert_sig_rows or []
        self.captured_queries: list[str] = []

    def execute(self, query, params=None):
        q = str(query or '')
        self.captured_queries.append(q)
        lowered = q.lower()
        if 'telemetry_events' in lowered:
            return _Result(rows=self._telemetry_rows)
        if 'from alerts' in lowered and 'dedupe_signature' in lowered:
            return _Result(rows=self._alert_sig_rows)
        return _Result(rows=[])

    def commit(self):
        pass


def _row(
    telemetry_id=None,
    tx_hash=TX_90C7,
    evidence_source='live',
    chain_id=8453,
    event_type='wallet_transfer_detected',
    observed_at='2026-06-16T00:00:00+00:00',
    wallet_address=WALLET_ADDR,
    drop_tx_hash=False,
):
    payload = {
        'from': wallet_address,
        'to': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        'value': '500000000000000000',
        'chain_id': chain_id,
        'block_number': 47_300_000,
    }
    if not drop_tx_hash:
        payload['tx_hash'] = tx_hash
    return {
        'id': telemetry_id or str(uuid.uuid4()),
        'target_id': TARGET_ID,
        'event_type': event_type,
        'observed_at': observed_at,
        'payload_json': payload,
        'evidence_source': evidence_source,
        'target_name': 'Base Treasury Wallet',
        'target_wallet_address': wallet_address,
        'owner_user_id': USER_ID,
        'monitored_system_id': None,
        'protected_asset_id': None,
    }


def _patch_common(monkeypatch, conn):
    from services.api.app import monitoring_runner
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    return monitoring_runner


def test_target_backfill_creates_smoke_and_sig_alerts(monkeypatch):
    conn = _FakeConn([_row()])
    m = _patch_common(monkeypatch, conn)

    smoke_calls: list[dict] = []
    sig_calls: list[dict] = []
    smoke_id, sig_id = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert', lambda **k: smoke_calls.append(k) or smoke_id)
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **k: sig_calls.append(k) or sig_id)

    result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['status'] == 'completed'
    assert result['workspace_id'] == WORKSPACE_ID
    assert result['target_id'] == TARGET_ID
    assert result['telemetry_processed'] == 1
    assert result['created_count'] == 2  # smoke + SIG
    assert result['deduped_count'] == 0
    assert result['linked_count'] == 1
    assert result['skipped_count'] == 0
    assert set(result['alert_ids']) == {smoke_id, sig_id}
    assert len(smoke_calls) == 1 and len(sig_calls) == 1
    # alerts are owned by the target's user (alerts.user_id is NOT NULL)
    assert smoke_calls[0]['user_id'] == USER_ID
    assert sig_calls[0]['target_wallet_address'] == WALLET_ADDR


def test_target_backfill_is_create_only_when_alert_exists(monkeypatch):
    """When the dedupe key is already present, the alert rules must NOT be invoked.

    This is the anti-leak guarantee: _wallet_transfer_smoke_alert inserts a monitoring_runs
    row on every call (even on dedupe), so re-running every poll cycle must skip the call
    entirely for rows whose alert already exists."""
    from services.api.app import monitoring_runner as m

    smoke_sig = m._smoke_dedupe_signature(
        workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash=TX_90C7)
    sig_sig = m._sig_dedupe_signature(
        workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash=TX_90C7)
    existing_smoke, existing_sig = str(uuid.uuid4()), str(uuid.uuid4())

    conn = _FakeConn(
        [_row(tx_hash=TX_90C7)],
        alert_sig_rows=[
            {'dedupe_signature': smoke_sig, 'id': existing_smoke},
            {'dedupe_signature': sig_sig, 'id': existing_sig},
        ],
    )
    _patch_common(monkeypatch, conn)

    def _boom(**_):  # must never be called when the alert already exists
        raise AssertionError('alert rule invoked for an already-alerted tx_hash (would leak a monitoring_run)')

    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert', _boom)
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', _boom)

    result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['created_count'] == 0
    assert result['deduped_count'] == 2
    assert result['linked_count'] == 1
    assert result['skipped_count'] == 0


def test_target_backfill_two_tx_hashes_two_alerts(monkeypatch):
    conn = _FakeConn([_row(tx_hash=TX_90C7), _row(tx_hash=TX_A517)])
    m = _patch_common(monkeypatch, conn)

    id_90c7, id_a517 = str(uuid.uuid4()), str(uuid.uuid4())

    def _smoke(**k):
        return id_90c7 if str(k['payload'].get('tx_hash')) == TX_90C7 else id_a517

    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert', _smoke)
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **_: None)

    result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['telemetry_processed'] == 2
    assert result['linked_count'] == 2
    assert result['created_count'] == 2
    assert set(result['alert_ids']) == {id_90c7, id_a517}


def test_target_backfill_skips_simulator_rows(monkeypatch):
    conn = _FakeConn([
        _row(tx_hash=TX_90C7, evidence_source='live'),
        _row(tx_hash=TX_A517, evidence_source='simulator'),
    ])
    m = _patch_common(monkeypatch, conn)

    smoke_calls: list[dict] = []
    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert', lambda **k: smoke_calls.append(k) or str(uuid.uuid4()))
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **_: None)

    result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['telemetry_processed'] == 2
    assert result['skipped_count'] == 1
    assert result['linked_count'] == 1
    assert len(smoke_calls) == 1
    assert smoke_calls[0]['evidence_source'] == 'live'


def test_target_backfill_skips_non_base_chain(monkeypatch):
    conn = _FakeConn([_row(tx_hash=TX_90C7, chain_id=1)])  # Ethereum mainnet, not Base
    m = _patch_common(monkeypatch, conn)

    called = {'smoke': 0}
    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert', lambda **_: called.__setitem__('smoke', called['smoke'] + 1) or str(uuid.uuid4()))
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **_: None)

    result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['skipped_count'] == 1
    assert result['linked_count'] == 0
    assert called['smoke'] == 0


def test_target_backfill_skips_missing_tx_hash(monkeypatch):
    conn = _FakeConn([_row(drop_tx_hash=True)])
    m = _patch_common(monkeypatch, conn)

    called = {'smoke': 0}
    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert', lambda **_: called.__setitem__('smoke', called['smoke'] + 1) or str(uuid.uuid4()))
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **_: None)

    result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['skipped_count'] == 1
    assert result['linked_count'] == 0
    assert called['smoke'] == 0


def test_target_backfill_invalid_ids_returns_status_without_raising(monkeypatch):
    from services.api.app import monitoring_runner as m
    monkeypatch.setattr(m, 'require_live_mode', lambda: None)

    result = m.backfill_strategic_guard_alerts_for_target('not-a-uuid', TARGET_ID)
    assert result['status'] == 'invalid_ids'
    assert result['linked_count'] == 0


def test_target_backfill_emits_required_log_taxonomy(monkeypatch, caplog):
    """The six required debug events are emitted with workspace/target/telemetry/tx_hash/
    dedupe_key/observed_at and the created/deduped/linked/skipped counts, and an a517 row
    skipped as simulator logs the exact skipped_reason."""
    conn = _FakeConn([
        _row(telemetry_id=str(uuid.uuid4()), tx_hash=TX_90C7, evidence_source='live',
             observed_at='2026-06-16T00:00:00+00:00'),
        _row(telemetry_id=str(uuid.uuid4()), tx_hash=TX_A517, evidence_source='simulator',
             observed_at='2026-06-13T00:00:00+00:00'),
    ])
    m = _patch_common(monkeypatch, conn)
    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert', lambda **_: str(uuid.uuid4()))
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **_: None)

    with caplog.at_level(logging.INFO, logger='services.api.app.monitoring_runner'):
        m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'strategic_guard_target_backfill_started' in text
    assert 'strategic_guard_backfill_row_seen' in text
    assert 'strategic_guard_alert_created' in text
    assert 'strategic_guard_telemetry_alert_linked' in text
    assert 'strategic_guard_target_backfill_completed' in text
    # the older simulator a517 row's age is visible and the skip reason is explicit
    assert 'observed_at=2026-06-13T00:00:00+00:00' in text
    assert 'skipped_reason=evidence_source_not_live' in text
    assert TX_A517 in text  # a517 row was seen, not silently dropped
    for fragment in ('created_count=', 'deduped_count=', 'linked_count=', 'skipped_count='):
        assert fragment in text


# ---------------------------------------------------------------------------
# Requirement E: backfill must NOT mint alerts from old telemetry during an RPC
# failure (provider 429 backoff) or for a wrong-chain target. The guard lives in
# the function so every call site is covered (worker cycle AND the telemetry
# "alerts only" read path which would otherwise create alerts on demand mid-backoff).
# ---------------------------------------------------------------------------

class _ChainConn(_FakeConn):
    """_FakeConn that also answers the chain_network lookup the guard performs."""

    def __init__(self, telemetry_rows, chain_network, alert_sig_rows=None):
        super().__init__(telemetry_rows, alert_sig_rows)
        self._chain_network = chain_network

    def execute(self, query, params=None):
        lowered = str(query or '').lower()
        if 'chain_network' in lowered and 'from targets' in lowered:
            self.captured_queries.append(str(query or ''))
            return _Result(rows=[{'chain_network': self._chain_network}])
        return super().execute(query, params)


def test_target_backfill_skipped_during_provider_backoff(monkeypatch, caplog):
    from services.api.app import evm_activity_provider as eap
    conn = _FakeConn([_row(tx_hash=TX_90C7, evidence_source='live')])
    m = _patch_common(monkeypatch, conn)
    called = {'smoke': 0}
    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert',
                        lambda **_: called.__setitem__('smoke', called['smoke'] + 1) or str(uuid.uuid4()))
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **_: None)

    eap.record_rpc_rate_limited(None)  # a prior cycle hit HTTP 429 — backoff is armed

    with caplog.at_level(logging.WARNING, logger='services.api.app.monitoring_runner'):
        result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['status'] == 'skipped_provider_backoff_active'
    assert result['created_count'] == 0 and result['linked_count'] == 0
    assert called['smoke'] == 0, 'no alert may be created from old telemetry during a 429 backoff'
    # The telemetry scan must not even run while the provider is rate-limited.
    assert not any('telemetry_events' in q.lower() for q in conn.captured_queries)
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'strategic_guard_backfill_skipped reason=provider_backoff_active' in text


def test_target_backfill_skipped_on_chain_mismatch(monkeypatch, caplog):
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')  # worker serves Base
    conn = _ChainConn([_row(tx_hash=TX_90C7, evidence_source='live')], chain_network='ethereum')
    m = _patch_common(monkeypatch, conn)
    called = {'smoke': 0}
    monkeypatch.setattr(m, '_wallet_transfer_smoke_alert',
                        lambda **_: called.__setitem__('smoke', called['smoke'] + 1) or str(uuid.uuid4()))
    monkeypatch.setattr(m, '_strategic_infrastructure_guard_alert', lambda **_: None)

    with caplog.at_level(logging.WARNING, logger='services.api.app.monitoring_runner'):
        result = m.backfill_strategic_guard_alerts_for_target(WORKSPACE_ID, TARGET_ID)

    assert result['status'] == 'skipped_chain_mismatch'
    assert called['smoke'] == 0, 'a wrong-chain target must never have its telemetry turned into alerts'
    assert not any('telemetry_events' in q.lower() for q in conn.captured_queries)
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'strategic_guard_backfill_skipped reason=chain_mismatch' in text
