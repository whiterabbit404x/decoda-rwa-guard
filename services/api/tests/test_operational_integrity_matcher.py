"""Operational Integrity — the deterministic matcher.

The invariant every test here defends:

    A transaction can be cryptographically valid and still be operationally
    unauthorized — and the reverse mistake (calling an outage an unauthorized
    issuance) must be impossible.

Pure-function tests: no DB, no network, no AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.api.app.domains.operational_integrity import config as oic
from services.api.app.domains.operational_integrity import matcher, normalization, schemas

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
WS = 'ws-1'
ASSET = 'asset-1'
TX = '0x' + 'ab' * 32


def _cfg(**overrides):
    cfg = oic.engine_config()
    cfg.update(overrides)
    return cfg


def _mint_event(*, amount='5000000', observed_at=NOW, reference=None, source='rpc_polling', **kw):
    """A cryptographically valid, on-chain-included mint of 5,000,000 base units."""
    row = {
        'id': 'tel-1',
        'asset_id': ASSET,
        'event_type': 'erc20_transfer',
        'provider_type': 'evm_rpc',
        'evidence_source': 'live',
        'observed_at': observed_at,
        'payload_json': {
            'tx_hash': TX,
            'from': normalization.ZERO_ADDRESS,
            'to': '0x' + 'cd' * 20,
            'amount': amount,
            'token_decimals': 0,
            'token_symbol': 'USTB',
            'block_number': 21_000_000,
            'chain_id': 8453,
            'ingestion_source': source,
            **({'external_reference': reference} if reference else {}),
        },
    }
    row['payload_json'].update(kw)
    return normalization.normalize_telemetry_row(row)


def _authoritative(**kw):
    row = {
        'source_name': 'Acme Transfer Agent',
        'source_status': 'reported',
        'observed_at': NOW,
        'expected_total_supply': Decimal('4500000'),
    }
    row.update(kw)
    return row


def _authorization(**kw):
    row = {
        'id': 'auth-1',
        'operation': 'mint',
        'amount': Decimal('5000000'),
        'settlement_state': 'settled',
        'external_reference': 'SUB-81922',
        'source_name': 'Acme Transfer Agent',
        'evidence_source': 'live',
        'authorized_at': NOW - timedelta(minutes=10),
        'effective_from': None,
        'effective_until': None,
    }
    row.update(kw)
    return row


# --------------------------------------------------------------------------
# 1. Valid mint + matching authorized issuance -> NO detection
# --------------------------------------------------------------------------
def test_valid_mint_with_matching_authorized_issuance_is_not_an_anomaly():
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=_authoritative(),
        authorizations=[_authorization()], now=NOW, config=_cfg(),
    )
    assert result.conclusion == schemas.CONCLUSION_OPERATIONALLY_AUTHORIZED
    assert result.is_anomaly is False
    assert result.deterministic_reason_code == oic.MATCHED_AUTHORIZED_ISSUANCE
    checks = {k: v.status for k, v in result.checks.items()}
    assert checks[schemas.CHECK_TRANSFER_AGENT_MATCH] == schemas.PASS
    assert checks[schemas.CHECK_SETTLEMENT_MATCH] == schemas.PASS


def test_a_matching_reference_is_required_when_the_chain_event_carries_one():
    # The event names SUB-99999; the only authorization is for SUB-81922.
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(reference='SUB-99999'), authoritative=_authoritative(),
        authorizations=[_authorization()], now=NOW, config=_cfg(),
    )
    assert result.is_anomaly is True
    assert result.deterministic_reason_code == oic.REFERENCE_MISMATCH
    assert result.detection_type == oic.TRANSFER_AGENT_MISMATCH


# --------------------------------------------------------------------------
# 2 + 3. Valid mint + NO authorized issuance -> UNMATCHED_ISSUANCE, exact variance
# --------------------------------------------------------------------------
def test_valid_mint_without_any_authorized_issuance_is_unmatched_issuance():
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=_authoritative(),
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert result.detection_type == oic.UNMATCHED_ISSUANCE
    assert result.deterministic_reason_code == oic.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert result.severity == 'critical'
    assert result.conclusion == schemas.CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY


def test_the_signature_is_valid_and_the_operation_is_still_unauthorized():
    # The whole argument of the screen, asserted as a fact rather than as copy.
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=_authoritative(),
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert result.checks[schemas.CHECK_SIGNER_VALIDITY].status == schemas.PASS
    assert result.checks[schemas.CHECK_ON_CHAIN_EVENT].status == schemas.PASS
    assert result.checks[schemas.CHECK_TRANSFER_AGENT_MATCH].status == schemas.FAIL
    assert result.checks[schemas.CHECK_SETTLEMENT_MATCH].status == schemas.FAIL


def test_observed_5m_against_expected_0_produces_the_exact_variance():
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(amount='5000000'), authoritative=_authoritative(),
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert result.observed_amount == Decimal('5000000')
    assert result.expected_amount == Decimal('0')
    assert result.variance_amount == Decimal('5000000')
    # Exact integers, never a float that lost the low digits.
    assert isinstance(result.variance_amount, Decimal)
    assert str(result.variance_amount) == '5000000'


def test_a_huge_uint256_amount_keeps_every_digit():
    huge = '123456789012345678901234567890123456789'
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(amount=huge), authoritative=_authoritative(),
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert str(result.observed_amount) == huge
    assert str(result.variance_amount) == huge


def test_an_amount_mismatch_is_a_transfer_agent_mismatch_not_a_missing_authorization():
    # An authorization exists for a DIFFERENT amount. Reporting "nothing was
    # authorized" would misdescribe the evidence to the operator.
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(amount='5000000'), authoritative=_authoritative(),
        authorizations=[_authorization(amount=Decimal('4000000'), external_reference=None)],
        now=NOW, config=_cfg(),
    )
    assert result.deterministic_reason_code == oic.AMOUNT_MISMATCH
    assert result.detection_type == oic.TRANSFER_AGENT_MISMATCH
    assert result.expected_amount == Decimal('4000000')


def test_an_uncleared_settlement_keeps_the_transfer_agent_check_passing():
    # The transfer agent DID authorize it; only settlement is incomplete.
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=_authoritative(),
        authorizations=[_authorization(settlement_state='pending', external_reference=None)],
        now=NOW, config=_cfg(),
    )
    assert result.deterministic_reason_code == oic.SETTLEMENT_NOT_COMPLETE
    assert result.checks[schemas.CHECK_TRANSFER_AGENT_MATCH].status == schemas.PASS
    assert result.checks[schemas.CHECK_SETTLEMENT_MATCH].status == schemas.FAIL
    assert result.detection_type == oic.SETTLEMENT_TIMEOUT


# --------------------------------------------------------------------------
# 6. Missing / unavailable / stale operational source -> UNKNOWN, never FAIL
# --------------------------------------------------------------------------
def test_a_missing_authoritative_source_is_unknown_not_a_fabricated_failure():
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=None,
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert result.deterministic_reason_code == oic.AUTHORITATIVE_SOURCE_MISSING
    assert result.checks[schemas.CHECK_TRANSFER_AGENT_MATCH].status == schemas.UNKNOWN
    assert result.checks[schemas.CHECK_SETTLEMENT_MATCH].status == schemas.UNKNOWN
    assert result.conclusion == schemas.CONCLUSION_INDETERMINATE
    assert result.is_anomaly is False


def test_an_unavailable_authoritative_source_never_becomes_an_unauthorized_issuance():
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(),
        authoritative=_authoritative(source_status='unavailable'),
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert result.deterministic_reason_code == oic.AUTHORITATIVE_SOURCE_UNAVAILABLE
    assert result.deterministic_reason_code != oic.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert result.is_anomaly is False
    assert result.is_indeterminate is True


def test_a_stale_authoritative_source_is_indeterminate_and_only_medium():
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(),
        authoritative=_authoritative(observed_at=NOW - timedelta(days=2)),
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert result.deterministic_reason_code == oic.AUTHORITATIVE_SOURCE_STALE
    assert result.conclusion == schemas.CONCLUSION_INDETERMINATE
    # An outage is a data-quality problem, never a critical integrity claim.
    assert result.severity == 'medium'


def test_an_undecodable_operation_is_indeterminate_not_an_anomaly():
    row = {
        'id': 'tel-x', 'asset_id': ASSET, 'event_type': 'erc20_transfer',
        'provider_type': 'evm_rpc', 'evidence_source': 'live', 'observed_at': NOW,
        # A plain wallet-to-wallet transfer: not a mint, not a burn.
        'payload_json': {'tx_hash': TX, 'from': '0x' + '11' * 20, 'to': '0x' + '22' * 20, 'amount': '10'},
    }
    event = normalization.normalize_telemetry_row(row)
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=event, authoritative=_authoritative(),
        authorizations=[], now=NOW, config=_cfg(),
    )
    assert result.deterministic_reason_code == oic.OPERATION_NOT_DECODED
    assert result.is_anomaly is False


# --------------------------------------------------------------------------
# Severity is configuration, never evidence text and never a model
# --------------------------------------------------------------------------
def test_severity_comes_from_configuration():
    cfg = _cfg(severity_by_type={**oic.engine_config()['severity_by_type'], oic.UNMATCHED_ISSUANCE: 'high'})
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=_authoritative(),
        authorizations=[], now=NOW, config=cfg,
    )
    assert result.severity == 'high'
    # Severity drives the conclusion wording, so it must follow the config too.
    assert result.conclusion == schemas.CONCLUSION_OPERATIONAL_ANOMALY


# --------------------------------------------------------------------------
# Settlement timeout
# --------------------------------------------------------------------------
def test_settlement_timeout_fires_only_after_the_deadline_passes():
    fresh = matcher.evaluate_settlement_deadline(
        workspace_id=WS, authorization=_authorization(settlement_state='pending'),
        asset_id=ASSET, asset_name='US Treasury Bond #013', now=NOW, config=_cfg(),
    )
    assert fresh is None  # authorized 10 minutes ago, default deadline is 2 days

    late = matcher.evaluate_settlement_deadline(
        workspace_id=WS,
        authorization=_authorization(settlement_state='pending', authorized_at=NOW - timedelta(days=5)),
        asset_id=ASSET, asset_name='US Treasury Bond #013', now=NOW, config=_cfg(),
    )
    assert late is not None
    assert late.detection_type == oic.SETTLEMENT_TIMEOUT
    assert late.deterministic_reason_code == oic.SETTLEMENT_DEADLINE_EXCEEDED
    assert late.checks[schemas.CHECK_SETTLEMENT_MATCH].status == schemas.FAIL
    assert late.checks[schemas.CHECK_TRANSFER_AGENT_MATCH].status == schemas.PASS


def test_a_settled_authorization_never_times_out():
    assert matcher.evaluate_settlement_deadline(
        workspace_id=WS,
        authorization=_authorization(settlement_state='settled', authorized_at=NOW - timedelta(days=90)),
        asset_id=ASSET, asset_name='A', now=NOW, config=_cfg(),
    ) is None


def test_the_settlement_deadline_prefers_the_authorization_over_the_global_default():
    # An explicit effective_until on the record wins over the global window, so
    # the deadline is never a hardcoded product-wide constant.
    result = matcher.evaluate_settlement_deadline(
        workspace_id=WS,
        authorization=_authorization(
            settlement_state='pending', authorized_at=NOW - timedelta(minutes=30),
            effective_until=NOW - timedelta(minutes=1),
        ),
        asset_id=ASSET, asset_name='A', now=NOW, config=_cfg(),
    )
    assert result is not None
    assert result.provenance['deadline_source'] == 'authorization.effective_until'


# --------------------------------------------------------------------------
# Canonical event object
# --------------------------------------------------------------------------
def test_the_canonical_event_carries_everything_downstream_screens_need():
    result = matcher.evaluate_issuance(
        workspace_id=WS, event=_mint_event(), authoritative=_authoritative(),
        authorizations=[], now=NOW, config=_cfg(),
    )
    payload = result.as_dict()
    for key in (
        'workspace_id', 'asset_id', 'chain_id', 'category', 'detection_type', 'severity',
        'observed_amount', 'expected_amount', 'variance_amount', 'tx_hash', 'telemetry_source',
        'telemetry_stage', 'operational_checks', 'deterministic_reason_code', 'confidence',
        'first_seen_at', 'provenance',
    ):
        assert key in payload, key
    assert payload['category'] == 'OPERATIONAL_INTEGRITY'
    assert payload['detection_type'] == 'UNMATCHED_ISSUANCE'
    assert payload['chain_id'] == 8453
    # Amounts serialize as exact integers, never floats.
    assert payload['observed_amount'] == 5000000
    assert payload['variance_amount'] == 5000000
    assert list(payload['operational_checks']) == list(schemas.CHECK_ORDER)
