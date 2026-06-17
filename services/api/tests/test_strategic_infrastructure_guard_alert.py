"""
Tests for the Strategic Infrastructure Guard (SIG) alert rule.

Rule: Critical alert on outbound ETH transfer from a monitored Base chain wallet.

Coverage:
  1. Outbound transfer on Base → Critical alert created
  2. Same tx_hash twice → only one alert (dedup via detection UUID5)
  3. Inbound transfer (to_address is monitored wallet) → no SIG alert
  4. Non-Base chain (chain_id != 8453) → no SIG alert
  5. Simulator evidence → no alert
  6. Missing tx_hash → no alert
  7. Transfer value explicitly 0 → no alert
  8. Alert fields: severity=critical, module_key set, tx_hash/from/to in payload
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any

import pytest

from services.api.app import monitoring_runner

WORKSPACE_ID = str(uuid.uuid4())
TARGET_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
MONITORED_WALLET = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
OTHER_WALLET = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
TX_HASH = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
BLOCK_NUMBER = 47_300_000


def _outbound_payload(
    *,
    tx_hash: str = TX_HASH,
    from_addr: str = MONITORED_WALLET,
    to_addr: str = OTHER_WALLET,
    chain_id: int = 8453,
    value: str | None = '500000000000000000',
    block_number: int = BLOCK_NUMBER,
) -> dict[str, Any]:
    p: dict[str, Any] = {
        'tx_hash': tx_hash,
        'from': from_addr,
        'to': to_addr,
        'chain_id': chain_id,
        'block_number': block_number,
        'event_type': 'transaction',
    }
    if value is not None:
        p['value'] = value
    return p


# ---------------------------------------------------------------------------
# Minimal DB stub (same pattern as test_wallet_transfer_alert_escalation.py)
# ---------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows=None, rowcount=None):
        self._rows = list(rows or [])
        self.rowcount = rowcount if rowcount is not None else len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _StubConn:
    def __init__(self):
        self.inserts: list[tuple[str, tuple]] = []
        self.commit_calls = 0
        self._suppression_row = None
        self._existing_alert_row = None
        self._detection_conflict = False

    def execute(self, query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            if 'detections' in table and self._detection_conflict:
                return _Rows(rowcount=0)
            return _Rows(rowcount=1)
        if 'alert_suppression_rules' in q and 'select' in q:
            return _Rows([self._suppression_row] if self._suppression_row else [])
        if q.startswith('select') and 'from alerts' in q:
            return _Rows([self._existing_alert_row] if self._existing_alert_row else [])
        if q.startswith('update'):
            return _Rows(rowcount=1)
        return _Rows()

    def commit(self):
        self.commit_calls += 1

    @contextmanager
    def transaction(self):
        yield


def _fake_pg(stub: _StubConn):
    @contextmanager
    def _ctx():
        yield stub
    return _ctx


# ---------------------------------------------------------------------------
# 1. Outbound transfer on Base → Critical alert created
# ---------------------------------------------------------------------------

def test_sig_alert_created_for_live_outbound_base_transfer():
    stub = _StubConn()
    with monitoring_runner.pg_connection.__module__ and \
            pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        alert_id = monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=_outbound_payload(),
            evidence_source='live',
        )

    assert alert_id, 'SIG alert must return an alert_id for live outbound Base transfer'
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert alert_inserts, 'an alerts row must be inserted'
    assert stub.commit_calls >= 1


# ---------------------------------------------------------------------------
# 2. Dedup: same tx_hash twice → only one alert
# ---------------------------------------------------------------------------

def test_sig_alert_deduplicates_same_tx_hash():
    stub = _StubConn()
    stub._detection_conflict = True  # simulate detection already exists

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        result = monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=_outbound_payload(),
            evidence_source='live',
        )

    assert result is None, 'duplicate tx must not produce a second alert'
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert not alert_inserts, 'no alerts row must be inserted for duplicate detection'


# ---------------------------------------------------------------------------
# 3. Inbound transfer → no SIG alert
# ---------------------------------------------------------------------------

def test_sig_alert_not_created_for_inbound_transfer():
    stub = _StubConn()
    payload = _outbound_payload(
        from_addr=OTHER_WALLET,
        to_addr=MONITORED_WALLET,
    )
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        result = monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=payload,
            evidence_source='live',
        )

    assert result is None, 'inbound transfer must not fire SIG alert'
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert not alert_inserts


# ---------------------------------------------------------------------------
# 4. Non-Base chain → no SIG alert
# ---------------------------------------------------------------------------

def test_sig_alert_not_created_for_non_base_chain():
    stub = _StubConn()
    payload = _outbound_payload(chain_id=1)  # Ethereum mainnet
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        result = monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=payload,
            evidence_source='live',
        )

    assert result is None, 'non-Base chain must not fire SIG alert'
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert not alert_inserts


# ---------------------------------------------------------------------------
# 5. Simulator evidence → no alert
# ---------------------------------------------------------------------------

def test_sig_alert_not_created_for_simulator_evidence():
    stub = _StubConn()
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        result = monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=_outbound_payload(),
            evidence_source='simulator',
        )

    assert result is None
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert not alert_inserts


# ---------------------------------------------------------------------------
# 6. Missing tx_hash → no alert
# ---------------------------------------------------------------------------

def test_sig_alert_not_created_without_tx_hash():
    stub = _StubConn()
    payload = _outbound_payload()
    del payload['tx_hash']
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        result = monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=payload,
            evidence_source='live',
        )

    assert result is None, 'missing tx_hash must not fire SIG alert'


# ---------------------------------------------------------------------------
# 7. Transfer value explicitly 0 → no alert
# ---------------------------------------------------------------------------

def test_sig_alert_not_created_for_zero_value_transfer():
    stub = _StubConn()
    payload = _outbound_payload(value='0')
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        result = monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=payload,
            evidence_source='live',
        )

    assert result is None, 'zero-value transfer must not fire SIG alert'


# ---------------------------------------------------------------------------
# 8. Alert fields: severity=critical, module_key, tx_hash/from/to in payload
# ---------------------------------------------------------------------------

def test_sig_alert_has_correct_fields():
    captured: list[dict] = []
    stub = _StubConn()

    original_upsert = monitoring_runner._upsert_alert

    def _capturing_upsert(conn, *, response, module_key=None, **kwargs):
        captured.append({'response': dict(response), 'module_key': module_key})
        return original_upsert(conn, response=response, module_key=module_key, **kwargs)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(monitoring_runner, 'pg_connection', _fake_pg(stub))
        mp.setattr(monitoring_runner, '_upsert_alert', _capturing_upsert)
        monitoring_runner._strategic_infrastructure_guard_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Test Treasury Wallet',
            target_wallet_address=MONITORED_WALLET,
            payload=_outbound_payload(),
            evidence_source='live',
        )

    assert captured, '_upsert_alert must be called for live outbound transfer'
    r = captured[0]['response']
    assert r['severity'] == 'critical', f'severity must be critical, got {r["severity"]!r}'
    assert r['tx_hash'] == TX_HASH
    assert r['from_address'] == MONITORED_WALLET
    assert r['to_address'] == OTHER_WALLET
    assert r['chain_id'] == 8453
    assert r['source'] == 'rpc_polling'
    assert r['evidence_type'] == 'live_onchain_transaction'
    assert captured[0]['module_key'] == 'strategic_infrastructure_guard'


# ---------------------------------------------------------------------------
# 9. Deterministic dedup key: same inputs always produce same signature
# ---------------------------------------------------------------------------

def test_sig_alert_dedup_signature_is_deterministic():
    import uuid as _uuid
    import json as _json
    dedup_seed = _json.dumps(
        {
            'target_id': TARGET_ID,
            'tx_hash': TX_HASH,
            'rule': monitoring_runner._SIG_RULE_KEY,
            'chain_id': 8453,
        },
        sort_keys=True,
    )
    sig1 = _uuid.uuid5(_uuid.NAMESPACE_DNS, dedup_seed).hex
    sig2 = _uuid.uuid5(_uuid.NAMESPACE_DNS, dedup_seed).hex
    assert sig1 == sig2, 'dedup signature must be deterministic for same inputs'

    # SIG signature must differ from smoke signature for the same tx
    smoke_seed = _json.dumps(
        {'target_id': TARGET_ID, 'tx_hash': TX_HASH, 'rule': 'smoke_wallet_transfer'},
        sort_keys=True,
    )
    smoke_sig = _uuid.uuid5(_uuid.NAMESPACE_DNS, smoke_seed).hex
    assert sig1 != smoke_sig, 'SIG and smoke signatures must be distinct for the same tx'
