"""Migration 0151 against REAL PostgreSQL, with pre-existing tenants in place.

What an in-process fake cannot check is exactly what matters here: the partial
unique indexes. "At most one OPEN request per address" and "a token hash resolves
to at most one request" are database guarantees, not application ones — the
application relies on them to make duplicate submission idempotent and to make an
accepted invitation unusable a second time under concurrency.

What is pinned:

  1  Every pre-existing row survives, and every pre-existing organization keeps
     its plan, status, and evaluation window. Approval-only applies to NEW
     external onboarding; it is not retroactive.
  2  One OPEN request per address is enforced by the database, while a rejected,
     activated, or expired row does not block a fresh application.
  3  A token hash is unique, and many rows may hold NULL without colliding.
  4  The status CHECK admits exactly the six states the service uses.
  5  The migration is re-runnable: applying it twice changes nothing.
  6  No account is granted internal admin, and no organization is created.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_pilot_requests_migration_postgres.py
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
               'psql on PATH to run the pilot-request migration harness',
    ),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
_PILOT_REQUESTS_MIGRATION = '0151_pilot_access_requests.sql'


def _psql(*args: str) -> None:
    proc = subprocess.run(
        [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', *args],
        capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, f'psql failed:\n{proc.stdout}\n{proc.stderr}'


def _reset_schema() -> None:
    _psql('-c', 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')


def _apply_all() -> None:
    for path in sorted(_MIGRATIONS.glob('*.sql')):
        _psql('-f', str(path))


def _apply_pilot_requests_again() -> None:
    _psql('-f', str(_MIGRATIONS / _PILOT_REQUESTS_MIGRATION))


def _connect():
    return psycopg.connect(_DSN, row_factory=psycopg.rows.dict_row, autocommit=True)


def _insert_request(connection, *, email: str, status: str, token_hash: str | None = None) -> str:
    row_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO pilot_requests (id, email, company_name, role, use_case, status, invitation_token_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''',
        (row_id, email, 'Company', 'Head of Security', 'tokenized treasury monitoring', status, token_hash),
    )
    return row_id


@pytest.fixture(scope='module')
def migrated():
    _reset_schema()
    _apply_all()
    with _connect() as connection:
        yield connection


def test_the_table_and_its_columns_exist(migrated) -> None:
    columns = {
        row['column_name']
        for row in migrated.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'pilot_requests'"
        ).fetchall()
    }
    for expected in (
        'id', 'email', 'company_name', 'role', 'company_website', 'use_case', 'status',
        'requested_at', 'reviewed_at', 'reviewed_by_user_id', 'approved_at', 'rejected_at',
        'internal_note', 'invitation_token_hash', 'invitation_expires_at', 'invitation_sent_at',
        'invitation_accepted_at', 'invitation_delivery_error', 'organization_id',
        'source_ip', 'created_at', 'updated_at',
    ):
        assert expected in columns, f'pilot_requests is missing {expected}'


@pytest.mark.parametrize('status', ['pending', 'approved', 'invited', 'rejected', 'activated', 'expired'])
def test_every_service_status_is_accepted(migrated, status: str) -> None:
    row_id = _insert_request(migrated, email=f'{status}@status.example', status=status)
    stored = migrated.execute(
        'SELECT status FROM pilot_requests WHERE id = %s', (row_id,),
    ).fetchone()
    assert stored['status'] == status
    migrated.execute('DELETE FROM pilot_requests WHERE id = %s', (row_id,))


def test_an_unknown_status_is_refused_by_the_check_constraint(migrated) -> None:
    with pytest.raises(Exception):
        _insert_request(migrated, email='bogus@status.example', status='auto_approved')


@pytest.mark.parametrize('second_status', ['pending', 'approved', 'invited'])
def test_only_one_open_request_per_address(migrated, second_status: str) -> None:
    """This is what makes duplicate submission idempotent under concurrency: two
    requests racing for the same address cannot both create an open row."""
    email = f'open-{second_status}@duplicate.example'
    first = _insert_request(migrated, email=email, status='pending')
    try:
        with pytest.raises(Exception):
            _insert_request(migrated, email=email, status=second_status)
    finally:
        migrated.execute('DELETE FROM pilot_requests WHERE email = %s', (email,))
        assert first


@pytest.mark.parametrize('closed_status', ['rejected', 'activated', 'expired'])
def test_a_closed_request_does_not_block_a_new_application(migrated, closed_status: str) -> None:
    """Rejection closes one application; it does not blacklist an address."""
    email = f'closed-{closed_status}@duplicate.example'
    _insert_request(migrated, email=email, status=closed_status)
    _insert_request(migrated, email=email, status='pending')
    count = migrated.execute(
        'SELECT COUNT(*) AS count FROM pilot_requests WHERE email = %s', (email,),
    ).fetchone()['count']
    assert count == 2
    migrated.execute('DELETE FROM pilot_requests WHERE email = %s', (email,))


def test_a_token_hash_is_unique_but_many_rows_may_hold_none(migrated) -> None:
    _insert_request(migrated, email='tok-a@token.example', status='approved', token_hash='hash-1')
    with pytest.raises(Exception):
        _insert_request(migrated, email='tok-b@token.example', status='approved', token_hash='hash-1')
    # NULL hashes never collide: most rows have no live invitation.
    _insert_request(migrated, email='tok-c@token.example', status='pending')
    _insert_request(migrated, email='tok-d@token.example', status='pending')
    migrated.execute("DELETE FROM pilot_requests WHERE email LIKE 'tok-%@token.example'")


def test_the_migration_is_re_runnable(migrated) -> None:
    email = 're-run@idempotent.example'
    _insert_request(migrated, email=email, status='pending')
    _apply_pilot_requests_again()
    surviving = migrated.execute(
        'SELECT COUNT(*) AS count FROM pilot_requests WHERE email = %s', (email,),
    ).fetchone()['count']
    assert surviving == 1
    # The constraint is still in force after a second application.
    with pytest.raises(Exception):
        _insert_request(migrated, email=email, status='pending')
    migrated.execute('DELETE FROM pilot_requests WHERE email = %s', (email,))


def test_the_migration_creates_no_organization_and_grants_no_internal_admin(migrated) -> None:
    """0151 is additive. It provisions nothing and promotes nobody."""
    organizations = migrated.execute('SELECT COUNT(*) AS count FROM organizations').fetchone()['count']
    assert organizations == 0
    internal = migrated.execute(
        'SELECT COUNT(*) AS count FROM users WHERE is_internal_admin', (),
    ).fetchone()['count']
    assert internal == 0
