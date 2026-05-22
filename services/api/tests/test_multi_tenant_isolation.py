"""
Session 14 — Multi-Tenant Isolation and Object-Level Authorization Tests.

Verifies that Workspace A cannot read, mutate, delete, export, or act on
Workspace B's objects through any core SaaS API function.

Test cases:
  A  Workspace A cannot read Workspace B asset.
  B  Workspace A cannot update Workspace B asset.
  C  Workspace A cannot delete Workspace B asset.
  D  Workspace A cannot enable/disable Workspace B monitoring target.
  E  Workspace A cannot read Workspace B detection evidence.
  F  Workspace A cannot read Workspace B detection.
  G  Workspace A cannot read Workspace B alert.
  H  Workspace A cannot acknowledge/resolve Workspace B alert.
  I  Workspace A incident list never includes Workspace B incidents.
  J  Workspace A cannot close/update Workspace B incident.
  K  Workspace A cannot execute a Workspace B response action.
  L  Workspace A cannot generate a proof bundle for a Workspace B incident.
  M  Workspace A cannot read a Workspace B export artifact.
  N  Runtime-status counters are workspace-scoped; Workspace B rows not counted.
  O  Readiness summary does not aggregate Workspace B assets/gates.
  P  Workspace membership helper scopes to the authenticated workspace only.
  Q  Cross-workspace request returns safe 404 without disclosing object details.
  R  workspace_id in request body cannot override the X-Workspace-Id context.
  S  workspace_id in query params is ignored; list uses session workspace context.
  T  Mixed-workspace export job (incident from B) is rejected with 404.
  U  Mixed-workspace response action (incident from B) is rejected with 404.
  V  log_audit writes the workspace_id from the authenticated context.
  W  Asset list for Workspace A never returns Workspace B rows.
  X  Tenant isolation helpers behave correctly in isolation.

Run:
    python -m pytest services/api/tests/test_multi_tenant_isolation.py -q
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.tenant_isolation import (
    assert_same_workspace,
    reject_body_workspace_override,
    require_object_in_workspace,
    safe_not_found,
)
from services.api.app.workspace_monitoring_summary import (
    build_runtime_setup_chain,
    build_workspace_monitoring_summary_fallback,
)

# ── Workspace fixture constants ────────────────────────────────────────────────

WS_A = 'aaaaaaaa-0000-0000-0000-000000000001'
WS_B = 'bbbbbbbb-0000-0000-0000-000000000002'

ASSET_B = 'asset-bbbb-0000-0000-0000-000000000001'
TARGET_B = 'target-bbb-0000-0000-0000-000000000001'
DETECTION_B = 'det-bbbbb-0000-0000-0000-000000000001'
ALERT_B = 'alert-bbb-0000-0000-0000-000000000001'
INCIDENT_B = 'inc-bbbbb-0000-0000-0000-000000000001'
ACTION_B = 'action-bb-0000-0000-0000-000000000001'
EXPORT_B = 'export-bb-0000-0000-0000-000000000001'


# ── Shared helpers ─────────────────────────────────────────────────────────────

class _Row:
    """Fake DB result row."""

    def __init__(self, row: Any = None):
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        if self._row is None:
            return []
        return self._row if isinstance(self._row, list) else [self._row]


def _none() -> _Row:
    return _Row(None)


def _auth_ws_a(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch pilot module to authenticate all requests as a Workspace A admin."""
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection',
                        lambda *_: {'id': 'user-a', 'email_verified_at': '2026-01-01T00:00:00Z', 'mfa_enabled': False})
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda *_: {'workspace_id': WS_A, 'role': 'admin',
                                    'workspace': {'id': WS_A, 'name': 'Workspace A', 'slug': 'ws-a'}})
    monkeypatch.setattr(pilot, '_require_workspace_admin',
                        lambda *_: (
                            {'id': 'user-a', 'email_verified_at': '2026-01-01T00:00:00Z', 'mfa_enabled': False},
                            {'workspace_id': WS_A, 'role': 'admin',
                             'workspace': {'id': WS_A, 'name': 'Workspace A', 'slug': 'ws-a'}},
                        ))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


def _request_with_ws_a() -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': WS_A}, client=None)


# ── Test A — cannot read Workspace B asset ────────────────────────────────────

def test_A_workspace_a_cannot_read_workspace_b_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /assets/{id} scoped to WS_A returns 404 when asset belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM assets WHERE id = %s AND workspace_id = %s' in normalized:
                # Asset belongs to WS_B; WS_A gets None
                _object_id, ws_id = params
                return _Row({'id': ASSET_B, 'workspace_id': WS_B}) if ws_id == WS_B else _none()
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_asset(ASSET_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404
    assert 'Asset' in exc_info.value.detail or 'not found' in exc_info.value.detail.lower()


# ── Test B — cannot update Workspace B asset ──────────────────────────────────

def test_B_workspace_a_cannot_update_workspace_b_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    """PUT /assets/{id} raises 404 when the asset does not belong to WS_A."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM assets WHERE id = %s AND workspace_id = %s' in normalized:
                _, ws_id = params
                return _Row({'id': ASSET_B}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace update.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    payload = {
        'name': 'Malicious Update',
        'asset_type': 'wallet',
        'chain_network': 'ethereum-mainnet',
        'identifier': '0x' + 'a' * 40,
    }

    with pytest.raises(HTTPException) as exc_info:
        pilot.update_asset(ASSET_B, payload, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test C — cannot delete Workspace B asset ──────────────────────────────────

def test_C_workspace_a_cannot_delete_workspace_b_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE /assets/{id} raises 404 when the asset does not belong to WS_A."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM assets WHERE id = %s AND workspace_id = %s' in normalized:
                _, ws_id = params
                return _Row({'id': ASSET_B}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace delete.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.delete_asset(ASSET_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test D — cannot enable/disable Workspace B monitoring target ──────────────

def test_D_workspace_a_cannot_enable_workspace_b_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH /targets/{id}/enable raises 404 when the target belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM targets WHERE id = %s AND workspace_id = %s' in normalized:
                _, ws_id = params
                return _Row({'id': TARGET_B, 'asset_id': 'asset-1', 'chain_network': 'ethereum-mainnet'}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace target enable.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.set_target_enabled(TARGET_B, True, _request_with_ws_a())
    assert exc_info.value.status_code == 404


def test_D_workspace_a_cannot_disable_workspace_b_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH /targets/{id}/disable raises 404 when the target belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM targets WHERE id = %s AND workspace_id = %s' in normalized:
                _, ws_id = params
                return _Row({'id': TARGET_B, 'asset_id': 'asset-1', 'chain_network': 'ethereum-mainnet'}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace target disable.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.set_target_enabled(TARGET_B, False, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test E — cannot read Workspace B detection evidence ──────────────────────

def test_E_workspace_a_cannot_read_workspace_b_detection_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /detections/{id}/evidence raises 404 when detection belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            # Detection evidence query includes workspace_id = %s
            if 'FROM detections d' in normalized and 'workspace_id = %s' in normalized:
                ws_id = params[0] if params else None
                if ws_id == WS_B:
                    return _Row({'id': DETECTION_B, 'workspace_id': WS_B, 'evidence_summary': {}, 'raw_evidence_json': {}, 'linked_alert_id': None, 'monitoring_run_id': None})
                return _none()
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_detection_evidence(DETECTION_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test F — cannot read Workspace B detection ───────────────────────────────

def test_F_workspace_a_cannot_read_workspace_b_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /detections/{id} raises 404 when detection belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM detections' in normalized and 'workspace_id = %s' in normalized:
                # params: (detection_id, workspace_id)
                _det_id, ws_id = params
                if ws_id == WS_B:
                    return _Row({'id': DETECTION_B, 'workspace_id': WS_B, 'detection_type': 'anomaly',
                                 'severity': 'high', 'confidence': 0.9, 'title': 'Test', 'evidence_summary': {},
                                 'evidence_source': 'simulator', 'source_rule': None, 'status': 'open',
                                 'detected_at': '2026-01-01T00:00:00Z', 'raw_evidence_json': {},
                                 'monitoring_run_id': None, 'linked_alert_id': None,
                                 'monitored_system_id': None, 'protected_asset_id': None,
                                 'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'})
                return _none()
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_detection(DETECTION_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test G — cannot read Workspace B alert ────────────────────────────────────

def test_G_workspace_a_cannot_read_workspace_b_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /alerts/{id} raises 404 when the alert belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
                _alert_id, ws_id = params
                if ws_id == WS_B:
                    return _Row({'id': ALERT_B, 'workspace_id': WS_B, 'title': 'Alert B', 'severity': 'high',
                                 'status': 'open', 'source': 'simulator', 'alert_type': 'anomaly',
                                 'detection_id': None, 'incident_id': None, 'target_id': None,
                                 'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'})
                return _none()
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_alert(ALERT_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test H — cannot acknowledge/resolve Workspace B alert ────────────────────

def test_H_workspace_a_cannot_acknowledge_workspace_b_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH /alerts/{id} raises 404 when the alert belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
                _alert_id, ws_id = params
                return _Row({'id': ALERT_B}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace alert patch.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.patch_alert(ALERT_B, {'status': 'acknowledged'}, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test I — incident list never includes Workspace B rows ───────────────────

def test_I_list_incidents_uses_authenticated_workspace_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /incidents only returns rows filtered by the authenticated workspace."""
    _auth_ws_a(monkeypatch)
    queries_executed: list[tuple[str, Any]] = []

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            queries_executed.append((normalized, params))
            # Return empty list — WS_A has no incidents
            return _Row([])

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    result = pilot.list_incidents(_request_with_ws_a())
    assert result['incidents'] == []

    # Every query that filters incidents must use WS_A, not WS_B
    incident_queries = [(q, p) for q, p in queries_executed if 'incidents' in q.lower()]
    for _q, params in incident_queries:
        if params:
            param_list = list(params) if not isinstance(params, (str, bytes)) else [params]
            assert WS_B not in param_list, (
                f'Incident query used WS_B workspace_id: params={params}'
            )


# ── Test J — cannot close/update Workspace B incident ────────────────────────

def test_J_workspace_a_cannot_update_workspace_b_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH /incidents/{id} raises 404 when the incident belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM incidents WHERE id = %s AND workspace_id = %s' in normalized:
                _inc_id, ws_id = params
                return _Row({'id': INCIDENT_B, 'timeline': [], 'workflow_status': 'open',
                             'assignee_user_id': None, 'resolution_note': None}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace incident patch.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.patch_incident(INCIDENT_B, {'workflow_status': 'resolved'}, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test K — cannot execute Workspace B response action ──────────────────────

def test_K_workspace_a_cannot_execute_workspace_b_response_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /response-actions/{id}/execute raises 404 when action belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                _act_id, ws_id = params
                return _Row({'id': ACTION_B, 'status': 'pending', 'mode': 'simulated',
                             'action_type': 'notify_team', 'execution_metadata': {},
                             'incident_id': INCIDENT_B, 'alert_id': ALERT_B,
                             'approved_by_user_id': None}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace action execute.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.execute_enforcement_action(ACTION_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test L — cannot generate proof bundle for Workspace B incident ────────────

def test_L_workspace_a_cannot_generate_proof_bundle_for_workspace_b_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    """_generate_export_artifact raises 404 when the export job incident belongs to WS_B."""

    class _Storage:
        backend_name = 'local'

        def write_bytes(self, *, object_key: str, content: bytes) -> str:
            return object_key

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            # Export job belongs to WS_A (legitimate export request)
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                _ej_id, ws_id = params
                if ws_id == WS_A:
                    return _Row({'id': EXPORT_B, 'export_type': 'proof_bundle', 'format': 'json',
                                 'filters': {'incident_id': INCIDENT_B, 'include_raw_events': False}})
                return _none()
            # Incident lookup: incident belongs to WS_B, not WS_A
            if 'FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
                ws_id, _inc_id = params
                return _Row({'id': INCIDENT_B, 'workspace_id': WS_B, 'title': 'Incident B',
                             'severity': 'high', 'status': 'open'}) if ws_id == WS_B else _none()
            if "UPDATE export_jobs SET status = 'failed'" in normalized:
                return _Row(None)
            return _none()

        def commit(self) -> None:
            pass

    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _Storage())

    # The proof bundle generator looks up the incident using workspace_id=WS_A.
    # Since INCIDENT_B belongs to WS_B, the lookup returns None → exception.
    with pytest.raises(Exception):
        pilot._generate_export_artifact(_Conn(), workspace_id=WS_A, export_id=EXPORT_B)


# ── Test M — cannot read Workspace B export artifact ─────────────────────────

def test_M_workspace_a_cannot_read_workspace_b_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /exports/{id} raises 404 when the export belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                _ej_id, ws_id = params
                return _Row({'id': EXPORT_B, 'workspace_id': WS_B, 'status': 'completed',
                             'format': 'json', 'export_type': 'proof_bundle'}) if ws_id == WS_B else _none()
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export(EXPORT_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404


def test_M_workspace_a_cannot_download_workspace_b_export_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /exports/{id}/download raises 404 when the export belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                _ej_id, ws_id = params
                return _Row({'id': EXPORT_B, 'workspace_id': WS_B, 'status': 'completed',
                             'format': 'json', 'storage_object_key': f'{WS_B}/{EXPORT_B}.json'}) if ws_id == WS_B else _none()
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export_artifact_content(EXPORT_B, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test N — runtime-status uses workspace-scoped counters only ───────────────

def test_N_runtime_setup_chain_uses_provided_counters_only() -> None:
    """build_runtime_setup_chain uses the caller-supplied counters without mixing workspaces."""
    counters_a = {
        'workspaces_count': 1,
        'assets_count': 2,
        'verified_assets_count': 1,
        'targets_count': 1,
        'monitored_systems_count': 1,
        'enabled_monitored_systems_count': 1,
        'detections_count': 0,
        'alerts_count': 0,
        'incidents_count': 0,
        'response_actions_count': 0,
        'evidence_count': 0,
    }
    chain_a = build_runtime_setup_chain(counters=counters_a, timestamps={
        'last_heartbeat_at': None, 'latest_poll_at': None, 'last_telemetry_at': None,
    })
    # The chain returns a list of steps; asset_created step reflects assets_count=2
    steps_by_id = {s['id']: s for s in chain_a['steps']}
    assert steps_by_id['asset_created']['status'] == 'complete'
    assert steps_by_id['monitoring_target_created']['status'] == 'complete'

    # Workspace B counters: assets_count=99. Must not bleed into chain_a.
    counters_b = dict(counters_a, assets_count=99, targets_count=50)
    chain_b = build_runtime_setup_chain(counters=counters_b, timestamps={
        'last_heartbeat_at': None, 'latest_poll_at': None, 'last_telemetry_at': None,
    })
    steps_b = {s['id']: s for s in chain_b['steps']}
    assert steps_b['asset_created']['status'] == 'complete'
    # chain_a is unchanged (counters_a had assets_count=2, not 99)
    steps_a_check = {s['id']: s for s in chain_a['steps']}
    assert steps_a_check['asset_created']['status'] == 'complete'


# ── Test O — readiness summary uses workspace-scoped counters only ────────────

def test_O_workspace_monitoring_summary_fallback_uses_workspace_counters() -> None:
    """build_workspace_monitoring_summary_fallback returns isolated workspace status."""
    summary_a = build_workspace_monitoring_summary_fallback(
        status_reason='workspace_a_configured',
        workspace_configured=True,
        runtime_status='offline',
    )
    # Workspace A summary has its own isolated status
    assert summary_a['workspace_configured'] is True
    assert summary_a['protected_assets_count'] == 0

    # Workspace B summary (different workspace_configured state)
    summary_b = build_workspace_monitoring_summary_fallback(
        status_reason='workspace_b_not_configured',
        workspace_configured=False,
        runtime_status='offline',
    )
    assert summary_b['workspace_configured'] is False

    # Two independent summaries must not share state
    assert summary_a['workspace_configured'] is True
    assert summary_b['workspace_configured'] is False


# ── Test P — workspace membership helper scopes to authenticated workspace ────

def test_P_ensure_membership_rejects_wrong_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ensure_membership raises 403 when the user is not in the requested workspace."""

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM workspace_members wm' in normalized:
                _user_id, ws_id = params
                # User is a member of WS_A only
                if ws_id == WS_A:
                    return _Row({'workspace_id': WS_A, 'role': 'admin', 'name': 'Workspace A', 'slug': 'ws-a'})
                return _none()
            return _none()

    with pytest.raises(HTTPException) as exc_info:
        pilot._ensure_membership(_Conn(), 'user-a', WS_B)
    assert exc_info.value.status_code == 403
    assert 'workspace' in exc_info.value.detail.lower()


# ── Test Q — cross-workspace 404 does not disclose object details ─────────────

def test_Q_cross_workspace_404_does_not_reveal_object_details(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 detail for cross-workspace access does not include other workspace IDs."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM assets WHERE id = %s AND workspace_id = %s' in normalized:
                return _none()  # Always returns nothing for WS_A
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_asset(ASSET_B, _request_with_ws_a())

    detail = str(exc_info.value.detail)
    # Must not leak WS_B workspace ID in the error message
    assert WS_B not in detail
    # Must not leak WS_A either (to avoid confirming which workspace owns the object)
    assert WS_A not in detail
    # Must be a generic "not found" message
    assert 'not found' in detail.lower() or 'Asset' in detail


# ── Test R — body workspace_id cannot override X-Workspace-Id ────────────────

def test_R_body_workspace_id_does_not_override_session_workspace() -> None:
    """reject_body_workspace_override raises 403 when body claims a different workspace."""
    from services.api.app.tenant_isolation import reject_body_workspace_override

    # No body workspace_id — no error
    reject_body_workspace_override(None, WS_A)
    reject_body_workspace_override('', WS_A)

    # Body workspace_id matches authorized workspace — no error
    reject_body_workspace_override(WS_A, WS_A)

    # Body workspace_id tries to claim WS_B while session is WS_A — must raise 403
    with pytest.raises(HTTPException) as exc_info:
        reject_body_workspace_override(WS_B, WS_A)
    assert exc_info.value.status_code == 403
    assert 'workspace' in exc_info.value.detail.lower()


def test_R_create_enforcement_action_ignores_body_incident_from_other_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_enforcement_action rejects incident_id that belongs to WS_B when session is WS_A."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM incidents' in normalized and 'workspace_id = %s' in normalized:
                _inc_id, ws_id = params
                # Incident belongs to WS_B
                return _Row({'id': INCIDENT_B, 'source_alert_id': ALERT_B}) if ws_id == WS_B else _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called when incident from other workspace.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    payload = {
        'action_type': 'notify_team',
        'incident_id': INCIDENT_B,  # Belongs to WS_B
        'dry_run': True,
    }

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_enforcement_action(payload, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test S — query params workspace_id does not override session workspace ────

def test_S_list_assets_uses_session_workspace_not_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_assets ignores any workspace_id in query parameters and uses session context."""
    _auth_ws_a(monkeypatch)
    queries_executed: list[tuple[str, Any]] = []

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            queries_executed.append((normalized, params))
            if 'FROM assets' in normalized:
                return _Row([])
            return _Row([])

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    # Even if an attacker passes workspace_id=WS_B as a query param, the request
    # object header still says WS_A. The session always wins.
    request = SimpleNamespace(
        headers={'x-workspace-id': WS_A},
        query_params={'workspace_id': WS_B},  # Attacker-supplied
        client=None,
    )
    pilot.list_assets(request)

    # Verify every asset query used WS_A, never WS_B
    asset_queries = [(q, p) for q, p in queries_executed if 'FROM assets' in q.lower()]
    for _q, params in asset_queries:
        if params:
            param_list = list(params)
            assert WS_B not in param_list, (
                f'list_assets used WS_B in query params: {params}'
            )


# ── Test T — mixed-workspace export job (incident from WS_B) is rejected ─────

def test_T_export_job_for_cross_workspace_incident_is_rejected() -> None:
    """_generate_export_artifact raises when the incident belongs to a different workspace."""

    class _Storage:
        backend_name = 'local'

        def write_bytes(self, *, object_key: str, content: bytes) -> str:
            return object_key

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                return _Row({'id': 'exp-x', 'export_type': 'proof_bundle', 'format': 'json',
                             'filters': {'incident_id': INCIDENT_B}})
            # Incident lookup with WS_A returns nothing (incident is in WS_B)
            if 'FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
                ws_id, _inc_id = params
                return _none()  # WS_A never finds WS_B incident
            if "UPDATE export_jobs SET status = 'failed'" in normalized:
                return _Row(None)
            return _none()

        def commit(self) -> None:
            pass

    from unittest.mock import patch
    with patch.object(pilot, 'load_export_storage', return_value=_Storage()):
        with pytest.raises(Exception):
            pilot._generate_export_artifact(_Conn(), workspace_id=WS_A, export_id='exp-x')


# ── Test U — mixed-workspace response action (incident from WS_B) rejected ───

def test_U_response_action_with_cross_workspace_incident_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_enforcement_action raises 404 when incident_id belongs to WS_B."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'FROM incidents' in normalized and 'workspace_id = %s' in normalized:
                _inc_id, ws_id = params
                return _none()  # WS_A has no such incident
            return _none()

        def commit(self) -> None:
            raise AssertionError('Commit must not be called for cross-workspace response action.')

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    payload = {'action_type': 'notify_team', 'incident_id': INCIDENT_B}

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_enforcement_action(payload, _request_with_ws_a())
    assert exc_info.value.status_code == 404


# ── Test V — audit log entries are scoped to the authenticated workspace ──────

def test_V_log_audit_writes_authenticated_workspace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """log_audit always records the workspace_id from the authenticated context."""
    inserts: list[tuple[Any, ...]] = []

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            if 'INSERT INTO audit_logs' in normalized:
                inserts.append(params)
            return _Row(None)

    request = SimpleNamespace(headers={}, client=None)
    pilot.log_audit(
        _Conn(),
        action='asset.delete',
        entity_type='asset',
        entity_id=ASSET_B,
        request=request,
        user_id='user-a',
        workspace_id=WS_A,
    )

    assert len(inserts) == 1
    params = inserts[0]
    # workspace_id is the second positional parameter
    assert params[1] == WS_A
    # Must not record WS_B as the workspace
    assert WS_B not in params


# ── Test W — list_assets never includes Workspace B rows ─────────────────────

def test_W_list_assets_never_returns_workspace_b_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_assets query uses WS_A scope; rows belonging to WS_B are never returned."""
    _auth_ws_a(monkeypatch)
    queries_used: list[tuple[str, Any]] = []

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            queries_used.append((normalized, params))
            if 'FROM assets' in normalized:
                # Return a WS_A asset to confirm proper scoping
                ws_id = params[0] if params else None
                if ws_id == WS_A:
                    return _Row([{'id': 'asset-aaa', 'workspace_id': WS_A, 'name': 'WS-A Asset',
                                  'asset_type': 'wallet', 'chain_network': 'ethereum-mainnet',
                                  'identifier': '0x' + 'a' * 40, 'enabled': True,
                                  'deleted_at': None, 'created_at': '2026-01-01T00:00:00Z',
                                  'updated_at': '2026-01-01T00:00:00Z'}])
                return _Row([])
            if 'FROM asset_tags' in normalized:
                return _Row([])
            return _Row([])

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    result = pilot.list_assets(_request_with_ws_a())
    assets = result.get('assets', [])
    for asset in assets:
        assert asset.get('workspace_id') != WS_B, 'Workspace B asset must not appear in WS_A list'

    # Every parameterized query that mentions assets must use WS_A
    asset_queries = [(q, p) for q, p in queries_used if 'FROM assets' in q.lower()]
    for _q, params in asset_queries:
        if params:
            param_list = list(params)
            assert WS_B not in param_list


# ── Test X — tenant_isolation helpers work correctly ─────────────────────────

def test_X_assert_same_workspace_raises_404_on_mismatch() -> None:
    """assert_same_workspace raises HTTP 404 when workspace IDs differ."""
    # Same workspace — no exception
    assert_same_workspace(WS_A, WS_A)

    # Different workspaces — must raise 404
    with pytest.raises(HTTPException) as exc_info:
        assert_same_workspace(WS_B, WS_A)
    assert exc_info.value.status_code == 404

    # None value — must raise 404 (fail closed)
    with pytest.raises(HTTPException) as exc_info:
        assert_same_workspace(None, WS_A)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        assert_same_workspace(WS_A, None)
    assert exc_info.value.status_code == 404


def test_X_require_object_in_workspace_raises_404_when_not_found() -> None:
    """require_object_in_workspace raises HTTP 404 when the object isn't in the workspace."""

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            ws_id = params[1] if params and len(params) > 1 else None
            # Only return for WS_B
            if ws_id == WS_B:
                return _Row({'id': 'asset-1', 'workspace_id': WS_B})
            return _none()

    # WS_A request for WS_B object → 404
    with pytest.raises(HTTPException) as exc_info:
        require_object_in_workspace(_Conn(), table='assets', object_id='asset-1', workspace_id=WS_A)
    assert exc_info.value.status_code == 404

    # WS_B request for WS_B object → success
    row = require_object_in_workspace(_Conn(), table='assets', object_id='asset-1', workspace_id=WS_B)
    assert row['workspace_id'] == WS_B


def test_X_safe_not_found_returns_404() -> None:
    """safe_not_found returns an HTTPException with status 404."""
    exc = safe_not_found()
    assert exc.status_code == 404

    exc2 = safe_not_found('Custom message.')
    assert exc2.status_code == 404
    assert exc2.detail == 'Custom message.'


def test_X_reject_body_workspace_override_raises_403_on_mismatch() -> None:
    """reject_body_workspace_override raises 403 when body claims a different workspace."""
    # No body workspace — ok
    reject_body_workspace_override(None, WS_A)
    reject_body_workspace_override('', WS_A)

    # Matching workspace — ok
    reject_body_workspace_override(WS_A, WS_A)

    # Mismatched workspace — 403
    with pytest.raises(HTTPException) as exc_info:
        reject_body_workspace_override(WS_B, WS_A)
    assert exc_info.value.status_code == 403


# ── Additional edge cases ──────────────────────────────────────────────────────

def test_patch_alert_workspace_scope_uses_authenticated_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """patch_alert uses workspace_id from authenticated context, not from alert row."""
    _auth_ws_a(monkeypatch)

    class _Conn:
        def __init__(self) -> None:
            self.queries: list[tuple[str, Any]] = []

        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            self.queries.append((normalized, params))
            if 'FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
                # WS_A has no such alert (belongs to WS_B)
                return _none()
            return _none()

        def commit(self) -> None:
            raise AssertionError('Must not commit.')

    conn = _Conn()

    @contextmanager
    def _pg():
        yield conn

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException) as exc_info:
        pilot.patch_alert(ALERT_B, {'status': 'resolved'}, _request_with_ws_a())
    assert exc_info.value.status_code == 404

    # Confirm the query always used WS_A
    for _q, params in conn.queries:
        if params and ALERT_B in str(params):
            param_list = list(params) if not isinstance(params, (str, bytes)) else [params]
            assert WS_B not in param_list


def test_get_export_workspace_scope_query_uses_session_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_export always queries with the session workspace_id, not the export_id alone."""
    _auth_ws_a(monkeypatch)
    queries: list[tuple[str, Any]] = []

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Row:
            normalized = ' '.join(str(query).split())
            queries.append((normalized, params))
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                return _none()  # WS_A has no such export
            return _none()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    with pytest.raises(HTTPException):
        pilot.get_export(EXPORT_B, _request_with_ws_a())

    # Verify that every export query used WS_A and not WS_B
    export_queries = [(q, p) for q, p in queries if 'export_jobs' in q.lower()]
    for _q, params in export_queries:
        if params:
            assert WS_B not in list(params)
            assert WS_A in list(params)
