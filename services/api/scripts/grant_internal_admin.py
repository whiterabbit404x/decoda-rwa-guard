#!/usr/bin/env python3
"""
Grant or revoke Decoda internal (founder) admin access for one account.

Internal admin is the authorization behind /admin/customers. There is NO API
that writes it — a customer cannot grant it to themselves through any request,
role, or payload — so it is granted out of band, here, by someone who already
has database access.

Prints structured output with no secrets. Exits 0 on success.

Usage:
    python -m services.api.scripts.grant_internal_admin founder@decodasecurity.com
    python -m services.api.scripts.grant_internal_admin founder@decodasecurity.com --revoke
    python -m services.api.scripts.grant_internal_admin --list
"""
from __future__ import annotations

import sys

from services.api.app.pilot import pg_connection


def _column_exists(connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='users' AND column_name='is_internal_admin'",
    ).fetchone()
    return row is not None


def _list_admins(connection) -> int:
    rows = connection.execute(
        'SELECT email, last_sign_in_at FROM users WHERE is_internal_admin IS TRUE ORDER BY email',
    ).fetchall()
    if not rows:
        print('internal_admins=0')
        return 0
    print(f'internal_admins={len(rows)}')
    for row in rows:
        item = dict(row)
        print(f"  email={item['email']} last_sign_in_at={item.get('last_sign_in_at')}")
    return 0


def main(argv: list[str]) -> int:
    args = [value for value in argv if not value.startswith('--')]
    revoke = '--revoke' in argv
    listing = '--list' in argv

    if not listing and len(args) != 1:
        print(__doc__)
        return 2

    with pg_connection() as connection:
        if not _column_exists(connection):
            print('error=schema_not_migrated detail=run migration 0150 before granting internal admin')
            return 3

        if listing:
            return _list_admins(connection)

        email = args[0].strip().lower()
        user = connection.execute(
            'SELECT id, email FROM users WHERE lower(email) = %s', (email,),
        ).fetchone()
        if user is None:
            print(f'error=user_not_found email={email}')
            return 4

        connection.execute(
            'UPDATE users SET is_internal_admin = %s, updated_at = NOW() WHERE id = %s',
            (not revoke, dict(user)['id']),
        )
        connection.commit()
        print(f"action={'revoked' if revoke else 'granted'} email={email} scope=internal_admin")
        return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
