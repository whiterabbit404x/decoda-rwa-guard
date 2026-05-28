"""Tests for monitoring_runner connection-retry and coverage-evidence fixes.

Covers:
- closed connection at post-aggregation stage retries once and succeeds
- retry failure returns degraded payload with connection_closed classification
- live telemetry coverage (coverage_heartbeat_updates > 0) clears no-evidence streak
- coverage_only_persistent_no_evidence fires when nothing is persisted
- runtime-status does not return OFFLINE when fresh live telemetry evidence exists
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import psycopg
import pytest

from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row or {}
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FullConn:
    """Full-featured mock connection that satisfies all queries in _monitoring_runtime_status_impl."""

    def __init__(self, *, telemetry_at: datetime | None = None, evidence_at: datetime | None = None):
        self.telemetry_at = telemetry_at
        self.evidence_at = evidence_at
        self.cursor_mock = MagicMock()
        self.cursor_mock.__enter__ = lambda s: s
        self.cursor_mock.__exit__ = MagicMock(return_value=False)
        self.cursor_mock.execute = MagicMock()
        self.cursor_mock.fetchone = MagicMock(return_value={'count': 1})

    def cursor(self):
        return self.cursor_mock

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
            return _Result(rows=[
                {
                    'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1',
                    'target_id': 'target-1', 'chain': 'ethereum', 'is_enabled': True,
                    'runtime_status': 'active', 'status': 'active',
                    'last_heartbeat': now_iso, 'last_event_at': now_iso,
                    'last_coverage_telemetry_at': now_iso,
                    'freshness_status': 'fresh', 'confidence_status': 'high',
                    'coverage_reason': None, 'last_error_text': None,
                    'monitoring_interval_seconds': 30, 'created_at': now_iso,
                    'target_type': 'evm_address',
                },
            ])
        if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
            return _Result({'target_count': 1, 'asset_count': 1})
        if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
            return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1', 'target_type': 'evm_address'}])
        if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
            return _Result({'c': 0})
        if 'FROM evidence' in q and 'ORDER BY observed_at DESC LIMIT 1' in q:
            row = {'observed_at': self.evidence_at or self.telemetry_at, 'block_number': 123} if (self.evidence_at or self.telemetry_at) else {}
            return _Result(row)
        if 'FROM analysis_runs' in q:
            return _Result(None)
        if 'FROM alerts' in q and 'COUNT(*)' in q:
            return _Result({'c': 0})
        if 'FROM incidents' in q and 'COUNT(*)' in q:
            return _Result({'c': 0})
        if 'FROM detections' in q and 'COUNT(*)' in q:
            return _Result({'c': 0})
        if 'FROM response_actions' in q and 'COUNT(*)' in q:
            return _Result({'c': 0})
        if 'FROM assets' in q and 'COUNT(*)' in q:
            return _Result({'c': 1})
        if 'FROM targets t' in q and 'COUNT(*)' in q:
            return _Result({'c': 0})
        if 'FROM monitoring_polls' in q:
            return _Result({'ts': now_iso})
        if 'FROM monitoring_heartbeats' in q:
            return _Result({'ts': now_iso})
        if 'FROM telemetry_events' in q and 'MAX(observed_at)' in q:
            ts = self.telemetry_at.isoformat() if self.telemetry_at else None
            return _Result({'ts': ts})
        if 'FROM detection_events' in q and 'MAX(created_at)' in q:
            return _Result({'ts': None})
        if 'FROM target_coverage_records' in q and 'DISTINCT ON (target_id)' in q:
            if self.telemetry_at:
                return _Result(rows=[{
                    'target_id': 'target-1',
                    'coverage_status': 'reporting',
                    'last_telemetry_at': self.telemetry_at.isoformat(),
                    'evidence_source': 'live',
                    'computed_at': self.telemetry_at.isoformat(),
                    'metadata': '{}',
                }])
            return _Result(rows=[])
        if 'FROM telemetry_events te' in q and 'DISTINCT te.target_id' in q:
            if self.telemetry_at:
                return _Result(rows=[{'target_id': 'target-1'}])
            return _Result(rows=[])
        if 'FROM target_coverage_records tcr' in q and 'latest_coverage' in q:
            return _Result(rows=[])
        if 'FROM monitoring_event_receipts' in q:
            if self.telemetry_at:
                return _Result(rows=[{
                    'monitored_system_id': 'sys-1',
                    'workspace_receipt_count': 1,
                    'latest_processed_at': self.telemetry_at.isoformat(),
                    'workspace_latest_processed_at': self.telemetry_at.isoformat(),
                }])
            return _Result(rows=[])
        if 'FROM provider_health_records' in q:
            return _Result(rows=[])
        if 'FROM monitoring_workspace_runtime_summary' in q:
            return _Result({'active_alerts_count': 0, 'active_incidents_count': 0, 'updated_at': now_iso})
        if 'FROM monitoring_configs' in q:
            return _Result({'c': 1})
        if 'FROM governance_actions' in q:
            return _Result({'c': 0})
        if 'FROM incident_timeline' in q:
            return _Result({'c': 0})
        if 'FROM monitoring_worker_state' in q:
            return _Result({})
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
            'checks': {'evm_rpc_reachable': True, 'provider_reachable_or_backfilling': True},
            'sales_claims_allowed': True,
            'status': 'PASS',
            'recent_truthfulness_state': 'real',
            'recent_evidence_state': 'real',
            'recent_real_event_count': 1,
        },
    )


def _default_health(now):
    return {
        'last_heartbeat_at': now.isoformat(),
        'last_cycle_at': now.isoformat(),
        'degraded': False,
        'last_error': None,
        'source_type': 'polling',
        'worker_running': True,
        'ingestion_mode': 'polling',
    }


@pytest.fixture(autouse=True)
def _clear_caches():
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK.clear()
    yield
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK.clear()


# ---------------------------------------------------------------------------
# Tests for _workspace_coverage_only_state
# ---------------------------------------------------------------------------


def test_coverage_heartbeat_clears_no_evidence_streak():
    """coverage_heartbeat_updates > 0 means telemetry was persisted — streak should NOT activate."""
    now = datetime.now(timezone.utc)
    # Drive many cycles with coverage_heartbeat_updates > 0 but real_events = 0
    for _ in range(200):
        result = monitoring_runner._workspace_coverage_only_state(
            workspace_id='ws-cov-1',
            cycle_at=now,
            provider_reachable=True,
            coverage_heartbeat_updates=1,
            real_events_detected=0,
        )
    assert result['active'] is False, 'streak must not activate when coverage telemetry is being persisted'
    assert result.get('state') != 'coverage_only_persistent_no_evidence'


def test_no_evidence_streak_fires_when_nothing_persisted():
    """When provider is reachable but neither coverage nor real events are produced, streak activates."""
    monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK.clear()
    threshold = monitoring_runner.MONITORING_COVERAGE_ONLY_WARNING_SECONDS
    now = datetime.now(timezone.utc)
    # First cycle sets up the state
    monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-cov-2',
        cycle_at=now,
        provider_reachable=True,
        coverage_heartbeat_updates=0,
        real_events_detected=0,
    )
    # Simulate enough time passing
    future = now + timedelta(seconds=threshold + 60)
    result = monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-cov-2',
        cycle_at=future,
        provider_reachable=True,
        coverage_heartbeat_updates=0,
        real_events_detected=0,
    )
    assert result['active'] is True, 'streak should activate when nothing is persisted beyond threshold'


def test_coverage_heartbeat_clears_existing_streak():
    """An existing streak clears immediately when coverage telemetry resumes."""
    monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK.clear()
    threshold = monitoring_runner.MONITORING_COVERAGE_ONLY_WARNING_SECONDS
    now = datetime.now(timezone.utc)
    # Build up an active streak (nothing persisted)
    monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-cov-3',
        cycle_at=now,
        provider_reachable=True,
        coverage_heartbeat_updates=0,
        real_events_detected=0,
    )
    future = now + timedelta(seconds=threshold + 60)
    active = monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-cov-3',
        cycle_at=future,
        provider_reachable=True,
        coverage_heartbeat_updates=0,
        real_events_detected=0,
    )
    assert active['active'] is True, 'prerequisite: streak must be active'

    # Now coverage heartbeat resumes — streak must clear
    cleared = monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-cov-3',
        cycle_at=future + timedelta(seconds=30),
        provider_reachable=True,
        coverage_heartbeat_updates=1,
        real_events_detected=0,
    )
    assert cleared['active'] is False
    assert 'ws-cov-3' not in monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK


# ---------------------------------------------------------------------------
# Tests for connection retry on psycopg.OperationalError
# ---------------------------------------------------------------------------


def test_closed_connection_retry_returns_degraded_with_connection_closed_classification(monkeypatch):
    """When pg_connection raises 'the connection is closed' on every attempt, the degraded payload
    must carry db_failure_classification='connection_closed' and reason tokens."""
    _enable_live_mode(monkeypatch)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: _default_health(now))

    def _always_closed():
        raise psycopg.OperationalError('the connection is closed')

    @contextmanager
    def _failing_pg():
        _always_closed()
        yield  # pragma: no cover

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _failing_pg())

    payload = monitoring_runner.monitoring_runtime_status()

    assert payload.get('db_failure_classification') == 'connection_closed', (
        f"Expected connection_closed classification, got: {payload.get('db_failure_classification')}"
    )
    # reason_tokens lives under payload['error']['reason_tokens'] in the normalized contract
    error_block = payload.get('error') or {}
    reason_tokens = error_block.get('reason_tokens') or []
    assert 'database_error.connection_closed' in reason_tokens, (
        f'Expected database_error.connection_closed in reason_tokens, got: {reason_tokens}'
    )


def test_closed_connection_retry_success_returns_real_payload(monkeypatch):
    """When the first pg_connection raises closed-connection but the retry succeeds,
    the returned payload must NOT be a degraded/error payload."""
    _enable_live_mode(monkeypatch)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: _default_health(now))
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace_context_for_request', lambda conn, req: (
        {'id': 'user-1'},
        {'workspace_id': 'ws-retry', 'workspace': {'slug': 'retry-slug'}},
        True,
    ))

    call_count = {'n': 0}

    @contextmanager
    def _pg_with_retry():
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise psycopg.OperationalError('the connection is closed')
        yield _FullConn(telemetry_at=now - timedelta(seconds=10), evidence_at=now - timedelta(seconds=10))

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _pg_with_retry())
    monkeypatch.setattr(
        monitoring_runner, 'evaluate_workspace_monitoring_continuity',
        lambda **_: {'continuity_status': 'healthy', 'continuity_slo_pass': True, 'continuity_reason_codes': []},
    )
    monkeypatch.setattr(monitoring_runner, 'list_workspace_monitored_system_rows', lambda conn, ws: [])
    monkeypatch.setattr(monitoring_runner, 'is_canonical_runtime_truth_enabled', lambda: False)

    payload = monitoring_runner.monitoring_runtime_status()

    # The retry should have succeeded — payload must not be an error shell
    assert payload.get('db_failure_classification') != 'connection_closed', (
        'Retry succeeded so payload should not carry connection_closed classification'
    )
    assert payload.get('status') != 'error', f'Unexpected error status: {payload.get("status")}'


# ---------------------------------------------------------------------------
# Tests for runtime-status with fresh live telemetry
# ---------------------------------------------------------------------------


def _build_live_conn(now):
    """Connection mock that provides fresh live telemetry data."""
    return _FullConn(
        telemetry_at=now - timedelta(seconds=20),
        evidence_at=now - timedelta(seconds=20),
    )


def test_runtime_status_not_offline_with_fresh_telemetry(monkeypatch):
    """When fresh telemetry_events rows with evidence_source=live exist, runtime_status must not be offline."""
    _enable_live_mode(monkeypatch)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: _default_health(now))
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_build_live_conn(now)))
    monkeypatch.setattr(
        monitoring_runner, 'evaluate_workspace_monitoring_continuity',
        lambda **_: {'continuity_status': 'healthy', 'continuity_slo_pass': True, 'continuity_reason_codes': []},
    )
    monkeypatch.setattr(monitoring_runner, 'list_workspace_monitored_system_rows', lambda conn, ws: [])
    monkeypatch.setattr(monitoring_runner, 'is_canonical_runtime_truth_enabled', lambda: False)

    payload = monitoring_runner.monitoring_runtime_status()

    assert payload.get('status') != 'Offline', f'Expected non-Offline status, got: {payload.get("status")}'
    assert payload.get('monitoring_status') != 'offline', (
        f'Expected non-offline monitoring_status, got: {payload.get("monitoring_status")}'
    )
