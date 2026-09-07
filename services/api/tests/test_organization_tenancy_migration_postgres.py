"""Migration 0150 against REAL PostgreSQL, with pre-existing data already in it.

Adding a tenant column to a live product is where a migration destroys data. The
in-process fakes used elsewhere cannot catch that, because the failure modes are
all database-level: a NOT NULL added before the backfill, a join that merges two
customers into one tenant, a partial run that mints a duplicate organization on
retry.

What is pinned here:

  1  Every pre-existing row survives. No table loses a row, and no workspace,
     asset, evidence package, or membership is rewritten.
  2  Each pre-existing workspace becomes its OWN organization. Two workspaces are
     never merged into a shared tenant, because a merge would hand one customer's
     records to another — exactly the regression the tenancy layer exists to stop.
  3  Plan is derived from the workspace's own active subscription, so a paying
     workspace does not silently become an evaluation.
  4  A grandfathered workspace gets NO retroactive evaluation deadline, and its
     limits are raised to at least the footprint it already has — so a customer
     who was over the Pilot numbers before the migration is not locked out by it.
  5  Organization membership mirrors workspace membership exactly. Nobody gains
     access to anything they could not already reach.
  6  The backfill is idempotent: re-running it mints no second tenant, so a retry
     after a partial failure is safe.
  7  No account is granted internal admin by the migration.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_organization_tenancy_migration_postgres.py

Skipped entirely when that DSN is absent, so the default suite stays hermetic.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import uuid

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
               'psql on PATH to run the tenancy migration harness',
    ),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
_TENANCY_MIGRATION = '0150_organization_tenancy_foundation.sql'

_COUNTED_TABLES = (
    'users', 'workspaces', 'workspace_members', 'assets', 'export_jobs', 'billing_subscriptions',
)


def _psql(*args: str) -> None:
    proc = subprocess.run(
        [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', *args],
        capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, f'psql failed:\n{proc.stdout}\n{proc.stderr}'


def _reset_schema() -> None:
    _psql('-c', 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')


def _migrations(*, include_tenancy: bool) -> list[pathlib.Path]:
    paths = sorted(_MIGRATIONS.glob('*.sql'))
    if include_tenancy:
        return paths
    return [path for path in paths if path.name != _TENANCY_MIGRATION]


def _apply(paths: list[pathlib.Path]) -> None:
    for path in paths:
        _psql('-f', str(path))


def _seed_pre_existing_data(connection) -> dict[str, str]:
    """Three workspaces owned by two unrelated customers, one of them paying.

    The free workspace is deliberately ALREADY over the Pilot ceilings (8
    contracts, 12 evidence packages), which is the case a naive migration breaks.
    """
    ids = {
        'user_abc_owner': str(uuid.uuid4()),
        'user_abc_analyst': str(uuid.uuid4()),
        'user_xyz_owner': str(uuid.uuid4()),
        'ws_abc_free': str(uuid.uuid4()),
        'ws_abc_paid': str(uuid.uuid4()),
        'ws_xyz': str(uuid.uuid4()),
    }
    for key, email in (
        ('user_abc_owner', 'owner@abc.example'),
        ('user_abc_analyst', 'analyst@abc.example'),
        ('user_xyz_owner', 'owner@xyz.example'),
    ):
        connection.execute(
            'INSERT INTO users (id, email, password_hash, full_name) VALUES (%s, %s, %s, %s)',
            (ids[key], email, 'not-a-real-hash', email.split('@')[0]),
        )
    for key, name, slug, owner in (
        ('ws_abc_free', 'ABC Free', 'abc-free', 'user_abc_owner'),
        ('ws_abc_paid', 'ABC Paid', 'abc-paid', 'user_abc_owner'),
        ('ws_xyz', 'XYZ Ops', 'xyz-ops', 'user_xyz_owner'),
    ):
        connection.execute(
            'INSERT INTO workspaces (id, name, slug, created_by_user_id) VALUES (%s, %s, %s, %s)',
            (ids[key], name, slug, ids[owner]),
        )
    for ws_key, user_key, role in (
        ('ws_abc_free', 'user_abc_owner', 'workspace_owner'),
        ('ws_abc_free', 'user_abc_analyst', 'workspace_member'),
        ('ws_abc_paid', 'user_abc_owner', 'owner'),
        ('ws_xyz', 'user_xyz_owner', 'workspace_admin'),
    ):
        connection.execute(
            'INSERT INTO workspace_members (id, workspace_id, user_id, role) VALUES (%s, %s, %s, %s)',
            (str(uuid.uuid4()), ids[ws_key], ids[user_key], role),
        )
    connection.execute(
        "INSERT INTO billing_subscriptions (id, workspace_id, provider, provider_subscription_id, plan_key, status)"
        " VALUES (%s, %s, 'paddle', 'sub_test_1', 'growth', 'active')",
        (str(uuid.uuid4()), ids['ws_abc_paid']),
    )
    for index in range(8):
        connection.execute(
            'INSERT INTO assets (id, workspace_id, created_by_user_id, updated_by_user_id, name,'
            " asset_type, chain_network, identifier)"
            " VALUES (%s, %s, %s, %s, %s, 'contract', 'base-mainnet', %s)",
            (str(uuid.uuid4()), ids['ws_abc_free'], ids['user_abc_owner'], ids['user_abc_owner'],
             f'Legacy asset {index}', f'0x{index:040x}'),
        )
    for _ in range(12):
        connection.execute(
            'INSERT INTO export_jobs (id, workspace_id, requested_by_user_id, export_type, format, status)'
            " VALUES (%s, %s, %s, 'proof_bundle', 'json', 'completed')",
            (str(uuid.uuid4()), ids['ws_abc_free'], ids['user_abc_owner']),
        )
    return ids


def _counts(connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f'SELECT COUNT(*) AS c FROM {table}').fetchone()['c'])
        for table in _COUNTED_TABLES
    }


@pytest.fixture(scope='module')
def migrated():
    """Everything up to 0149, realistic data, then 0150 on top of it."""
    from psycopg.rows import dict_row

    _reset_schema()
    _apply(_migrations(include_tenancy=False))
    with psycopg.connect(_DSN, autocommit=True, row_factory=dict_row) as connection:
        ids = _seed_pre_existing_data(connection)
        before = _counts(connection)
    _apply([_MIGRATIONS / _TENANCY_MIGRATION])
    with psycopg.connect(_DSN, autocommit=True, row_factory=dict_row) as connection:
        yield {'ids': ids, 'before': before, 'connection': connection}


# ── 1 — nothing is destroyed ──────────────────────────────────────────────────

def test_no_pre_existing_row_is_lost(migrated) -> None:
    assert _counts(migrated['connection']) == migrated['before']


def test_every_workspace_is_linked_to_a_tenant(migrated) -> None:
    unlinked = migrated['connection'].execute(
        'SELECT COUNT(*) AS c FROM workspaces WHERE organization_id IS NULL',
    ).fetchone()['c']
    assert int(unlinked) == 0


# ── 2 — no two customers are merged ───────────────────────────────────────────

def test_each_workspace_becomes_its_own_organization(migrated) -> None:
    rows = migrated['connection'].execute(
        'SELECT o.id, (SELECT COUNT(*) FROM workspaces w WHERE w.organization_id = o.id) AS workspaces'
        ' FROM organizations o',
    ).fetchall()
    assert len(rows) == 3
    assert {int(row['workspaces']) for row in rows} == {1}


# ── 3 / 4 — plan and evaluation window are derived, never guessed ─────────────

def test_a_paying_workspace_is_not_turned_into_an_evaluation(migrated) -> None:
    row = migrated['connection'].execute(
        "SELECT plan, entitlement_overrides FROM organizations WHERE slug = 'abc-paid'",
    ).fetchone()
    assert row['plan'] == 'scale'
    assert dict(row['entitlement_overrides']) == {}


def test_grandfathered_workspaces_get_no_retroactive_deadline(migrated) -> None:
    rows = migrated['connection'].execute(
        'SELECT slug, evaluation_expires_at, status FROM organizations',
    ).fetchall()
    for row in rows:
        assert row['evaluation_expires_at'] is None, row['slug']
        assert row['status'] == 'active', row['slug']


def test_an_over_limit_workspace_keeps_working_after_the_migration(migrated) -> None:
    """8 contracts and 12 evidence packages already existed; the backfilled limits
    must be at least that, or the migration would have locked the customer out of
    their own account."""
    overrides = dict(migrated['connection'].execute(
        "SELECT entitlement_overrides FROM organizations WHERE slug = 'abc-free'",
    ).fetchone()['entitlement_overrides'])
    assert overrides['backfilled_from_workspace'] is True
    assert overrides['max_monitored_contracts'] >= 8
    assert overrides['max_evidence_packages'] >= 12


def test_a_workspace_within_the_limits_keeps_the_published_pilot_numbers(migrated) -> None:
    overrides = dict(migrated['connection'].execute(
        "SELECT entitlement_overrides FROM organizations WHERE slug = 'xyz-ops'",
    ).fetchone()['entitlement_overrides'])
    assert overrides['max_monitored_contracts'] == 5
    assert overrides['max_evidence_packages'] == 10
    assert overrides['max_workspaces'] == 1


# ── 5 — membership mirrors what already existed ───────────────────────────────

def test_organization_membership_mirrors_workspace_membership(migrated) -> None:
    rows = migrated['connection'].execute(
        'SELECT o.slug, u.email, m.role FROM organization_memberships m'
        ' JOIN organizations o ON o.id = m.organization_id'
        ' JOIN users u ON u.id = m.user_id ORDER BY o.slug, u.email',
    ).fetchall()
    assert [(row['slug'], row['email'], row['role']) for row in rows] == [
        ('abc-free', 'analyst@abc.example', 'analyst'),
        ('abc-free', 'owner@abc.example', 'owner'),
        ('abc-paid', 'owner@abc.example', 'owner'),
        ('xyz-ops', 'owner@xyz.example', 'admin'),
    ]


def test_no_user_reaches_a_tenant_they_were_not_already_a_member_of(migrated) -> None:
    leaked = migrated['connection'].execute(
        '''
        SELECT COUNT(*) AS c
        FROM organization_memberships m
        WHERE NOT EXISTS (
            SELECT 1 FROM workspace_members wm
            JOIN workspaces w ON w.id = wm.workspace_id
            WHERE w.organization_id = m.organization_id AND wm.user_id = m.user_id
        )
        ''',
    ).fetchone()['c']
    assert int(leaked) == 0


# ── 6 / 7 — idempotency and no privilege grant ────────────────────────────────

def test_re_running_the_backfill_creates_no_duplicate_tenant(migrated) -> None:
    _apply([_MIGRATIONS / _TENANCY_MIGRATION])
    connection = migrated['connection']
    assert int(connection.execute('SELECT COUNT(*) AS c FROM organizations').fetchone()['c']) == 3
    assert int(connection.execute('SELECT COUNT(*) AS c FROM organization_memberships').fetchone()['c']) == 4
    assert _counts(connection) == migrated['before']


def test_the_migration_grants_nobody_internal_admin(migrated) -> None:
    granted = migrated['connection'].execute(
        'SELECT COUNT(*) AS c FROM users WHERE is_internal_admin',
    ).fetchone()['c']
    assert int(granted) == 0


def test_tenant_indexes_exist_for_the_scoped_queries(migrated) -> None:
    names = {
        row['indexname']
        for row in migrated['connection'].execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'",
        ).fetchall()
    }
    for expected in (
        'idx_workspaces_organization',
        'idx_workspaces_organization_created',
        'idx_organizations_plan_status',
        'idx_organizations_status_expires',
        'idx_organization_memberships_user',
        'idx_organization_feedback_org_created',
    ):
        assert expected in names, expected
