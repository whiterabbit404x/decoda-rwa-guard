"""The append-only audit guard and the retention anonymizer, against REAL PostgreSQL.

This file exists because the defect it pins could not be seen from the test suite
that was here before it. `audit_logs` is protected by a PL/pgSQL trigger, and a
trigger is not a Python object: a fake connection cannot raise from one, and a
test that asserts on the migration's TEXT only proves the text. So migration 0100
shipped a guard that rejected EVERY update, `data_retention` shipped an
anonymizer that performs one, `pilot_retention` selected `anonymize` as the audit
mode for the end-of-Pilot sequence, and all three passed their tests while the
combination could not run at all.

What is pinned, all of it executed by PostgreSQL rather than described to it:

  1  An ordinary INSERT is allowed; an ordinary UPDATE and an ordinary DELETE are
     refused, with the trigger's own error.
  2  The real anonymization path — `data_retention.execute_request`, the function
     the retention worker calls — completes against a live database.
  3  It destroys exactly the actor, the IP and the metadata, and leaves the
     primary key, the tenant, the action, the entity, the timestamp and every
     hash-chain column byte-identical.
  4  The retention capability is not a write bypass: with the flag held, changing
     the action, the workspace, the timestamp, the hash chain or the metadata to
     anything but the approved marker is still refused.
  5  The two capabilities are separate. The delete flag cannot perform an update
     and the anonymize flag cannot perform a delete.
  6  The authorization is transaction-local and does not survive its transaction,
     so it cannot leak onto a later statement or a reused connection.
  7  The real scheduled hard-delete path removes the anonymized skeleton.
  8  Anonymizing one workspace leaves another workspace's audit rows untouched.
  9  A legal hold still blocks the sweep.
 10  The hash chain's behaviour across anonymization is what the verifier claims:
     linkage still verifies, the anonymized rows are counted, and the result stops
     claiming the chain is fully verifiable.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_audit_retention_anonymization_postgres.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

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
               'psql on PATH to run the audit retention anonymization harness',
    ),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
_GUARD_MIGRATION = '0156_audit_retention_anonymization_guard.sql'

_APPEND_ONLY = 'audit_logs is append-only'


def _immutable_columns(connection) -> list[str]:
    """Every column on the LIVE `audit_logs` that anonymization must leave alone.

    Derived from the database rather than hand-listed, so a column added to
    `audit_logs` later is covered by these assertions the day it lands instead of
    the day someone remembers to extend a literal here.
    """
    from services.api.app.data_retention import AUDIT_ANONYMIZED_COLUMNS

    rows = connection.execute(
        '''SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'audit_logs' ORDER BY column_name'''
    ).fetchall()
    columns = [str(row['column_name']) for row in rows]
    assert set(AUDIT_ANONYMIZED_COLUMNS) <= set(columns), 'the approved set must exist on the table'
    return [column for column in columns if column not in AUDIT_ANONYMIZED_COLUMNS]


def _psql(*args: str) -> None:
    proc = subprocess.run(
        [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', *args],
        capture_output=True, text=True, timeout=1800,
    )
    assert proc.returncode == 0, f'psql failed:\n{proc.stdout}\n{proc.stderr}'


def _connect():
    return psycopg.connect(_DSN, row_factory=psycopg.rows.dict_row)


@pytest.fixture(scope='module')
def migrated() -> None:
    """A database with every migration applied, the guard fix among them."""
    assert (_MIGRATIONS / _GUARD_MIGRATION).exists(), \
        f'{_GUARD_MIGRATION} must exist: these assertions are about the guard it installs'
    _psql('-c', 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
    for path in sorted(_MIGRATIONS.glob('*.sql')):
        _psql('-f', str(path))


@pytest.fixture()
def db(migrated):
    """A clean tenant fixture per test, on a connection that is NOT in autocommit.

    Not autocommit on purpose: the retention worker runs `execute_request` inside
    a transaction, and the whole authorization design depends on that being true.
    """
    with _connect() as connection:
        connection.execute('DELETE FROM data_deletion_events')
        connection.execute('DELETE FROM data_deletion_requests')
        connection.execute('DELETE FROM workspace_legal_holds')
        connection.execute("SELECT set_config('app.retention_worker', 'on', true)")
        connection.execute('DELETE FROM audit_logs')
        connection.execute('DELETE FROM workspaces')
        connection.execute('DELETE FROM users')
        connection.commit()
        yield connection


def _seed_tenant(connection, *, label: str) -> dict[str, str]:
    user_id, workspace_id = str(uuid.uuid4()), str(uuid.uuid4())
    connection.execute(
        'INSERT INTO users (id, email, full_name, password_hash) VALUES (%s, %s, %s, %s)',
        (user_id, f'{label}@example.test', f'User {label}', 'not-a-real-hash'),
    )
    connection.execute(
        'INSERT INTO workspaces (id, name, slug, created_by_user_id) VALUES (%s, %s, %s, %s)',
        (workspace_id, f'WS {label}', f'ws-{label}', user_id),
    )
    connection.execute(
        'INSERT INTO workspace_members (id, workspace_id, user_id, role) VALUES (%s, %s, %s, %s)',
        (str(uuid.uuid4()), workspace_id, user_id, 'owner'),
    )
    return {'user_id': user_id, 'workspace_id': workspace_id}


def _append_audit_rows(connection, tenant: dict[str, str], *, count: int, age_days: int) -> list[dict]:
    """Insert a real, correctly hash-chained run of audit rows, as `log_audit` does."""
    from services.api.app.evidence_signing import canonical_json, compute_audit_row_hash

    previous = connection.execute(
        '''SELECT row_hash FROM audit_logs WHERE workspace_id = %s AND row_hash IS NOT NULL
           ORDER BY created_at DESC, id DESC LIMIT 1''',
        (tenant['workspace_id'],),
    ).fetchone()
    previous_row_hash = str(previous['row_hash']) if previous else None

    written = []
    for index in range(count):
        row_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc) - timedelta(days=age_days, seconds=(count - index) * 60)
        metadata = {'email': f'user{index}@example.test', 'seq': index}
        row_hash = compute_audit_row_hash(
            row_id=row_id,
            workspace_id=tenant['workspace_id'],
            user_id=tenant['user_id'],
            action='workspace.member.login',
            entity_type='user',
            entity_id=tenant['user_id'],
            created_at_iso=created_at.isoformat(),
            metadata_sha256=hashlib.sha256(canonical_json(metadata)).hexdigest(),
            previous_row_hash=previous_row_hash,
        )
        connection.execute(
            '''INSERT INTO audit_logs (id, workspace_id, user_id, action, entity_type, entity_id,
                                       ip_address, metadata, created_at, row_hash, previous_row_hash,
                                       hash_algorithm, sealed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)''',
            (row_id, tenant['workspace_id'], tenant['user_id'], 'workspace.member.login', 'user',
             tenant['user_id'], f'10.0.0.{index + 1}', json.dumps(metadata), created_at,
             row_hash, previous_row_hash, 'sha256', created_at),
        )
        written.append({'id': row_id, 'row_hash': row_hash, 'previous_row_hash': previous_row_hash,
                        'created_at': created_at, 'metadata': metadata})
        previous_row_hash = row_hash
    connection.commit()
    return written


def _snapshot(connection, workspace_id: str) -> dict[str, dict]:
    rows = connection.execute(
        'SELECT * FROM audit_logs WHERE workspace_id = %s ORDER BY created_at, id', (workspace_id,)
    ).fetchall()
    return {str(row['id']): dict(row) for row in rows}


def _queue_request(connection, tenant: dict[str, str], *, mode: str, cutoff: datetime,
                   data_class: str = 'audit_logs') -> dict:
    request_id = str(uuid.uuid4())
    connection.execute(
        '''INSERT INTO data_deletion_requests
           (id, workspace_id, request_type, data_classes, cutoff_at, status, reason,
            requested_by_user_id, result)
           VALUES (%s, %s, 'retention_sweep', %s::jsonb, %s, 'approved', 'harness',
                   %s, %s::jsonb)''',
        (request_id, tenant['workspace_id'], json.dumps([data_class]), cutoff,
         tenant['user_id'], json.dumps({'deletion_modes': {data_class: mode}})),
    )
    connection.commit()
    return connection.execute(
        'SELECT * FROM data_deletion_requests WHERE id = %s', (request_id,)
    ).fetchone()


def _run_retention(connection, request) -> dict:
    """Invoke the real path the retention worker invokes."""
    from services.api.app.data_retention import execute_request
    outcome = execute_request(connection, request, worker_name='pytest-retention-worker')
    connection.commit()
    return outcome


# ── 1. append-only holds for ordinary callers ────────────────────────────────

def test_ordinary_insert_is_allowed_update_and_delete_are_refused(db):
    tenant = _seed_tenant(db, label='alpha')
    rows = _append_audit_rows(db, tenant, count=1, age_days=400)
    assert db.execute('SELECT COUNT(*) AS n FROM audit_logs').fetchone()['n'] == 1

    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute("UPDATE audit_logs SET action = 'forged' WHERE id = %s", (rows[0]['id'],))
    db.rollback()

    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute('DELETE FROM audit_logs WHERE id = %s', (rows[0]['id'],))
    db.rollback()

    # Even the anonymizer's own statement is refused without the authorization.
    from services.api.app.data_retention import ANONYMIZE_SQL
    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute(ANONYMIZE_SQL['audit_logs'], (tenant['workspace_id'], datetime.now(timezone.utc)))
    db.rollback()

    assert db.execute('SELECT COUNT(*) AS n FROM audit_logs').fetchone()['n'] == 1


# ── 2-3. the real anonymization path ─────────────────────────────────────────

def test_real_retention_path_anonymizes_only_the_approved_columns(db):
    tenant = _seed_tenant(db, label='beta')
    _append_audit_rows(db, tenant, count=3, age_days=400)
    before = _snapshot(db, tenant['workspace_id'])

    request = _queue_request(db, tenant, mode='anonymize', cutoff=datetime.now(timezone.utc))
    outcome = _run_retention(db, request)

    assert outcome['status'] == 'completed', outcome
    assert outcome['operations']['audit_logs']['mode'] == 'anonymize'
    assert outcome['operations']['audit_logs']['records_affected'] == 3

    after = _snapshot(db, tenant['workspace_id'])
    assert set(after) == set(before), 'anonymization must not add or remove audit rows'

    immutable = _immutable_columns(db)
    assert {'id', 'workspace_id', 'action', 'created_at', 'row_hash', 'previous_row_hash'} <= set(immutable)
    for row_id, row in after.items():
        assert row['user_id'] is None, 'actor identity must be destroyed'
        assert row['ip_address'] is None, 'IP address must be destroyed'
        assert row['metadata'] == {'_retention_anonymized': True}, 'event details must be destroyed'
        for column in immutable:
            assert row[column] == before[row_id][column], f'{column} must survive anonymization unchanged'


def test_anonymization_is_recorded_as_a_receipt_with_chain_anchors(db):
    tenant = _seed_tenant(db, label='receipt')
    _append_audit_rows(db, tenant, count=2, age_days=400)
    request = _queue_request(db, tenant, mode='anonymize', cutoff=datetime.now(timezone.utc))
    _run_retention(db, request)

    event = db.execute(
        '''SELECT operation, records_affected, chain_anchor_before, details
           FROM data_deletion_events WHERE workspace_id = %s AND data_class = 'audit_logs' ''',
        (tenant['workspace_id'],),
    ).fetchone()
    assert event['operation'] == 'anonymize'
    assert event['records_affected'] == 2
    assert event['chain_anchor_before'], 'the receipt must anchor the chain tip it acted on'
    assert event['details']['worker_name'] == 'pytest-retention-worker'


def test_retry_of_the_same_request_is_idempotent(db):
    tenant = _seed_tenant(db, label='retry')
    _append_audit_rows(db, tenant, count=2, age_days=400)
    request = _queue_request(db, tenant, mode='anonymize', cutoff=datetime.now(timezone.utc))

    first = _run_retention(db, request)
    after_first = _snapshot(db, tenant['workspace_id'])
    second = _run_retention(db, request)
    after_second = _snapshot(db, tenant['workspace_id'])

    assert first['status'] == second['status'] == 'completed'
    assert after_first == after_second, 'a retry must not change an already-anonymized row'
    receipts = db.execute(
        '''SELECT COUNT(*) AS n FROM data_deletion_events
           WHERE workspace_id = %s AND data_class = 'audit_logs' AND operation = 'anonymize' ''',
        (tenant['workspace_id'],),
    ).fetchone()
    assert receipts['n'] == 1, 'the idempotency key must collapse the retry to one receipt'


# ── 4. the capability is not a write bypass ──────────────────────────────────

@pytest.mark.parametrize(
    ('label', 'statement', 'message'),
    [
        ('action', "UPDATE audit_logs SET action = 'forged', user_id = NULL, ip_address = NULL, "
                   "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'immutable columns: action'),
        ('workspace', 'UPDATE audit_logs SET workspace_id = gen_random_uuid(), user_id = NULL, ip_address = NULL, '
                      "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'immutable columns: workspace_id'),
        ('timestamp', "UPDATE audit_logs SET created_at = NOW(), user_id = NULL, ip_address = NULL, "
                      "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'immutable columns: created_at'),
        ('entity', "UPDATE audit_logs SET entity_type = 'other', user_id = NULL, ip_address = NULL, "
                   "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'immutable columns: entity_type'),
        ('row_hash', "UPDATE audit_logs SET row_hash = 'forged', user_id = NULL, ip_address = NULL, "
                     "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'immutable columns: row_hash'),
        ('previous_row_hash', "UPDATE audit_logs SET previous_row_hash = 'forged', user_id = NULL, ip_address = NULL, "
                              "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'immutable columns: previous_row_hash'),
        ('sealed_at', "UPDATE audit_logs SET sealed_at = NOW(), user_id = NULL, ip_address = NULL, "
                      "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'immutable columns: sealed_at'),
        ('arbitrary metadata', "UPDATE audit_logs SET user_id = NULL, ip_address = NULL, "
                               "metadata = '{\"injected\": \"evidence\"}'::jsonb WHERE id = %s",
         'must clear user_id and ip_address'),
        ('substituted actor', "UPDATE audit_logs SET user_id = gen_random_uuid(), ip_address = NULL, "
                              "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'must clear user_id and ip_address'),
        ('substituted ip', "UPDATE audit_logs SET user_id = NULL, ip_address = '203.0.113.9', "
                           "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
         'must clear user_id and ip_address'),
        ('metadata kept', 'UPDATE audit_logs SET user_id = NULL, ip_address = NULL WHERE id = %s',
         'must clear user_id and ip_address'),
    ],
)
def test_authorized_retention_worker_still_cannot_make_an_arbitrary_change(db, label, statement, message):
    tenant = _seed_tenant(db, label='guard')
    rows = _append_audit_rows(db, tenant, count=1, age_days=400)
    before = _snapshot(db, tenant['workspace_id'])

    from services.api.app.data_retention import AUDIT_ANONYMIZE_SETTING
    db.execute('SELECT set_config(%s, %s, true)', (AUDIT_ANONYMIZE_SETTING, 'on'))
    with pytest.raises(psycopg.errors.RaiseException, match=message):
        db.execute(statement, (rows[0]['id'],))
    db.rollback()

    assert _snapshot(db, tenant['workspace_id']) == before, f'{label}: the row must be untouched'


def test_the_two_retention_capabilities_cannot_substitute_for_each_other(db):
    from services.api.app.data_retention import AUDIT_ANONYMIZE_SETTING, AUDIT_RETENTION_DELETE_SETTING
    tenant = _seed_tenant(db, label='separation')
    rows = _append_audit_rows(db, tenant, count=1, age_days=400)

    # The DELETE authorization cannot perform the anonymization UPDATE.
    db.execute('SELECT set_config(%s, %s, true)', (AUDIT_RETENTION_DELETE_SETTING, 'on'))
    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute(
            "UPDATE audit_logs SET user_id = NULL, ip_address = NULL, "
            "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s",
            (rows[0]['id'],),
        )
    db.rollback()

    # And the anonymize authorization cannot perform a DELETE.
    db.execute('SELECT set_config(%s, %s, true)', (AUDIT_ANONYMIZE_SETTING, 'on'))
    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute('DELETE FROM audit_logs WHERE id = %s', (rows[0]['id'],))
    db.rollback()

    assert db.execute('SELECT COUNT(*) AS n FROM audit_logs').fetchone()['n'] == 1


# ── 5. the authorization is transaction-local ────────────────────────────────

def test_authorization_does_not_outlive_its_transaction(db):
    from services.api.app.data_retention import AUDIT_ANONYMIZE_SETTING
    tenant = _seed_tenant(db, label='local')
    rows = _append_audit_rows(db, tenant, count=1, age_days=400)
    anonymize = ("UPDATE audit_logs SET user_id = NULL, ip_address = NULL, "
                 "metadata = jsonb_build_object('_retention_anonymized', true) WHERE id = %s")

    # Granted, then the transaction ends: the grant ends with it.
    db.execute('SELECT set_config(%s, %s, true)', (AUDIT_ANONYMIZE_SETTING, 'on'))
    db.commit()
    assert db.execute(
        'SELECT current_setting(%s, true) AS value', (AUDIT_ANONYMIZE_SETTING,)
    ).fetchone()['value'] in (None, '')
    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute(anonymize, (rows[0]['id'],))
    db.rollback()

    # Same after a rollback, so a failed sweep cannot leave the flag standing.
    db.execute('SELECT set_config(%s, %s, true)', (AUDIT_ANONYMIZE_SETTING, 'on'))
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute(anonymize, (rows[0]['id'],))
    db.rollback()

    # And a brand-new connection never inherits it.
    with _connect() as fresh:
        assert fresh.execute(
            'SELECT current_setting(%s, true) AS value', (AUDIT_ANONYMIZE_SETTING,)
        ).fetchone()['value'] in (None, '')
        with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
            fresh.execute(anonymize, (rows[0]['id'],))
        fresh.rollback()


def test_the_worker_leaves_no_standing_authorization_after_a_sweep(db):
    from services.api.app.data_retention import AUDIT_ANONYMIZE_SETTING
    tenant = _seed_tenant(db, label='afterflag')
    _append_audit_rows(db, tenant, count=1, age_days=400)
    request = _queue_request(db, tenant, mode='anonymize', cutoff=datetime.now(timezone.utc))
    _run_retention(db, request)

    assert db.execute(
        'SELECT current_setting(%s, true) AS value', (AUDIT_ANONYMIZE_SETTING,)
    ).fetchone()['value'] in (None, ''), 'the sweep must not leave the capability set on the connection'

    fresh_rows = _append_audit_rows(db, tenant, count=1, age_days=0)
    with pytest.raises(psycopg.errors.RaiseException, match=_APPEND_ONLY):
        db.execute("UPDATE audit_logs SET action = 'forged' WHERE id = %s", (fresh_rows[0]['id'],))
    db.rollback()


# ── 6. the scheduled hard delete ─────────────────────────────────────────────

def test_real_retention_path_hard_deletes_the_anonymized_skeleton(db):
    tenant = _seed_tenant(db, label='purge')
    _append_audit_rows(db, tenant, count=3, age_days=400)

    _run_retention(db, _queue_request(db, tenant, mode='anonymize', cutoff=datetime.now(timezone.utc)))
    assert db.execute(
        'SELECT COUNT(*) AS n FROM audit_logs WHERE workspace_id = %s', (tenant['workspace_id'],)
    ).fetchone()['n'] == 3

    outcome = _run_retention(db, _queue_request(db, tenant, mode='hard_delete',
                                                cutoff=datetime.now(timezone.utc)))
    assert outcome['status'] == 'completed'
    assert outcome['operations']['audit_logs']['mode'] == 'hard_delete'
    assert db.execute(
        'SELECT COUNT(*) AS n FROM audit_logs WHERE workspace_id = %s', (tenant['workspace_id'],)
    ).fetchone()['n'] == 0


# ── 7. tenant isolation ──────────────────────────────────────────────────────

def test_anonymizing_one_workspace_leaves_another_untouched(db):
    swept = _seed_tenant(db, label='swept')
    neighbour = _seed_tenant(db, label='neighbour')
    _append_audit_rows(db, swept, count=2, age_days=400)
    _append_audit_rows(db, neighbour, count=2, age_days=400)
    neighbour_before = _snapshot(db, neighbour['workspace_id'])

    _run_retention(db, _queue_request(db, swept, mode='anonymize', cutoff=datetime.now(timezone.utc)))

    assert _snapshot(db, neighbour['workspace_id']) == neighbour_before, 'cross-tenant leakage'
    for row in _snapshot(db, swept['workspace_id']).values():
        assert row['user_id'] is None

    # And the hard delete is scoped the same way.
    _run_retention(db, _queue_request(db, swept, mode='hard_delete', cutoff=datetime.now(timezone.utc)))
    assert db.execute(
        'SELECT COUNT(*) AS n FROM audit_logs WHERE workspace_id = %s', (neighbour['workspace_id'],)
    ).fetchone()['n'] == 2


# ── 8. legal hold ────────────────────────────────────────────────────────────

def test_legal_hold_blocks_audit_anonymization(db):
    tenant = _seed_tenant(db, label='hold')
    _append_audit_rows(db, tenant, count=2, age_days=400)
    before = _snapshot(db, tenant['workspace_id'])
    db.execute(
        '''INSERT INTO workspace_legal_holds
           (id, workspace_id, name, reason, data_classes, status, created_by_user_id)
           VALUES (%s, %s, %s, %s, %s::jsonb, 'active', %s)''',
        (str(uuid.uuid4()), tenant['workspace_id'], 'Hold 1', 'litigation',
         json.dumps(['audit_logs']), tenant['user_id']),
    )
    db.commit()

    outcome = _run_retention(db, _queue_request(db, tenant, mode='anonymize',
                                                 cutoff=datetime.now(timezone.utc)))

    assert outcome['status'] == 'blocked_by_legal_hold'
    assert _snapshot(db, tenant['workspace_id']) == before, 'a held workspace must not be anonymized'


# ── 9. the hash chain, before and after ──────────────────────────────────────

def test_hash_chain_behaviour_across_anonymization_is_what_the_verifier_claims(db):
    from services.api.app.evidence_signing import verify_audit_chain

    tenant = _seed_tenant(db, label='chain')
    _append_audit_rows(db, tenant, count=4, age_days=400)
    _append_audit_rows(db, tenant, count=2, age_days=0)

    def chain() -> list[dict]:
        return [dict(row) for row in db.execute(
            '''SELECT id, workspace_id, user_id, action, entity_type, entity_id, metadata,
                      created_at, row_hash, previous_row_hash
               FROM audit_logs WHERE workspace_id = %s ORDER BY created_at, id''',
            (tenant['workspace_id'],),
        ).fetchall()]

    before = verify_audit_chain(chain())
    assert before['valid'] and before['fully_verifiable']
    assert before['anonymized_rows'] == 0 and before['chain_length'] == 6

    # Only the four rows past the cutoff are anonymized.
    cutoff = datetime.now(timezone.utc) - timedelta(days=200)
    _run_retention(db, _queue_request(db, tenant, mode='anonymize', cutoff=cutoff))

    after = verify_audit_chain(chain())
    assert after['valid'], f'a published retention sweep must not read as tampering: {after["errors"]}'
    assert after['anonymized_rows'] == 4, 'the cost of anonymization must be counted, not hidden'
    assert after['fully_verifiable'] is False, 'the chain must stop claiming full verifiability'
    assert after['chain_length'] == 6

    # Linkage is still enforced across the anonymized stretch: tamper with a row
    # that survived the sweep and the chain still fails.
    rows = chain()
    rows[-1] = {**rows[-1], 'action': 'forged'}
    tampered = verify_audit_chain(rows)
    assert not tampered['valid']
    assert any('row_hash_mismatch' in error for error in tampered['errors'])
