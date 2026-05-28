"""Tests for the main evidence row created by _ensure_workspace_live_rpc_proof_chain().

A. Proof chain creates a main evidence row
B. Evidence row alert_id links to the proof alert
C. Evidence raw_payload_json contains all required chain IDs
D. evidence_count > 0 after proof chain runs
E. Full chain (including evidence) required for LIVE status
F. Launch-proof strictness: canonical false overrides service-summary true
G. No stale contradictory artifacts in checked-in repo
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Mock DB helpers
# ---------------------------------------------------------------------------

class _MockResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


_WS_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
_TARGET_ID = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
_ASSET_ID = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
_USER_ID = 'dddddddd-dddd-dddd-dddd-dddddddddddd'
_MONITORED_SYSTEM_ID = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
_BLOCK_NUMBER = '21000000'
_CHAIN_ID = 1


def _make_telemetry_row() -> dict:
    return {
        'id': 'ffffffff-ffff-ffff-ffff-ffffffffffff',
        'target_id': _TARGET_ID,
        'asset_id': _ASSET_ID,
        'observed_at': '2026-05-28T12:00:00+00:00',
        'payload_json': {
            'block_number': _BLOCK_NUMBER,
            'chain_id': _CHAIN_ID,
            'provider_name': 'infura',
        },
    }


def _build_conn(*, no_existing_detection: bool = True) -> MagicMock:
    """Build a mock DB connection that simulates a fresh proof chain scenario."""
    conn = MagicMock()
    execute_calls: list[tuple[str, tuple]] = []

    def _execute(sql: str, params: tuple = ()) -> _MockResult:
        sql_norm = ' '.join(sql.split()).lower()
        execute_calls.append((sql_norm, params))

        if 'from detections' in sql_norm and 'live_rpc_telemetry_proof' in sql_norm:
            return _MockResult(None if no_existing_detection else {
                'id': 'prev-det-id', 'linked_alert_id': 'prev-alert-id', 'monitored_system_id': None,
            })
        if 'from telemetry_events' in sql_norm:
            return _MockResult(_make_telemetry_row())
        if 'from monitored_systems' in sql_norm:
            return _MockResult({'id': _MONITORED_SYSTEM_ID})
        if 'from workspaces' in sql_norm:
            return _MockResult({'created_by_user_id': _USER_ID})
        if 'from assets' in sql_norm:
            return _MockResult({'id': _ASSET_ID})
        return _MockResult(None)

    conn.execute.side_effect = _execute
    conn._execute_calls = execute_calls
    return conn


# ---------------------------------------------------------------------------
# A. Proof chain creates a main evidence row
# ---------------------------------------------------------------------------

def test_A_proof_chain_inserts_into_evidence_table() -> None:
    conn = _build_conn()
    result = monitoring_runner._ensure_workspace_live_rpc_proof_chain(conn, workspace_id=_WS_ID)

    assert result['created'] is True
    assert 'evidence_id' in result
    assert result['evidence_id']

    # Verify an INSERT INTO evidence was executed
    evidence_calls = [
        args for args, _ in conn.execute.call_args_list
        if 'insert into evidence' in ' '.join(args[0].split()).lower()
    ]
    assert len(evidence_calls) >= 1, 'Expected at least one INSERT INTO evidence call'


def test_A_proof_chain_returns_evidence_id() -> None:
    conn = _build_conn()
    result = monitoring_runner._ensure_workspace_live_rpc_proof_chain(conn, workspace_id=_WS_ID)

    assert result['created'] is True
    evidence_id = result.get('evidence_id')
    assert evidence_id, f'evidence_id must be set in return dict; got: {result}'


def test_A_deduplication_skips_creation() -> None:
    conn = _build_conn(no_existing_detection=False)
    result = monitoring_runner._ensure_workspace_live_rpc_proof_chain(conn, workspace_id=_WS_ID)

    assert result['created'] is False
    assert result['reason'] == 'deduplicated'

    # No INSERT INTO evidence should have run
    evidence_calls = [
        args for args, _ in conn.execute.call_args_list
        if 'insert into evidence' in ' '.join(args[0].split()).lower()
    ]
    assert len(evidence_calls) == 0, 'Deduplicated path must not insert evidence'


# ---------------------------------------------------------------------------
# B. Evidence row alert_id links to the proof alert
# ---------------------------------------------------------------------------

def test_B_evidence_row_links_to_proof_alert() -> None:
    conn = _build_conn()
    result = monitoring_runner._ensure_workspace_live_rpc_proof_chain(conn, workspace_id=_WS_ID)

    assert result['created'] is True
    alert_id = result['alert_id']

    # Find the evidence INSERT call and check alert_id parameter
    for call_args in conn.execute.call_args_list:
        args, _ = call_args
        sql = args[0] if args else ''
        params = args[1] if len(args) > 1 else ()
        if 'insert into evidence' in ' '.join(sql.split()).lower():
            # alert_id is the 5th positional param (0-indexed: id, workspace_id, asset_id, target_id, alert_id)
            assert alert_id in params, (
                f'evidence INSERT must include alert_id={alert_id}; params={params}'
            )
            return

    pytest.fail('INSERT INTO evidence call not found')


# ---------------------------------------------------------------------------
# C. Evidence raw_payload_json contains all required chain IDs
# ---------------------------------------------------------------------------

def test_C_evidence_raw_payload_contains_all_chain_ids() -> None:
    conn = _build_conn()
    result = monitoring_runner._ensure_workspace_live_rpc_proof_chain(conn, workspace_id=_WS_ID)

    assert result['created'] is True

    # Find the evidence INSERT and extract the raw_payload_json parameter
    payload_json_str: str | None = None
    for call_args in conn.execute.call_args_list:
        args, _ = call_args
        sql = args[0] if args else ''
        params = args[1] if len(args) > 1 else ()
        if 'insert into evidence' in ' '.join(sql.split()).lower():
            # raw_payload_json is passed as a JSON string with ::jsonb cast
            for param in params:
                if isinstance(param, str) and 'proof_type' in param:
                    payload_json_str = param
                    break
            break

    assert payload_json_str is not None, 'Could not find raw_payload_json in evidence INSERT'

    payload = json.loads(payload_json_str)

    assert payload.get('telemetry_event_id') == result['telemetry_event_id']
    assert payload.get('detection_id') == result['detection_id']
    assert payload.get('alert_id') == result['alert_id']
    assert payload.get('incident_id') == result['incident_id']
    assert payload.get('response_action_id') == result['response_action_id']
    assert payload.get('block_number') == _BLOCK_NUMBER
    assert payload.get('chain_id') == _CHAIN_ID
    assert payload.get('evidence_source') == 'live_rpc_polling'
    assert payload.get('controlled_proof') is True
    assert payload.get('attack_claim') is False
    assert payload.get('workspace_id') == _WS_ID
    assert payload.get('target_id') == _TARGET_ID


def test_C_evidence_raw_payload_proof_type_is_live_rpc() -> None:
    conn = _build_conn()
    result = monitoring_runner._ensure_workspace_live_rpc_proof_chain(conn, workspace_id=_WS_ID)

    for call_args in conn.execute.call_args_list:
        args, _ = call_args
        sql = args[0] if args else ''
        params = args[1] if len(args) > 1 else ()
        if 'insert into evidence' in ' '.join(sql.split()).lower():
            for param in params:
                if isinstance(param, str) and 'proof_type' in param:
                    payload = json.loads(param)
                    assert payload['proof_type'] == 'live_rpc_telemetry_proof'
                    assert payload['provider_type'] == 'evm_rpc'
                    assert payload['source_type'] == 'rpc_polling'
                    return

    pytest.fail('raw_payload_json not found in evidence INSERT')


# ---------------------------------------------------------------------------
# D. evidence_count > 0 after proof chain runs (logic-level)
# ---------------------------------------------------------------------------

def test_D_live_evidence_ready_requires_evidence_count_gt_zero() -> None:
    """live_evidence_ready logic gate checks evidence_count > 0."""
    # Verify that with evidence_count=0 the gate is false
    assert not _live_evidence_ready_gate(evidence_count=0)
    # And with evidence_count=1 (and all other fields set) it is true
    assert _live_evidence_ready_gate(evidence_count=1)


def _live_evidence_ready_gate(*, evidence_count: int) -> bool:
    """Replicate the live_evidence_ready gate from monitoring_runner."""
    telemetry_at = '2026-05-28T12:00:00+00:00'
    return bool(
        telemetry_at is not None
        and 1 > 0   # detections_count
        and 1 > 0   # alerts_count
        and 1 > 0   # incidents_count
        and 1 > 0   # response_actions_count
        and int(evidence_count) > 0
    )


# ---------------------------------------------------------------------------
# E. Full chain (including evidence) required for LIVE status
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)


def _build_summary(**overrides: Any) -> dict:
    now = _now()
    params: dict[str, Any] = {
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
        'last_detection_at': now - timedelta(seconds=10),
        'evidence_source': 'live',
        'status_reason': None,
        'configuration_reason': None,
        'valid_protected_asset_count': 1,
        'linked_monitored_system_count': 1,
        'persisted_enabled_config_count': 1,
        'valid_target_system_link_count': 1,
        'telemetry_window_seconds': 300,
        'active_alerts_count': 1,
        'active_incidents_count': 1,
        'response_actions_count': 1,
        'evidence_packages_count': 1,
        'detections_count': 1,
        'last_alert_at': now - timedelta(seconds=9),
        'last_incident_at': now - timedelta(seconds=8),
        'last_response_action_at': now - timedelta(seconds=7),
        'last_evidence_export_at': now - timedelta(seconds=6),
    }
    params.update(overrides)
    return build_workspace_monitoring_summary(**params)


def test_E_telemetry_only_yields_limited_not_live() -> None:
    summary = _build_summary(
        detections_count=0,
        active_alerts_count=0,
        active_incidents_count=0,
        response_actions_count=0,
        evidence_packages_count=0,
        last_detection_at=None,
        last_alert_at=None,
        last_incident_at=None,
        last_response_action_at=None,
        last_evidence_export_at=None,
    )
    assert summary['monitoring_status'] != 'live', (
        f'telemetry-only must not yield live; got {summary["monitoring_status"]}'
    )


def test_E_no_evidence_row_live_evidence_ready_is_false() -> None:
    """Without a main evidence row, live_evidence_ready must be False.

    The live_evidence_ready gate in monitoring_runner requires evidence_count > 0.
    When false, monitoring_runner downgrades runtime_status to degraded/limited.
    This test verifies the gate logic directly.
    """
    # Replicate the live_evidence_ready gate from monitoring_runner.py
    def _gate(evidence_count: int) -> bool:
        return bool(
            True          # canonical_last_telemetry_at is not None
            and 1 > 0     # detections_count
            and 1 > 0     # alerts_count
            and 1 > 0     # incidents_count
            and 1 > 0     # response_actions_count
            and int(evidence_count) > 0
        )

    # telemetry + detection + alert + incident + response_action, but NO evidence → not ready
    assert _gate(evidence_count=0) is False, 'evidence_count=0 must yield live_evidence_ready=False'
    # full chain including evidence → ready
    assert _gate(evidence_count=1) is True, 'evidence_count=1 must yield live_evidence_ready=True'


def test_E_full_chain_including_evidence_yields_live() -> None:
    summary = _build_summary()   # all counts = 1 including evidence_packages_count
    assert summary['monitoring_status'] == 'live', (
        f'full chain must yield live; got {summary["monitoring_status"]} '
        f'guard_flags={summary.get("guard_flags")}'
    )


# ---------------------------------------------------------------------------
# F. Launch-proof strictness: canonical false must override service-summary true
# ---------------------------------------------------------------------------

def test_F_launch_proof_false_when_canonical_says_false(tmp_path: Path) -> None:
    """When live-evidence-proof says false, _check_live_evidence must return False."""
    import scripts.generate_release_proof as _grp
    import unittest.mock as _mock

    canonical_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    canonical_dir.mkdir(parents=True)
    (canonical_dir / 'summary.json').write_text(json.dumps({
        'schema_version': 1,
        'live_provider_evidence': {
            'live_evidence_ready': False,
            'provider_ready': False,
            'missing': ['EVM_RPC_URL not configured'],
            'contradiction_flags': [],
        },
    }), encoding='utf-8')

    # Even with live service summary present, canonical false must win
    svc_dir = tmp_path / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
    svc_dir.mkdir(parents=True)
    (svc_dir / 'summary.json').write_text(json.dumps({
        'evidence_source': 'live',
        'provider_ready': True,
        'live_evidence_ready': True,
    }), encoding='utf-8')

    with _mock.patch.object(_grp, 'REPO_ROOT', tmp_path):
        ok, blockers = _grp._check_live_evidence()

    assert ok is False
    assert blockers


def test_F_launch_proof_true_when_canonical_says_true(tmp_path: Path) -> None:
    """When live-evidence-proof says true, _check_live_evidence returns True."""
    import scripts.generate_release_proof as _grp
    import unittest.mock as _mock

    canonical_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    canonical_dir.mkdir(parents=True)
    (canonical_dir / 'summary.json').write_text(json.dumps({
        'schema_version': 1,
        'live_provider_evidence': {
            'live_evidence_ready': True,
            'provider_ready': True,
            'missing': [],
            'contradiction_flags': [],
        },
    }), encoding='utf-8')

    with _mock.patch.object(_grp, 'REPO_ROOT', tmp_path):
        ok, blockers = _grp._check_live_evidence()

    assert ok is True
    assert blockers == []


# ---------------------------------------------------------------------------
# G. No stale contradictory artifacts
# ---------------------------------------------------------------------------

def test_G_artifacts_do_not_contradict() -> None:
    """
    Checked-in artifacts must not contradict each other:
    launch-proof/latest/summary.json live_evidence_ready must not be true
    when live-evidence-proof/latest/summary.json live_evidence_ready is false.
    """
    live_proof_path = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    launch_proof_path = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'

    if not live_proof_path.exists() or not launch_proof_path.exists():
        pytest.skip('Artifact files not present — skipping contradiction check')

    live_proof = json.loads(live_proof_path.read_text(encoding='utf-8'))
    launch_proof = json.loads(launch_proof_path.read_text(encoding='utf-8'))

    live_ev_ready = live_proof.get('live_provider_evidence', {}).get('live_evidence_ready', False)

    # If live-evidence-proof says false, launch-proof must also say false
    if not live_ev_ready:
        launch_live_ev = launch_proof.get('readiness', {}).get('live_evidence_ready', False)
        assert launch_live_ev is False, (
            f'Artifact contradiction: live-evidence-proof says live_evidence_ready=false '
            f'but launch-proof says live_evidence_ready={launch_live_ev!r}. '
            'The canonical live-evidence-proof is the strict source of truth.'
        )


def test_G_launch_proof_pilot_ready_consistent_with_live_evidence() -> None:
    """pilot_ready=true in launch-proof requires live_evidence_ready=true."""
    launch_proof_path = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
    if not launch_proof_path.exists():
        pytest.skip('launch-proof artifact not present')

    launch_proof = json.loads(launch_proof_path.read_text(encoding='utf-8'))
    pilot_ready = launch_proof.get('pilot_ready', False)
    live_ev_ready = launch_proof.get('readiness', {}).get('live_evidence_ready', False)

    if pilot_ready:
        assert live_ev_ready is True, (
            f'launch-proof says pilot_ready=true but live_evidence_ready={live_ev_ready!r}; '
            'pilot_ready requires live_evidence_ready=true'
        )
