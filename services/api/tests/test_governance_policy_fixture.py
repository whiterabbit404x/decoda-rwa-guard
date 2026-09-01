"""The Screen 11 policy fixture the frontend render spec asserts against.

apps/web/tests/settings-policies-render-screen11.spec.ts mounts the real
Settings page and serves it a KNOWN policy + evaluation payload. That payload is
not invented in the spec: it is produced HERE by running the ACTUAL deterministic
engine over the reference policy and serializing it through the ACTUAL endpoint
narrative layer.

This test re-derives the fixture on every backend run and compares it to the
committed file, so the DOM the frontend spec asserts on cannot drift from the
engine without a backend test failing first.

Regenerate with:

    DECODA_WRITE_FIXTURES=1 python -m pytest \\
      services/api/tests/test_governance_policy_fixture.py
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from decimal import Decimal

from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import engine, explanation
from services.api.app.domains.governance_policy.schemas import EvaluationContext, PolicyDefinition

FIXTURE_PATH = pathlib.Path(__file__).parent / 'fixtures' / 'governance_policy_demo.json'

# Fixed so the fixture is byte-stable across runs.
EVALUATED_AT = datetime(2026, 9, 1, 10, 42, 18, tzinfo=timezone.utc)
DENY_EVALUATION_ID = 'f1x7u4e0-0000-0000-0000-000000000001'
ALLOW_EVALUATION_ID = 'f1x7u4e0-0000-0000-0000-000000000002'
POLICY_ID = 'f1x7u4e0-0000-0000-0000-0000000000a1'


def _policy() -> PolicyDefinition:
    return PolicyDefinition(
        policy_id=POLICY_ID,
        policy_key='POL-MINT-007',
        name='RWA Mint Policy',
        operation=gpc.OPERATION_MINT,
        status=gpc.STATUS_ACTIVE,
        version=7,
        workspace_id='f1x7u4e0-0000-0000-0000-0000000000ws',
        required_business_event=gpc.BUSINESS_EVENT_SUBSCRIPTION,
        settlement_requirement=gpc.REQUIREMENT_CLEARED,
        allowed_window_start_utc='08:00',
        allowed_window_end_utc='18:00',
        maximum_daily_amount_usd=Decimal('10000000.00'),
        required_roles=(gpc.ROLE_TREASURY_OPERATOR, gpc.ROLE_COMPLIANCE_APPROVER),
        updated_at=EVALUATED_AT,
    )


def _context(*, compliance_approval: bool) -> EvaluationContext:
    return EvaluationContext(
        operation=gpc.OPERATION_MINT,
        amount_usd=Decimal('5000000'),
        operator_id='f1x7u4e0-0000-0000-0000-0000000000u1',
        operator_has_treasury_role=True,
        business_event=gpc.BUSINESS_EVENT_SUBSCRIPTION,
        settlement_status=gpc.SETTLEMENT_CLEARED,
        compliance_approval=compliance_approval,
        evaluated_at=EVALUATED_AT,
        daily_total_usd=Decimal('0'),
        canonical_event_id='EVT-928181',
        simulation=True,
    )


def build_fixture() -> dict:
    policy = _policy()
    deny = engine.evaluate_policy(
        policy, _context(compliance_approval=False),
        evaluation_id=DENY_EVALUATION_ID, now=EVALUATED_AT,
    )
    allow = engine.evaluate_policy(
        policy, _context(compliance_approval=True),
        evaluation_id=ALLOW_EVALUATION_ID, now=EVALUATED_AT,
    )
    return {
        'policy': policy.as_dict(),
        # Serialized through the real narrative layer with AI disabled — the
        # deterministic default every deployment falls back to.
        'deny_evaluation': explanation.explain(deny.as_dict(), config={'enabled': False}),
        'allow_evaluation': explanation.explain(allow.as_dict(), config={'enabled': False}),
        'history': [
            {
                'version': 7, 'status': 'ACTIVE',
                'change_summary': 'Maximum issuance changed: 5000000.00 → 10000000.00',
                'previous_values': {'maximum_daily_amount_usd': '5000000.00'},
                'new_values': {'maximum_daily_amount_usd': '10000000.00'},
                'changed_by': 'admin@acme.test',
                'changed_by_user_id': 'f1x7u4e0-0000-0000-0000-0000000000u1',
                'changed_at': '2026-08-30T09:15:00+00:00',
            },
            {
                'version': 6, 'status': 'ACTIVE',
                'change_summary': 'Required roles changed: TREASURY_OPERATOR → TREASURY_OPERATOR, COMPLIANCE_APPROVER',
                'previous_values': {'required_roles': ['TREASURY_OPERATOR']},
                'new_values': {'required_roles': ['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER']},
                'changed_by': 'admin@acme.test',
                'changed_by_user_id': 'f1x7u4e0-0000-0000-0000-0000000000u1',
                'changed_at': '2026-08-12T14:02:00+00:00',
            },
        ],
    }


def test_the_committed_fixture_matches_what_the_engine_produces_today():
    fixture = build_fixture()
    if os.getenv('DECODA_WRITE_FIXTURES'):
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(json.dumps(fixture, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    assert FIXTURE_PATH.exists(), f'missing fixture: run with DECODA_WRITE_FIXTURES=1'
    committed = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
    assert committed == fixture, (
        'The Screen 11 policy fixture is stale. The frontend render spec asserts on it, so '
        'regenerate it with DECODA_WRITE_FIXTURES=1 and review the diff.'
    )


def test_the_fixture_carries_the_reference_scenario_from_the_design():
    fixture = build_fixture()
    assert fixture['policy']['policy_key'] == 'POL-MINT-007'
    assert fixture['policy']['version'] == 7
    assert fixture['policy']['status'] == 'ACTIVE'
    assert fixture['deny_evaluation']['decision'] == 'DENY'
    assert fixture['deny_evaluation']['reason_codes'] == ['COMPLIANCE_APPROVAL_MISSING']
    assert fixture['allow_evaluation']['decision'] == 'ALLOW'
    # The narrative in the fixture is the deterministic one, never a model's.
    assert fixture['deny_evaluation']['ai_explanation_source'] == 'deterministic'
