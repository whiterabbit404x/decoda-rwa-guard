"""Screen 3 Integrity endpoints + the AI trust boundary.

The AI layer is READ + EXPLAIN ONLY. These tests assert that guarantee
mechanically: a model that returns a different variance, a different severity,
or a different status cannot change what the product reports, and Screen 3 works
identically with AI disabled.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.api.app.domains.asset_integrity import ai_explanation as ai
from services.api.app.domains.asset_integrity import config as aic
from services.api.app.domains.asset_integrity import endpoints
from services.api.app.domains.asset_integrity import reconciliation as engine

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
CFG = aic.integrity_config()


# --------------------------------------------------------------------------
# AI trust boundary
# --------------------------------------------------------------------------
def _facts(**kw):
    base = {
        'asset_name': 'US Treasury Bond #013',
        'status': engine.UNEXPLAINED_VARIANCE,
        'reason_code': engine.NO_MATCHING_AUTHORIZED_ISSUANCE,
        'severity': 'critical',
        'variance_units': 500000,
        'observed_supply': 5000000,
        'expected_supply': 4500000,
        'authoritative_source': 'Demo Transfer Agent',
        'rule_id': 'RP-17',
        'rule_version': 4,
        'evidence_count': 6,
    }
    base.update(kw)
    return base


def test_ai_disabled_still_produces_a_grounded_explanation():
    summary = ai.generate_summary(_facts(), config={'enabled': False})
    assert summary['source'] == 'deterministic'
    assert summary['explanation']
    assert '+500,000' in summary['explanation']
    assert summary['risk_impact'] == 'Critical'


def test_deterministic_fallback_never_calls_a_provider_when_unconfigured():
    for cfg in (
        {'enabled': True, 'provider': 'openai', 'has_key': False, 'model': 'gpt'},
        {'enabled': True, 'provider': 'openai', 'has_key': True, 'model': ''},
        {'enabled': True, 'provider': 'unknown', 'has_key': True, 'model': 'x'},
    ):
        assert ai.generate_summary(_facts(), config=cfg)['source'] == 'deterministic'


def test_ai_cannot_override_the_deterministic_severity():
    # The model claims the risk is Low; the deterministic severity is critical.
    validated = ai.validate_summary_schema(
        {'explanation': 'Everything looks fine.', 'risk_impact': 'Low', 'severity': 'low', 'next_steps': []},
        deterministic=ai.build_deterministic_summary(_facts()),
    )
    assert validated['risk_impact'] == 'Critical'
    assert 'severity' not in validated


def test_ai_cannot_inject_a_status_reason_code_or_variance():
    validated = ai.validate_summary_schema(
        {
            'explanation': 'Reworded narrative.',
            'status': engine.RECONCILED,
            'reason_code': 'MATCHED_AUTHORIZED_ISSUANCE',
            'variance_units': 0,
            'observed_supply': 1,
            'next_steps': ['step'],
        },
        deterministic=ai.build_deterministic_summary(_facts()),
    )
    assert set(validated) == {'explanation', 'risk_impact', 'next_steps', 'source', 'schema_version', 'evidence_count'}
    assert validated['evidence_count'] == 6


def test_malformed_ai_output_is_rejected():
    deterministic = ai.build_deterministic_summary(_facts())
    for bad in ({}, {'explanation': ''}, {'explanation': 42}, 'not an object', {'explanation': 'ok', 'next_steps': 'x'}):
        try:
            ai.validate_summary_schema(bad, deterministic=deterministic)
        except ai.SummaryValidationError:
            continue
        raise AssertionError(f'accepted malformed AI output: {bad!r}')


def test_provider_failure_falls_back_without_raising(monkeypatch):
    import services.api.app.ai_providers as providers

    def _boom(*_args, **_kwargs):
        raise RuntimeError('provider down')

    monkeypatch.setattr(providers, 'get_triage_provider', _boom, raising=False)
    summary = ai.generate_summary(
        _facts(), config={'enabled': True, 'provider': 'openai', 'has_key': True, 'model': 'gpt-x'},
    )
    assert summary['source'] == 'deterministic'
    assert summary['ai_fallback_reason']


def test_ai_prompt_forbids_recomputing_the_deterministic_outputs():
    prompt = ai._build_prompt(_facts())
    system = prompt['system'].lower()
    assert 'never compute' in system
    assert 'never decide whether a transaction was authorized' in system
    assert 'already been computed' in system
    # Every number the narrative may cite is supplied verbatim.
    payload = json.loads(prompt['user'])
    assert payload['variance_units'] == 500000
    assert payload['severity'] == 'critical'


def test_indeterminate_narrative_claims_neither_safe_nor_anomalous():
    summary = ai.build_deterministic_summary(
        _facts(status=engine.SOURCE_UNAVAILABLE, reason_code=engine.AUTHORITATIVE_SOURCE_UNAVAILABLE,
               severity='medium', variance_units=None),
    )
    text = summary['explanation'].lower()
    assert 'could not establish' in text
    assert 'not evidence of an anomaly' in text
    assert 'not evidence that the asset is healthy' in text


def test_reconciled_narrative_does_not_claim_an_anomaly():
    summary = ai.build_deterministic_summary(
        _facts(status=engine.RECONCILED, reason_code=engine.SUPPLY_MATCHES_AUTHORITATIVE_STATE,
               severity='low', variance_units=0),
    )
    assert 'reconciles' in summary['explanation']
    assert summary['risk_impact'] == 'Low'


# --------------------------------------------------------------------------
# Endpoint payload builders (pure — no request needed)
# --------------------------------------------------------------------------
def _onchain_row(**kw):
    row = {
        'id': 'obs-1', 'total_supply': Decimal('5000000'), 'token_decimals': 6,
        'chain_network': 'base-mainnet', 'contract_address': '0x' + 'a' * 40,
        'block_number': 21_000_000, 'tx_hash': '0x' + 'b' * 64, 'last_delta': Decimal('500000'),
        'last_delta_operation': 'mint', 'last_delta_at': NOW, 'provider_type': 'evm_rpc',
        'evidence_source': 'live', 'telemetry_event_id': None, 'observed_at': NOW,
    }
    row.update(kw)
    return row


def test_absent_onchain_state_is_reported_unavailable_not_zero():
    payload = endpoints._onchain_state_payload(None, now=NOW, config=CFG)
    assert payload['available'] is False
    assert payload['unavailable_reason'] == 'no_observation'
    assert payload['total_supply'] is None
    assert payload['observed_at'] is None


def test_onchain_state_exposes_provider_only_as_provenance():
    payload = endpoints._onchain_state_payload(_onchain_row(), now=NOW, config=CFG)
    assert payload['available'] is True
    assert payload['provider_type'] == 'evm_rpc'
    assert payload['evidence_source'] == 'live'
    assert payload['total_supply'] == '5000000'
    assert payload['stale'] is False


def test_onchain_state_marks_a_stale_observation():
    payload = endpoints._onchain_state_payload(
        _onchain_row(observed_at=NOW - timedelta(hours=6)), now=NOW, config=CFG,
    )
    assert payload['stale'] is True
    assert payload['age_seconds'] == 6 * 3600


def test_absent_authoritative_state_reports_missing_source_status():
    payload = endpoints._authoritative_state_payload(None, now=NOW, config=CFG)
    assert payload['available'] is False
    assert payload['source_status'] == 'missing'
    assert payload['expected_total_supply'] is None


def test_unavailable_authoritative_source_is_not_available_even_with_a_stored_value():
    payload = endpoints._authoritative_state_payload(
        {
            'id': 'a', 'expected_total_supply': Decimal('4500000'), 'token_decimals': 6,
            'settlement_state': 'settled', 'source_name': 'Transfer Agent', 'source_kind': 'transfer_agent',
            'source_status': 'unavailable', 'source_error': 'timeout', 'external_reference': 'SUB-1',
            'evidence_source': 'live', 'observed_at': NOW,
        },
        now=NOW, config=CFG,
    )
    assert payload['available'] is False
    assert payload['source_status'] == 'unavailable'
    assert payload['source_error'] == 'timeout'


def _snapshot_row(**kw):
    row = {
        'id': 'snap-1', 'observed_supply': Decimal('5000000'), 'expected_supply': Decimal('4500000'),
        'variance_units': Decimal('500000'), 'token_decimals': 6, 'status': engine.UNEXPLAINED_VARIANCE,
        'reason_code': engine.NO_MATCHING_AUTHORIZED_ISSUANCE, 'severity': 'critical',
        'rule_id': 'RP-17', 'rule_version': 4, 'rule_config': {}, 'evaluated_at': NOW,
        'onchain_observed_at': NOW, 'authoritative_observed_at': NOW, 'onchain_source': 'evm_rpc',
        'authoritative_source': 'Demo Transfer Agent', 'evidence_source': 'live',
        'block_number': 21_000_000, 'tx_hash': '0x' + 'b' * 64, 'external_reference': 'SUB-81922',
        'matched_issuance_id': None, 'evidence_count': 6, 'evidence_refs': [], 'match_detail': {},
        'canonical_event_id': 'evt-1', 'ai_summary': None, 'ai_summary_source': 'deterministic',
        'trigger_source': 'manual',
    }
    row.update(kw)
    return row


def test_reconciliation_payload_classifies_an_anomaly():
    payload = endpoints._reconciliation_payload(_snapshot_row())
    assert payload['status'] == engine.UNEXPLAINED_VARIANCE
    assert payload['is_anomaly'] is True
    assert payload['is_indeterminate'] is False
    assert payload['variance_units'] == '500000'
    assert payload['rule_id'] == 'RP-17'
    assert payload['rule_version'] == 4
    assert payload['evidence_count'] == 6


def test_reconciliation_payload_classifies_an_indeterminate_state():
    payload = endpoints._reconciliation_payload(
        _snapshot_row(status=engine.STALE_AUTHORITATIVE_DATA, reason_code=engine.AUTHORITATIVE_SOURCE_STALE,
                      severity='medium', canonical_event_id=None),
    )
    assert payload['is_anomaly'] is False
    assert payload['is_indeterminate'] is True


def test_no_snapshot_yields_no_reconciliation_payload():
    assert endpoints._reconciliation_payload(None) is None


def test_ai_panel_falls_back_to_the_deterministic_template_when_no_summary_stored():
    payload = endpoints._ai_assessment_payload(_snapshot_row(ai_summary=None), {'name': 'US Treasury Bond #013'})
    assert payload['source'] == 'deterministic'
    assert '+500,000' in payload['explanation']
    assert payload['risk_impact'] == 'Critical'


def test_ai_panel_uses_the_stored_summary_but_keeps_deterministic_risk_impact():
    payload = endpoints._ai_assessment_payload(
        _snapshot_row(ai_summary='Model narrative.', ai_summary_source='ai'),
        {'name': 'Bond'},
    )
    assert payload['explanation'] == 'Model narrative.'
    assert payload['source'] == 'ai'
    assert payload['risk_impact'] == 'Critical'  # from the persisted deterministic severity


def test_result_rebuilt_from_a_snapshot_keeps_its_original_rule_version():
    # A newer configured rule version must NOT be applied to historical evidence.
    result = endpoints._result_from_snapshot(_snapshot_row(rule_version=2))
    assert result.rule_version == 2
    assert result.status == engine.UNEXPLAINED_VARIANCE
    assert result.variance_units == Decimal('500000')
