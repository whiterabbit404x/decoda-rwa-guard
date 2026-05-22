"""Session 12 — Customer-Facing Evidence Export Quality.

Tests for evidence export source truthfulness, section availability,
customer summary accuracy, redaction safety, and workspace isolation.

Test cases:
A. Simulator evidence is labeled simulator, not live_provider.
B. Unknown source is not treated as live_provider.
C. Missing telemetry appears in unavailable_sections.
D. Missing response action appears in unavailable_sections.
E. Partial package has package_status partial.
F. No usable evidence has package_status blocked.
G. Complete package has package_status complete only when all required sections exist.
H. customer_summary contains simulator limitation when simulator evidence is used.
I. customer_summary contains missing live-provider limitation when live evidence is unavailable.
J. Export JSON does not include secret-like values.
K. redactions_applied is true when sensitive fields are removed.
L. Workspace isolation is preserved in export generation.
M. Section statuses include all required sections.
N. Existing proof bundle export tests still pass.
"""
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


class _LiveCompleteConnection:
    """All sections present, live provider evidence."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _FakeRow({'id': 'exp-live', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-live', 'include_raw_events': True}})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow({'id': 'inc-live', 'workspace_id': 'ws-live', 'title': 'Live Incident', 'severity': 'high', 'status': 'open', 'asset_id': 'asset-1'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-live-1', 'severity': 'high', 'source': 'live_provider', 'target_id': 'target-1'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {'tx_hash': '0xreal'}}])
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([{'id': 'action-1', 'action_type': 'freeze_wallet', 'status': 'executed', 'mode': 'live', 'execution_metadata': None, 'created_at': '2026-01-01T00:10:00Z', 'executed_at': '2026-01-01T00:11:00Z', 'rolled_back_at': None}])
        if 'FROM detections' in normalized and 'linked_alert_id = ANY' in normalized:
            return _FakeRow([{'id': 'det-1', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.97, 'evidence_source': 'live', 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Live anomaly'}])
        if 'FROM audit_logs' in normalized:
            return _FakeRow([{'id': 'audit-1', 'action': 'export.generate', 'entity_type': 'export_job', 'entity_id': 'exp-live', 'metadata': None, 'created_at': '2026-01-01T00:12:00Z'}])
        if "UPDATE export_jobs SET status = 'completed'" in normalized:
            return _FakeRow(None)
        if "UPDATE export_jobs SET status = 'failed'" in normalized:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {query}')


class _SimulatorCompleteConnection(_LiveCompleteConnection):
    """All sections present, simulator evidence source."""

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


class _UnknownSourceConnection(_LiveCompleteConnection):
    """Alert with unrecognized evidence_source value."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-unk-1', 'severity': 'low', 'source': 'custom_integration_xyz', 'target_id': 'target-1'}])
        if 'FROM detections' in normalized and 'linked_alert_id = ANY' in normalized:
            return _FakeRow([{'id': 'det-unk-1', 'detection_type': 'anomaly', 'severity': 'low', 'confidence': 0.5, 'evidence_source': 'custom_integration_xyz', 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Unknown source detection'}])
        return super().execute(query, params)


class _MissingTelemetryConnection(_LiveCompleteConnection):
    """No detection_metrics — telemetry section absent."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([])
        return super().execute(query, params)


class _MissingResponseActionsConnection(_LiveCompleteConnection):
    """No response_actions — response_action section absent."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([])
        return super().execute(query, params)


class _NoEvidenceConnection(_LiveCompleteConnection):
    """No alerts, detections, metrics, or response_actions — no usable evidence."""

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


class _SecretInMetadataConnection(_LiveCompleteConnection):
    """Response action execution_metadata contains secret-like field."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM response_actions' in normalized and 'incident_id = %s' in normalized:
            return _FakeRow([{
                'id': 'action-1',
                'action_type': 'freeze_wallet',
                'status': 'executed',
                'mode': 'live',
                'execution_metadata': {'api_key': 'sk-secret-value', 'result': 'frozen'},
                'created_at': '2026-01-01T00:10:00Z',
                'executed_at': '2026-01-01T00:11:00Z',
                'rolled_back_at': None,
            }])
        return super().execute(query, params)


class _CrossWorkspaceConnection:
    """Incident not found for requesting workspace."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _FakeRow({'id': 'exp-x', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-other-ws', 'include_raw_events': True}})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {query}')


# ── Test cases ────────────────────────────────────────────────────────────────

def test_A_simulator_evidence_labeled_simulator_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """A. Simulator evidence must be labeled simulator, never live_provider."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _SimulatorCompleteConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-sim')

    assert meta['evidence_source_type'] == 'simulator', 'simulator evidence must be labeled simulator'
    assert meta['evidence_source_type'] != 'live', 'simulator evidence must not be labeled live'

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['evidence_source_type'] == 'simulator'
    assert summary['source_truthfulness_status'] == 'verified_simulator'
    assert summary['source_truthfulness_status'] != 'verified_live'


def test_B_unknown_source_not_treated_as_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """B. Unknown evidence source must not be treated as live_provider."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _UnknownSourceConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    assert meta['evidence_source_type'] == 'unknown'
    assert meta['evidence_source_type'] != 'live'

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['source_truthfulness_status'] == 'unknown'
    assert summary['source_truthfulness_status'] != 'verified_live'


def test_C_missing_telemetry_in_unavailable_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """C. Missing telemetry must appear in unavailable_sections."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _MissingTelemetryConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert 'telemetry' in summary['unavailable_sections'], \
        'Missing telemetry must be listed in unavailable_sections'
    assert 'telemetry' not in summary['available_sections']


def test_D_missing_response_action_in_unavailable_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """D. Missing response action must appear in unavailable_sections."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _MissingResponseActionsConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert 'response_action' in summary['unavailable_sections'], \
        'Missing response_action must be listed in unavailable_sections'


def test_E_partial_package_has_package_status_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """E. Partial package (missing response actions) must have package_status=partial."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _MissingResponseActionsConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    assert meta['export_status'] == 'partial'

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['package_status'] == 'partial'


def test_F_no_usable_evidence_has_package_status_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """F. No usable evidence must have package_status=blocked."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _NoEvidenceConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    assert meta['export_status'] == 'incomplete'

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['package_status'] == 'blocked'


def test_G_complete_package_status_only_when_all_sections_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """G. package_status=complete only when all required sections exist."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _LiveCompleteConnection()
    meta = pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    assert meta['export_status'] == 'complete'
    assert meta['missing_sections'] == []

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['package_status'] == 'complete'
    assert summary['chain_complete'] is True


def test_H_customer_summary_contains_simulator_limitation(monkeypatch: pytest.MonkeyPatch) -> None:
    """H. customer_summary must mention simulator when simulator evidence is used."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _SimulatorCompleteConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-sim')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    cs = summary['customer_summary']

    assert 'source_note' in cs
    assert 'simulator' in cs['source_note'].lower(), \
        'source_note must mention simulator when simulator evidence is used'
    assert any('simulator' in lim.lower() for lim in cs.get('limitations', [])), \
        'limitations must mention simulator source'


def test_I_customer_summary_missing_live_provider_limitation(monkeypatch: pytest.MonkeyPatch) -> None:
    """I. customer_summary must mention missing live-provider when live evidence unavailable."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _NoEvidenceConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    cs = summary['customer_summary']

    assert 'source_note' in cs
    assert 'limitations' in cs
    assert any('live' in lim.lower() for lim in cs['limitations']), \
        'limitations must mention missing live provider evidence'


def test_J_export_json_does_not_include_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """J. Export JSON must not contain secret-like values in any field."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _SecretInMetadataConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    raw_content = fake_storage.content.decode('utf-8')
    assert 'sk-secret-value' not in raw_content, \
        'Raw secret value must not appear in export output'


def test_K_redactions_applied_true_when_sensitive_fields_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """K. redactions_applied must be true when sensitive fields were removed."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _SecretInMetadataConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['redactions_applied'] is True, \
        'redactions_applied must be True when secret-like fields are removed'


def test_L_workspace_isolation_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """L. Export generation must enforce workspace isolation — cross-workspace incident rejected."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _CrossWorkspaceConnection()
    with pytest.raises(HTTPException) as exc_info:
        pilot._generate_export_artifact(connection, workspace_id='ws-attacker', export_id='exp-x')
    assert exc_info.value.status_code == 404


def test_M_section_statuses_include_all_required_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """M. section_statuses must include all required section names."""
    required_sections = {
        'telemetry', 'detection', 'alert', 'incident', 'response_action',
        'asset_context', 'target_context', 'provider_context', 'export_metadata',
    }
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _LiveCompleteConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    section_names = {s['section_name'] for s in summary['section_statuses']}
    for required in required_sections:
        assert required in section_names, f'section_statuses must include section: {required}'


def test_N_summary_contains_all_required_metadata_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """N. summary.json must contain all required package metadata fields (schema 1.1)."""
    required_fields = {
        'schema_version', 'export_id', 'generated_at', 'generated_by',
        'workspace_id', 'incident_id',
        'export_status', 'package_status', 'export_format_version',
        'evidence_source_type', 'evidence_source',
        'source_truthfulness_status', 'source_truthfulness_reason',
        'missing_sections', 'unavailable_sections', 'available_sections', 'section_statuses',
        'warnings', 'redactions_applied', 'chain_complete', 'customer_summary',
    }
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _LiveCompleteConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    for field in required_fields:
        assert field in summary, f'summary.json missing required field: {field}'

    assert summary['schema_version'] == '1.1'
    assert summary['export_id'] == 'exp-live'
    assert summary['workspace_id'] == 'ws-live'


def test_redact_secret_fields_helper_removes_known_patterns() -> None:
    """Direct unit test for _redact_secret_fields helper."""
    data = {
        'api_key': 'sk-12345',
        'secret_key': 'secret-value',
        'webhook_secret': 'whsec_abc',
        'smtp_password': 'pass123',
        'database_url': 'postgresql://user:pass@host/db',
        'auth_token': 'bearer abc123',
        'safe_field': 'keep-me',
        'workspace_id': 'ws-safe-to-keep',
        'nested': {
            'bearer': 'token-value',
            'result': 'ok',
        },
    }
    cleaned, redacted = pilot._redact_secret_fields(data)
    assert redacted is True
    assert cleaned['api_key'] == '[REDACTED]'
    assert cleaned['secret_key'] == '[REDACTED]'
    assert cleaned['webhook_secret'] == '[REDACTED]'
    assert cleaned['smtp_password'] == '[REDACTED]'
    assert cleaned['database_url'] == '[REDACTED]'
    assert cleaned['auth_token'] == '[REDACTED]'
    assert cleaned['safe_field'] == 'keep-me'
    assert cleaned['workspace_id'] == 'ws-safe-to-keep'
    assert cleaned['nested']['bearer'] == '[REDACTED]'
    assert cleaned['nested']['result'] == 'ok'


def test_redact_secret_fields_no_redaction_when_clean() -> None:
    """_redact_secret_fields must return redacted=False when no secrets present."""
    data = {
        'workspace_id': 'ws-1',
        'incident_id': 'inc-1',
        'alert_id': 'alert-1',
        'export_status': 'complete',
    }
    cleaned, redacted = pilot._redact_secret_fields(data)
    assert redacted is False
    assert cleaned == data


def test_build_customer_export_summary_simulator_source() -> None:
    """_build_customer_export_summary must flag simulator as non-live in source_note."""
    summary = pilot._build_customer_export_summary(
        export_status='complete',
        evidence_source_type='simulator',
        missing_sections=[],
        alert_count=2,
        detection_count=2,
        action_count=1,
        metric_count=3,
    )
    assert 'simulator' in summary['source_note'].lower()
    assert any('simulator' in lim.lower() for lim in summary['limitations'])


def test_build_customer_export_summary_complete_live_no_limitations() -> None:
    """Complete live package should have no source limitations."""
    summary = pilot._build_customer_export_summary(
        export_status='complete',
        evidence_source_type='live',
        missing_sections=[],
        alert_count=1,
        detection_count=1,
        action_count=1,
        metric_count=2,
    )
    assert 'live' in summary['source_note'].lower()
    assert summary['limitations'] == []
    assert 'complete' in summary['headline'].lower()


def test_build_customer_export_summary_missing_sections_listed() -> None:
    """Missing sections must appear in limitations."""
    summary = pilot._build_customer_export_summary(
        export_status='partial',
        evidence_source_type='live',
        missing_sections=['response_actions', 'audit_log'],
        alert_count=2,
        detection_count=1,
        action_count=0,
        metric_count=2,
    )
    assert any('response_actions' in lim for lim in summary['limitations'])
    assert any('audit_log' in lim for lim in summary['limitations'])
    assert 'partial' in summary['headline'].lower()


# ── Session 12 Follow-Up: Canonical evidence_source field tests ───────────────

_CANONICAL_EVIDENCE_SOURCE_ENUM = frozenset({'live_provider', 'simulator', 'fixture', 'unavailable', 'unknown'})


def test_canonical_A_export_includes_evidence_source_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """A. Every proof bundle summary.json must include the canonical evidence_source field."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _LiveCompleteConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert 'evidence_source' in summary, 'summary.json must include canonical evidence_source field'


def test_canonical_B_legacy_live_maps_to_live_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """B. Legacy evidence_source_type='live' must map to canonical evidence_source='live_provider'."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _LiveCompleteConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['evidence_source_type'] == 'live', 'legacy field must remain live for backward compat'
    assert summary['evidence_source'] == 'live_provider', 'canonical field must be live_provider'


def test_canonical_C_simulator_maps_to_simulator_not_live_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """C. Simulator source must map to evidence_source='simulator' and never 'live_provider'."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _SimulatorCompleteConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-sim')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['evidence_source'] == 'simulator', 'simulator evidence must map to canonical simulator'
    assert summary['evidence_source'] != 'live_provider', 'simulator must never become live_provider'


def test_canonical_D_unknown_source_maps_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """D. Unknown or missing evidence source must map to evidence_source='unknown'."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _UnknownSourceConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['evidence_source'] == 'unknown', 'unrecognized source must fail closed to unknown'
    assert summary['evidence_source'] != 'live_provider'


def test_canonical_E_evidence_source_value_is_valid_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    """E. Canonical evidence_source must be one of the allowed enum values in all cases."""
    cases = [
        (_LiveCompleteConnection(), 'ws-live', 'exp-live'),
        (_SimulatorCompleteConnection(), 'ws-1', 'exp-sim'),
        (_UnknownSourceConnection(), 'ws-live', 'exp-live'),
        (_NoEvidenceConnection(), 'ws-live', 'exp-live'),
    ]
    for conn, ws, exp in cases:
        fake_storage = _FakeStorage()
        monkeypatch.setattr(pilot, 'load_export_storage', lambda _s=fake_storage: _s)
        pilot._generate_export_artifact(conn, workspace_id=ws, export_id=exp)
        payload = json.loads(fake_storage.content.decode('utf-8'))
        summary = payload['rows'][0]['summary.json']
        assert summary['evidence_source'] in _CANONICAL_EVIDENCE_SOURCE_ENUM, (
            f"evidence_source={summary['evidence_source']!r} is not a valid canonical enum value"
        )


def test_canonical_F_truthfulness_status_consistent_with_canonical_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """F. source_truthfulness_status must stay consistent with canonical evidence_source."""
    expected_truthfulness = {
        'live_provider': 'verified_live',
        'simulator': 'verified_simulator',
        'fixture': 'fixture_only',
        'unavailable': 'unavailable',
        'unknown': 'unknown',
    }
    cases = [
        (_LiveCompleteConnection(), 'ws-live', 'exp-live'),
        (_SimulatorCompleteConnection(), 'ws-1', 'exp-sim'),
        (_UnknownSourceConnection(), 'ws-live', 'exp-live'),
    ]
    for conn, ws, exp in cases:
        fake_storage = _FakeStorage()
        monkeypatch.setattr(pilot, 'load_export_storage', lambda _s=fake_storage: _s)
        pilot._generate_export_artifact(conn, workspace_id=ws, export_id=exp)
        payload = json.loads(fake_storage.content.decode('utf-8'))
        summary = payload['rows'][0]['summary.json']
        src = summary['evidence_source']
        want = expected_truthfulness.get(src)
        if want:
            assert summary['source_truthfulness_status'] == want, (
                f"For evidence_source={src!r}, expected source_truthfulness_status={want!r}, "
                f"got {summary['source_truthfulness_status']!r}"
            )


def test_normalize_evidence_source_helper() -> None:
    """Direct unit test for normalize_evidence_source helper."""
    assert pilot.normalize_evidence_source('live') == 'live_provider'
    assert pilot.normalize_evidence_source('live_provider') == 'live_provider'
    assert pilot.normalize_evidence_source('simulator') == 'simulator'
    assert pilot.normalize_evidence_source('simulation') == 'simulator'
    assert pilot.normalize_evidence_source('guided_simulator') == 'simulator'
    assert pilot.normalize_evidence_source('fixture') == 'fixture'
    assert pilot.normalize_evidence_source('test_fixture') == 'fixture'
    assert pilot.normalize_evidence_source('unavailable') == 'unavailable'
    assert pilot.normalize_evidence_source('unknown') == 'unknown'
    assert pilot.normalize_evidence_source(None) == 'unknown'
    assert pilot.normalize_evidence_source('') == 'unknown'
    assert pilot.normalize_evidence_source('custom_source_xyz') == 'unknown'
    assert pilot.normalize_evidence_source('missing') == 'unknown'


# ── Session 12 Hardening: Fixture connection + stricter tests ─────────────────

class _FixtureCompleteConnection(_LiveCompleteConnection):
    """All sections present, fixture evidence source."""

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-fix-1', 'severity': 'low', 'source': 'fixture', 'target_id': 'target-1'}])
        if 'FROM detections' in normalized and 'linked_alert_id = ANY' in normalized:
            return _FakeRow([{
                'id': 'det-fix-1', 'detection_type': 'anomaly', 'severity': 'low',
                'confidence': 0.6, 'evidence_source': 'test_fixture', 'status': 'open',
                'detected_at': '2026-03-01T00:01:00Z', 'title': 'Fixture detection',
            }])
        return super().execute(query, params)


_CANONICAL_EVIDENCE_SOURCE_ENUM_HARDENING = frozenset({
    'live_provider', 'simulator', 'fixture', 'unavailable', 'unknown',
})

_FORBIDDEN_CUSTOMER_CLAIMS = [
    'broad paid saas ready',
    'enterprise ready',
    'audit certified',
    'regulatory compliant',
]


def test_hardening_A_complete_impossible_when_evidence_source_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A. package_status cannot be complete when evidence_source is unknown."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_UnknownSourceConnection(), workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['evidence_source'] == 'unknown'
    assert summary['package_status'] != 'complete', (
        f"package_status must not be complete when evidence_source is unknown, "
        f"got package_status={summary['package_status']!r}"
    )


def test_hardening_B_complete_impossible_when_truthfulness_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """B. package_status cannot be complete when source_truthfulness_status is unknown."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_UnknownSourceConnection(), workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['source_truthfulness_status'] == 'unknown'
    assert summary['package_status'] != 'complete', (
        f"package_status must not be complete when source_truthfulness_status is unknown, "
        f"got package_status={summary['package_status']!r}"
    )


def test_hardening_C_simulator_customer_summary_not_live_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """C. Simulator package customer_summary must explicitly say it is not live-provider proof."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_SimulatorCompleteConnection(), workspace_id='ws-1', export_id='exp-sim')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    cs = payload['rows'][0]['summary.json']['customer_summary']
    source_note = cs.get('source_note', '').lower()
    assert 'not live-provider proof' in source_note, (
        f"simulator source_note must say 'not live-provider proof', got: {cs.get('source_note')!r}"
    )


def test_hardening_D_fixture_customer_summary_not_live_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """D. Fixture package customer_summary must say it is not live-provider proof."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_FixtureCompleteConnection(), workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['evidence_source'] == 'fixture', (
        f"expected evidence_source='fixture', got {summary['evidence_source']!r}"
    )
    cs = summary['customer_summary']
    source_note = cs.get('source_note', '').lower()
    assert 'not live-provider proof' in source_note, (
        f"fixture source_note must say 'not live-provider proof', got: {cs.get('source_note')!r}"
    )


def test_hardening_E_unknown_source_customer_summary_warns_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """E. Unknown source customer_summary must warn it should not be treated as live-provider proof."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_UnknownSourceConnection(), workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    cs = payload['rows'][0]['summary.json']['customer_summary']
    source_note = cs.get('source_note', '').lower()
    assert 'live-provider proof' in source_note, (
        f"unknown source_note must warn about live-provider proof, got: {cs.get('source_note')!r}"
    )


def test_hardening_F_blocked_when_no_usable_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """F. package_status must be blocked when no usable evidence sections exist."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_NoEvidenceConnection(), workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert summary['package_status'] == 'blocked', (
        f"expected package_status='blocked' with no evidence, got {summary['package_status']!r}"
    )
    assert len(summary.get('unavailable_sections', [])) > 0, (
        'blocked package must list unavailable sections'
    )


def test_hardening_G_no_complete_without_response_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """G. package_status must not be complete if response_action section is missing."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_MissingResponseActionsConnection(), workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert 'response_action' in summary['unavailable_sections'], (
        'response_action must be in unavailable_sections when missing'
    )
    assert summary['package_status'] != 'complete', (
        f"package_status must not be complete when response_action is missing, "
        f"got {summary['package_status']!r}"
    )


def test_hardening_H_no_complete_without_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """H. package_status must not be complete if telemetry section is missing."""
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    pilot._generate_export_artifact(_MissingTelemetryConnection(), workspace_id='ws-live', export_id='exp-live')

    payload = json.loads(fake_storage.content.decode('utf-8'))
    summary = payload['rows'][0]['summary.json']
    assert 'telemetry' in summary['unavailable_sections'], (
        'telemetry must be in unavailable_sections when missing'
    )
    assert summary['package_status'] != 'complete', (
        f"package_status must not be complete when telemetry is missing, "
        f"got {summary['package_status']!r}"
    )


def test_hardening_I_customer_summary_no_forbidden_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """I. customer_summary must never contain forbidden overclaiming phrases across all source types."""
    cases = [
        (_LiveCompleteConnection(), 'ws-live', 'exp-live'),
        (_SimulatorCompleteConnection(), 'ws-1', 'exp-sim'),
        (_UnknownSourceConnection(), 'ws-live', 'exp-live'),
        (_NoEvidenceConnection(), 'ws-live', 'exp-live'),
        (_MissingResponseActionsConnection(), 'ws-live', 'exp-live'),
        (_FixtureCompleteConnection(), 'ws-live', 'exp-live'),
    ]
    for conn, ws, exp in cases:
        fake_storage = _FakeStorage()
        monkeypatch.setattr(pilot, 'load_export_storage', lambda _s=fake_storage: _s)
        pilot._generate_export_artifact(conn, workspace_id=ws, export_id=exp)
        payload = json.loads(fake_storage.content.decode('utf-8'))
        cs = payload['rows'][0]['summary.json']['customer_summary']
        all_text = ' '.join([
            cs.get('headline', ''),
            cs.get('what_happened', ''),
            cs.get('why_it_matters', ''),
            cs.get('source_note', ''),
            ' '.join(cs.get('limitations', [])),
        ]).lower()
        for forbidden in _FORBIDDEN_CUSTOMER_CLAIMS:
            assert forbidden not in all_text, (
                f"customer_summary contains forbidden claim {forbidden!r} "
                f"for {conn.__class__.__name__}"
            )


def test_hardening_J_canonical_evidence_source_valid_enum_all_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    """J. canonical evidence_source must always be a valid enum value across all connection types."""
    cases = [
        (_LiveCompleteConnection(), 'ws-live', 'exp-live'),
        (_SimulatorCompleteConnection(), 'ws-1', 'exp-sim'),
        (_UnknownSourceConnection(), 'ws-live', 'exp-live'),
        (_NoEvidenceConnection(), 'ws-live', 'exp-live'),
        (_FixtureCompleteConnection(), 'ws-live', 'exp-live'),
        (_MissingTelemetryConnection(), 'ws-live', 'exp-live'),
        (_MissingResponseActionsConnection(), 'ws-live', 'exp-live'),
    ]
    for conn, ws, exp in cases:
        fake_storage = _FakeStorage()
        monkeypatch.setattr(pilot, 'load_export_storage', lambda _s=fake_storage: _s)
        pilot._generate_export_artifact(conn, workspace_id=ws, export_id=exp)
        payload = json.loads(fake_storage.content.decode('utf-8'))
        summary = payload['rows'][0]['summary.json']
        src = summary['evidence_source']
        assert src in _CANONICAL_EVIDENCE_SOURCE_ENUM_HARDENING, (
            f"evidence_source={src!r} is not a valid canonical enum value "
            f"for {conn.__class__.__name__}"
        )
