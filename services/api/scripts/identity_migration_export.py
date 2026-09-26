#!/usr/bin/env python3
"""Export RWA Guard's legacy accounts as a reviewed Decoda identity manifest.

READ-ONLY. Runs in a read-only transaction against the Guard database and
writes a JSON manifest that an operator reviews before anything changes. The
Decoda platform import (``npm run platform:import-legacy`` in decoda-website)
consumes it — dry run first, ``--apply`` only after review.

Every account is classified into exactly one bucket:

  matched            already linked to a Decoda (WorkOS) identity
  needs_invitation   active, verified address, member of at least one active
                     organization — gets a WorkOS invitation for the exact
                     legacy account (linked only when that invitation is accepted)
  conflict           needs a human: duplicate address (case-insensitive),
                     unverified or malformed address. NEVER merged or applied.
  skipped            suspended, no organization, every organization inactive,
                     or a reserved test domain

What is NOT exported: password hashes, MFA secrets or recovery codes, session
or reset tokens, API keys — nothing that authenticates anyone. Addresses and
names are exported because the invitation has to reach the person.

Usage:
    python -m services.api.scripts.identity_migration_export --out manifest.json
    python -m services.api.scripts.identity_migration_export --out -          # stdout
    python -m services.api.scripts.identity_migration_export --out m.json --database-url postgresql://…

Prints a summary and the manifest's SHA-256 (the platform import records it
on every link it creates). Exit 0 on success, 2 on usage errors, 3 when the
database is not migrated far enough (organization tenancy, migration 0150).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Iterable

MANIFEST_FORMAT = 'decoda.legacy_identity_manifest/v1'
PRODUCT = 'rwa_guard'

MATCHED = 'matched'
NEEDS_INVITATION = 'needs_invitation'
CONFLICT = 'conflict'
SKIPPED = 'skipped'
BUCKETS = (MATCHED, NEEDS_INVITATION, CONFLICT, SKIPPED)

# Highest first: the organization where a person holds the strongest role is
# their primary organization (the one their legacy link is recorded against).
ROLE_RANK = {'owner': 0, 'admin': 1, 'analyst': 2, 'viewer': 3}

# RFC 2606 / 6761 reserved names: never real customers.
_RESERVED_DOMAINS = ('example.com', 'example.net', 'example.org')
_RESERVED_SUFFIXES = ('.test', '.example', '.invalid', '.localhost', '.local')
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat().replace('+00:00', 'Z')
    return str(value)


def _reserved_domain(email: str) -> bool:
    domain = email.rsplit('@', 1)[-1]
    return domain in _RESERVED_DOMAINS or any(domain == s[1:] or domain.endswith(s) for s in _RESERVED_SUFFIXES)


def build_manifest(
    *,
    users: Iterable[dict[str, Any]],
    organizations: Iterable[dict[str, Any]],
    memberships: Iterable[dict[str, Any]],
    generated_at: datetime,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify every legacy account. Pure: rows in, manifest out."""
    orgs = {str(o['id']): dict(o) for o in organizations}
    users = [dict(u) for u in users]
    by_user: dict[str, list[dict[str, Any]]] = {}
    for m in memberships:
        org = orgs.get(str(m['organization_id']))
        if org is None:
            continue
        by_user.setdefault(str(m['user_id']), []).append({'organization_id': str(m['organization_id']), 'role': str(m['role'])})

    address_counts = Counter(str(u.get('email') or '').strip().lower() for u in users)

    entries: list[dict[str, Any]] = []
    for user in users:
        user_id = str(user['id'])
        email = str(user.get('email') or '').strip().lower()
        user_orgs = sorted(
            by_user.get(user_id, []),
            key=lambda m: (ROLE_RANK.get(m['role'], 9), str(orgs[m['organization_id']].get('name') or ''), m['organization_id']),
        )
        active_orgs = [m for m in user_orgs if str(orgs[m['organization_id']].get('status') or 'active') == 'active']
        linked = str(user.get('auth_provider') or '') == 'workos' and bool(user.get('external_subject'))

        reasons: list[str] = []
        if linked:
            bucket = MATCHED
            reasons.append('already_linked')
        elif user.get('suspended_at') is not None:
            bucket = SKIPPED
            reasons.append('suspended')
        elif not _EMAIL.match(email):
            bucket = CONFLICT
            reasons.append('invalid_email')
        elif address_counts[email] > 1:
            bucket = CONFLICT
            reasons.append('duplicate_email')
        elif _reserved_domain(email):
            bucket = SKIPPED
            reasons.append('reserved_test_domain')
        elif not user_orgs:
            bucket = SKIPPED
            reasons.append('no_organization')
        elif not active_orgs:
            bucket = SKIPPED
            reasons.append('organization_inactive')
        elif user.get('email_verified_at') is None:
            bucket = CONFLICT
            reasons.append('email_unverified')
        else:
            bucket = NEEDS_INVITATION

        primary = (active_orgs or user_orgs or [None])[0]
        entries.append(
            {
                'legacy_user_id': user_id,
                'email': email,
                'full_name': (str(user.get('full_name') or '').strip() or None),
                'status': bucket,
                'reasons': reasons,
                'auth_provider': str(user.get('auth_provider') or 'password'),
                'workos_user_id': str(user['external_subject']) if linked else None,
                'email_verified': user.get('email_verified_at') is not None,
                'mfa_enrolled': user.get('mfa_enabled_at') is not None,
                'internal_admin': bool(user.get('is_internal_admin')),
                'last_sign_in_at': _iso(user.get('last_sign_in_at')),
                'primary_organization_id': primary['organization_id'] if primary else None,
                'organizations': [{'legacy_organization_id': m['organization_id'], 'role': m['role']} for m in user_orgs],
            }
        )
    entries.sort(key=lambda e: (BUCKETS.index(e['status']), e['email'], e['legacy_user_id']))

    importable = {e['legacy_user_id'] for e in entries if e['status'] in (MATCHED, NEEDS_INVITATION)}
    org_entries = []
    for org_id, org in sorted(orgs.items(), key=lambda item: (str(item[1].get('name') or ''), item[0])):
        members = sorted(
            (
                {'legacy_user_id': user_id, 'role': m['role']}
                for user_id, user_memberships in by_user.items()
                for m in user_memberships
                if m['organization_id'] == org_id
            ),
            key=lambda m: (ROLE_RANK.get(m['role'], 9), m['legacy_user_id']),
        )
        org_entries.append(
            {
                'legacy_organization_id': org_id,
                'name': org.get('name'),
                'slug': org.get('slug'),
                'plan': org.get('plan'),
                'status': org.get('status'),
                'evaluation_expires_at': _iso(org.get('evaluation_expires_at')),
                'platform_organization_id': str(org['platform_organization_id']) if org.get('platform_organization_id') else None,
                'workos_organization_id': org.get('workos_organization_id'),
                'members': members,
                'importable_members': sum(1 for m in members if m['legacy_user_id'] in importable),
            }
        )

    summary = Counter(e['status'] for e in entries)
    return {
        'format': MANIFEST_FORMAT,
        'product': PRODUCT,
        'generated_at': _iso(generated_at),
        'source': source or {},
        'summary': {
            'users': {bucket: summary.get(bucket, 0) for bucket in BUCKETS},
            'organizations': len(org_entries),
            'organizations_to_import': sum(1 for o in org_entries if o['status'] == 'active' and o['importable_members']),
        },
        'organizations': org_entries,
        'users': entries,
    }


def serialize(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


# ── Database (read-only) ────────────────────────────────────────────────────


def _columns(connection: Any, table: str) -> set[str]:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    ).fetchall()
    return {str(dict(row)['column_name']) for row in rows}


def load_rows(connection: Any) -> dict[str, list[dict[str, Any]]]:
    """Read the three inputs inside a READ ONLY transaction."""
    connection.execute('SET TRANSACTION READ ONLY')
    user_columns = _columns(connection, 'users')
    org_columns = _columns(connection, 'organizations')
    if not org_columns or 'role' not in _columns(connection, 'organization_memberships'):
        raise RuntimeError('organization tenancy (migration 0150) is not applied')
    optional_user = [c for c in ('auth_provider', 'external_subject', 'suspended_at', 'is_internal_admin') if c in user_columns]
    optional_org = [c for c in ('platform_organization_id', 'workos_organization_id', 'evaluation_expires_at') if c in org_columns]
    users = connection.execute(
        'SELECT id, email, full_name, email_verified_at, mfa_enabled_at, last_sign_in_at'
        + ''.join(f', {c}' for c in optional_user)
        + ' FROM users ORDER BY created_at, id'
    ).fetchall()
    organizations = connection.execute(
        'SELECT id, name, slug, plan, status' + ''.join(f', {c}' for c in optional_org) + ' FROM organizations ORDER BY created_at, id'
    ).fetchall()
    memberships = connection.execute('SELECT organization_id, user_id, role FROM organization_memberships ORDER BY organization_id, user_id').fetchall()
    latest = connection.execute(
        "SELECT to_regclass('schema_migrations') IS NOT NULL AS present"
    ).fetchone()
    migration = None
    if latest and dict(latest)['present']:
        row = connection.execute('SELECT max(version) AS version FROM schema_migrations').fetchone()
        migration = dict(row).get('version') if row else None
    connection.rollback()
    return {
        'users': [dict(r) for r in users],
        'organizations': [dict(r) for r in organizations],
        'memberships': [dict(r) for r in memberships],
        'migration': migration,
    }


def _source_label(database_url: str) -> dict[str, Any]:
    """Where the export came from, without credentials."""
    from urllib.parse import urlsplit

    parts = urlsplit(database_url)
    return {'database_host': parts.hostname or 'local', 'database_name': (parts.path or '/').lstrip('/') or None}


def main(argv: list[str] | None = None, *, connect: Any = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description='Export the RWA Guard legacy identity manifest (read-only).')
    parser.add_argument('--out', required=True, help="Manifest path, or '-' for stdout.")
    parser.add_argument('--database-url', default=None, help='Guard database (default: DATABASE_URL).')
    args = parser.parse_args(argv)

    database_url = args.database_url or os.getenv('DATABASE_URL', '').strip()
    if not database_url and connect is None:
        print('error=missing_database_url detail=set DATABASE_URL or pass --database-url', file=sys.stderr)
        return 2
    if connect is None:
        import psycopg
        from psycopg.rows import dict_row

        def connect():  # noqa: E306 - small local factory
            return psycopg.connect(database_url, row_factory=dict_row)

    with connect() as connection:
        try:
            rows = load_rows(connection)
        except RuntimeError as exc:
            print(f'error=schema_not_migrated detail={exc}', file=sys.stderr)
            return 3
    source = {**(_source_label(database_url) if database_url else {}), 'migration': rows['migration']}
    manifest = build_manifest(
        users=rows['users'],
        organizations=rows['organizations'],
        memberships=rows['memberships'],
        generated_at=now or datetime.now(UTC),
        source=source,
    )
    payload = serialize(manifest)
    digest = hashlib.sha256(payload).hexdigest()
    if args.out == '-':
        sys.stdout.buffer.write(payload)
    else:
        with open(args.out, 'wb') as handle:
            handle.write(payload)
    report = sys.stderr if args.out == '-' else sys.stdout
    users = manifest['summary']['users']
    print(
        f"manifest={args.out} sha256={digest} format={MANIFEST_FORMAT} "
        f"organizations={manifest['summary']['organizations']} organizations_to_import={manifest['summary']['organizations_to_import']} "
        + ' '.join(f'{bucket}={users[bucket]}' for bucket in BUCKETS),
        file=report,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
