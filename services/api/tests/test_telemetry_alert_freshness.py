"""Tests for telemetry-to-alert pipeline and freshness status fixes.

Coverage:
  A. backfill_missing_alerts_for_target creates a SIG alert from live wallet_transfer_detected telemetry
  B. Running backfill twice for the same target yields no duplicate alerts
  C. ingest_tx_by_hash fires smoke and SIG alert evaluators after persisting new telemetry
  D. stale_telemetry self-monitoring does not fire when workspace has fresh rpc_polling telemetry
  E. alerts_only telemetry filter returns wallet_transfer_detected rows linked via tx_hash
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any


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


WORKSPACE_ID = str(uuid.uuid4())
TARGET_ID    = str(uuid.uuid4())
USER_ID      = str(uuid.uuid4())
TELEMETRY_ID = str(uuid.uuid4())
TX_HASH      = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
WALLET_ADDR  = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'


def _make_telemetry_row(
    telemetry_id=TELEMETRY_ID,
    target_id=TARGET_ID,
    tx_hash=TX_HASH,
    wallet_address=WALLET_ADDR,
    evidence_source='live',
    chain_id=8453,
):
    return {
        'id': telemetry_id,
        'target_id': target_id,
        'payload_json': {
            'tx_hash': tx_hash,
            'from': wallet_address,
            'to': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            'value': '500000000000000000',
            'chain_id': chain_id,
            'block_number': 47_300_000,
        },
        'evidence_source': evidence_source,
        'target_name': 'Test Base Wallet',
        'target_wallet_address': wallet_address,
        'monitored_system_id': None,
        'protected_asset_id': None,
    }


# ── Test A: backfill creates SIG (Critical) alert ─────────────────────────────

def test_backfill_creates_sig_critical_alert(monkeypatch):
    """backfill_missing_alerts_for_target must invoke the SIG evaluator for a live
    outbound Base (chain_id=8453) wallet_transfer_detected row."""
    from services.api.app import monitoring_runner

    sig_calls: list[dict] = []

    def _fake_sig(**kwargs):
        sig_calls.append(kwargs)
        return str(uuid.uuid4())

    conn = type('C', (), {
        'execute': lambda self, q, p=None: _Result(rows=[_make_telemetry_row()]),
        'commit': lambda self: None,
    })()
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: str(uuid.uuid4()))
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', _fake_sig)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    assert result['status'] == 'completed'
    assert result['alerts_created'] >= 1
    assert len(sig_calls) == 1
    assert sig_calls[0]['target_wallet_address'] == WALLET_ADDR
    assert sig_calls[0]['evidence_source'] == 'live'


# ── Test B: backfill dedupe — no duplicate alerts on second run ──────────────

def test_backfill_dedupe_returns_same_ids_on_second_call(monkeypatch):
    """Running backfill twice for the same target must return identical alert ID sets
    without creating new alert records."""
    from services.api.app import monitoring_runner

    smoke_id = str(uuid.uuid4())
    sig_id   = str(uuid.uuid4())

    conn = type('C', (), {
        'execute': lambda self, q, p=None: _Result(rows=[_make_telemetry_row()]),
        'commit': lambda self: None,
    })()
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    # Both evaluators return the same IDs each time — UUID5 deterministic dedup
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: smoke_id)
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: sig_id)

    req = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    r1 = monitoring_runner.backfill_missing_alerts_for_target(req, target_id=TARGET_ID)
    r2 = monitoring_runner.backfill_missing_alerts_for_target(req, target_id=TARGET_ID)

    assert set(r1['alert_ids']) == {smoke_id, sig_id}
    assert set(r1['alert_ids']) == set(r2['alert_ids']), 'second call must not produce new IDs'


# ── Test C: ingest_tx_by_hash fires alert evaluators after insert ─────────────

def test_ingest_tx_by_hash_fires_alert_evaluators_after_insert(monkeypatch):
    """ingest_tx_by_hash must call smoke and SIG evaluators immediately after
    persisting a new wallet_transfer_detected telemetry row."""
    import services.api.app.evm_activity_provider as _evm
    from services.api.app import monitoring_runner

    smoke_calls: list[dict] = []
    sig_calls:   list[dict] = []

    TX  = '0x' + 'cd' * 32
    WS  = str(uuid.uuid4())
    TGT = str(uuid.uuid4())
    WAL = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

    target_data = {
        'id': TGT, 'workspace_id': WS, 'target_type': 'wallet',
        'wallet_address': WAL, 'chain_network': 'base',
        'name': 'Test Target', 'asset_id': None,
        'created_by_user_id': USER_ID, 'updated_by_user_id': None,
        'monitored_system_id': None, 'contract_identifier': None,
    }

    fake_tx = {
        'from': WAL,
        'to': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        'value': hex(10 ** 18),
        'chainId': hex(8453),
        'blockNumber': hex(47_300_000),
        'blockHash': '0x' + 'ab' * 32,
        'hash': TX,
    }

    _chain_rpc_info = {
        'rpc_url': 'http://fake-rpc',
        'rpc_urls': ['http://fake-rpc'],
        'expected_chain_id': 8453,
    }

    class _FakeRpcClient:
        def call(self, method, params=None):
            if method == 'eth_chainId':
                return hex(8453)
            if method == 'eth_getTransactionByHash':
                return fake_tx
            if method == 'eth_getTransactionReceipt':
                return {'status': '0x1', 'gasUsed': '0x5208'}
            if method in ('eth_getBlockByHash', 'eth_getBlockByNumber'):
                return {'timestamp': hex(1_700_000_000)}
            return {}

    class _FakeDbConn:
        @contextmanager
        def transaction(self):
            yield self

        def execute(self, query, params=None):
            q = (query or '').strip().lower()
            if 'from targets' in q:
                return _Result(rows=[target_data])
            if 'insert into telemetry_events' in q:
                return _Result(rows=[], rowcount=1)
            return _Result(rows=[])

        def commit(self):
            pass

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_FakeDbConn()))
    monkeypatch.setattr(monitoring_runner, 'normalize_workspace_header_value', lambda v: WS)
    monkeypatch.setattr(_evm, 'resolve_monitored_wallet', lambda t: WAL)
    monkeypatch.setattr(_evm, 'resolve_chain_rpc', lambda *a, **kw: _chain_rpc_info)
    monkeypatch.setattr(_evm, 'FailoverJsonRpcClient', lambda *a, **kw: _FakeRpcClient())
    monkeypatch.setattr(
        monitoring_runner, '_wallet_transfer_smoke_alert',
        lambda **kw: (smoke_calls.append(kw), str(uuid.uuid4()))[1],
    )
    monkeypatch.setattr(
        monitoring_runner, '_strategic_infrastructure_guard_alert',
        lambda **kw: (sig_calls.append(kw), str(uuid.uuid4()))[1],
    )

    request = SimpleNamespace(headers={'x-workspace-id': WS})
    result  = monitoring_runner.ingest_tx_by_hash(request, target_id=TGT, tx_hash=TX)

    assert result.get('imported') is True, f'expected imported=True, got: {result}'
    assert len(smoke_calls) == 1, 'smoke alert evaluator must be called once'
    assert len(sig_calls)   == 1, 'SIG alert evaluator must be called once'
    assert smoke_calls[0]['evidence_source'] == 'live'
    assert sig_calls[0]['target_wallet_address'] == WAL


# ── Test D: stale_telemetry not fired when rpc_polling is fresh ───────────────

def test_stale_telemetry_query_excludes_fresh_rpc_polling_workspaces(monkeypatch):
    """evaluate_monitoring_system_alerts must not fire stale_telemetry for workspaces
    that have a fresh rpc_polling event.  The SQL HAVING clause must contain a
    FILTER aggregate or equivalent guard on rpc_polling/evidence_source=live."""
    from services.api.app import pilot

    captured: list[tuple[str, Any]] = []

    class _CaptureConn:
        def execute(self, query, params=None):
            captured.append((query or '', params))
            return _Result(rows=[])

        def commit(self):
            pass

    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(_CaptureConn()))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'send_external_oncall_alert', lambda *a, **kw: False)

    pilot.evaluate_monitoring_system_alerts(stale_after_seconds=120)

    stale_qs = [
        q for q, _ in captured
        if 'group by workspace_id' in q.lower() and 'telemetry_events' in q.lower()
    ]
    assert stale_qs, 'stale_telemetry query was not executed'
    q_lower = stale_qs[0].lower()
    assert 'rpc_polling' in q_lower, (
        "stale_telemetry query must reference rpc_polling to exclude fresh-coverage workspaces"
    )
    assert 'filter' in q_lower, (
        "stale_telemetry query must use FILTER aggregate to check rpc_polling freshness"
    )


# ── Test E: alerts_only filter matches rows via tx_hash ──────────────────────

def test_alerts_only_filter_sql_matches_by_tx_hash(monkeypatch):
    """list_target_telemetry must build an alerts_only SQL filter that matches
    telemetry rows linked to alerts via tx_hash, not only via telemetry_id."""
    from services.api.app import monitoring_runner

    captured: list[tuple[str, list]] = []

    class _CaptureConn:
        def execute(self, query, params=None):
            captured.append((query or '', list(params or [])))
            return _Result(rows=[])

        def commit(self):
            pass

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CaptureConn()))
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(
        monitoring_runner,
        'authenticate_with_connection',
        lambda *_: {'id': USER_ID},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace',
        lambda *_: {'workspace_id': WORKSPACE_ID},
    )

    request = SimpleNamespace(
        headers={'x-workspace-id': WORKSPACE_ID},
        query_params={'event_type_filter': 'alerts_only'},
    )
    try:
        monitoring_runner.list_target_telemetry(
            request,
            target_id=TARGET_ID,
            event_type_filter='alerts_only',
            limit=10,
            offset=0,
            q=None,
        )
    except Exception:
        pass

    telemetry_queries = [
        q for q, _ in captured
        if 'telemetry_events' in q.lower() and 'alerts' in q.lower()
    ]
    assert telemetry_queries, 'no telemetry+alerts query was issued'
    q_lower = telemetry_queries[0].lower()
    assert "payload->>'tx_hash'" in q_lower or "payload->>'tx_hash'" in telemetry_queries[0], (
        "alerts_only filter must match by tx_hash, not only by telemetry_id"
    )
    assert 'wallet_transfer_detected' in q_lower, (
        "alerts_only tx_hash filter must be scoped to wallet_transfer_detected events"
    )
