"""Tests for the live RPC proof chain gating logic.

A. Live telemetry only → live_telemetry_without_proof_chain guard fires, status != live
B. Live telemetry + detection but no alert/incident → not fully LIVE
C. Full proof chain (detection + alert + incident + response + evidence) → status == live
D. Launch proof: summary fields require full chain to be truthy
E. _build_summary helper exports consistent chain field set
F. monitoring_status == 'live' only when full proof chain exists (no guard flags)
G. _ensure_workspace_live_rpc_proof_chain worker path: canonical + legacy chain created
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services.api.app.workspace_monitoring_summary import (
    HARD_GUARD_FLAGS,
    HARD_GUARD_PRIORITY,
    build_workspace_monitoring_summary,
)


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)


def _base_params(**overrides: object) -> dict[str, object]:
    now = _now()
    params: dict[str, object] = {
        'now': now,
        'workspace_configured': True,
        'configuration_reason_codes': None,
        'query_failure_detected': False,
        'schema_drift_detected': False,
        'missing_telemetry_only': False,
        'monitoring_mode': 'live',
        'runtime_status': 'live',
        'configured_systems': 1,
        'monitored_systems_count': 1,
        'reporting_systems': 1,
        'protected_assets': 1,
        'last_poll_at': now,
        'last_heartbeat_at': now,
        'last_telemetry_at': now - timedelta(seconds=30),
        'last_coverage_telemetry_at': now - timedelta(seconds=30),
        'telemetry_kind': 'coverage',
        'last_detection_at': None,
        'evidence_source': 'live',
        'status_reason': None,
        'configuration_reason': None,
        'valid_protected_asset_count': 1,
        'linked_monitored_system_count': 1,
        'persisted_enabled_config_count': 1,
        'valid_target_system_link_count': 1,
        'telemetry_window_seconds': 300,
        'active_alerts_count': 0,
        'active_incidents_count': 0,
        'response_actions_count': 0,
        'evidence_packages_count': 0,
        'detections_count': 0,
    }
    params.update(overrides)
    return params


def _build(**overrides: object) -> dict[str, object]:
    return build_workspace_monitoring_summary(**_base_params(**overrides))


# ──────────────────────────────────────────────────────────────────────────────
# A. Live telemetry only → live_telemetry_without_proof_chain guard, not LIVE
# ──────────────────────────────────────────────────────────────────────────────

def test_A_live_telemetry_without_detection_sets_proof_chain_guard() -> None:
    summary = _build(detections_count=0)
    assert 'live_telemetry_without_proof_chain' in summary['contradiction_flags']


def test_A_proof_chain_guard_is_in_hard_guard_flags() -> None:
    assert 'live_telemetry_without_proof_chain' in HARD_GUARD_FLAGS


def test_A_live_telemetry_only_does_not_yield_live_status() -> None:
    summary = _build(detections_count=0)
    assert summary['monitoring_status'] != 'live', (
        f"Expected non-live but got {summary['monitoring_status']}; "
        f"guard_flags={summary['guard_flags']}"
    )


def test_A_guard_flag_present_in_guard_flags_list() -> None:
    summary = _build(detections_count=0)
    assert 'live_telemetry_without_proof_chain' in summary['guard_flags']


# ──────────────────────────────────────────────────────────────────────────────
# B. Partial chain (detection exists, but no alert/incident) → not LIVE
# ──────────────────────────────────────────────────────────────────────────────

def test_B_detection_without_alert_does_not_yield_live_status() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=0,
    )
    assert summary['monitoring_status'] != 'live', (
        f"Expected non-live; guard_flags={summary['guard_flags']}"
    )


def test_B_proof_chain_incomplete_guard_fires_when_detection_without_alert() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=0,
    )
    assert 'live_proof_chain_incomplete' in summary['contradiction_flags']


def test_B_telemetry_guard_absent_when_detection_exists() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=0,
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_B_proof_chain_incomplete_is_in_hard_guard_flags() -> None:
    assert 'live_proof_chain_incomplete' in HARD_GUARD_FLAGS
    assert 'live_proof_chain_incomplete' in HARD_GUARD_PRIORITY


def test_B_incident_without_alert_fires_contradiction() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=1,
    )
    assert 'incident_exists_without_alert' in summary['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# C. Full proof chain → monitoring_status == 'live'
# ──────────────────────────────────────────────────────────────────────────────

def test_C_full_proof_chain_yields_live_status() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        response_actions_count=1,
        evidence_packages_count=1,
        last_alert_at=now - timedelta(seconds=9),
        last_incident_at=now - timedelta(seconds=8),
        last_response_action_at=now - timedelta(seconds=7),
        last_evidence_export_at=now - timedelta(seconds=6),
        telemetry_kind='coverage',
    )
    guard = summary['guard_flags']
    assert summary['monitoring_status'] == 'live', (
        f"Expected live but got {summary['monitoring_status']}; guard_flags={guard}"
    )


def test_C_full_proof_chain_has_no_hard_guard_flags() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        response_actions_count=1,
        evidence_packages_count=1,
        last_alert_at=now - timedelta(seconds=9),
        last_incident_at=now - timedelta(seconds=8),
        last_response_action_at=now - timedelta(seconds=7),
        last_evidence_export_at=now - timedelta(seconds=6),
        telemetry_kind='coverage',
    )
    assert summary['guard_flags'] == [], f"Unexpected guard flags: {summary['guard_flags']}"


def test_C_full_proof_chain_no_proof_chain_guard() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        telemetry_kind='coverage',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# D. Launch proof strictness: summary fields require full chain
# ──────────────────────────────────────────────────────────────────────────────

def test_D_proof_chain_guard_not_fired_when_no_reporting_systems() -> None:
    # Guard requires reporting_systems > 0 (live telemetry present). Without
    # reporting systems the workspace isn't even in the live coverage window.
    summary = _build(
        reporting_systems=0,
        detections_count=0,
        telemetry_kind='coverage',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_D_proof_chain_guard_not_fired_when_telemetry_stale() -> None:
    now = _now()
    summary = _build(
        detections_count=0,
        last_telemetry_at=now - timedelta(seconds=3600),
        last_coverage_telemetry_at=now - timedelta(seconds=3600),
        telemetry_window_seconds=300,
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_D_proof_chain_guard_not_fired_for_simulator_evidence() -> None:
    summary = _build(
        detections_count=0,
        evidence_source='simulator',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_D_proof_chain_guard_not_fired_when_evidence_source_none() -> None:
    summary = _build(
        detections_count=0,
        evidence_source='none',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# E. _build_summary helper: detections_count=0 default doesn't break old callers
# ──────────────────────────────────────────────────────────────────────────────

def test_E_build_workspace_monitoring_summary_accepts_detections_count_param() -> None:
    now = _now()
    result = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=None,
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='live',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=30),
        last_coverage_telemetry_at=now - timedelta(seconds=30),
        telemetry_kind='coverage',
        last_detection_at=None,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
        detections_count=0,
    )
    assert isinstance(result, dict)
    assert 'monitoring_status' in result


def test_E_omitting_detections_count_does_not_fire_proof_chain_guard() -> None:
    # When detections_count is omitted (default None), the proof chain guard
    # must not fire — this preserves backward compatibility for existing callers.
    now = _now()
    result = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=None,
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='live',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=30),
        last_coverage_telemetry_at=now - timedelta(seconds=30),
        telemetry_kind='coverage',
        last_detection_at=None,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    assert 'live_telemetry_without_proof_chain' not in result['contradiction_flags']
    assert 'live_proof_chain_incomplete' not in result['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# F. UI status: monitoring_status == 'live' only after full proof chain
# ──────────────────────────────────────────────────────────────────────────────

def test_F_no_chain_yields_non_live_status() -> None:
    summary = _build(
        detections_count=0,
        active_alerts_count=0,
        active_incidents_count=0,
        response_actions_count=0,
        evidence_packages_count=0,
    )
    assert summary['monitoring_status'] != 'live'


def test_F_status_transitions_to_live_only_when_full_chain_present() -> None:
    now = _now()
    before_chain = _build(detections_count=0)
    after_chain = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        response_actions_count=1,
        evidence_packages_count=1,
        last_alert_at=now - timedelta(seconds=9),
        last_incident_at=now - timedelta(seconds=8),
        last_response_action_at=now - timedelta(seconds=7),
        last_evidence_export_at=now - timedelta(seconds=6),
        telemetry_kind='coverage',
    )
    assert before_chain['monitoring_status'] != 'live'
    assert after_chain['monitoring_status'] == 'live'


def test_F_proof_chain_guard_flag_in_hard_guard_set() -> None:
    assert 'live_telemetry_without_proof_chain' in HARD_GUARD_FLAGS


# ──────────────────────────────────────────────────────────────────────────────
# G. _ensure_workspace_live_rpc_proof_chain: worker creates canonical + legacy chain
# ──────────────────────────────────────────────────────────────────────────────

class _Row(dict):
    """psycopg3 dict_row rows behave like plain dicts."""


def _row(**kw: Any) -> _Row:
    return _Row(kw)


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _WorkerFakeConn:
    """Fake psycopg connection for _ensure_workspace_live_rpc_proof_chain tests.

    By default every SELECT returns None (no complete chain, no telemetry) unless
    overridden via `overrides`.  All INSERT/UPDATE calls are recorded in `executed`.
    """

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self._overrides = overrides or {}
        self.executed: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = ()) -> _Result:
        q = ' '.join(str(query).split())
        self.executed.append((q, params))
        for keyword, value in self._overrides.items():
            if keyword in q:
                if callable(value):
                    return _Result(value(q, params))
                return _Result(value)
        return _Result(None)


def _make_worker_conn(
    *,
    has_complete_chain: bool = False,
    has_telemetry: bool = True,
    has_monitored_system: bool = True,
) -> _WorkerFakeConn:
    """Build a fake connection for _ensure_workspace_live_rpc_proof_chain."""
    tid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    msid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    def respond(q: str, _params: Any) -> Any:
        # Complete-chain existence check (deduplication guard)
        if 'FROM detections d' in q and 'live_rpc_telemetry_proof' in q and 'detection_event_id IS NOT NULL' in q:
            return _row(id=str(uuid.uuid4())) if has_complete_chain else None
        # Telemetry fetch
        if 'FROM telemetry_events te' in q and 'evidence_source' in q:
            if not has_telemetry:
                return None
            return _row(
                id=str(uuid.uuid4()),
                target_id=tid,
                asset_id=None,
                observed_at=now,
                payload_json={'block_number': 99999, 'chain_id': 1, 'provider_name': 'alchemy'},
            )
        # monitored_systems
        if 'FROM monitored_systems ms' in q:
            return _row(id=msid, asset_id=None) if has_monitored_system else None
        # workspace creator
        if 'SELECT created_by_user_id FROM workspaces' in q:
            return _row(created_by_user_id=uid)
        # asset check
        if 'SELECT id FROM assets' in q:
            return None
        # All INSERTs/UPDATEs return None
        return None

    conn = _WorkerFakeConn(overrides={'': respond})
    return conn


class TestGWorkerProofChainCreation:
    """Worker path (_ensure_workspace_live_rpc_proof_chain) creates the full chain."""

    def _run(self, conn: _WorkerFakeConn) -> dict[str, Any]:
        from services.api.app._proof_chain_worker import _ensure_workspace_live_rpc_proof_chain
        return _ensure_workspace_live_rpc_proof_chain(conn, workspace_id=str(uuid.uuid4()))

    def test_G_returns_created_true_with_all_expected_keys(self) -> None:
        conn = _make_worker_conn()
        result = self._run(conn)
        assert result.get('created') is True
        expected = {
            'detection_event_id', 'detection_id', 'detection_evidence_id',
            'alert_id', 'incident_id', 'incident_timeline_id',
            'response_action_id', 'evidence_id',
            'telemetry_event_id', 'target_id', 'block_number', 'chain_id',
        }
        missing = expected - set(result.keys())
        assert missing == set(), f'Missing keys: {missing}'

    def test_G_inserts_detection_events_row(self) -> None:
        conn = _make_worker_conn()
        self._run(conn)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO detection_events' in q]
        assert inserts, 'Expected INSERT INTO detection_events for canonical path'

    def test_G_alert_insert_includes_detection_event_id(self) -> None:
        conn = _make_worker_conn()
        self._run(conn)
        alert_inserts = [q for q, _ in conn.executed if 'INSERT INTO alerts' in q]
        assert alert_inserts, 'Expected INSERT INTO alerts'
        assert 'detection_event_id' in alert_inserts[0], (
            'Alert INSERT must include detection_event_id column'
        )

    def test_G_alert_insert_includes_legacy_detection_id(self) -> None:
        conn = _make_worker_conn()
        self._run(conn)
        alert_inserts = [q for q, _ in conn.executed if 'INSERT INTO alerts' in q]
        assert 'detection_id' in alert_inserts[0], (
            'Alert INSERT must include legacy detection_id column'
        )

    def test_G_inserts_incident_timeline_row(self) -> None:
        conn = _make_worker_conn()
        self._run(conn)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO incident_timeline' in q]
        assert inserts, 'Expected INSERT INTO incident_timeline'

    def test_G_inserts_detection_evidence_row(self) -> None:
        conn = _make_worker_conn()
        self._run(conn)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO detection_evidence' in q]
        assert inserts, 'Expected INSERT INTO detection_evidence (legacy path)'

    def test_G_inserts_evidence_row(self) -> None:
        conn = _make_worker_conn()
        self._run(conn)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO evidence' in q]
        assert inserts, 'Expected INSERT INTO evidence'

    def test_G_incomplete_chain_not_deduplicated(self) -> None:
        # has_complete_chain=False means the deduplication check returns None,
        # so the worker must NOT short-circuit — it should create a new chain.
        conn = _make_worker_conn(has_complete_chain=False)
        result = self._run(conn)
        assert result.get('reason') != 'deduplicated', (
            'Incomplete chain must not be returned as deduplicated'
        )
        assert result.get('created') is True

    def test_G_complete_chain_is_deduplicated(self) -> None:
        conn = _make_worker_conn(has_complete_chain=True)
        result = self._run(conn)
        assert result.get('created') is False
        assert result.get('reason') == 'deduplicated'

    def test_G_orphan_alerts_archived_before_new_chain_created(self) -> None:
        # When no complete chain exists, UPDATE alerts (orphan archival) must run
        # before the new chain INSERTs.
        conn = _make_worker_conn(has_complete_chain=False)
        self._run(conn)
        update_alerts = [i for i, (q, _) in enumerate(conn.executed) if 'UPDATE alerts' in q]
        insert_detection_events = [i for i, (q, _) in enumerate(conn.executed) if 'INSERT INTO detection_events' in q]
        assert update_alerts, 'Expected UPDATE alerts for orphan archival'
        assert insert_detection_events, 'Expected INSERT INTO detection_events'
        assert update_alerts[0] < insert_detection_events[0], (
            'Orphan alert archival must happen before new chain is inserted'
        )

    def test_G_orphan_incidents_archived_before_new_chain_created(self) -> None:
        conn = _make_worker_conn(has_complete_chain=False)
        self._run(conn)
        update_incidents = [i for i, (q, _) in enumerate(conn.executed) if 'UPDATE incidents' in q and 'SET status' in q and 'FROM alerts a' in q]
        insert_detection_events = [i for i, (q, _) in enumerate(conn.executed) if 'INSERT INTO detection_events' in q]
        assert update_incidents, 'Expected UPDATE incidents for orphan archival'
        assert update_incidents[0] < insert_detection_events[0], (
            'Orphan incident archival must happen before new chain is inserted'
        )

    def test_G_no_telemetry_returns_no_live_telemetry(self) -> None:
        conn = _make_worker_conn(has_telemetry=False)
        result = self._run(conn)
        assert result.get('created') is False
        assert result.get('reason') == 'no_live_telemetry'

    def test_G_detection_event_id_in_return_value(self) -> None:
        conn = _make_worker_conn()
        result = self._run(conn)
        assert result.get('detection_event_id') is not None

    def test_G_incident_timeline_id_in_return_value(self) -> None:
        conn = _make_worker_conn()
        result = self._run(conn)
        assert result.get('incident_timeline_id') is not None
