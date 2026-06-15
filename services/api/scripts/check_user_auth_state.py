#!/usr/bin/env python3
"""
Production-safe debug script: check auth state for a given email.

Prints structured output with no secrets or passwords exposed.
Exits 0 if user is found and can sign in, non-zero otherwise.

Usage:
    python -m services.api.scripts.check_user_auth_state decoda.guard@gmail.com
"""
from __future__ import annotations

import os
import sys
from services.api.app.pilot import ensure_pilot_schema, pg_connection

# Tables that must exist for sign-in to work.
_REQUIRED_AUTH_TABLES = (
    'users',
    'auth_sessions',
    'auth_tokens',
    'workspace_members',
    'workspaces',
)


def _check_auth_tables(conn) -> list[str]:
    """Return a list of required auth tables that are missing."""
    missing: list[str] = []
    for table in _REQUIRED_AUTH_TABLES:
        try:
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
                (table,),
            ).fetchone()
            if row is None:
                missing.append(table)
        except Exception:
            missing.append(table)
    return missing


def _check_env_vars() -> list[str]:
    """Check critical environment variables. Returns list of warnings (no secrets)."""
    warnings: list[str] = []
    database_url = (os.getenv('DATABASE_URL') or '').strip()
    if not database_url:
        warnings.append('DATABASE_URL is not set — cannot connect to the production database')
    elif database_url.startswith('sqlite'):
        warnings.append('DATABASE_URL points to SQLite — this is a local dev database, not production')

    auth_secret = (os.getenv('AUTH_TOKEN_SECRET') or '').strip()
    if not auth_secret:
        warnings.append('AUTH_TOKEN_SECRET is not set — session tokens cannot be signed, sign-in will fail at cookie_write_failed step')
    elif len(auth_secret) < 32:
        warnings.append('AUTH_TOKEN_SECRET is shorter than 32 characters — consider using a longer secret')

    app_url = (os.getenv('APP_PUBLIC_URL') or os.getenv('NEXT_PUBLIC_APP_URL') or '').strip()
    if not app_url:
        warnings.append('APP_PUBLIC_URL is not set — cookie domain may not be set correctly for rwa.decodasecurity.com')
    elif 'rwa.decodasecurity.com' not in app_url:
        warnings.append(f'APP_PUBLIC_URL={app_url!r} does not contain rwa.decodasecurity.com — verify this is the correct production URL')

    return warnings


def check_user_auth_state(email: str) -> int:
    email = email.strip().lower()
    print(f'checking email={email}')
    print()

    env_warnings = _check_env_vars()
    if env_warnings:
        print('ENV_WARNINGS')
        for w in env_warnings:
            print(f'  warning={w}')
        print()

    try:
        with pg_connection() as conn:
            ensure_pilot_schema(conn)

            missing_tables = _check_auth_tables(conn)
            if missing_tables:
                print('AUTH_TABLES_MISSING')
                for t in missing_tables:
                    print(f'  missing_table={t}')
                print('  action=run database migrations before attempting sign-in')
                print()
            else:
                print(f'AUTH_TABLES_OK tables_checked={len(_REQUIRED_AUTH_TABLES)}')
                print()

            user = conn.execute(
                '''
                SELECT id, email, email_verified_at,
                       password_hash IS NOT NULL AS has_password_hash,
                       last_sign_in_at, suspended_at, mfa_enabled_at, created_at
                FROM users WHERE email = %s
                ''',
                (email,),
            ).fetchone()
    except Exception as exc:
        print(f'ERROR db_error={exc}')
        return 2

    if user is None:
        print('RESULT user_not_found')
        print('  reason=user does not exist in the database')
        print('  action=create account at /sign-up or check email spelling')
        return 1

    user_id = str(user['id'])
    email_verified = bool(user['email_verified_at'])
    has_password = bool(user['has_password_hash'])
    suspended = bool(user['suspended_at'])
    mfa_enabled = bool(user['mfa_enabled_at'])

    print('RESULT user_found')
    print(f'  user_id={user_id}')
    print(f'  email={user["email"]}')
    print(f'  has_password_hash={has_password}')
    print(f'  email_verified={email_verified}')
    print(f'  email_verified_at={user["email_verified_at"]}')
    print(f'  suspended={suspended}')
    print(f'  mfa_enabled={mfa_enabled}')
    print(f'  last_sign_in_at={user["last_sign_in_at"]}')
    print(f'  created_at={user["created_at"]}')
    print()

    issues: list[str] = []
    if not has_password:
        issues.append('no_password_hash: password was never set or was cleared')
    if not email_verified:
        issues.append('email_unverified: user must verify email before signing in')
    if suspended:
        issues.append('account_suspended: account is suspended')

    try:
        with pg_connection() as conn:
            memberships = conn.execute(
                '''
                SELECT wm.role, wm.workspace_id, w.name AS workspace_name
                FROM workspace_members wm
                JOIN workspaces w ON w.id = wm.workspace_id
                WHERE wm.user_id = %s
                ORDER BY wm.created_at
                ''',
                (user_id,),
            ).fetchall()
    except Exception as exc:
        print(f'  workspace_memberships=error ({exc})')
        memberships = []

    if memberships:
        print(f'  workspace_memberships={len(memberships)}')
        for m in memberships:
            print(f'    workspace_id={m["workspace_id"]} name={m["workspace_name"]} role={m["role"]}')
    else:
        print('  workspace_memberships=0')
        issues.append('no_workspace: user has no workspace membership — onboarding incomplete')

    print()
    if issues:
        print('SIGN_IN_BLOCKED')
        for issue in issues:
            print(f'  issue={issue}')
        return 1

    print('SIGN_IN_ELIGIBLE: no blockers found — credentials should work if password is correct')
    return 0


if __name__ == '__main__':
    target_email = sys.argv[1] if len(sys.argv) > 1 else 'decoda.guard@gmail.com'
    sys.exit(check_user_auth_state(target_email))
