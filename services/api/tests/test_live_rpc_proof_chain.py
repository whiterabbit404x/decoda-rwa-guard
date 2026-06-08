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

    def fetchall(self) -> list[Any]:
        return [self._row] if self._row is not None else []


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

    run_id = str(uuid.uuid4())

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
        # Resolver step A: simulate tid existing in monitored_targets.id so the
        # resolver succeeds immediately without needing a real upsert.
        if 'FROM monitored_targets' in q or 'INTO monitored_targets' in q:
            return _row(id=tid)
        # monitored_systems
        if 'FROM monitored_systems ms' in q:
            return _row(id=msid, asset_id=None) if has_monitored_system else None
        # workspace creator
        if 'SELECT created_by_user_id FROM workspaces' in q:
            return _row(created_by_user_id=uid)
        # asset check
        if 'SELECT id FROM assets' in q:
            return None
        # monitoring_run_id resolver: step A returns existing row so no INSERT needed
        if 'monitoring_runs' in q:
            return _row(id=run_id)
        # All INSERTs/UPDATEs return None
        return None

    conn = _WorkerFakeConn(overrides={'': respond})
    return conn


class _ReadOnlyResult:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _ReadOnlyConnection:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row
        self.executed: list[str] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _ReadOnlyResult:
        self.executed.append(query)
        return _ReadOnlyResult(self.row)


def _policy_chain_row(**overrides: Any) -> dict[str, Any]:
    row = {
        'telemetry_event_id': '33333333-3333-4333-8333-333333333333',
        'detection_event_id': '44444444-4444-4444-8444-444444444444',
        'detection_id': '55555555-5555-4555-8555-555555555555',
        'alert_id': '66666666-6666-4666-8666-666666666666',
        'incident_id': None,
        'response_action_id': None,
        'evidence_id': '77777777-7777-4777-8777-777777777777',
        'target_id': '22222222-2222-4222-8222-222222222222',
        'target_identifier': '0x1234567890abcdef1234567890abcdef12345678',
        'observed_at': datetime.now(timezone.utc),
        'payload_json': {'provider_receipt': {'request_id': 'rpc-request-001'}},
        'detection_type': 'large_transfer_threshold_exceeded',
        'detection_event_severity': 'high',
        'detection_severity': 'high',
        'raw_evidence_json': {
            'detector_result': {'triggered': True, 'status': 'anomaly_detected'}
        },
        'monitoring_run_id': '88888888-8888-4888-8888-888888888888',
        'tx_hash': '0xabc',
        'block_number': 123,
        'evidence_payload': {
            'target_identifier': '0x1234567890abcdef1234567890abcdef12345678',
            'activity_matched': True,
        },
    }
    row.update(overrides)
    return row


class TestGWorkerProofChainSelection:
    """The worker may select policy-created evidence but must never manufacture it."""

    def _run(self, conn: _ReadOnlyConnection) -> dict[str, Any]:
        from services.api.app._proof_chain_worker import _ensure_workspace_live_rpc_proof_chain
        return _ensure_workspace_live_rpc_proof_chain(
            conn, workspace_id='11111111-1111-4111-8111-111111111111'
        )

    def test_returns_existing_policy_chain_without_creating_records(self) -> None:
        conn = _ReadOnlyConnection(_policy_chain_row())
        result = self._run(conn)
        assert result['created'] is False
        assert result['reason'] == 'existing_policy_detector_chain'
        assert result['incident_id'] is None
        assert result['response_action_id'] is None
        assert result['persisted_linkage']['persisted'] is True
        assert all('INSERT ' not in query.upper() for query in conn.executed)
        assert all('UPDATE ' not in query.upper() for query in conn.executed)

    def test_no_qualifying_chain_is_a_read_only_noop(self) -> None:
        conn = _ReadOnlyConnection(None)
        result = self._run(conn)
        assert result == {'created': False, 'reason': 'no_qualifying_target_detector_chain'}
        assert len(conn.executed) == 1

    def test_missing_provider_receipt_fails_closed(self) -> None:
        conn = _ReadOnlyConnection(_policy_chain_row(payload_json={}))
        result = self._run(conn)
        assert result == {'created': False, 'reason': 'provider_receipt_missing'}

    def test_detector_must_have_triggered(self) -> None:
        conn = _ReadOnlyConnection(_policy_chain_row(raw_evidence_json={
            'detector_result': {'triggered': False, 'status': 'no_match'}
        }))
        result = self._run(conn)
        assert result == {'created': False, 'reason': 'detector_not_triggered'}

    def test_query_excludes_connectivity_and_monitoring_proofs(self) -> None:
        conn = _ReadOnlyConnection(None)
        self._run(conn)
        query = conn.executed[0]
        assert 'live_rpc_block_observed' in query
        assert 'live_rpc_telemetry_proof' in query
        assert "a.alert_type <> 'monitoring_proof'" in query
        assert "NOT IN ('', 'info', 'informational', 'none')" in query
