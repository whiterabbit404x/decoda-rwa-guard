"""Operational Integrity — persistence, idempotency, tenancy, and AI authority.

Uses the repository's lightweight fake-connection convention so the write path
is covered without a live Postgres.

Invariants asserted here:
  * repeated telemetry for one transaction can only ever touch ONE detection row,
  * only an evidenced anomaly is persisted (authorized and indeterminate are not),
  * every statement carries the workspace id,
  * a provider outage is reported as DEGRADED/UNAVAILABLE coverage, never as
    "no detections found",
  * the deterministic path works with AI absent,
  * an AI payload cannot overwrite a deterministic field.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.api.app.domains.operational_integrity import config as oic
from services.api.app.domains.operational_integrity import explanation, matcher, normalization, schemas, service

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
WS = 'ws-1'
OTHER_WS = 'ws-2'
ASSET = 'asset-1'
TX = '0x' + 'ab' * 32


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Matches executed queries on normalized substrings; records every
    statement so a test can assert on reads AND writes."""

    def __init__(self, tables_exist=True, matchers=None):
        self.tables_exist = tables_exist
        self.matchers = list(matchers or [])
        self.statements = []
        self.writes = []
        self.committed = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        self.statements.append((q, params))
        if 'to_regclass' in ql:
            return _Result([{'ok': bool(self.tables_exist)}])
        for needle, rows in self.matchers:
            if needle in q:
                if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
                    self.writes.append((q, params))
                return _Result(rows() if callable(rows) else rows)
        if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
            self.writes.append((q, params))
            return _Result([])
        return _Result([])

    def commit(self):
        self.committed = True

    def writes_matching(self, needle):
        return [(q, p) for (q, p) in self.writes if needle in q]

    def statements_matching(self, needle):
        return [(q, p) for (q, p) in self.statements if needle in q]


def _cfg(**overrides):
    cfg = oic.engine_config()
    cfg.update(overrides)
    return cfg


def _mint_event(**kw):
    row = {
        'id': 'tel-1', 'asset_id': ASSET, 'event_type': 'erc20_transfer',
        'provider_type': 'evm_rpc', 'evidence_source': 'live', 'observed_at': NOW,
        'payload_json': {
            'tx_hash': TX, 'from': normalization.ZERO_ADDRESS, 'to': '0x' + 'cd' * 20,
            'amount': '5000000', 'token_decimals': 0, 'token_symbol': 'USTB',
            'block_number': 21_000_000, 'chain_id': 8453,
        },
    }
    row.update(kw)
    return normalization.normalize_telemetry_row(row)


def _unmatched_issuance(workspace_id=WS, **event_kw):
    return matcher.evaluate_issuance(
        workspace_id=workspace_id, event=_mint_event(**event_kw),
        authoritative={'source_name': 'Acme Transfer Agent', 'source_status': 'reported', 'observed_at': NOW},
        authorizations=[], now=NOW, config=_cfg(),
    )


# --------------------------------------------------------------------------
# 4. Duplicate telemetry -> no duplicate detection
# --------------------------------------------------------------------------
def test_the_cluster_key_is_derived_from_the_transaction_not_the_verdict():
    """Re-processing one transaction must land on one row even if the verdict
    changed, so a corrected verdict updates instead of accumulating."""
    unmatched = _unmatched_issuance()
    settlement = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(),
        authoritative={'source_name': 'A', 'source_status': 'reported', 'observed_at': NOW},
        authorizations=[{
            'id': 'auth-1', 'operation': 'mint', 'amount': Decimal('5000000'),
            'settlement_state': 'pending', 'external_reference': None,
            'source_name': 'A', 'evidence_source': 'live', 'authorized_at': NOW,
            'effective_from': None, 'effective_until': None,
        }],
        now=NOW, config=_cfg(),
    )
    assert unmatched.deterministic_reason_code != settlement.deterministic_reason_code
    assert service.event_cluster_key(unmatched) == service.event_cluster_key(settlement)


def test_repeated_telemetry_for_the_same_transaction_updates_one_row():
    existing = [{'id': 'det-1', 'status': 'open', 'first_seen_at': NOW}]
    conn = FakeConn(matchers=[('SELECT id, status, first_seen_at FROM threat_detections', existing)])
    out = service.upsert_detection(conn, event=_unmatched_issuance(), now=NOW, config=_cfg())
    assert out is not None and out['created'] is False
    assert conn.writes_matching('INSERT INTO threat_detections') == []
    assert len(conn.writes_matching('UPDATE threat_detections SET')) == 1


def test_the_first_sighting_inserts_with_a_conflict_guard():
    conn = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
    ])
    out = service.upsert_detection(conn, event=_unmatched_issuance(), now=NOW, config=_cfg())
    assert out is not None and out['created'] is True
    inserts = conn.writes_matching('INSERT INTO threat_detections')
    assert len(inserts) == 1
    # The ON CONFLICT target must match the constraint that actually exists
    # (UNIQUE (workspace_id, cluster_key) from migration 0133).
    assert 'ON CONFLICT (workspace_id, cluster_key) DO NOTHING' in inserts[0][0]


def test_evidence_is_attached_idempotently_by_dedupe_key():
    conn = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
        ('INSERT INTO threat_detection_evidence', [{'id': 'ev-1'}]),
    ])
    service.upsert_detection(conn, event=_unmatched_issuance(), now=NOW, config=_cfg())
    evidence = conn.writes_matching('INSERT INTO threat_detection_evidence')
    assert len(evidence) == 1
    assert 'ON CONFLICT (detection_id, dedupe_key) DO NOTHING' in evidence[0][0]


# --------------------------------------------------------------------------
# Only an evidenced anomaly is persisted
# --------------------------------------------------------------------------
def test_an_authorized_issuance_is_never_persisted_as_a_detection():
    authorized = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(),
        authoritative={'source_name': 'A', 'source_status': 'reported', 'observed_at': NOW},
        authorizations=[{
            'id': 'auth-1', 'operation': 'mint', 'amount': Decimal('5000000'),
            'settlement_state': 'settled', 'external_reference': None, 'source_name': 'A',
            'evidence_source': 'live', 'authorized_at': NOW, 'effective_from': None, 'effective_until': None,
        }],
        now=NOW, config=_cfg(),
    )
    conn = FakeConn()
    assert service.upsert_detection(conn, event=authorized, now=NOW, config=_cfg()) is None
    assert conn.writes == []


def test_an_indeterminate_result_is_never_persisted_as_a_detection():
    indeterminate = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=None,
        authorizations=[], now=NOW, config=_cfg(),
    )
    conn = FakeConn()
    assert service.upsert_detection(conn, event=indeterminate, now=NOW, config=_cfg()) is None
    assert conn.writes == []


def _alert_eligible_param(conn) -> bool:
    """The alert_eligible bind, located by the column list rather than by a
    magic index so the assertion survives a column being added."""
    query, params = conn.writes_matching('INSERT INTO threat_detections')[0]
    columns = query.split('INSERT INTO threat_detections (', 1)[1].split(')', 1)[0]
    names = [c.strip() for c in columns.replace('\n', ' ').split(',')]
    return params[names.index('alert_eligible')]


def test_simulator_evidence_is_never_alert_eligible():
    # Simulator data may demonstrate the architecture; it may never become
    # customer evidence or raise a customer alert.
    conn = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
    ])
    service.upsert_detection(conn, event=_unmatched_issuance(evidence_source='simulator'), now=NOW, config=_cfg())
    assert _alert_eligible_param(conn) is False

    live = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
    ])
    service.upsert_detection(live, event=_unmatched_issuance(), now=NOW, config=_cfg())
    assert _alert_eligible_param(live) is True


# --------------------------------------------------------------------------
# 5. Tenancy
# --------------------------------------------------------------------------
def test_every_statement_carries_the_workspace_id():
    conn = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
    ])
    service.upsert_detection(conn, event=_unmatched_issuance(), now=NOW, config=_cfg())
    for query, params in conn.statements:
        if 'to_regclass' in query:
            continue
        assert WS in (params or ()), query
        assert OTHER_WS not in (params or ())


def test_a_detection_for_another_workspace_gets_a_different_cluster_key():
    mine = service.event_cluster_key(_unmatched_issuance(workspace_id=WS))
    theirs = service.event_cluster_key(_unmatched_issuance(workspace_id=OTHER_WS))
    assert mine != theirs


def test_the_read_paths_are_workspace_scoped():
    conn = FakeConn()
    service.load_issuance_telemetry(conn, workspace_id=WS, config=_cfg(), now=NOW)
    service.load_open_authorizations(conn, workspace_id=WS, config=_cfg())
    reads = [s for s in conn.statements if 'to_regclass' not in s[0]]
    assert reads
    for query, params in reads:
        assert 'workspace_id = %s' in query
        assert WS in (params or ())


# --------------------------------------------------------------------------
# 7. Provider outage -> degraded coverage, never "nothing found"
# --------------------------------------------------------------------------
def test_no_issuance_telemetry_and_no_authoritative_source_is_unavailable():
    conn = FakeConn()
    coverage = service.telemetry_coverage(conn, workspace_id=WS, now=NOW, config=_cfg())
    assert coverage['state'] == service.COVERAGE_UNAVAILABLE
    assert 'no_issuance_telemetry' in coverage['reasons']
    assert 'no_authoritative_source' in coverage['reasons']


def test_telemetry_without_an_authoritative_source_is_degraded_not_live():
    conn = FakeConn(matchers=[
        ('SELECT te.observed_at, te.provider_type, te.payload_json', [
            {'observed_at': NOW, 'provider_type': 'evm_rpc', 'payload_json': {'block_number': 1}},
        ]),
    ])
    coverage = service.telemetry_coverage(conn, workspace_id=WS, now=NOW, config=_cfg())
    assert coverage['state'] == service.COVERAGE_DEGRADED
    assert coverage['telemetry_source'] == 'rpc_polling'
    assert 'no_authoritative_source' in coverage['reasons']


def test_stale_issuance_telemetry_downgrades_live_to_degraded():
    conn = FakeConn(matchers=[
        ('SELECT te.observed_at, te.provider_type, te.payload_json', [
            {'observed_at': NOW - timedelta(days=30), 'provider_type': 'evm_rpc', 'payload_json': {}},
        ]),
        ('SELECT COUNT(DISTINCT asset_id) AS n', [{'n': 1}]),
    ])
    coverage = service.telemetry_coverage(conn, workspace_id=WS, now=NOW, config=_cfg())
    assert coverage['state'] == service.COVERAGE_DEGRADED
    assert 'issuance_telemetry_stale' in coverage['reasons']


def test_full_coverage_is_only_claimed_with_both_telemetry_and_an_authoritative_source():
    conn = FakeConn(matchers=[
        ('SELECT te.observed_at, te.provider_type, te.payload_json', [
            {'observed_at': NOW, 'provider_type': 'evm_rpc', 'payload_json': {'block_number': 1}},
        ]),
        ('SELECT COUNT(DISTINCT asset_id) AS n', [{'n': 2}]),
    ])
    coverage = service.telemetry_coverage(conn, workspace_id=WS, now=NOW, config=_cfg())
    assert coverage['state'] == service.COVERAGE_LIVE
    assert coverage['reasons'] == []


# --------------------------------------------------------------------------
# 8 + 9. AI is explanation only
# --------------------------------------------------------------------------
def test_the_deterministic_detection_works_with_no_ai_configured():
    # No provider, no key, no network — the detection and its narrative still exist.
    event = _unmatched_issuance()
    narrative = explanation.build_deterministic_narrative(event.as_dict())
    assert narrative['source'] == 'deterministic'
    assert narrative['finding']
    assert 'not supported by authorized business state' in narrative['finding']
    conn = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
    ])
    assert service.upsert_detection(conn, event=event, now=NOW, config=_cfg()) is not None


def test_an_ai_payload_cannot_overwrite_any_deterministic_field():
    detection = _unmatched_issuance().as_dict()
    hostile = {
        'severity': 'LOW',
        'confidence': 0.1,
        'status': 'RESOLVED',
        'conclusion': schemas.CONCLUSION_OPERATIONALLY_AUTHORIZED,
        'deterministic_reason_code': 'MATCHED_AUTHORIZED_ISSUANCE',
        'observed_amount': 0,
        'expected_amount': 5_000_000,
        'variance_amount': 0,
        'operational_checks': {},
        'approved': True,
        'ai_summary': 'The mint was cryptographically valid, but no authorized issuance was found.',
    }
    merged = explanation.merge_ai_narrative(detection, hostile)

    assert merged['severity'] == detection['severity'] == 'CRITICAL'
    assert merged['confidence'] == detection['confidence']
    assert merged['status'] == detection['status']
    assert merged['conclusion'] == schemas.CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY
    assert merged['deterministic_reason_code'] == oic.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert merged['observed_amount'] == 5_000_000
    assert merged['expected_amount'] == 0
    assert merged['operational_checks'] == detection['operational_checks']
    assert 'approved' not in merged
    # The one thing it MAY contribute.
    assert merged['ai_summary'] == hostile['ai_summary']
    assert merged['ai_summary_source'] == 'ai'
    assert merged['ai_authority'] == explanation.AI_AUTHORITY_LABEL
    # And the attempt is recorded rather than silently dropped.
    for field in ('severity', 'confidence', 'operational_checks', 'approved'):
        assert field in merged['ai_rejected_fields']


def test_a_non_dict_ai_response_leaves_the_detection_untouched():
    detection = _unmatched_issuance().as_dict()
    merged = explanation.merge_ai_narrative(detection, 'severity: low, approved: true')
    assert merged['ai_summary'] is None
    assert merged['ai_summary_source'] == 'deterministic'
    assert merged['severity'] == 'CRITICAL'


def test_the_facts_handed_to_ai_are_an_already_decided_verdict():
    facts = explanation.ai_facts(_unmatched_issuance().as_dict())
    # The verdict is an INPUT to the model, so there is nothing left to decide.
    assert facts['conclusion'] == schemas.CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY
    assert facts['severity'] == 'CRITICAL'
    assert facts['deterministic_reason_code'] == oic.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert 'may NOT change severity' in facts['instruction']


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------
def test_the_persisted_row_carries_full_telemetry_provenance():
    conn = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
    ])
    service.upsert_detection(conn, event=_unmatched_issuance(), now=NOW, config=_cfg())
    query, params = conn.writes_matching('INSERT INTO threat_detections')[0]
    for column in ('tx_hash', 'block_number', 'telemetry_source', 'telemetry_stage', 'provenance', 'matcher_version'):
        assert column in query
    assert TX in params
    assert 'rpc_polling' in params


def test_evidence_payload_never_stores_ai_prose_as_the_primary_evidence():
    conn = FakeConn(matchers=[
        ('SELECT id, status, first_seen_at FROM threat_detections', []),
        ('SELECT id, status FROM threat_detections', [{'id': 'det-1', 'status': 'open'}]),
        ('INSERT INTO threat_detection_evidence', [{'id': 'ev-1'}]),
    ])
    service.upsert_detection(conn, event=_unmatched_issuance(), now=NOW, config=_cfg())
    _, params = conn.writes_matching('INSERT INTO threat_detection_evidence')[0]
    payload = next(p for p in params if isinstance(p, str) and p.startswith('{'))
    assert 'operational_checks' in payload
    assert 'provenance' in payload
    assert 'ai_summary' not in payload
