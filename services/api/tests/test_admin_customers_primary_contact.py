"""The founder console's PRIMARY CONTACT column — who it names, and who may read it.

/admin/customers lists organizations whose names ("Rabbit", "a", "decoda") do not
say which human is behind them. The listing therefore resolves ONE contact per
organization from real membership rows: owner, else admin, else the earliest
member of any role.

What these tests pin down

  A  The selection itself, executed as SQL. The ordering lives in the listing
     query, so these cases run the REAL query string from
     ``organizations.list_customer_organizations`` against an in-memory SQL
     engine rather than against a fake that re-implements the ordering — a fake
     would only prove that the fake agrees with itself.
  B  Determinism. Two equally-eligible members must resolve to the same address
     on every refresh; a column that named a different person each time it was
     read would be worse than no column.
  C  Absence. An organization with no resolvable member reports None, which the
     console renders as an em dash. No guess from the organization name, slug,
     or any unrelated address.
  D  One query for the whole page — the contact is a correlated subquery inside
     the existing listing query, not a per-organization round trip.
  E  Exposure. Internal staff receive the address; a customer receives 403 and
     no organization data at all; and no credential, session, or token field is
     part of the payload.

Run:
    python -m pytest services/api/tests/test_admin_customers_primary_contact.py -q
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app.domains.tenancy import endpoints as tenancy_endpoints

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

ORG_A = 'aaaaaaaa-1111-1111-1111-111111111111'
ORG_B = 'bbbbbbbb-2222-2222-2222-222222222222'
ORG_EMPTY = 'cccccccc-3333-3333-3333-333333333333'

# UUID-shaped so that the user_id tie-break sorts identically here and in
# PostgreSQL, where the column is a real UUID.
USER_OWNER = '11111111-0000-0000-0000-000000000001'
USER_ADMIN = '22222222-0000-0000-0000-000000000002'
USER_ADMIN_2 = '33333333-0000-0000-0000-000000000003'
USER_ANALYST = '44444444-0000-0000-0000-000000000004'
USER_VIEWER = '55555555-0000-0000-0000-000000000005'
USER_CUSTOMER = '66666666-0000-0000-0000-000000000006'

PASSWORD_HASH = 'argon2id$v=19$m=65536,t=3,p=4$NOT-A-REAL-HASH'

_SCHEMA = '''
    CREATE TABLE organizations (
        id TEXT PRIMARY KEY, name TEXT, slug TEXT, plan TEXT, status TEXT,
        evaluation_started_at TEXT, evaluation_expires_at TEXT,
        entitlement_overrides TEXT, created_at TEXT, updated_at TEXT
    );
    CREATE TABLE users (
        id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, full_name TEXT,
        created_at TEXT, last_sign_in_at TEXT, is_internal_admin INTEGER DEFAULT 0
    );
    CREATE TABLE organization_memberships (
        id TEXT PRIMARY KEY, organization_id TEXT, user_id TEXT, role TEXT, created_at TEXT
    );
    CREATE TABLE workspaces (id TEXT PRIMARY KEY, organization_id TEXT);
    CREATE TABLE assets (id TEXT PRIMARY KEY, workspace_id TEXT, deleted_at TEXT);
    CREATE TABLE targets (id TEXT PRIMARY KEY, workspace_id TEXT, deleted_at TEXT);
    CREATE TABLE export_jobs (id TEXT PRIMARY KEY, workspace_id TEXT, export_type TEXT);
    CREATE TABLE audit_logs (id TEXT PRIMARY KEY, workspace_id TEXT, created_at TEXT);
    CREATE TABLE organization_feedback (id TEXT PRIMARY KEY, organization_id TEXT);
'''


class _SchemaProbeResult:
    """Migration 0150's information_schema probe, which SQLite cannot answer."""

    def fetchone(self) -> dict[str, Any]:
        return {'table_count': len(org_service.TENANCY_TABLES), 'link_count': 1}

    def fetchall(self) -> list[Any]:  # pragma: no cover - probe is fetchone only
        return [self.fetchone()]


class SqliteConnection:
    """A psycopg-shaped connection backed by in-memory SQLite.

    Only two things are translated: the ``%s`` placeholder style, and the one
    PostgreSQL catalog probe above. The SQL under test is executed verbatim, and
    every statement is recorded so a test can prove the listing is one query.
    """

    def __init__(self) -> None:
        self._db = sqlite3.connect(':memory:')
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self.queries: list[str] = []

    def execute(self, query: str, params: Any = None) -> Any:
        sql = ' '.join(str(query).split())
        self.queries.append(sql)
        if 'information_schema' in sql.lower():
            return _SchemaProbeResult()
        return self._db.execute(sql.replace('%s', '?'), tuple(params or ()))

    def commit(self) -> None:
        self._db.commit()

    # ── seeding ──────────────────────────────────────────────────────────────
    def add_organization(self, organization_id: str, name: str) -> None:
        self._db.execute(
            'INSERT INTO organizations (id, name, slug, plan, status, evaluation_started_at, '
            'evaluation_expires_at, entitlement_overrides, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (organization_id, name, name.lower(), 'pilot', 'active', NOW.isoformat(),
             (NOW + timedelta(days=23)).isoformat(), '{}', NOW.isoformat(), NOW.isoformat()),
        )

    def add_member(
        self,
        organization_id: str,
        user_id: str,
        email: str,
        role: str,
        *,
        joined: datetime,
        is_internal_admin: bool = False,
    ) -> None:
        self._db.execute(
            'INSERT OR IGNORE INTO users (id, email, password_hash, full_name, created_at, '
            'is_internal_admin) VALUES (?, ?, ?, ?, ?, ?)',
            (user_id, email, PASSWORD_HASH, 'Test Person', joined.isoformat(), int(is_internal_admin)),
        )
        self._db.execute(
            'INSERT INTO organization_memberships (id, organization_id, user_id, role, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (f'{organization_id}:{user_id}', organization_id, user_id, role, joined.isoformat()),
        )


def _contact(connection: SqliteConnection, organization_id: str) -> str | None:
    rows = org_service.list_customer_organizations(connection, now=NOW)
    row = next(item for item in rows if item['id'] == organization_id)
    return row['primary_contact_email']


# ── A — owner, then admin, then earliest member ──────────────────────────────

def test_A_the_owner_is_the_primary_contact() -> None:
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'Rabbit')
    # The owner joined LAST, so only the role rank can be what selects them.
    connection.add_member(ORG_A, USER_ANALYST, 'analyst@example.com', 'analyst', joined=NOW - timedelta(days=9))
    connection.add_member(ORG_A, USER_ADMIN, 'admin@example.com', 'admin', joined=NOW - timedelta(days=5))
    connection.add_member(ORG_A, USER_OWNER, 'owner@example.com', 'owner', joined=NOW - timedelta(days=1))

    assert _contact(connection, ORG_A) == 'owner@example.com'


def test_A2_an_admin_is_used_when_the_organization_has_no_owner() -> None:
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'Rabbit')
    connection.add_member(ORG_A, USER_VIEWER, 'viewer@example.com', 'viewer', joined=NOW - timedelta(days=30))
    connection.add_member(ORG_A, USER_ADMIN, 'admin@example.com', 'admin', joined=NOW - timedelta(days=2))

    assert _contact(connection, ORG_A) == 'admin@example.com'


def test_A3_the_earliest_member_is_used_when_there_is_no_owner_or_admin() -> None:
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'Rabbit')
    connection.add_member(ORG_A, USER_VIEWER, 'viewer@example.com', 'viewer', joined=NOW - timedelta(days=4))
    connection.add_member(ORG_A, USER_ANALYST, 'analyst@example.com', 'analyst', joined=NOW - timedelta(days=12))

    assert _contact(connection, ORG_A) == 'analyst@example.com'


def test_A4_analyst_and_viewer_rank_equal_so_join_order_alone_decides() -> None:
    """Neither non-privileged role outranks the other: the earlier join wins."""
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'Rabbit')
    connection.add_member(ORG_A, USER_ANALYST, 'analyst@example.com', 'analyst', joined=NOW - timedelta(days=3))
    connection.add_member(ORG_A, USER_VIEWER, 'viewer@example.com', 'viewer', joined=NOW - timedelta(days=8))

    assert _contact(connection, ORG_A) == 'viewer@example.com'


# ── B — determinism ──────────────────────────────────────────────────────────

def test_B_two_admins_resolve_to_the_one_who_joined_first() -> None:
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'Rabbit')
    # Inserted newest-first so row order cannot be what produces the answer.
    connection.add_member(ORG_A, USER_ADMIN_2, 'second-admin@example.com', 'admin', joined=NOW - timedelta(days=1))
    connection.add_member(ORG_A, USER_ADMIN, 'first-admin@example.com', 'admin', joined=NOW - timedelta(days=6))

    assert _contact(connection, ORG_A) == 'first-admin@example.com'


def test_B2_an_exact_tie_is_broken_by_user_id_and_never_changes() -> None:
    """Two owners enrolled in the same transaction share a created_at."""
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'Rabbit')
    same_moment = NOW - timedelta(days=2)
    connection.add_member(ORG_A, USER_ADMIN_2, 'z-owner@example.com', 'owner', joined=same_moment)
    connection.add_member(ORG_A, USER_OWNER, 'a-owner@example.com', 'owner', joined=same_moment)

    # USER_OWNER sorts lowest, and repeated reads must agree.
    assert [_contact(connection, ORG_A) for _ in range(3)] == ['a-owner@example.com'] * 3


def test_B3_the_role_ranking_is_generated_from_one_declared_priority() -> None:
    """The SQL rank is derived from PRIMARY_CONTACT_ROLE_PRIORITY, not retyped."""
    assert org_service.PRIMARY_CONTACT_ROLE_PRIORITY == ('owner', 'admin')
    sql = ' '.join(org_service._PRIMARY_CONTACT_EMAIL_SQL.split())
    assert "WHEN 'owner' THEN 0" in sql
    assert "WHEN 'admin' THEN 1" in sql
    assert 'ELSE 2 END' in sql
    assert 'm.created_at ASC, m.user_id ASC' in sql


# ── C — absence is reported as absence ───────────────────────────────────────

def test_C_an_organization_with_no_members_reports_no_contact() -> None:
    connection = SqliteConnection()
    connection.add_organization(ORG_EMPTY, 'a')

    row = next(
        item for item in org_service.list_customer_organizations(connection, now=NOW)
        if item['id'] == ORG_EMPTY
    )
    assert row['primary_contact_email'] is None
    assert row['members'] == 0
    # Not guessed from the organization's own name or slug.
    assert 'a' != row['primary_contact_email']


def test_C2_a_membership_row_for_another_organization_is_not_borrowed() -> None:
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'Rabbit')
    connection.add_organization(ORG_EMPTY, 'Datto')
    connection.add_member(ORG_A, USER_OWNER, 'owner@example.com', 'owner', joined=NOW - timedelta(days=1))

    assert _contact(connection, ORG_A) == 'owner@example.com'
    assert _contact(connection, ORG_EMPTY) is None


@pytest.mark.parametrize('stored,expected', [
    ('owner@example.com', 'owner@example.com'),
    ('  spaced@example.com  ', 'spaced@example.com'),
    ('   ', None),
    ('', None),
    (None, None),
])
def test_C3_a_blank_address_is_reported_as_absent(stored: Any, expected: str | None) -> None:
    assert org_service._email_or_none(stored) == expected


# ── D — one query for the whole page ─────────────────────────────────────────

def test_D_the_contact_costs_no_extra_query_per_organization() -> None:
    connection = SqliteConnection()
    for index, (organization_id, name) in enumerate(
        [(ORG_A, 'Rabbit'), (ORG_B, 'decoda'), (ORG_EMPTY, 'Datto')]
    ):
        connection.add_organization(organization_id, name)
        connection.add_member(
            organization_id, f'{index}0000000-0000-0000-0000-00000000000{index}',
            f'owner{index}@example.com', 'owner', joined=NOW - timedelta(days=index + 1),
        )
    connection.queries.clear()

    rows = org_service.list_customer_organizations(connection, now=NOW)

    assert len(rows) == 3
    assert all(row['primary_contact_email'] for row in rows)
    # Three organizations, one statement: the contact rides the listing query.
    assert len(connection.queries) == 1


# ── E — who may read it, and what is never in it ─────────────────────────────

def _request() -> SimpleNamespace:
    return SimpleNamespace(headers={}, client=None)


def _serve(monkeypatch: pytest.MonkeyPatch, connection: SqliteConnection, *, user_id: str) -> None:
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.delenv(org_service.INTERNAL_ADMIN_EMAILS_ENV, raising=False)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': user_id},
    )


def _founder_console(monkeypatch: pytest.MonkeyPatch) -> SqliteConnection:
    """One tenant whose owner is the founder account, plus a customer tenant."""
    connection = SqliteConnection()
    connection.add_organization(ORG_A, 'decoda')
    connection.add_organization(ORG_B, 'Rabbit')
    connection.add_member(
        ORG_A, USER_OWNER, 'decoda.guard@gmail.com', 'owner',
        joined=NOW - timedelta(days=40), is_internal_admin=True,
    )
    connection.add_member(
        ORG_B, USER_CUSTOMER, 'security@customer.com', 'owner', joined=NOW - timedelta(days=3),
    )
    return connection


def test_E_internal_admin_receives_the_primary_contact_of_every_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _founder_console(monkeypatch)
    _serve(monkeypatch, connection, user_id=USER_OWNER)

    payload = tenancy_endpoints.list_admin_customers(_request())

    contacts = {row['id']: row['primary_contact_email'] for row in payload['customers']}
    assert payload['count'] == 2
    # The founder's own organization reports the address held in membership —
    # matched from the data, never from an address written into the code.
    assert contacts[ORG_A] == 'decoda.guard@gmail.com'
    assert contacts[ORG_B] == 'security@customer.com'


def test_E2_a_customer_cannot_call_the_admin_listing_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _founder_console(monkeypatch)
    _serve(monkeypatch, connection, user_id=USER_CUSTOMER)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.list_admin_customers(_request())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'INTERNAL_ADMIN_REQUIRED'
    # The refusal carries no other tenant's contact, name, or id.
    detail = str(exc_info.value.detail)
    assert 'decoda.guard@gmail.com' not in detail
    assert ORG_A not in detail


def test_E3_no_credential_session_or_token_field_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _founder_console(monkeypatch)
    _serve(monkeypatch, connection, user_id=USER_OWNER)

    payload = tenancy_endpoints.list_admin_customers(_request())

    serialized = str(payload)
    assert PASSWORD_HASH not in serialized
    for row in payload['customers']:
        for key in row:
            assert not any(
                marker in key.lower()
                for marker in ('password', 'hash', 'token', 'session', 'secret', 'credential')
            ), key
        # The address is the only personal field, and no user id rides along.
        assert 'primary_contact_email' in row
        assert 'primary_contact_user_id' not in row


def test_E4_the_selection_reads_membership_and_no_authentication_column() -> None:
    sql = ' '.join(org_service._PRIMARY_CONTACT_EMAIL_SQL.split()).lower()
    assert 'organization_memberships' in sql
    assert 'u.email' in sql
    for column in ('password', 'session', 'token', 'mfa', 'is_internal_admin'):
        assert column not in sql
