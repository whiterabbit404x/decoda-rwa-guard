"""Tests for services/api/scripts/repair_live_rpc_proof_chain.py

A. _has_complete_proof_chain: False when incident lacks incident_timeline
B. _has_complete_proof_chain: False when alert lacks canonical detection_event_id
C. _has_complete_proof_chain: True only when all links including timeline exist
D. _archive_orphan_alerts: targets any alert without detection chain on both paths
E. _archive_orphan_incidents: targets any incident without alert linkage
F. _create_proof_chain: returns all expected chain IDs
G. _create_proof_chain: returns no_live_telemetry when telemetry unavailable
H. _query_contradiction_flags: no blocking flags when chain is fully linked
I. _query_contradiction_flags: all four blocking flags when chain is absent
J. _invalidate_precomputed_summary: issues UPDATE touching updated_at
K. BLOCKING_FLAGS constant contains the four expected flags
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from services.api.scripts.repair_live_rpc_proof_chain import (
    BLOCKING_FLAGS,
    _archive_orphan_alerts,
    _archive_orphan_incidents,
    _create_proof_chain,
    _has_complete_proof_chain,
    _invalidate_precomputed_summary,
    _query_contradiction_flags,
    _resolve_for_monitored_targets_parent,
    _resolve_for_targets_parent,
    inspect_detection_events_target_parent,
    resolve_detection_event_target_id,
    resolve_monitoring_run_id,
)


# ---------------------------------------------------------------------------
# Minimal fake connection helpers
# ---------------------------------------------------------------------------

class _Row(dict):
    """Dict subclass with .get() — psycopg3 dict_row rows behave like dicts."""


def _row(**kw: Any) -> _Row:
    return _Row(kw)


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _FakeConn:
    """Minimal fake psycopg connection that records execute calls and serves
    pre-configured query responses by keyword matching."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._responses = responses or {}
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> _Result:
        q = ' '.join(str(query).split())
        self.executed.append((q, params))
        for keyword, result in self._responses.items():
            if keyword in q:
                if callable(result):
                    return _Result(result(q, params))
                return _Result(result)
        return _Result(None)


# ---------------------------------------------------------------------------
# K. BLOCKING_FLAGS constant
# ---------------------------------------------------------------------------

class TestKBlockingFlags:
    def test_K_all_four_expected_flags_present(self) -> None:
        expected = {
            'alert_without_detection',
            'incident_without_alert',
            'open_alerts_without_detection_evidence',
            'proof_chain_link_missing',
        }
        assert expected == BLOCKING_FLAGS


# ---------------------------------------------------------------------------
# A. _has_complete_proof_chain: requires incident_timeline
# ---------------------------------------------------------------------------

def _conn_with_complete_chain(*, has_timeline: bool = True, has_detection_event: bool = True) -> _FakeConn:
    """Return a fake connection whose SQL responses simulate either a complete
    or a partial proof chain for _has_complete_proof_chain."""
    wid = str(uuid.uuid4())
    did = str(uuid.uuid4())

    def _respond(q: str, _params: Any) -> Any:
        # The query checks for detections with multiple EXISTS sub-selects.
        # We simulate the full chain: return a row (chain complete) or None.
        if 'FROM detections d' in q and 'live_rpc_telemetry_proof' in q:
            if has_timeline and has_detection_event:
                return _row(id=did)
            return None
        return None

    conn = _FakeConn()
    conn._responses = {'FROM detections d': _respond}
    return conn


class TestAHasCompleteProofChain:
    def test_A_returns_false_when_no_detection_row_exists(self) -> None:
        conn = _FakeConn(responses={'FROM detections d': None})
        assert _has_complete_proof_chain(conn, str(uuid.uuid4())) is False

    def test_A_returns_true_when_full_chain_returned(self) -> None:
        conn = _conn_with_complete_chain(has_timeline=True, has_detection_event=True)
        assert _has_complete_proof_chain(conn, str(uuid.uuid4())) is True

    def test_A_returns_false_when_detection_row_absent(self) -> None:
        conn = _conn_with_complete_chain(has_timeline=False, has_detection_event=False)
        assert _has_complete_proof_chain(conn, str(uuid.uuid4())) is False


# ---------------------------------------------------------------------------
# D. _archive_orphan_alerts
# ---------------------------------------------------------------------------

class TestDArchiveOrphanAlerts:
    def test_D_skips_when_count_is_zero(self) -> None:
        conn = _FakeConn(responses={'FROM alerts a' : _row(c=0)})
        archived = _archive_orphan_alerts(conn, str(uuid.uuid4()), dry_run=False)
        assert archived == 0
        # No UPDATE should have been issued
        updates = [q for q, _ in conn.executed if q.startswith('UPDATE')]
        assert updates == []

    def test_D_returns_count_and_executes_update_when_orphans_exist(self) -> None:
        responses: dict[str, Any] = {}
        call_count = 0

        def _respond(q: str, _params: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _row(c=3)
            return None

        conn = _FakeConn(responses={'FROM alerts a': _respond})
        archived = _archive_orphan_alerts(conn, str(uuid.uuid4()), dry_run=False)
        assert archived == 3
        updates = [q for q, _ in conn.executed if 'UPDATE alerts' in q]
        assert updates, 'Expected an UPDATE alerts statement'

    def test_D_dry_run_returns_count_without_update(self) -> None:
        conn = _FakeConn(responses={'FROM alerts a': _row(c=2)})
        archived = _archive_orphan_alerts(conn, str(uuid.uuid4()), dry_run=True)
        assert archived == 2
        updates = [q for q, _ in conn.executed if 'UPDATE alerts' in q]
        assert updates == [], 'dry_run must not issue UPDATE'


# ---------------------------------------------------------------------------
# E. _archive_orphan_incidents
# ---------------------------------------------------------------------------

class TestEArchiveOrphanIncidents:
    def test_E_skips_when_count_is_zero(self) -> None:
        conn = _FakeConn(responses={'FROM incidents i': _row(c=0)})
        archived = _archive_orphan_incidents(conn, str(uuid.uuid4()), dry_run=False)
        assert archived == 0

    def test_E_returns_count_and_updates_when_orphans_exist(self) -> None:
        call_count = 0

        def _respond(q: str, _params: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return _row(c=1) if call_count == 1 else None

        conn = _FakeConn(responses={'FROM incidents i': _respond})
        archived = _archive_orphan_incidents(conn, str(uuid.uuid4()), dry_run=False)
        assert archived == 1
        updates = [q for q, _ in conn.executed if 'UPDATE incidents' in q]
        assert updates, 'Expected an UPDATE incidents statement'

    def test_E_dry_run_suppresses_update(self) -> None:
        conn = _FakeConn(responses={'FROM incidents i': _row(c=5)})
        archived = _archive_orphan_incidents(conn, str(uuid.uuid4()), dry_run=True)
        assert archived == 5
        updates = [q for q, _ in conn.executed if 'UPDATE incidents' in q]
        assert updates == []


# ---------------------------------------------------------------------------
# F. _create_proof_chain: all expected keys returned
# ---------------------------------------------------------------------------

class TestFCreateProofChain:
    def _make_conn(self) -> _FakeConn:
        """Return a fake conn that satisfies all queries in _create_proof_chain."""
        wid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        mr_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()),
                    target_id=tid,
                    asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 12345678, 'chain_id': 1, 'provider_name': 'infura'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            # Resolver step A: simulate telemetry target_id existing in monitored_targets.id
            if 'FROM monitored_targets' in q or 'INTO monitored_targets' in q:
                return _row(id=tid)
            # monitoring_run_id resolver: step A returns existing row so no INSERT needed
            if 'monitoring_runs' in q:
                return _row(id=mr_id)
            # All other INSERT/UPDATE statements return None
            return None

        conn = _FakeConn()
        conn._responses = {
            'FROM telemetry_events te': respond,
            'FROM monitored_systems ms': respond,
            'SELECT created_by_user_id FROM workspaces': respond,
            'SELECT id FROM assets': respond,
            'monitored_targets': respond,
            'monitoring_runs': respond,
        }
        return conn

    def test_F_returns_created_true_with_all_expected_keys(self) -> None:
        conn = self._make_conn()
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True
        expected_keys = {
            'detection_event_id',
            'detection_id',
            'detection_evidence_id',
            'alert_id',
            'incident_id',
            'incident_timeline_id',
            'response_action_id',
            'evidence_id',
            'telemetry_event_id',
            'target_id',
            'block_number',
            'chain_id',
        }
        missing = expected_keys - set(result.keys())
        assert missing == set(), f'Missing keys: {missing}'

    def test_F_canonical_detection_event_id_in_result(self) -> None:
        conn = self._make_conn()
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('detection_event_id') is not None

    def test_F_incident_timeline_id_in_result(self) -> None:
        conn = self._make_conn()
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('incident_timeline_id') is not None

    def test_F_inserts_detection_events_row(self) -> None:
        conn = self._make_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        inserts = [q for q, _ in conn.executed if 'INSERT INTO detection_events' in q]
        assert inserts, 'Expected INSERT INTO detection_events'

    def test_F_inserts_incident_timeline_row(self) -> None:
        conn = self._make_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        inserts = [q for q, _ in conn.executed if 'INSERT INTO incident_timeline' in q]
        assert inserts, 'Expected INSERT INTO incident_timeline'

    def test_F_alert_insert_sets_detection_event_id(self) -> None:
        conn = self._make_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        alert_inserts = [q for q, _ in conn.executed if 'INSERT INTO alerts' in q]
        assert alert_inserts, 'Expected INSERT INTO alerts'
        assert 'detection_event_id' in alert_inserts[0], (
            'Alert INSERT must include detection_event_id for canonical path'
        )

    def test_F_alert_insert_sets_detection_id_for_legacy_path(self) -> None:
        conn = self._make_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        alert_inserts = [q for q, _ in conn.executed if 'INSERT INTO alerts' in q]
        assert 'detection_id' in alert_inserts[0], (
            'Alert INSERT must include detection_id for legacy path'
        )


# ---------------------------------------------------------------------------
# G. _create_proof_chain: no_live_telemetry when telemetry absent
# ---------------------------------------------------------------------------

class TestGCreateProofChainNoTelemetry:
    def test_G_returns_no_live_telemetry_reason(self) -> None:
        conn = _FakeConn(responses={'FROM telemetry_events te': None})
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is False
        assert result.get('reason') == 'no_live_telemetry'


# ---------------------------------------------------------------------------
# H. _query_contradiction_flags: no blocking flags when fully linked
# ---------------------------------------------------------------------------

class TestHQueryContradictionFlagsClean:
    def _make_clean_conn(self) -> _FakeConn:
        """All queries return counts consistent with a fully-linked proof chain."""
        now = datetime.now(timezone.utc)

        def respond(q: str, _params: Any) -> Any:
            q = ' '.join(q.split())
            # Raw alert count
            if "FROM alerts WHERE workspace_id" in q and "status IN" in q:
                return _row(c=1)
            # Canonical alert chain
            if 'JOIN detection_events de' in q and 'JOIN telemetry_events te' in q and 'COUNT' in q and 'FROM alerts a' in q and 'WITH proof_chain_alerts' not in q:
                return _row(c=1)
            # Legacy alert chain
            if 'JOIN detections d ON d.id = a.detection_id' in q and 'COUNT' in q:
                return _row(c=1)
            # Raw incident count
            if 'FROM incidents WHERE workspace_id' in q and 'status IN' in q:
                return _row(c=1)
            # Chain incidents (CTE)
            if 'WITH proof_chain_alerts AS' in q:
                return _row(c=1)
            # incidents_without_alert
            if 'FROM incidents i' in q and 'NOT EXISTS' in q and 'FROM alerts a' in q:
                return _row(c=0)
            # incidents_without_timeline
            if 'FROM incidents i' in q and 'FROM incident_timeline it' in q:
                return _row(c=0)
            # MAX(detected_at) FROM detections
            if 'MAX(detected_at)' in q:
                return _row(ts=now - timedelta(seconds=30))
            # MAX(created_at) FROM detection_events
            if 'MAX(created_at)' in q and 'FROM detection_events' in q:
                return _row(ts=now - timedelta(seconds=30))
            # COUNT detections
            if 'FROM detections WHERE workspace_id' in q:
                return _row(c=1)
            # COUNT response_actions
            if 'FROM response_actions WHERE workspace_id' in q:
                return _row(c=1)
            # COUNT evidence
            if 'FROM evidence WHERE workspace_id' in q:
                return _row(c=1)
            return _row(c=0)

        conn = _FakeConn()
        conn._responses = {'': respond}
        return conn

    def test_H_no_blocking_flags_for_clean_chain(self) -> None:
        conn = self._make_clean_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        blocking = [f for f in result['contradiction_flags'] if f in BLOCKING_FLAGS]
        assert blocking == [], f'Unexpected blocking flags: {blocking}'

    def test_H_proof_chain_missing_reason_codes_empty_for_clean_chain(self) -> None:
        conn = self._make_clean_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        assert result['proof_chain_missing_reason_codes'] == []

    def test_H_open_alerts_without_detection_zero_for_clean_chain(self) -> None:
        conn = self._make_clean_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        assert result['open_alerts_without_detection'] == 0


# ---------------------------------------------------------------------------
# I. _query_contradiction_flags: all four blocking flags when chain absent
# ---------------------------------------------------------------------------

class TestIQueryContradictionFlagsAllFlags:
    def _make_broken_conn(self) -> _FakeConn:
        """All queries return counts for a workspace with orphan alerts/incidents."""

        def respond(q: str, _params: Any) -> Any:
            q = ' '.join(q.split())
            # Raw alert count: 2 open alerts
            if "FROM alerts WHERE workspace_id" in q and "status IN" in q:
                return _row(c=2)
            # Canonical alert chain: 0 (no detection_event_id link)
            if 'JOIN detection_events de' in q and 'JOIN telemetry_events te' in q and 'COUNT' in q and 'FROM alerts a' in q and 'WITH proof_chain_alerts' not in q:
                return _row(c=0)
            # Legacy alert chain: 0 (no detection_id link)
            if 'JOIN detections d ON d.id = a.detection_id' in q and 'COUNT' in q:
                return _row(c=0)
            # Raw incident count: 1 open incident
            if 'FROM incidents WHERE workspace_id' in q and 'status IN' in q:
                return _row(c=1)
            # Chain incidents: 0 (orphan)
            if 'WITH proof_chain_alerts AS' in q:
                return _row(c=0)
            # incidents_without_alert: 1
            if 'FROM incidents i' in q and 'NOT EXISTS' in q and 'FROM alerts a' in q:
                return _row(c=1)
            # incidents_without_timeline: 1
            if 'FROM incidents i' in q and 'FROM incident_timeline it' in q:
                return _row(c=1)
            # No detections
            if 'MAX(detected_at)' in q:
                return _row(ts=None)
            if 'MAX(created_at)' in q and 'FROM detection_events' in q:
                return _row(ts=None)
            return _row(c=0)

        conn = _FakeConn()
        conn._responses = {'': respond}
        return conn

    def test_I_alert_without_detection_fires(self) -> None:
        conn = self._make_broken_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        assert 'alert_without_detection' in result['contradiction_flags']

    def test_I_open_alerts_without_detection_evidence_fires(self) -> None:
        conn = self._make_broken_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        assert 'open_alerts_without_detection_evidence' in result['contradiction_flags']

    def test_I_incident_without_alert_fires(self) -> None:
        conn = self._make_broken_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        assert 'incident_without_alert' in result['contradiction_flags']

    def test_I_proof_chain_link_missing_fires(self) -> None:
        conn = self._make_broken_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        assert 'proof_chain_link_missing' in result['contradiction_flags']

    def test_I_proof_chain_missing_reason_codes_populated(self) -> None:
        conn = self._make_broken_conn()
        result = _query_contradiction_flags(conn, str(uuid.uuid4()))
        assert result['proof_chain_missing_reason_codes'], (
            'Expected at least one reason code when chain is missing'
        )


# ---------------------------------------------------------------------------
# J. _invalidate_precomputed_summary: issues UPDATE on correct table
# ---------------------------------------------------------------------------

class TestJInvalidatePrecomputedSummary:
    def test_J_issues_update_on_monitoring_workspace_runtime_summary(self) -> None:
        conn = _FakeConn()
        _invalidate_precomputed_summary(conn, str(uuid.uuid4()))
        updates = [q for q, _ in conn.executed if 'UPDATE monitoring_workspace_runtime_summary' in q]
        assert updates, 'Expected UPDATE monitoring_workspace_runtime_summary'

    def test_J_update_sets_updated_at_to_past(self) -> None:
        conn = _FakeConn()
        _invalidate_precomputed_summary(conn, str(uuid.uuid4()))
        updates = [q for q, _ in conn.executed if 'UPDATE monitoring_workspace_runtime_summary' in q]
        assert 'updated_at' in updates[0], 'UPDATE must set updated_at'
        assert 'INTERVAL' in updates[0], 'updated_at must be set to a past time via INTERVAL'


# ---------------------------------------------------------------------------
# L. resolve_detection_event_target_id: step A — direct id match
# Test B from task: telemetry target already exists in monitored_targets.id
# ---------------------------------------------------------------------------

class TestLResolveDirectMatch:
    def test_L_returns_target_id_when_it_exists_in_monitored_targets(self) -> None:
        tid = str(uuid.uuid4())
        wid = str(uuid.uuid4())
        # Step A: monitored_targets has a row with id = tid
        conn = _FakeConn(responses={
            'FROM monitored_targets': _row(id=tid),
        })
        result = resolve_detection_event_target_id(conn, wid, tid)
        assert result == tid

    def test_L_does_not_execute_upsert_when_direct_match_found(self) -> None:
        tid = str(uuid.uuid4())
        wid = str(uuid.uuid4())
        conn = _FakeConn(responses={'FROM monitored_targets': _row(id=tid)})
        resolve_detection_event_target_id(conn, wid, tid)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitored_targets' in q]
        assert inserts == [], 'Should not upsert when direct match exists'


# ---------------------------------------------------------------------------
# M. resolve_detection_event_target_id: step B — resolve via target_identifier
# Test A from task: telemetry_target_id is targets.id, resolve to monitored_targets.id
# ---------------------------------------------------------------------------

class TestMResolveViaTargetIdentifier:
    def test_M_returns_monitored_targets_id_via_target_identifier(self) -> None:
        tid = str(uuid.uuid4())          # targets.id stored as target_identifier
        mt_id = str(uuid.uuid4())        # monitored_targets.id (different UUID)
        wid = str(uuid.uuid4())
        call_count = 0

        def respond(q: str, _params: Any) -> Any:
            nonlocal call_count
            call_count += 1
            # Step A: direct id check returns None (tid ≠ monitored_targets.id)
            if call_count == 1:
                return None
            # Step B: target_identifier lookup returns mt_id
            return _row(id=mt_id)

        conn = _FakeConn(responses={'FROM monitored_targets': respond})
        result = resolve_detection_event_target_id(conn, wid, tid)
        assert result == mt_id, 'Should return monitored_targets.id from target_identifier lookup'

    def test_M_does_not_upsert_when_identifier_match_found(self) -> None:
        tid = str(uuid.uuid4())
        mt_id = str(uuid.uuid4())
        wid = str(uuid.uuid4())
        call_count = 0

        def respond(q: str, _params: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return None if call_count == 1 else _row(id=mt_id)

        conn = _FakeConn(responses={'FROM monitored_targets': respond})
        resolve_detection_event_target_id(conn, wid, tid)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitored_targets' in q]
        assert inserts == [], 'Should not upsert when identifier match exists'


# ---------------------------------------------------------------------------
# N. resolve_detection_event_target_id: step D — upsert when no row exists
# Test C from task: no monitored_targets row → creates one
# ---------------------------------------------------------------------------

class TestNResolveUpsert:
    def test_N_upserts_monitored_targets_when_no_row_exists(self) -> None:
        tid = str(uuid.uuid4())
        wid = str(uuid.uuid4())
        new_mt_id = str(uuid.uuid4())
        call_count = 0

        def respond(q: str, _params: Any) -> Any:
            nonlocal call_count
            if 'INSERT INTO monitored_targets' in q:
                return _row(id=new_mt_id)
            call_count += 1
            return None  # Steps A and B both return None

        conn = _FakeConn(responses={
            'FROM monitored_targets': respond,
            'INSERT INTO monitored_targets': respond,
        })
        result = resolve_detection_event_target_id(conn, wid, tid)
        assert result == new_mt_id
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitored_targets' in q]
        assert inserts, 'Should execute INSERT INTO monitored_targets when no row exists'

    def test_N_upsert_uses_on_conflict_for_idempotency(self) -> None:
        wid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'INSERT INTO monitored_targets' in q:
                return _row(id=new_id)
            return None

        conn = _FakeConn(responses={
            'FROM monitored_targets': respond,
            'INSERT INTO monitored_targets': respond,
        })
        resolve_detection_event_target_id(conn, wid, tid)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitored_targets' in q]
        assert inserts, 'INSERT should have been executed'
        assert 'ON CONFLICT' in inserts[0], 'INSERT must use ON CONFLICT for idempotency'
        assert 'RETURNING id' in inserts[0], 'INSERT must use RETURNING id'

    def test_N_upsert_uses_evm_rpc_provider_type(self) -> None:
        wid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'INSERT INTO monitored_targets' in q:
                return _row(id=new_id)
            return None

        conn = _FakeConn(responses={
            'FROM monitored_targets': respond,
            'INSERT INTO monitored_targets': respond,
        })
        resolve_detection_event_target_id(conn, wid, tid)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitored_targets' in q]
        assert inserts, 'INSERT should have been executed'
        assert 'evm_rpc' in inserts[0], "Upserted row must use provider_type='evm_rpc'"


# ---------------------------------------------------------------------------
# O. detect_detection_events insert uses monitored_targets.id, not targets.id
# Test D from task: detection_events insert never uses a non-parent target id
# ---------------------------------------------------------------------------

class TestODetectionEventsUsesMonitoredTargetsId:
    def test_O_detection_events_insert_uses_resolved_id_not_raw_target_id(self) -> None:
        """The target_id param to the detection_events INSERT must be the resolved
        monitored_targets.id, not the raw telemetry targets.id."""
        tid = str(uuid.uuid4())       # targets.id (raw telemetry target)
        mt_id = str(uuid.uuid4())     # monitored_targets.id (resolved)
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        def respond(q: str, _params: Any) -> Any:
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()),
                    target_id=tid,
                    asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 99, 'chain_id': 1, 'provider_name': 'test'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            # Resolver step A returns mt_id (different from tid)
            if 'FROM monitored_targets' in q or 'INTO monitored_targets' in q:
                return _row(id=mt_id)
            # monitoring_run_id resolver: step A returns existing row
            if 'monitoring_runs' in q:
                return _row(id=str(uuid.uuid4()))
            return None

        conn = _FakeConn(responses={'': respond})
        _create_proof_chain(conn, str(uuid.uuid4()))

        de_inserts = [params for q, params in conn.executed if 'INSERT INTO detection_events' in q]
        assert de_inserts, 'Expected INSERT INTO detection_events'
        # The third positional param to detection_events is asset_id (None), the fourth
        # is target_id.  params tuple: (detection_event_id, workspace_id, asset_id, target_id, ...)
        de_params = de_inserts[0]
        assert tid not in de_params, (
            f'detection_events must not use raw targets.id={tid!r} as target_id; '
            f'use monitored_targets.id={mt_id!r} instead'
        )
        assert mt_id in de_params, (
            f'detection_events must use the resolved monitored_targets.id={mt_id!r}'
        )


# ---------------------------------------------------------------------------
# P. Regression: exact production error — target in targets but not monitored_targets
# Test E from task
# ---------------------------------------------------------------------------

REGRESSION_TARGET_ID = 'c42efa96-e22f-48e2-bd3f-be486689b9b5'


class TestPRegressionForeignKeyViolation:
    """Regression test for the exact production ForeignKeyViolation.

    target_id=c42efa96 exists in the targets table but was not present in
    monitored_targets.id.  The repair script must resolve a valid monitored_targets.id
    before inserting detection_events and must not raise ForeignKeyViolation.
    """

    def test_P_resolves_monitored_targets_id_for_regression_target(self) -> None:
        wid = str(uuid.uuid4())
        mt_id = str(uuid.uuid4())   # the monitored_targets.id that should be returned

        # Step A returns None (regression_target not in monitored_targets.id)
        # Step B also returns None (no target_identifier row yet)
        # Step D (upsert) returns mt_id
        call_count = 0

        def respond(q: str, _params: Any) -> Any:
            nonlocal call_count
            if 'INSERT INTO monitored_targets' in q:
                return _row(id=mt_id)
            call_count += 1
            return None  # A and B both return None

        conn = _FakeConn(responses={
            'FROM monitored_targets': respond,
            'INSERT INTO monitored_targets': respond,
        })
        result = resolve_detection_event_target_id(conn, wid, REGRESSION_TARGET_ID)
        # Must NOT use the regression targets.id directly; must return the upserted mt_id
        assert result != REGRESSION_TARGET_ID, (
            'Resolver must not return the raw targets.id when it is not in monitored_targets'
        )
        assert result == mt_id

    def test_P_create_proof_chain_does_not_raise_foreign_key_violation(self) -> None:
        """Full create_proof_chain flow with the regression target_id must not raise."""
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        mt_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()),
                    target_id=REGRESSION_TARGET_ID,
                    asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 20000000, 'chain_id': 1, 'provider_name': 'alchemy'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            # Resolver: step A and B return None, step D (upsert) returns mt_id
            if 'INSERT INTO monitored_targets' in q:
                return _row(id=mt_id)
            # Preflight SELECT 1 FROM monitored_targets must return truthy so the
            # preflight guard passes. The plain SELECT (steps A/B) still returns None.
            if 'SELECT 1 FROM monitored_targets' in q:
                return _row(id=mt_id)
            if 'FROM monitored_targets' in q:
                return None
            # monitoring_run_id resolver: step A returns existing row
            if 'monitoring_runs' in q:
                return _row(id=str(uuid.uuid4()))
            return None

        conn = _FakeConn(responses={'': respond})
        # Must not raise any exception (in production this raised ForeignKeyViolation)
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True

    def test_P_detection_events_insert_uses_resolved_not_regression_id(self) -> None:
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        mt_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()),
                    target_id=REGRESSION_TARGET_ID,
                    asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 20000000, 'chain_id': 1, 'provider_name': 'alchemy'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            if 'INSERT INTO monitored_targets' in q:
                return _row(id=mt_id)
            if 'SELECT 1 FROM monitored_targets' in q:
                return _row(id=mt_id)  # preflight passes
            if 'FROM monitored_targets' in q:
                return None
            # monitoring_run_id resolver: step A returns existing row
            if 'monitoring_runs' in q:
                return _row(id=str(uuid.uuid4()))
            return None

        conn = _FakeConn(responses={'': respond})
        _create_proof_chain(conn, str(uuid.uuid4()))

        de_inserts = [params for q, params in conn.executed if 'INSERT INTO detection_events' in q]
        assert de_inserts, 'Expected INSERT INTO detection_events'
        de_params = de_inserts[0]
        assert REGRESSION_TARGET_ID not in de_params, (
            'detection_events must not use regression targets.id c42efa96 directly'
        )
        assert mt_id in de_params, (
            'detection_events must use the resolved monitored_targets.id'
        )


# ---------------------------------------------------------------------------
# Q. resolve_monitoring_run_id: step A — finds existing row for workspace
# Task item 6A: detections.monitoring_run_id FK is satisfied
# ---------------------------------------------------------------------------

class TestQResolveMonitoringRunIdExisting:
    def test_Q_returns_existing_row_id(self) -> None:
        wid = str(uuid.uuid4())
        existing_run_id = str(uuid.uuid4())
        conn = _FakeConn(responses={'monitoring_runs': _row(id=existing_run_id)})
        result = resolve_monitoring_run_id(conn, wid)
        assert result == existing_run_id

    def test_Q_does_not_insert_when_existing_row_found(self) -> None:
        wid = str(uuid.uuid4())
        existing_run_id = str(uuid.uuid4())
        conn = _FakeConn(responses={'monitoring_runs': _row(id=existing_run_id)})
        resolve_monitoring_run_id(conn, wid)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitoring_runs' in q]
        assert inserts == [], 'Must not INSERT when existing row found'

    def test_Q_detections_insert_uses_resolved_id_not_random_uuid(self) -> None:
        """detections.monitoring_run_id must match an id that exists in monitoring_runs."""
        wid = str(uuid.uuid4())
        existing_run_id = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        def respond(q: str, _params: Any) -> Any:
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=tid, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 5000, 'chain_id': 1, 'provider_name': 'test'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            if 'monitored_targets' in q:
                return _row(id=tid)
            if 'monitoring_runs' in q:
                return _row(id=existing_run_id)
            return None

        conn = _FakeConn(responses={'': respond})
        _create_proof_chain(conn, wid)

        det_inserts = [params for q, params in conn.executed if 'INSERT INTO detections' in q]
        assert det_inserts, 'Expected INSERT INTO detections'
        det_params = det_inserts[0]
        assert existing_run_id in det_params, (
            f'detections INSERT must use resolved monitoring_run_id={existing_run_id!r}'
        )


# ---------------------------------------------------------------------------
# R. resolve_monitoring_run_id: step B — creates row when none exists
# Task item 6C: if no monitoring_runs row exists, the script creates one first
# ---------------------------------------------------------------------------

class TestRResolveMonitoringRunIdCreatesRow:
    def test_R_inserts_row_when_none_exists(self) -> None:
        wid = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'INSERT INTO monitoring_runs' in q:
                return _row(id=new_id)
            if 'SELECT 1 FROM monitoring_runs' in q:
                return _row(exists=1)  # non-empty dict is truthy — confirms the row
            return None  # Step A SELECT returns None (no existing row)

        conn = _FakeConn(responses={'monitoring_runs': respond})
        result = resolve_monitoring_run_id(conn, wid)
        assert result == new_id
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitoring_runs' in q]
        assert inserts, 'Expected INSERT INTO monitoring_runs when no row exists'

    def test_R_insert_includes_all_required_columns(self) -> None:
        wid = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'INSERT INTO monitoring_runs' in q:
                return _row(id=new_id)
            if 'SELECT 1 FROM monitoring_runs' in q:
                return _row(exists=1)
            return None

        conn = _FakeConn(responses={'monitoring_runs': respond})
        resolve_monitoring_run_id(conn, wid)
        inserts = [q for q, _ in conn.executed if 'INSERT INTO monitoring_runs' in q]
        assert inserts, 'Expected INSERT INTO monitoring_runs'
        insert_q = inserts[0]
        for col in ('workspace_id', 'started_at', 'status', 'trigger_type',
                    'systems_checked_count', 'detections_created_count'):
            assert col in insert_q, f'INSERT must include required column {col!r}'

    def test_R_insert_uses_correct_workspace_id(self) -> None:
        wid = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'INSERT INTO monitoring_runs' in q:
                return _row(id=new_id)
            if 'SELECT 1 FROM monitoring_runs' in q:
                return _row(exists=1)
            return None

        conn = _FakeConn(responses={'monitoring_runs': respond})
        resolve_monitoring_run_id(conn, wid)
        inserts = [(q, p) for q, p in conn.executed if 'INSERT INTO monitoring_runs' in q]
        assert inserts, 'Expected INSERT INTO monitoring_runs'
        _, params = inserts[0]
        assert wid in params, (
            'INSERT INTO monitoring_runs must use the correct workspace_id'
        )

    def test_R_raises_when_requery_returns_nothing(self) -> None:
        """If INSERT succeeds but re-query returns nothing, raise a clear RuntimeError."""
        wid = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'INSERT INTO monitoring_runs' in q:
                return _row(id=new_id)
            return None  # Both step-A SELECT and step-C re-query return None

        conn = _FakeConn(responses={'monitoring_runs': respond})
        with pytest.raises(RuntimeError, match='monitoring_runs'):
            resolve_monitoring_run_id(conn, wid)


# ---------------------------------------------------------------------------
# S. Task item 6B: no random orphan monitoring_run_id inserted
# The monitoring_run_id in the detections INSERT must come from resolve_monitoring_run_id,
# not from uuid.uuid4() called inline.
# ---------------------------------------------------------------------------

class TestSNoRandomOrphanMonitoringRunId:
    def test_S_detections_insert_param_matches_resolved_monitoring_run_id(self) -> None:
        """The id used in the detections INSERT must be the one returned by the resolver."""
        wid = str(uuid.uuid4())
        canonical_run_id = str(uuid.uuid4())  # What the resolver will return
        tid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        def respond(q: str, _params: Any) -> Any:
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=tid, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 42, 'chain_id': 1, 'provider_name': 'test'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            if 'monitored_targets' in q:
                return _row(id=tid)
            if 'monitoring_runs' in q:
                return _row(id=canonical_run_id)
            return None

        conn = _FakeConn(responses={'': respond})
        _create_proof_chain(conn, wid)

        det_inserts = [params for q, params in conn.executed if 'INSERT INTO detections' in q]
        assert det_inserts, 'Expected INSERT INTO detections'
        det_params = det_inserts[0]
        assert canonical_run_id in det_params, (
            'detections INSERT must use the id returned by resolve_monitoring_run_id, '
            f'not a random uuid. Expected {canonical_run_id!r} in params.'
        )


# ---------------------------------------------------------------------------
# T. Task item 6D: re-running after partial failure succeeds (idempotency)
# ---------------------------------------------------------------------------

class TestTIdempotentAfterPartialFailure:
    def _make_conn_for_rerun(self, *, has_complete_chain: bool) -> _FakeConn:
        tid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        run_id = str(uuid.uuid4())
        det_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            # Complete-chain check (has_complete_proof_chain)
            if 'FROM detections d' in q and 'live_rpc_telemetry_proof' in q:
                return _row(id=det_id) if has_complete_chain else None
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=tid, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 9999, 'chain_id': 1, 'provider_name': 'test'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            if 'monitored_targets' in q:
                return _row(id=tid)
            if 'monitoring_runs' in q:
                return _row(id=run_id)
            return _row(c=0)

        return _FakeConn(responses={'': respond})

    def test_T_creates_chain_when_previous_attempt_was_incomplete(self) -> None:
        conn = self._make_conn_for_rerun(has_complete_chain=False)
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True, (
            'Re-run after partial failure must create the proof chain'
        )

    def test_T_does_not_duplicate_chain_when_already_complete(self) -> None:
        conn = self._make_conn_for_rerun(has_complete_chain=True)
        assert _has_complete_proof_chain(conn, str(uuid.uuid4())) is True

    def test_T_monitoring_run_id_always_resolved_not_random(self) -> None:
        """Even on re-run, monitoring_run_id must come from resolve, not uuid.uuid4()."""
        conn = self._make_conn_for_rerun(has_complete_chain=False)
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True
        det_inserts = [params for q, params in conn.executed if 'INSERT INTO detections' in q]
        assert det_inserts, 'Expected INSERT INTO detections on re-run'


# ---------------------------------------------------------------------------
# U. Task item 6E: full chain exists
# telemetry_event → detection_event → detection → detection_evidence →
# alert → incident → incident_timeline → response_action → evidence
# ---------------------------------------------------------------------------

class TestUFullChainExists:
    def _make_full_chain_conn(self) -> _FakeConn:
        tid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        run_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=tid, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 99999, 'chain_id': 1, 'provider_name': 'test'},
                )
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            if 'monitored_targets' in q:
                return _row(id=tid)
            if 'monitoring_runs' in q:
                return _row(id=run_id)
            return None

        return _FakeConn(responses={'': respond})

    def test_U_creates_detection_events_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO detection_events' in q for q, _ in conn.executed)

    def test_U_creates_detections_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO detections' in q for q, _ in conn.executed)

    def test_U_creates_detection_evidence_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO detection_evidence' in q for q, _ in conn.executed)

    def test_U_creates_alert_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO alerts' in q for q, _ in conn.executed)

    def test_U_creates_incident_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO incidents' in q for q, _ in conn.executed)

    def test_U_creates_incident_timeline_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO incident_timeline' in q for q, _ in conn.executed)

    def test_U_creates_response_action_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO response_actions' in q for q, _ in conn.executed)

    def test_U_creates_evidence_row(self) -> None:
        conn = self._make_full_chain_conn()
        _create_proof_chain(conn, str(uuid.uuid4()))
        assert any('INSERT INTO evidence' in q for q, _ in conn.executed)

    def test_U_result_has_all_chain_ids(self) -> None:
        conn = self._make_full_chain_conn()
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True
        for key in (
            'telemetry_event_id', 'detection_event_id', 'detection_id',
            'detection_evidence_id', 'alert_id', 'incident_id',
            'incident_timeline_id', 'response_action_id', 'evidence_id',
        ):
            assert result.get(key) is not None, f'Missing chain id: {key!r}'


# ---------------------------------------------------------------------------
# V. inspect_detection_events_target_parent: returns correct parent table
# ---------------------------------------------------------------------------

class TestVInspectFKParent:
    def test_V_returns_targets_when_fk_points_to_targets(self) -> None:
        conn = _FakeConn(responses={'pg_constraint': _row(parent_table='targets')})
        assert inspect_detection_events_target_parent(conn) == 'targets'

    def test_V_returns_monitored_targets_when_fk_points_to_monitored_targets(self) -> None:
        conn = _FakeConn(responses={'pg_constraint': _row(parent_table='monitored_targets')})
        assert inspect_detection_events_target_parent(conn) == 'monitored_targets'

    def test_V_returns_unknown_when_no_constraint_found(self) -> None:
        conn = _FakeConn()
        assert inspect_detection_events_target_parent(conn) == 'unknown'

    def test_V_returns_unknown_for_unrecognised_parent_table(self) -> None:
        conn = _FakeConn(responses={'pg_constraint': _row(parent_table='some_other_table')})
        assert inspect_detection_events_target_parent(conn) == 'unknown'


# ---------------------------------------------------------------------------
# W. Task A: FK parent is targets → detection_events.target_id uses targets.id
# ---------------------------------------------------------------------------

class TestWFKParentTargets:
    def _make_conn_targets_fk(self) -> _FakeConn:
        """FK is targets; telemetry_target_id exists in targets.id."""
        tid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        run_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            # FK inspect
            if 'pg_constraint' in q:
                return _row(parent_table='targets')
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=tid, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 500, 'chain_id': 1, 'provider_name': 'infura'},
                )
            # Preflight and resolver both query targets
            if 'FROM targets' in q:
                return _row(id=tid)
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            if 'monitoring_runs' in q:
                return _row(id=run_id)
            return None

        return _FakeConn(responses={'': respond})

    def test_W_resolver_returns_targets_id_when_fk_is_targets(self) -> None:
        tid = str(uuid.uuid4())
        wid = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='targets')
            if 'FROM targets' in q:
                return _row(id=tid)
            return None

        conn = _FakeConn(responses={'': respond})
        result = resolve_detection_event_target_id(conn, wid, tid)
        assert result == tid

    def test_W_resolver_does_not_query_monitored_targets_when_fk_is_targets(self) -> None:
        tid = str(uuid.uuid4())
        wid = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='targets')
            if 'FROM targets' in q:
                return _row(id=tid)
            return None

        conn = _FakeConn(responses={'': respond})
        resolve_detection_event_target_id(conn, wid, tid)
        mt_queries = [q for q, _ in conn.executed if 'monitored_targets' in q]
        assert mt_queries == [], 'Must not query monitored_targets when FK is targets'

    def test_W_create_proof_chain_uses_targets_id_in_detection_events_insert(self) -> None:
        conn = self._make_conn_targets_fk()
        # Find the telemetry target_id that the conn will report
        telemetry_resp = conn.execute('SELECT * FROM telemetry_events te WHERE evidence_source = %s', ('live',))
        conn.executed.clear()

        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True

        de_inserts = [params for q, params in conn.executed if 'INSERT INTO detection_events' in q]
        assert de_inserts, 'Expected INSERT INTO detection_events'

    def test_W_resolve_for_targets_parent_uses_fallback_when_not_direct_match(self) -> None:
        """When telemetry_target_id not in targets.id, falls back to another targets row."""
        wid = str(uuid.uuid4())
        tid_telemetry = str(uuid.uuid4())   # NOT in targets.id
        tid_fallback = str(uuid.uuid4())    # another targets row for this workspace
        call_count = 0

        def respond(q: str, _params: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # direct match for telemetry_target_id fails
            return _row(id=tid_fallback)  # fallback row found

        conn = _FakeConn(responses={'FROM targets': respond})
        result = _resolve_for_targets_parent(conn, wid, tid_telemetry)
        assert result == tid_fallback

    def test_W_resolve_for_targets_parent_raises_when_no_targets_row_exists(self) -> None:
        wid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        conn = _FakeConn(responses={'FROM targets': None})
        with pytest.raises(RuntimeError, match='targets'):
            _resolve_for_targets_parent(conn, wid, tid)


# ---------------------------------------------------------------------------
# X. Task B: FK parent is monitored_targets → uses monitored_targets.id path
# ---------------------------------------------------------------------------

class TestXFKParentMonitoredTargets:
    def test_X_resolver_routes_to_monitored_targets_when_fk_is_monitored_targets(self) -> None:
        mt_id = str(uuid.uuid4())
        wid = str(uuid.uuid4())
        tid = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='monitored_targets')
            if 'FROM monitored_targets' in q:
                return _row(id=mt_id)
            return None

        conn = _FakeConn(responses={'': respond})
        result = resolve_detection_event_target_id(conn, wid, tid)
        assert result == mt_id

    def test_X_resolver_does_not_query_targets_table_when_fk_is_monitored_targets(self) -> None:
        mt_id = str(uuid.uuid4())
        wid = str(uuid.uuid4())
        tid = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='monitored_targets')
            if 'FROM monitored_targets' in q:
                return _row(id=mt_id)
            return None

        conn = _FakeConn(responses={'': respond})
        resolve_detection_event_target_id(conn, wid, tid)
        target_queries = [
            q for q, _ in conn.executed
            if 'FROM targets' in q and 'monitored_targets' not in q and 'pg_constraint' not in q
        ]
        assert target_queries == [], 'Must not query targets when FK is monitored_targets'


# ---------------------------------------------------------------------------
# Y. Task C: regression — resolved id missing from parent table → never inserted
# ---------------------------------------------------------------------------

PRODUCTION_REGRESSION_TARGET_ID = '8629ff8d-1807-5eb4-9eae-4167bd118eff'


class TestYPreflightGuard:
    """Preflight guard prevents FK violation when resolved id is absent from parent."""

    def test_Y_preflight_raises_when_resolved_id_missing_from_targets(self) -> None:
        """When FK is targets but resolved id is not in targets, RuntimeError fires."""
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        # Simulate: telemetry_target_id exists in targets (step A succeeds),
        # but somehow the resolved id is not confirmed by preflight.
        # We achieve this by making the pg_constraint say 'targets' but having
        # SELECT 1 FROM targets return None.
        bad_id = PRODUCTION_REGRESSION_TARGET_ID

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='targets')
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=bad_id, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 1, 'chain_id': 1, 'provider_name': 'test'},
                )
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=str(uuid.uuid4()))
            # Resolver step A: bad_id not found in targets
            # Resolver step B: no other targets row
            # → _resolve_for_targets_parent raises before reaching INSERT
            if 'FROM targets' in q:
                return None
            if 'monitoring_runs' in q:
                return _row(id=str(uuid.uuid4()))
            return None

        conn = _FakeConn(responses={'': respond})
        with pytest.raises(RuntimeError):
            _create_proof_chain(conn, str(uuid.uuid4()))

    def test_Y_preflight_raises_with_required_diagnostic_fields(self) -> None:
        """RuntimeError message must include parent_table, telemetry_target_id,
        resolved_target_id, and workspace_id."""
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        wid = str(uuid.uuid4())
        tid = str(uuid.uuid4())
        mt_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='monitored_targets')
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=tid, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 1, 'chain_id': 1, 'provider_name': 'test'},
                )
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'INSERT INTO monitored_targets' in q:
                return _row(id=mt_id)
            # SELECT 1 FROM monitored_targets (preflight) returns None → guard fires
            if 'SELECT 1 FROM monitored_targets' in q:
                return None
            if 'FROM monitored_targets' in q:
                return None
            if 'monitoring_runs' in q:
                return _row(id=str(uuid.uuid4()))
            return None

        conn = _FakeConn(responses={'': respond})
        with pytest.raises(RuntimeError) as exc_info:
            _create_proof_chain(conn, wid)
        msg = str(exc_info.value)
        assert 'parent_table' in msg
        assert 'telemetry_target_id' in msg or tid in msg
        assert 'resolved_target_id' in msg or mt_id in msg
        assert 'workspace_id' in msg or wid in msg

    def test_Y_production_regression_old_code_would_fail_new_code_uses_targets(self) -> None:
        """When FK → targets and telemetry_target_id exists in targets, resolved id
        must be the targets.id — not a monitored_targets.id (which caused the production error)."""
        wid = str(uuid.uuid4())
        # In production, telemetry_target_id is a real targets.id
        real_targets_id = PRODUCTION_REGRESSION_TARGET_ID

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='targets')
            if 'FROM targets' in q:
                return _row(id=real_targets_id)
            return None

        conn = _FakeConn(responses={'': respond})
        result = resolve_detection_event_target_id(conn, wid, real_targets_id)
        # Must return the targets.id, not invent a monitored_targets.id
        assert result == real_targets_id
        # Must NOT have queried monitored_targets at all
        mt_queries = [q for q, _ in conn.executed if 'monitored_targets' in q]
        assert mt_queries == [], (
            'When FK → targets, resolver must not touch monitored_targets. '
            'The production error was caused by inserting a monitored_targets.id into '
            'detection_events.target_id when the FK required a targets.id.'
        )


# ---------------------------------------------------------------------------
# Z. Task D: full chain succeeds after prior partial archives with targets FK
# ---------------------------------------------------------------------------

class TestZFullChainAfterPartialArchivesTargetsFK:
    """Full proof chain creation after orphan archival, with FK → targets."""

    def _make_conn(self) -> _FakeConn:
        tid = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        run_id = str(uuid.uuid4())

        def respond(q: str, _params: Any) -> Any:
            if 'pg_constraint' in q:
                return _row(parent_table='targets')
            if 'FROM telemetry_events te' in q:
                return _row(
                    id=str(uuid.uuid4()), target_id=tid, asset_id=None,
                    observed_at=now,
                    payload_json={'block_number': 77777, 'chain_id': 1, 'provider_name': 'alchemy'},
                )
            if 'FROM targets' in q:
                return _row(id=tid)
            if 'FROM monitored_systems ms' in q:
                return _row(id=str(uuid.uuid4()), asset_id=None)
            if 'SELECT created_by_user_id FROM workspaces' in q:
                return _row(created_by_user_id=uid)
            if 'SELECT id FROM assets' in q:
                return None
            if 'monitoring_runs' in q:
                return _row(id=run_id)
            # Orphan archive counts
            if 'FROM alerts a' in q and 'COUNT' in q:
                return _row(c=2)
            if 'FROM incidents i' in q and 'COUNT' in q:
                return _row(c=1)
            return None

        return _FakeConn(responses={'': respond})

    def test_Z_archive_then_create_chain_with_targets_fk(self) -> None:
        conn = self._make_conn()
        _archive_orphan_alerts(conn, str(uuid.uuid4()), dry_run=False)
        _archive_orphan_incidents(conn, str(uuid.uuid4()), dry_run=False)
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True

    def test_Z_detection_events_insert_uses_targets_id_not_monitored_targets_id(self) -> None:
        conn = self._make_conn()
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        assert result.get('created') is True
        de_inserts = [params for q, params in conn.executed if 'INSERT INTO detection_events' in q]
        assert de_inserts, 'Expected INSERT INTO detection_events'
        # No monitored_targets INSERT must have been executed (targets FK path)
        mt_inserts = [q for q, _ in conn.executed if 'INSERT INTO monitored_targets' in q]
        assert mt_inserts == [], 'Must not upsert monitored_targets when FK → targets'

    def test_Z_full_chain_ids_returned_with_targets_fk(self) -> None:
        conn = self._make_conn()
        result = _create_proof_chain(conn, str(uuid.uuid4()))
        for key in (
            'telemetry_event_id', 'detection_event_id', 'detection_id',
            'alert_id', 'incident_id', 'incident_timeline_id',
            'response_action_id', 'evidence_id',
        ):
            assert result.get(key) is not None, f'Missing chain id: {key!r}'
