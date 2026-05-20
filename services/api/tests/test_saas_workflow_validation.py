"""
Final SaaS workflow validation test.

Proves that Decoda RWA Guard has a real end-to-end workflow, not isolated screens.

Canonical workflow under test:
    User/Workspace
    → Protected Asset
    → Monitoring Target / System
    → Enabled Monitoring Config
    → Telemetry
    → Detection
    → Alert
    → Incident
    → Response Action
    → Evidence / Audit
    → Export / Proof Bundle

Each section below corresponds to one stage of the workflow and asserts:
  - the stage's canonical data structure is correct
  - workspace isolation is enforced
  - simulator/test evidence is never labeled as live
  - chain cross-references are truthful (null, not invented)

Run:
    python -m pytest services/api/tests/test_saas_workflow_validation.py -v
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.workspace_monitoring_summary import (
    build_runtime_setup_chain,
    build_workspace_monitoring_summary_fallback,
)
from services.api.app.production_readiness import build_production_readiness
from services.api.scripts.seed import build_realistic_demo_chain


# ── Shared helpers ─────────────────────────────────────────────────

FULL_COUNTERS = {
    'workspaces_count': 1,
    'assets_count': 1,
    'verified_assets_count': 1,
    'targets_count': 1,
    'monitored_systems_count': 1,
    'enabled_monitored_systems_count': 1,
    'detections_count': 1,
    'alerts_count': 1,
    'incidents_count': 1,
    'response_actions_count': 1,
    'evidence_count': 1,
}

FULL_TIMESTAMPS = {
    'last_heartbeat_at': '2026-05-20T10:00:00Z',
    'latest_poll_at': '2026-05-20T10:01:00Z',
    'last_telemetry_at': '2026-05-20T10:02:00Z',
}


class _Row:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


@contextmanager
def _fake_pg(conn):
    yield conn


def _bootstrap(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-test'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-test'})


# ═══════════════════════════════════════════════════════════════════
# 1. WORKFLOW STEP CHAIN (workspace → export)
# ═══════════════════════════════════════════════════════════════════

EXPECTED_STEP_ORDER = [
    'workspace_created',
    'asset_created',
    'asset_verified',
    'monitoring_target_created',
    'monitored_system_created',
    'worker_reporting',
    'telemetry_received',
    'detection_created',
    'alert_created',
    'incident_opened',
    'response_ready',
    'evidence_export_ready',
]


def test_saas_workflow_step_order_matches_canonical_path() -> None:
    """build_runtime_setup_chain must expose the canonical 12-step SaaS workflow in correct order."""
    chain = build_runtime_setup_chain(counters=FULL_COUNTERS, timestamps=FULL_TIMESTAMPS)
    step_ids = [s['id'] for s in chain['steps']]
    assert step_ids == EXPECTED_STEP_ORDER, (
        f'Step order mismatch.\nExpected: {EXPECTED_STEP_ORDER}\nActual:   {step_ids}'
    )


def test_saas_workflow_all_steps_complete_when_full_counters_and_timestamps() -> None:
    """When all counters and timestamps are set, every workflow step must be complete."""
    chain = build_runtime_setup_chain(counters=FULL_COUNTERS, timestamps=FULL_TIMESTAMPS)
    incomplete = [s for s in chain['steps'] if s['status'] != 'complete']
    assert incomplete == [], (
        f'Expected all steps complete but found incomplete: {[s["id"] for s in incomplete]}'
    )


def test_saas_workflow_steps_blocked_when_no_telemetry() -> None:
    """Steps after worker_reporting must be blocked/pending when telemetry is missing."""
    counters_no_telemetry = {**FULL_COUNTERS, 'detections_count': 0, 'alerts_count': 0,
                             'incidents_count': 0, 'response_actions_count': 0, 'evidence_count': 0}
    timestamps_no_telemetry = {**FULL_TIMESTAMPS, 'last_telemetry_at': None}

    chain = build_runtime_setup_chain(counters=counters_no_telemetry, timestamps=timestamps_no_telemetry)
    step_by_id = {s['id']: s for s in chain['steps']}

    assert step_by_id['telemetry_received']['status'] != 'complete'
    assert step_by_id['detection_created']['status'] != 'complete'
    assert step_by_id['alert_created']['status'] != 'complete'
    assert step_by_id['incident_opened']['status'] != 'complete'


def test_saas_workflow_steps_blocked_when_no_assets() -> None:
    """Steps after asset_created must be pending/blocked when no assets exist."""
    chain = build_runtime_setup_chain(
        counters={'workspaces_count': 1, 'assets_count': 0, 'verified_assets_count': 0,
                  'targets_count': 0, 'monitored_systems_count': 0, 'enabled_monitored_systems_count': 0,
                  'detections_count': 0, 'alerts_count': 0, 'incidents_count': 0,
                  'response_actions_count': 0, 'evidence_count': 0},
        timestamps={'last_heartbeat_at': None, 'latest_poll_at': None, 'last_telemetry_at': None},
    )
    step_by_id = {s['id']: s for s in chain['steps']}
    assert step_by_id['workspace_created']['status'] == 'complete'
    assert step_by_id['asset_created']['status'] != 'complete'
    assert step_by_id['monitoring_target_created']['status'] != 'complete'


def test_saas_workflow_current_step_is_first_incomplete() -> None:
    """current_step must point to the first incomplete step, not a completed one."""
    counters_only_workspace = {**{k: 0 for k in FULL_COUNTERS}, 'workspaces_count': 1}
    chain = build_runtime_setup_chain(
        counters=counters_only_workspace,
        timestamps={'last_heartbeat_at': None, 'latest_poll_at': None, 'last_telemetry_at': None},
    )
    assert chain['current_step'] == 'asset_created'


# ═══════════════════════════════════════════════════════════════════
# 2. EVIDENCE SOURCE CANONICALIZATION
#    Simulator evidence must never be labeled as live.
# ═══════════════════════════════════════════════════════════════════

SIMULATOR_SOURCES = ['simulator', 'guided_simulator', 'demo', 'synthetic', 'fallback', 'lab', 'replay']
LIVE_SOURCES = ['live', 'live_provider', 'provider', 'indexer', 'rpc', 'compliance_feed']


def test_canonicalize_evidence_source_simulator_family() -> None:
    """Simulator-family source values must canonicalize to 'simulator'."""
    for source in ['simulator', 'guided_simulator']:
        assert pilot.canonicalize_evidence_source(source) == 'simulator', (
            f'Expected "simulator" for source={source!r}'
        )


def test_canonicalize_evidence_source_live_family() -> None:
    """Live-family source values must canonicalize to 'live_provider'."""
    for source in LIVE_SOURCES:
        result = pilot.canonicalize_evidence_source(source)
        assert result == 'live_provider', (
            f'Expected "live_provider" for source={source!r}, got {result!r}'
        )


def test_canonicalize_evidence_source_none_is_not_live() -> None:
    """None/empty evidence source must not canonicalize to live."""
    for value in [None, '', '  ']:
        result = pilot.canonicalize_evidence_source(value)
        assert result != 'live_provider', (
            f'Null/empty evidence source must not become live, got {result!r}'
        )


def test_simulator_evidence_origin_label_is_simulated_not_live() -> None:
    """detection_evidence_origin_label must return SIMULATED for simulator, LIVE for live."""
    assert pilot.detection_evidence_origin_label('simulator') == 'SIMULATED EVIDENCE'
    assert pilot.detection_evidence_origin_label('live') == 'LIVE EVIDENCE'
    assert pilot.detection_evidence_origin_label('live_provider') == 'LIVE EVIDENCE'
    # guided_simulator is canonicalized upstream; the label function handles 'simulator' post-canonicalization
    assert pilot.detection_evidence_origin_label('guided_simulator') != 'LIVE EVIDENCE'


def test_simulator_evidence_never_becomes_live_label() -> None:
    """No simulator-family source value must ever produce a live evidence label."""
    for source in SIMULATOR_SOURCES:
        label = pilot.detection_evidence_origin_label(source)
        assert label != 'LIVE EVIDENCE', (
            f'Simulator source {source!r} must not produce LIVE EVIDENCE label'
        )


# ═══════════════════════════════════════════════════════════════════
# 3. DETECTION → ALERT → INCIDENT → ACTION CHAIN LINKAGE
#    IDs must reference actual objects; missing IDs must be null.
# ═══════════════════════════════════════════════════════════════════

def test_response_action_full_chain_all_ids_present() -> None:
    """_response_action_payload must propagate all chain IDs when present."""
    action = {
        'id': 'ra-final-1',
        'mode': 'simulated',
        'status': 'pending',
        'incident_id': 'inc-final-1',
        'alert_id': 'alert-final-1',
        'execution_metadata': {
            'chain_linked_ids': {
                'detection_id': 'det-final-1',
                'alert_id': 'alert-final-1',
                'incident_id': 'inc-final-1',
            },
        },
    }
    result = pilot._response_action_payload(action)
    ids = result['chain_linked_ids']
    assert ids['action_id'] == 'ra-final-1'
    assert ids['incident_id'] == 'inc-final-1'
    assert ids['alert_id'] == 'alert-final-1'
    assert ids['detection_id'] == 'det-final-1'


def test_response_action_chain_ids_null_not_invented_when_absent() -> None:
    """When chain IDs are absent, _response_action_payload must return null, not invented values."""
    action = {
        'id': 'ra-final-2',
        'mode': 'simulated',
        'status': 'pending',
        'incident_id': None,
        'alert_id': None,
        'execution_metadata': {},
    }
    result = pilot._response_action_payload(action)
    ids = result['chain_linked_ids']
    assert ids['incident_id'] is None
    assert ids['alert_id'] is None
    assert ids['detection_id'] is None
    assert ids['action_id'] == 'ra-final-2'


def test_chain_detection_id_null_not_invented_from_empty_metadata() -> None:
    """detection_id must be null when metadata does not include it, not silently filled."""
    action = {
        'id': 'ra-final-3',
        'mode': 'simulated',
        'status': 'pending',
        'incident_id': 'inc-x',
        'alert_id': 'alert-x',
        'execution_metadata': {'chain_linked_ids': {}},
    }
    result = pilot._response_action_payload(action)
    assert result['chain_linked_ids']['detection_id'] is None


# ═══════════════════════════════════════════════════════════════════
# 4. EXPORT / PROOF BUNDLE
#    Completeness status and evidence source label must be truthful.
# ═══════════════════════════════════════════════════════════════════

class _FakeStorage:
    backend_name = 'local'

    def __init__(self):
        self.content = b''

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self.content = content
        return object_key


class _FullChainConn:
    """All chain sections present with simulator evidence."""

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in q:
            return _Row(row={'id': 'exp-val-1', 'export_type': 'proof_bundle', 'format': 'json',
                             'filters': {'incident_id': 'inc-val-1', 'include_raw_events': False}})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in q:
            return _Row(row={'id': 'inc-val-1', 'workspace_id': 'ws-val', 'title': 'Test Incident',
                             'severity': 'high', 'status': 'open'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in q:
            return _Row(rows=[{'id': 'alert-val-1', 'severity': 'high', 'source': 'simulator', 'target_id': 'tgt-1'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in q:
            return _Row(rows=[{'id': 'metric-val-1', 'event_observed_at': '2026-05-01T00:00:00Z',
                               'detected_at': '2026-05-01T00:02:00Z', 'mttd_seconds': 120,
                               'evidence': {'tx_hash': '0xtest_only'}}])
        if 'FROM response_actions' in q and 'incident_id = %s' in q:
            return _Row(rows=[{'id': 'action-val-1', 'action_type': 'notify_team', 'status': 'completed',
                               'mode': 'simulated', 'execution_metadata': None,
                               'created_at': '2026-05-01T00:05:00Z', 'executed_at': None, 'rolled_back_at': None}])
        if 'FROM detections' in q and 'linked_alert_id = ANY' in q:
            return _Row(rows=[{'id': 'det-val-1', 'detection_type': 'anomaly', 'severity': 'high',
                               'confidence': 0.85, 'evidence_source': 'simulator', 'status': 'open',
                               'detected_at': '2026-05-01T00:01:00Z', 'title': 'Test anomaly'}])
        if 'FROM audit_logs' in q:
            return _Row(rows=[{'id': 'audit-val-1', 'action': 'export.generate', 'entity_type': 'export_job',
                               'entity_id': 'exp-val-1', 'metadata': None, 'created_at': '2026-05-01T00:10:00Z'}])
        if "UPDATE export_jobs SET status = 'completed'" in q:
            return _Row(row=None)
        if "UPDATE export_jobs SET status = 'failed'" in q:
            return _Row(row=None)
        raise AssertionError(f'Unexpected query in _FullChainConn: {query!r}')


class _EmptyChainConn(_FullChainConn):
    """No alerts, detections, or actions — chain is incomplete."""

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM alerts a JOIN detection_metrics dm' in q:
            return _Row(rows=[])
        if 'FROM detection_metrics WHERE workspace_id' in q:
            return _Row(rows=[])
        if 'FROM response_actions' in q and 'incident_id = %s' in q:
            return _Row(rows=[])
        if 'FROM detections' in q and 'linked_alert_id = ANY' in q:
            return _Row(rows=[])
        return super().execute(query, params)


def test_proof_bundle_simulator_evidence_labeled_simulator_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proof bundle for simulator evidence must have evidence_source_type='simulator', not 'live'."""
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage())
    meta = pilot._generate_export_artifact(_FullChainConn(), workspace_id='ws-val', export_id='exp-val-1')

    assert meta['evidence_source_type'] == 'simulator'
    assert meta['evidence_source_type'] != 'live'


def test_proof_bundle_simulator_evidence_generates_simulator_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulator evidence bundle must include a warning that it is not live proof."""
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage())
    meta = pilot._generate_export_artifact(_FullChainConn(), workspace_id='ws-val', export_id='exp-val-1')

    assert any('simulator' in w.lower() for w in meta['warnings']), (
        f'Expected simulator warning but got: {meta["warnings"]}'
    )


def test_proof_bundle_complete_chain_has_complete_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all chain sections are present, export_status must be 'complete'."""
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage())
    meta = pilot._generate_export_artifact(_FullChainConn(), workspace_id='ws-val', export_id='exp-val-1')

    assert meta['export_status'] == 'complete'
    assert meta['missing_sections'] == []


def test_proof_bundle_empty_chain_has_incomplete_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no alerts/detections/actions exist, export_status must be 'incomplete'."""
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage())
    meta = pilot._generate_export_artifact(_EmptyChainConn(), workspace_id='ws-val', export_id='exp-val-1')

    assert meta['export_status'] == 'incomplete'
    assert 'alerts' in meta['missing_sections']


def test_proof_bundle_missing_sections_are_listed_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing sections must be explicitly listed; they must not be hidden as empty lists."""
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage())
    meta = pilot._generate_export_artifact(_EmptyChainConn(), workspace_id='ws-val', export_id='exp-val-1')

    # incomplete chain: missing_sections must enumerate what is absent
    assert isinstance(meta['missing_sections'], list)
    assert len(meta['missing_sections']) > 0


def test_proof_bundle_cross_workspace_incident_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Export for an incident not belonging to requesting workspace must raise 404."""
    class _WrongWsConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in q:
                return _Row({'id': 'exp-x', 'export_type': 'proof_bundle', 'format': 'json',
                             'filters': {'incident_id': 'inc-other-ws', 'include_raw_events': False}})
            if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in q:
                return _Row(None)
            raise AssertionError(f'Unexpected: {query!r}')

    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage())
    with pytest.raises(HTTPException) as exc_info:
        pilot._generate_export_artifact(_WrongWsConn(), workspace_id='ws-attacker', export_id='exp-x')
    assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# 5. PRODUCTION READINESS GATES
#    ready_for_pilot requires full workflow facts.
#    ready_for_paid_public_launch additionally requires billing/email/live evidence.
# ═══════════════════════════════════════════════════════════════════

def _pilot_env_checks(*, db=True, auth=True) -> dict:
    return {
        'database_reachable': db,
        'auth_session_configured': auth,
        'required_env_vars_present': True,
        'app_base_url_configured': False,
        'api_url_configured': False,
        'redis_required': False,
        'redis_configured': False,
        'email_required': False,
        'email_configured': False,
        'billing_required': False,
        'billing_configured': False,
        'paid_ui_disabled': True,
    }


def _full_runtime(*, evidence_source: str = 'simulator') -> dict:
    return {
        'last_heartbeat_at': '2026-05-20T10:00:00Z',
        'latest_poll_at': '2026-05-20T10:01:00Z',
        'last_telemetry_at': '2026-05-20T10:02:00Z',
        'contradiction_flags': [],
        'evidence_source': evidence_source,
        'reporting_systems_count': 1,
    }


def _full_workflow() -> dict:
    return {
        'protected_assets_count': 1,
        'enabled_monitoring_configs_count': 1,
        'detections_count': 1,
        'alerts_count': 1,
        'incidents_count': 1,
        'response_actions_count': 1,
        'latest_detection_at': '2026-05-20T10:03:00Z',
        'latest_alert_at': '2026-05-20T10:04:00Z',
        'latest_incident_at': '2026-05-20T10:05:00Z',
        'latest_response_action_at': '2026-05-20T10:06:00Z',
    }


def _minimal_integrations() -> dict:
    return {
        'slack_status': 'not_configured',
        'webhook_status': 'not_configured',
        'delivery_log_count': 0,
        'api_keys_count': 1,
    }


def _minimal_exports() -> dict:
    return {
        'export_capability_status': 'pass',
        'latest_export_status': 'completed',
        'audit_log_available': True,
        'proof_bundle_capable': True,
        'evidence_source_status': 'simulator',
    }


def _minimal_security() -> dict:
    return {
        'readiness_access_control': True,
        'secret_redaction': True,
        'workspace_scoped': True,
    }


def test_readiness_ready_for_pilot_requires_db_auth_telemetry_assets() -> None:
    """ready_for_pilot must be False when DB or auth is missing."""
    result_no_db = build_production_readiness(
        env_checks=_pilot_env_checks(db=False),
        runtime=_full_runtime(),
        workflow=_full_workflow(),
        integrations=_minimal_integrations(),
        exports=_minimal_exports(),
        security=_minimal_security(),
    )
    assert result_no_db['ready_for_pilot'] is False
    assert 'database_unreachable' in result_no_db['blocking_reasons']

    result_no_auth = build_production_readiness(
        env_checks=_pilot_env_checks(auth=False),
        runtime=_full_runtime(),
        workflow=_full_workflow(),
        integrations=_minimal_integrations(),
        exports=_minimal_exports(),
        security=_minimal_security(),
    )
    assert result_no_auth['ready_for_pilot'] is False
    assert 'auth_session_not_configured' in result_no_auth['blocking_reasons']


def test_readiness_paid_public_launch_false_when_simulator_evidence() -> None:
    """ready_for_paid_public_launch must be False when evidence_source is simulator."""
    result = build_production_readiness(
        env_checks={
            **_pilot_env_checks(),
            'app_base_url_configured': True,
            'api_url_configured': True,
            'billing_required': True,
            'billing_configured': True,
            'email_required': True,
            'email_configured': True,
            'paid_ui_disabled': False,
        },
        runtime=_full_runtime(evidence_source='simulator'),
        workflow=_full_workflow(),
        integrations=_minimal_integrations(),
        exports={**_minimal_exports(), 'evidence_source_status': 'simulator'},
        security=_minimal_security(),
    )
    assert result['ready_for_paid_public_launch'] is False


def test_readiness_paid_public_launch_false_without_billing() -> None:
    """ready_for_paid_public_launch must be False when billing is required but not configured."""
    result = build_production_readiness(
        env_checks={
            **_pilot_env_checks(),
            'app_base_url_configured': True,
            'api_url_configured': True,
            'billing_required': True,
            'billing_configured': False,
            'paid_ui_disabled': False,
        },
        runtime=_full_runtime(evidence_source='live_provider'),
        workflow=_full_workflow(),
        integrations=_minimal_integrations(),
        exports={**_minimal_exports(), 'evidence_source_status': 'live'},
        security=_minimal_security(),
    )
    assert result['ready_for_paid_public_launch'] is False
    assert 'billing_required_not_configured' in result['blocking_reasons']


def test_readiness_result_includes_required_top_level_fields() -> None:
    """Production readiness result must include all required top-level fields."""
    result = build_production_readiness(
        env_checks=_pilot_env_checks(),
        runtime=_full_runtime(),
        workflow=_full_workflow(),
        integrations=_minimal_integrations(),
        exports=_minimal_exports(),
        security=_minimal_security(),
    )
    for field in ('generated_at', 'ready_for_pilot', 'ready_for_paid_public_launch',
                  'blocking_reasons', 'warnings', 'categories'):
        assert field in result, f'Missing required field: {field}'


def test_readiness_blocking_reasons_are_explicit_not_hidden() -> None:
    """Blockers must be listed explicitly; empty blockers means pilot-eligible."""
    result_with_blockers = build_production_readiness(
        env_checks=_pilot_env_checks(db=False),
        runtime=_full_runtime(),
        workflow=_full_workflow(),
        integrations=_minimal_integrations(),
        exports=_minimal_exports(),
        security=_minimal_security(),
    )
    assert isinstance(result_with_blockers['blocking_reasons'], list)
    assert len(result_with_blockers['blocking_reasons']) > 0


# ═══════════════════════════════════════════════════════════════════
# 6. WORKSPACE ISOLATION (negative tests)
#    Objects from another workspace must be rejected, not leaked.
# ═══════════════════════════════════════════════════════════════════

def test_get_alert_from_wrong_workspace_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alert belonging to ws-A must not be accessible when requesting with ws-B header."""
    class _WrongWsConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'SELECT * FROM alerts WHERE id = %s AND workspace_id = %s' in q:
                return _Row(row=None)  # alert not found for this workspace
            return _Row()

    _bootstrap(monkeypatch, _WrongWsConn())
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-B'})

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_alert('alert-from-ws-A', request)
    assert exc_info.value.status_code in (404, 403)


def test_list_enforcement_actions_query_scoped_to_requesting_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_enforcement_actions must always filter response_actions by requesting workspace."""
    executed: list[tuple[str, object]] = []
    target_workspace = 'ws-isolated'

    class _Conn:
        def execute(self, query, params=None):
            executed.append((' '.join(str(query).split()), params))
            return _Row(rows=[])

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(_Conn()))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-test'})
    # resolve_workspace forwards the header workspace so the query uses it
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda *_a, **_k: {'workspace_id': target_workspace})

    request = SimpleNamespace(headers={'x-workspace-id': target_workspace})
    pilot.list_enforcement_actions(request)

    action_queries = [(q, p) for q, p in executed if 'FROM response_actions' in q]
    assert len(action_queries) >= 1, 'No response_actions query was made'
    for q, p in action_queries:
        assert 'workspace_id = %s' in q, f'Query missing workspace_id filter: {q}'
        assert p[0] == target_workspace, (
            f'Expected workspace_id param {target_workspace!r} but got {p[0]!r}'
        )


# ═══════════════════════════════════════════════════════════════════
# 7. REALISTIC DEMO CHAIN (build_realistic_demo_chain)
#    Full chain summary must cover every workflow stage.
#    Simulator chains must never be marked production-eligible.
# ═══════════════════════════════════════════════════════════════════

_BOOTSTRAP_IDS = {
    'workspace_id': 'ws-validate',
    'asset_id': 'asset-validate',
    'target_id': 'target-validate',
    'monitored_system_id': 'sys-validate',
    'monitoring_config_id': 'cfg-validate',
    'monitoring_heartbeat_id': 'hb-validate',
    'monitoring_poll_id': 'poll-validate',
    'telemetry_event_id': 'tel-validate',
    'detection_event_id': 'de-validate',
    'detection_id': 'det-validate',
    'alert_id': 'alert-validate',
    'incident_id': 'inc-validate',
    'governance_action_id': 'gov-validate',
    'response_action_history_id': 'hist-validate',
    'evidence_source': 'simulator',
    'telemetry_event_observed_at': '2026-05-20T10:00:00Z',
}

REQUIRED_CHAIN_STEP_NAMES = {
    'protected_asset',
    'monitored_target',
    'monitoring_config',
    'heartbeat',
    'poll',
    'telemetry_event',
    'detection',
    'alert',
    'incident',
    'governance_action',
    'action_history',
}


def test_realistic_demo_chain_all_workflow_steps_present() -> None:
    """build_realistic_demo_chain must produce a step for every SaaS workflow stage."""
    chain = build_realistic_demo_chain(_BOOTSTRAP_IDS)
    step_names = {s['name'] for s in chain['steps']}
    missing = REQUIRED_CHAIN_STEP_NAMES - step_names
    assert missing == set(), f'Missing chain steps: {missing}'


def test_realistic_demo_chain_all_steps_reference_same_workspace() -> None:
    """Every chain step ID must come from the same bootstrap context (no cross-tenant mixing)."""
    chain = build_realistic_demo_chain(_BOOTSTRAP_IDS)
    assert chain['workspace_id'] == 'ws-validate'
    assert chain['asset_id'] == 'asset-validate'
    assert chain['target_id'] == 'target-validate'
    # spot-check step IDs
    step_by_name = {s['name']: s for s in chain['steps']}
    assert step_by_name['protected_asset']['id'] == 'asset-validate'
    assert step_by_name['monitored_target']['id'] == 'target-validate'
    assert step_by_name['telemetry_event']['id'] == 'tel-validate'
    assert step_by_name['incident']['id'] == 'inc-validate'


def test_realistic_demo_chain_simulator_source_not_production_eligible() -> None:
    """Simulator-sourced demo chain must never be marked production_claim_eligible."""
    chain = build_realistic_demo_chain(_BOOTSTRAP_IDS)
    assert chain['production_claim_eligible'] is False
    assert chain['runtime_status_evidence_origin'] == 'simulator'


def test_realistic_demo_chain_simulator_label_is_not_live() -> None:
    """ui_evidence_origin_label must not contain 'live' for a simulator chain."""
    chain = build_realistic_demo_chain(_BOOTSTRAP_IDS)
    label = chain.get('ui_evidence_origin_label', '')
    assert 'live' not in label.lower() or 'not live' in label.lower(), (
        f'Simulator chain must not claim live evidence: {label!r}'
    )


def test_realistic_demo_chain_summary_string_covers_full_path() -> None:
    """chain_summary must describe the full path from asset to governance action."""
    chain = build_realistic_demo_chain(_BOOTSTRAP_IDS)
    summary = chain['chain_summary']
    for segment in ('protected_asset', 'telemetry', 'detection', 'alert', 'incident', 'governance_action'):
        assert segment in summary, f'chain_summary missing segment: {segment!r}\nGot: {summary!r}'


# ═══════════════════════════════════════════════════════════════════
# 8. WORKFLOW FALLBACK SUMMARY (offline state is truthful)
# ═══════════════════════════════════════════════════════════════════

def test_monitoring_summary_fallback_is_not_healthy() -> None:
    """Fallback summary (no live data) must not claim 'live' or 'healthy' monitoring status."""
    summary = build_workspace_monitoring_summary_fallback(status_reason='test')
    assert summary['runtime_status'] != 'live'
    assert summary['monitoring_status'] not in ('live', 'healthy')
    assert summary['evidence_source_summary'] in ('none', 'simulator', 'unavailable', 'missing')


def test_monitoring_summary_fallback_has_zero_reporting_systems() -> None:
    """Fallback summary must report zero reporting/monitored systems, not fabricated counts."""
    summary = build_workspace_monitoring_summary_fallback(status_reason='test')
    assert summary.get('reporting_systems_count', 0) == 0
    assert summary.get('monitored_systems_count', 0) == 0
