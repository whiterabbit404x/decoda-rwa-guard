"""P0 evidence integrity tests — Session 14 audit remediation.

Covers:
  1. Proof chain rejects simulator in production (APP_ENV=production + LIVE_MODE=false)
  2. Fallback evidence cannot become verified live evidence
  3. Degraded evidence is labelled (simulator/fallback bundles carry watermarks)
  4. Signed proof bundle includes evidence_state / verified_live / exportable_as_verified
  5. Demo seed data cannot enter production evidence (APP_ENV=production gate)
  6. Existing truthfulness: simulated evidence cannot pass live evidence-ready
"""
from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Minimal fastapi/starlette stub so modules load without the real package
# ---------------------------------------------------------------------------

def _install_fastapi_stubs() -> None:
    if 'fastapi' in sys.modules:
        return

    class _HTTPException(Exception):
        def __init__(self, status_code: int = 400, detail: str = '') -> None:
            self.status_code = status_code
            self.detail = detail

    class _status:
        HTTP_400_BAD_REQUEST = 400
        HTTP_401_UNAUTHORIZED = 401
        HTTP_402_PAYMENT_REQUIRED = 402
        HTTP_403_FORBIDDEN = 403
        HTTP_404_NOT_FOUND = 404
        HTTP_409_CONFLICT = 409
        HTTP_422_UNPROCESSABLE_ENTITY = 422
        HTTP_500_INTERNAL_SERVER_ERROR = 500
        HTTP_503_SERVICE_UNAVAILABLE = 503

    fastapi_mod = ModuleType('fastapi')
    fastapi_mod.HTTPException = _HTTPException  # type: ignore[attr-defined]
    fastapi_mod.FastAPI = MagicMock()  # type: ignore[attr-defined]
    fastapi_mod.Request = MagicMock()  # type: ignore[attr-defined]
    fastapi_mod.status = _status  # type: ignore[attr-defined]
    fastapi_mod.APIRouter = MagicMock()  # type: ignore[attr-defined]
    fastapi_mod.Depends = MagicMock()  # type: ignore[attr-defined]

    # All third-party packages that pilot.py / main.py transitively need
    _stub_names = [
        'fastapi.responses', 'fastapi.middleware', 'fastapi.middleware.cors',
        'starlette', 'starlette.status',
        'redis', 'redis.asyncio',
        'psycopg', 'psycopg.rows', 'psycopg_pool',
        'stripe', 'paddle_billing', 'paddle_billing.models',
    ]
    for name in _stub_names:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

    # Patch starlette.status with real HTTP codes so comparisons work
    starlette_status_mod = sys.modules['starlette.status']
    for attr in dir(_status):
        if not attr.startswith('__'):
            setattr(starlette_status_mod, attr, getattr(_status, attr))

    sys.modules['fastapi'] = fastapi_mod


_install_fastapi_stubs()

# Now safe to import app modules that depend on fastapi
from services.api.app._proof_chain_worker import _ensure_workspace_live_rpc_proof_chain  # noqa: E402
from services.api.app.runtime_truthfulness import (  # noqa: E402
    classify_evidence_state,
    validate_evidence_for_live_proof,
)

# pilot and main need lazy import inside tests to pick up monkeypatched env vars
# and to avoid issues with module-level execution in main.py


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

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


class _ProofBundleConnection:
    """Minimal DB connection stub for proof bundle generation tests."""

    def __init__(self, source: str):
        self._source = source

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in q:
            return _FakeRow({
                'id': 'exp-t1',
                'export_type': 'proof_bundle',
                'format': 'json',
                'filters': {'incident_id': 'inc-t1', 'include_raw_events': False},
                'requested_by_user_id': 'user-t1',
            })
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in q:
            return _FakeRow({'id': 'inc-t1', 'workspace_id': 'ws-t1', 'title': 'Test Incident', 'severity': 'high', 'status': 'open'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in q:
            return _FakeRow([{'id': 'alert-t1', 'severity': 'high', 'source': self._source, 'target_id': 'target-t1'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in q:
            return _FakeRow([{'id': 'metric-t1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {'tx_hash': '0xtest'}}])
        if 'FROM response_actions' in q and 'incident_id = %s' in q:
            return _FakeRow([{'id': 'action-t1', 'action_type': 'freeze_wallet', 'status': 'executed', 'mode': 'live', 'execution_metadata': None, 'created_at': '2026-01-01T00:10:00Z', 'executed_at': None, 'rolled_back_at': None}])
        if 'FROM detections' in q and 'linked_alert_id = ANY' in q:
            return _FakeRow([{'id': 'det-t1', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.9, 'evidence_source': self._source, 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Test anomaly'}])
        if 'FROM audit_logs' in q and 'row_hash IS NOT NULL' in q:
            return _FakeRow(None)
        if 'FROM audit_logs' in q:
            return _FakeRow([])
        if "UPDATE export_jobs SET status = 'completed'" in q:
            return _FakeRow(None)
        if "UPDATE export_jobs SET status = 'failed'" in q:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {query}')


class _FakeProofChainConnection:
    """Stub that never returns rows (no live evidence in DB)."""

    def execute(self, query, params=None):
        return _FakeRow(None)


# ---------------------------------------------------------------------------
# 1. Proof chain rejects simulator in production
# ---------------------------------------------------------------------------

def test_proof_chain_rejects_simulator_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV=production + LIVE_MODE=false → explicit simulator rejection."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE', 'false')
    result = _ensure_workspace_live_rpc_proof_chain(_FakeProofChainConnection(), workspace_id='ws-prod-1')
    assert result['created'] is False
    assert result['reason'] == 'simulator_rejected_in_production'
    assert 'Cannot produce verified live proof bundle' in result['error']
    assert result['verified_live'] is False
    assert result['evidence_state'] == 'SIMULATOR_EVIDENCE'


def test_proof_chain_production_live_mode_passes_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV=production + LIVE_MODE=true → gate passes, falls to DB query."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE', 'true')
    result = _ensure_workspace_live_rpc_proof_chain(_FakeProofChainConnection(), workspace_id='ws-prod-2')
    assert result['reason'] == 'no_qualifying_target_detector_chain'


def test_proof_chain_dev_with_live_mode_false_not_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV=local + LIVE_MODE=false → not rejected (dev is allowed)."""
    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.setenv('LIVE_MODE', 'false')
    result = _ensure_workspace_live_rpc_proof_chain(_FakeProofChainConnection(), workspace_id='ws-dev-1')
    assert result['reason'] == 'no_qualifying_target_detector_chain'


# ---------------------------------------------------------------------------
# 2. Fallback evidence provenance fields
# ---------------------------------------------------------------------------

def _get_main_module():
    """Import main lazily so fastapi stub is already in sys.modules."""
    import importlib
    if 'services.api.app.main' in sys.modules:
        return sys.modules['services.api.app.main']
    return importlib.import_module('services.api.app.main')


def test_fallback_compliance_dashboard_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_compliance_dashboard()
    assert data['source'] == 'fallback'
    assert data['degraded'] is True
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False
    assert 'reason' in data


def test_fallback_transfer_screening_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_transfer_screening({})
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False


def test_fallback_residency_screening_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_residency_screening({'approved_regions': [], 'restricted_regions': []})
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False


def test_fallback_governance_action_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_governance_action({'action_type': 'freeze', 'target_id': 'wallet-1'})
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False


def test_fallback_resilience_dashboard_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_resilience_dashboard()
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False


def test_fallback_reconcile_state_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_reconcile_state({'expected_total_supply': 1000, 'ledgers': []})
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False


def test_fallback_backstop_evaluate_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_backstop_evaluate({'volatility_score': 0})
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False


def test_fallback_incident_record_has_provenance_fields() -> None:
    m = _get_main_module()
    data = m.fallback_incident_record({'event_type': 'test'})
    assert data['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert data['verified_live'] is False
    assert data['exportable_as_verified'] is False


# ---------------------------------------------------------------------------
# 3. Degraded evidence is labelled in proof bundles
# ---------------------------------------------------------------------------

def _get_pilot():
    import importlib
    return importlib.import_module('services.api.app.pilot')


def test_simulator_bundle_carries_watermark_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulator evidence → bundle warnings include non-live watermark."""
    monkeypatch.setenv('APP_MODE', 'local')
    p = _get_pilot()
    fake_storage = _FakeStorage()
    monkeypatch.setattr(p, 'load_export_storage', lambda: fake_storage)
    meta = p._generate_export_artifact(
        _ProofBundleConnection('simulator'), workspace_id='ws-1', export_id='exp-t1'
    )
    assert any('NOT LIVE EVIDENCE' in w or 'not constitute live' in w for w in meta['warnings'])


def test_fallback_bundle_carries_watermark_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """fallback source → bundle warnings include non-live watermark."""
    monkeypatch.setenv('APP_MODE', 'local')
    p = _get_pilot()
    fake_storage = _FakeStorage()
    monkeypatch.setattr(p, 'load_export_storage', lambda: fake_storage)
    meta = p._generate_export_artifact(
        _ProofBundleConnection('fallback'), workspace_id='ws-1', export_id='exp-t1'
    )
    assert any('NOT LIVE EVIDENCE' in w or 'fallback' in w.lower() for w in meta['warnings'])


def test_simulator_bundle_package_status_is_degraded_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulator evidence → package_status is degraded_diagnostic, never complete."""
    monkeypatch.setenv('APP_MODE', 'local')
    p = _get_pilot()
    fake_storage = _FakeStorage()
    monkeypatch.setattr(p, 'load_export_storage', lambda: fake_storage)
    p._generate_export_artifact(
        _ProofBundleConnection('simulator'), workspace_id='ws-1', export_id='exp-t1'
    )
    bundle = json.loads(fake_storage.content)
    summary = bundle['rows'][0]['summary.json']
    assert summary['package_status'] == 'degraded_diagnostic', (
        f"Expected 'degraded_diagnostic', got {summary['package_status']!r}"
    )


def test_production_mode_non_live_bundle_has_production_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """In production mode, non-live evidence carries explicit production rejection warning."""
    monkeypatch.setenv('APP_MODE', 'production')
    # Provide a strong signing secret so the production signing check passes
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'x' * 40)
    import importlib
    from services.api.app import evidence_signing
    importlib.reload(evidence_signing)
    p = _get_pilot()
    importlib.reload(p)
    fake_storage = _FakeStorage()
    monkeypatch.setattr(p, 'load_export_storage', lambda: fake_storage)
    meta = p._generate_export_artifact(
        _ProofBundleConnection('simulator'), workspace_id='ws-1', export_id='exp-t1'
    )
    assert any('PRODUCTION' in w or 'Cannot produce verified live' in w for w in meta['warnings'])
    # Restore local mode
    monkeypatch.setenv('APP_MODE', 'local')
    importlib.reload(evidence_signing)


# ---------------------------------------------------------------------------
# 4. Signed proof bundle includes evidence provenance
# ---------------------------------------------------------------------------

def test_proof_bundle_summary_live_evidence_has_real_evidence_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live evidence → summary has evidence_state=REAL_EVIDENCE, verified_live=True."""
    p = _get_pilot()
    fake_storage = _FakeStorage()
    monkeypatch.setattr(p, 'load_export_storage', lambda: fake_storage)
    p._generate_export_artifact(
        _ProofBundleConnection('live_provider'), workspace_id='ws-1', export_id='exp-t1'
    )
    bundle = json.loads(fake_storage.content)
    summary = bundle['rows'][0]['summary.json']
    assert summary['evidence_state'] == 'REAL_EVIDENCE'
    assert summary['verified_live'] is True
    assert summary['exportable_as_verified'] is True


def test_proof_bundle_summary_simulator_has_false_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulator evidence → summary has verified_live=False."""
    p = _get_pilot()
    fake_storage = _FakeStorage()
    monkeypatch.setattr(p, 'load_export_storage', lambda: fake_storage)
    p._generate_export_artifact(
        _ProofBundleConnection('simulator'), workspace_id='ws-1', export_id='exp-t1'
    )
    bundle = json.loads(fake_storage.content)
    summary = bundle['rows'][0]['summary.json']
    assert summary['evidence_state'] == 'SIMULATOR_EVIDENCE'
    assert summary['verified_live'] is False
    assert summary['exportable_as_verified'] is False


def test_proof_bundle_source_truthfulness_no_verified_simulator_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulator evidence → source_truthfulness_status must NOT be 'verified_simulator'."""
    p = _get_pilot()
    fake_storage = _FakeStorage()
    monkeypatch.setattr(p, 'load_export_storage', lambda: fake_storage)
    p._generate_export_artifact(
        _ProofBundleConnection('simulator'), workspace_id='ws-1', export_id='exp-t1'
    )
    bundle = json.loads(fake_storage.content)
    summary = bundle['rows'][0]['summary.json']
    assert summary['source_truthfulness_status'] != 'verified_simulator', (
        "'verified_simulator' must not be used — it implies verified status for non-live data"
    )
    assert summary['source_truthfulness_status'] == 'simulator_only'


# ---------------------------------------------------------------------------
# 5. Demo seed data blocked in production
# ---------------------------------------------------------------------------

def test_demo_seed_status_blocked_in_production_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV=production, no ALLOW_DEMO_MODE → demo_seed_status returns disabled."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    p = _get_pilot()
    result = p.demo_seed_status('demo@decoda.app')
    assert result['present'] is False
    assert result['status'] == 'production_demo_disabled'
    assert 'reason' in result


def test_demo_seed_status_allowed_in_production_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV=production + ALLOW_DEMO_MODE=true → gate passes."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'true')
    p = _get_pilot()
    result = p.demo_seed_status('demo@decoda.app')
    # live_mode_enabled() returns False in test env → 'not_configured', not 'production_demo_disabled'
    assert result['status'] != 'production_demo_disabled'


def test_demo_seed_status_non_production_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_ENV=local → production gate not triggered."""
    monkeypatch.setenv('APP_ENV', 'local')
    p = _get_pilot()
    result = p.demo_seed_status('demo@decoda.app')
    assert result['status'] in {'not_configured', 'missing', 'present'}


# ---------------------------------------------------------------------------
# 6. validate_evidence_for_live_proof
# ---------------------------------------------------------------------------

def test_validate_live_evidence_passes() -> None:
    result = validate_evidence_for_live_proof(evidence_source='live')
    assert result['valid'] is True
    assert result['evidence_state'] == 'REAL_EVIDENCE'
    assert result['verified_live'] is True
    assert result['exportable_as_verified'] is True
    assert result['error'] is None


def test_validate_live_provider_passes() -> None:
    assert validate_evidence_for_live_proof(evidence_source='live_provider')['valid'] is True


def test_validate_simulator_fails() -> None:
    result = validate_evidence_for_live_proof(evidence_source='simulator')
    assert result['valid'] is False
    assert result['evidence_state'] == 'SIMULATOR_EVIDENCE'
    assert result['verified_live'] is False
    assert result['exportable_as_verified'] is False
    assert 'simulator' in result['error'].lower()


def test_validate_fallback_fails() -> None:
    result = validate_evidence_for_live_proof(evidence_source='fallback')
    assert result['valid'] is False
    assert result['evidence_state'] == 'FALLBACK_EVIDENCE'
    assert 'fallback' in result['error'].lower()


def test_validate_guided_simulator_fails() -> None:
    assert validate_evidence_for_live_proof(evidence_source='guided_simulator')['valid'] is False


def test_validate_demo_fails() -> None:
    assert validate_evidence_for_live_proof(evidence_source='demo')['valid'] is False


def test_validate_production_with_live_mode_false_fails() -> None:
    result = validate_evidence_for_live_proof(
        evidence_source='live',
        app_env='production',
        live_mode='false',
    )
    assert result['valid'] is False
    assert 'production' in result['error'].lower()


# ---------------------------------------------------------------------------
# 7. classify_evidence_state parameterised
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('source, expected', [
    ('live', 'REAL_EVIDENCE'),
    ('live_provider', 'REAL_EVIDENCE'),
    ('rpc', 'REAL_EVIDENCE'),
    ('indexer', 'REAL_EVIDENCE'),
    ('simulator', 'SIMULATOR_EVIDENCE'),
    ('guided_simulator', 'SIMULATOR_EVIDENCE'),
    ('demo', 'SIMULATOR_EVIDENCE'),
    ('replay', 'SIMULATOR_EVIDENCE'),
    ('fallback', 'FALLBACK_EVIDENCE'),
    ('degraded', 'FALLBACK_EVIDENCE'),
    ('fixture', 'FIXTURE_EVIDENCE'),
    ('test_fixture', 'FIXTURE_EVIDENCE'),
    (None, 'UNKNOWN_EVIDENCE'),
    ('', 'UNKNOWN_EVIDENCE'),
    ('unknown', 'UNKNOWN_EVIDENCE'),
])
def test_classify_evidence_state(source, expected) -> None:
    assert classify_evidence_state(source) == expected
