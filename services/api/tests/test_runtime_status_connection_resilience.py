"""Tests for runtime-status connection resilience and telemetry presence.

Covers:
- Retry-once behavior when the canonical-queries block hits psycopg "connection is closed"
- Correct summary fields when live telemetry and coverage records exist
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from services.api.app import monitoring_runner


# ── shared helpers ────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row or {}
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FullConn:
    """Mock connection that returns plausible data for all runtime-status queries."""

    def __init__(self, telemetry_at: datetime | None):
        self.telemetry_at = telemetry_at

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        now_iso = datetime.now(timezone.utc).isoformat()
        tel_iso = self.telemetry_at.isoformat() if self.telemetry_at else None

        # ── monitored_systems rows ────────────────────────────────────────────
        if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
            return _Result(rows=[
                {
                    'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1',
                    'target_id': 'tgt-1', 'is_enabled': True, 'runtime_status': 'active',
                    'last_heartbeat': now_iso, 'last_event_at': tel_iso,
                    'last_coverage_telemetry_at': tel_iso,
                    'monitoring_interval_seconds': 30, 'created_at': now_iso,
                },
            ])

        # ── targets / assets counts ───────────────────────────────────────────
        if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
            return _Result({'c': 0})
        if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
            return _Result({'target_count': 1, 'asset_count': 1})
        if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
            return _Result(rows=[{'id': 'tgt-1'}])

        # ── alerts / incidents ────────────────────────────────────────────────
        if 'FROM alerts' in q:
            return _Result({'c': 0})
        if 'FROM incidents' in q:
            return _Result({'c': 0})

        # ── canonical last telemetry (live evm_rpc rows) ──────────────────────
        if 'FROM telemetry_events' in q and "evidence_source = 'live'" in q and 'MAX(observed_at)' in q:
            return _Result({'ts': tel_iso})

        # ── canonical reporting: distinct targets from recent telemetry ───────
        if 'SELECT DISTINCT te.target_id' in q and 'FROM telemetry_events te' in q:
            if tel_iso:
                return _Result(rows=[{'target_id': 'tgt-1'}])
            return _Result(rows=[])

        # ── target_coverage_records ───────────────────────────────────────────
        if 'FROM target_coverage_records' in q and 'DISTINCT ON (target_id)' in q:
            if tel_iso:
                return _Result(rows=[
                    {
                        'target_id': 'tgt-1', 'coverage_status': 'reporting',
                        'last_telemetry_at': tel_iso, 'evidence_source': 'live',
                        'computed_at': tel_iso, 'metadata': {},
                    }
                ])
            return _Result(rows=[])

        # ── canonical reporting via coverage CTE ──────────────────────────────
        if 'WITH latest_coverage AS' in q:
            return _Result(rows=[])

        # ── polls / heartbeats ────────────────────────────────────────────────
        if 'FROM monitoring_polls' in q and 'MAX(' in q:
            return _Result({'ts': tel_iso})
        if 'FROM monitoring_heartbeats' in q and 'MAX(' in q:
            return _Result({'ts': now_iso})
        if 'FROM detection_events' in q and 'MAX(' in q:
            return _Result({'ts': None})

        # ── coverage receipts ─────────────────────────────────────────────────
        if 'FROM monitoring_event_receipts' in q:
            return _Result(rows=[])

        # ── analysis runs / evidence ──────────────────────────────────────────
        if 'FROM analysis_runs' in q:
            return _Result(None)
        if 'FROM evidence' in q:
            return _Result({'observed_at': tel_iso, 'block_number': 123} if tel_iso else {})

        # ── provider_health_records ───────────────────────────────────────────
        if 'FROM provider_health_records' in q:
            return _Result(rows=[])

        return _Result({})


@contextmanager
def _fake_pg(conn):
    yield conn


def _enable_live_mode(monkeypatch):
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {
            'checks': {'evm_rpc_reachable': True},
            'sales_claims_allowed': False,
            'status': 'FAIL',
            'recent_truthfulness_state': 'unknown_risk',
        },
    )


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    _enable_live_mode(monkeypatch)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()


# ── retry-once tests ──────────────────────────────────────────────────────────

def test_retry_once_when_canonical_block_raises_connection_closed(monkeypatch):
    """The second with-pg_connection block (canonical queries) raises
    psycopg.OperationalError('connection is closed').  The endpoint must retry
    _monitoring_runtime_status_impl once with a fresh connection and return
    a valid (non-degraded) response rather than summary_unavailable.
    """
    now = datetime.now(timezone.utc)
    pg_call_count = [0]

    class _ClosedConn:
        def execute(self, query, params=None):
            raise psycopg.OperationalError('connection is closed')

    @contextmanager
    def _counting_pg():
        pg_call_count[0] += 1
        call_num = pg_call_count[0]
        # pg_connection() calls per impl run:
        #   1 - main queries block
        #   2 - configs block (new, added in connection-retry fix)
        #   3 - post-aggregation canonical block ← inject closed connection here
        # The error at call 3 propagates up and triggers retry of the whole impl.
        # Calls 4,5,6 are the second run (all succeed).
        if call_num == 3:
            yield _ClosedConn()
        else:
            yield _FullConn(now - timedelta(seconds=30))

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', _counting_pg)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()

    # Retry must have happened: at least 6 total pg_connection calls
    # (3 per impl run × 2 runs: 1 main + 1 configs + 1 post-aggregation)
    assert pg_call_count[0] >= 6, (
        f"Expected ≥6 pg_connection calls (3 per impl run × 2 runs), got {pg_call_count[0]}"
    )
    # The retry returned a valid payload, not the degraded offline fallback
    assert payload.get('status') != 'Offline'
    assert payload.get('summary_unavailable') is not True


def test_retry_returns_degraded_when_retry_also_fails(monkeypatch):
    """If both the first attempt and the retry raise 'connection is closed',
    the endpoint returns the honest degraded payload with reason token
    'database_error.connection_closed'."""
    now = datetime.now(timezone.utc)

    class _AlwaysClosedConn:
        def execute(self, query, params=None):
            raise psycopg.OperationalError('connection is closed')

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_AlwaysClosedConn()))
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()

    # After both attempts fail, the endpoint must return the degraded payload
    # with an honest connection_closed classification rather than a 200-OK live response.
    assert payload.get('db_failure_classification') == 'connection_closed', (
        f"Expected db_failure_classification='connection_closed', "
        f"got {payload.get('db_failure_classification')!r}"
    )
    # Must not claim live monitoring is healthy
    assert payload.get('status') != 'Active'


# ── telemetry-present tests ───────────────────────────────────────────────────

def test_runtime_status_not_offline_when_live_telemetry_persisted(monkeypatch):
    """When telemetry_event_persisted and target_coverage_records exist,
    runtime-status must NOT return offline and must reflect live evidence."""
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'pg_connection',
        lambda: _fake_pg(_FullConn(now - timedelta(seconds=60))),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'rpc_polling',
            'worker_running': True,
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload.get('workspace_monitoring_summary') or {}

    assert payload.get('status') != 'Offline', (
        f"Expected non-Offline status when live telemetry exists, got {payload.get('status')!r}"
    )
    assert int(summary.get('configured_systems') or 0) > 0, (
        f"Expected configured_systems > 0, got summary={summary}"
    )
    assert int(summary.get('reporting_systems') or 0) > 0, (
        f"Expected reporting_systems > 0 when telemetry exists, got {summary.get('reporting_systems')!r}"
    )
    assert int(summary.get('protected_assets') or payload.get('protected_assets') or 0) > 0, (
        f"Expected protected_assets > 0"
    )
    assert summary.get('evidence_source') != 'none', (
        f"Expected evidence_source != 'none', got {summary.get('evidence_source')!r}"
    )
    assert summary.get('freshness_status') != 'unavailable', (
        f"Expected freshness_status != 'unavailable', got {summary.get('freshness_status')!r}"
    )
    assert summary.get('runtime_status') != 'offline', (
        f"Expected runtime_status != 'offline', got {summary.get('runtime_status')!r}"
    )


def test_runtime_status_workspace_configured_true_with_targets(monkeypatch):
    """workspace_configured must be true when monitored systems exist."""
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'pg_connection',
        lambda: _fake_pg(_FullConn(now - timedelta(seconds=60))),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'rpc_polling',
            'worker_running': True,
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload.get('workspace_monitoring_summary') or {}

    assert summary.get('workspace_configured') is True, (
        f"Expected workspace_configured=True, got {summary.get('workspace_configured')!r}"
    )
    assert int(summary.get('configured_systems') or 0) > 0
