"""The Operational Integrity DEMO FIXTURE is what the real engine actually produces.

``services/api/tests/fixtures/operational_integrity_demo.json`` is the payload the
frontend render test (apps/web/tests/threat-operational-integrity-render-screen5.spec.ts)
serves in place of the API, so the browser asserts the real components against a
real backend shape rather than a hand-written guess.

A fixture is only worth anything if it cannot drift from the code it stands in
for. These tests re-derive the fixture's verdict from the PURE matcher and
re-serialize it through the REAL Screen 5 serializers, then assert the checked-in
JSON still matches. Change the matcher and this fails until the fixture is
regenerated — the UI test can never keep asserting a verdict the engine stopped
producing.

The fixture is test data. It is never imported by application code, never written
to a workspace, and its identifiers are obviously synthetic ('fixture0-…') so a
record from it can never be mistaken for customer evidence.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.api.app.domains.operational_integrity import config as oic
from services.api.app.domains.operational_integrity import matcher, normalization, schemas
from services.api.app.domains.threat_detection import endpoints

FIXTURE_PATH = pathlib.Path(__file__).resolve().parent / 'fixtures' / 'operational_integrity_demo.json'

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
OBSERVED_AT = NOW - timedelta(minutes=5)

UNMATCHED_ID = 'fixture0-0000-0000-0000-000000000001'
SETTLEMENT_ID = 'fixture0-0000-0000-0000-000000000002'


@pytest.fixture(scope='module')
def fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))


def _detection(fixture: dict, detection_id: str) -> dict:
    return next(d for d in fixture['detections'] if d['id'] == detection_id)


# --------------------------------------------------------------------------
# The fixture is honest about being a fixture
# --------------------------------------------------------------------------
def test_the_fixture_declares_itself_as_isolated_test_data(fixture):
    meta = fixture['_fixture']
    assert 'TEST DATA ONLY' in meta['isolation']
    # Synthetic ids: a fixture record can never be mistaken for a real one.
    for detection in fixture['detections']:
        assert detection['id'].startswith('fixture0-')
        assert detection['primary_asset_id'].startswith('fixtureA-')


def test_the_fixture_covers_the_priority_case_and_all_three_check_states(fixture):
    types = {d['detection_type'] for d in fixture['detections']}
    assert 'unmatched_issuance' in types, 'the priority detector must be represented'
    statuses = {
        check['status']
        for detail in fixture['details'].values()
        for check in detail['detection']['operational_analysis']['checks']
    }
    # PASS, FAIL and UNKNOWN must all be reachable, or the UI test cannot prove
    # that "could not be checked" renders differently from "failed".
    assert statuses == {'PASS', 'FAIL', 'UNKNOWN'}


# --------------------------------------------------------------------------
# UNMATCHED_ISSUANCE — re-derived from the pure matcher
# --------------------------------------------------------------------------
def test_the_unmatched_issuance_fixture_is_what_the_matcher_produces(fixture):
    """mint_observed = true AND matching_authorized_issuance = false."""
    event = normalization.normalize_telemetry_row({
        'id': 'fixtureT-0000-0000-0000-000000000001',
        'asset_id': 'fixtureA-0000-0000-0000-000000000001',
        'event_type': 'erc20_transfer',
        'provider_type': 'evm_rpc',
        'evidence_source': 'live',
        'observed_at': OBSERVED_AT,
        'payload_json': {
            'tx_hash': '0x7a71d' + 'c' * 53 + '9c3f',
            'from': '0x' + '00' * 20, 'to': '0x' + 'cd' * 20,
            'amount': '5000000', 'token_decimals': 0, 'token_symbol': 'USTB',
            'block_number': 21_000_000, 'chain_id': 8453,
        },
    })
    result = matcher.evaluate_issuance(
        workspace_id='fixture-ws',
        event=event,
        # An authoritative source EXISTS and is fresh — the engine genuinely
        # checked it. It authorized nothing.
        authoritative={'source_name': 'Acme Transfer Agent', 'source_status': 'reported', 'observed_at': NOW - timedelta(minutes=1)},
        authorizations=[],
        now=NOW,
        asset_name='US Treasury Bond #013',
    )

    stored = _detection(fixture, UNMATCHED_ID)
    assert result.category == stored['category'] == oic.CATEGORY_OPERATIONAL_INTEGRITY
    assert result.detection_type == stored['detection_type'] == oic.UNMATCHED_ISSUANCE
    assert result.deterministic_reason_code == stored['deterministic_reason_code'] == oic.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert result.severity == stored['severity'] == 'critical'
    assert str(int(result.observed_amount)) == stored['observed_amount'] == '5000000'
    assert str(int(result.expected_amount)) == stored['expected_amount'] == '0'
    assert str(int(result.variance_amount)) == stored['variance_amount'] == '5000000'
    assert result.conclusion == schemas.CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY
    # The chain accepted it. Only the business checks failed — that is the whole
    # argument of the screen and the fixture must keep making it.
    assert schemas.checks_as_dict(result.checks) == stored['operational_checks']
    assert stored['operational_checks']['signer_validity']['status'] == 'PASS'
    assert stored['operational_checks']['on_chain_event']['status'] == 'PASS'
    assert stored['operational_checks']['transfer_agent_match']['status'] == 'FAIL'
    assert stored['operational_checks']['settlement_match']['status'] == 'FAIL'


def test_the_settlement_timeout_fixture_is_what_the_matcher_produces(fixture):
    result = matcher.evaluate_settlement_deadline(
        workspace_id='fixture-ws',
        authorization={
            'id': 'fixture-auth-1', 'operation': 'mint', 'amount': Decimal('250000'),
            'settlement_state': 'pending', 'external_reference': 'SUB-81922',
            'source_name': 'Acme Transfer Agent', 'evidence_source': 'live',
            'authorized_at': NOW - timedelta(days=30),
        },
        asset_id='fixtureA-0000-0000-0000-000000000002',
        asset_name='Corporate Bond 2034-06',
        now=NOW,
    )
    assert result is not None

    stored = _detection(fixture, SETTLEMENT_ID)
    assert result.detection_type == stored['detection_type'] == oic.SETTLEMENT_TIMEOUT
    assert result.deterministic_reason_code == stored['deterministic_reason_code'] == oic.SETTLEMENT_DEADLINE_EXCEEDED
    assert schemas.checks_as_dict(result.checks) == stored['operational_checks']
    # Signer validity is genuinely UNKNOWN here: there is no on-chain event to
    # verify. It must never be rendered as a failure.
    assert stored['operational_checks']['signer_validity']['status'] == 'UNKNOWN'
    assert stored['operational_checks']['transfer_agent_match']['status'] == 'PASS'


# --------------------------------------------------------------------------
# The analysis payload behind the panel is the real serializer's output
# --------------------------------------------------------------------------
@pytest.mark.parametrize('detection_id', [UNMATCHED_ID, SETTLEMENT_ID])
def test_the_analysis_payload_matches_the_real_serializer(fixture, detection_id):
    stored = _detection(fixture, detection_id)
    rebuilt = endpoints.operational_analysis({
        'detection_type': stored['detection_type'],
        'category': stored['category'],
        'severity': stored['severity'],
        'confidence': stored['confidence'],
        'deterministic_reason_code': stored['deterministic_reason_code'],
        'operational_checks': stored['operational_checks'],
        'matcher_version': stored['matcher_version'],
        'observed_amount': stored['observed_amount'],
        'expected_amount': stored['expected_amount'],
        'variance_amount': stored['variance_amount'],
        'operation': stored['operation'],
        'telemetry_source': stored['telemetry_source'],
        'telemetry_stage': stored['telemetry_stage'],
        'tx_hash': stored['tx_hash'],
        'provenance': stored['provenance'],
        'ai_summary': stored['ai_summary'],
        'ai_summary_source': stored['ai_summary_source'],
    })
    assert rebuilt is not None
    fixture_analysis = fixture['details'][detection_id]['detection']['operational_analysis']
    assert rebuilt['checks'] == fixture_analysis['checks']
    assert rebuilt['conclusion'] == fixture_analysis['conclusion']
    assert rebuilt['deterministic_reason_code'] == fixture_analysis['deterministic_reason_code']
    assert rebuilt['detection_type_label'] == fixture_analysis['detection_type_label']
    # AI text is an explanation of an already-decided verdict, never its author.
    assert rebuilt['ai_summary_source'] == 'deterministic'
    assert 'Explanation only' in rebuilt['ai_authority']


def test_a_cyber_detection_has_no_operational_analysis():
    """The panel must say "not applicable", which requires a null analysis."""
    assert endpoints.operational_analysis({
        'detection_type': 'unusual_transfer', 'category': 'CYBER_SECURITY', 'severity': 'high',
    }) is None
