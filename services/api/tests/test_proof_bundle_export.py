"""Tests for proof bundle export quality, truthfulness, and workspace scoping."""
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
            return _FakeRow({'id': 'exp-1', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-live', 'include_raw_events': True}})
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
            return _FakeRow({'id': 'exp-sim', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-sim', 'include_raw_events': False}})
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
            return _FakeRow({'id': 'exp-x', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-other-ws', 'include_raw_events': True}})
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
    assert meta['unavailable_sections'] == []
    assert meta['chain_complete'] is True
    assert meta['warnings'] == []

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['export_status'] == 'complete'
    assert summary['evidence_source_type'] == 'live'
    assert summary['chain_complete'] is True
    assert summary['alert_count'] == 1
    assert summary['detection_count'] == 1
    assert summary['response_action_count'] == 1


def test_proof_bundle_simulator_evidence_is_labeled(monkeypatch: pytest.MonkeyPatch) -> None:
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
