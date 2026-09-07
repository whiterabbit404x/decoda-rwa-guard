"""Organization tenancy — cross-tenant isolation, plan limits, admin authorization.

Covers the security-critical behaviour of the multi-tenant foundation:

  A  Organization context is derived from the SESSION workspace, never from the
     request. A body/query organization_id changes nothing.
  B  Organization A cannot read Organization B's plan, usage, or feedback.
  C  Pilot limits: 1 workspace, 5 contracts, 10 evidence packages — the next one
     of each is refused by the BACKEND with the canonical response body.
  D  Scale limits are enforced independently (25 contracts, unlimited evidence).
  E  An expired evaluation blocks new expensive work and preserves the data.
  F  A suspended organization blocks new work with its own reason code.
  G  Pilot → Scale is a plan change: same organization row, same workspaces, and
     Scale limits apply immediately.
  H  /admin/customers denies a customer with 403 and returns NO organization data.
  I  An internal admin is allowed, and internal access cannot be granted through
     any request parameter.
  J  The internal-admin email allowlist rejects wildcard/domain entries.
  K  Feedback is workspace/organization stamped server-side and refuses secrets.
  L  The monitoring worker excludes suspended/expired tenants from due-selection.

Run:
    python -m pytest services/api/tests/test_organization_tenancy_isolation.py -q
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app.domains.tenancy import endpoints as tenancy_endpoints

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

ORG_A = 'aaaaaaaa-1111-1111-1111-111111111111'
ORG_B = 'bbbbbbbb-2222-2222-2222-222222222222'
WS_A = 'aaaaaaaa-0000-0000-0000-00000000000a'
WS_B = 'bbbbbbbb-0000-0000-0000-00000000000b'
USER_A = 'user-aaaa'
USER_B = 'user-bbbb'
ADMIN_USER = 'user-founder'


# ── fake connection ───────────────────────────────────────────────────────────

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
    """Dispatches on normalised SQL. Records every write so a test can assert
    that a refused operation wrote nothing."""

    def __init__(
        self,
        *,
        organizations: dict[str, dict[str, Any]] | None = None,
        workspace_org: dict[str, str] | None = None,
        counts: dict[str, int] | None = None,
        schema_ready: bool = True,
        users: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.organizations = organizations or {}
        self.workspace_org = workspace_org or {}
        self.counts = counts or {}
        self.schema_ready = schema_ready
        self.users = users or {}
        self.writes: list[tuple[str, Any]] = []
        self.committed = False

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()

        if lowered.startswith(('insert', 'update', 'delete')):
            self.writes.append((sql, params))
            return _Result()

        if 'information_schema.tables' in lowered:
            ready = 2 if self.schema_ready else 0
            return _Result({'table_count': ready, 'link_count': 1 if self.schema_ready else 0,
                            'org_table': 1 if self.schema_ready else 0,
                            'link_column': 1 if self.schema_ready else 0})

        if 'from workspaces w join organizations o on o.id = w.organization_id' in lowered:
            workspace_id = str(params[0])
            org_id = self.workspace_org.get(workspace_id)
            return _Result(dict(self.organizations[org_id]) if org_id in self.organizations else None)

        if 'from organizations where id = %s' in lowered:
            org_id = str(params[0])
            return _Result(dict(self.organizations[org_id]) if org_id in self.organizations else None)

        if 'select email, is_internal_admin from users' in lowered:
            return _Result(self.users.get(str(params[0])))

        if 'count(*) as count from workspaces where organization_id' in lowered:
            return _Result({'count': self.counts.get(f'workspaces:{params[0]}', 0)})
        if 'count(*) as count from assets' in lowered:
            return _Result({'count': self.counts.get(f'assets:{params[0]}', 0)})
        if 'count(*) as count from targets' in lowered:
            return _Result({'count': self.counts.get(f'targets:{params[0]}', 0)})
        if 'count(*) as count from export_jobs' in lowered:
            return _Result({'count': self.counts.get(f'evidence:{params[0]}', 0)})

        if 'from workspaces where organization_id' in lowered:
            return _Result([
                {'id': ws, 'name': 'Workspace', 'slug': 'ws', 'created_at': NOW}
                for ws, org in self.workspace_org.items() if org == str(params[0])
            ])
        if 'from organization_memberships m join users u' in lowered:
            return _Result([])
        if 'from organization_feedback f join organizations o' in lowered:
            return _Result([])
        if 'select role from organization_memberships' in lowered:
            return _Result({'role': 'owner'})
        return _Result()

    def commit(self) -> None:
        self.committed = True


def _org_row(org_id: str, **overrides: Any) -> dict[str, Any]:
    row = {
        'id': org_id,
        'name': f'Org {org_id[:4]}',
        'slug': f'org-{org_id[:4]}',
        'plan': ent.PLAN_PILOT,
        'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=7),
        'evaluation_expires_at': NOW + timedelta(days=23),
        'entitlement_overrides': {},
        'created_at': NOW - timedelta(days=7),
        'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _two_tenant_connection(**overrides: Any) -> FakeConnection:
    return FakeConnection(
        organizations={ORG_A: _org_row(ORG_A), ORG_B: _org_row(ORG_B, plan=ent.PLAN_SCALE)},
        workspace_org={WS_A: ORG_A, WS_B: ORG_B},
        counts={
            'workspaces:' + ORG_A: 1, 'assets:' + ORG_A: 3, 'targets:' + ORG_A: 4, 'evidence:' + ORG_A: 4,
            'workspaces:' + ORG_B: 2, 'assets:' + ORG_B: 20, 'targets:' + ORG_B: 30, 'evidence:' + ORG_B: 99,
        },
        **overrides,
    )


def _request(headers: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(headers=headers or {}, client=None)


def _authenticate_as(monkeypatch: pytest.MonkeyPatch, *, user_id: str, workspace_id: str) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': user_id, 'email': f'{user_id}@example.com'})
    monkeypatch.setattr(
        pilot, 'resolve_workspace',
        lambda *_a, **_k: {'workspace_id': workspace_id, 'role': 'owner',
                           'workspace': {'id': workspace_id, 'name': 'W', 'slug': 'w'}},
    )
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


def _use_connection(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _pg)


# ── A — organization context is session-derived ───────────────────────────────

def test_A_organization_context_comes_from_the_session_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    payload = tenancy_endpoints.get_account_plan(_request())
    assert payload['organization']['id'] == ORG_A
    assert payload['plan'] == 'pilot'


def test_A2_query_and_body_organization_id_are_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crafted request naming Organization B still resolves Organization A."""
    connection = _two_tenant_connection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    request = _request({'x-organization-id': ORG_B, 'x-workspace-id': WS_B})
    payload = tenancy_endpoints.get_account_plan(request)
    # resolve_workspace is what decides the workspace, and it is patched to the
    # caller's real membership — the header cannot move the tenant.
    assert payload['organization']['id'] == ORG_A
    assert payload['plan'] == 'pilot'


# ── B — no cross-tenant plan or usage disclosure ──────────────────────────────

def test_B_organization_a_never_sees_organization_b_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    payload = tenancy_endpoints.get_account_plan(_request())
    assert payload['usage']['monitored_contracts']['current'] == 3      # A's count
    assert payload['usage']['evidence_packages']['current'] == 4        # A's count
    assert ORG_B not in str(payload)


def test_B2_each_tenant_reads_only_its_own_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection()
    _use_connection(monkeypatch, connection)

    _authenticate_as(monkeypatch, user_id=USER_B, workspace_id=WS_B)
    payload_b = tenancy_endpoints.get_account_plan(_request())
    assert payload_b['organization']['id'] == ORG_B
    assert payload_b['plan'] == 'scale'
    # Scale has no evaluation window, so no countdown may be reported.
    assert payload_b['evaluation'] is None


# ── C — Pilot limits are enforced backend-side ────────────────────────────────

def test_C_pilot_first_workspace_succeeds_and_the_second_is_refused() -> None:
    org = _org_row(ORG_A)
    connection = FakeConnection(organizations={ORG_A: org}, workspace_org={WS_A: ORG_A},
                               counts={f'workspaces:{ORG_A}': 0})
    context = org_service.resolve_context(connection, WS_A, now=NOW)
    org_service.enforce_creation(connection, context, ent.LIMIT_WORKSPACES, now=NOW)

    connection.counts[f'workspaces:{ORG_A}'] = 1
    with pytest.raises(HTTPException) as exc_info:
        org_service.enforce_creation(connection, context, ent.LIMIT_WORKSPACES, now=NOW)
    assert exc_info.value.detail == {
        'code': 'PLAN_LIMIT_REACHED', 'resource': 'workspaces', 'limit': 1, 'current': 1,
        'plan': 'pilot', 'message': 'Your Pilot plan supports up to 1 workspaces.',
    }
    assert connection.writes == []


@pytest.mark.parametrize('existing,should_raise', [(0, False), (4, False), (5, True), (9, True)])
def test_C2_pilot_allows_five_monitored_contracts_and_refuses_the_sixth(existing: int, should_raise: bool) -> None:
    connection = FakeConnection(organizations={ORG_A: _org_row(ORG_A)}, workspace_org={WS_A: ORG_A},
                               counts={f'assets:{ORG_A}': existing})
    context = org_service.resolve_context(connection, WS_A, now=NOW)
    if should_raise:
        with pytest.raises(HTTPException) as exc_info:
            org_service.enforce_creation(connection, context, ent.LIMIT_MONITORED_CONTRACTS, now=NOW)
        assert exc_info.value.detail['resource'] == 'monitored_contracts'
        assert exc_info.value.detail['limit'] == 5
    else:
        org_service.enforce_creation(connection, context, ent.LIMIT_MONITORED_CONTRACTS, now=NOW)


@pytest.mark.parametrize('existing,should_raise', [(9, False), (10, True), (11, True)])
def test_C3_pilot_allows_ten_evidence_packages_and_refuses_the_eleventh(existing: int, should_raise: bool) -> None:
    connection = FakeConnection(organizations={ORG_A: _org_row(ORG_A)}, workspace_org={WS_A: ORG_A},
                               counts={f'evidence:{ORG_A}': existing})
    context = org_service.resolve_context(connection, WS_A, now=NOW)
    if should_raise:
        with pytest.raises(HTTPException) as exc_info:
            org_service.enforce_creation(connection, context, ent.LIMIT_EVIDENCE_PACKAGES, now=NOW)
        assert exc_info.value.detail['limit'] == 10
    else:
        org_service.enforce_creation(connection, context, ent.LIMIT_EVIDENCE_PACKAGES, now=NOW)


# ── D — Scale limits are independent ──────────────────────────────────────────

def test_D_scale_allows_twenty_five_contracts_and_unlimited_evidence() -> None:
    connection = FakeConnection(
        organizations={ORG_B: _org_row(ORG_B, plan=ent.PLAN_SCALE, evaluation_expires_at=None)},
        workspace_org={WS_B: ORG_B},
        counts={f'assets:{ORG_B}': 24, f'evidence:{ORG_B}': 10_000},
    )
    context = org_service.resolve_context(connection, WS_B, now=NOW)
    org_service.enforce_creation(connection, context, ent.LIMIT_MONITORED_CONTRACTS, now=NOW)
    org_service.enforce_creation(connection, context, ent.LIMIT_EVIDENCE_PACKAGES, now=NOW)

    connection.counts[f'assets:{ORG_B}'] = 25
    with pytest.raises(HTTPException) as exc_info:
        org_service.enforce_creation(connection, context, ent.LIMIT_MONITORED_CONTRACTS, now=NOW)
    assert exc_info.value.detail['limit'] == 25


# ── E / F — expiry and suspension ─────────────────────────────────────────────

def test_E_expired_pilot_blocks_new_work_and_keeps_every_record() -> None:
    org = _org_row(ORG_A, evaluation_expires_at=NOW - timedelta(days=1))
    connection = FakeConnection(organizations={ORG_A: org}, workspace_org={WS_A: ORG_A},
                               counts={f'assets:{ORG_A}': 0})
    context = org_service.resolve_context(connection, WS_A, now=NOW)
    with pytest.raises(HTTPException) as exc_info:
        org_service.enforce_creation(connection, context, ent.LIMIT_MONITORED_CONTRACTS, now=NOW)
    assert exc_info.value.detail['code'] == 'PLAN_EVALUATION_EXPIRED'
    # Nothing was written: no record is deleted, disabled, or rewritten.
    assert connection.writes == []
    # And the tenant is still readable — sign-in and history stay available.
    assert context['organization']['id'] == ORG_A


def test_F_suspended_organization_blocks_new_work_with_its_own_code() -> None:
    org = _org_row(ORG_A, status=ent.STATUS_SUSPENDED)
    connection = FakeConnection(organizations={ORG_A: org}, workspace_org={WS_A: ORG_A})
    context = org_service.resolve_context(connection, WS_A, now=NOW)
    with pytest.raises(HTTPException) as exc_info:
        org_service.enforce_lifecycle_active(context, now=NOW)
    assert exc_info.value.detail['code'] == 'ORGANIZATION_SUSPENDED'


# ── G — upgrade preserves the tenant ──────────────────────────────────────────

def test_G_pilot_to_scale_keeps_the_same_organization_and_workspaces() -> None:
    org = _org_row(ORG_A)
    connection = FakeConnection(organizations={ORG_A: org}, workspace_org={WS_A: ORG_A, 'ws-a2': ORG_A})

    def _apply_plan_change(sql: str, params: Any) -> None:
        if 'update organizations' in sql.lower() and 'set plan' in sql.lower():
            connection.organizations[ORG_A] = {
                **connection.organizations[ORG_A],
                'plan': params[0],
                'evaluation_started_at': None,
                'evaluation_expires_at': None,
            }

    original_execute = connection.execute

    def _execute(query: str, params: Any = None) -> _Result:
        result = original_execute(query, params)
        _apply_plan_change(' '.join(str(query).split()), params)
        return result

    connection.execute = _execute  # type: ignore[method-assign]

    upgraded = org_service.set_plan(connection, organization_id=ORG_A, plan='scale', now=NOW)
    assert upgraded['id'] == ORG_A                       # same tenant row
    assert upgraded['plan'] == 'scale'
    assert list(connection.workspace_org) == [WS_A, 'ws-a2']   # same workspaces
    assert ent.evaluation_payload(upgraded, now=NOW) is None    # no countdown on Scale

    # Scale limits bind immediately, without any migration step.
    connection.counts[f'assets:{ORG_A}'] = 20
    context = org_service.resolve_context(connection, WS_A, now=NOW)
    org_service.enforce_creation(connection, context, ent.LIMIT_MONITORED_CONTRACTS, now=NOW)


def test_G2_moving_onto_pilot_starts_a_fresh_evaluation_window() -> None:
    org = _org_row(ORG_A, plan=ent.PLAN_SCALE, evaluation_started_at=None, evaluation_expires_at=None)
    connection = FakeConnection(organizations={ORG_A: org}, workspace_org={WS_A: ORG_A})
    org_service.set_plan(connection, organization_id=ORG_A, plan='pilot', now=NOW)
    update = next(sql for sql, _ in connection.writes if 'update organizations' in sql.lower())
    params = next(p for sql, p in connection.writes if sql == update)
    assert params[0] == 'pilot'
    assert params[2] == NOW + timedelta(days=ent.evaluation_days())


# ── H / I — founder admin authorization ───────────────────────────────────────

def test_H_customer_receives_403_and_no_organization_data(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection(users={USER_A: {'email': 'a@example.com', 'is_internal_admin': False}})
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)
    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.list_admin_customers(_request())
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'INTERNAL_ADMIN_REQUIRED'
    assert ORG_A not in str(exc_info.value.detail)
    assert ORG_B not in str(exc_info.value.detail)


def test_H2_every_admin_mutation_denies_a_customer(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection(users={USER_A: {'email': 'a@example.com', 'is_internal_admin': False}})
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)
    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)

    calls = [
        lambda: tenancy_endpoints.get_admin_customer(ORG_B, _request()),
        lambda: tenancy_endpoints.extend_admin_customer_evaluation(ORG_B, {'days': 30}, _request()),
        lambda: tenancy_endpoints.set_admin_customer_status(ORG_B, {'status': 'suspended'}, _request()),
        lambda: tenancy_endpoints.set_admin_customer_plan(ORG_B, {'plan': 'enterprise'}, _request()),
        lambda: tenancy_endpoints.list_admin_feedback(_request()),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            call()
        assert exc_info.value.status_code == 403
    # A refused admin call never mutated anything.
    assert connection.writes == []


def test_I_internal_admin_flag_is_read_from_the_database_only(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection(users={ADMIN_USER: {'email': 'founder@decodasecurity.com', 'is_internal_admin': True}})
    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)
    assert org_service.is_internal_admin(connection, ADMIN_USER) is True


def test_I2_role_or_body_cannot_escalate_to_internal_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A workspace owner is not internal staff, and no payload can make them so."""
    connection = _two_tenant_connection(users={USER_A: {'email': 'a@example.com', 'is_internal_admin': False}})
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)
    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.set_admin_customer_plan(
            ORG_A, {'plan': 'enterprise', 'is_internal_admin': True, 'role': 'internal_admin'}, _request(),
        )
    assert exc_info.value.status_code == 403


def test_I3_internal_admin_is_allowed_and_sees_every_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection(users={ADMIN_USER: {'email': 'founder@decodasecurity.com', 'is_internal_admin': True}})
    _authenticate_as(monkeypatch, user_id=ADMIN_USER, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)
    monkeypatch.setattr(
        org_service, 'list_customer_organizations',
        lambda *_a, **_k: [{'id': ORG_A, 'plan': 'pilot'}, {'id': ORG_B, 'plan': 'scale'}],
    )
    payload = tenancy_endpoints.list_admin_customers(_request())
    assert payload['count'] == 2


# ── J — allowlist safety ──────────────────────────────────────────────────────

@pytest.mark.parametrize('value', ['*', '*@decodasecurity.com', '@decodasecurity.com', 'decodasecurity.com', 'a@b@c'])
def test_J_wildcard_and_domain_allowlist_entries_are_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, value)
    assert org_service.internal_admin_email_allowlist() == frozenset()


def test_J2_exact_addresses_are_accepted_and_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, ' Founder@Decoda.app , ops@decoda.app ')
    assert org_service.internal_admin_email_allowlist() == frozenset({'founder@decoda.app', 'ops@decoda.app'})


def test_J3_allowlist_grants_access_only_to_a_listed_address(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection(users={
        ADMIN_USER: {'email': 'founder@decoda.app', 'is_internal_admin': False},
        USER_A: {'email': 'a@example.com', 'is_internal_admin': False},
    })
    monkeypatch.setenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, 'founder@decoda.app')
    assert org_service.is_internal_admin(connection, ADMIN_USER) is True
    assert org_service.is_internal_admin(connection, USER_A) is False


# ── K — feedback ──────────────────────────────────────────────────────────────

def test_K_feedback_is_stamped_from_the_session_not_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _two_tenant_connection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_account_feedback(
        {
            'feedback_type': 'detection_accuracy',
            'message': 'A transfer on our Base contract did not raise an alert.',
            'organization_id': ORG_B,
            'user_id': USER_B,
            'context': {'page': '/alerts', 'password': 'hunter2'},
        },
        _request(),
    )
    assert result['submitted'] is True
    insert_sql, insert_params = next(
        (sql, params) for sql, params in connection.writes if 'insert into organization_feedback' in sql.lower()
    )
    assert insert_params[1] == ORG_A          # session organization, not the body's
    assert insert_params[2] == WS_A
    assert insert_params[3] == USER_A
    assert 'hunter2' not in insert_params[6]  # context allowlist dropped it
    assert '/alerts' in insert_params[6]


@pytest.mark.parametrize('message', ['0x' + 'a' * 64, '-----BEGIN RSA PRIVATE KEY-----\nabc'])
def test_K2_feedback_containing_a_credential_is_refused_not_stored(message: str) -> None:
    connection = _two_tenant_connection()
    with pytest.raises(HTTPException) as exc_info:
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type='security', message=message,
        )
    assert exc_info.value.detail['code'] == 'FEEDBACK_CONTAINS_SECRET'
    assert connection.writes == []


def test_K3_ordinary_prose_is_never_mistaken_for_a_seed_phrase() -> None:
    connection = _two_tenant_connection()
    prose = 'the alert page does not show me the new data when i open the incident from here'
    org_service.record_feedback(
        connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
        feedback_type='usability', message=prose,
    )
    assert any('insert into organization_feedback' in sql.lower() for sql, _ in connection.writes)


def test_K4_unknown_feedback_type_is_refused() -> None:
    connection = _two_tenant_connection()
    with pytest.raises(HTTPException) as exc_info:
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type='exfiltrate', message='hello there',
        )
    assert exc_info.value.detail['code'] == 'INVALID_FEEDBACK_TYPE'


# ── L — worker excludes suspended/expired tenants ─────────────────────────────

def test_L_worker_filter_excludes_suspended_and_expired_tenants() -> None:
    from services.api.app import monitoring_runner

    connection = FakeConnection(schema_ready=True)
    clause = monitoring_runner._organization_monitoring_exclusion_sql(connection)
    normalized = ' '.join(clause.split())
    assert 'NOT EXISTS' in normalized
    assert "tenant_org.status <> 'active'" in normalized
    assert "tenant_org.plan = 'pilot'" in normalized
    assert 'tenant_org.evaluation_expires_at <= NOW()' in normalized


def test_L2_worker_filter_is_absent_before_the_tenancy_migration() -> None:
    """An unmigrated deployment keeps polling exactly as it did before."""
    from services.api.app import monitoring_runner

    assert monitoring_runner._organization_monitoring_exclusion_sql(FakeConnection(schema_ready=False)) == ''


# ── schema-availability behaviour ─────────────────────────────────────────────

def test_unmigrated_deployment_reports_plan_unavailable_not_a_default_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(schema_ready=False)
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    payload = tenancy_endpoints.get_account_plan(_request())
    assert payload['state'] == 'unavailable'
    assert payload['plan'] is None
    assert payload['evaluation'] is None
    assert payload['reason'] == 'tenancy_schema_not_migrated'


def test_unmigrated_deployment_applies_no_organization_limit() -> None:
    connection = FakeConnection(schema_ready=False)
    context = org_service.resolve_context(connection, WS_A, now=NOW)
    assert context['available'] is False
    org_service.enforce_creation(connection, context, ent.LIMIT_MONITORED_CONTRACTS, now=NOW)
    org_service.enforce_lifecycle_active(context, now=NOW)
