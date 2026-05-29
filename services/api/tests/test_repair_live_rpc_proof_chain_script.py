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
    resolve_detection_event_target_id,
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
            # All other INSERT/UPDATE statements return None
            return None

        conn = _FakeConn()
        conn._responses = {
            'FROM telemetry_events te': respond,
            'FROM monitored_systems ms': respond,
            'SELECT created_by_user_id FROM workspaces': respond,
            'SELECT id FROM assets': respond,
            'monitored_targets': respond,
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
            if 'FROM monitored_targets' in q:
                return None
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
            if 'FROM monitored_targets' in q:
                return None
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
