"""The legacy identity manifest export: classification, secrecy, read-only.

The classification is a pure function and is tested without a database. The
command itself (``main``) is then run against a real, migrated PostgreSQL when
``DECODA_MIGRATION_TEST_DSN`` is set, proving it reads inside a READ ONLY
transaction and never writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import uuid
from datetime import UTC, datetime

import pytest

from services.api.scripts import identity_migration_export as export

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
VERIFIED = datetime(2026, 1, 5, tzinfo=UTC)


def _user(email: str, **fields):
    return {
        'id': fields.pop('id', str(uuid.uuid4())),
        'email': email,
        'full_name': fields.pop('full_name', email.split('@')[0].title()),
        'email_verified_at': fields.pop('email_verified_at', VERIFIED),
        'mfa_enabled_at': fields.pop('mfa_enabled_at', None),
        'last_sign_in_at': fields.pop('last_sign_in_at', None),
        'auth_provider': fields.pop('auth_provider', 'password'),
        'external_subject': fields.pop('external_subject', None),
        'suspended_at': fields.pop('suspended_at', None),
        'is_internal_admin': fields.pop('is_internal_admin', False),
        # Present on the row, and must never leave it.
        'password_hash': 'scrypt$SECRET',
        'mfa_totp_secret': 'TOTPSECRET',
        **fields,
    }


def _org(name: str, status: str = 'active', **fields):
    return {'id': str(uuid.uuid4()), 'name': name, 'slug': name.lower().replace(' ', '-'), 'plan': 'pilot', 'status': status, **fields}


def _classify(users, orgs, memberships):
    return export.build_manifest(users=users, organizations=orgs, memberships=memberships, generated_at=NOW)


def _by_email(manifest):
    return {entry['email']: entry for entry in manifest['users']}


def test_every_account_lands_in_exactly_one_bucket_with_its_reason():
    harbor, closed = _org('Harbor Trust'), _org('Closed Co', status='expired')
    users = [
        _user('owner@harbor.io', mfa_enabled_at=VERIFIED),
        _user('linked@harbor.io', auth_provider='workos', external_subject='user_01LINKED'),
        _user('suspended@harbor.io', suspended_at=VERIFIED),
        _user('Dup@harbor.io'),
        _user('dup@harbor.io'),
        _user('unverified@harbor.io', email_verified_at=None),
        _user('qa@example.com'),
        _user('ci@harbor.test'),
        _user('loner@harbor.io'),
        _user('gone@closed.io'),
        _user('not-an-address'),
        _user('founder@decodasecurity.com', is_internal_admin=True),
    ]
    by_email = {u['email']: u for u in users}
    memberships = [
        {'organization_id': harbor['id'], 'user_id': by_email[e]['id'], 'role': 'viewer'}
        for e in ('linked@harbor.io', 'suspended@harbor.io', 'Dup@harbor.io', 'dup@harbor.io', 'unverified@harbor.io', 'qa@example.com',
                  'ci@harbor.test', 'not-an-address', 'founder@decodasecurity.com')
    ] + [
        {'organization_id': harbor['id'], 'user_id': by_email['owner@harbor.io']['id'], 'role': 'owner'},
        {'organization_id': closed['id'], 'user_id': by_email['gone@closed.io']['id'], 'role': 'owner'},
    ]
    manifest = _classify(users, [harbor, closed], memberships)
    entries = _by_email(manifest)

    expected = {
        'owner@harbor.io': ('needs_invitation', []),
        'linked@harbor.io': ('matched', ['already_linked']),
        'suspended@harbor.io': ('skipped', ['suspended']),
        'dup@harbor.io': ('conflict', ['duplicate_email']),
        'unverified@harbor.io': ('conflict', ['email_unverified']),
        'qa@example.com': ('skipped', ['reserved_test_domain']),
        'ci@harbor.test': ('skipped', ['reserved_test_domain']),
        'loner@harbor.io': ('skipped', ['no_organization']),
        'gone@closed.io': ('skipped', ['organization_inactive']),
        'not-an-address': ('conflict', ['invalid_email']),
        'founder@decodasecurity.com': ('needs_invitation', []),
    }
    for email, (bucket, reasons) in expected.items():
        assert (entries[email]['status'], entries[email]['reasons']) == (bucket, reasons), email
    # Both case-variants of the duplicate are conflicts — neither is picked.
    assert [e['status'] for e in manifest['users'] if e['email'] == 'dup@harbor.io'] == ['conflict', 'conflict']
    assert manifest['summary']['users'] == {'matched': 1, 'needs_invitation': 2, 'conflict': 4, 'skipped': 5}
    assert entries['founder@decodasecurity.com']['internal_admin'] is True
    assert entries['owner@harbor.io']['mfa_enrolled'] is True
    assert entries['linked@harbor.io']['workos_user_id'] == 'user_01LINKED'


def test_no_credential_material_is_exported():
    harbor = _org('Harbor Trust')
    owner = _user('owner@harbor.io', mfa_enabled_at=VERIFIED)
    payload = export.serialize(_classify([owner], [harbor], [{'organization_id': harbor['id'], 'user_id': owner['id'], 'role': 'owner'}]))
    text = payload.decode()
    for secret in ('SECRET', 'TOTPSECRET', 'password_hash', 'mfa_totp_secret', 'token', 'recovery'):
        assert secret not in text


def test_the_primary_organization_is_where_the_person_holds_the_strongest_role():
    a, b, closed = _org('Alpha'), _org('Beta'), _org('Aardvark', status='suspended')
    person = _user('multi@harbor.io')
    memberships = [
        {'organization_id': a['id'], 'user_id': person['id'], 'role': 'viewer'},
        {'organization_id': b['id'], 'user_id': person['id'], 'role': 'admin'},
        {'organization_id': closed['id'], 'user_id': person['id'], 'role': 'owner'},
    ]
    entry = _by_email(_classify([person], [a, b, closed], memberships))['multi@harbor.io']
    assert entry['status'] == 'needs_invitation'
    assert entry['primary_organization_id'] == b['id']  # the active org with the strongest role
    assert [o['legacy_organization_id'] for o in entry['organizations']] == [closed['id'], b['id'], a['id']]


def test_organizations_report_what_will_be_imported():
    harbor, empty, expired = _org('Harbor Trust'), _org('Empty Co'), _org('Expired Co', status='expired')
    owner, suspended = _user('owner@harbor.io'), _user('old@harbor.io', suspended_at=VERIFIED)
    memberships = [
        {'organization_id': harbor['id'], 'user_id': owner['id'], 'role': 'owner'},
        {'organization_id': harbor['id'], 'user_id': suspended['id'], 'role': 'viewer'},
        {'organization_id': expired['id'], 'user_id': owner['id'], 'role': 'owner'},
    ]
    manifest = _classify([owner, suspended], [harbor, empty, expired], memberships)
    orgs = {o['name']: o for o in manifest['organizations']}
    assert orgs['Harbor Trust']['importable_members'] == 1 and len(orgs['Harbor Trust']['members']) == 2
    assert orgs['Harbor Trust']['members'][0] == {'legacy_user_id': owner['id'], 'role': 'owner'}
    assert orgs['Empty Co']['importable_members'] == 0
    assert manifest['summary']['organizations'] == 3 and manifest['summary']['organizations_to_import'] == 1


def test_the_manifest_is_deterministic_for_the_same_data():
    harbor = _org('Harbor Trust')
    users = [_user('b@harbor.io'), _user('a@harbor.io')]
    memberships = [{'organization_id': harbor['id'], 'user_id': u['id'], 'role': 'viewer'} for u in users]
    first = export.serialize(_classify(users, [harbor], memberships))
    second = export.serialize(_classify(list(reversed(users)), [harbor], list(reversed(memberships))))
    assert first == second
    assert json.loads(first)['format'] == 'decoda.legacy_identity_manifest/v1'


def test_the_command_needs_a_database(monkeypatch, capsys):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert export.main(['--out', '-']) == 2
    assert 'missing_database_url' in capsys.readouterr().err


class _RecordingConnection:
    """Answers the export's reads and records every statement it sends."""

    def __init__(self):
        self.statements: list[str] = []
        self.rolled_back = False

    def execute(self, sql, params=None):
        self.statements.append(' '.join(sql.split()))
        lowered = sql.lower()
        if 'information_schema.columns' in lowered:
            table = params[0]
            columns = {
                'users': ['id', 'email', 'auth_provider', 'external_subject', 'suspended_at', 'is_internal_admin'],
                'organizations': ['id', 'name', 'platform_organization_id'],
                'organization_memberships': ['organization_id', 'user_id', 'role'],
            }[table]
            return _Rows([{'column_name': c} for c in columns])
        if 'to_regclass' in lowered:
            return _Rows([{'present': False}])
        return _Rows([])

    def rollback(self):
        self.rolled_back = True


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_the_export_reads_inside_a_read_only_transaction_and_writes_nothing():
    connection = _RecordingConnection()
    rows = export.load_rows(connection)
    assert rows == {'users': [], 'organizations': [], 'memberships': [], 'migration': None}
    assert connection.statements[0] == 'SET TRANSACTION READ ONLY'
    assert connection.rolled_back is True
    for statement in connection.statements[1:]:
        assert statement.split()[0].upper() == 'SELECT', statement
    assert not any('password' in statement or 'mfa_totp' in statement for statement in connection.statements)


# ── Against a real, migrated database ───────────────────────────────────────


def _fingerprint(connection) -> tuple:
    """Every row of the tables the export reads, plus the audit trail's size."""
    return connection.execute(
        """
        SELECT (SELECT md5(coalesce(string_agg(u::text, '|' ORDER BY u.id), '')) FROM users u),
               (SELECT md5(coalesce(string_agg(o::text, '|' ORDER BY o.id), '')) FROM organizations o),
               (SELECT md5(coalesce(string_agg(m::text, '|' ORDER BY m.id), '')) FROM organization_memberships m),
               (SELECT count(*) FROM audit_logs)
        """
    ).fetchone()

_DSN = os.environ.get('DECODA_MIGRATION_TEST_DSN')
_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'


def _real_psycopg():
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


@pytest.mark.integration
@pytest.mark.skipif(not _DSN, reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) to run')
def test_the_command_exports_a_real_database_without_writing(tmp_path, capsys):
    psycopg = _real_psycopg()
    from psycopg.rows import dict_row

    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
        for path in sorted(_MIGRATIONS.glob('*.sql')):
            connection.execute(path.read_text())
        org_id, owner_id, stray_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute("INSERT INTO organizations (id, name, slug) VALUES (%s, 'Harbor Trust', 'harbor-trust')", (org_id,))
        for user_id, email in ((owner_id, 'owner@harbor.io'), (stray_id, 'stray@harbor.io')):
            connection.execute(
                'INSERT INTO users (id, email, password_hash, full_name, email_verified_at) VALUES (%s, %s, %s, %s, NOW())',
                (user_id, email, 'scrypt$NOT-EXPORTED', 'Harbor Person'),
            )
        connection.execute(
            "INSERT INTO organization_memberships (id, organization_id, user_id, role) VALUES (%s, %s, %s, 'owner')",
            (str(uuid.uuid4()), org_id, owner_id),
        )
        before = _fingerprint(connection)

    out = tmp_path / 'manifest.json'

    def connect():
        return psycopg.connect(_DSN, row_factory=dict_row)

    assert export.main(['--out', str(out)], connect=connect, now=NOW) == 0
    printed = capsys.readouterr().out
    payload = out.read_bytes()
    assert f'sha256={hashlib.sha256(payload).hexdigest()}' in printed
    manifest = json.loads(payload)
    entries = _by_email(manifest)
    assert entries['owner@harbor.io']['status'] == 'needs_invitation'
    assert entries['owner@harbor.io']['primary_organization_id'] == org_id
    assert entries['stray@harbor.io']['status'] == 'skipped'
    assert 'NOT-EXPORTED' not in payload.decode()

    with psycopg.connect(_DSN, autocommit=True) as connection:
        after = _fingerprint(connection)
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
    assert after == before  # the export changed nothing
