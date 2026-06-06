"""Tests for proof bundle export quality, truthfulness, workspace scoping, and signing."""
from __future__ import annotations

import json
import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _FakeStorage:
    backend_name = 'local'

    def __init__(self):
        self.content = b''

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self.content = content
        return object_key

    def object_lock_status(self):
        return {'object_lock_enabled': False}


class _FakeRow:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row if isinstance(self._row, list) else ([] if self._row is None else [self._row])


class _CompleteChainConnection:
    """All chain sections present; live evidence source."""

    def __init__(self):
        self.storage_update_called = False

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _FakeRow({
                'id': 'exp-1',
                'export_type': 'proof_bundle',
                'format': 'json',
                'filters': {'incident_id': 'inc-live', 'include_raw_events': True},
                'requested_by_user_id': 'user-test-1',
            })
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow({'id': 'inc-live', 'workspace_id': 'ws-live', 'title': 'Live Incident', 'severity': 'high', 'status': 'open'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-live-1', 'severity': 'high', 'source': 'live_provider', 'target_id': 'target-1'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {'tx_hash': '0xreal'}}])
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([{'id': 'action-live-1', 'action_type': 'freeze_wallet', 'status': 'executed', 'mode': 'live', 'execution_metadata': None, 'created_at': '2026-01-01T00:10:00Z', 'executed_at': '2026-01-01T00:11:00Z', 'rolled_back_at': None}])
        if 'FROM detections' in normalized and 'linked_alert_id = ANY' in normalized:
            return _FakeRow([{'id': 'det-live-1', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.97, 'evidence_source': 'live', 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Live anomaly'}])
        if 'FROM audit_logs' in normalized and 'row_hash IS NOT NULL' in normalized:
            # Audit chain tip query
            return _FakeRow(None)
        if 'FROM audit_logs' in normalized:
            return _FakeRow([{'id': 'audit-1', 'action': 'export.generate', 'entity_type': 'export_job', 'entity_id': 'exp-1', 'metadata': None, 'created_at': '2026-01-01T00:12:00Z'}])
        if "UPDATE export_jobs SET status = 'completed'" in normalized:
            self.storage_update_called = True
            return _FakeRow(None)
        if "UPDATE export_jobs SET status = 'failed'" in normalized:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {query}')


class _SimulatorChainConnection(_CompleteChainConnection):
    """Chain present with simulator evidence source."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _FakeRow({'id': 'exp-sim', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-sim', 'include_raw_events': False}, 'requested_by_user_id': None})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow({'id': 'inc-sim', 'workspace_id': 'ws-1', 'title': 'Simulator Incident', 'severity': 'medium', 'status': 'open'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-sim-1', 'severity': 'medium', 'source': 'simulator', 'target_id': 'target-1'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([{'id': 'metric-sim', 'event_observed_at': '2026-02-01T00:00:00Z', 'detected_at': '2026-02-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {'tx_hash': '0xsim'}}])
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([{'id': 'action-sim-1', 'action_type': 'notify_team', 'status': 'completed', 'mode': 'simulated', 'execution_metadata': None, 'created_at': '2026-02-01T00:05:00Z', 'executed_at': None, 'rolled_back_at': None}])
        if 'FROM detections' in normalized and 'linked_alert_id = ANY' in normalized:
            return _FakeRow([{'id': 'det-sim-1', 'detection_type': 'anomaly', 'severity': 'medium', 'confidence': 0.8, 'evidence_source': 'simulator', 'status': 'open', 'detected_at': '2026-02-01T00:01:00Z', 'title': 'Simulator anomaly'}])
        if 'FROM audit_logs' in normalized and 'row_hash IS NOT NULL' in normalized:
            return _FakeRow(None)
        if 'FROM audit_logs' in normalized:
            return _FakeRow([])
        return super().execute(query, params)


class _MissingResponseActionsConnection(_CompleteChainConnection):
    """No response actions — chain is partial."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([])
        return super().execute(query, params)


class _NoAlertsConnection(_CompleteChainConnection):
    """No alerts, detections, evidence, or response actions — chain is incomplete."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([])
        if 'FROM detections' in normalized and 'linked_alert_id = ANY' in normalized:
            return _FakeRow([])
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([])
        return super().execute(query, params)


class _CrossWorkspaceConnection:
    """Incident not found (cross-workspace attempt)."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _FakeRow({'id': 'exp-x', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-other-ws', 'include_raw_events': True}, 'requested_by_user_id': None})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {query}')


# ── Tests ──────────────────────────────────────────────────────────

def test_proof_bundle_complete_chain_live_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Complete chain with live evidence → export_status=complete, evidence_source_type=live."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    assert meta['export_status'] == 'complete'
    assert meta['evidence_source_type'] == 'live'
    assert meta['missing_sections'] == []
    """Simulator evidence → evidence_source_type=simulator, warning included."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _SimulatorChainConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-sim')

    assert meta['evidence_source_type'] == 'simulator'
    assert any('simulator' in w.lower() for w in meta['warnings'])

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['evidence_source_type'] == 'simulator'


def test_proof_bundle_missing_response_actions_is_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """No response actions → export_status=partial, response_actions in missing_sections."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _MissingResponseActionsConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    assert meta['export_status'] == 'partial'
    assert 'response_actions' in meta['missing_sections']
    assert any('partial' in w.lower() or 'missing' in w.lower() for w in meta['warnings'])


def test_proof_bundle_no_alerts_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    """No alerts, detections, or evidence → export_status=incomplete."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _NoAlertsConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    assert meta['export_status'] == 'incomplete'
    assert 'alerts' in meta['missing_sections']
    assert 'telemetry_evidence' in meta['missing_sections']
    assert meta['evidence_source_type'] == 'missing'


def test_proof_bundle_cross_workspace_incident_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incident not found for requesting workspace → 404 raised."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CrossWorkspaceConnection()
    with pytest.raises(HTTPException) as exc_info:
        pilot._generate_export_artifact(connection, workspace_id='ws-attacker', export_id='exp-x')
    assert exc_info.value.status_code == 404


def test_proof_bundle_does_not_expose_raw_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof bundle JSON must not contain raw API key/token patterns."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    content = fake_storage.content.decode('utf-8').lower()
    # Should not contain obviously raw secret patterns
    assert 'api_key' not in content or '"api_key": null' in content or 'api_key' not in json.loads(fake_storage.content)
    assert 'password' not in content
    assert 'private_key' not in content


def test_proof_bundle_summary_includes_all_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """summary.json must include all identity and status fields."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    required_fields = {
        'generated_at', 'workspace_id', 'incident_id',
        'export_format_version', 'export_status', 'evidence_source_type', 'missing_sections',
        'unavailable_sections', 'warnings', 'chain_complete', 'alert_count', 'detection_count',
        'response_action_count', 'detection_metric_count',
    }
    for field in required_fields:
        assert field in summary, f'summary.json missing required field: {field}'


def test_proof_bundle_includes_response_actions_and_detections_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof bundle must include response_actions.json, detections.json, audit_log.json."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    assert 'response_actions.json' in row
    assert 'detections.json' in row
    assert 'audit_log.json' in row
    assert isinstance(row['response_actions.json'], list)
    assert isinstance(row['detections.json'], list)
    assert len(row['response_actions.json']) == 1
    assert len(row['detections.json']) == 1


# ── P0: Cryptographic signing tests ────────────────────────────────

def test_proof_bundle_includes_manifest_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof bundle must include manifest.json with all required fields."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    assert 'manifest.json' in row, 'Proof bundle must include manifest.json'
    manifest = row['manifest.json']
    required_manifest_fields = {
        'manifest_version', 'export_id', 'export_type', 'workspace_id',
        'generated_at', 'storage_backend', 'files', 'manifest_sha256',
    }
    for f in required_manifest_fields:
        assert f in manifest, f'manifest.json missing field: {f}'


def test_proof_bundle_includes_seal_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof bundle must include seal.json with HMAC signature."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    assert 'seal.json' in row, 'Proof bundle must include seal.json'
    seal = row['seal.json']
    assert seal.get('signature_algorithm') == 'HMAC-SHA256'
    assert seal.get('key_id')
    assert seal.get('signature')
    assert len(seal['signature']) == 64, 'HMAC-SHA256 hex digest must be 64 chars'


def test_proof_bundle_manifest_file_hashes_match_bundle_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every file listed in manifest.json must have a SHA-256 that matches the actual file content."""
    import hashlib
    import json as _json

    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = _json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    manifest = row['manifest.json']

    for entry in manifest['files']:
        path = entry['path']
        expected_sha256 = entry['sha256']
        assert path in row, f'File {path} listed in manifest but missing from bundle'
        # Re-serialize with canonical JSON (sort_keys, compact separators)
        actual_bytes = _json.dumps(row[path], sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
        actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
        assert actual_sha256 == expected_sha256, (
            f'SHA-256 mismatch for {path}: manifest={expected_sha256} actual={actual_sha256}'
        )


def test_proof_bundle_manifest_does_not_contain_signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """manifest.json and seal.json must not contain the signing secret."""
    import os
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'test-secret-value-abc123')
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    content = fake_storage.content.decode('utf-8')
    assert 'test-secret-value-abc123' not in content, 'Signing secret must not appear in export bundle'


def test_proof_bundle_signing_metadata_in_artifact_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """artifact_meta must include signing metadata when bundle is signed."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    assert 'signing' in meta, 'artifact_meta must include signing metadata'
    signing = meta['signing']
    assert signing.get('signed') is True
    assert signing.get('manifest_sha256')
    assert signing.get('signature_algorithm') == 'HMAC-SHA256'


def test_proof_bundle_tampering_fails_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tampering with a bundle file must fail signature verification."""
    import json as _json
    from services.api.app.evidence_signing import verify_bundle

    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = _json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    manifest = row['manifest.json']
    seal = row['seal.json']

    # Build file_values without manifest.json and seal.json
    file_values = {k: v for k, v in row.items() if k not in ('manifest.json', 'seal.json')}

    # Verify clean bundle passes (using dev secret or configured secret)
    secret = b'decoda-dev-signing-secret-NOT-FOR-PRODUCTION'
    result = verify_bundle(file_values, manifest, seal, signing_secret=secret)
    assert result['valid'], f'Clean bundle should verify successfully: {result["errors"]}'

    # Tamper with a file
    tampered_values = dict(file_values)
    tampered_values['alerts.json'] = [{'id': 'TAMPERED', 'injected': True}]
    tampered_result = verify_bundle(tampered_values, manifest, seal, signing_secret=secret)
    assert not tampered_result['valid'], 'Tampered file must fail verification'
    assert any('tampered' in e or 'mismatch' in e for e in tampered_result['errors'])


def test_proof_bundle_wrong_hmac_secret_fails_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Using the wrong HMAC secret for verification must fail."""
    import json as _json
    from services.api.app.evidence_signing import verify_bundle

    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')

    payload = _json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    manifest = row['manifest.json']
    seal = row['seal.json']
    file_values = {k: v for k, v in row.items() if k not in ('manifest.json', 'seal.json')}

    result = verify_bundle(file_values, manifest, seal, signing_secret=b'wrong-secret')
    assert not result['valid']
    assert 'hmac_signature_invalid' in result['errors']


def test_proof_bundle_production_without_signing_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """In production mode, missing signing secret must cause export creation to fail (503)."""
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()

    # Re-import evidence_signing after env change to clear module-level cache
    import importlib
    from services.api.app import evidence_signing
    importlib.reload(evidence_signing)

    with pytest.raises(HTTPException) as exc_info:
        pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')
    assert exc_info.value.status_code == 503

    # Restore
    monkeypatch.setenv('APP_MODE', 'local')
    importlib.reload(evidence_signing)


def test_proof_bundle_dev_mode_with_test_secret_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """In dev/local mode, export signing proceeds with dev fallback secret."""
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CompleteChainConnection()

    # Should not raise
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-1')
    assert meta.get('signing', {}).get('signed') is True


def test_incident_report_includes_manifest_and_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incident report export must also include manifest.json and seal.json."""

    class _IncidentReportConnection:
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
                return _FakeRow({'id': 'exp-ir-1', 'export_type': 'incident_report', 'format': 'json', 'filters': {'incident_id': 'inc-ir-1'}, 'requested_by_user_id': 'user-1'})
            if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
                return _FakeRow({'id': 'inc-ir-1', 'workspace_id': 'ws-2', 'title': 'Test Incident', 'severity': 'high', 'status': 'open', 'linked_alert_ids': []})
            if 'FROM incident_timeline' in normalized:
                return _FakeRow([])
            if 'FROM alerts WHERE workspace_id = %s AND id = ANY' in normalized:
                return _FakeRow([])
            if 'FROM response_actions WHERE workspace_id = %s AND incident_id = %s' in normalized:
                return _FakeRow([])
            if 'FROM audit_logs' in normalized and 'row_hash IS NOT NULL' in normalized:
                return _FakeRow(None)
            if "UPDATE export_jobs SET status = 'completed'" in normalized:
                return _FakeRow(None)
            if "UPDATE export_jobs SET status = 'failed'" in normalized:
                return _FakeRow(None)
            raise AssertionError(f'unexpected incident_report query: {query}')

    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_IncidentReportConnection(), workspace_id='ws-2', export_id='exp-ir-1')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    assert 'manifest.json' in row, 'Incident report must include manifest.json'
    assert 'seal.json' in row, 'Incident report must include seal.json'
    assert row['manifest.json'].get('export_type') == 'incident_report'
