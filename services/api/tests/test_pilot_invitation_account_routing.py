"""Invitation acceptance routes on whether the approved address has an account.

The bug this file pins
----------------------
An approved Pilot invitation sent every recipient to ``/sign-in``. For someone
who had never had a Decoda account — which is most approved applicants — that is
a screen with nothing to offer: they have no password, so they arrived at
"Invalid email or password" with no way forward. The invitation email worked, the
token was valid, the approval was real, and onboarding still dead-ended.

The fix is one FACT and one new route, both server-side:

  ``account_exists``                 answered from the database, for the address
                                     the invitation itself names, and only for an
                                     invitation that still resolves
  ``POST /pilot-invitations/signup`` creates the account that invitation is for,
                                     and signs it in

What must stay true while that is added is everything the approval-only change
established: the body chooses nothing, the token is still single-use, and
creating an account is still not the same event as activating a Pilot.

  A  the fact       account_exists, and who is allowed to learn it
  B  account        invitation-aware signup: what it creates and what it refuses
  C  the body       nothing in it selects an address, plan, tenant, or role
  D  invitation     expired, used, rejected, and unknown tokens all refuse
  E  activation     still the separate, server-decided step it always was
  F  idempotency    a second submit does not make a second account

Run:
    python -m pytest services/api/tests/test_pilot_invitation_account_routing.py -q
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app import pilot_access
from services.api.app.domains.tenancy import endpoints as tenancy_endpoints

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

APPLICANT_EMAIL = 'security@company.com'
ATTACKER_EMAIL = 'attacker@gmail.com'
FOUNDER_EMAIL = 'decoda.guard@gmail.com'

FOUNDER_USER = 'user-founder'
EXISTING_USER = 'user-existing'

REQUEST_ID = 'bbbbbbbb-2222-2222-2222-222222222222'

#: What an approved applicant types. Note what is NOT here: no email. The address
#: comes from the invitation row, so the form has no field that could name one.
VALID_SIGNUP = {'full_name': 'Sam Rivera', 'password': 'Str0ngPilotPass!'}

#: Accounts this change must not disturb (Phase 15).
PRE_EXISTING_USERS: dict[str, dict[str, Any]] = {
    FOUNDER_USER: {'id': FOUNDER_USER, 'email': FOUNDER_EMAIL, 'is_internal_admin': True},
    'user-rabbit': {'id': 'user-rabbit', 'email': 'rabbit@decoda.app', 'is_internal_admin': False},
    'user-datto': {'id': 'user-datto', 'email': 'datpt3@funix.edu.vn', 'is_internal_admin': False},
}


# ── fake connection ──────────────────────────────────────────────────────────
class _Result:
    def __init__(self, rows: Any = None) -> None:
        if rows is None:
            self._rows: list[Any] = []
        elif isinstance(rows, list):
            self._rows = rows
        else:
            self._rows = [rows]

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """``pilot_requests`` + ``users`` + the writes activation makes.

    Every statement is recorded so a test can prove that a REFUSED operation
    wrote nothing — the only way to show a refusal left no half-made account.
    """

    def __init__(
        self,
        *,
        requests: dict[str, dict[str, Any]] | None = None,
        users: dict[str, dict[str, Any]] | None = None,
        organizations: dict[str, dict[str, Any]] | None = None,
        memberships: dict[str, str] | None = None,
        user_probe_raises: bool = False,
    ) -> None:
        self.requests = requests or {}
        self.users = dict(users or PRE_EXISTING_USERS)
        self.organizations = dict(organizations or {})
        self.memberships = memberships or {}
        self.sessions: list[tuple[str, str]] = []
        #: Simulates a database that cannot answer the existence probe.
        self.user_probe_raises = user_probe_raises
        self.writes: list[tuple[str, Any]] = []
        self.committed = False
        self.rolled_back = False

    # -- writes ---------------------------------------------------------------
    def _apply_write(self, lowered: str, params: Any) -> None:
        if lowered.startswith('insert into pilot_requests'):
            (row_id, email, company, role, website, use_case, status_value, source_ip) = params
            self.requests[str(row_id)] = {
                'id': row_id, 'email': email, 'company_name': company, 'role': role,
                'company_website': website, 'use_case': use_case, 'status': status_value,
                'requested_at': NOW, 'reviewed_at': None, 'reviewed_by_user_id': None,
                'approved_at': None, 'rejected_at': None, 'internal_note': None,
                'invitation_token_hash': None, 'invitation_expires_at': None,
                'invitation_sent_at': None, 'invitation_accepted_at': None,
                'invitation_delivery_error': None, 'organization_id': None,
                'source_ip': source_ip, 'created_at': NOW, 'updated_at': NOW,
            }
            return
        if lowered.startswith('update pilot_requests'):
            self._apply_request_update(lowered, params)
            return
        if lowered.startswith('insert into users'):
            # Invitation-aware signup passes four values; the generic signup path
            # also passes current_workspace_id. Both land in the same table.
            (user_id, email, password_hash, full_name) = tuple(params)[:4]
            self.users[str(user_id)] = {
                'id': user_id, 'email': email, 'password_hash': password_hash,
                'full_name': full_name, 'is_internal_admin': False,
                'email_verified_at': NOW, 'current_workspace_id': None,
                'session_version': 1,
            }
            return
        if lowered.startswith('insert into auth_sessions'):
            self.sessions.append((str(params[1]), str(params[3])))
            return
        if lowered.startswith('insert into organizations'):
            (org_id, name, slug, plan, status_value, started_at, expires_at) = params
            self.organizations[str(org_id)] = {
                'id': org_id, 'name': name, 'slug': slug, 'plan': plan, 'status': status_value,
                'evaluation_started_at': started_at, 'evaluation_expires_at': expires_at,
                'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
            }
            return
        if lowered.startswith('insert into organization_memberships'):
            (_row_id, org_id, user_id, _role) = params
            self.memberships[str(user_id)] = str(org_id)
            return

    def _apply_request_update(self, lowered: str, params: Any) -> None:
        if 'invitation_token_hash = %s' in lowered and 'set status = %s' in lowered:
            (status_value, reviewed_at, reviewer, approved_at, hashed, expires_at, row_id) = params
            row = self.requests.get(str(row_id))
            if row is None:
                return
            row.update({
                'status': status_value, 'reviewed_at': reviewed_at, 'reviewed_by_user_id': reviewer,
                'approved_at': row.get('approved_at') or approved_at, 'rejected_at': None,
                'invitation_token_hash': hashed, 'invitation_expires_at': expires_at,
                'invitation_sent_at': None, 'invitation_delivery_error': None,
            })
            return
        if 'rejected_at = %s' in lowered:
            (status_value, reviewed_at, reviewer, rejected_at, note, row_id) = params
            row = self.requests.get(str(row_id))
            if row is None or row.get('status') == pilot_access.STATUS_ACTIVATED:
                return
            row.update({
                'status': status_value, 'reviewed_at': reviewed_at, 'reviewed_by_user_id': reviewer,
                'rejected_at': rejected_at, 'approved_at': None,
                'internal_note': note if note is not None else row.get('internal_note'),
                'invitation_token_hash': None, 'invitation_expires_at': None,
                'invitation_sent_at': None, 'invitation_delivery_error': None,
            })
            return
        if 'invitation_sent_at = now()' in lowered:
            (status_value, row_id, required_status) = params
            row = self.requests.get(str(row_id))
            if row is not None and row.get('status') == required_status:
                row.update({'status': status_value, 'invitation_sent_at': NOW})
            return
        if 'invitation_accepted_at = %s' in lowered:
            (status_value, org_id, accepted_at, row_id, allowed) = params
            row = self.requests.get(str(row_id))
            if row is not None and row.get('status') in list(allowed):
                row.update({
                    'status': status_value, 'organization_id': org_id,
                    'invitation_accepted_at': accepted_at, 'invitation_token_hash': None,
                })
            return
        if 'set status = %s, invitation_token_hash = null' in lowered:
            (status_value, row_id, allowed) = params
            row = self.requests.get(str(row_id))
            if row is not None and row.get('status') in list(allowed):
                row.update({'status': status_value, 'invitation_token_hash': None})
            return

    # -- dispatch -------------------------------------------------------------
    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()

        if lowered.startswith(('insert', 'update', 'delete')):
            self.writes.append((sql, params))
            self._apply_write(lowered, params)
            return _Result()

        if 'information_schema.tables' in lowered and 'pilot_requests' in str(params):
            return _Result({'table_count': 1})
        if 'information_schema.tables' in lowered:
            return _Result({'table_count': 2, 'link_count': 1})

        if 'select id from users where email' in lowered:
            if self.user_probe_raises:
                raise RuntimeError('users table unreadable')
            match = [dict(u) for u in self.users.values() if u.get('email') == str(params[0])]
            return _Result({'id': match[0]['id']} if match else None)
        if 'select email, is_internal_admin from users' in lowered:
            return _Result(self.users.get(str(params[0])))
        # build_user_response hydrates the account the endpoint returns. Answered
        # here rather than stubbed so the real response builder runs.
        if 'from users where id = %s' in lowered:
            row = self.users.get(str(params[0]))
            if row is None:
                return _Result(None)
            return _Result({
                'id': row['id'], 'email': row['email'], 'full_name': row.get('full_name'),
                'current_workspace_id': row.get('current_workspace_id'),
                'created_at': NOW, 'updated_at': NOW, 'last_sign_in_at': NOW,
                'email_verified_at': row.get('email_verified_at'),
                'mfa_enabled_at': None,
            })
        if 'from workspace_members wm join workspaces w' in lowered:
            return _Result([])
        if 'from workspace_members' in lowered:
            return _Result(None)

        if 'from pilot_requests where invitation_token_hash' in lowered:
            match = [
                dict(row) for row in self.requests.values()
                if row.get('invitation_token_hash') == str(params[0])
            ]
            return _Result(match[0] if match else None)
        if 'from pilot_requests where id = %s' in lowered:
            row = self.requests.get(str(params[0]))
            return _Result(dict(row) if row else None)
        if 'from pilot_requests where email = %s and status = any' in lowered:
            email, statuses = params
            match = [
                dict(row) for row in self.requests.values()
                if row.get('email') == email and row.get('status') in list(statuses)
            ]
            return _Result(match[0] if match else None)

        if 'count(*) as count from organization_memberships where user_id' in lowered:
            return _Result({'count': 1 if str(params[0]) in self.memberships else 0})
        if 'count(*) as count from workspaces where organization_id' in lowered:
            return _Result({'count': 0})
        if 'from organizations where id = %s' in lowered:
            row = self.organizations.get(str(params[0]))
            return _Result(dict(row) if row else None)
        if 'select 1 from organizations where slug' in lowered:
            taken = any(str(o.get('slug')) == str(params[0]) for o in self.organizations.values())
            return _Result({'1': 1} if taken else None)
        return _Result(None)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


# ── wiring ───────────────────────────────────────────────────────────────────
def _request() -> SimpleNamespace:
    return SimpleNamespace(
        headers={}, client=SimpleNamespace(host='198.51.100.7'), scope={'path': '/pilot-invitations'},
    )


@pytest.fixture(autouse=True)
def _base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    # A real digest, so "the raw token is never stored" assertions cannot pass
    # vacuously against a prefixed echo.
    monkeypatch.setattr(
        pilot, '_auth_token_hash',
        lambda value: hashlib.sha256(f'test-secret::{value}'.encode('utf-8')).hexdigest(),
    )
    monkeypatch.setattr(pilot, 'create_access_token', lambda user_id, _v=1: f'session-for-{user_id}')


def _use_connection(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _pg)


def _pending_request(**overrides: Any) -> dict[str, Any]:
    row = {
        'id': REQUEST_ID, 'email': APPLICANT_EMAIL, 'company_name': 'Company Treasury',
        'role': 'Head of Security', 'company_website': 'company.com',
        'use_case': 'tokenized treasury monitoring', 'status': pilot_access.STATUS_PENDING,
        'requested_at': NOW, 'reviewed_at': None, 'reviewed_by_user_id': None,
        'approved_at': None, 'rejected_at': None, 'internal_note': None,
        'invitation_token_hash': None, 'invitation_expires_at': None,
        'invitation_sent_at': None, 'invitation_accepted_at': None,
        'invitation_delivery_error': None, 'organization_id': None,
        'source_ip': None, 'created_at': NOW, 'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _connection(**kwargs: Any) -> FakeConnection:
    return FakeConnection(requests={REQUEST_ID: _pending_request()}, **kwargs)


def _approve(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> str:
    """Approve REQUEST_ID as the founder and return the raw invitation token."""
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(pilot, '_dispatch_transactional_email', lambda _c, **kw: sent.append(kw))
    _use_connection(monkeypatch, connection)
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection',
        lambda *_a, **_k: {'id': FOUNDER_USER, 'email': FOUNDER_EMAIL, 'email_verified': True},
    )
    tenancy_endpoints.approve_admin_pilot_request(REQUEST_ID, {}, _request())
    return str(sent[0]['token'])


def _existing_account_connection(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeConnection, str]:
    """An approved invitation whose address ALREADY has a Decoda account."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    connection.users[EXISTING_USER] = {
        'id': EXISTING_USER, 'email': APPLICANT_EMAIL, 'is_internal_admin': False,
        'password_hash': 'existing-hash', 'email_verified_at': NOW,
    }
    return connection, token


def _lookup(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection, token: str) -> dict[str, Any]:
    _use_connection(monkeypatch, connection)
    return tenancy_endpoints.lookup_pilot_invitation(token, _request())


def _new_user_id(connection: FakeConnection) -> str:
    created = [uid for uid in connection.users if uid not in PRE_EXISTING_USERS and uid != EXISTING_USER]
    assert len(created) == 1, f'expected exactly one new account, got {created}'
    return created[0]


# ═════════════════════════════════════════════════════════════════════════════
# A — the fact the routing turns on
# ═════════════════════════════════════════════════════════════════════════════

def test_01_a_valid_invitation_with_no_account_reports_account_exists_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the old flow could not see, and therefore mis-routed."""
    connection = _connection()
    token = _approve(monkeypatch, connection)

    result = _lookup(monkeypatch, connection, token)

    assert result['valid'] is True
    assert result['invitation']['account_exists'] is False
    assert result['invitation']['email'] == APPLICANT_EMAIL


def test_02_a_valid_invitation_with_an_existing_account_reports_account_exists_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, token = _existing_account_connection(monkeypatch)

    result = _lookup(monkeypatch, connection, token)

    assert result['valid'] is True
    assert result['invitation']['account_exists'] is True


def test_03_a_the_probe_is_case_insensitive_like_the_rest_of_the_auth_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Security@Company.com`` and ``security@company.com`` are one address."""
    connection = _connection()
    connection.requests[REQUEST_ID]['email'] = APPLICANT_EMAIL
    token = _approve(monkeypatch, connection)
    connection.users[EXISTING_USER] = {
        'id': EXISTING_USER, 'email': APPLICANT_EMAIL, 'is_internal_admin': False,
    }
    _use_connection(monkeypatch, connection)

    assert pilot_access.account_exists_for_email(connection, 'Security@Company.COM ') is True


def test_04_a_an_unreadable_probe_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """"We could not look" routes to sign-in, which states its own outcome
    truthfully, rather than to a signup form that would 409 on submit."""
    connection = _connection(user_probe_raises=True)

    assert pilot_access.account_exists_for_email(connection, APPLICANT_EMAIL) is True


def test_05_a_an_empty_address_is_never_reported_as_free(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()

    assert pilot_access.account_exists_for_email(connection, '') is True
    assert pilot_access.account_exists_for_email(connection, None) is True


@pytest.mark.parametrize('overrides,expected_code', [
    ({'status': pilot_access.STATUS_REJECTED}, pilot_access.CODE_INVITATION_INVALID),
    ({'status': pilot_access.STATUS_ACTIVATED}, pilot_access.CODE_INVITATION_USED),
    ({'invitation_expires_at': NOW - timedelta(hours=1)}, pilot_access.CODE_INVITATION_EXPIRED),
])
def test_06_a_a_refused_invitation_reports_nothing_about_any_account(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], expected_code: str,
) -> None:
    """``account_exists`` rides on a VALID invitation only. A refusal carries no
    invitation object at all, so a leaked or dead token is not an oracle for
    whether an address holds a Decoda account."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    connection.requests[REQUEST_ID].update(overrides)
    connection.users[EXISTING_USER] = {
        'id': EXISTING_USER, 'email': APPLICANT_EMAIL, 'is_internal_admin': False,
    }

    result = _lookup(monkeypatch, connection, token)

    assert result['valid'] is False
    assert result['code'] == expected_code
    assert result['invitation'] is None


def test_07_a_an_unknown_token_describes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    _approve(monkeypatch, connection)

    result = _lookup(monkeypatch, connection, 'not-a-real-token')

    assert result['valid'] is False
    assert result['invitation'] is None


def test_08_a_the_lookup_never_returns_the_token_hash_or_an_internal_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)

    result = _lookup(monkeypatch, connection, token)

    assert set(result['invitation']) == {
        'email', 'company_name', 'expires_at', 'evaluation_days', 'status', 'account_exists',
    }
    serialized = repr(result)
    assert 'invitation_token_hash' not in serialized
    assert token not in serialized
    assert REQUEST_ID not in serialized


# ═════════════════════════════════════════════════════════════════════════════
# B — invitation-aware account creation
# ═════════════════════════════════════════════════════════════════════════════

def test_09_b_a_valid_invitation_creates_the_account_for_the_approved_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    user_id = _new_user_id(connection)
    assert connection.users[user_id]['email'] == APPLICANT_EMAIL
    assert connection.users[user_id]['full_name'] == 'Sam Rivera'
    assert result['user']['id'] == user_id
    assert connection.committed is True


def test_10_b_the_password_is_stored_only_as_a_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    stored = connection.users[_new_user_id(connection)]['password_hash']
    assert stored
    assert VALID_SIGNUP['password'] not in stored
    # The same verifier the rest of the auth model uses — not a second scheme.
    assert pilot.verify_password(VALID_SIGNUP['password'], stored) is True
    assert pilot.verify_password('the-wrong-password', stored) is False
    # And nowhere in any statement this endpoint issued.
    assert not any(VALID_SIGNUP['password'] in str(params) for _sql, params in connection.writes)


def test_11_b_the_existing_password_policy_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.signup_invited_user({'token': token, 'full_name': 'Sam', 'password': 'short'}, _request())

    assert refusal.value.status_code == 400
    assert not any(sql.lower().startswith('insert into users') for sql, _ in connection.writes)


def test_12_b_the_account_is_signed_in_with_a_real_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """The response is shaped like sign-in's, so the existing auth proxy turns it
    into the session cookie with no second cookie-writing convention."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    user_id = _new_user_id(connection)
    assert result['access_token'] == f'session-for-{user_id}'
    assert result['token_type'] == 'bearer'
    # Stored hashed, like every other session row.
    assert connection.sessions == [(user_id, pilot._auth_token_hash(result['access_token']))]


def test_13_b_the_invitation_settles_email_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invitation token IS an email-control challenge: single-use, expiring,
    delivered by Decoda to this address and nowhere else. Requiring a second
    challenge that proves the same thing would strand the applicant between two
    of them — the reasoning already applied to password reset."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    insert = next(sql for sql, _ in connection.writes if sql.lower().startswith('insert into users'))
    assert 'email_verified_at' in insert.lower()
    assert connection.users[_new_user_id(connection)]['email_verified_at'] is not None


def test_14_b_creating_the_account_does_not_create_a_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """An account is not an evaluation. No workspace, no organization, no plan,
    no monitoring — activation is still a separate, audited step."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    written = [sql.lower() for sql, _ in connection.writes]
    for table in ('workspaces', 'workspace_members', 'organizations', 'organization_memberships'):
        assert not any(sql.startswith(f'insert into {table}') for sql in written), table
    assert connection.organizations == {}
    assert result['invitation_accepted'] is False


def test_15_b_creating_the_account_does_not_spend_the_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance consumes the token, exactly once. Signup must leave it usable
    or the account it just made could never activate anything."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    row = connection.requests[REQUEST_ID]
    assert row['status'] in pilot_access.INVITABLE_STATUSES
    assert row['invitation_token_hash'] is not None
    assert pilot_access.find_by_token(connection, token) is not None


# ═════════════════════════════════════════════════════════════════════════════
# C — what the request body is allowed to say
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('claim', [
    {'email': ATTACKER_EMAIL},
    {'plan': 'scale'},
    {'plan': 'enterprise'},
    {'is_internal_admin': True},
    {'organization_id': 'org-scale'},
    {'role': 'owner'},
    {'workspace_name': 'Attacker Inc'},
    {'evaluation_days': 3650},
    {'email_verified': True, 'entitlement_overrides': {'max_monitored_contracts': 9999}},
])
def test_16_c_the_body_cannot_choose_the_address_the_plan_or_the_tenant(
    monkeypatch: pytest.MonkeyPatch, claim: dict[str, Any],
) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP, **claim}, _request())

    created = connection.users[_new_user_id(connection)]
    # The approved address, from the invitation row — never the body's.
    assert created['email'] == APPLICANT_EMAIL
    assert created['is_internal_admin'] is False
    assert connection.organizations == {}
    assert connection.memberships == {}


def test_17_c_a_body_email_is_not_even_present_in_the_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.signup_invited_user(
        {'token': token, 'email': ATTACKER_EMAIL, **VALID_SIGNUP}, _request(),
    )

    insert_params = next(
        params for sql, params in connection.writes if sql.lower().startswith('insert into users')
    )
    assert ATTACKER_EMAIL not in [str(value) for value in insert_params]
    assert APPLICANT_EMAIL in [str(value) for value in insert_params]


def test_18_c_a_missing_name_falls_back_without_failing_the_applicant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.signup_invited_user({'token': token, 'password': VALID_SIGNUP['password']}, _request())

    assert connection.users[_new_user_id(connection)]['full_name'] == 'security'


# ═════════════════════════════════════════════════════════════════════════════
# D — an invitation that may not be used
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('overrides,expected_code', [
    ({'invitation_expires_at': NOW - timedelta(hours=1)}, pilot_access.CODE_INVITATION_EXPIRED),
    ({'status': pilot_access.STATUS_ACTIVATED}, pilot_access.CODE_INVITATION_USED),
    ({'status': pilot_access.STATUS_REJECTED}, pilot_access.CODE_INVITATION_INVALID),
    ({'status': pilot_access.STATUS_EXPIRED}, pilot_access.CODE_INVITATION_EXPIRED),
])
def test_19_d_signup_refuses_an_invitation_that_is_not_usable(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], expected_code: str,
) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    connection.requests[REQUEST_ID].update(overrides)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    assert refusal.value.detail['code'] == expected_code
    assert not any(sql.lower().startswith('insert into users') for sql, _ in connection.writes)


@pytest.mark.parametrize('token', ['', '   ', 'not-a-real-token'])
def test_20_d_an_unknown_or_missing_token_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, token: str,
) -> None:
    connection = _connection()
    _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    assert refusal.value.detail['code'] == pilot_access.CODE_INVITATION_INVALID
    assert not any(sql.lower().startswith('insert into users') for sql, _ in connection.writes)


def test_21_d_a_revoked_invitation_dies_with_the_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting clears the token hash, so the link stops resolving entirely."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection',
        lambda *_a, **_k: {'id': FOUNDER_USER, 'email': FOUNDER_EMAIL, 'email_verified': True},
    )
    tenancy_endpoints.reject_admin_pilot_request(REQUEST_ID, {}, _request())

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    assert refusal.value.detail['code'] == pilot_access.CODE_INVITATION_INVALID
    assert not any(sql.lower().startswith('insert into users') for sql, _ in connection.writes)


# ═════════════════════════════════════════════════════════════════════════════
# E — activation is still the separate, server-decided step
# ═════════════════════════════════════════════════════════════════════════════

def _activate(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection, token: str, user_id: str):
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection',
        lambda *_a, **_k: {'id': user_id, 'email': APPLICANT_EMAIL, 'email_verified': True},
    )
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': user_id})
    return tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())


@pytest.fixture
def invited_and_activated(monkeypatch: pytest.MonkeyPatch):
    """The whole brand-new-applicant path: approve → sign up → accept."""
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())
    user_id = _new_user_id(connection)
    result = _activate(monkeypatch, connection, token, user_id)
    return SimpleNamespace(connection=connection, result=result, token=token, user_id=user_id)


def test_22_e_the_new_account_ends_on_an_active_pilot_organization(invited_and_activated) -> None:
    assert len(invited_and_activated.connection.organizations) == 1
    organization = next(iter(invited_and_activated.connection.organizations.values()))
    assert organization['plan'] == ent.PLAN_PILOT
    assert organization['status'] == ent.STATUS_ACTIVE
    assert invited_and_activated.result['organization']['plan'] == ent.PLAN_PILOT


def test_23_e_the_evaluation_window_is_the_configured_length(invited_and_activated) -> None:
    organization = next(iter(invited_and_activated.connection.organizations.values()))
    assert organization['evaluation_started_at'] is not None
    window = organization['evaluation_expires_at'] - organization['evaluation_started_at']
    assert window.days == ent.evaluation_days()


def test_24_e_the_account_owns_a_workspace_and_an_organization_membership(
    invited_and_activated,
) -> None:
    written = [sql.lower() for sql, _ in invited_and_activated.connection.writes]
    assert any(sql.startswith('insert into workspaces') for sql in written)
    assert any(sql.startswith('insert into workspace_members') for sql in written)
    assert invited_and_activated.connection.memberships[invited_and_activated.user_id]
    assert invited_and_activated.result['workspace']['id']


def test_25_e_the_request_is_bound_to_the_organization_it_made(invited_and_activated) -> None:
    row = invited_and_activated.connection.requests[REQUEST_ID]
    assert row['status'] == pilot_access.STATUS_ACTIVATED
    assert row['organization_id'] == invited_and_activated.result['organization']['id']
    # Single use: the token resolves to nothing from here on.
    assert row['invitation_token_hash'] is None


def test_26_e_a_second_acceptance_makes_no_second_workspace(invited_and_activated, monkeypatch) -> None:
    connection = invited_and_activated.connection
    before = len(connection.organizations)
    workspaces_before = sum(
        1 for sql, _ in connection.writes if sql.lower().startswith('insert into workspaces')
    )

    with pytest.raises(HTTPException):
        _activate(monkeypatch, connection, invited_and_activated.token, invited_and_activated.user_id)

    assert len(connection.organizations) == before
    workspaces_after = sum(
        1 for sql, _ in connection.writes if sql.lower().startswith('insert into workspaces')
    )
    assert workspaces_after == workspaces_before


def test_27_e_a_different_account_cannot_accept_the_invitation(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection',
        lambda *_a, **_k: {'id': 'user-attacker', 'email': ATTACKER_EMAIL, 'email_verified': True},
    )

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    assert refusal.value.status_code == 403
    assert connection.organizations == {}
    # The approved address is not echoed back to a non-matching caller.
    assert APPLICANT_EMAIL not in str(refusal.value.detail)


# ═════════════════════════════════════════════════════════════════════════════
# E2 — how the route is wired
# ═════════════════════════════════════════════════════════════════════════════

def test_21_b_the_route_is_registered_and_public() -> None:
    from services.api.app import main as api_main

    routes = {
        getattr(route, 'path', None): getattr(route, 'methods', set())
        for route in api_main.app.routes
    }
    assert 'POST' in routes['/pilot-invitations/signup']
    # The existing surfaces are untouched.
    assert 'GET' in routes['/pilot-invitations']
    assert 'POST' in routes['/pilot-invitations/accept']


def test_21_c_only_the_signup_path_is_csrf_exempt() -> None:
    """The exemption is the full path, not a prefix.

    ``/pilot-invitations/signup`` has no session and therefore no CSRF cookie to
    double-submit — the same posture as /auth/signup. Accepting an invitation is
    an AUTHENTICATED mutation and must keep full CSRF enforcement, so a prefix
    like ``/pilot-invitations`` would have quietly disarmed it.
    """
    from services.api.app import main as api_main

    def exempt(path: str) -> bool:
        return any(
            path == prefix or path.startswith(prefix + '/')
            for prefix in api_main._CSRF_EXEMPT_PREFIXES
        )

    assert exempt('/pilot-invitations/signup') is True
    assert exempt('/pilot-invitations/accept') is False
    assert exempt('/pilot-invitations') is False


# ═════════════════════════════════════════════════════════════════════════════
# F — idempotency, and the accounts that must not change
# ═════════════════════════════════════════════════════════════════════════════

def test_28_f_a_second_signup_does_not_create_a_second_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())
    first = _new_user_id(connection)

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    assert refusal.value.status_code == 409
    assert refusal.value.detail['account_exists'] is True
    assert _new_user_id(connection) == first


def test_29_f_signup_never_overwrites_an_existing_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invitation is not a password reset. Someone holding a leaked link must
    not be able to set a new password on the account it was approved for."""
    connection, token = _existing_account_connection(monkeypatch)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.signup_invited_user({'token': token, **VALID_SIGNUP}, _request())

    assert refusal.value.status_code == 409
    assert connection.users[EXISTING_USER]['password_hash'] == 'existing-hash'
    assert not any(sql.lower().startswith('update users') for sql, _ in connection.writes)
    assert not any(sql.lower().startswith('insert into users') for sql, _ in connection.writes)


def test_30_f_the_generic_signup_path_still_creates_no_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The approval-only loophole stays closed: /auth/signup without an
    invitation makes an account and nothing else."""
    source = pilot.signup_user.__doc__ or ''
    assert 'not a tenant' in source.lower()
    connection = _connection()
    _use_connection(monkeypatch, connection)
    monkeypatch.setattr(pilot, '_create_user_token', lambda *_a, **_k: 'verify-token')
    monkeypatch.setattr(pilot, '_dispatch_transactional_email', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': 'someone'})

    pilot.signup_user(
        {'email': 'nobody@example.com', 'password': VALID_SIGNUP['password'], 'full_name': 'Nobody'},
        _request(),
    )

    written = [sql.lower() for sql, _ in connection.writes]
    for table in ('workspaces', 'organizations', 'organization_memberships'):
        assert not any(sql.startswith(f'insert into {table}') for sql in written), table
    assert connection.organizations == {}


def test_31_f_pre_existing_accounts_are_untouched_by_the_whole_flow(
    invited_and_activated,
) -> None:
    connection = invited_and_activated.connection
    for user_id, original in PRE_EXISTING_USERS.items():
        assert connection.users[user_id] == original
    # And nothing wrote to a user row that was not the one just created.
    for sql, params in connection.writes:
        if sql.lower().startswith(('insert into users', 'update users')):
            assert invited_and_activated.user_id in [str(value) for value in params]
