"""Screen 9 — canonical incident→evidence collector link regression tests.

Background (the reported COLLECTOR LINK FAILURE, EV-2026-004):
  The live Incident detail page for incident 3c42654c-… shows Linked alerts = 1,
  Evidence records = 3, and a completed Investigation Workflow (Detection, Triage,
  Evidence Collection, Correlation). Those relationships are resolved by ONE
  canonical builder — ai_triage.build_evidence_snapshot — plus the deterministic
  forensic workflow (forensic_investigation.derive_workflow_stages).

  Screen 9's proof-bundle collector (_generate_export_artifact) instead resolved
  alerts/telemetry through detection_metrics and detections.linked_alert_id —
  relations that are legitimately EMPTY for the create-from-alert / escalation
  flow. So a package for an incident the Incident page proves has an alert, a
  detection and an investigation timeline reported:
      alert_id = null, detection_id = null,
      original_alert = missing, detection_provenance = missing,
      investigation_timeline = missing.

These tests lock in the fix: the collector reuses the SAME canonical relationships
the Incident detail page uses (never a title/timestamp heuristic), so the alert,
detection provenance, telemetry and investigation timeline are collected — and
records that genuinely do not exist stay honestly missing.

Pure unit tests: fake DB connection + fake storage; the canonical builder and the
forensic helpers are monkeypatched to the exact shape the Incident page resolves,
so these tests exercise the COLLECTOR WIRING (does Screen 9 use the canonical
relationships?) rather than re-testing the snapshot builder (which has its own
tests).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import ai_triage, forensic_investigation, pilot


# ── The exact production identifiers from the reported incident ───────────────
INCIDENT_ID = '3c42654c-8504-4743-b548-253e0503326e'
ALERT_ID = 'a1111111-1111-4111-8111-111111111111'
DETECTION_ID = 'd2222222-2222-4222-8222-222222222222'
TARGET_ID = 't3333333-3333-4333-8333-333333333333'
ASSET_ID = 'a4444444-4444-4444-8444-444444444444'
TELEMETRY_ID = 'e5555555-5555-4555-8555-555555555555'
RULE_ID = 'strategic_infrastructure_guard'
WS = 'ws-forensic-1'


# ── Fakes ─────────────────────────────────────────────────────────────────────
class _FakeStorage:
    backend_name = 'local'

    def __init__(self) -> None:
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


def _canonical_assembled(*, with_alert: bool = True):
    """The assembled snapshot the Incident page resolves for this incident.

    Mirrors ai_triage.build_evidence_snapshot's return contract: a `snapshot` with
    alert/rule/target/telemetry and a `source_record_ids` map of canonical ids.
    """
    if not with_alert:
        return {
            'snapshot': {
                'alert': {'alert_id': None, 'rule_id': None},
                'rule': {'rule_id': None},
                'target': {'target_id': None, 'asset_id': None},
                'telemetry': [],
                'evidence_complete': False,
                'evidence_incomplete': True,
                'incomplete_reasons': ['incident has no linked source alert'],
            },
            'source_record_ids': {
                'incident_id': INCIDENT_ID, 'alert_id': None, 'detection_event_id': None,
                'detection_id': None, 'target_id': None, 'telemetry_ids': [],
            },
        }
    return {
        'snapshot': {
            'alert': {'alert_id': ALERT_ID, 'severity': 'high',
                      'created_at': '2026-02-01T00:00:00Z', 'rule_id': RULE_ID},
            'rule': {'rule_id': RULE_ID, 'name': 'Strategic Infrastructure Guard',
                     'description': 'Large protected-asset wallet transfer.',
                     'conditions': {}, 'version': '1'},
            'target': {'target_id': TARGET_ID, 'asset_id': ASSET_ID, 'chain_id': 8453,
                       'address': '0xdeadbeef', 'asset_type': 'wallet'},
            'telemetry': [{
                'telemetry_id': TELEMETRY_ID, 'event_type': 'wallet_transfer_detected',
                'detected_by': 'quicknode', 'tx_hash': '0xabc123', 'block_number': 987654,
                'chain_id': 8453, 'observed_at': '2026-02-01T00:00:05Z',
                'evidence_source': 'live', 'target_id': TARGET_ID,
            }],
            'evidence_complete': True,
            'evidence_incomplete': False,
            'incomplete_reasons': [],
        },
        'source_record_ids': {
            'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID, 'detection_event_id': None,
            'detection_id': DETECTION_ID, 'target_id': TARGET_ID,
            'telemetry_ids': [TELEMETRY_ID],
        },
    }


class _LinkFailureConnection:
    """Reproduces the EV-2026-004 collector-link-failure production shape.

    The detection_metrics bridge and detections.linked_alert_id relation are EMPTY
    (as they are for the create-from-alert flow); the incident's canonical
    source_alert_id / linked_alert_ids DO point at a real alert, and the canonical
    detections-table row is reachable by id. Telemetry lives in telemetry_events
    (resolved by the snapshot), never in detection_metrics.
    """

    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []

    def execute(self, query, params=None):
        stmt = ' '.join(str(query).split())
        self.executed.append((stmt, params))
        return self._handle(stmt, params)

    def commit(self):
        pass

    def _handle(self, stmt, params):
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in stmt:
            return _FakeRow({
                'id': 'exp-forensic-1', 'export_type': 'proof_bundle', 'format': 'json',
                'filters': {'incident_id': INCIDENT_ID, 'include_raw_events': True},
                'requested_by_user_id': 'user-1',
            })
        # Base incident row (SELECT * …) carries the canonical incident→alert links.
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in stmt:
            return _FakeRow({
                'id': INCIDENT_ID, 'workspace_id': WS, 'title': 'Protected asset transfer',
                'severity': 'high', 'status': 'open', 'asset_id': None,
                'source_alert_id': ALERT_ID, 'linked_alert_ids': [ALERT_ID],
            })
        # detection_metrics bridge → empty (the bug trigger).
        if 'FROM alerts a JOIN detection_metrics dm' in stmt:
            return _FakeRow([])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in stmt:
            return _FakeRow([])
        # Canonical incident→alert fallback (incidents.source_alert_id / linked_alert_ids
        # / alerts.incident_id) — the SAME relation the Incident page uses. Resolves.
        if 'FROM alerts a WHERE a.workspace_id' in stmt and 'a.id = ANY' in stmt:
            return _FakeRow([{
                'id': ALERT_ID, 'workspace_id': WS, 'severity': 'high', 'status': 'open',
                'source': 'live_provider', 'target_id': TARGET_ID, 'title': 'Wallet transfer',
                'incident_id': INCIDENT_ID, 'detection_id': DETECTION_ID,
            }])
        # Canonical alert-by-id load (only fires if the fallback above did not).
        if 'FROM alerts a WHERE a.workspace_id' in stmt and 'a.id = %s::uuid' in stmt:
            return _FakeRow({
                'id': ALERT_ID, 'workspace_id': WS, 'severity': 'high', 'status': 'open',
                'source': 'live_provider', 'target_id': TARGET_ID, 'title': 'Wallet transfer',
                'incident_id': INCIDENT_ID, 'detection_id': DETECTION_ID,
            })
        if 'FROM detection_metrics WHERE workspace_id = %s AND alert_id = ANY' in stmt:
            return _FakeRow([])
        if 'FROM response_actions' in stmt and 'incident_id = %s' in stmt:
            return _FakeRow([])
        # detections.linked_alert_id → empty (wrong relation for this flow).
        if 'FROM detections' in stmt and 'linked_alert_id = ANY' in stmt:
            return _FakeRow([])
        # Canonical detection-by-id (alerts.detection_id → detections) — resolves.
        if 'FROM detections' in stmt and 'AND id = %s::uuid' in stmt:
            return _FakeRow({
                'id': DETECTION_ID, 'detection_type': 'wallet_transfer',
                'source_rule': RULE_ID, 'evidence_summary': 'Large transfer detected',
                'evidence_source': 'live', 'raw_evidence_json': {'tx_hash': '0xabc123'},
                'detected_at': '2026-02-01T00:00:02Z',
            })
        if 'FROM audit_logs' in stmt and 'row_hash IS NOT NULL' in stmt:
            return _FakeRow(None)
        if 'FROM audit_logs' in stmt:
            return _FakeRow([])
        # Canonical investigation timeline state-transition rows.
        if 'FROM incident_timeline' in stmt:
            return _FakeRow([
                {'id': 'tl-2', 'event_type': 'evidence_collection_completed',
                 'message': 'Evidence collected', 'actor_user_id': None,
                 'metadata': {}, 'created_at': '2026-02-01T00:03:00Z'},
                {'id': 'tl-1', 'event_type': 'detection', 'message': 'Alert opened',
                 'actor_user_id': None, 'metadata': {}, 'created_at': '2026-02-01T00:00:00Z'},
            ])
        if "UPDATE export_jobs SET status = 'completed'" in stmt:
            return _FakeRow(None)
        if "UPDATE export_jobs SET status = 'failed'" in stmt:
            return _FakeRow(None)
        # Package-number allocation is best-effort (wrapped in try/except in the
        # collector); model it so the summary carries a stable number.
        if 'SELECT status, package_number FROM export_jobs WHERE id = %s' in stmt:
            return _FakeRow({'status': 'completed', 'package_number': None})
        if 'pg_advisory_xact_lock' in stmt:
            return _FakeRow(None)
        if 'MAX(NULLIF(regexp_replace(package_number' in stmt:
            return _FakeRow({'max_seq': 4})
        if 'UPDATE export_jobs SET package_number' in stmt:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {stmt!r}')


@pytest.fixture()
def _patch_canonical(monkeypatch):
    """Route the collector's canonical builder + forensic helpers to the exact
    relationships the Incident detail page resolves."""
    def _apply(*, with_alert: bool = True):
        monkeypatch.setattr(
            ai_triage, 'build_evidence_snapshot',
            lambda *a, **k: _canonical_assembled(with_alert=with_alert),
        )
        monkeypatch.setattr(forensic_investigation, '_triage_status', lambda *a, **k: None)
        monkeypatch.setattr(forensic_investigation, '_recommendation_count', lambda *a, **k: 0)
        monkeypatch.setattr(forensic_investigation, '_report_generated', lambda *a, **k: False)
    return _apply


def _generate(monkeypatch, connection):
    storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)
    meta = pilot._generate_export_artifact(connection, workspace_id=WS, export_id='exp-forensic-1')
    payload = json.loads(storage.content.decode('utf-8'))
    return meta, payload['rows'][0]


# ── 1. Incident linked alert is collected (was null) ──────────────────────────
def test_incident_linked_alert_is_collected(monkeypatch, _patch_canonical):
    """The incident's canonical originating alert is collected — alert_id is no
    longer null for a package whose incident has a linked alert."""
    _patch_canonical()
    _meta, row = _generate(monkeypatch, _LinkFailureConnection())
    summary = row['summary.json']

    assert summary['alert_id'] == ALERT_ID, 'Screen 9 must collect the linked alert id'
    assert 'alerts' not in summary['missing_sections'], 'original_alert must be present'
    assert summary['alert_count'] == 1
    # Bundle carries the real alert row.
    assert any(a.get('id') == ALERT_ID for a in row['alerts.json'])


# ── 2. Alerts tab and Screen 9 resolve the SAME alert id (never a heuristic) ──
def test_alerts_tab_and_screen9_resolve_same_alert(monkeypatch, _patch_canonical):
    """The alert id Screen 9 collects equals the incident's canonical source_alert_id
    — the exact relationship the Incident Record → Alerts tab uses. This must never
    regress back to null."""
    _patch_canonical()
    _meta, row = _generate(monkeypatch, _LinkFailureConnection())
    summary = row['summary.json']

    # incident.source_alert_id (Alerts-tab / Incident-detail linkage) == collected id.
    assert summary['alert_id'] == ALERT_ID
    assert summary['alert_id'] is not None
    # And it is the SAME id the canonical snapshot exposes to the Incident page.
    assert ai_triage.build_evidence_snapshot(None)['source_record_ids']['alert_id'] == summary['alert_id']


# ── 3. Detection provenance is collected via the canonical relation ───────────
def test_detection_provenance_is_collected(monkeypatch, _patch_canonical):
    """Detection provenance resolves through alerts.detection_id → detections (the
    canonical relation), NOT detections.linked_alert_id — so detection_id is no
    longer null."""
    _patch_canonical()
    _meta, row = _generate(monkeypatch, _LinkFailureConnection())
    summary = row['summary.json']

    assert summary['detection_id'] == DETECTION_ID, 'canonical detection id must be collected'
    prov = summary['detection_provenance']
    assert prov['present'] is True
    assert prov['detection_id'] == DETECTION_ID
    assert prov['rule']['rule_id'] == RULE_ID
    assert 'detections' not in summary['missing_sections']


# ── 4. Investigation workflow becomes investigation-timeline evidence ─────────
def test_investigation_timeline_is_collected(monkeypatch, _patch_canonical):
    """The incident's Investigation Workflow (the forensic seven-stage workflow) is
    captured as investigation-timeline evidence — no longer 'missing'."""
    _patch_canonical()
    _meta, row = _generate(monkeypatch, _LinkFailureConnection())
    summary = row['summary.json']

    assert summary['investigation_timeline_present'] is True
    timeline = row['investigation_timeline.json']
    assert timeline['present'] is True
    stages = {s['stage']: s['state'] for s in timeline['workflow_stages']}
    # The exact stages the live Incident page shows completed.
    assert stages['detection'] == 'completed'
    assert stages['triage'] == 'completed'
    assert stages['evidence_collection'] == 'completed'
    assert stages['correlation'] == 'completed'
    # Later stages are honestly still pending.
    assert stages['analysis'] == 'pending'
    assert stages['recommendation'] == 'pending'
    # Real state-transition rows are captured too.
    assert len(timeline['timeline_events']) == 2


# ── 5+6. Historical telemetry is collected by canonical REFERENCE, not a window ─
def test_historical_telemetry_collected_by_reference_not_window(monkeypatch, _patch_canonical):
    """Telemetry is resolved through canonical identifiers (the snapshot's telemetry),
    so a historical incident's telemetry is captured even though it is absent from
    detection_metrics and the current 7-day Threat-Monitoring window is empty. The
    collector issues NO time-window / title query to resolve it."""
    _patch_canonical()
    conn = _LinkFailureConnection()
    _meta, row = _generate(monkeypatch, conn)
    summary = row['summary.json']

    assert 'telemetry_events.json' in row, 'canonical historical telemetry must be included'
    assert row['telemetry_events.json'][0]['telemetry_id'] == TELEMETRY_ID
    assert 'telemetry_evidence' not in summary['missing_sections']
    # No collector query fabricates a relationship by a recent-time window or by
    # title / nearest-timestamp similarity.
    joined = ' '.join(s for s, _ in conn.executed).lower()
    assert 'interval' not in joined, 'no time-window telemetry heuristic'
    assert 'now() -' not in joined and 'now()-' not in joined, 'no recent-window heuristic'
    assert 'ilike' not in joined, 'no fuzzy similarity match'
    # No title-similarity correlation on any table (the only LIKEs are the audit
    # action-prefix filter and the package_number sequence scan — never a title).
    for stmt, _ in conn.executed:
        low = stmt.lower()
        if 'like' in low:
            assert 'title' not in low, f'title-similarity heuristic: {stmt!r}'


# ── 7. Completeness rises because real evidence became present ────────────────
def test_completeness_rises_when_canonical_evidence_present(monkeypatch, _patch_canonical):
    """Deterministic completeness is recalculated from facts: with the alert,
    detection provenance, telemetry, asset and investigation timeline now present,
    the score is materially higher than the degraded EV-2026-004 baseline. No target
    score is hardcoded — it is compared against the same collector with the canonical
    links unavailable."""
    # Corrected collector (canonical links available).
    _patch_canonical(with_alert=True)
    _meta_ok, row_ok = _generate(monkeypatch, _LinkFailureConnection())
    fixed = row_ok['summary.json']['completeness']['score']

    # Same collector, canonical links genuinely unavailable (nothing to resolve).
    _patch_canonical(with_alert=False)
    _meta_bad, row_bad = _generate(monkeypatch, _MissingEverythingConnection())
    degraded = row_bad['summary.json']['completeness']['score']

    assert fixed > degraded, f'completeness must rise ({degraded} -> {fixed})'
    # The concrete categories the Incident page proved are present.
    facts = {c['code']: c for c in row_ok['summary.json']['completeness']['categories']}
    assert facts['original_alert']['status'] == 'present'
    assert facts['detection_provenance']['status'] == 'present'
    assert facts['investigation_timeline']['status'] == 'present'


class _MissingEverythingConnection(_LinkFailureConnection):
    """An incident with genuinely NO linked alert / detection / telemetry / timeline."""

    def _handle(self, stmt, params):
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in stmt:
            return _FakeRow({
                'id': INCIDENT_ID, 'workspace_id': WS, 'title': 'Bare incident',
                'severity': 'low', 'status': 'open', 'asset_id': None,
                'source_alert_id': None, 'linked_alert_ids': [],
            })
        if 'FROM alerts a WHERE a.workspace_id' in stmt:
            return _FakeRow([]) if 'a.id = ANY' in stmt else _FakeRow(None)
        if 'FROM detections' in stmt:
            return _FakeRow([]) if 'linked_alert_id = ANY' in stmt else _FakeRow(None)
        if 'FROM incident_timeline' in stmt:
            return _FakeRow([])
        return super()._handle(stmt, params)


# ── 8. Missing records stay honestly missing (no fabrication) ─────────────────
def test_missing_records_stay_missing(monkeypatch, _patch_canonical):
    """When the incident genuinely has no alert/detection/telemetry/timeline, the
    collector must NOT fabricate them — every provenance field is null/absent and the
    sections are honestly reported missing."""
    _patch_canonical(with_alert=False)
    _meta, row = _generate(monkeypatch, _MissingEverythingConnection())
    summary = row['summary.json']

    assert summary['alert_id'] is None
    assert summary['detection_id'] is None
    assert summary['detection_provenance']['present'] is False
    assert summary['investigation_timeline_present'] is False
    assert 'alerts' in summary['missing_sections']
    assert 'detections' in summary['missing_sections']
    assert 'telemetry_evidence' in summary['missing_sections']
    assert 'telemetry_events.json' not in row


# ── 9. The collector REUSES the canonical builder (no parallel heuristic) ──────
def test_collector_reuses_canonical_builder(monkeypatch):
    """Screen 9 must resolve provenance through ai_triage.build_evidence_snapshot —
    the SAME builder the Incident detail page uses — not a second parallel query."""
    called = {'n': 0}
    real = _canonical_assembled(with_alert=True)

    def _spy(connection, *, workspace_id, incident_id):
        called['n'] += 1
        assert incident_id == INCIDENT_ID
        assert workspace_id == WS
        return real

    monkeypatch.setattr(ai_triage, 'build_evidence_snapshot', _spy)
    monkeypatch.setattr(forensic_investigation, '_triage_status', lambda *a, **k: None)
    monkeypatch.setattr(forensic_investigation, '_recommendation_count', lambda *a, **k: 0)
    monkeypatch.setattr(forensic_investigation, '_report_generated', lambda *a, **k: False)

    _generate(monkeypatch, _LinkFailureConnection())
    assert called['n'] >= 1, 'collector must reuse the canonical evidence-snapshot builder'


# ── 10. Snapshot failure is fail-closed — base collection is never broken ─────
def test_snapshot_failure_is_fail_closed(monkeypatch):
    """If the canonical builder raises, the collector degrades gracefully to its base
    collection rather than failing the package (fail-closed, additive)."""
    def _boom(*a, **k):
        raise RuntimeError('snapshot backend unavailable')

    monkeypatch.setattr(ai_triage, 'build_evidence_snapshot', _boom)

    meta, row = _generate(monkeypatch, _LinkFailureConnection())
    # The package still generates; provenance simply falls back to base relations.
    assert row['summary.json']['incident_id'] == INCIDENT_ID
    assert meta['export_status'] in {'complete', 'partial', 'incomplete'}


# ── 11. A collector repair generates a SUPERSEDING package, never mutating the
#        historical one (EV-2026-004 is preserved; EV-2026-005 is minted) ──────
class _SupersedeConnection:
    """Models create_proof_bundle_export in supersede/regenerate mode with an
    existing completed package for the same incident."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.inserted_pkg_id: str | None = None
        self.filters_repaired = False
        self.committed = False

    def execute(self, stmt, params=None):
        s = ' '.join(str(stmt).split())
        self.executed.append((s, params))
        if "(filters->>'response_action_id') IS NULL" in s:
            # An existing completed package (EV-2026-004) for this incident.
            return _FakeRow({
                'id': 'ev-2026-004', 'status': 'completed',
                'storage_object_key': f'{WS}/ev-2026-004.json',
                'package_number': 'EV-2026-004',
                'filters': {'incident_id': INCIDENT_ID, 'include_raw_events': True},
            })
        if 'UPDATE export_jobs SET filters' in s:
            self.filters_repaired = True
            return _FakeRow(None)
        if 'INSERT INTO export_jobs' in s:
            self.inserted_pkg_id = params[0] if params else None
            return _FakeRow(None)
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in s:
            return _FakeRow({
                'id': self.inserted_pkg_id or 'ev-new', 'export_type': 'proof_bundle',
                'format': 'json',
                'filters': {'incident_id': INCIDENT_ID, 'include_raw_events': True, 'regenerated': True},
                'requested_by_user_id': 'user-1',
            })
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in s:
            return _FakeRow({
                'id': INCIDENT_ID, 'workspace_id': WS, 'title': 'Protected asset transfer',
                'severity': 'high', 'status': 'open', 'asset_id': None,
                'source_alert_id': ALERT_ID, 'linked_alert_ids': [ALERT_ID],
            })
        if 'FROM alerts a JOIN detection_metrics dm' in s:
            return _FakeRow([])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in s:
            return _FakeRow([])
        if 'FROM alerts a WHERE a.workspace_id' in s and 'a.id = ANY' in s:
            return _FakeRow([{'id': ALERT_ID, 'source': 'live_provider', 'target_id': TARGET_ID}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND alert_id = ANY' in s:
            return _FakeRow([])
        if 'FROM response_actions' in s:
            return _FakeRow([])
        if 'FROM detections' in s and 'linked_alert_id = ANY' in s:
            return _FakeRow([])
        if 'FROM detections' in s and 'AND id = %s::uuid' in s:
            return _FakeRow(None)
        if 'FROM audit_logs' in s:
            return _FakeRow(None) if 'row_hash IS NOT NULL' in s else _FakeRow([])
        if 'FROM incident_timeline' in s:
            return _FakeRow([])
        if "UPDATE export_jobs SET status = 'completed'" in s:
            return _FakeRow(None)
        if 'SELECT status, error_message, package_number FROM export_jobs WHERE id = %s' in s:
            return _FakeRow({'status': 'completed', 'error_message': None, 'package_number': 'EV-2026-005'})
        if 'SELECT status, package_number FROM export_jobs WHERE id = %s' in s:
            return _FakeRow({'status': 'completed', 'package_number': None})
        if 'pg_advisory_xact_lock' in s:
            return _FakeRow(None)
        if 'MAX(NULLIF(regexp_replace(package_number' in s:
            return _FakeRow({'max_seq': 4})
        if 'UPDATE export_jobs SET package_number' in s:
            return _FakeRow(None)
        return _FakeRow(None)

    def commit(self):
        self.committed = True


def test_repair_generates_superseding_package_not_mutation(monkeypatch, _patch_canonical):
    """Regenerating with the corrected collector mints a NEW package (superseding)
    and never rewrites the historical EV-2026-004 row."""
    _patch_canonical()
    conn = _SupersedeConnection()

    @contextmanager
    def _pg():
        yield conn

    storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (
        {'id': 'user-1', 'mfa_enabled': False}, {'workspace_id': WS, 'role': 'admin'},
    ))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_a, **_k: {'exports_enabled': True})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)

    req = SimpleNamespace(headers={'x-workspace-id': WS},
                          query_params=SimpleNamespace(get=lambda *_a, **_k: None))
    result = pilot.create_proof_bundle_export(
        {'incident_id': INCIDENT_ID, 'supersede': True, 'superseded_from': 'ev-2026-004'}, req,
    )

    # A brand-new package id was minted — never the historical EV-2026-004 row.
    assert result['created'] is True
    assert conn.inserted_pkg_id is not None
    assert conn.inserted_pkg_id != 'ev-2026-004'
    assert result['package_id'] != 'ev-2026-004'
    # The historical row's contents were never rewritten.
    assert conn.filters_repaired is False, 'supersede must not overwrite EV-2026-004'
