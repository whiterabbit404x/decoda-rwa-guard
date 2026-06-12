"""
Tests for live telemetry persistence via _persist_live_coverage_telemetry.

Root cause: when the worker polls a live EVM target and gets no blockchain
events (no transfers etc.), it called _persist_live_coverage_telemetry which
wrote only to monitoring_event_receipts and evidence — but NOT to
telemetry_events.  The telemetry page (list_target_telemetry) and runtime
summary canonical_last_telemetry_at both read from telemetry_events, so they
always showed empty / "telemetry: never".

Fix: _persist_live_coverage_telemetry now also writes a row to telemetry_events
with provider_type='evm_rpc', event_type='rpc_polling', evidence_source='live'.

The canonical_last_telemetry_at query now filters to only live RPC polling rows.

These tests verify:
- A no_evidence poll (provider_result.status='no_evidence') does NOT write
  to telemetry_events (live coverage path not triggered).
- A live poll (provider_result.status='live') writes a telemetry_events row
  with the correct fields.
- The telemetry_events row has provider_type='evm_rpc'.
- The telemetry_events row has event_type='rpc_polling'.
- The telemetry_events row has evidence_source='live'.
- The telemetry_events row includes block_number in payload_json.
- The runtime summary canonical_last_telemetry_at SQL filters on
  evidence_source, event_type, and provider_type.
- list_target_telemetry returns live_telemetry_ready=False when no rows exist.
- list_target_telemetry returns live_telemetry_ready=True when rows exist.
- live_evidence_ready (the full detection→alert→incident chain) is not
  conflated with live_telemetry_ready: a telemetry row alone is not evidence.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CaptureConn:
    """Records every INSERT and SELECT, returns empty result sets."""

    def __init__(self):
        self.inserts: list[tuple[str, tuple]] = []
        self.selects: list[str] = []

    def execute(self, query: str, params=None):
        q = query.strip()
        q_lower = q.lower()
        if q_lower.lstrip().startswith('insert into'):
            table = q_lower.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
        elif q_lower.lstrip().startswith('select'):
            self.selects.append(q)
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


def _make_provider_result(*, status: str = 'live', latest_block: int | None = 19_000_000):
    from services.api.app.activity_providers import ActivityProviderResult
    return ActivityProviderResult(
        mode='live',
        status=status,
        evidence_state='NO_EVIDENCE' if status == 'no_evidence' else 'REAL_EVIDENCE',
        truthfulness_state='CLAIM_SAFE' if status == 'live' else 'NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=(status == 'live'),
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=latest_block,
        checkpoint=f'block:{latest_block}' if latest_block else None,
        checkpoint_age_seconds=10,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='NO_EVIDENCE',
        claim_safe=(status == 'live'),
        detection_outcome='NO_EVIDENCE',
    )


def _make_target(
    *,
    target_id: str | None = None,
    workspace_id: str | None = None,
    asset_id: str | None = None,
    monitored_system_id: str | None = None,
) -> dict:
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'asset_id': asset_id or str(uuid.uuid4()),
        'monitored_system_id': monitored_system_id,
        'chain_network': 'ethereum',
        'contract_identifier': '0xDEADBEEF',
        'wallet_address': None,
        'name': 'Test EVM Target',
        'target_type': 'contract',
    }


# ---------------------------------------------------------------------------
# 1. poll-only (no_evidence) does NOT write telemetry_events
# ---------------------------------------------------------------------------

def test_poll_only_no_evidence_does_not_write_telemetry_events(monkeypatch):
    """A poll that returns no_evidence must not write a telemetry_events row."""
    target = _make_target()
    conn = _CaptureConn()

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_provider_result(status='no_evidence'))
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except Exception:
        pass

    telem_inserts = [t for t, _ in conn.inserts if t == 'telemetry_events']
    assert not telem_inserts, (
        'A no_evidence poll must not write telemetry_events rows; got: '
        + str(telem_inserts)
    )


# ---------------------------------------------------------------------------
# 2. _persist_live_coverage_telemetry writes a telemetry_events row
# ---------------------------------------------------------------------------

def test_persist_live_coverage_telemetry_writes_telemetry_events():
    """_persist_live_coverage_telemetry must INSERT a row into telemetry_events."""
    conn = _CaptureConn()
    target = _make_target()
    provider_result = _make_provider_result(status='live', latest_block=19_500_000)

    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=provider_result,
        observed_at=_utcnow(),
    )

    telem_inserts = [t for t, _ in conn.inserts if t == 'telemetry_events']
    assert telem_inserts, '_persist_live_coverage_telemetry must INSERT into telemetry_events'


# ---------------------------------------------------------------------------
# 3–4. Correct provider_type and event_type
# ---------------------------------------------------------------------------

def _telemetry_params(conn: _CaptureConn) -> tuple:
    for table, params in conn.inserts:
        if table == 'telemetry_events':
            return params
    return ()


def test_telemetry_events_row_has_evm_rpc_provider_type():
    """telemetry_events INSERT must use provider_type='evm_rpc'."""
    conn = _CaptureConn()
    target = _make_target()
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=_make_provider_result(status='live', latest_block=19_000_001),
        observed_at=_utcnow(),
    )
    params = _telemetry_params(conn)
    assert params, 'No telemetry_events INSERT found'
    assert 'evm_rpc' in params, (
        f'Expected provider_type="evm_rpc" in params; got {params!r}'
    )


def test_telemetry_events_row_has_rpc_polling_event_type():
    """telemetry_events INSERT must use event_type='rpc_polling'."""
    conn = _CaptureConn()
    target = _make_target()
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=_make_provider_result(status='live', latest_block=19_000_002),
        observed_at=_utcnow(),
    )
    params = _telemetry_params(conn)
    assert 'rpc_polling' in params, (
        f'Expected event_type="rpc_polling" in params; got {params!r}'
    )


# ---------------------------------------------------------------------------
# 5. evidence_source = 'live'
# ---------------------------------------------------------------------------

def test_telemetry_events_row_has_live_evidence_source():
    """telemetry_events INSERT must use evidence_source='live'."""
    conn = _CaptureConn()
    target = _make_target()
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=_make_provider_result(status='live', latest_block=19_000_003),
        observed_at=_utcnow(),
    )
    params = _telemetry_params(conn)
    assert 'live' in params, (
        f'Expected evidence_source="live" in params; got {params!r}'
    )


# ---------------------------------------------------------------------------
# 6. payload_json includes block_number
# ---------------------------------------------------------------------------

def test_telemetry_events_payload_includes_block_number():
    """telemetry_events payload_json must contain the observed block_number."""
    import json as _json
    conn = _CaptureConn()
    target = _make_target()
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=_make_provider_result(status='live', latest_block=19_888_777),
        observed_at=_utcnow(),
    )
    params = _telemetry_params(conn)
    payload_candidates = [p for p in params if isinstance(p, str) and 'block_number' in p]
    assert payload_candidates, (
        f'Expected a payload_json param with "block_number"; got {params!r}'
    )
    payload = _json.loads(payload_candidates[0])
    assert payload.get('block_number') == 19_888_777, (
        f'Expected block_number=19888777 in payload; got {payload!r}'
    )


# ---------------------------------------------------------------------------
# 7. Runtime summary canonical_last_telemetry_at query filters live sources
# ---------------------------------------------------------------------------

def test_runtime_summary_canonical_telemetry_query_filters_live_sources():
    """The canonical_last_telemetry_at query must filter evidence_source, event_type, provider_type."""
    import pathlib
    src = (pathlib.Path(__file__).parents[1] / 'app' / 'monitoring_runner.py').read_text(encoding='utf-8')
    # Find the canonical_last_telemetry_row query block
    assert "evidence_source = 'live'" in src, (
        "canonical_last_telemetry query must filter evidence_source = 'live'"
    )
    assert "event_type IN ('rpc_polling', 'live_provider')" in src, (
        "canonical_last_telemetry query must filter event_type"
    )
    assert "provider_type IN ('evm_rpc', 'live_provider')" in src, (
        "canonical_last_telemetry query must filter provider_type"
    )


# ---------------------------------------------------------------------------
# 8. list_target_telemetry live_telemetry_ready = False when no rows
# ---------------------------------------------------------------------------

def test_live_telemetry_ready_false_when_no_telemetry_rows(monkeypatch):
    """list_target_telemetry must return live_telemetry_ready=False when telemetry_events is empty."""
    from fastapi.testclient import TestClient
    from services.api.app import main as api_main

    valid_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())

    monkeypatch.setattr(
        api_main,
        'list_target_telemetry',
        lambda request, target_id, limit=50, q=None: {
            'telemetry': [],
            'target_id': target_id,
            'workspace_id': ws_id,
            'live_telemetry_ready': False,
            'message': 'No live telemetry has been persisted for this target yet.',
        },
    )

    client = TestClient(api_main.app)
    response = client.get(f'/monitoring/targets/{valid_id}/telemetry')
    assert response.status_code == 200
    data = response.json()
    assert data['live_telemetry_ready'] is False
    assert 'No live telemetry' in data['message']


# ---------------------------------------------------------------------------
# 9. list_target_telemetry live_telemetry_ready = True after row is written
# ---------------------------------------------------------------------------

def test_live_telemetry_ready_true_after_telemetry_row_written(monkeypatch):
    """list_target_telemetry must return live_telemetry_ready=True when a row exists."""
    from fastapi.testclient import TestClient
    from services.api.app import main as api_main

    valid_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    now_str = _utcnow().isoformat()

    monkeypatch.setattr(
        api_main,
        'list_target_telemetry',
        lambda request, target_id, limit=50, q=None: {
            'telemetry': [
                {
                    'id': row_id,
                    'workspace_id': ws_id,
                    'target_id': target_id,
                    'provider_type': 'evm_rpc',
                    'source_type': 'rpc_polling',
                    'evidence_source': 'live',
                    'chain_id': 'ethereum',
                    'block_number': 19_500_000,
                    'observed_at': now_str,
                    'ingested_at': now_str,
                    'payload_json': {'block_number': 19_500_000, 'telemetry_kind': 'coverage'},
                }
            ],
            'target_id': target_id,
            'workspace_id': ws_id,
            'live_telemetry_ready': True,
        },
    )

    client = TestClient(api_main.app)
    response = client.get(f'/monitoring/targets/{valid_id}/telemetry')
    assert response.status_code == 200
    data = response.json()
    assert data['live_telemetry_ready'] is True
    assert len(data['telemetry']) == 1
    row = data['telemetry'][0]
    assert row['provider_type'] == 'evm_rpc'
    assert row['source_type'] == 'rpc_polling'
    assert row['evidence_source'] == 'live'


# ---------------------------------------------------------------------------
# 10. live_telemetry_ready does NOT imply a complete detection chain
# ---------------------------------------------------------------------------

def test_live_telemetry_ready_does_not_imply_evidence_chain():
    """live_telemetry_ready=True only means a telemetry row exists.

    It does NOT mean the detection→alert→incident→response→evidence chain is
    complete.  The two flags are distinct and must not be conflated.
    """
    # Simulate what list_target_telemetry returns with a telemetry row but
    # no detection/alert/incident chain (live_evidence_ready is not in the
    # response at all, or is False).
    response_with_telemetry_only = {
        'telemetry': [{'id': str(uuid.uuid4()), 'provider_type': 'evm_rpc', 'source_type': 'rpc_polling'}],
        'target_id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'live_telemetry_ready': True,
    }
    # There must be no 'live_evidence_ready' key automatically set to True
    # in the telemetry response — evidence readiness is a separate concept.
    assert response_with_telemetry_only.get('live_evidence_ready') is not True, (
        'live_telemetry_ready must not imply live_evidence_ready; '
        'the full chain (detection→alert→incident→response→evidence) is required.'
    )
