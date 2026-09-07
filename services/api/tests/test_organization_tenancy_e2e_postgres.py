"""The staging acceptance sequence (TEST A–H), executed against real PostgreSQL.

Everything else in the tenancy suite tests a layer in isolation. This test walks
the whole customer path once, through the real handlers, against a real database
— signup, limits, isolation, evidence, the execution lock, expiry, upgrade, and
the founder console — so the sequence a human would run by hand on staging is
also run by CI.

Order matters here: each phase depends on the state the previous one left, which
is exactly why it is written as one ordered scenario rather than as independent
cases.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_organization_tenancy_e2e_postgres.py -q

Skipped entirely when that DSN is absent, so the default suite stays hermetic.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

_DSN = os.environ.get('DECODA_MIGRATION_TEST_DSN')
_PSQL = shutil.which('psql')


def _real_psycopg():
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_DSN and _PSQL),
        reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have '
               'psql on PATH to run the tenancy acceptance sequence',
    ),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
_PASSWORD = 'Str0ng!Passw0rd#2026'


def _request(token: str, workspace_id: str | None = None) -> SimpleNamespace:
    headers = {'authorization': f'Bearer {token}', 'user-agent': 'tenancy-acceptance'}
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host='127.0.0.1'),
        scope={'path': '/acceptance', 'type': 'http'},
        method='POST',
        query_params={},
    )


@pytest.fixture(scope='module')
def live():
    """A migrated database plus the live-mode environment the handlers require."""
    previous = {
        key: os.environ.get(key)
        for key in ('DATABASE_URL', 'LIVE_MODE_ENABLED', 'APP_MODE', 'EMAIL_PROVIDER')
    }
    os.environ.update({
        'DATABASE_URL': _DSN, 'LIVE_MODE_ENABLED': 'true', 'APP_MODE': 'live',
        'EMAIL_PROVIDER': 'console',
    })
    os.environ.setdefault('AUTH_TOKEN_SECRET', 'x' * 48)
    os.environ.setdefault('TOKEN_SECRET', 'x' * 48)

    from psycopg.rows import dict_row

    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
        for path in sorted(_MIGRATIONS.glob('*.sql')):
            connection.execute(path.read_text())

    with psycopg.connect(_DSN, autocommit=True, row_factory=dict_row) as connection:
        yield connection

    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')


def _sign_up(connection, *, label: str, email: str) -> dict[str, str]:
    """A real self-serve signup, then a verified session for it."""
    from services.api.app import pilot

    result = pilot.signup_user(
        {'email': email, 'password': _PASSWORD, 'full_name': label, 'workspace_name': f'{label} Workspace'},
        _request('bootstrap'),
    )
    user_id = result['user']['id']
    connection.execute('UPDATE users SET email_verified_at = NOW() WHERE id = %s', (user_id,))
    token = pilot.create_access_token(user_id, 1)
    connection.execute(
        'INSERT INTO auth_sessions (id, user_id, session_token_hash, expires_at, created_at, updated_at)'
        ' VALUES (%s, %s, %s, %s, NOW(), NOW())',
        (str(uuid.uuid4()), user_id, pilot._auth_token_hash(token),
         datetime.now(timezone.utc) + timedelta(hours=8)),
    )
    workspace_id = str(connection.execute(
        'SELECT current_workspace_id AS w FROM users WHERE id = %s', (user_id,),
    ).fetchone()['w'])
    organization_id = str(connection.execute(
        'SELECT organization_id AS o FROM workspaces WHERE id = %s', (workspace_id,),
    ).fetchone()['o'])
    return {'user_id': user_id, 'token': token, 'workspace_id': workspace_id,
            'organization_id': organization_id}


@pytest.fixture(scope='module')
def tenants(live) -> dict[str, dict[str, str]]:
    return {
        'a': _sign_up(live, label='ABC Tokenization', email='owner@abc.example'),
        'b': _sign_up(live, label='XYZ Capital', email='owner@xyz.example'),
    }


# ── TEST A — Pilot onboarding ────────────────────────────────────────────────

def test_A_signup_creates_a_pilot_organization_and_plan_surface(live, tenants) -> None:
    from services.api.app import organizations as org_service
    from services.api.app.domains.tenancy import endpoints as tenancy

    tenant = tenants['a']
    plan = tenancy.get_account_plan(_request(tenant['token'], tenant['workspace_id']))
    assert plan['state'] == 'available'
    assert plan['organization']['id'] == tenant['organization_id']
    assert plan['plan'] == 'pilot'
    assert plan['status'] == 'active'
    assert plan['lifecycle_state'] == 'ACTIVE_PILOT'
    # The badge's countdown comes from the configured default, not a literal.
    assert plan['evaluation']['days_remaining'] in (29, 30)
    assert plan['usage']['monitored_contracts'] == {'current': 0, 'limit': 5}
    assert plan['usage']['workspaces'] == {'current': 1, 'limit': 1}
    assert plan['usage']['evidence_packages'] == {'current': 0, 'limit': 10}
    assert org_service.membership_role(
        live, organization_id=tenant['organization_id'], user_id=tenant['user_id'],
    ) == 'owner'


# ── TEST B — Limits ──────────────────────────────────────────────────────────

def test_B_five_contracts_succeed_and_the_sixth_is_refused(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app import pilot

    tenant = tenants['a']
    for index in range(5):
        pilot.create_asset(
            {'name': f'ABC Token {index}', 'asset_type': 'contract',
             'chain_network': 'base-mainnet', 'identifier': f'0x{index:040x}'},
            _request(tenant['token'], tenant['workspace_id']),
        )

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_asset(
            {'name': 'ABC Token 6', 'asset_type': 'contract',
             'chain_network': 'base-mainnet', 'identifier': '0x' + 'f' * 40},
            _request(tenant['token'], tenant['workspace_id']),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'PLAN_LIMIT_REACHED'
    assert exc_info.value.detail['resource'] == 'monitored_contracts'
    assert exc_info.value.detail['limit'] == 5

    stored = live.execute(
        'SELECT COUNT(*) AS n FROM assets WHERE workspace_id = %s AND deleted_at IS NULL',
        (tenant['workspace_id'],),
    ).fetchone()['n']
    assert int(stored) == 5, 'the refused create must not have written a row'


def test_B2_a_second_workspace_is_refused_on_the_pilot_limit(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app import pilot

    tenant = tenants['a']
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_workspace_for_user(
            {'name': 'Second ABC workspace'}, _request(tenant['token'], tenant['workspace_id']),
        )
    assert exc_info.value.detail['code'] == 'PLAN_LIMIT_REACHED'
    assert exc_info.value.detail['resource'] == 'workspaces'


# ── TEST C — Tenant isolation ────────────────────────────────────────────────

def test_C_each_tenant_reads_only_its_own_plan_and_usage(live, tenants) -> None:
    from services.api.app.domains.tenancy import endpoints as tenancy

    tenant_b = tenants['b']
    assert tenant_b['organization_id'] != tenants['a']['organization_id']
    plan = tenancy.get_account_plan(_request(tenant_b['token'], tenant_b['workspace_id']))
    assert plan['organization']['id'] == tenant_b['organization_id']
    assert plan['usage']['monitored_contracts']['current'] == 0


def test_C2_a_workspace_header_naming_another_tenant_is_refused(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app.domains.tenancy import endpoints as tenancy

    with pytest.raises(HTTPException) as exc_info:
        tenancy.get_account_plan(_request(tenants['b']['token'], tenants['a']['workspace_id']))
    assert exc_info.value.status_code == 403


def test_C3_cross_tenant_object_read_returns_a_safe_404(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app import pilot

    asset_id = str(live.execute(
        'SELECT id FROM assets WHERE workspace_id = %s LIMIT 1', (tenants['a']['workspace_id'],),
    ).fetchone()['id'])
    with pytest.raises(HTTPException) as exc_info:
        pilot.get_asset(asset_id, _request(tenants['b']['token'], tenants['b']['workspace_id']))
    # 404, not 403: the response must not confirm that the object exists.
    assert exc_info.value.status_code == 404


# ── TEST D — Evidence ────────────────────────────────────────────────────────

def test_D_the_eleventh_evidence_package_is_refused(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app import entitlements as ent
    from services.api.app import organizations as org_service

    tenant = tenants['a']
    for _ in range(10):
        live.execute(
            'INSERT INTO export_jobs (id, workspace_id, requested_by_user_id, export_type, format, status)'
            " VALUES (%s, %s, %s, 'proof_bundle', 'json', 'completed')",
            (str(uuid.uuid4()), tenant['workspace_id'], tenant['user_id']),
        )
    context = org_service.resolve_context(live, tenant['workspace_id'])
    with pytest.raises(HTTPException) as exc_info:
        org_service.enforce_creation(live, context, ent.LIMIT_EVIDENCE_PACKAGES)
    assert exc_info.value.detail['resource'] == 'evidence_packages'
    assert exc_info.value.detail['limit'] == 10


# ── TEST E — Response actions run in recommend-only mode ─────────────────────

def test_E_a_live_run_is_locked_while_simulation_stays_available(live, tenants) -> None:
    from services.api.app import pilot

    workspace_id = tenants['a']['workspace_id']
    lock = pilot.plan_execution_lock(live, workspace_id)
    assert lock == {'locked': True, 'reason': 'plan_recommend_only', 'plan': 'pilot'}

    authorized = {
        'decision': 'AUTHORIZED', 'decision_label': 'Execution Authorized', 'can_execute': True,
        'authorization_decision': 'AUTHORIZED', 'policy_decision': 'ALLOW',
        'reason_codes': ['EXECUTION_AUTHORIZED'], 'reasons': [],
    }
    locked = pilot._apply_plan_execution_lock(
        live, dict(authorized), action={'id': 'a1', 'mode': 'live'}, workspace_id=workspace_id,
    )
    assert locked['can_execute'] is False
    assert 'PLAN_EXECUTION_NOT_ENTITLED' in locked['reason_codes']
    # The policy verdict is reported unchanged: the plan is what blocks the run,
    # and an operator must not be told a policy denied something it did not.
    assert locked['authorization_decision'] == 'AUTHORIZED'
    assert locked['policy_decision'] == 'ALLOW'

    simulated = pilot._apply_plan_execution_lock(
        live, dict(authorized), action={'id': 'a1', 'mode': 'simulated'}, workspace_id=workspace_id,
    )
    assert simulated['can_execute'] is True
    assert simulated['plan_execution_locked'] is False


# ── TEST F — Expiration ──────────────────────────────────────────────────────

def test_F_expiry_blocks_new_work_and_destroys_nothing(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app import pilot
    from services.api.app.domains.tenancy import endpoints as tenancy

    tenant = tenants['a']
    live.execute(
        "UPDATE organizations SET evaluation_expires_at = NOW() - INTERVAL '1 day' WHERE id = %s",
        (tenant['organization_id'],),
    )
    plan = tenancy.get_account_plan(_request(tenant['token'], tenant['workspace_id']))
    assert plan['lifecycle_state'] == 'EXPIRED_PILOT'
    assert plan['evaluation']['expired'] is True
    # Sign-in still works and the history is intact.
    assert plan['usage']['monitored_contracts']['current'] == 5
    assert plan['usage']['evidence_packages']['current'] == 10

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_asset(
            {'name': 'After expiry', 'asset_type': 'contract',
             'chain_network': 'base-mainnet', 'identifier': '0x' + 'e' * 40},
            _request(tenant['token'], tenant['workspace_id']),
        )
    assert exc_info.value.detail['code'] == 'PLAN_EVALUATION_EXPIRED'

    remaining = live.execute(
        'SELECT COUNT(*) AS n FROM assets WHERE workspace_id = %s', (tenant['workspace_id'],),
    ).fetchone()['n']
    assert int(remaining) == 5


# ── TEST G — Upgrade Pilot → Scale ───────────────────────────────────────────

def test_G_upgrade_preserves_everything_and_applies_scale_limits(live, tenants) -> None:
    from services.api.app import organizations as org_service
    from services.api.app import pilot
    from services.api.app.domains.tenancy import endpoints as tenancy

    tenant = tenants['a']
    org_service.set_plan(live, organization_id=tenant['organization_id'], plan='scale')

    plan = tenancy.get_account_plan(_request(tenant['token'], tenant['workspace_id']))
    assert plan['organization']['id'] == tenant['organization_id']   # same tenant
    assert plan['plan'] == 'scale'
    assert plan['evaluation'] is None                                 # no countdown on Scale
    assert plan['usage']['monitored_contracts'] == {'current': 5, 'limit': 25}
    assert plan['usage']['evidence_packages'] == {'current': 10, 'limit': None}

    created = pilot.create_asset(
        {'name': 'Sixth after upgrade', 'asset_type': 'contract',
         'chain_network': 'base-mainnet', 'identifier': '0x' + 'd' * 40},
        _request(tenant['token'], tenant['workspace_id']),
    )
    assert created.get('id')

    user = pilot.create_workspace_for_user(
        {'name': 'Second ABC workspace'}, _request(tenant['token'], tenant['workspace_id']),
    )
    assert len(user['memberships']) == 2
    # The new workspace joins the SAME organization — adding a workspace is an
    # operation inside one tenant, never a way to mint a second one.
    organizations = live.execute(
        'SELECT DISTINCT organization_id AS o FROM workspaces WHERE created_by_user_id = %s',
        (tenant['user_id'],),
    ).fetchall()
    assert [str(row['o']) for row in organizations] == [tenant['organization_id']]


# ── TEST H — Founder admin ───────────────────────────────────────────────────

def test_H_a_customer_is_denied_and_sees_no_organization_data(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app.domains.tenancy import endpoints as tenancy

    tenant = tenants['b']
    with pytest.raises(HTTPException) as exc_info:
        tenancy.list_admin_customers(_request(tenant['token'], tenant['workspace_id']))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'INTERNAL_ADMIN_REQUIRED'
    assert tenants['a']['organization_id'] not in str(exc_info.value.detail)


def test_H2_an_internal_admin_sees_every_tenant_with_real_usage(live, tenants) -> None:
    from services.api.app.domains.tenancy import endpoints as tenancy

    admin = tenants['b']
    live.execute('UPDATE users SET is_internal_admin = TRUE WHERE id = %s', (admin['user_id'],))
    listing = tenancy.list_admin_customers(_request(admin['token'], admin['workspace_id']))
    # Two signups, and the extra workspace from TEST G joined an existing tenant.
    assert listing['count'] == 2
    row = next(item for item in listing['customers'] if item['id'] == tenants['a']['organization_id'])
    assert row['plan'] == 'scale'
    assert row['usage']['workspaces']['current'] == 2
    assert row['usage']['monitored_contracts']['current'] == 6
    assert row['last_activity_at'] is not None


def test_H3_suspend_reactivate_and_plan_change_work_in_place(live, tenants) -> None:
    from services.api.app import organizations as org_service
    from services.api.app.domains.tenancy import endpoints as tenancy

    admin = tenants['b']
    request = _request(admin['token'], admin['workspace_id'])
    target = admin['organization_id']

    tenancy.set_admin_customer_status(target, {'status': 'suspended'}, request)
    assert org_service.get_organization(live, target)['status'] == 'suspended'

    tenancy.set_admin_customer_status(target, {'status': 'active'}, request)
    assert org_service.get_organization(live, target)['status'] == 'active'

    detail = tenancy.set_admin_customer_plan(target, {'plan': 'enterprise'}, request)
    assert detail['organization']['plan'] == 'enterprise'
    assert detail['evaluation'] is None


def test_H4_extending_an_active_evaluation_adds_to_its_deadline(live, tenants) -> None:
    """Extending a LIVE evaluation must not shorten it back to today + N."""
    from services.api.app import organizations as org_service
    from services.api.app.domains.tenancy import endpoints as tenancy

    admin = tenants['b']
    request = _request(admin['token'], admin['workspace_id'])
    target = admin['organization_id']

    tenancy.set_admin_customer_plan(target, {'plan': 'pilot'}, request)
    before = org_service.get_organization(live, target)['evaluation_expires_at']
    detail = tenancy.extend_admin_customer_evaluation(target, {'days': 14}, request)
    after = org_service.get_organization(live, target)['evaluation_expires_at']
    assert (after - before).days == 14
    assert detail['evaluation']['days_remaining'] > 14


def test_H5_extending_an_expired_evaluation_grants_the_full_window(live, tenants) -> None:
    from services.api.app import organizations as org_service
    from services.api.app.domains.tenancy import endpoints as tenancy

    admin = tenants['b']
    request = _request(admin['token'], admin['workspace_id'])
    target = tenants['a']['organization_id']

    tenancy.set_admin_customer_plan(target, {'plan': 'pilot'}, request)
    live.execute(
        "UPDATE organizations SET evaluation_expires_at = NOW() - INTERVAL '10 days', status = 'expired'"
        ' WHERE id = %s',
        (target,),
    )
    detail = tenancy.extend_admin_customer_evaluation(target, {'days': 30}, request)
    assert detail['evaluation']['days_remaining'] in (29, 30)
    assert detail['evaluation']['expired'] is False
    assert org_service.get_organization(live, target)['status'] == 'active'


def test_H6_every_privileged_lifecycle_action_is_audited(live, tenants) -> None:
    actions = {
        row['action']
        for row in live.execute(
            "SELECT DISTINCT action FROM audit_logs WHERE entity_type = 'organization'",
        ).fetchall()
    }
    assert {
        'organization.pilot_created',
        'organization.status_changed',
        'organization.plan_changed',
        'organization.evaluation_extended',
    } <= actions


def test_H7_no_audit_row_carries_a_credential(live) -> None:
    """Lifecycle audit metadata must never carry a secret.

    Checked as credential-shaped KEYS and VALUES rather than as English words:
    a tenant legitimately named "ABC Tokenization" contains "token", and a test
    that fired on that would be noise rather than a control.
    """
    import json
    import re

    forbidden_keys = {
        'password', 'password_hash', 'private_key', 'secret', 'api_key',
        'session_token', 'access_token', 'token', 'seed_phrase', 'mnemonic',
        'authorization', 'csrf_token',
    }
    credential_shapes = (
        re.compile(r'0x[a-fA-F0-9]{64}'),
        re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
        re.compile(re.escape(_PASSWORD)),
    )

    rows = live.execute(
        "SELECT metadata FROM audit_logs"
        " WHERE entity_type IN ('organization', 'organization_feedback')",
    ).fetchall()
    assert rows, 'the lifecycle actions above should have produced audit rows'
    for row in rows:
        metadata = row['metadata'] or {}
        assert not (set(metadata) & forbidden_keys), sorted(set(metadata) & forbidden_keys)
        serialized = json.dumps(metadata, default=str)
        for pattern in credential_shapes:
            assert pattern.search(serialized) is None, pattern.pattern


def test_H8_feedback_audit_records_the_kind_not_the_message(live, tenants) -> None:
    """The message body stays in the one internal-only table.

    Copying it into the audit log would duplicate security feedback into a
    second surface that more roles can read.
    """
    rows = live.execute(
        "SELECT metadata FROM audit_logs WHERE action = 'organization.feedback_submitted'",
    ).fetchall()
    for row in rows:
        metadata = row['metadata'] or {}
        assert 'feedback_type' in metadata
        assert 'message' not in metadata


# ── Feedback ─────────────────────────────────────────────────────────────────

def test_feedback_is_stamped_to_the_submitting_tenant_and_refuses_secrets(live, tenants) -> None:
    from fastapi import HTTPException
    from services.api.app.domains.tenancy import endpoints as tenancy

    submitter, admin = tenants['a'], tenants['b']
    tenancy.submit_account_feedback(
        {
            'feedback_type': 'detection_accuracy',
            'message': 'A Base transfer did not raise an alert on our treasury contract.',
            'context': {'page': '/alerts'},
            # A body naming another tenant must change nothing.
            'organization_id': admin['organization_id'],
        },
        _request(submitter['token'], submitter['workspace_id']),
    )
    listing = tenancy.list_admin_feedback(_request(admin['token'], admin['workspace_id']))
    assert listing['count'] == 1
    assert listing['feedback'][0]['organization_id'] == submitter['organization_id']
    assert listing['feedback'][0]['context'] == {'page': '/alerts'}

    with pytest.raises(HTTPException) as exc_info:
        tenancy.submit_account_feedback(
            {'feedback_type': 'security', 'message': 'my key is 0x' + 'a' * 64},
            _request(submitter['token'], submitter['workspace_id']),
        )
    assert exc_info.value.detail['code'] == 'FEEDBACK_CONTAINS_SECRET'
    stored = live.execute('SELECT COUNT(*) AS n FROM organization_feedback').fetchone()['n']
    assert int(stored) == 1, 'a message carrying a credential must never reach the database'
