"""Plan entitlement engine — limits, features, lifecycle, and error shapes.

The entitlement engine is the ONE place a plan's capabilities are written down,
so these tests are what keep it aligned with the published pricing page
(apps/web/app/pricing-plans.ts) and with the truthfulness rules:

  * a countdown exists only for a plan that has an evaluation window
  * an unrecognised plan falls back to the STRICTEST plan, never the most
    permissive one
  * an unlimited limit is reported as None, never as a large number
  * an expired or suspended tenant is refused with its own reason code, not with
    a limit message that would imply a different remedy

Run:
    python -m pytest services/api/tests/test_plan_entitlements_engine.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from services.api.app import entitlements as ent

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _org(**overrides):
    base = {
        'id': 'org-1',
        'name': 'ABC Tokenization',
        'plan': ent.PLAN_PILOT,
        'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=7),
        'evaluation_expires_at': NOW + timedelta(days=23),
        'entitlement_overrides': {},
    }
    base.update(overrides)
    return base


# ── published pricing must match the engine ───────────────────────────────────

def test_pilot_limits_match_published_pricing() -> None:
    entitlements = ent.get_entitlements(_org(plan='pilot'))
    assert entitlements[ent.LIMIT_WORKSPACES] == 1
    assert entitlements[ent.LIMIT_MONITORED_CONTRACTS] == 5
    assert entitlements[ent.LIMIT_EVIDENCE_PACKAGES] == 10
    assert entitlements['evidence_unlimited'] is False


def test_scale_limits_match_published_pricing() -> None:
    entitlements = ent.get_entitlements(_org(plan='scale'))
    assert entitlements[ent.LIMIT_WORKSPACES] == 3
    assert entitlements[ent.LIMIT_MONITORED_CONTRACTS] == 25
    assert entitlements[ent.LIMIT_EVIDENCE_PACKAGES] is None
    assert entitlements['evidence_unlimited'] is True


def test_enterprise_limits_are_unlimited_by_default() -> None:
    entitlements = ent.get_entitlements(_org(plan='enterprise'))
    for key in ent.LIMIT_KEYS:
        assert ent.limit_for(entitlements, key) is None


def test_pilot_keeps_the_full_detection_and_evidence_workflow() -> None:
    """Pilot is a scoped evaluation, not a crippled product."""
    entitlements = ent.get_entitlements(_org(plan='pilot'))
    for feature in (
        ent.FEATURE_THREAT_MONITORING,
        ent.FEATURE_AI_INVESTIGATION,
        ent.FEATURE_RESPONSE_RECOMMENDATIONS,
        ent.FEATURE_EVIDENCE_EXPORT,
    ):
        assert ent.has_entitlement(entitlements, feature) is True


def test_no_plan_enables_automatic_execution_by_default() -> None:
    """AI recommends; a deterministic engine decides; a human authorizes."""
    for plan in ent.PLANS:
        entitlements = ent.get_entitlements(_org(plan=plan))
        assert ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION) is False


# ── fail-closed normalisation ─────────────────────────────────────────────────

def test_unknown_plan_falls_back_to_the_strictest_plan() -> None:
    assert ent.normalize_plan('platinum') == ent.PLAN_PILOT
    assert ent.normalize_plan(None) == ent.PLAN_PILOT
    entitlements = ent.get_entitlements(_org(plan='platinum'))
    assert entitlements[ent.LIMIT_MONITORED_CONTRACTS] == 5


def test_unknown_status_is_treated_as_suspended_not_active() -> None:
    assert ent.normalize_status('whatever') == ent.STATUS_SUSPENDED
    assert ent.normalize_status('') == ent.STATUS_ACTIVE
    assert ent.lifecycle_state(_org(status='whatever')) == ent.LIFECYCLE_SUSPENDED


def test_missing_organization_yields_the_strictest_entitlements() -> None:
    entitlements = ent.get_entitlements(None)
    assert entitlements[ent.LIMIT_MONITORED_CONTRACTS] == 5
    assert ent.has_entitlement(entitlements, ent.FEATURE_MULTI_NETWORK) is False


# ── overrides ─────────────────────────────────────────────────────────────────

def test_enterprise_overrides_apply_for_known_keys() -> None:
    org = _org(plan='enterprise', entitlement_overrides={
        ent.LIMIT_MONITORED_CONTRACTS: 500,
        ent.FEATURE_AUTOMATIC_EXECUTION: True,
    })
    entitlements = ent.get_entitlements(org)
    assert entitlements[ent.LIMIT_MONITORED_CONTRACTS] == 500
    assert ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION) is True


def test_unknown_and_malformed_overrides_are_discarded() -> None:
    org = _org(entitlement_overrides={
        'max_monitored_contracts': 'not-a-number',
        'max_workspaces': -3,
        'ai_investigation': 'yes',
        'shell_access': True,
    })
    entitlements = ent.get_entitlements(org)
    assert entitlements[ent.LIMIT_MONITORED_CONTRACTS] == 5      # override rejected
    assert entitlements[ent.LIMIT_WORKSPACES] == 1               # negative rejected
    assert entitlements[ent.FEATURE_AI_INVESTIGATION] is True    # non-bool rejected
    assert 'shell_access' not in entitlements                    # unknown key dropped


def test_override_can_express_unlimited_as_none() -> None:
    org = _org(entitlement_overrides={ent.LIMIT_EVIDENCE_PACKAGES: None})
    entitlements = ent.get_entitlements(org)
    assert ent.limit_for(entitlements, ent.LIMIT_EVIDENCE_PACKAGES) is None
    assert entitlements['evidence_unlimited'] is True


# ── evaluation window ─────────────────────────────────────────────────────────

def test_evaluation_duration_is_centralised_and_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ent.EVALUATION_DAYS_ENV, raising=False)
    assert ent.evaluation_days() == ent.DEFAULT_EVALUATION_DAYS == 30
    monkeypatch.setenv(ent.EVALUATION_DAYS_ENV, '45')
    assert ent.evaluation_days() == 45
    monkeypatch.setenv(ent.EVALUATION_DAYS_ENV, 'garbage')
    assert ent.evaluation_days() == ent.DEFAULT_EVALUATION_DAYS
    monkeypatch.setenv(ent.EVALUATION_DAYS_ENV, '99999')
    assert ent.evaluation_days() == ent.MAX_EVALUATION_DAYS


def test_days_remaining_counts_down_for_pilot() -> None:
    assert ent.days_remaining(_org(), now=NOW) == 23


def test_days_remaining_is_none_outside_an_evaluation_plan() -> None:
    """A Scale or Enterprise tenant must never render a countdown."""
    for plan in ('scale', 'enterprise'):
        org = _org(plan=plan, evaluation_expires_at=NOW + timedelta(days=23))
        assert ent.days_remaining(org, now=NOW) is None
        assert ent.evaluation_payload(org, now=NOW) is None


def test_days_remaining_is_none_when_no_deadline_is_recorded() -> None:
    """A missing date is not evidence of expiry, so no countdown is invented."""
    org = _org(evaluation_expires_at=None)
    assert ent.days_remaining(org, now=NOW) is None
    assert ent.evaluation_expired(org, now=NOW) is False


def test_expired_evaluation_reports_zero_days_and_expired_state() -> None:
    org = _org(evaluation_expires_at=NOW - timedelta(days=1))
    assert ent.evaluation_expired(org, now=NOW) is True
    assert ent.days_remaining(org, now=NOW) == 0
    assert ent.lifecycle_state(org, now=NOW) == ent.LIFECYCLE_EXPIRED_PILOT
    payload = ent.evaluation_payload(org, now=NOW)
    assert payload is not None and payload['expired'] is True


# ── lifecycle ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    'org_kwargs,expected',
    [
        ({}, ent.LIFECYCLE_ACTIVE_PILOT),
        ({'evaluation_expires_at': NOW - timedelta(days=1)}, ent.LIFECYCLE_EXPIRED_PILOT),
        ({'status': 'suspended'}, ent.LIFECYCLE_SUSPENDED),
        ({'plan': 'scale'}, ent.LIFECYCLE_ACTIVE_SCALE),
        ({'plan': 'enterprise'}, ent.LIFECYCLE_ENTERPRISE),
    ],
)
def test_lifecycle_states(org_kwargs, expected) -> None:
    assert ent.lifecycle_state(_org(**org_kwargs), now=NOW) == expected


def test_suspension_outranks_plan_and_evaluation() -> None:
    org = _org(plan='enterprise', status='suspended')
    assert ent.lifecycle_state(org, now=NOW) == ent.LIFECYCLE_SUSPENDED
    assert ent.monitoring_allowed(org, now=NOW) is False


def test_expired_pilot_stops_expensive_work_but_keeps_the_tenant() -> None:
    org = _org(evaluation_expires_at=NOW - timedelta(days=1))
    assert ent.monitoring_allowed(org, now=NOW) is False
    assert ent.provisioning_allowed(org, now=NOW) is False
    # Nothing here revokes read access or destroys data; the engine only ever
    # answers "may this tenant start new expensive work".
    assert ent.get_entitlements(org)[ent.LIMIT_MONITORED_CONTRACTS] == 5


# ── enforcement error shapes ──────────────────────────────────────────────────

def test_check_limit_allows_below_the_cap() -> None:
    ent.check_limit(_org(), ent.LIMIT_MONITORED_CONTRACTS, 4)


def test_check_limit_raises_the_canonical_plan_limit_body() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ent.check_limit(_org(), ent.LIMIT_MONITORED_CONTRACTS, 5)
    assert exc_info.value.status_code == 403
    detail = exc_info.value.detail
    assert detail['code'] == 'PLAN_LIMIT_REACHED'
    assert detail['resource'] == 'monitored_contracts'
    assert detail['limit'] == 5
    assert detail['current'] == 5
    assert detail['plan'] == 'pilot'


def test_unlimited_limit_never_raises() -> None:
    ent.check_limit(_org(plan='scale'), ent.LIMIT_EVIDENCE_PACKAGES, 10_000)


def test_scale_limits_are_enforced_independently_of_pilot() -> None:
    scale = _org(plan='scale')
    ent.check_limit(scale, ent.LIMIT_MONITORED_CONTRACTS, 24)
    with pytest.raises(HTTPException) as exc_info:
        ent.check_limit(scale, ent.LIMIT_MONITORED_CONTRACTS, 25)
    assert exc_info.value.detail['limit'] == 25
    assert exc_info.value.detail['plan'] == 'scale'


def test_expired_tenant_is_refused_before_the_limit_is_consulted() -> None:
    """The message must name the real remedy, not imply a count problem."""
    org = _org(evaluation_expires_at=NOW - timedelta(days=1))
    with pytest.raises(HTTPException) as exc_info:
        ent.enforce_resource_creation(org, ent.LIMIT_MONITORED_CONTRACTS, 0, now=NOW)
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert exc_info.value.detail['lifecycle_state'] == ent.LIFECYCLE_EXPIRED_PILOT


def test_suspended_tenant_is_refused_with_its_own_code() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ent.enforce_resource_creation(_org(status='suspended'), ent.LIMIT_WORKSPACES, 0, now=NOW)
    assert exc_info.value.detail['code'] == ent.CODE_ORGANIZATION_SUSPENDED


def test_require_entitlement_raises_for_a_missing_feature() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ent.require_entitlement(_org(), ent.FEATURE_AUTOMATIC_EXECUTION)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'PLAN_ENTITLEMENT_REQUIRED'
    assert exc_info.value.detail['entitlement'] == 'automatic_execution'


def test_unknown_entitlement_keys_are_a_programming_error_not_a_silent_pass() -> None:
    with pytest.raises(ValueError):
        ent.has_entitlement(ent.get_entitlements(_org()), 'root_access')
    with pytest.raises(ValueError):
        ent.limit_for(ent.get_entitlements(_org()), 'max_everything')


# ── upgrade preserves nothing but the plan ────────────────────────────────────

def test_upgrade_from_pilot_to_scale_changes_only_the_governing_limits() -> None:
    """Plan change is an entitlement change, not a migration.

    The organization row keeps its identity; only what it is allowed to do
    changes, which is what makes Pilot → Scale a one-row update.
    """
    pilot_org = _org()
    scale_org = {**pilot_org, 'plan': 'scale', 'evaluation_started_at': None, 'evaluation_expires_at': None}
    assert scale_org['id'] == pilot_org['id']
    assert ent.get_entitlements(pilot_org)[ent.LIMIT_MONITORED_CONTRACTS] == 5
    assert ent.get_entitlements(scale_org)[ent.LIMIT_MONITORED_CONTRACTS] == 25
    assert ent.evaluation_payload(scale_org, now=NOW) is None
