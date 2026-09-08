"""The founder account and the customer account are the same kind of account.

Decoda runs ONE product. There is no management application, no founder plan,
and no second dashboard. What separates the founder from a Pilot evaluator is a
single server-side privilege on the user row:

    PRIVILEGE     users.is_internal_admin   a property of the ACCOUNT
    SUBSCRIPTION  organizations.plan        a property of the ORGANIZATION

These are independent facts and this module exists to keep them that way. The
privilege opens exactly one door — the internal console under /admin/customers —
and it opens nothing else. It raises no limit, grants no entitlement, unlocks no
execution, and widens no query. The founder using the product is a customer of
whichever organization owns the workspace they are in, and is governed by that
organization's plan like anyone else.

What these tests pin down (Phase 8 of the account-model request):

   1  decoda.guard@gmail.com can be marked internal admin, out of band.
   2  An internal admin reaches /admin/customers.
   3  A normal customer does not, and receives no organization data.
   4  An internal admin inside a Pilot workspace still gets PILOT entitlements.
   5  ...still cannot exceed the 5-contract Pilot limit.
   6  ...still cannot exceed the Pilot evidence-package limit.
   7  ...still cannot execute against production under the Pilot lock.
   8  ...still reads only their own tenant through ordinary product endpoints.
   9  A normal signup defaults the user to is_internal_admin = false.
  10  A signup body cannot request is_internal_admin = true.
  11  A signup body cannot request a Scale or Enterprise plan.
  12  A new organization defaults to Pilot with a real evaluation window.
  13  An existing Scale organization stays Scale.
  14  An existing Enterprise organization stays Enterprise.
  15  Granting internal admin touches ONE column and destroys nothing.

Run:
    python -m pytest services/api/tests/test_internal_admin_account_model.py -q
"""

from __future__ import annotations

import importlib.util
import inspect
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app.domains.response_gate import config as rgc
from services.api.app.domains.tenancy import endpoints as tenancy_endpoints

#: A fixed instant for record timestamps that no rule reads.
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _clock_now() -> datetime:
    """The real clock.

    Lifecycle rules ("is this evaluation still running?") are evaluated against
    the wall clock by the code under test, so an evaluation window pinned to a
    fixed date would silently expire and turn every limit test into an
    expiry test. The window below is anchored to now for that reason.
    """
    return datetime.now(timezone.utc)


FOUNDER_EMAIL = 'decoda.guard@gmail.com'
CUSTOMER_EMAIL = 'evaluator@example.com'

FOUNDER_USER = 'user-founder-0001'
CUSTOMER_USER = 'user-customer-0001'

# Org A is the founder's own workspace. Org B is the separate Pilot customer.
ORG_A = 'aaaaaaaa-1111-1111-1111-111111111111'
ORG_B = 'bbbbbbbb-2222-2222-2222-222222222222'
WS_A = 'aaaaaaaa-0000-0000-0000-00000000000a'
WS_B = 'bbbbbbbb-0000-0000-0000-00000000000b'
INCIDENT_B = 'cccccccc-3333-3333-3333-333333333333'


# ── fakes ─────────────────────────────────────────────────────────────────────

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
    """Answers the tenancy queries and RECORDS every write.

    Recording matters as much as answering: several tests below assert that a
    refusal wrote nothing, and that granting internal admin wrote exactly one
    column on one row.
    """

    def __init__(
        self,
        *,
        organizations: dict[str, dict[str, Any]] | None = None,
        workspace_org: dict[str, str] | None = None,
        counts: dict[str, int] | None = None,
        users: dict[str, dict[str, Any]] | None = None,
        incidents: list[dict[str, Any]] | None = None,
        schema_ready: bool = True,
    ) -> None:
        self.organizations = organizations or {}
        self.workspace_org = workspace_org or {}
        self.counts = counts or {}
        self.users = users or {}
        self.incidents = incidents or []
        self.schema_ready = schema_ready
        self.writes: list[tuple[str, Any]] = []
        self.reads: list[tuple[str, Any]] = []
        self.committed = False

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()

        if lowered.startswith(('insert', 'update', 'delete', 'drop', 'truncate', 'alter')):
            self.writes.append((sql, params))
            return _Result()

        self.reads.append((sql, params))

        if 'information_schema.tables' in lowered:
            return _Result({'table_count': 2 if self.schema_ready else 0,
                            'link_count': 1 if self.schema_ready else 0})

        if 'from workspaces w join organizations o on o.id = w.organization_id' in lowered:
            org_id = self.workspace_org.get(str(params[0]))
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

        if 'from incidents i' in lowered:
            # The real query binds the SESSION workspace first and the requested
            # incident id last. Filtering on exactly those two is what makes this
            # a real isolation test rather than a stubbed answer.
            workspace_id = str(params[0])
            incident_id = params[10]
            return _Result([
                row for row in self.incidents
                if row['workspace_id'] == workspace_id
                and (incident_id is None or row['id'] == str(incident_id))
            ])

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


def _org_row(org_id: str, plan: str = ent.PLAN_PILOT, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        'id': org_id,
        'name': f'Org {org_id[:4]}',
        'slug': f'org-{org_id[:4]}',
        'plan': plan,
        'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': _clock_now() - timedelta(days=7) if plan == ent.PLAN_PILOT else None,
        'evaluation_expires_at': _clock_now() + timedelta(days=23) if plan == ent.PLAN_PILOT else None,
        'entitlement_overrides': {},
        'created_at': NOW - timedelta(days=7),
        'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _founder_and_customer(**overrides: Any) -> FakeConnection:
    """The exact deployment shape this request describes.

    The founder holds internal admin AND sits in a Pilot organization, because
    that is the combination every "does the privilege leak into the plan?" test
    below needs to be able to fail.
    """
    settings: dict[str, Any] = {
        'organizations': {ORG_A: _org_row(ORG_A), ORG_B: _org_row(ORG_B)},
        'workspace_org': {WS_A: ORG_A, WS_B: ORG_B},
        'users': {
            FOUNDER_USER: {'email': FOUNDER_EMAIL, 'is_internal_admin': True},
            CUSTOMER_USER: {'email': CUSTOMER_EMAIL, 'is_internal_admin': False},
        },
        'counts': {
            f'workspaces:{ORG_A}': 1, f'assets:{ORG_A}': 2, f'targets:{ORG_A}': 2, f'evidence:{ORG_A}': 3,
            f'workspaces:{ORG_B}': 1, f'assets:{ORG_B}': 2, f'targets:{ORG_B}': 2, f'evidence:{ORG_B}': 3,
        },
    }
    settings.update(overrides)
    return FakeConnection(**settings)


def _request(headers: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(headers=headers or {}, client=None)


def _authenticate_as(
    monkeypatch: pytest.MonkeyPatch, *, user_id: str, email: str, workspace_id: str,
) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': user_id, 'email': email},
    )
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


def _no_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """The database flag alone decides here.

    The bootstrap allowlist is a second, independent source of internal access.
    Clearing it keeps these tests honest: an environment variable left set in a
    developer's shell must not be what makes an authorization test pass.
    """
    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)


# ── 1 — the founder address can be marked internal admin, out of band ─────────

def _grant_script():
    """The real script, loaded so its own pg_connection can be replaced."""
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'grant_internal_admin.py'
    spec = importlib.util.spec_from_file_location('grant_internal_admin_under_test', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _GrantConnection:
    """Enough of a database for the grant script: a users table and a column."""

    def __init__(self, *, users: list[dict[str, Any]], column_exists: bool = True) -> None:
        self.users = users
        self.column_exists = column_exists
        self.writes: list[tuple[str, Any]] = []
        self.committed = False

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split()).lower()
        if sql.startswith(('insert', 'update', 'delete', 'drop', 'truncate', 'alter')):
            self.writes.append((' '.join(str(query).split()), params))
            return _Result()
        if 'information_schema.columns' in sql:
            return _Result({'exists': 1} if self.column_exists else None)
        if 'where lower(email) = %s' in sql:
            wanted = str(params[0])
            return _Result(next((u for u in self.users if u['email'].lower() == wanted), None))
        if 'where is_internal_admin is true' in sql:
            return _Result([u for u in self.users if u.get('is_internal_admin')])
        return _Result()

    def commit(self) -> None:
        self.committed = True

    def __enter__(self) -> '_GrantConnection':
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _run_grant(module: Any, connection: _GrantConnection, argv: list[str]) -> int:
    @contextmanager
    def _pg():
        yield connection

    module.pg_connection = _pg
    return module.main(argv)


def test_01_the_founder_address_can_be_granted_internal_admin() -> None:
    module = _grant_script()
    connection = _GrantConnection(
        users=[{'id': FOUNDER_USER, 'email': FOUNDER_EMAIL, 'is_internal_admin': False}],
    )

    assert _run_grant(module, connection, [FOUNDER_EMAIL]) == 0

    assert len(connection.writes) == 1
    statement, params = connection.writes[0]
    assert 'update users set is_internal_admin' in statement.lower()
    assert params == (True, FOUNDER_USER)
    assert connection.committed is True


def test_01b_the_grant_is_reversible_and_addresses_are_case_insensitive() -> None:
    module = _grant_script()
    connection = _GrantConnection(
        users=[{'id': FOUNDER_USER, 'email': FOUNDER_EMAIL, 'is_internal_admin': True}],
    )

    assert _run_grant(module, connection, ['DECODA.Guard@Gmail.com', '--revoke']) == 0
    assert connection.writes[0][1] == (False, FOUNDER_USER)


def test_01c_an_unknown_address_grants_nothing() -> None:
    module = _grant_script()
    connection = _GrantConnection(users=[])

    assert _run_grant(module, connection, ['stranger@example.com']) == 4
    assert connection.writes == []


def test_01d_the_grant_refuses_before_the_migration_rather_than_guessing() -> None:
    module = _grant_script()
    connection = _GrantConnection(
        users=[{'id': FOUNDER_USER, 'email': FOUNDER_EMAIL}], column_exists=False,
    )

    assert _run_grant(module, connection, [FOUNDER_EMAIL]) == 3
    assert connection.writes == []


# ── 2 / 3 — the one door the privilege opens ─────────────────────────────────

def test_02_internal_admin_reaches_the_customer_console(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer()
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    payload = tenancy_endpoints.list_admin_customers(_request())

    assert payload['count'] >= 0
    assert 'customers' in payload


def test_03_a_normal_customer_is_refused_and_reads_no_organization_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer()
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email=CUSTOMER_EMAIL, workspace_id=WS_B)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.list_admin_customers(_request())

    assert refusal.value.status_code == 403
    assert refusal.value.detail['code'] == org_service.CODE_INTERNAL_ADMIN_REQUIRED
    # Authorization ran BEFORE any organization read: nothing about another
    # tenant was loaded on the way to the refusal.
    assert not any('from organizations' in sql.lower() for sql, _ in connection.reads)


@pytest.mark.parametrize(
    'call',
    [
        lambda request: tenancy_endpoints.list_admin_customers(request),
        lambda request: tenancy_endpoints.get_admin_customer(ORG_A, request),
        lambda request: tenancy_endpoints.set_admin_customer_plan(ORG_A, {'plan': 'enterprise'}, request),
        lambda request: tenancy_endpoints.set_admin_customer_status(ORG_A, {'status': 'active'}, request),
        lambda request: tenancy_endpoints.extend_admin_customer_evaluation(ORG_A, {'days': 30}, request),
        lambda request: tenancy_endpoints.list_admin_feedback(request),
    ],
)
def test_03b_every_console_entry_point_refuses_the_customer(
    monkeypatch: pytest.MonkeyPatch, call: Any,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer()
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email=CUSTOMER_EMAIL, workspace_id=WS_B)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as refusal:
        call(_request())

    assert refusal.value.status_code == 403
    assert connection.writes == []


def test_03c_the_privilege_is_not_claimable_from_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A customer cannot ask to be internal staff. There is no such input."""
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer()
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email=CUSTOMER_EMAIL, workspace_id=WS_B)
    _use_connection(monkeypatch, connection)

    hostile = _request({
        'x-internal-admin': 'true',
        'x-decoda-role': 'internal_admin',
        'x-user-email': FOUNDER_EMAIL,
    })

    with pytest.raises(HTTPException) as refusal:
        tenancy_endpoints.list_admin_customers(hostile)

    assert refusal.value.status_code == 403


def test_03d_an_unreadable_privilege_is_a_refusal_not_a_grant() -> None:
    """Fail closed: a flag we could not read is not internal access."""

    class _Broken:
        def execute(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError('database unavailable')

    assert org_service.is_internal_admin(_Broken(), FOUNDER_USER) is False


# ── 4 — privilege does not change the plan the founder works under ───────────

def test_04_the_founder_in_a_pilot_workspace_gets_pilot_entitlements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer()
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    plan = tenancy_endpoints.get_account_plan(_request())

    assert plan['plan'] == ent.PLAN_PILOT
    assert plan['entitlements'][ent.LIMIT_WORKSPACES] == 1
    assert plan['entitlements'][ent.LIMIT_MONITORED_CONTRACTS] == 5
    assert plan['entitlements'][ent.LIMIT_EVIDENCE_PACKAGES] == 10
    assert plan['entitlements'][ent.FEATURE_AUTOMATIC_EXECUTION] is False
    assert plan['evaluation'] is not None


def test_04b_founder_and_customer_on_pilot_receive_identical_entitlements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_allowlist(monkeypatch)

    def _plan_for(user_id: str, email: str, workspace_id: str) -> dict[str, Any]:
        connection = _founder_and_customer()
        _authenticate_as(monkeypatch, user_id=user_id, email=email, workspace_id=workspace_id)
        _use_connection(monkeypatch, connection)
        return tenancy_endpoints.get_account_plan(_request())

    founder = _plan_for(FOUNDER_USER, FOUNDER_EMAIL, WS_A)
    customer = _plan_for(CUSTOMER_USER, CUSTOMER_EMAIL, WS_B)

    assert founder['entitlements'] == customer['entitlements']
    assert founder['lifecycle_state'] == customer['lifecycle_state']


def test_04c_the_entitlement_engine_has_no_way_to_know_who_is_asking() -> None:
    """Structural: the plan is computed from the ORGANIZATION and nothing else.

    A privilege can only leak into entitlements through a parameter that carries
    it. These functions take none, so there is no such leak to review for.
    """
    for function in (ent.get_entitlements, ent.plan_entitlements, ent.lifecycle_state):
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {'user', 'user_id', 'is_internal_admin', 'internal_admin', 'request'}

    signature = set(inspect.signature(org_service.enforce_creation).parameters)
    assert not signature & {'user', 'user_id', 'is_internal_admin', 'internal_admin', 'request'}


# ── 5 / 6 — the founder hits the same Pilot ceilings ─────────────────────────

@pytest.mark.parametrize('existing,refused', [(0, False), (4, False), (5, True), (9, True)])
def test_05_the_founder_cannot_exceed_the_five_contract_pilot_limit(
    monkeypatch: pytest.MonkeyPatch, existing: int, refused: bool,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer(counts={
        f'assets:{ORG_A}': existing, f'targets:{ORG_A}': 0,
        f'workspaces:{ORG_A}': 1, f'evidence:{ORG_A}': 0,
    })

    def _enforce() -> None:
        pilot.enforce_plan_creation_limit(connection, WS_A, ent.LIMIT_MONITORED_CONTRACTS)

    if not refused:
        _enforce()
        return

    with pytest.raises(HTTPException) as blocked:
        _enforce()
    assert blocked.value.status_code == 403
    assert blocked.value.detail['code'] == ent.CODE_PLAN_LIMIT_REACHED
    assert blocked.value.detail['limit'] == 5
    assert connection.writes == []


@pytest.mark.parametrize('existing,refused', [(9, False), (10, True), (25, True)])
def test_06_the_founder_cannot_exceed_the_pilot_evidence_limit(
    monkeypatch: pytest.MonkeyPatch, existing: int, refused: bool,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer(counts={
        f'evidence:{ORG_A}': existing, f'assets:{ORG_A}': 0,
        f'targets:{ORG_A}': 0, f'workspaces:{ORG_A}': 1,
    })

    def _enforce() -> None:
        pilot.enforce_plan_creation_limit(connection, WS_A, ent.LIMIT_EVIDENCE_PACKAGES)

    if not refused:
        _enforce()
        return

    with pytest.raises(HTTPException) as blocked:
        _enforce()
    assert blocked.value.status_code == 403
    assert blocked.value.detail['code'] == ent.CODE_PLAN_LIMIT_REACHED
    assert blocked.value.detail['limit'] == 10


def test_06b_the_refusal_is_identical_for_the_founder_and_for_a_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer(counts={
        f'assets:{ORG_A}': 5, f'targets:{ORG_A}': 0, f'workspaces:{ORG_A}': 1, f'evidence:{ORG_A}': 0,
        f'assets:{ORG_B}': 5, f'targets:{ORG_B}': 0, f'workspaces:{ORG_B}': 1, f'evidence:{ORG_B}': 0,
    })

    refusals = []
    for workspace_id in (WS_A, WS_B):
        with pytest.raises(HTTPException) as blocked:
            pilot.enforce_plan_creation_limit(connection, workspace_id, ent.LIMIT_MONITORED_CONTRACTS)
        refusals.append(blocked.value.detail)

    assert refusals[0] == refusals[1]


# ── 7 — the founder is still recommend-only inside a Pilot tenant ────────────

def test_07_the_founder_cannot_execute_against_production_under_pilot() -> None:
    connection = _founder_and_customer()

    lock = pilot.plan_execution_lock(connection, WS_A)

    assert lock['locked'] is True
    assert lock['reason'] == 'plan_recommend_only'
    assert lock['plan'] == ent.PLAN_PILOT


def test_07b_a_live_run_is_locked_on_the_gate_the_founder_sees() -> None:
    connection = _founder_and_customer()
    authorized = {
        'decision': rgc.GATE_AUTHORIZED,
        'decision_label': rgc.GATE_DECISION_LABELS[rgc.GATE_AUTHORIZED],
        'can_execute': True,
        'reason_codes': [rgc.EXECUTION_AUTHORIZED],
        'reasons': [{'code': rgc.EXECUTION_AUTHORIZED, 'label': rgc.reason_label(rgc.EXECUTION_AUTHORIZED)}],
    }

    gate = pilot._apply_plan_execution_lock(
        connection, authorized, action={'id': 'a1', 'mode': 'live'}, workspace_id=WS_A,
    )

    assert gate['plan_execution_locked'] is True
    assert gate['can_execute'] is False
    assert gate['decision'] == rgc.GATE_LOCKED
    assert rgc.PLAN_EXECUTION_NOT_ENTITLED in gate['reason_codes']


def test_07c_the_execution_lock_never_learns_who_the_operator_is() -> None:
    """Structural: the lock is resolved from the workspace's tenant alone."""
    parameters = set(inspect.signature(pilot.plan_execution_lock).parameters)
    assert not parameters & {'user', 'user_id', 'is_internal_admin', 'internal_admin', 'request'}


# ── 8 — ordinary product endpoints stay tenant-scoped for the founder ────────

def _incident_row(incident_id: str, workspace_id: str) -> dict[str, Any]:
    return {
        'id': incident_id,
        'workspace_id': workspace_id,
        'event_type': 'anomalous_transfer',
        'title': 'Customer B incident',
        'severity': 'high',
        'status': 'open',
        'workflow_status': 'open',
        'target_id': None,
        'source_alert_id': None,
        'linked_alert_ids': [],
        'owner': None,
        'owner_user_id': None,
        'assignee_user_id': None,
        'summary': 'Belongs to the other tenant.',
        'resolution_note': None,
        'resolution_notes': None,
        'timeline': [],
        'created_at': NOW,
        'updated_at': NOW,
        'linked_detection_id': None,
        'linked_evidence_count': 0,
        'last_evidence_at': None,
        'evidence_source': None,
        'tx_hash': None,
        'block_number': None,
        'detector_kind': None,
        'evidence_origin': None,
        'linked_action_id': None,
        'response_action_mode': None,
    }


def test_08_the_founder_scoped_to_org_a_cannot_read_org_b_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-tenant case, through the real product endpoint.

    The founder holds internal admin and is working inside Org A. Incident B
    belongs to Org B. /incidents/{id} is an ordinary customer endpoint, so it
    answers 404 — the privilege buys nothing here.
    """
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer(incidents=[_incident_row(INCIDENT_B, WS_B)])
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as missing:
        pilot.get_incident(INCIDENT_B, _request())

    assert missing.value.status_code == 404
    # And the query really did bind Org A's workspace, so the 404 is isolation
    # rather than an empty fixture.
    incident_reads = [params for sql, params in connection.reads if 'from incidents i' in sql.lower()]
    assert incident_reads and all(params[0] == WS_A for params in incident_reads)


def test_08b_the_same_endpoint_serves_the_owning_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 404 above is scoping, not a broken fixture: Org B reads it fine."""
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer(incidents=[_incident_row(INCIDENT_B, WS_B)])
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email=CUSTOMER_EMAIL, workspace_id=WS_B)
    _use_connection(monkeypatch, connection)

    incident = pilot.get_incident(INCIDENT_B, _request())

    assert incident['incident']['id'] == INCIDENT_B


def test_08c_the_founders_own_plan_endpoint_reports_only_their_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer()
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    plan = tenancy_endpoints.get_account_plan(
        _request({'x-organization-id': ORG_B, 'x-workspace-id': WS_B}),
    )

    assert plan['organization']['id'] == ORG_A


# ── 9 / 10 / 11 / 12 — what a self-serve signup may and may not ask for ──────

PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'


@pytest.fixture(scope='module')
def signup_pilot():
    """A private copy of pilot.py, so signup monkeypatching is isolated."""
    spec = importlib.util.spec_from_file_location('pilot_account_model', PILOT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SignupConnection:
    """A new deployment: no users, no workspaces, tenancy schema migrated."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, Any]] = []
        self.organizations: dict[str, dict[str, Any]] = {}
        self.committed = False

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()

        if lowered.startswith('insert into organizations'):
            self.writes.append((sql, params))
            (org_id, name, slug, plan, status_value, started_at, expires_at) = params
            self.organizations[str(org_id)] = {
                'id': org_id, 'name': name, 'slug': slug, 'plan': plan, 'status': status_value,
                'evaluation_started_at': started_at, 'evaluation_expires_at': expires_at,
                'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
            }
            return _Result()

        if lowered.startswith(('insert', 'update', 'delete', 'drop', 'truncate', 'alter')):
            self.writes.append((sql, params))
            return _Result()

        if 'information_schema.tables' in lowered:
            return _Result({'table_count': 2, 'link_count': 1})
        if 'from organizations where id = %s' in lowered:
            row = self.organizations.get(str(params[0]))
            return _Result(dict(row) if row else None)
        return _Result(None)

    def commit(self) -> None:
        self.committed = True


def _signup(module: Any, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> _SignupConnection:
    connection = _SignupConnection()

    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(module, 'pg_connection', _pg)
    monkeypatch.setattr(module, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(module, 'hash_password', lambda _password: 'hashed')
    monkeypatch.setattr(module, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(module, '_create_user_token', lambda *_a, **_k: 'verify-token')
    monkeypatch.setattr(
        module, 'build_user_response',
        lambda _connection, user_id: {'id': user_id, 'is_internal_admin': False, 'current_workspace': None},
    )
    module.signup_user(payload, SimpleNamespace(headers={}, client=None))
    return connection


def _user_insert(connection: _SignupConnection) -> tuple[str, Any]:
    return next((sql, params) for sql, params in connection.writes if sql.lower().startswith('insert into users'))


def _organization_insert(connection: _SignupConnection) -> tuple[str, Any]:
    return next(
        (sql, params) for sql, params in connection.writes if sql.lower().startswith('insert into organizations')
    )


def test_09_a_normal_signup_never_writes_the_internal_admin_column(
    signup_pilot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The column is left to its DEFAULT FALSE, which is the strongest form of
    "customers are not internal staff": there is no value to get wrong."""
    connection = _signup(signup_pilot, monkeypatch, {
        'email': CUSTOMER_EMAIL, 'password': 'StrongPass1234',
        'full_name': 'Pilot Evaluator', 'workspace_name': 'Evaluator Ops',
    })

    statement, _params = _user_insert(connection)
    assert 'is_internal_admin' not in statement.lower()
    assert not any('is_internal_admin' in sql.lower() for sql, _ in connection.writes)


@pytest.mark.parametrize('claim', [
    {'is_internal_admin': True},
    {'internal_admin': True},
    {'role': 'internal_admin'},
    {'is_internal_admin': 'true', 'internal_role': 'founder'},
])
def test_10_a_signup_body_cannot_ask_for_internal_admin(
    signup_pilot: Any, monkeypatch: pytest.MonkeyPatch, claim: dict[str, Any],
) -> None:
    connection = _signup(signup_pilot, monkeypatch, {
        'email': CUSTOMER_EMAIL, 'password': 'StrongPass1234',
        'full_name': 'Pilot Evaluator', 'workspace_name': 'Evaluator Ops',
        **claim,
    })

    assert not any('is_internal_admin' in sql.lower() for sql, _ in connection.writes)
    _statement, params = _organization_insert(connection)
    assert params[3] == ent.PLAN_PILOT


@pytest.mark.parametrize('claim', [
    {'plan': 'scale'},
    {'plan': 'enterprise'},
    {'organization': {'plan': 'enterprise'}},
    {'plan': 'enterprise', 'entitlement_overrides': {'max_monitored_contracts': 9999}},
    {'entitlements': {'automatic_execution': True}},
])
def test_11_a_signup_body_cannot_ask_for_a_paid_plan_or_an_override(
    signup_pilot: Any, monkeypatch: pytest.MonkeyPatch, claim: dict[str, Any],
) -> None:
    connection = _signup(signup_pilot, monkeypatch, {
        'email': CUSTOMER_EMAIL, 'password': 'StrongPass1234',
        'full_name': 'Pilot Evaluator', 'workspace_name': 'Evaluator Ops',
        **claim,
    })

    statement, params = _organization_insert(connection)
    assert params[3] == ent.PLAN_PILOT
    assert params[4] == ent.STATUS_ACTIVE
    # Overrides are written as a literal empty object by the INSERT itself, so a
    # body-supplied override has nowhere to land.
    assert "'{}'::jsonb" in statement
    organization = next(iter(connection.organizations.values()))
    assert ent.get_entitlements(organization)[ent.FEATURE_AUTOMATIC_EXECUTION] is False
    assert ent.get_entitlements(organization)[ent.LIMIT_MONITORED_CONTRACTS] == 5


def test_12_a_new_organization_defaults_to_pilot_with_a_real_evaluation_window(
    signup_pilot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _signup(signup_pilot, monkeypatch, {
        'email': CUSTOMER_EMAIL, 'password': 'StrongPass1234',
        'full_name': 'Pilot Evaluator', 'workspace_name': 'Evaluator Ops',
    })

    _statement, params = _organization_insert(connection)
    _org_id, _name, _slug, plan, status_value, started_at, expires_at = params

    assert plan == ent.PLAN_PILOT
    assert status_value == ent.STATUS_ACTIVE
    assert started_at is not None and expires_at is not None
    # The window is the ONE configured duration, not a hard-coded literal.
    assert (expires_at - started_at).days == ent.evaluation_days()


def test_12b_the_pilot_evaluation_length_follows_its_configured_value(
    signup_pilot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ent.EVALUATION_DAYS_ENV, '14')
    connection = _signup(signup_pilot, monkeypatch, {
        'email': CUSTOMER_EMAIL, 'password': 'StrongPass1234',
        'full_name': 'Pilot Evaluator', 'workspace_name': 'Evaluator Ops',
    })

    _statement, params = _organization_insert(connection)
    assert (params[6] - params[5]).days == 14


# ── 13 / 14 — paying tenants are not re-evaluated ───────────────────────────

@pytest.mark.parametrize('plan,contracts,evidence', [
    (ent.PLAN_SCALE, 25, None),
    (ent.PLAN_ENTERPRISE, None, None),
])
def test_13_an_existing_paid_organization_keeps_its_plan_and_limits(
    monkeypatch: pytest.MonkeyPatch, plan: str, contracts: int | None, evidence: int | None,
) -> None:
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer(organizations={
        ORG_A: _org_row(ORG_A), ORG_B: _org_row(ORG_B, plan=plan),
    })
    _authenticate_as(monkeypatch, user_id=CUSTOMER_USER, email=CUSTOMER_EMAIL, workspace_id=WS_B)
    _use_connection(monkeypatch, connection)

    payload = tenancy_endpoints.get_account_plan(_request())

    assert payload['plan'] == plan
    assert payload['entitlements'][ent.LIMIT_MONITORED_CONTRACTS] == contracts
    assert payload['entitlements'][ent.LIMIT_EVIDENCE_PACKAGES] == evidence
    # A paid tenant is never shown an evaluation countdown it does not have.
    assert payload['evaluation'] is None
    assert connection.writes == []


@pytest.mark.parametrize('plan', [ent.PLAN_SCALE, ent.PLAN_ENTERPRISE])
def test_14_a_paid_tenant_is_untouched_by_a_founder_signing_in(
    monkeypatch: pytest.MonkeyPatch, plan: str,
) -> None:
    """Reading the founder's own plan writes nothing to anyone else's tenant."""
    _no_allowlist(monkeypatch)
    connection = _founder_and_customer(organizations={
        ORG_A: _org_row(ORG_A), ORG_B: _org_row(ORG_B, plan=plan),
    })
    _authenticate_as(monkeypatch, user_id=FOUNDER_USER, email=FOUNDER_EMAIL, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.get_account_plan(_request())

    assert connection.writes == []
    assert connection.organizations[ORG_B]['plan'] == plan


# ── 15 — the grant is not a migration, a reset, or a deletion ───────────────

def test_15_granting_internal_admin_changes_one_column_on_one_row() -> None:
    module = _grant_script()
    connection = _GrantConnection(users=[
        {'id': FOUNDER_USER, 'email': FOUNDER_EMAIL, 'is_internal_admin': False},
        {'id': CUSTOMER_USER, 'email': CUSTOMER_EMAIL, 'is_internal_admin': False},
    ])

    _run_grant(module, connection, [FOUNDER_EMAIL])

    assert len(connection.writes) == 1
    statement, params = connection.writes[0]
    lowered = statement.lower()
    for destructive in ('delete', 'drop', 'truncate', 'insert into', 'alter table'):
        assert destructive not in lowered
    for untouched in ('workspaces', 'organizations', 'assets', 'incidents', 'evidence', 'current_workspace_id'):
        assert untouched not in lowered
    assert params[1] == FOUNDER_USER


def test_15b_the_grant_never_creates_a_founder_plan_or_a_founder_tenant() -> None:
    source = (Path(__file__).resolve().parents[1] / 'scripts' / 'grant_internal_admin.py').read_text(encoding='utf-8')
    lowered = source.lower()
    assert 'insert into organizations' not in lowered
    assert 'entitlement_overrides' not in lowered
    assert "plan = 'enterprise'" not in lowered
    # And the address is never baked into the code: it is an argument.
    assert 'decoda.guard' not in lowered


# ── the user payload reports the privilege, and only reports it ─────────────

def test_the_user_payload_states_internal_admin_from_the_server_side_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_allowlist(monkeypatch)
    seen: dict[str, bool] = {}

    def _fake(_connection: Any, user_id: str) -> bool:
        return seen.get(user_id, False)

    seen[FOUNDER_USER] = True
    monkeypatch.setattr(org_service, 'is_internal_admin', _fake)

    class _UserConnection:
        def __init__(self, user_id: str, email: str) -> None:
            self.user_id = user_id
            self.email = email

        def execute(self, query: str, params: Any = None) -> _Result:
            lowered = ' '.join(str(query).split()).lower()
            if 'from users where id' in lowered:
                return _Result({
                    'id': self.user_id, 'email': self.email, 'full_name': 'Name',
                    'current_workspace_id': None, 'created_at': NOW, 'updated_at': NOW,
                    'last_sign_in_at': None, 'email_verified_at': NOW, 'mfa_enabled_at': None,
                })
            return _Result([])

    founder = pilot.build_user_response(_UserConnection(FOUNDER_USER, FOUNDER_EMAIL), FOUNDER_USER)
    customer = pilot.build_user_response(_UserConnection(CUSTOMER_USER, CUSTOMER_EMAIL), CUSTOMER_USER)

    assert founder['is_internal_admin'] is True
    assert customer['is_internal_admin'] is False
    # The payload states a PRIVILEGE. It never states a plan, because the plan
    # belongs to the organization and is reported by GET /account/plan.
    assert 'plan' not in founder
    assert 'entitlements' not in founder
