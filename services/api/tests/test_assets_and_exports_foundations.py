from __future__ import annotations

import json
from contextlib import contextmanager as _contextmanager
from types import SimpleNamespace as _SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


def test_validate_asset_payload_accepts_workspace_asset_shape() -> None:
    payload = {
        'name': 'Core Treasury Wallet',
        'description': 'Primary treasury signer',
        'asset_type': 'wallet',
        'chain_network': 'ethereum-mainnet',
        'identifier': '0x1111111111111111111111111111111111111111',
        'asset_class': 'treasury_token',
        'issuer_name': 'US Treasury',
        'asset_symbol': 'USTB',
        'asset_identifier': 'US912810',
        'token_contract_address': '0x1111111111111111111111111111111111111111',
        'custody_wallets': ['0x1111111111111111111111111111111111111111'],
        'treasury_ops_wallets': ['0x2222222222222222222222222222222222222222'],
        'expected_counterparties': ['0x3333333333333333333333333333333333333333'],
        'baseline_status': 'configured',
        'baseline_source': 'manual',
        'risk_tier': 'high',
        'owner_team': 'finance',
        'notes': 'Operational hot wallet',
        'enabled': True,
        'tags': ['treasury', 'hot-wallet'],
    }
    validated = pilot._validate_asset_payload(payload)
    assert validated['name'] == 'Core Treasury Wallet'
    assert validated['asset_type'] == 'wallet'
    assert validated['tags'] == ['treasury', 'hot-wallet']
    assert validated['asset_class'] == 'treasury_token'


def test_validate_asset_payload_rejects_unknown_asset_type() -> None:
    with pytest.raises(HTTPException):
        pilot._validate_asset_payload({
            'name': 'Broken',
            'asset_type': 'unknown',
            'chain_network': 'ethereum-mainnet',
            'identifier': 'abc',
        })


def test_validate_asset_payload_returns_field_specific_error_shape() -> None:
    with pytest.raises(HTTPException) as exc_info:
        pilot._validate_asset_payload({
            'name': '',
            'asset_type': 'wallet',
            'chain_network': 'ethereum-mainnet',
            'identifier': '0x1111111111111111111111111111111111111111',
        })

    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail['message'] == 'Asset name is required (max 120 chars).'
    assert detail['field_errors']['name'] == 'Asset name is required (max 120 chars).'


class _FakeRow:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row


class _FakeStorage:
    backend_name = 'local'

    def __init__(self):
        self.content = b''

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self.content = content
        return object_key


class _FakeConnection:
    def __init__(self):
        self.storage_update_called = False

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _FakeRow({'id': 'exp-1', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-1', 'include_raw_events': True}, 'requested_by_user_id': None})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow({'id': 'inc-1', 'workspace_id': 'ws-1', 'title': 'Incident', 'severity': 'high'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-1', 'severity': 'high', 'source': 'simulator'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {'tx_hash': '0xabc'}}])
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([{'id': 'action-1', 'action_type': 'notify_team', 'status': 'completed', 'mode': 'simulated', 'execution_metadata': None, 'created_at': '2026-01-01T00:05:00Z', 'executed_at': None, 'rolled_back_at': None}])
        if 'FROM detections' in normalized and 'linked_alert_id = ANY' in normalized:
            return _FakeRow([{'id': 'det-1', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.9, 'evidence_source': 'simulator', 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Test detection'}])
        if 'FROM audit_logs' in normalized and 'row_hash IS NOT NULL' in normalized:
            return _FakeRow(None)  # audit chain tip query
        if 'FROM audit_logs' in normalized:
            return _FakeRow([])
        if "UPDATE export_jobs SET status = 'completed'" in normalized:
            self.storage_update_called = True
            return _FakeRow(None)
        if "UPDATE export_jobs SET status = 'failed'" in normalized:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {query}')


def test_generate_export_artifact_proof_bundle_contains_expected_files(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _FakeConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-1')
    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    expected_keys = {
        'alerts.json', 'detection_metrics.json', 'evidence.json', 'incidents.json',
        'summary.json', 'detections.json', 'response_actions.json', 'audit_log.json',
        'manifest.json', 'seal.json',
    }
    assert set(row.keys()) == expected_keys
    summary = row['summary.json']
    assert summary['incident_id'] == 'inc-1'
    assert 'export_status' in summary
    assert 'evidence_source_type' in summary
    assert 'missing_sections' in summary
    assert 'chain_complete' in summary
    assert connection.storage_update_called is True
    # artifact_meta propagated
    assert meta.get('export_status') in {'complete', 'partial', 'incomplete'}
    assert meta.get('evidence_source_type') in {'live', 'simulator', 'unavailable', 'missing', 'unknown'}


def test_generate_export_artifact_report_template_includes_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ReportConnection(_FakeConnection):
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                return _FakeRow({'id': 'exp-2', 'export_type': 'report', 'format': 'json', 'filters': {'report_template': 'oracle_integrity_report', 'evidence_refs': [{'kind': 'alert', 'id': 'alert-1'}]}, 'requested_by_user_id': None})
            if 'FROM analysis_runs WHERE workspace_id = %s' in normalized:
                return _FakeRow([{'id': 'run-1', 'analysis_type': 'oracle', 'status': 'completed', 'title': 'Oracle variance', 'summary': 'ok', 'created_at': '2026-01-01T00:00:00Z'}])
            return super().execute(query, params)

    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _ReportConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-2')
    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    metadata = row['metadata.json']
    assert metadata['workspace_scope']['workspace_id'] == 'ws-1'
    assert metadata['artifact_type'] == 'oracle_integrity'
    assert metadata['report_template'] == 'oracle_integrity_report'
    assert metadata['provenance']['export_job_id'] == 'exp-2'
    assert metadata['provenance']['evidence_references'][0]['id'] == 'alert-1'


def test_create_export_job_requires_supported_report_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_export_job('report', {'format': 'json', 'filters': {'report_template': 'unsupported'}}, request=None)
    assert exc_info.value.status_code == 400


def test_report_template_artifact_types_cover_required_exports() -> None:
    assert set(pilot.REPORT_TEMPLATE_ARTIFACT_TYPES.keys()) == {
        'treasury_security_posture_report',
        'rwa_incident_timeline',
        'oracle_integrity_report',
        'custody_evidence_report',
        'compliance_audit_export',
    }


# ---------------------------------------------------------------------------
# create_export_job authN / authZ boundary.
#
# The gate is a single call in create_export_job:
#     _require_workspace_permission(connection, request, 'evidence.export')
# whose order is: authenticate_with_connection() [401 on missing/invalid bearer]
# -> resolve_workspace() [builds principal + role] -> _workspace_permission_granted()
# [403 when the role lacks evidence.export]. These tests exercise that real order so
# the 401 (unauthenticated) vs 403 (authenticated-but-unauthorized) distinction is
# actually verified — rather than stubbing the whole gate, which hides it.
# ---------------------------------------------------------------------------
def _authenticated_request():
    """A request carrying a bearer token. Authentication is mocked to accept it in
    the role tests, so this stands in for an already-authenticated principal — never
    an anonymous request. Production code only ever calls ``request.headers.get(...)``,
    so a headers mapping is a faithful, framework-agnostic stand-in."""
    return _SimpleNamespace(headers={'authorization': 'Bearer test-session-token', 'x-workspace-id': 'ws-1'})


class _RecordingExportConn:
    """Fake connection that records SQL and returns no rows for the RBAC/policy
    lookups, so the REAL ``_workspace_permission_granted`` falls back to
    ``DEFAULT_ROLE_PERMISSIONS`` (viewer -> denied, owner/admin -> allowed). Export-job
    reads return a completed row for the owner/admin happy path."""

    def __init__(self):
        self.queries: list[str] = []
        self.committed = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        self.queries.append(q)

        class _R:
            def fetchone(self_inner):
                if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in q:
                    return {'id': 'exp-x', 'export_type': 'alerts', 'format': 'csv', 'filters': {}}
                if 'SELECT status, error_message' in q:
                    return {'status': 'completed', 'error_message': None}
                # workspace_role_permissions override + workspace_auth_policies: no row,
                # so the real default-role permission logic and 'optional' MFA policy apply.
                return None

            def fetchall(self_inner):
                return []

        return _R()

    def commit(self):
        self.committed = True

    def inserted_export_job(self) -> bool:
        return any('INSERT INTO export_jobs' in q for q in self.queries)

    def ran_permission_lookup(self) -> bool:
        return any('workspace_role_permissions' in q for q in self.queries)


def _bind_pg(monkeypatch, conn):
    @_contextmanager
    def _pg():
        yield conn
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)


def test_create_export_job_missing_bearer_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """No credentials -> 401 (unauthenticated), through the REAL authentication path."""
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    conn = _RecordingExportConn()
    _bind_pg(monkeypatch, conn)
    req = _SimpleNamespace(headers={})  # no Authorization header at all
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_export_job('alerts', {'format': 'csv'}, request=req)
    assert exc_info.value.status_code == 401
    assert not conn.inserted_export_job() and conn.committed is False


def test_create_export_job_invalid_bearer_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed/garbage bearer token -> 401, through the REAL token decoder."""
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    conn = _RecordingExportConn()
    _bind_pg(monkeypatch, conn)
    req = _SimpleNamespace(headers={'authorization': 'Bearer not-a-valid-token'})
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_export_job('alerts', {'format': 'csv'}, request=req)
    assert exc_info.value.status_code == 401
    assert not conn.inserted_export_job() and conn.committed is False


def test_create_export_job_viewer_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AUTHENTICATED viewer is rejected by the REAL RBAC gate with 403 (not 401).

    Authentication + workspace resolution are mocked to return an authenticated
    viewer principal; the permission decision (_workspace_permission_granted) is left
    REAL, so this asserts the authenticated-but-unauthorized path, not an anonymous one.
    """
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    # Authentication SUCCEEDS and yields a viewer-role principal.
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'viewer-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda c, u, w=None: {'workspace_id': 'ws-1', 'role': 'viewer', 'workspace': {'id': 'ws-1'}})
    conn = _RecordingExportConn()
    _bind_pg(monkeypatch, conn)
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_export_job('alerts', {'format': 'csv'}, request=_authenticated_request())
    assert exc_info.value.status_code == 403
    # The real RBAC decision ran (permission lookup happened) and nothing was written.
    assert conn.ran_permission_lookup()
    assert not conn.inserted_export_job() and conn.committed is False


def test_create_export_job_authorization_precedes_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    """The authorization check runs BEFORE any export job is inserted or committed.

    A forbidden viewer must fail at the RBAC gate — before the entitlement check and
    the INSERT. The plan check (which runs only after the gate allows the action) is
    booby-trapped to prove ordering.
    """
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'viewer-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda c, u, w=None: {'workspace_id': 'ws-1', 'role': 'viewer', 'workspace': {'id': 'ws-1'}})
    conn = _RecordingExportConn()
    _bind_pg(monkeypatch, conn)

    def _must_not_run(*a, **k):
        raise AssertionError('entitlement/plan check ran before RBAC rejected the viewer')

    monkeypatch.setattr(pilot, '_workspace_plan', _must_not_run)
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_export_job('alerts', {'format': 'csv'}, request=_authenticated_request())
    assert exc_info.value.status_code == 403
    assert not conn.inserted_export_job() and conn.committed is False


def test_create_export_job_admin_is_permitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AUTHENTICATED owner/admin passes the REAL RBAC gate and the job is created."""
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'admin-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda c, u, w=None: {'workspace_id': 'ws-1', 'role': 'admin', 'workspace': {'id': 'ws-1'}})
    conn = _RecordingExportConn()
    _bind_pg(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_workspace_plan', lambda c, wid: {'exports_enabled': True})
    monkeypatch.setattr(pilot, '_generate_export_artifact', lambda c, workspace_id, export_id: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)

    result = pilot.create_export_job('alerts', {'format': 'csv'}, request=_authenticated_request())
    assert result['status'] == 'completed'
    # The real RBAC gate ran (admin is granted evidence.export) and the job was written.
    assert conn.ran_permission_lookup()
    assert conn.inserted_export_job()


def test_list_exports_exposes_proof_bundle_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager
    from fastapi import Request

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'u-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda c, u, w: {'workspace_id': 'ws-1'})
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)

    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            class _R:
                def fetchall(self_inner):
                    if 'FROM export_jobs' in q:
                        return [{
                            'id': 'exp-1',
                            'export_type': 'proof_bundle',
                            'format': 'json',
                            'status': 'completed',
                            'output_path': 'ws-1/exp-1.json',
                            'storage_backend': 'local',
                            'storage_object_key': 'ws-1/exp-1.json',
                            'error_message': None,
                            'filters': {
                                'incident_id': 'inc-1',
                                'export_status': 'partial',
                                'evidence_source_type': 'simulator',
                                'missing_sections': ['response_actions'],
                                'unavailable_sections': [],
                                'warnings': ['simulator evidence'],
                                'chain_complete': False,
                            },
                            'created_at': '2026-01-01T00:00:00Z',
                            'updated_at': '2026-01-01T00:01:00Z',
                        }]
                    return []
            return _R()

    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)

    payload = pilot.list_exports(Request({'type': 'http', 'headers': []}))
    item = payload['exports'][0]
    assert item['export_status'] == 'partial'
    assert item['evidence_source_type'] == 'simulator'
    assert item['missing_sections'] == ['response_actions']
    assert item['unavailable_sections'] == []
    assert item['chain_complete'] is False
