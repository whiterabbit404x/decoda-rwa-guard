from __future__ import annotations

import json
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
            return _FakeRow({'id': 'exp-1', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-1', 'include_raw_events': True}})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow({'id': 'inc-1', 'workspace_id': 'ws-1', 'title': 'Incident', 'severity': 'high'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-1', 'severity': 'high'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {'tx_hash': '0xabc'}}])
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
    pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-1')
    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    assert sorted(row.keys()) == ['alerts.json', 'detection_metrics.json', 'evidence.json', 'incidents.json', 'summary.json']
    assert row['summary.json']['incident_id'] == 'inc-1'
    assert connection.storage_update_called is True


def test_generate_export_artifact_report_template_includes_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ReportConnection(_FakeConnection):
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                return _FakeRow({'id': 'exp-2', 'export_type': 'report', 'format': 'json', 'filters': {'report_template': 'oracle_integrity_report', 'evidence_refs': [{'kind': 'alert', 'id': 'alert-1'}]}})
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


def test_create_export_job_viewer_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Viewers must not be allowed to create export jobs; only owner/admin may."""
    from contextlib import contextmanager
    from fastapi import Request

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)

    def deny_non_admin(connection, request):
        raise HTTPException(status_code=403, detail='Owner or admin role is required for this action.')

    @contextmanager
    def fake_pg():
        class _C:
            def execute(self, *a, **k):
                pass
            def commit(self):
                pass
        yield _C()

    monkeypatch.setattr(pilot, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, '_require_workspace_admin', deny_non_admin)

    req = Request({'type': 'http', 'headers': []})
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_export_job('alerts', {'format': 'csv'}, request=req)
    assert exc_info.value.status_code == 403


def test_create_export_job_admin_is_permitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner/admin role allows export job creation."""
    from contextlib import contextmanager
    from fastapi import Request

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda c, r: ({'id': 'u1'}, {'workspace_id': 'ws-1', 'role': 'admin'}))

    inserted: list[str] = []

    class _Conn:
        def execute(self, query, params=None):
            q = str(query)
            inserted.append(q)

            class _R:
                def fetchone(self_inner):
                    if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in ' '.join(q.split()):
                        return {'id': 'exp-x', 'export_type': 'alerts', 'format': 'csv', 'filters': {}}
                    if 'SELECT status, error_message' in q:
                        return {'status': 'completed', 'error_message': None}
                    return None

                def fetchall(self_inner):
                    return []

            return _R()

        def commit(self):
            pass

    @contextmanager
    def fake_pg():
        yield _Conn()

    monkeypatch.setattr(pilot, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, '_workspace_plan', lambda c, wid: {'exports_enabled': True})
    monkeypatch.setattr(pilot, '_generate_export_artifact', lambda c, workspace_id, export_id: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)

    req = Request({'type': 'http', 'headers': []})
    result = pilot.create_export_job('alerts', {'format': 'csv'}, request=req)
    assert result['status'] == 'completed'
    assert any('INSERT INTO export_jobs' in q for q in inserted)
