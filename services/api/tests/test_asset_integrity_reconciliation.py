"""Deterministic asset reconciliation engine — the Screen 3 Integrity verdict.

Pure unit tests (no DB). These cover the required cases A-F from the Screen 3
spec plus the truthfulness invariants that must never be relaxed:

  * an upstream failure is NEVER reported as UNEXPLAINED_VARIANCE,
  * a cryptographically valid transaction is NOT an authorization,
  * severity is deterministic and never AI-decided,
  * every result carries the rule id/version it was produced under.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.api.app.domains.asset_integrity import reconciliation as r

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
RULES = r.ReconciliationRules()


def _onchain(supply, **kw):
    base = dict(total_supply=Decimal(str(supply)), observed_at=NOW, provider_type='evm_rpc', evidence_source='live')
    base.update(kw)
    return r.OnChainObservation(**base)


def _authoritative(expected, **kw):
    base = dict(
        expected_total_supply=(None if expected is None else Decimal(str(expected))),
        observed_at=NOW, settlement_state='settled', source_name='Transfer Agent',
        source_status='reported', evidence_source='live',
    )
    base.update(kw)
    return r.AuthoritativeState(**base)


def _issuance(amount, **kw):
    base = dict(
        id='iss-1', operation='mint', amount=Decimal(str(amount)), settlement_state='settled',
        authorized_at=NOW, source_name='Transfer Agent', evidence_source='live',
    )
    base.update(kw)
    return r.AuthorizedIssuance(**base)


def _evaluate(**kw):
    kw.setdefault('rules', RULES)
    kw.setdefault('now', NOW)
    kw.setdefault('authorizations', [])
    return r.evaluate(**kw)


# --------------------------------------------------------------------------
# CASE A — exact match
# --------------------------------------------------------------------------
def test_case_a_exact_match_is_reconciled_with_zero_variance():
    res = _evaluate(onchain=_onchain(4_500_000), authoritative=_authoritative(4_500_000))
    assert res.status == r.RECONCILED
    assert res.reason_code == r.SUPPLY_MATCHES_AUTHORITATIVE_STATE
    assert res.variance_units == Decimal('0')
    assert res.severity == 'low'
    assert res.is_healthy is True
    assert res.is_anomaly is False


# --------------------------------------------------------------------------
# CASE B — unauthorized mint (the canonical Screen 3 scenario)
# --------------------------------------------------------------------------
def test_case_b_unauthorized_mint_is_unexplained_variance():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[],
    )
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert res.variance_units == Decimal('500000')
    assert res.observed_supply == Decimal('5000000')
    assert res.expected_supply == Decimal('4500000')
    assert res.is_anomaly is True


def test_case_b_severity_is_critical_for_unauthorized_issuance():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
    )
    assert res.severity == 'critical'


def test_case_b_result_carries_rule_id_and_version():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
    )
    assert res.rule_id == 'RP-17'
    assert res.rule_version == 4
    assert res.rule_config['authoritative_stale_seconds'] == 3600


def test_variance_is_exact_integer_arithmetic_not_float():
    # A uint256-scale supply must survive without binary-float error.
    observed = Decimal('5000000000000000000000001')
    expected = Decimal('5000000000000000000000000')
    res = _evaluate(
        onchain=_onchain(observed, last_delta=Decimal('1'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(expected),
    )
    assert res.variance_units == Decimal('1')
    assert isinstance(res.variance_units, Decimal)


# --------------------------------------------------------------------------
# CASE C — valid authorized mint
# --------------------------------------------------------------------------
def test_case_c_matching_settled_authorization_is_not_an_anomaly():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(500_000, settlement_state='settled')],
    )
    assert res.status == r.AUTHORIZED_VARIANCE
    assert res.reason_code == r.MATCHED_AUTHORIZED_ISSUANCE
    assert res.is_anomaly is False
    assert res.is_healthy is True
    assert res.severity == 'low'
    assert res.matched_issuance_id == 'iss-1'


def test_authorized_redemption_matches_a_burn():
    res = _evaluate(
        onchain=_onchain(4_000_000, last_delta=Decimal('500000'), last_delta_operation='burn', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(500_000, operation='burn')],
    )
    assert res.status == r.AUTHORIZED_VARIANCE
    assert res.reason_code == r.MATCHED_AUTHORIZED_REDEMPTION
    assert res.variance_units == Decimal('-500000')


def test_unauthorized_burn_reports_the_redemption_reason_code():
    res = _evaluate(
        onchain=_onchain(4_000_000, last_delta=Decimal('500000'), last_delta_operation='burn', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[],
    )
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.NO_MATCHING_AUTHORIZED_REDEMPTION


# --------------------------------------------------------------------------
# CASE D — amount mismatch
# --------------------------------------------------------------------------
def test_case_d_amount_mismatch_is_an_unexplained_variance_with_its_own_reason():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(400_000)],
    )
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.AMOUNT_MISMATCH
    assert res.variance_units == Decimal('500000')


# --------------------------------------------------------------------------
# CASE E — authoritative source unavailable
# --------------------------------------------------------------------------
def test_case_e_source_unavailable_is_not_an_unexplained_variance():
    res = _evaluate(
        onchain=_onchain(5_000_000),
        authoritative=_authoritative(None, source_status='unavailable'),
    )
    assert res.status == r.SOURCE_UNAVAILABLE
    assert res.reason_code == r.AUTHORITATIVE_SOURCE_UNAVAILABLE
    assert res.status != r.UNEXPLAINED_VARIANCE
    assert res.is_anomaly is False
    assert res.is_healthy is False  # and it is NOT healthy either
    assert res.is_indeterminate is True


def test_case_e_source_error_is_also_source_unavailable():
    res = _evaluate(onchain=_onchain(5_000_000), authoritative=_authoritative(4_500_000, source_status='error'))
    assert res.status == r.SOURCE_UNAVAILABLE


def test_missing_authoritative_record_is_missing_not_variance():
    res = _evaluate(onchain=_onchain(5_000_000), authoritative=None)
    assert res.status == r.MISSING_AUTHORITATIVE_DATA
    assert res.reason_code == r.AUTHORITATIVE_SOURCE_MISSING
    assert res.is_anomaly is False


def test_reported_source_with_no_expected_value_is_missing_data():
    res = _evaluate(onchain=_onchain(5_000_000), authoritative=_authoritative(None))
    assert res.status == r.MISSING_AUTHORITATIVE_DATA
    assert res.reason_code == r.AUTHORITATIVE_SOURCE_MISSING


def test_source_unavailable_severity_is_medium_never_critical():
    res = _evaluate(onchain=_onchain(5_000_000), authoritative=_authoritative(None, source_status='unavailable'))
    assert res.severity == 'medium'


# --------------------------------------------------------------------------
# CASE F — stale authoritative data
# --------------------------------------------------------------------------
def test_case_f_stale_authoritative_data_is_stale_not_variance():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000, observed_at=NOW - timedelta(hours=9)),
    )
    assert res.status == r.STALE_AUTHORITATIVE_DATA
    assert res.reason_code == r.AUTHORITATIVE_SOURCE_STALE
    assert res.is_anomaly is False
    assert res.is_healthy is False
    # The variance is still reported (it is a fact) — it is just not adjudicated.
    assert res.variance_units == Decimal('500000')
    assert res.authoritative_age_seconds == 9 * 3600


def test_authoritative_data_inside_the_freshness_window_is_adjudicated():
    res = _evaluate(
        onchain=_onchain(4_500_000),
        authoritative=_authoritative(4_500_000, observed_at=NOW - timedelta(minutes=30)),
    )
    assert res.status == r.RECONCILED


def test_stale_onchain_observation_cannot_support_a_verdict():
    res = _evaluate(
        onchain=_onchain(5_000_000, observed_at=NOW - timedelta(hours=5)),
        authoritative=_authoritative(4_500_000),
    )
    assert res.status == r.INSUFFICIENT_EVIDENCE
    assert res.reason_code == r.ONCHAIN_OBSERVATION_STALE


def test_missing_onchain_observation_is_insufficient_evidence():
    res = _evaluate(onchain=None, authoritative=_authoritative(4_500_000))
    assert res.status == r.INSUFFICIENT_EVIDENCE
    assert res.reason_code == r.ONCHAIN_OBSERVATION_MISSING
    assert res.is_healthy is False


# --------------------------------------------------------------------------
# The matcher — a valid signature is not an authorization
# --------------------------------------------------------------------------
def test_unsettled_authorization_does_not_authorize_a_mint():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(500_000, settlement_state='pending')],
    )
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.SETTLEMENT_NOT_COMPLETE


def test_reference_mismatch_blocks_the_match():
    res = _evaluate(
        onchain=_onchain(
            5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint',
            last_delta_at=NOW, external_reference='SUB-81922',
        ),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(500_000, external_reference='SUB-00000')],
    )
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.REFERENCE_MISMATCH


def test_matching_reference_is_case_and_whitespace_insensitive():
    res = _evaluate(
        onchain=_onchain(
            5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint',
            last_delta_at=NOW, external_reference=' sub-81922 ',
        ),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(500_000, external_reference='SUB-81922')],
    )
    assert res.status == r.AUTHORIZED_VARIANCE


def test_authorization_outside_its_effective_window_does_not_authorize():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(
            500_000,
            effective_from=NOW - timedelta(days=10),
            effective_until=NOW - timedelta(days=5),
        )],
    )
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.OUTSIDE_AUTHORIZED_WINDOW


def test_authorization_far_outside_the_match_window_does_not_authorize():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(500_000, authorized_at=NOW - timedelta(days=30))],
    )
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.OUTSIDE_AUTHORIZED_WINDOW


def test_an_authorization_for_the_other_operation_is_not_a_near_miss():
    # A burn authorization must not be reported as an "amount mismatch" for a mint.
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(500_000, operation='burn')],
    )
    assert res.reason_code == r.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert res.match.candidates_considered == 0


def test_matcher_reports_the_most_specific_rejection_reason():
    # Settlement-not-complete is more informative than amount-mismatch, so it wins.
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(400_000, id='a'), _issuance(500_000, id='b', settlement_state='pending')],
    )
    assert res.reason_code == r.SETTLEMENT_NOT_COMPLETE


def test_matcher_finds_the_authorization_among_several_candidates():
    res = _evaluate(
        onchain=_onchain(5_000_000, last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=_authoritative(4_500_000),
        authorizations=[_issuance(100_000, id='a'), _issuance(500_000, id='b'), _issuance(900_000, id='c')],
    )
    assert res.status == r.AUTHORIZED_VARIANCE
    assert res.matched_issuance_id == 'b'


def test_variance_without_a_discrete_event_infers_the_operation_from_direction():
    res = _evaluate(onchain=_onchain(5_000_000), authoritative=_authoritative(4_500_000))
    assert res.status == r.UNEXPLAINED_VARIANCE
    assert res.reason_code == r.NO_MATCHING_AUTHORIZED_ISSUANCE


def test_settlement_states_that_count_as_complete():
    for state in ('settled', 'Cleared', 'COMPLETE', 'finalized'):
        assert r.is_settled(state) is True
    for state in ('pending', 'in_flight', '', None, 'unknown'):
        assert r.is_settled(state) is False


# --------------------------------------------------------------------------
# Tolerance + severity mapping
# --------------------------------------------------------------------------
def test_configured_tolerance_absorbs_dust_variance():
    rules = r.ReconciliationRules(variance_tolerance_units=Decimal('10'))
    res = _evaluate(onchain=_onchain(4_500_005), authoritative=_authoritative(4_500_000), rules=rules)
    assert res.status == r.RECONCILED
    assert res.variance_units == Decimal('5')


def test_tolerance_does_not_absorb_a_real_variance():
    rules = r.ReconciliationRules(variance_tolerance_units=Decimal('10'))
    res = _evaluate(onchain=_onchain(4_500_050), authoritative=_authoritative(4_500_000), rules=rules)
    assert res.status == r.UNEXPLAINED_VARIANCE


def test_severity_never_exceeds_medium_for_indeterminate_states():
    # A state we could not establish is never escalated to high/critical, however
    # large the numbers attached to it are.
    for status_value in sorted(r.INDETERMINATE_STATUSES):
        assert r.compute_severity(
            status=status_value, reason_code='X', variance_units=Decimal('999999999'),
            expected_supply=Decimal('1'), operation='mint',
        ) in ('low', 'medium')


def test_an_unestablished_state_is_medium_but_a_non_applicable_one_is_low():
    # Missing/stale/unavailable evidence is a data-quality problem to chase.
    for status_value in (r.STALE_AUTHORITATIVE_DATA, r.MISSING_AUTHORITATIVE_DATA,
                         r.INSUFFICIENT_EVIDENCE, r.SOURCE_UNAVAILABLE):
        assert r.compute_severity(
            status=status_value, reason_code='X', variance_units=None,
            expected_supply=None, operation=None,
        ) == 'medium'
    # A dimension that does not apply to the asset is not a gap to chase at all.
    assert r.compute_severity(
        status=r.NOT_APPLICABLE, reason_code=r.SUPPLY_RECONCILIATION_NOT_APPLICABLE,
        variance_units=None, expected_supply=None, operation=None,
    ) == 'low'


def test_severity_for_a_healthy_result_is_low():
    assert r.compute_severity(
        status=r.RECONCILED, reason_code=r.SUPPLY_MATCHES_AUTHORITATIVE_STATE,
        variance_units=Decimal('0'), expected_supply=Decimal('100'), operation=None,
    ) == 'low'


def test_unexplained_burn_variance_is_high_not_silently_low():
    severity = r.compute_severity(
        status=r.UNEXPLAINED_VARIANCE, reason_code=r.AMOUNT_MISMATCH,
        variance_units=Decimal('-500000'), expected_supply=Decimal('4500000'), operation='burn',
    )
    assert severity == 'high'


# --------------------------------------------------------------------------
# Status taxonomy invariants
# --------------------------------------------------------------------------
def test_no_status_is_both_healthy_and_anomalous():
    assert r.ANOMALY_STATUSES.isdisjoint(r.INDETERMINATE_STATUSES)
    assert r.RECONCILED not in r.ANOMALY_STATUSES
    assert r.RECONCILED not in r.INDETERMINATE_STATUSES
    assert r.AUTHORIZED_VARIANCE not in r.ANOMALY_STATUSES


def test_every_declared_status_is_classified():
    for status_value in r.RECONCILIATION_STATUSES:
        healthy = status_value in (r.RECONCILED, r.AUTHORIZED_VARIANCE)
        assert healthy or status_value in r.ANOMALY_STATUSES or status_value in r.INDETERMINATE_STATUSES


# --------------------------------------------------------------------------
# Applicability — resolved before every evidence gap
# --------------------------------------------------------------------------
def test_supply_reconciliation_that_does_not_apply_is_not_an_evidence_gap():
    """A wallet has no token supply, so "no observation stored" would name a gap
    that can never close. NOT_APPLICABLE says so instead."""
    result = r.evaluate(onchain=None, authoritative=None, supply_applicable=False)
    assert result.status == r.NOT_APPLICABLE
    assert result.reason_code == r.SUPPLY_RECONCILIATION_NOT_APPLICABLE
    assert result.status != r.INSUFFICIENT_EVIDENCE
    assert result.reason_code != r.ONCHAIN_OBSERVATION_MISSING


def test_a_not_applicable_result_is_neither_healthy_nor_an_anomaly():
    result = r.evaluate(onchain=None, authoritative=None, supply_applicable=False)
    assert result.is_anomaly is False
    assert result.is_indeterminate is True
    assert result.status not in (r.RECONCILED, r.AUTHORIZED_VARIANCE)


def test_a_not_applicable_result_carries_no_variance_or_supply():
    result = r.evaluate(onchain=None, authoritative=None, supply_applicable=False)
    assert result.variance_units is None
    assert result.observed_supply is None
    assert result.expected_supply is None


def test_applicability_outranks_a_full_set_of_usable_inputs():
    """Even with inputs that would otherwise produce a verdict, a dimension that
    does not apply cannot yield one."""
    result = r.evaluate(
        onchain=r.OnChainObservation(total_supply=Decimal('5000000'), observed_at=NOW, available=True),
        authoritative=r.AuthoritativeState(
            expected_total_supply=Decimal('5000000'), observed_at=NOW,
            settlement_state='settled', source_status='reported',
        ),
        now=NOW,
        supply_applicable=False,
    )
    assert result.status == r.NOT_APPLICABLE
    assert result.variance_units is None


def test_supply_applicable_defaults_to_true_so_existing_callers_are_unchanged():
    assert r.evaluate(onchain=None, authoritative=None).status == r.INSUFFICIENT_EVIDENCE
    assert r.evaluate(onchain=None, authoritative=None).reason_code == r.ONCHAIN_OBSERVATION_MISSING
