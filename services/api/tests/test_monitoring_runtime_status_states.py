from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status
from psycopg.errors import SyntaxError as PsycopgSyntaxError

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row or {}
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, evidence_at: datetime | None):
        self.evidence_at = evidence_at

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM alerts' in q:
            return _Result({'c': 1})
        if 'FROM incidents' in q:
            return _Result({'c': 1})
        if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
            now = datetime.now(timezone.utc).isoformat()
            return _Result(
                rows=[
                    {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now},
                    {'id': 'sys-2', 'workspace_id': 'ws-1', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now},
                    {'id': 'sys-3', 'workspace_id': 'ws-1', 'asset_id': 'asset-3', 'target_id': 'target-3', 'is_enabled': False, 'runtime_status': 'idle', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now},
                ]
            )
        if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
            return _Result({'c': 0})
        if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
            return _Result({'target_count': 2, 'asset_count': 2})
        if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
            return _Result(rows=[{'id': 'target-1'}, {'id': 'target-2'}])
        if 'FROM evidence' in q:
            return _Result({'observed_at': self.evidence_at, 'block_number': 123})
        if 'FROM analysis_runs' in q:
            return _Result(None)
        return _Result({})


@contextmanager
def _fake_pg(conn):
    yield conn


def _enable_live_mode(monkeypatch):
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {'checks': {'evm_rpc_reachable': True}, 'sales_claims_allowed': False, 'status': 'FAIL', 'recent_truthfulness_state': 'unknown_risk'},
    )


@pytest.fixture(autouse=True)
def _runtime_defaults(monkeypatch):
    _enable_live_mode(monkeypatch)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)


def test_runtime_status_idle_when_worker_healthy_without_recent_evidence(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Idle'




def test_runtime_status_canonical_reporting_window_uses_ingested_at(monkeypatch):
    now = datetime.now(timezone.utc)

    class _CaptureConn(_Conn):
        def __init__(self):
            super().__init__(now)
            self.queries: list[str] = []

        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            self.queries.append(q)
            return super().execute(query, params)

    conn = _CaptureConn()
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'websocket'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))

    monitoring_runner.monitoring_runtime_status()

    canonical_query = next(
        q for q in conn.queries
        if 'SELECT DISTINCT te.target_id' in q and 'FROM telemetry_events te' in q
    )
    assert 'te.ingested_at >= %s' in canonical_query
    assert 'te.created_at >= %s' not in canonical_query

def test_runtime_status_active_with_recent_evidence(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'websocket'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Active'
    assert payload['monitoring_status'] in {'active', 'live'}
    assert payload['active_systems'] == 2
    assert payload['monitored_systems'] == 3
    assert payload['protected_assets'] == 2
    assert payload['telemetry_available'] is True
    assert payload['monitored_systems_count'] == 3
    assert payload['protected_assets_count'] == 2
    assert payload['workspace_monitoring_summary']['runtime_status'] in {'idle', 'healthy'}
    assert payload['workspace_monitoring_summary']['coverage_state']['configured_systems'] == 2
    assert payload['workspace_monitoring_summary']['freshness_status'] in {'fresh', 'stale', 'unavailable'}
    assert payload['workspace_monitoring_summary']['contradiction_flags'] == []
    assert payload['workspace_monitoring_summary']['last_heartbeat_at'] is not None
    assert payload['workspace_monitoring_summary']['field_reason_codes'].get('configured_systems') != ['query_failure']
    assert payload['workspace_monitoring_summary']['field_reason_codes'].get('protected_assets') != ['query_failure']


def test_runtime_status_forces_degraded_when_live_continuity_slo_fails(monkeypatch):
    now = datetime.now(timezone.utc)
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
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(None)))
    monkeypatch.setattr(
        monitoring_runner,
        'evaluate_workspace_monitoring_continuity',
        lambda **_kwargs: {
            'continuity_status': 'degraded',
            'continuity_reason_codes': ['event_ingestion_stale'],
            'continuity_slo_pass': False,
            'heartbeat_age_seconds': 10,
            'telemetry_age_seconds': 900,
            'event_ingestion_age_seconds': 900,
            'detection_age_seconds': 900,
            'detection_pipeline_age_seconds': 900,
            'detection_eval_age_seconds': 900,
            'heartbeat_threshold_seconds': 180,
            'telemetry_threshold_seconds': 300,
            'detection_threshold_seconds': 300,
            'thresholds_seconds': {'heartbeat': 180, 'event_ingestion': 300, 'detection_eval': 300},
            'required_thresholds_seconds': {'heartbeat': 180, 'event_ingestion': 300, 'detection_eval': 300},
            'continuity_thresholds_seconds': {'heartbeat': 180, 'event_ingestion': 300, 'detection_eval': 300},
            'continuity_signals': {},
            'ingestion_freshness': 'stale',
            'detection_pipeline_freshness': 'stale',
            'worker_heartbeat_freshness': 'fresh',
            'event_throughput_window': 'out_of_window',
            'event_throughput_window_seconds': 300,
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()

    assert payload['monitoring_status'] in {'degraded', 'limited'}
    assert payload['status'] == 'Degraded'
    assert payload['runtime_status'] == 'degraded'
    assert payload['runtime_status_summary'] == 'degraded'
    assert payload['workspace_monitoring_summary']['runtime_status'] == 'degraded'
    assert payload['workspace_monitoring_summary']['monitoring_status'] == 'limited'
    assert payload['workspace_monitoring_summary']['continuity_slo_pass'] is False
    assert payload['continuity_slo']['pass'] is False
    assert payload['continuity_slo']['detection_age_seconds'] == 900
    assert payload['continuity_slo']['detection_pipeline_age_seconds'] == 900
    assert payload['continuity_slo']['detection_threshold_seconds'] == 300
    assert payload['runtime_degraded_reason_codes'] == ['continuity_slo_failed', 'event_ingestion_stale']
    assert payload['runtime_status_reason_codes'] == ['continuity_slo_failed', 'event_ingestion_stale']
    assert payload['workspace_monitoring_summary']['runtime_degraded_reason_codes'] == ['continuity_slo_failed', 'event_ingestion_stale']
    assert payload['degraded_reason'] == 'continuity_slo_failed:event_ingestion_stale'
    assert payload['continuity_breach_reasons'][0]['code'] == 'event_ingestion_stale'
    assert payload['continuity_slo']['breach_reasons'][0]['code'] == 'event_ingestion_stale'


def test_runtime_status_transitions_across_continuous_monitoring_lifecycle(monkeypatch):
    now = datetime.now(timezone.utc)
    health_state: dict[str, object] = {
        'last_heartbeat_at': now.isoformat(),
        'last_cycle_at': now.isoformat(),
        'degraded': False,
        'last_error': None,
        'source_type': 'polling',
        'worker_running': True,
    }

    class _TransitionConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'active',
                            'last_heartbeat': now_iso,
                            'last_event_at': now_iso,
                            'last_coverage_telemetry_at': now_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now_iso,
                        },
                    ]
                )
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_TransitionConn(None)))
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: dict(health_state))

    idle_payload = monitoring_runner.monitoring_runtime_status()
    assert idle_payload['status'] == 'Idle'
    assert idle_payload['runtime_status_summary'] in {'idle', 'healthy', 'live'}

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_TransitionConn(now - timedelta(seconds=20))))
    active_payload = monitoring_runner.monitoring_runtime_status()
    assert active_payload['status'] == 'Active'
    assert active_payload['runtime_status_summary'] in {'active', 'healthy', 'live'}

    health_state.update({'degraded': True, 'degraded_reason': 'stale_heartbeat'})
    degraded_payload = monitoring_runner.monitoring_runtime_status()
    assert degraded_payload['monitoring_status'] in {'degraded', 'limited'}
    assert degraded_payload['status'] == 'Degraded'


def test_runtime_status_active_live_coverage_promotes_mode_out_of_degraded(monkeypatch):
    now = datetime.now(timezone.utc)

    class _LiveCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                telemetry_iso = (now - timedelta(seconds=20)).isoformat()
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'active',
                            'last_heartbeat': now_iso,
                            'last_event_at': telemetry_iso,
                            'last_coverage_telemetry_at': telemetry_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now_iso,
                        },
                        {
                            'id': 'sys-2',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-2',
                            'target_id': 'target-2',
                            'is_enabled': True,
                            'runtime_status': 'active',
                            'last_heartbeat': now_iso,
                            'last_event_at': telemetry_iso,
                            'last_coverage_telemetry_at': telemetry_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now_iso,
                        },
                    ]
                )
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 1, 'evidence_state': 'real'}}})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'operational_mode': 'DEGRADED',
            'mode': 'live',
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_LiveCoverageConn(now - timedelta(seconds=20))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitoring_status'] in {'active', 'live'}
    assert payload['workspace_monitoring_summary']['reporting_systems'] > 0
    assert payload['source_of_evidence'] == 'live'
    assert payload['mode'] != 'DEGRADED'


def test_runtime_status_healthy_summary_with_live_coverage_does_not_force_degraded_mode(monkeypatch):
    now = datetime.now(timezone.utc)

    class _HealthyCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                telemetry_iso = (now - timedelta(seconds=15)).isoformat()
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'active',
                            'last_heartbeat': now_iso,
                            'last_event_at': telemetry_iso,
                            'last_coverage_telemetry_at': telemetry_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now_iso,
                        },
                    ]
                )
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 2, 'evidence_state': 'real'}}})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'operational_mode': 'DEGRADED',
            'mode': 'live',
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'degraded_reason': None,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HealthyCoverageConn(now - timedelta(seconds=15))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['workspace_monitoring_summary']['runtime_status'] == 'healthy'
    assert payload['workspace_monitoring_summary']['status_reason'] is None
    assert payload['workspace_monitoring_summary']['reporting_systems'] > 0
    assert payload['source_of_evidence'] == 'live'
    assert payload['mode'] != 'DEGRADED'


def test_runtime_status_degraded_mode_requires_degraded_reasons(monkeypatch):
    now = datetime.now(timezone.utc)

    class _LiveCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                telemetry_iso = (now - timedelta(seconds=20)).isoformat()
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'active',
                            'last_heartbeat': now_iso,
                            'last_event_at': telemetry_iso,
                            'last_coverage_telemetry_at': telemetry_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now_iso,
                        },
                    ]
                )
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 1, 'evidence_state': 'real'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_LiveCoverageConn(now - timedelta(seconds=20))))

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'operational_mode': 'DEGRADED',
            'mode': 'live',
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    payload_without_reason = monitoring_runner.monitoring_runtime_status()
    assert payload_without_reason['mode'] != 'DEGRADED'

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'operational_mode': 'LIVE',
            'mode': 'live',
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': True,
            'degraded_reason': 'stale_heartbeat',
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    payload_with_reason = monitoring_runner.monitoring_runtime_status()
    assert payload_with_reason['mode'] == 'DEGRADED'


def test_runtime_status_live_coverage_keeps_live_mode_when_only_claim_safety_risk(monkeypatch):
    now = datetime.now(timezone.utc)

    class _LiveCoverageNoRecentEventsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                telemetry_iso = (now - timedelta(seconds=20)).isoformat()
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'active',
                            'last_heartbeat': now_iso,
                            'last_event_at': telemetry_iso,
                            'last_coverage_telemetry_at': telemetry_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now_iso,
                        },
                    ]
                )
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now,
                        'response_payload': {'metadata': {'recent_real_event_count': 0, 'evidence_state': 'real'}},
                    }
                )
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_LiveCoverageNoRecentEventsConn(now - timedelta(seconds=20))))
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'operational_mode': 'LIVE',
            'mode': 'live',
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['workspace_monitoring_summary']['workspace_configured'] is True
    assert payload['monitoring_status'] in {'active', 'live'}
    assert payload['source_of_evidence'] == 'live'
    summary = payload['workspace_monitoring_summary']
    reporting_systems = int(summary.get('reporting_systems') or summary.get('reporting_systems_count') or 0)
    freshness_status = str(summary.get('freshness_status') or summary.get('telemetry_freshness') or '')
    assert reporting_systems > 0
    assert freshness_status == 'fresh'
    assert payload['mode'] in {'LIVE', 'HYBRID'}
    assert payload['mode'] != 'DEGRADED'
    assert 'no_recent_real_events' in payload['claim_safety_risk_indicators']


def test_runtime_status_marks_no_recent_real_events_as_limited_claim(monkeypatch):
    now = datetime.now(timezone.utc)

    class _LiveCoverageNoRecentEventsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                telemetry_iso = (now - timedelta(seconds=20)).isoformat()
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'active',
                            'last_heartbeat': now_iso,
                            'last_event_at': telemetry_iso,
                            'last_coverage_telemetry_at': telemetry_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now_iso,
                        },
                    ]
                )
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now,
                        'response_payload': {'metadata': {'recent_real_event_count': 0, 'evidence_state': 'real'}},
                    }
                )
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_LiveCoverageNoRecentEventsConn(now - timedelta(seconds=20))))
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'operational_mode': 'LIVE',
            'mode': 'live',
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {
            'checks': {
                'recent_real_event_count_positive': False,
                'evidence_window_recent_real_events': False,
                'no_recent_degraded_or_missing': False,
            },
            'reason_codes': ['recent_real_event_count_positive', 'evidence_window_recent_real_events', 'no_recent_degraded_or_missing'],
            'sales_claims_allowed': False,
            'status': 'FAIL',
            'recent_truthfulness_state': 'not_claim_safe',
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['continuity_status'] == 'continuous_no_evidence'
    assert payload['source_of_evidence'] == 'live'
    assert payload['mode'] == 'LIVE'
    assert payload['claim_validator_status'] == 'FAIL'
    assert 'no_recent_real_events' in payload['claim_safety_risk_indicators']
    assert 'claim_validator_fail' in payload['claim_safety_risk_indicators']
    assert 'claim_validator_reason_recent_real_event_count_positive' in payload['claim_safety_risk_indicators']


def test_runtime_status_counts_protected_assets_from_enabled_systems_not_only_active(monkeypatch):
    now = datetime.now(timezone.utc)

    class _IdleEnabledConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now_iso, 'monitoring_interval_seconds': 30, 'created_at': now_iso},
                        {'id': 'sys-2', 'workspace_id': 'ws-1', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now_iso, 'monitoring_interval_seconds': 30, 'created_at': now_iso},
                    ]
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_IdleEnabledConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['protected_assets'] == 2
    assert payload['protected_assets_count'] == 2
    assert payload['active_systems'] == 0
    assert payload['status'] == 'Idle'
    assert payload['monitoring_status'] == 'idle'


def test_runtime_status_coverage_uses_recent_heartbeats(monkeypatch):
    now = datetime.now(timezone.utc)

    class _MixedHeartbeatConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-2', 'workspace_id': 'ws-1', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': (now - timedelta(minutes=5)).isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_MixedHeartbeatConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['systems_with_recent_heartbeat'] == 1
    assert payload['monitored_systems'] == 2
    assert payload['status'] == 'Idle'


def test_runtime_status_degraded_on_stale_heartbeat(monkeypatch):
    now = datetime.now(timezone.utc)

    class _StaleConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                rows = super().execute(query, params)._rows
                stale = now - timedelta(minutes=15)
                return _Result(rows=[{**row, 'last_heartbeat': stale.isoformat()} for row in rows])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': (now - timedelta(minutes=20)).isoformat(), 'last_cycle_at': (now - timedelta(minutes=20)).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_StaleConn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Degraded'


def test_runtime_status_not_degraded_solely_for_zero_event_idle_systems(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ZeroEventHealthyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ZeroEventHealthyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitoring_status'] == 'idle'
    assert payload['status'] == 'Idle'
    assert payload['workspace_monitoring_summary']['runtime_status'] in {'idle', 'degraded'}
    assert payload['workspace_monitoring_summary']['coverage_state']['reporting_systems'] == 0
    assert payload['workspace_monitoring_summary']['contradiction_flags'] == []


def test_runtime_status_workspace_unconfigured_false_when_coverage_exists(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'last_event_at': None, 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 1, 'last_real_event_at': now.isoformat(), 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['workspace_monitoring_summary']['workspace_configured'] is True
    assert 'workspace_unconfigured_with_coverage' not in payload['workspace_monitoring_summary']['contradiction_flags']


def test_runtime_status_unconfigured_reason_codes_and_contract_keys_are_deterministic(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            if 'FROM analysis_runs' in q:
                return _Result(None)
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'ingestion_mode': 'live',
            'operational_mode': 'LIVE',
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredConn(None)))
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {
            'checks': {'evm_rpc_reachable': True},
            'sales_claims_allowed': False,
            'status': 'FAIL',
            'recent_truthfulness_state': 'unknown_risk',
            'recent_evidence_state': 'missing',
            'recent_real_event_count': 0,
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()

    assert payload['workspace_configured'] is False
    assert payload['status_reason'] == 'workspace_configuration_invalid:no_valid_protected_assets'
    assert payload['configuration_reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]
    assert payload['workspace_monitoring_summary']['configuration_reason_codes'] == payload['configuration_reason_codes']
    assert isinstance(payload['count_reason_codes'], dict)
    for counter_key in (
        'raw_enabled_targets',
        'monitorable_enabled_targets',
        'valid_asset_linked_targets',
        'enabled_monitored_systems',
        'valid_target_system_links',
    ):
        assert counter_key in payload
    assert set(payload['workspace_monitoring_summary']['field_reason_codes'].keys()) == {
        'protected_assets',
        'configured_systems',
        'reporting_systems',
        'last_poll_at',
        'last_heartbeat_at',
        'last_telemetry_at',
    }
    assert payload['workspace_monitoring_summary']['field_reason_codes']['protected_assets'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['configured_systems'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['reporting_systems'] == ['unconfigured_workspace']


def test_runtime_status_promotes_to_reporting_system_with_simulator_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _SimulatorTelemetryConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'healthy',
                            'last_heartbeat': now.isoformat(),
                            'last_event_at': None,
                            'last_coverage_telemetry_at': (now - timedelta(seconds=20)).isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        },
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': now - timedelta(seconds=20), 'block_number': 1})
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0}}})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'ingestion_mode': 'demo',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_SimulatorTelemetryConn(now - timedelta(seconds=20))))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['configured_systems'] >= 1
    assert summary['reporting_systems'] >= 1
    assert summary['runtime_status'] == 'idle'
    assert summary['evidence_source'] == 'simulator'
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['last_telemetry_at'] is not None
    assert summary['confidence_status'] == 'unavailable'


def test_workspace_configuration_truth_asset_only_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=1,
        linked_monitored_system_count=0,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert configured is False
    assert reason == 'no_linked_monitored_systems'


def test_workspace_configuration_truth_monitored_system_only_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=0,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert configured is False
    assert reason == 'no_valid_protected_assets'


def test_workspace_configuration_truth_without_persisted_enabled_config_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=1,
    )
    assert configured is False
    assert reason == 'no_persisted_enabled_monitoring_config'


def test_workspace_configuration_truth_invalid_target_system_linkage_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=0,
    )
    assert configured is False
    assert reason == 'target_system_linkage_invalid'


def test_workspace_configuration_truth_with_all_required_links_is_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=2,
        valid_target_system_link_count=2,
    )
    assert configured is True
    assert reason is None


def test_runtime_status_unconfigured_uses_primary_configuration_reason_for_status_reason(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredAndDegradedConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': True,
            'degraded_reason': 'stale_heartbeat',
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredAndDegradedConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']

    assert summary['workspace_configured'] is False
    assert summary['configuration_reason'] == 'no_valid_protected_assets'
    assert summary['configuration_reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]
    assert summary['status_reason'] == 'workspace_configuration_invalid:no_valid_protected_assets'
    assert payload['status_reason'] == 'workspace_configuration_invalid:no_valid_protected_assets'


def test_runtime_status_includes_recent_successful_checkpoint_without_events(monkeypatch):
    now = datetime.now(timezone.utc)

    class _SuccessfulCycleConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now - timedelta(seconds=45),
                        'response_payload': {
                            'metadata': {
                                'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
                                'evidence_state': 'real',
                                'confidence_basis': 'provider_evidence',
                                'recent_real_event_count': 0,
                            }
                        },
                    }
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_SuccessfulCycleConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['successful_detection_evaluation'] is True
    assert payload['successful_detection_evaluation_recent'] is True
    assert payload['last_confirmed_checkpoint'] is not None


def test_runtime_status_counts_workspace_rows_even_when_target_join_metadata_is_missing(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OrphanRowConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'ms-orphan',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-deleted',
                            'chain': 'ethereum-mainnet',
                            'is_enabled': True,
                            'runtime_status': 'idle',
                            'status': 'active',
                            'last_heartbeat': now.isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OrphanRowConn(None)))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda *_a, **_k: {'created_or_updated': 0, 'created_monitored_systems': 0, 'preserved_monitored_systems': 1, 'removed_monitored_systems': 0},
    )

    payload = monitoring_runner.monitoring_runtime_status(request=SimpleNamespace(headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert payload['protected_assets'] == 1
    assert payload['status'] != 'Offline'
    assert payload['monitoring_status'] != 'offline'
    assert payload['recent_real_event_count'] == 0


def test_runtime_status_degraded_when_enabled_targets_are_invalid(monkeypatch):
    now = datetime.now(timezone.utc)

    class _InvalidConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 2})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_InvalidConn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Degraded'
    assert payload['invalid_enabled_targets'] == 2


def test_runtime_status_stays_degraded_when_linked_asset_missing_exists(monkeypatch):
    now = datetime.now(timezone.utc)

    class _LinkedAssetMissingConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 1})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_LinkedAssetMissingConn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Degraded'
    assert payload['degraded_reason'] == 'invalid_enabled_targets'
    assert payload['invalid_enabled_targets'] == 1


def test_runtime_status_offline_without_active_systems(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OfflineConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                rows = super().execute(query, params)._rows
                return _Result(rows=[{**row, 'is_enabled': False, 'runtime_status': 'offline'} for row in rows])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': False},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OfflineConn(now - timedelta(seconds=30))))
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitored_systems'] > 0
    assert payload['monitoring_status'] != 'offline'
    assert payload['status'] != 'Offline'


def test_runtime_status_scopes_counts_to_active_workspace(monkeypatch):
    now = datetime.now(timezone.utc)

    class _WorkspaceConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            workspace_id = (params or (None,))[0] if params else None
            if 'FROM alerts' in q:
                return _Result({'c': 0})
            if 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                if workspace_id == 'ws-1':
                    return _Result(
                        rows=[
                            {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()}
                        ]
                    )
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-2', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-2', 'workspace_id': 'ws-2', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-3', 'workspace_id': 'ws-2', 'asset_id': 'asset-3', 'target_id': 'target-3', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-4', 'workspace_id': 'ws-2', 'asset_id': 'asset-4', 'target_id': 'target-4', 'is_enabled': False, 'runtime_status': 'offline', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 0})
            if 'FROM evidence e WHERE e.workspace_id = %s' in q:
                return _Result({'observed_at': now - timedelta(seconds=20), 'block_number': 42})
            return _Result({})

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_WorkspaceConn()))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'list_workspace_monitored_system_rows',
        lambda _c, _w: [
            {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()}
        ],
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert payload['enabled_systems'] == 1
    assert payload['active_systems'] == 1
    assert payload['monitoring_status'] == 'active'
    assert payload['counted_monitored_systems'] == 1
    assert payload['counted_enabled_systems'] == 1
    assert payload['workspace_header_present'] is True
    assert payload['request_user_resolved'] is True


def test_runtime_status_not_offline_when_workspace_has_enabled_monitored_systems(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=15))))
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitored_systems'] > 0
    assert payload['enabled_systems'] > 0
    assert payload['monitoring_status'] != 'offline'


def test_runtime_status_not_offline_when_valid_enabled_targets_exist_without_rows(monkeypatch):
    now = datetime.now(timezone.utc)

    class _HealthyTargetsNoRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            return super().execute(query, params)

    reconcile_calls: list[str | None] = []
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda _c, workspace_id=None: reconcile_calls.append(workspace_id) or {'created_or_updated': 0},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HealthyTargetsNoRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert reconcile_calls == [None]
    assert payload['monitoring_status'] != 'offline'
    assert payload['status'] != 'Offline'
    assert payload['monitored_systems'] >= 1
    assert payload['protected_assets'] >= 1


def test_runtime_status_not_offline_when_workspace_has_monitored_rows_but_no_enabled_targets(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OnlyDisabledMonitoredRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-disabled', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': False, 'runtime_status': 'offline', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OnlyDisabledMonitoredRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitored_systems'] == 1
    assert payload['monitoring_status'] != 'offline'
    assert payload['status'] != 'Offline'


def test_runtime_status_triggers_reconcile_when_enabled_rows_missing_for_healthy_targets(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OnlyDisabledRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-disabled', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': False, 'runtime_status': 'offline', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            return super().execute(query, params)

    reconcile_calls: list[str | None] = []
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda _c, workspace_id=None: reconcile_calls.append(workspace_id) or {'created_or_updated': 1, 'created_monitored_systems': 1, 'preserved_monitored_systems': 0, 'removed_monitored_systems': 0},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OnlyDisabledRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert reconcile_calls == [None]
    assert payload['monitoring_status'] != 'offline'


def test_runtime_status_triggers_reconcile_when_healthy_target_ids_are_missing_even_if_counts_match(monkeypatch):
    now = datetime.now(timezone.utc)

    class _MismatchedTargetRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-enabled', 'workspace_id': 'ws-1', 'asset_id': 'asset-9', 'target_id': 'target-stale', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-healthy'}])
            return super().execute(query, params)

    reconcile_calls: list[str | None] = []
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda _c, workspace_id=None: reconcile_calls.append(workspace_id) or {'created_or_updated': 1, 'created_monitored_systems': 1, 'preserved_monitored_systems': 0, 'removed_monitored_systems': 0},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_MismatchedTargetRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert reconcile_calls == [None]
    assert payload['monitoring_status'] != 'offline'


def test_runtime_status_and_monitored_system_listing_use_same_workspace_rows(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()

    class _RowsConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM alerts' in q or 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 0})
            if 'FROM evidence' in q:
                return _Result({'observed_at': datetime.now(timezone.utc), 'block_number': 1})
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now}])
            return _Result({})

    conn = _RowsConn()
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': datetime.now(timezone.utc).isoformat(), 'last_cycle_at': datetime.now(timezone.utc).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(monitoring_runner, 'list_workspace_monitored_system_rows', lambda _c, _w: conn.execute('SELECT ... FROM monitored_systems ms ORDER BY ms.created_at DESC').fetchall())

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert len(payload['counted_monitored_system_ids']) == 1


def test_runtime_status_workspace_resolution_reports_header_presence(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()

    class _HeaderConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-current', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now}])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': datetime.now(timezone.utc).isoformat(), 'last_cycle_at': datetime.now(timezone.utc).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HeaderConn(datetime.now(timezone.utc))))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-current', 'workspace': {'id': 'ws-current'}}, False),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'list_workspace_monitored_system_rows',
        lambda _c, _w: [{'id': 'sys-1', 'workspace_id': 'ws-current', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now}],
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={}))
    assert payload['resolved_workspace_id'] == 'ws-current'
    assert payload['workspace_header_present'] is False


def test_runtime_status_workspace_scoped_path_uses_same_rows_as_monitored_systems_listing(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()

    class _ScopedRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'chain': 'ethereum-mainnet',
                            'is_enabled': True,
                            'runtime_status': 'idle',
                            'status': 'ready',
                            'last_heartbeat': now,
                            'monitoring_interval_seconds': 30,
                            'created_at': now,
                        }
                    ]
                )
            if 'FROM alerts' in q or 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence e WHERE e.workspace_id = %s' in q:
                return _Result({'observed_at': datetime.now(timezone.utc), 'block_number': 5})
            return _Result({})

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': datetime.now(timezone.utc).isoformat(), 'last_cycle_at': datetime.now(timezone.utc).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ScopedRowsConn(datetime.now(timezone.utc))))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert payload['protected_assets'] == 1
    assert payload['enabled_systems'] == 1
    assert payload['monitoring_status'] != 'offline'


def test_runtime_status_workspace_scoped_path_preserves_coverage_telemetry_field(monkeypatch):
    now = datetime.now(timezone.utc)
    coverage_at = now - timedelta(seconds=20)
    coverage_iso = coverage_at.isoformat()

    class _CoverageScopedConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-coverage',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'chain': 'ethereum-mainnet',
                            'is_enabled': True,
                            'runtime_status': 'healthy',
                            'status': 'ready',
                            'last_heartbeat': now.isoformat(),
                            'last_event_at': None,
                            'last_coverage_telemetry_at': coverage_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'FROM alerts' in q or 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now,
                        'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}},
                    }
                )
            return _Result({})

    conn = _CoverageScopedConn(None)
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
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'list_workspace_monitored_system_rows',
        lambda _c, _w: conn.execute('SELECT ... FROM monitored_systems ms ORDER BY ms.created_at DESC').fetchall(),
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    summary = payload['workspace_monitoring_summary']
    assert summary['reporting_systems'] > 0
    assert summary['runtime_status'] == 'healthy'
    assert summary['monitoring_mode'] == 'live'
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['evidence_source'] == 'live'
    assert summary['last_coverage_telemetry_at'] is not None
    assert summary['freshness_status'] == 'fresh'
    assert summary['confidence_status'] == 'high'


def test_contradiction_guard_offline_runtime_clears_current_telemetry(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OfflineTelemetryConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            if 'FROM evidence' in q:
                return _Result({'observed_at': now, 'block_number': 12})
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'last_real_event_at': now.isoformat(), 'recent_real_event_count': 2, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OfflineTelemetryConn(now)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['runtime_status'] == 'offline'
    assert summary['last_telemetry_at'] is None
    assert summary['freshness_status'] == 'unavailable'
    assert 'offline_with_current_telemetry' not in summary['contradiction_flags']


def test_contradiction_guard_never_marks_live_monitoring_without_reporting_systems(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NoReportingConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(), 'last_event_at': None}])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': None, 'block_number': None})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NoReportingConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['coverage_state']['configured_systems'] > 0
    assert summary['coverage_state']['reporting_systems'] == 0
    assert summary['runtime_status'] != 'healthy'
    assert 'live_monitoring_without_reporting_systems' not in summary['contradiction_flags']


def test_workspace_summary_stays_idle_until_first_reporting_telemetry(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredNoTelemetryConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'degraded', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(), 'last_event_at': None}])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': None, 'block_number': None})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': True, 'degraded_reason': 'provider_backpressure', 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredNoTelemetryConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['coverage_state']['configured_systems'] == 1
    assert summary['coverage_state']['reporting_systems'] == 0
    assert summary['configured_systems'] == 1
    assert summary['reporting_systems'] == 0
    assert summary['runtime_status'] == 'idle'
    assert summary['last_telemetry_at'] is None
    assert payload['telemetry_available'] is False


def test_contradiction_guard_flags_heartbeat_without_telemetry(monkeypatch):
    now = datetime.now(timezone.utc)

    class _HeartbeatOnlyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'idle',
                            'last_heartbeat': now.isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                            'last_event_at': None,
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now,
                        'response_payload': {
                            'metadata': {
                                'recent_real_event_count': 1,
                                'last_real_event_at': now.isoformat(),
                                'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
                            }
                        },
                    }
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HeartbeatOnlyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['last_heartbeat_at'] is not None
    assert summary['last_telemetry_at'] is None
    assert summary['runtime_status'] in {'idle', 'degraded'}
    assert summary['evidence_source'] != 'live'


def test_contradiction_guard_workspace_not_configured_with_monitored_systems_flagged(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': None, 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(), 'last_event_at': None}])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['workspace_configured'] is False
    assert summary['coverage_state']['configured_systems'] > 0
    assert summary['configured_systems'] > 0
    assert summary['configuration_reason'] == 'no_valid_protected_assets'
    assert 'workspace_unconfigured_with_coverage' in summary['contradiction_flags']


def test_runtime_status_live_with_fresh_coverage_telemetry_without_target_events(monkeypatch):
    now = datetime.now(timezone.utc)

    class _CoverageOnlyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=20)).isoformat(),
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            if 'FROM detections' in q:
                return _Result({'detected_at': now - timedelta(seconds=30)})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CoverageOnlyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['runtime_status'] == 'healthy'
    assert summary['freshness_status'] == 'fresh'
    assert summary['confidence_status'] == 'high'
    assert payload['confidence_status'] == summary['confidence_status']
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['evidence_source'] == 'live'
    assert summary['last_detection_at'] is not None


def test_runtime_status_receipts_only_keeps_reporting_systems_zero(monkeypatch):
    now = datetime.now(timezone.utc)

    class _CoverageReceiptsFallbackConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitoring_event_receipts e' in q and "e.evidence_source = 'live'" in q and "e.telemetry_kind = 'coverage'" in q:
                return _Result(
                    rows=[
                        {
                            'latest_processed_at': (now - timedelta(seconds=15)).isoformat(),
                            'workspace_latest_processed_at': (now - timedelta(seconds=15)).isoformat(),
                            'workspace_receipt_count': 1,
                            'receipt_count': 1,
                            'monitored_system_id': 'sys-1',
                        }
                    ]
                )
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CoverageReceiptsFallbackConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    reporting_systems = payload.get('reporting_systems', summary.get('reporting_systems', payload.get('legacy_diagnostics', {}).get('reporting_systems')))
    assert reporting_systems == 0
    assert payload['legacy_diagnostics']['legacy_reporting_systems'] == 1
    assert payload['details']['compatibility']['legacy_receipts_reporting_systems'] == 1


def test_runtime_status_poll_only_keeps_reporting_systems_zero(monkeypatch):
    now = datetime.now(timezone.utc)

    class _PollOnlyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'idle',
                    'last_heartbeat': None,
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': None, 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_PollOnlyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    reporting_systems = payload.get('reporting_systems', summary.get('reporting_systems', payload.get('legacy_diagnostics', {}).get('reporting_systems')))
    assert reporting_systems == 0


def test_runtime_status_heartbeat_only_keeps_reporting_systems_zero(monkeypatch):
    now = datetime.now(timezone.utc)

    class _HeartbeatOnlyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'idle',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': None, 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HeartbeatOnlyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    reporting_systems = payload.get('reporting_systems', summary.get('reporting_systems', payload.get('legacy_diagnostics', {}).get('reporting_systems')))
    assert reporting_systems == 0


def test_runtime_status_treats_null_enabled_system_as_enabled_for_live_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NullEnabledCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitoring_event_receipts e' in q and "e.evidence_source = 'live'" in q and "e.telemetry_kind = 'coverage'" in q:
                if 'ms.is_enabled IS DISTINCT FROM FALSE' not in q:
                    return _Result(rows=[])
                return _Result(
                    rows=[
                        {
                            'latest_processed_at': (now - timedelta(seconds=12)).isoformat(),
                            'workspace_latest_processed_at': (now - timedelta(seconds=12)).isoformat(),
                            'workspace_receipt_count': 1,
                            'receipt_count': 1,
                            'monitored_system_id': 'sys-null-enabled',
                        }
                    ]
                )
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                if 'COALESCE(ms.is_enabled, TRUE) AS is_enabled' not in q:
                    return _Result(rows=[])
                return _Result(rows=[{
                    'id': 'sys-null-enabled',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': None,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NullEnabledCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert payload['systems_with_recent_heartbeat'] == 1
    assert summary['reporting_systems'] == 1
    assert summary['runtime_status'] == 'healthy'
    assert summary['telemetry_kind'] == 'coverage'


def test_runtime_status_live_heartbeat_and_poll_without_coverage_keeps_confidence_unavailable(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NoCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'idle',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

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
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NoCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['freshness_status'] == 'unavailable'
    assert summary['runtime_status'] == 'idle'
    assert summary['confidence_status'] == 'unavailable'
    assert payload['confidence_status'] == summary['confidence_status']
    assert summary['reporting_systems'] == 0
    assert summary['status_reason'] == 'no_fresh_live_coverage_telemetry'


def test_runtime_status_demo_coverage_does_not_count_as_live(monkeypatch):
    now = datetime.now(timezone.utc)

    class _DemoCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1',
                    'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=15)).isoformat(),
                    'last_event_at': None, 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'ingestion_mode': 'demo', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_DemoCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['evidence_source'] == 'simulator'
    assert summary['monitoring_mode'] == 'simulator'
    assert summary['runtime_status'] == 'idle'
    assert summary['confidence_status'] == 'unavailable'


def test_runtime_status_replay_or_demo_receipts_do_not_count_as_live_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ReplayReceiptConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitoring_event_receipts e' in q and "e.evidence_source = 'live'" in q and "e.telemetry_kind = 'coverage'" in q:
                if "NOT IN ('demo', 'simulator', 'replay', 'synthetic', 'fallback')" in q:
                    return _Result(rows=[])
                return _Result(
                    rows=[{
                        'processed_at': (now - timedelta(seconds=8)).isoformat(),
                        'target_id': 'target-1',
                        'monitored_system_id': 'sys-1',
                    }]
                )
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1',
                    'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None, 'last_event_at': None, 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ReplayReceiptConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['reporting_systems'] == 0
    assert summary['runtime_status'] == 'idle'
    assert summary['evidence_source'] != 'live'


def test_runtime_status_live_coverage_with_historical_detections_stays_live(monkeypatch):
    now = datetime.now(timezone.utc)
    old_detection_at = now - timedelta(hours=3)

    class _CoverageWithHistoryConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=10)).isoformat(),
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': old_detection_at, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CoverageWithHistoryConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['runtime_status'] == 'healthy'
    assert summary['reporting_systems'] > 0
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['confidence_status'] == 'high'


def test_runtime_status_marks_heartbeat_only_as_no_evidence(monkeypatch):
    now = datetime.now(timezone.utc)

    class _HeartbeatOnlyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': now.isoformat(),
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'evidence_state': 'real'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HeartbeatOnlyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['coverage_heartbeat_count'] > 0
    assert payload['coverage_heartbeat_updates'] > 0
    assert payload['real_event_count'] == 0
    assert payload['real_events_detected'] == 0
    assert payload['recent_evidence_state'] == 'no_evidence'
    assert payload['recent_evidence_reason_code'] == 'coverage_only_no_events'
    assert payload['continuity_signals']['event_ingestion_freshness'] == 'missing'
    assert payload['continuity_slo_pass'] is False
    assert payload['required_thresholds_seconds']['heartbeat'] > 0
    assert payload['workspace_monitoring_summary']['continuity_slo_pass'] is False
    assert payload['workspace_monitoring_summary']['event_ingestion_age_seconds'] is None


def test_derive_system_runtime_state_marks_unsupported_target_type_explicitly():
    runtime_status, freshness_status, confidence_status, coverage_reason = monitoring_runner._derive_system_runtime_state(
        {
            'target_type': 'oracle_feed',
            'provider_status': 'no_evidence',
            'source_status': 'no_evidence',
            'events_ingested': 0,
            'recent_real_event_count': 0,
            'degraded_reason': None,
        },
        is_enabled=True,
    )
    assert runtime_status == 'degraded'
    assert freshness_status == 'stale'
    assert confidence_status == 'low'
    assert coverage_reason == 'unsupported_target_type_for_live_coverage'


def test_runtime_status_summary_prefers_unsupported_target_type_reason(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnsupportedTypeConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'target_type': 'oracle_feed',
                            'is_enabled': True,
                            'runtime_status': 'degraded',
                            'status': 'active',
                            'last_heartbeat': now.isoformat(),
                            'last_event_at': None,
                            'last_coverage_telemetry_at': None,
                            'coverage_reason': 'unsupported_target_type_for_live_coverage',
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': None, 'block_number': None})
            return super().execute(query, params)

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
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnsupportedTypeConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['status_reason'] == 'no_fresh_live_coverage_telemetry'
    assert payload['coverage_reason'] == 'no_evidence'


def test_runtime_status_workspace_configured_when_target_join_type_missing(monkeypatch):
    now = datetime.now(timezone.utc)

    class _MissingTargetTypeJoinConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'target_type': None,
                            'is_enabled': True,
                            'runtime_status': 'healthy',
                            'last_heartbeat': now.isoformat(),
                            'last_coverage_telemetry_at': (now - timedelta(seconds=15)).isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_MissingTargetTypeJoinConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['workspace_configured'] is True
    assert summary['configuration_reason'] is None
    assert summary['runtime_status'] == 'healthy'


def test_runtime_status_returns_explicit_configuration_and_evidence_fields(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=20)).isoformat(),
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['valid_protected_assets'] == 1
    assert summary['linked_monitored_systems'] == 1
    assert summary['enabled_configs'] == 1
    assert summary['valid_link_count'] == 1
    assert summary['source_of_evidence'] in {'live', 'replay_or_none', 'simulator'}
    assert payload['status_reason'] == summary['status_reason']
    assert isinstance(payload['stale_heartbeat'], bool)
    assert isinstance(payload['provider_degraded_flag'], bool)
    assert payload['coverage_receipts_workspace_count'] >= 0
    assert payload['coverage_receipts_last_at'] is None
    assert payload['stale_heartbeat'] == summary['stale_heartbeat']
    assert payload['provider_degraded_flag'] == summary['provider_degraded_flag']
    assert payload['coverage_receipts_workspace_count'] == summary['coverage_receipts_workspace_count']


def test_runtime_status_not_offline_when_configured_but_no_fresh_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _StaleCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'idle',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_StaleCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['workspace_configured'] is True
    assert summary['runtime_status'] in {'idle', 'degraded'}
    assert summary['runtime_status'] != 'offline'
    assert summary['status_reason'] == 'no_fresh_live_coverage_telemetry'


def test_runtime_status_includes_workspace_identity_fields(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-prod',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': now.isoformat(),
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-prod'}, state=SimpleNamespace())
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (
            {'id': 'user-1'},
            {'workspace_id': 'ws-prod', 'workspace': {'id': 'ws-prod', 'slug': 'prod-ops'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status(request)
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['workspace_slug'] == 'prod-ops'


def test_runtime_status_workspace_scoped_success_keeps_identity_and_reports_live_healthy_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _WorkspaceScopedHealthyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-prod',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'is_enabled': True,
                            'runtime_status': 'healthy',
                            'last_heartbeat': now.isoformat(),
                            'last_coverage_telemetry_at': now.isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM monitoring_event_receipts e' in q and "e.telemetry_kind = 'coverage'" in q:
                return _Result(rows=[{'processed_at': now, 'target_id': 'target-1', 'monitored_system_id': 'sys-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 2, 'evidence_state': 'real'}}})
            return super().execute(query, params)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-prod'}, state=SimpleNamespace())
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'ingestion_mode': 'live',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (
            {'id': 'user-1'},
            {'workspace_id': 'ws-prod', 'workspace': {'id': 'ws-prod', 'slug': 'prod-ops'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_WorkspaceScopedHealthyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status(request)
    summary = payload['workspace_monitoring_summary']
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['workspace_slug'] == 'prod-ops'
    assert summary['runtime_status'] == 'healthy'
    assert summary['configured_systems'] >= 1
    assert summary['reporting_systems'] >= 1
    assert summary['evidence_source'] == 'live'
    assert summary['telemetry_kind'] in {'coverage', 'event'}
    assert summary['last_coverage_telemetry_at'] is not None
    assert summary['last_telemetry_at'] is not None


def test_runtime_status_partial_query_failure_keeps_workspace_identity_and_structured_reason_codes(monkeypatch):
    now = datetime.now(timezone.utc)

    class _SyntaxErrorConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitoring_event_receipts e' in q and "e.telemetry_kind = 'coverage'" in q:
                raise PsycopgSyntaxError('syntax error at or near "$1"')
            return super().execute(query, params)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-prod'}, state=SimpleNamespace())
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (
            {'id': 'user-1'},
            {'workspace_id': 'ws-prod', 'workspace': {'id': 'ws-prod', 'slug': 'prod-ops'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_SyntaxErrorConn(None)))

    payload = monitoring_runner.monitoring_runtime_status(request)
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['workspace_slug'] == 'prod-ops'
    assert request.state.workspace_id == 'ws-prod'
    assert request.state.workspace_slug == 'prod-ops'
    assert payload['configuration_reason'] != 'runtime_status_unavailable'
    assert payload['runtime_error_code'] == 'runtime_coverage_query_failed'
    assert payload['runtime_degraded_reason'] == 'partial_query_failure'
    assert payload['status_reason'] in {'runtime_status_degraded:partial_query_failure', 'no_fresh_live_coverage_telemetry'}
    assert 'error' not in payload
    assert payload['field_reason_codes']['reporting_systems'] == ['optional_table_unavailable']
    assert payload['field_reason_codes']['last_coverage_telemetry_at'] == ['optional_table_unavailable']
    assert payload['field_reason_codes']['last_telemetry_at'] == ['optional_table_unavailable']
    assert payload['workspace_monitoring_summary']['runtime_error_code'] == 'runtime_coverage_query_failed'
    assert payload['workspace_monitoring_summary']['runtime_degraded_reason'] == 'partial_query_failure'
    assert payload['workspace_monitoring_summary']['field_reason_codes']['reporting_systems'] == ['optional_table_unavailable']
    assert payload['coverage_receipts_workspace_count'] == 0
    assert payload['coverage_receipts_last_at'] is None


def test_runtime_status_query_failure_uses_pre_resolved_workspace_identity_from_request_state(monkeypatch):
    now = datetime.now(timezone.utc)
    request = SimpleNamespace(headers={}, state=SimpleNamespace(workspace_id='ws-prod', workspace_slug='prod-ops'))

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (_ for _ in ()).throw(PsycopgSyntaxError('syntax error at or near "$1"')),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(None)))

    payload = monitoring_runner.monitoring_runtime_status(request)
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['workspace_slug'] == 'prod-ops'
    assert payload['error']['code'] == 'runtime_status_db_error'
    assert payload['error']['stage'] == 'query'
    assert payload['error']['stage_detail'] == 'workspace_context_resolution'
    assert payload['configuration_reason'] == 'runtime_status_unavailable'
    assert payload['workspace_monitoring_summary']['configuration_reason_codes'] == ['runtime_status_unavailable']


def test_runtime_status_partial_fallback_avoids_summary_unavailable_reason(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OptionalSummaryFailureConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM analysis_runs' in q:
                raise RuntimeError('optional summary probe failed')
            return super().execute(query, params)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-prod'}, state=SimpleNamespace())
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (
            {'id': 'user-1'},
            {'workspace_id': 'ws-prod', 'workspace': {'id': 'ws-prod', 'slug': 'prod-ops'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OptionalSummaryFailureConn(now - timedelta(seconds=20))))

    payload = monitoring_runner.monitoring_runtime_status(request)
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['workspace_monitoring_summary']
    assert payload['runtime_degraded_reason'] == 'partial_query_failure'
    assert payload['runtime_error_code'] == 'runtime_optional_query_failed'
    assert payload['configuration_reason'] != 'runtime_status_unavailable'
    assert 'summary_unavailable' not in str(payload.get('status_reason') or '')


def test_runtime_status_target_count_query_failure_degrades_without_summary_reset(monkeypatch):
    now = datetime.now(timezone.utc)

    class _TargetCountFailureConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                raise RuntimeError('transient target count query failure')
            return super().execute(query, params)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-prod'}, state=SimpleNamespace())
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (
            {'id': 'user-1'},
            {'workspace_id': 'ws-prod', 'workspace': {'id': 'ws-prod', 'slug': 'prod-ops'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_TargetCountFailureConn(now - timedelta(seconds=20))))

    payload = monitoring_runner.monitoring_runtime_status(request)
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['runtime_degraded_reason'] == 'partial_query_failure'
    assert payload['runtime_error_code'] == 'runtime_optional_query_failed'
    assert payload['configuration_reason'] != 'runtime_status_unavailable'
    assert payload['workspace_monitoring_summary']['runtime_degraded_reason'] == 'partial_query_failure'
    assert payload['field_reason_codes']['invalid_enabled_targets'] == ['optional_table_unavailable']
    assert 'summary_unavailable' not in str(payload.get('status_reason') or '')


def test_runtime_status_workspace_unconfigured_path_uses_configuration_diagnostics(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['workspace_configured'] is False
    assert payload['configuration_reason'] == 'no_valid_protected_assets'
    assert payload['configuration_diagnostics']['reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]
    assert payload['workspace_monitoring_summary']['field_reason_codes']['protected_assets'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['configured_systems'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['reporting_systems'] == ['unconfigured_workspace']


def test_runtime_debug_reports_configuration_reason_codes_in_production_when_workspace_unconfigured(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NoBootstrapWorkspaceConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NoBootstrapWorkspaceConn(None)))

    payload = monitoring_runner.monitoring_runtime_debug_payload()
    diagnostics = payload['configuration_diagnostics']
    assert payload['workspace_configured'] is False
    assert diagnostics['workspace_configured'] is False
    assert diagnostics['reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]


def test_runtime_debug_reports_workspace_configured_true_after_workspace_repaired(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=20))))

    payload = monitoring_runner.monitoring_runtime_debug_payload()
    diagnostics = payload['configuration_diagnostics']
    assert payload['workspace_configured'] is True
    assert diagnostics['workspace_configured'] is True
    assert diagnostics['reason_codes'] == []


def test_runtime_status_returns_schema_incomplete_payload_when_runtime_columns_missing(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)

    def _raise_schema_error(_connection):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                'code': 'runtime_schema_incomplete',
                'missing_columns': ['monitored_systems.last_coverage_telemetry_at'],
                'migration_hints': ['0036', '0037', '0038', '0039'],
            },
        )

    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', _raise_schema_error)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert payload['configuration_reason'] == 'runtime_schema_incomplete'
    assert payload['status_reason'] == 'runtime_schema_column_missing:monitored_systems.last_coverage_telemetry_at'
    assert payload['error']['code'] == 'runtime_schema_incomplete'
    assert payload['error']['migration_hints'] == ['0036', '0037', '0038', '0039']
    assert summary['configuration_reason'] == 'runtime_schema_incomplete'
    assert summary['status_reason'] == 'runtime_schema_column_missing:monitored_systems.last_coverage_telemetry_at'


def test_runtime_status_detection_evaluation_checkpoint_prevents_missing_pipeline(monkeypatch):
    now = datetime.now(timezone.utc)

    class _DetectionEvaluationConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=15)).isoformat(),
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now - timedelta(seconds=20),
                        'response_payload': {'metadata': {'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}},
                    }
                )
            if 'FROM detections' in q:
                return _Result(None)
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_DetectionEvaluationConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['detection_pipeline_freshness'] != 'missing'
    assert 'detection_pipeline_missing' not in (payload.get('continuity_reason_codes') or [])
    assert payload['continuity_status'] != 'degraded'


def test_runtime_status_surfaces_loop_health_fields(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': True, 'last_error': 'db timeout', 'source_type': 'polling', 'worker_running': False},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))
    monitoring_runner.set_background_loop_health(
        loop_running=False,
        last_successful_cycle='2026-04-29T12:00:00Z',
        consecutive_failures=3,
        next_retry_at='2026-04-29T12:02:00Z',
        backoff_seconds=120,
    )

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['loop_running'] is False
    assert payload['last_successful_cycle'] == '2026-04-29T12:00:00Z'
    assert payload['consecutive_failures'] == 3
    assert payload['next_retry_at'] == '2026-04-29T12:02:00Z'
    assert payload['backoff_seconds'] == 120


def test_workspace_summary_normalization_preserves_new_contradiction_flags_and_banner_reasons():
    from services.api.app import workspace_monitoring_summary as wms

    canonical = wms._canonical_summary(
        {
            'workspace_configured': True,
            'runtime_status': 'degraded',
            'monitoring_status': 'limited',
            'contradiction_flags': [
                'asset_monitoring_attached_but_no_monitored_systems',
                'ui_protected_assets_positive_but_runtime_zero',
                'ui_live_monitoring_claim_without_telemetry',
                'ui_healthy_claim_with_zero_reporting_systems',
            ],
            'top_banner_reasons': ['Live monitoring is claimed, but telemetry is missing.'],
        }
    )
    assert 'asset_monitoring_attached_but_no_monitored_systems' in canonical['contradiction_flags']
    assert 'ui_protected_assets_positive_but_runtime_zero' in canonical['contradiction_flags']
    assert 'ui_live_monitoring_claim_without_telemetry' in canonical['contradiction_flags']
    assert 'ui_healthy_claim_with_zero_reporting_systems' in canonical['contradiction_flags']
    assert canonical['top_banner_reasons'] == ['Live monitoring is claimed, but telemetry is missing.']
