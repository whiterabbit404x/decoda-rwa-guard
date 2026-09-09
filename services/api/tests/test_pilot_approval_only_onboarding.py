"""Approval-only Pilot access — request, review, invitation, activation.

The product rule under test: finding the Decoda URL does not give anyone a Pilot
account. Authentication proves someone controls an email address; only an
internal-admin approval, accepted by that same address, grants an evaluation.

  A  Public request  — one pending row, provisions nothing, refuses secrets,
                       handles duplicates, and is rate limited.
  B  Admin review    — internal staff only, at every one of the four verbs.
  C  Invitation      — random, hashed, single-use, time-limited, and dead after
                       rejection.
  D  Email matching  — the approved address, and no other, may accept.
  E  Activation      — creates exactly one Pilot organization + workspace, with
                       plan, status, and window decided server-side.
  F  Loopholes       — signup and workspace creation cannot mint a tenant.
  G  Existing data   — no pre-existing organization is touched by any of it.

Run:
    python -m pytest services/api/tests/test_pilot_approval_only_onboarding.py -q
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
PERSONAL_EMAIL = 'someone@gmail.com'
FOUNDER_EMAIL = 'decoda.guard@gmail.com'

APPLICANT_USER = 'user-applicant'
ATTACKER_USER = 'user-attacker'
FOUNDER_USER = 'user-founder'
CUSTOMER_USER = 'user-customer'

REQUEST_ID = 'aaaaaaaa-1111-1111-1111-111111111111'

VALID_SUBMISSION = {
    'email': APPLICANT_EMAIL,
    'company_name': 'Company Treasury',
    'role': 'Head of Security',
    'company_website': 'company.com',
    'use_case': 'tokenized treasury monitoring',
}

# Pre-existing tenants that this change must not disturb (Phase 12).
EXISTING_ORGS: dict[str, dict[str, Any]] = {
    'org-rabbit': {
        'id': 'org-rabbit', 'name': 'Rabbit', 'slug': 'rabbit',
        'plan': ent.PLAN_PILOT, 'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=5), 'evaluation_expires_at': None,
        'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
    },
    'org-datto': {
        'id': 'org-datto', 'name': 'Datto', 'slug': 'datto',
        'plan': ent.PLAN_PILOT, 'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=2),
        'evaluation_expires_at': NOW + timedelta(days=28),
        'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
    },
    'org-scale': {
        'id': 'org-scale', 'name': 'Scale Customer', 'slug': 'scale-customer',
        'plan': ent.PLAN_SCALE, 'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': None, 'evaluation_expires_at': None,
        'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
    },
    'org-enterprise': {
        'id': 'org-enterprise', 'name': 'Enterprise Customer', 'slug': 'enterprise-customer',
        'plan': ent.PLAN_ENTERPRISE, 'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': None, 'evaluation_expires_at': None,
        'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
    },
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
    """An in-memory ``pilot_requests`` table plus the reads the handlers make.

    Every write is recorded so a test can assert that a REFUSED operation wrote
    nothing at all — which is the only way to prove a refusal did not leave a
    half-provisioned tenant behind.
    """

    def __init__(
        self,
        *,
        requests: dict[str, dict[str, Any]] | None = None,
        users: dict[str, dict[str, Any]] | None = None,
        organizations: dict[str, dict[str, Any]] | None = None,
        memberships: dict[str, str] | None = None,
        workspace_member_user_ids: set[str] | None = None,
        workspace_counts: dict[str, int] | None = None,
        schema_ready: bool = True,
        pilot_requests_ready: bool = True,
    ) -> None:
        self.requests = requests or {}
        self.users = users or {}
        self.organizations = dict(organizations or {})
        self.memberships = memberships or {}
        #: workspace_members rows, by user. The PRE-tenancy proof of admission,
        #: kept apart from organization_memberships so a test can describe an
        #: account that holds one and not the other.
        self.workspace_member_user_ids = set(workspace_member_user_ids or set())
        #: Workspaces already owned by each organization, for the plan limit.
        self.workspace_counts = dict(workspace_counts or {})
        self.schema_ready = schema_ready
        self.pilot_requests_ready = pilot_requests_ready
        self.writes: list[tuple[str, Any]] = []
        self.committed = False
        self.rolled_back = False

    # -- writes ---------------------------------------------------------------
    def _apply_write(self, sql: str, lowered: str, params: Any) -> None:
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
        if lowered.startswith('insert into organizations'):
            (org_id, name, slug, plan, status_value, started_at, expires_at) = params
            self.organizations[str(org_id)] = {
                'id': org_id, 'name': name, 'slug': slug, 'plan': plan, 'status': status_value,
                'evaluation_started_at': started_at, 'evaluation_expires_at': expires_at,
                'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
            }
            return
        if lowered.startswith('insert into organization_memberships'):
            (_row_id, org_id, user_id, role) = params
            self.memberships[str(user_id)] = str(org_id)
            return

    def _apply_request_update(self, lowered: str, params: Any) -> None:
        # Approve
        if 'invitation_token_hash = %s' in lowered and 'set status = %s' in lowered:
            (status_value, reviewed_at, reviewer, approved_at, token_hash_value, expires_at, row_id) = params
            row = self.requests.get(str(row_id))
            if row is None:
                return
            row.update({
                'status': status_value, 'reviewed_at': reviewed_at,
                'reviewed_by_user_id': reviewer,
                'approved_at': row.get('approved_at') or approved_at,
                'rejected_at': None,
                'invitation_token_hash': token_hash_value,
                'invitation_expires_at': expires_at,
                'invitation_sent_at': None, 'invitation_delivery_error': None,
            })
            return
        # Reject
        if 'rejected_at = %s' in lowered:
            (status_value, reviewed_at, reviewer, rejected_at, note, row_id) = params
            row = self.requests.get(str(row_id))
            if row is None:
                return
            if row.get('status') == pilot_access.STATUS_ACTIVATED:
                return
            row.update({
                'status': status_value, 'reviewed_at': reviewed_at,
                'reviewed_by_user_id': reviewer, 'rejected_at': rejected_at,
                'approved_at': None,
                'internal_note': note if note is not None else row.get('internal_note'),
                'invitation_token_hash': None, 'invitation_expires_at': None,
                'invitation_sent_at': None, 'invitation_delivery_error': None,
            })
            return
        # Delivery succeeded
        if 'invitation_sent_at = now()' in lowered:
            (status_value, row_id, required_status) = params
            row = self.requests.get(str(row_id))
            if row is not None and row.get('status') == required_status:
                row.update({
                    'status': status_value, 'invitation_sent_at': NOW,
                    'invitation_delivery_error': None,
                })
            return
        # Delivery failed
        if 'invitation_delivery_error = %s' in lowered:
            (error, row_id) = params
            row = self.requests.get(str(row_id))
            if row is not None:
                row.update({'invitation_sent_at': None, 'invitation_delivery_error': error})
            return
        # Activate
        if 'invitation_accepted_at = %s' in lowered:
            (status_value, org_id, accepted_at, row_id, allowed) = params
            row = self.requests.get(str(row_id))
            if row is not None and row.get('status') in list(allowed):
                row.update({
                    'status': status_value, 'organization_id': org_id,
                    'invitation_accepted_at': accepted_at, 'invitation_token_hash': None,
                })
            return
        # Expire
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
            self._apply_write(sql, lowered, params)
            return _Result()

        if 'information_schema.tables' in lowered and 'pilot_requests' in str(params):
            return _Result({'table_count': 1 if self.pilot_requests_ready else 0})
        if 'information_schema.tables' in lowered:
            return _Result({
                'table_count': 2 if self.schema_ready else 0,
                'link_count': 1 if self.schema_ready else 0,
            })

        if 'from pilot_requests where invitation_token_hash' in lowered:
            token_hash_value = str(params[0])
            match = [
                dict(row) for row in self.requests.values()
                if row.get('invitation_token_hash') == token_hash_value
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
        if 'from pilot_requests where email = %s' in lowered:
            match = [dict(row) for row in self.requests.values() if row.get('email') == params[0]]
            return _Result(match[0] if match else None)
        if 'from pilot_requests' in lowered:
            status_filter = params[0] if params else None
            rows = [
                dict(row) for row in self.requests.values()
                if status_filter is None or row.get('status') == status_filter
            ]
            return _Result(rows)

        if 'select 1 from workspace_members where user_id' in lowered:
            return _Result({'1': 1} if str(params[0]) in self.workspace_member_user_ids else None)
        if 'count(*) as count from workspaces where organization_id' in lowered:
            return _Result({'count': int(self.workspace_counts.get(str(params[0]), 0))})

        if 'count(*) as count from organization_memberships where user_id' in lowered:
            return _Result({'count': 1 if str(params[0]) in self.memberships else 0})
        if 'from organization_memberships m join organizations o' in lowered:
            org_id = self.memberships.get(str(params[0]))
            return _Result(dict(self.organizations[org_id]) if org_id in self.organizations else None)

        if 'select email, is_internal_admin from users' in lowered:
            return _Result(self.users.get(str(params[0])))
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
def _request(headers: dict[str, str] | None = None, ip: str = '198.51.100.7') -> SimpleNamespace:
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=ip),
        scope={'path': '/pilot-requests'},
    )


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Internal admin comes from the database flag only, unless a test says so."""
    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    # A deterministic stand-in for the deployed keyed hash. It must be a REAL
    # digest, not a prefixed echo, or the "the raw token is never stored"
    # assertions below would pass vacuously.
    monkeypatch.setattr(
        pilot, '_auth_token_hash',
        lambda value: hashlib.sha256(f'test-secret::{value}'.encode('utf-8')).hexdigest(),
    )


def _use_connection(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _pg)


def _authenticate_as(
    monkeypatch: pytest.MonkeyPatch, *, user_id: str, email: str, email_verified: bool = True,
) -> None:
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection',
        lambda *_a, **_k: {'id': user_id, 'email': email, 'email_verified': email_verified},
    )


def _capture_email(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def _dispatch(_connection, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(pilot, '_dispatch_transactional_email', _dispatch)
    return sent


def _fail_email(monkeypatch: pytest.MonkeyPatch) -> None:
    def _dispatch(_connection, **_kwargs):
        raise RuntimeError('Failed to deliver email via Resend: status=403')

    monkeypatch.setattr(pilot, '_dispatch_transactional_email', _dispatch)


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


def _connection_with_pending(**overrides: Any) -> FakeConnection:
    return FakeConnection(
        requests={REQUEST_ID: _pending_request(**overrides)},
        users={
            FOUNDER_USER: {'email': FOUNDER_EMAIL, 'is_internal_admin': True},
            CUSTOMER_USER: {'email': 'customer@example.com', 'is_internal_admin': False},
            APPLICANT_USER: {'email': APPLICANT_EMAIL, 'is_internal_admin': False},
            ATTACKER_USER: {'email': ATTACKER_EMAIL, 'is_internal_admin': False},
        },
        organizations=dict(EXISTING_ORGS),
    )


def _approve(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> tuple[dict[str, Any], str]:
    """Approve REQUEST_ID as the founder and return (payload, raw token)."""
    tokens: list[str] = []
    sent = _capture_email(monkeypatch)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)
    payload = tenancy_endpoints.approve_admin_pilot_request(REQUEST_ID, {}, _request())
    tokens.extend(str(message.get('token')) for message in sent)
    return payload, tokens[0]


# ═════════════════════════════════════════════════════════════════════════════
# A — the public request
# ═════════════════════════════════════════════════════════════════════════════

def test_01_a_valid_request_creates_one_pending_row(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_pilot_request(dict(VALID_SUBMISSION), _request())

    assert result['received'] is True
    assert result['duplicate'] is False
    assert result['status'] == pilot_access.STATUS_PENDING
    assert len(connection.requests) == 1
    row = next(iter(connection.requests.values()))
    assert row['email'] == APPLICANT_EMAIL
    assert row['company_name'] == 'Company Treasury'
    assert connection.committed is True


def test_02_a_request_creates_no_workspace_and_no_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending application is a queue entry, not a tenant."""
    connection = FakeConnection()
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_pilot_request(dict(VALID_SUBMISSION), _request())

    written = [sql.lower() for sql, _ in connection.writes]
    assert not any(sql.startswith('insert into workspaces') for sql in written)
    assert not any(sql.startswith('insert into workspace_members') for sql in written)
    assert not any(sql.startswith('insert into organizations') for sql in written)
    assert not any(sql.startswith('insert into organization_memberships') for sql in written)
    assert connection.organizations == {}


def test_03_a_request_starts_no_monitoring_and_queues_no_background_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No RPC provider, no worker, no queued job — nothing that costs money."""
    connection = FakeConnection()
    _use_connection(monkeypatch, connection)
    monkeypatch.setattr(
        pilot, '_queue_background_job',
        lambda *_a, **_k: pytest.fail('a pending Pilot request must queue no work'),
    )

    tenancy_endpoints.submit_pilot_request(dict(VALID_SUBMISSION), _request())

    written = [sql.lower() for sql, _ in connection.writes]
    for table in ('monitoring_configs', 'targets', 'monitored_systems', 'background_jobs', 'assets'):
        assert not any(table in sql for sql in written), f'a pending request must not write {table}'


def test_04_a_duplicate_pending_request_does_not_create_a_second_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_pilot_request(dict(VALID_SUBMISSION), _request())

    assert result['duplicate'] is True
    assert 'already under review' in result['message']
    assert len(connection.requests) == 1
    assert not any(sql.lower().startswith('insert into pilot_requests') for sql, _ in connection.writes)


def test_04_b_a_rejected_request_may_be_resubmitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejection closes one application; it does not blacklist an address."""
    connection = _connection_with_pending(status=pilot_access.STATUS_REJECTED)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_pilot_request(dict(VALID_SUBMISSION), _request())

    assert result['duplicate'] is False
    assert len(connection.requests) == 2


@pytest.mark.parametrize('email', ['', 'not-an-email', 'no@domain', 'a b@example.com', 'x' * 250 + '@e.com'])
def test_05_a_invalid_email_is_refused_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch, email: str,
) -> None:
    connection = FakeConnection()
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.submit_pilot_request({**VALID_SUBMISSION, 'email': email}, _request())

    assert exc_info.value.status_code == 400
    assert connection.writes == []


@pytest.mark.parametrize('field', ['use_case', 'company_name', 'role'])
@pytest.mark.parametrize('secret', [
    '0x' + 'a' * 64,
    '-----BEGIN RSA PRIVATE KEY-----',
    'abandon ability able about above absent absorb abstract absurd abuse access accident',
])
def test_06_a_a_submission_that_carries_a_secret_is_refused_not_stored(
    monkeypatch: pytest.MonkeyPatch, field: str, secret: str,
) -> None:
    """Refuse rather than redact: a credential must never reach the database,
    so there is nothing to leak later from the review console or an audit row."""
    connection = FakeConnection()
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.submit_pilot_request({**VALID_SUBMISSION, field: secret}, _request())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail['code'] == pilot_access.CODE_PILOT_REQUEST_SECRET_DETECTED
    assert connection.writes == []
    assert connection.requests == {}


def test_06_b_the_submission_drops_every_field_it_does_not_own() -> None:
    """Only five fields survive validation, so a body naming a plan, an
    organization, or an internal-admin flag has nowhere for it to land."""
    fields = pilot_access.validate_submission({
        **VALID_SUBMISSION,
        'plan': 'enterprise',
        'organization_id': 'org-scale',
        'is_internal_admin': True,
        'status': 'approved',
        'entitlement_overrides': {'max_monitored_contracts': 9999},
    })

    assert set(fields) == {'email', 'company_name', 'role', 'company_website', 'use_case'}


def test_07_a_the_public_endpoint_is_rate_limited() -> None:
    """The public form reuses the auth limiter (IP + applicant address) rather
    than shipping a second, weaker anti-abuse story."""
    import inspect

    from services.api.app import main as api_main

    source = inspect.getsource(api_main.pilot_request_submit)
    assert 'enforce_auth_rate_limit' in source
    assert "'pilot_request'" in source
    limiter_call = source.index('enforce_auth_rate_limit')
    handler_call = source.index('submit_pilot_request')
    assert limiter_call < handler_call, 'the limiter must run before the handler'


def test_07_b_the_request_endpoint_refuses_when_the_schema_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed on a deployment that has not run migration 0151: report the
    condition rather than silently accepting a request that is never stored."""
    connection = FakeConnection(pilot_requests_ready=False)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.submit_pilot_request(dict(VALID_SUBMISSION), _request())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail['code'] == pilot_access.CODE_PILOT_REQUESTS_UNAVAILABLE
    assert connection.writes == []


@pytest.mark.parametrize('email', [APPLICANT_EMAIL, 'founder@startup.io', PERSONAL_EMAIL])
def test_11_a_no_email_domain_is_auto_approved(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    """A gmail.com applicant is not auto-rejected, and a company.com applicant is
    not auto-approved. Every address lands in the same review queue."""
    connection = FakeConnection()
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_pilot_request({**VALID_SUBMISSION, 'email': email}, _request())

    assert result['status'] == pilot_access.STATUS_PENDING
    row = next(iter(connection.requests.values()))
    assert row['status'] == pilot_access.STATUS_PENDING
    assert row['invitation_token_hash'] is None


# ═════════════════════════════════════════════════════════════════════════════
# B — internal review authorization
# ═════════════════════════════════════════════════════════════════════════════

def test_08_a_internal_admin_can_list_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    listing = tenancy_endpoints.list_admin_pilot_requests(_request())

    assert listing['count'] == 1
    assert listing['requests'][0]['email'] == APPLICANT_EMAIL
    # The token hash is internal even to the internal console.
    assert 'invitation_token_hash' not in listing['requests'][0]


def test_09_a_a_normal_user_cannot_list_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email='customer@example.com')

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.list_admin_pilot_requests(_request())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == org_service.CODE_INTERNAL_ADMIN_REQUIRED


@pytest.mark.parametrize('action', ['approve', 'reject', 'resend'])
def test_09_b_a_normal_user_cannot_review_a_request(
    monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email='customer@example.com')
    _capture_email(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        if action == 'approve':
            tenancy_endpoints.approve_admin_pilot_request(REQUEST_ID, {}, _request())
        elif action == 'reject':
            tenancy_endpoints.reject_admin_pilot_request(REQUEST_ID, {}, _request())
        else:
            tenancy_endpoints.resend_admin_pilot_invitation(REQUEST_ID, _request())

    assert exc_info.value.status_code == 403
    # Authorization runs BEFORE anything is read or written.
    assert connection.writes == []
    assert connection.requests[REQUEST_ID]['status'] == pilot_access.STATUS_PENDING


def test_10_a_internal_admin_can_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    payload, _token = _approve(monkeypatch, connection)

    assert payload['invitation_sent'] is True
    assert payload['request']['status'] == pilot_access.STATUS_INVITED
    assert connection.requests[REQUEST_ID]['reviewed_by_user_id'] == FOUNDER_USER


def test_12_a_internal_admin_can_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    payload = tenancy_endpoints.reject_admin_pilot_request(
        REQUEST_ID, {'internal_note': 'No live RWA assets to monitor yet.'}, _request(),
    )

    assert payload['request']['status'] == pilot_access.STATUS_REJECTED
    assert connection.requests[REQUEST_ID]['internal_note'] == 'No live RWA assets to monitor yet.'


def test_13_a_a_rejected_request_creates_no_invitation_and_no_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    tenancy_endpoints.reject_admin_pilot_request(REQUEST_ID, {}, _request())

    row = connection.requests[REQUEST_ID]
    assert row['invitation_token_hash'] is None
    assert row['invitation_expires_at'] is None
    written = [sql.lower() for sql, _ in connection.writes]
    assert not any(sql.startswith('insert into organizations') for sql in written)
    assert not any(sql.startswith('insert into workspaces') for sql in written)


def test_13_b_an_activated_request_cannot_be_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suspend the organization instead. Rejecting would leave a live tenant
    attached to a row that claims it was declined."""
    connection = _connection_with_pending(
        status=pilot_access.STATUS_ACTIVATED, organization_id='org-datto',
    )
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.reject_admin_pilot_request(REQUEST_ID, {}, _request())

    assert exc_info.value.status_code == 409
    assert connection.requests[REQUEST_ID]['status'] == pilot_access.STATUS_ACTIVATED


def test_38_a_the_founder_reaches_the_internal_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """The founder account keeps internal access from the database flag alone —
    no email is hard-coded in any authorization path."""
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    assert tenancy_endpoints.list_admin_pilot_requests(_request())['count'] == 1
    assert tenancy_endpoints.list_admin_customers(_request())['count'] >= 0


def test_38_b_no_founder_address_is_hard_coded_in_authorization() -> None:
    import inspect

    for module in (org_service, pilot_access, tenancy_endpoints):
        source = inspect.getsource(module)
        assert 'decoda.guard@gmail.com' not in source
        assert '@decodasecurity.com' not in source.replace('support@decodasecurity.com', '')


# ═════════════════════════════════════════════════════════════════════════════
# C — invitation security
# ═════════════════════════════════════════════════════════════════════════════

def test_14_a_approval_creates_an_invitation_and_mails_it(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    sent = _capture_email(monkeypatch)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    tenancy_endpoints.approve_admin_pilot_request(REQUEST_ID, {}, _request())

    assert len(sent) == 1
    assert sent[0]['purpose'] == 'pilot_invitation'
    assert sent[0]['to_email'] == APPLICANT_EMAIL
    row = connection.requests[REQUEST_ID]
    assert row['invitation_token_hash'] is not None
    assert row['invitation_expires_at'] > NOW


def test_15_a_only_a_token_hash_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plaintext token exists in the email and nowhere else, so a database
    dump cannot be used to accept an invitation."""
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)

    row = connection.requests[REQUEST_ID]
    assert token
    assert len(token) >= 32
    assert row['invitation_token_hash'] != token
    assert token not in str(row)
    # Nor does the raw token appear in any statement sent to the database.
    assert not any(token in str(params) for _sql, params in connection.writes)


def test_15_b_the_approval_response_never_carries_the_raw_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection_with_pending()
    payload, token = _approve(monkeypatch, connection)

    assert token not in str(payload)


def test_15_c_two_approvals_mint_different_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _first, token_one = _approve(monkeypatch, connection)
    _second, token_two = _approve(monkeypatch, connection)

    assert token_one != token_two
    # The replacement retires the old link: only the newest hash resolves.
    assert connection.requests[REQUEST_ID]['invitation_token_hash'] == pilot._auth_token_hash(token_two)


def test_16_a_a_valid_invitation_resolves_for_its_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    lookup = tenancy_endpoints.lookup_pilot_invitation(token, _request())

    assert lookup['valid'] is True
    assert lookup['invitation']['email'] == APPLICANT_EMAIL
    assert lookup['invitation']['evaluation_days'] == ent.evaluation_days()


def test_18_a_an_expired_invitation_is_refused_and_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    connection.requests[REQUEST_ID]['invitation_expires_at'] = NOW - timedelta(days=1)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    assert exc_info.value.detail['code'] == pilot_access.CODE_INVITATION_EXPIRED
    assert connection.requests[REQUEST_ID]['status'] == pilot_access.STATUS_EXPIRED
    assert connection.requests[REQUEST_ID]['invitation_token_hash'] is None
    assert connection.organizations == EXISTING_ORGS


def test_19_a_an_accepted_invitation_cannot_be_used_again(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': APPLICANT_USER})

    tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())
    organizations_after_first = dict(connection.organizations)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    assert exc_info.value.detail['code'] == pilot_access.CODE_INVITATION_INVALID
    assert connection.organizations == organizations_after_first


def test_20_a_a_rejected_requests_invitation_stops_working(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approve, then reject: the already-minted link must die with the decision."""
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)
    tenancy_endpoints.reject_admin_pilot_request(REQUEST_ID, {}, _request())

    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)
    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    assert exc_info.value.detail['code'] == pilot_access.CODE_INVITATION_INVALID
    assert connection.organizations == EXISTING_ORGS


def test_37_a_a_random_token_reaches_no_invitation(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=ATTACKER_USER, email=ATTACKER_EMAIL)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.accept_pilot_invitation({'token': 'guessed-token-value'}, _request())

    assert exc_info.value.detail['code'] == pilot_access.CODE_INVITATION_INVALID
    assert connection.organizations == EXISTING_ORGS


def test_17_c_a_lookup_with_no_token_reveals_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection_with_pending()
    _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)

    lookup = tenancy_endpoints.lookup_pilot_invitation('', _request())

    assert lookup['valid'] is False
    assert lookup['invitation'] is None


# ═════════════════════════════════════════════════════════════════════════════
# D — email matching
# ═════════════════════════════════════════════════════════════════════════════

def test_17_a_a_different_account_cannot_accept_the_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=ATTACKER_USER, email=ATTACKER_EMAIL)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == pilot_access.CODE_INVITATION_EMAIL_MISMATCH
    # The invited address is not disclosed to the wrong holder.
    assert APPLICANT_EMAIL not in str(exc_info.value.detail)
    assert connection.organizations == EXISTING_ORGS
    assert connection.requests[REQUEST_ID]['status'] == pilot_access.STATUS_INVITED


def test_17_b_an_unverified_account_cannot_accept_even_with_the_right_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claiming the address is not owning it. Until the address is verified, an
    exact string match proves nothing."""
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(
        monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL, email_verified=False,
    )

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    assert exc_info.value.status_code == 403
    assert connection.organizations == EXISTING_ORGS
    assert connection.requests[REQUEST_ID]['status'] == pilot_access.STATUS_INVITED


@pytest.mark.parametrize('variant', ['Security@Company.com', '  security@company.com  ', 'SECURITY@COMPANY.COM'])
def test_10_b_email_matching_uses_the_auth_models_normalisation(
    monkeypatch: pytest.MonkeyPatch, variant: str,
) -> None:
    """Case and surrounding whitespace are the same address; a different mailbox
    is not."""
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=variant)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': APPLICANT_USER})

    result = tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    assert result['activated'] is True


# ═════════════════════════════════════════════════════════════════════════════
# E — activation
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def activated(monkeypatch: pytest.MonkeyPatch):
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': APPLICANT_USER})
    result = tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())
    return SimpleNamespace(connection=connection, result=result, token=token)


def test_24_a_acceptance_creates_exactly_one_new_organization(activated) -> None:
    new_orgs = {k: v for k, v in activated.connection.organizations.items() if k not in EXISTING_ORGS}
    assert len(new_orgs) == 1
    organization = next(iter(new_orgs.values()))
    assert activated.result['organization']['id'] == str(organization['id'])
    assert activated.connection.requests[REQUEST_ID]['organization_id'] == str(organization['id'])


def test_25_a_the_new_organization_is_on_the_pilot_plan(activated) -> None:
    new_org = next(v for k, v in activated.connection.organizations.items() if k not in EXISTING_ORGS)
    assert new_org['plan'] == ent.PLAN_PILOT
    assert activated.result['organization']['plan'] == ent.PLAN_PILOT


def test_26_a_the_new_organization_is_active(activated) -> None:
    new_org = next(v for k, v in activated.connection.organizations.items() if k not in EXISTING_ORGS)
    assert new_org['status'] == ent.STATUS_ACTIVE


def test_27_a_the_evaluation_window_starts_at_activation(activated) -> None:
    new_org = next(v for k, v in activated.connection.organizations.items() if k not in EXISTING_ORGS)
    assert new_org['evaluation_started_at'] is not None
    assert new_org['evaluation_expires_at'] is not None


def test_28_a_the_window_length_is_the_configured_pilot_evaluation_days(activated) -> None:
    new_org = next(v for k, v in activated.connection.organizations.items() if k not in EXISTING_ORGS)
    window = new_org['evaluation_expires_at'] - new_org['evaluation_started_at']
    assert window.days == ent.evaluation_days()


def test_28_b_the_window_follows_a_changed_pilot_evaluation_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ent.EVALUATION_DAYS_ENV, '14')
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': APPLICANT_USER})

    tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    new_org = next(v for k, v in connection.organizations.items() if k not in EXISTING_ORGS)
    assert (new_org['evaluation_expires_at'] - new_org['evaluation_started_at']).days == 14


def test_29_a_a_workspace_is_created_and_selected(activated) -> None:
    written = [sql.lower() for sql, _ in activated.connection.writes]
    assert any(sql.startswith('insert into workspaces') for sql in written)
    assert any(sql.startswith('insert into workspace_members') for sql in written)
    assert any('current_workspace_id = %s' in sql for sql in written)
    assert activated.result['workspace']['id']


def test_21_a_the_first_member_of_a_new_tenant_is_its_owner(activated) -> None:
    membership = next(
        params for sql, params in activated.connection.writes
        if sql.lower().startswith('insert into organization_memberships')
    )
    assert membership[3] == 'owner'
    workspace_membership = next(
        params for sql, params in activated.connection.writes
        if sql.lower().startswith('insert into workspace_members')
    )
    assert workspace_membership[3] == 'owner'


@pytest.mark.parametrize('claim', [
    {'plan': 'scale'},
    {'plan': 'enterprise'},
    {'is_internal_admin': True},
    {'organization_id': 'org-scale'},
    {'role': 'owner', 'entitlement_overrides': {'max_monitored_contracts': 9999}},
    {'status': 'active', 'evaluation_expires_at': '2099-01-01T00:00:00Z'},
])
def test_21_b_the_acceptance_body_cannot_choose_plan_role_or_tenant(
    monkeypatch: pytest.MonkeyPatch, claim: dict[str, Any],
) -> None:
    """Only ``token`` is read from the body. Everything that governs entitlement
    is decided server-side, so a hostile client has nothing to set."""
    connection = _connection_with_pending()
    _payload, token = _approve(monkeypatch, connection)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': APPLICANT_USER})

    result = tenancy_endpoints.accept_pilot_invitation({'token': token, **claim}, _request())

    assert result['organization']['plan'] == ent.PLAN_PILOT
    assert result['organization']['status'] == ent.STATUS_ACTIVE
    assert result['organization']['id'] not in EXISTING_ORGS
    assert not any('is_internal_admin' in sql.lower() for sql, _ in connection.writes)
    new_org = next(v for k, v in connection.organizations.items() if k not in EXISTING_ORGS)
    assert new_org['entitlement_overrides'] == {}
    assert ent.get_entitlements(new_org)[ent.LIMIT_MONITORED_CONTRACTS] == 5
    assert ent.get_entitlements(new_org)[ent.FEATURE_AUTOMATIC_EXECUTION] is False


def test_30_a_activation_leads_to_the_existing_dashboard(activated) -> None:
    """No separate Pilot or demo dashboard: the response carries the ordinary
    workspace + user payload the product already renders."""
    assert set(activated.result) >= {'activated', 'organization', 'evaluation', 'workspace', 'user'}
    assert 'pilot_dashboard_url' not in activated.result
    assert 'demo' not in str(activated.result).lower()


def test_30_b_no_separate_pilot_dashboard_route_exists() -> None:
    import inspect

    from services.api.app import main as api_main

    source = inspect.getsource(api_main)
    assert '/pilot-dashboard' not in source
    assert '/demo-dashboard' not in source


def test_31_a_an_approval_whose_email_fails_is_not_reported_as_invited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Approved — invitation not sent" is the truthful state. Marking it
    ``invited`` would tell the founder a link went out that never did."""
    connection = _connection_with_pending()
    _fail_email(monkeypatch)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    payload = tenancy_endpoints.approve_admin_pilot_request(REQUEST_ID, {}, _request())

    assert payload['invitation_sent'] is False
    assert payload['request']['status'] == pilot_access.STATUS_APPROVED
    assert payload['request']['invitation_not_sent'] is True
    assert payload['request']['invitation_sent_at'] is None
    # The approval still stands, and the token remains valid for a retry.
    assert connection.requests[REQUEST_ID]['invitation_token_hash'] is not None


# ═════════════════════════════════════════════════════════════════════════════
# F — the loopholes this change closes
# ═════════════════════════════════════════════════════════════════════════════

def test_36_a_signing_up_grants_no_monitoring_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """A random authenticated account is not a tenant.

    ``signup_user`` writes the user row and nothing else — no workspace, no
    membership, no organization, no evaluation window.
    """
    written: list[str] = []

    class _SignupConn:
        def execute(self, query: str, params: Any = None) -> _Result:
            sql = ' '.join(str(query).split())
            if sql.lower().startswith(('insert', 'update', 'delete')):
                written.append(sql.lower())
            return _Result(None)

        def commit(self) -> None:
            return None

    @contextmanager
    def _pg():
        yield _SignupConn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'hash_password', lambda _p: 'hashed')
    monkeypatch.setattr(pilot, '_create_user_token', lambda *_a, **_k: 'verify-token')
    monkeypatch.setattr(pilot, '_dispatch_transactional_email', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': 'user-new'})

    pilot.signup_user(
        {'email': 'random@example.com', 'password': 'StrongPass1234',
         'full_name': 'Random Person', 'workspace_name': 'Random Ops'},
        _request(),
    )

    assert any(sql.startswith('insert into users') for sql in written)
    for forbidden in ('insert into workspaces', 'insert into workspace_members',
                      'insert into organizations', 'insert into organization_memberships'):
        assert not any(sql.startswith(forbidden) for sql in written), forbidden


def test_36_b_an_unapproved_account_cannot_create_a_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second loophole: creating a workspace used to mint an active tenant."""
    connection = FakeConnection()
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_: True)
    monkeypatch.setattr(
        org_service, 'create_organization',
        lambda *_a, **_k: pytest.fail('an unapproved account must not receive a tenant'),
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot._resolve_organization_for_new_workspace(
            connection, user_id='user-new',
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == pilot.PILOT_ACCESS_REQUIRED_CODE
    assert connection.writes == []


def test_36_b_2_a_crafted_signup_body_cannot_grant_a_tenant_plan_or_admin_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct-API version of the loophole.

    Hiding the workspace field in React would stop a browser and nobody else, so
    the check that matters is here: a body that names an organization, a plan, a
    role, or the internal-admin flag still writes exactly one users row. Every
    one of those fields is decided server-side at invitation acceptance.
    """
    written: list[tuple[str, Any]] = []

    class _SignupConn:
        def execute(self, query: str, params: Any = None) -> _Result:
            sql = ' '.join(str(query).split())
            if sql.lower().startswith(('insert', 'update', 'delete')):
                written.append((sql.lower(), params))
            return _Result(None)

        def commit(self) -> None:
            return None

    @contextmanager
    def _pg():
        yield _SignupConn()

    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'hash_password', lambda _p: 'hashed')
    monkeypatch.setattr(pilot, '_create_user_token', lambda *_a, **_k: 'verify-token')
    monkeypatch.setattr(pilot, '_dispatch_transactional_email', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': 'user-new'})

    pilot.signup_user(
        {
            'email': 'attacker@example.com', 'password': 'StrongPass1234',
            'full_name': 'Crafted Body', 'workspace_name': 'Crafted Ops',
            # None of these exist as inputs anywhere in the signup path.
            'organization_id': 'org-scale', 'plan': ent.PLAN_SCALE, 'role': 'owner',
            'is_internal_admin': True, 'status': ent.STATUS_ACTIVE,
            'entitlement_overrides': {'monitored_contracts': 9999},
            'evaluation_expires_at': '2099-01-01T00:00:00Z',
        },
        _request(),
    )

    inserts = [sql for sql, _params in written]
    assert [sql for sql in inserts if sql.startswith('insert into users')], 'the account was not created'
    for forbidden in ('insert into workspaces', 'insert into workspace_members',
                      'insert into organizations', 'insert into organization_memberships'):
        assert not any(sql.startswith(forbidden) for sql in inserts), forbidden
    # Nothing the body named reached a statement, not even as a stored value.
    flat = ' '.join(f'{sql} {params}' for sql, params in written)
    for forbidden in ('org-scale', ent.PLAN_SCALE, 'is_internal_admin', 'entitlement_overrides'):
        assert forbidden not in flat, forbidden


def test_36_b_3_workspace_creation_fails_closed_when_approval_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable tenancy schema is not an approval.

    organization_memberships is the only place approval is recorded. When the
    probe cannot read it, the gate used to return None and let the workspace be
    created anyway — an unapproved account's first workspace, owned by no
    organization and therefore metered by no plan and expired by no evaluation
    window. A caller with no membership is now refused instead.
    """
    connection = FakeConnection(schema_ready=False)
    monkeypatch.setattr(
        org_service, 'create_organization',
        lambda *_a, **_k: pytest.fail('an unapproved account must not receive a tenant'),
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot._resolve_organization_for_new_workspace(connection, user_id='user-new')

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == pilot.PILOT_ACCESS_REQUIRED_CODE
    assert connection.writes == []


def test_36_b_4_an_unreadable_probe_is_refused_the_same_as_an_absent_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNKNOWN and ABSENT are both "we cannot say you were approved"."""
    connection = FakeConnection()
    monkeypatch.setattr(
        org_service, 'tenancy_schema_state', lambda *_a, **_k: org_service.SCHEMA_UNKNOWN,
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot._resolve_organization_for_new_workspace(connection, user_id='user-new')

    assert exc_info.value.detail['code'] == pilot.PILOT_ACCESS_REQUIRED_CODE
    assert connection.writes == []


def test_36_b_5_an_existing_member_still_creates_a_workspace_during_a_rolling_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing closed must not lock out the people who were already admitted.

    An account holding a workspace membership was admitted before the probe
    broke, so it keeps working exactly as it did pre-0150 — the case the
    unmigrated branch exists for.
    """
    connection = FakeConnection(schema_ready=False, workspace_member_user_ids={CUSTOMER_USER})

    organization = pilot._resolve_organization_for_new_workspace(connection, user_id=CUSTOMER_USER)

    assert organization is None  # pre-0150 behaviour: a workspace with no tenant to own it
    assert connection.writes == []


def test_36_b_6_an_existing_member_can_still_create_a_workspace_under_the_plan_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3: an authorized member adding a workspace inside their own tenant.

    The tenant comes from their membership, never from the request body, and the
    plan limit is enforced against it exactly as before.
    """
    connection = FakeConnection(
        organizations=dict(EXISTING_ORGS),
        memberships={CUSTOMER_USER: 'org-scale'},
        workspace_counts={'org-scale': 1},
    )

    organization = pilot._resolve_organization_for_new_workspace(connection, user_id=CUSTOMER_USER)

    assert organization is not None
    assert str(organization['id']) == 'org-scale'
    assert ent.normalize_plan(organization['plan']) == ent.PLAN_SCALE
    assert connection.writes == []


def test_36_b_7_the_plan_limit_still_refuses_a_tenant_at_its_workspace_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activation does not lift a limit: a Pilot at its ceiling is still refused."""
    limit = ent.limit_for(ent.plan_entitlements(ent.PLAN_PILOT), ent.LIMIT_WORKSPACES)
    if limit is None:
        pytest.skip('the Pilot plan does not cap workspaces')
    connection = FakeConnection(
        organizations=dict(EXISTING_ORGS),
        memberships={APPLICANT_USER: 'org-datto'},
        workspace_counts={'org-datto': int(limit)},
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot._resolve_organization_for_new_workspace(connection, user_id=APPLICANT_USER)

    assert exc_info.value.status_code == 403
    assert connection.writes == []


def test_36_c_an_account_with_no_tenant_reports_no_pilot_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(users={CUSTOMER_USER: {'email': 'customer@example.com'}})
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email='customer@example.com')

    state = tenancy_endpoints.get_pilot_access_state(_request())

    assert state['has_access'] is False
    assert state['state'] == pilot_access.ACCESS_NONE


def test_36_d_an_applicant_awaiting_review_is_told_so_not_shown_a_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Under review" and "you have no access" are different facts, and the
    product says which one applies rather than rendering an empty dashboard."""
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)

    state = tenancy_endpoints.get_pilot_access_state(_request())

    assert state['has_access'] is False
    assert state['state'] == pilot_access.ACCESS_PENDING_REVIEW
    assert state['request']['status'] == pilot_access.STATUS_PENDING
    # The applicant sees their own status, never internal review detail.
    assert 'internal_note' not in state['request']
    assert 'reviewed_by_user_id' not in state['request']


def test_36_e_an_activated_member_reports_access(activated, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_connection(monkeypatch, activated.connection)
    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)

    state = tenancy_endpoints.get_pilot_access_state(_request())

    assert state['has_access'] is True
    assert state['state'] == pilot_access.ACCESS_ACTIVE


# ═════════════════════════════════════════════════════════════════════════════
# G — existing tenants are untouched
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('organization_id,expected_plan,expected_status', [
    ('org-rabbit', ent.PLAN_PILOT, ent.STATUS_ACTIVE),
    ('org-datto', ent.PLAN_PILOT, ent.STATUS_ACTIVE),
    ('org-scale', ent.PLAN_SCALE, ent.STATUS_ACTIVE),
    ('org-enterprise', ent.PLAN_ENTERPRISE, ent.STATUS_ACTIVE),
])
def test_31_to_35_existing_organizations_survive_an_activation_unchanged(
    activated, organization_id: str, expected_plan: str, expected_status: str,
) -> None:
    """Approval-only applies to NEW external onboarding. A workspace that already
    exists keeps its plan, its status, and its evaluation window."""
    organization = activated.connection.organizations[organization_id]
    assert organization == EXISTING_ORGS[organization_id]
    assert organization['plan'] == expected_plan
    assert organization['status'] == expected_status


def test_35_a_an_existing_active_pilot_keeps_its_grandfathered_window(activated) -> None:
    rabbit = activated.connection.organizations['org-rabbit']
    assert rabbit['evaluation_expires_at'] is None
    assert ent.lifecycle_state(rabbit) == ent.LIFECYCLE_ACTIVE_PILOT


def test_35_b_activation_writes_no_row_against_an_existing_organization(activated) -> None:
    for sql, params in activated.connection.writes:
        assert not any(str(existing) in str(params) for existing in EXISTING_ORGS), sql


# ═════════════════════════════════════════════════════════════════════════════
# Audit trail
# ═════════════════════════════════════════════════════════════════════════════

def test_20_b_the_lifecycle_is_audited_without_ever_logging_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(pilot, 'log_audit', lambda _c, **kwargs: audits.append(kwargs))

    connection = _connection_with_pending()
    sent = _capture_email(monkeypatch)
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)
    tenancy_endpoints.approve_admin_pilot_request(REQUEST_ID, {}, _request())
    token = str(sent[0]['token'])

    _authenticate_as(monkeypatch, user_id=APPLICANT_USER, email=APPLICANT_EMAIL)
    monkeypatch.setattr(pilot, 'build_user_response', lambda *_a, **_k: {'id': APPLICANT_USER})
    tenancy_endpoints.accept_pilot_invitation({'token': token}, _request())

    actions = [entry['action'] for entry in audits]
    assert 'pilot_request.approved' in actions
    assert 'pilot_request.invitation_sent' in actions
    assert 'pilot_request.invitation_accepted' in actions
    assert 'organization.pilot_created' in actions
    assert token not in str(audits)
    assert 'invitation_token_hash' not in str(audits)


def test_20_c_a_submission_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(pilot, 'log_audit', lambda _c, **kwargs: audits.append(kwargs))
    connection = FakeConnection()
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_pilot_request(dict(VALID_SUBMISSION), _request())

    assert [entry['action'] for entry in audits] == ['pilot_request.submitted']
    # An unauthenticated applicant has no actor and no workspace to attribute to.
    assert audits[0]['user_id'] is None
    assert audits[0]['workspace_id'] is None
    # The free-text use case stays in the one table internal staff read.
    assert VALID_SUBMISSION['use_case'] not in str(audits[0]['metadata'])


def test_20_d_a_rejection_records_that_a_note_exists_not_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(pilot, 'log_audit', lambda _c, **kwargs: audits.append(kwargs))
    connection = _connection_with_pending()
    _use_connection(monkeypatch, connection)
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL)

    note = 'Applicant has no live RWA assets; revisit next quarter.'
    tenancy_endpoints.reject_admin_pilot_request(REQUEST_ID, {'internal_note': note}, _request())

    assert audits[0]['action'] == 'pilot_request.rejected'
    assert audits[0]['metadata']['internal_note_recorded'] is True
    assert note not in str(audits)


# ═════════════════════════════════════════════════════════════════════════════
# Invitation lifetime configuration
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('raw,expected', [
    (None, pilot_access.DEFAULT_INVITATION_TTL_HOURS),
    ('', pilot_access.DEFAULT_INVITATION_TTL_HOURS),
    ('not-a-number', pilot_access.DEFAULT_INVITATION_TTL_HOURS),
    ('48', 48),
    ('0', pilot_access.MIN_INVITATION_TTL_HOURS),
    ('100000', pilot_access.MAX_INVITATION_TTL_HOURS),
])
def test_invitation_ttl_is_clamped_to_a_sane_range(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int,
) -> None:
    if raw is None:
        monkeypatch.delenv(pilot_access.INVITATION_TTL_HOURS_ENV, raising=False)
    else:
        monkeypatch.setenv(pilot_access.INVITATION_TTL_HOURS_ENV, raw)

    assert pilot_access.invitation_ttl_hours() == expected


def test_the_invitation_email_carries_a_link_and_no_secret_request() -> None:
    subject, text, html = pilot._email_message(
        'pilot_invitation',
        token='raw-token-value',
        context={
            'company_name': 'Company Treasury', 'reference': REQUEST_ID,
            'ttl_hours': 168, 'evaluation_days': 30,
        },
    )

    assert 'approved' in subject.lower()
    assert '/accept-invitation?token=raw-token-value' in text
    assert '/accept-invitation?token=raw-token-value' in html
    assert 'private keys' in text
    assert REQUEST_ID in text
    # No password, and nothing that would work after the invitation is consumed.
    assert 'password' not in text.lower()
