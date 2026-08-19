"""Regression coverage for the GET /workspace/invitations 500.

Production defect: ``list_workspace_invitations`` selected
``FROM workspace_invitations`` (no alias) but filtered with
``WHERE i.workspace_id = %s``. Postgres rejected the undefined alias with
``UndefinedTable: missing FROM-clause entry for table "i"`` and the endpoint
returned 500.

These tests drive the *real* handler through a fake connection that reproduces
Postgres alias resolution (an undefined table qualifier raises ``UndefinedTable``
exactly as the server did), so the suite fails if the stray alias is ever
reintroduced. They prove the list succeeds, workspace filtering is preserved,
another workspace's invitations cannot leak, and an empty list returns a valid
empty result rather than a 500. A structural guard on the source keeps the query
honest independent of the fake.
"""

from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg
import pytest

from services.api.app import pilot


# --- Faithful fake for the invitations SELECT -------------------------------
# Reproduces the single behavior that matters here: Postgres resolves a column's
# table qualifier against the FROM clause. `FROM workspace_invitations` (no alias)
# exposes only the qualifier `workspace_invitations`; aliasing it as `x` exposes
# only `x`. Any other qualifier -> UndefinedTable, exactly like the server.

_FROM_RE = re.compile(r'FROM\s+workspace_invitations(?:\s+(?:AS\s+)?(\w+))?', re.IGNORECASE)
_WHERE_RE = re.compile(r'WHERE\s+(?:(\w+)\.)?workspace_id\s*=\s*%s', re.IGNORECASE)

_SELECT_COLUMNS = ('id', 'email', 'role', 'status', 'expires_at', 'created_at', 'updated_at')


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """In-memory stand-in for a psycopg connection over workspace_invitations."""

    def __init__(self, invitations):
        self._invitations = list(invitations)
        self.executed = []

    def execute(self, query, params=None):
        sql = ' '.join(str(query).split())
        self.executed.append((sql, params))

        from_match = _FROM_RE.search(sql)
        where_match = _WHERE_RE.search(sql)
        if from_match is None or where_match is None:
            raise AssertionError(f'unexpected invitations query shape: {sql}')

        from_alias = from_match.group(1)
        valid_qualifiers = {from_alias} if from_alias else {'workspace_invitations'}
        where_qualifier = where_match.group(1)
        if where_qualifier is not None and where_qualifier not in valid_qualifiers:
            # This is precisely the production failure: `WHERE i.workspace_id`
            # against `FROM workspace_invitations` (no alias `i`).
            raise psycopg.errors.UndefinedTable(
                f'missing FROM-clause entry for table "{where_qualifier}"'
            )

        workspace_id = (params or [None])[0]
        rows = [dict(row) for row in self._invitations if row['workspace_id'] == workspace_id]
        rows.sort(key=lambda row: row['created_at'], reverse=True)
        projected = [{column: row[column] for column in _SELECT_COLUMNS} for row in rows]
        return _Result(projected)


def _invite(workspace_id, email, *, created_offset_minutes=0):
    now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    return {
        'id': f'inv-{workspace_id}-{email}',
        'workspace_id': workspace_id,
        'email': email,
        'role': 'viewer',
        'status': 'pending',
        'expires_at': now + timedelta(days=7),
        'created_at': now + timedelta(minutes=created_offset_minutes),
        'updated_at': now,
    }


def _bootstrap(monkeypatch, invitations, *, workspace_id):
    conn = _FakeConn(invitations)

    @contextmanager
    def _pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_admin',
        lambda connection, request: (
            {'id': 'user-1'},
            {'workspace_id': workspace_id, 'workspace': {'id': workspace_id, 'name': 'Acme'}},
        ),
    )
    return conn


def _request(workspace_id):
    return SimpleNamespace(headers={'x-workspace-id': workspace_id}, method='GET')


# --- The fake is a faithful model of the production failure ------------------

def test_fake_reproduces_postgres_alias_resolution():
    conn = _FakeConn([_invite('ws-a', 'a@example.com')])
    buggy = 'SELECT id FROM workspace_invitations WHERE i.workspace_id = %s ORDER BY created_at DESC'
    with pytest.raises(psycopg.errors.UndefinedTable):
        conn.execute(buggy, ('ws-a',))
    # The shipped fix (bare column) resolves cleanly.
    fixed = 'SELECT id, email, role, status, expires_at, created_at, updated_at FROM workspace_invitations WHERE workspace_id = %s ORDER BY created_at DESC'
    assert conn.execute(fixed, ('ws-a',)).fetchall()


# --- Behavior of the real handler -------------------------------------------

def test_list_succeeds_and_scopes_to_workspace(monkeypatch):
    invitations = [
        _invite('ws-a', 'older@example.com', created_offset_minutes=1),
        _invite('ws-a', 'newer@example.com', created_offset_minutes=5),
        _invite('ws-b', 'other@example.com'),
    ]
    conn = _bootstrap(monkeypatch, invitations, workspace_id='ws-a')

    result = pilot.list_workspace_invitations(_request('ws-a'))

    emails = [row['email'] for row in result['invitations']]
    # Succeeds (no 500), scoped to ws-a, newest first.
    assert emails == ['newer@example.com', 'older@example.com']
    assert result['workspace'] == {'id': 'ws-a', 'name': 'Acme'}

    # Workspace filtering is preserved in the query the handler actually ran.
    select = next(sql for sql, _ in conn.executed if sql.upper().startswith('SELECT'))
    assert 'WHERE workspace_id = %s' in select
    assert 'i.workspace_id' not in select
    params = next(params for sql, params in conn.executed if sql.upper().startswith('SELECT'))
    assert list(params) == ['ws-a']


def test_other_workspace_invitations_do_not_leak(monkeypatch):
    invitations = [
        _invite('ws-a', 'a@example.com'),
        _invite('ws-b', 'b1@example.com'),
        _invite('ws-b', 'b2@example.com'),
    ]
    _bootstrap(monkeypatch, invitations, workspace_id='ws-a')
    a_emails = [row['email'] for row in pilot.list_workspace_invitations(_request('ws-a'))['invitations']]
    assert a_emails == ['a@example.com']
    assert 'b1@example.com' not in a_emails
    assert 'b2@example.com' not in a_emails

    _bootstrap(monkeypatch, invitations, workspace_id='ws-b')
    b_emails = {row['email'] for row in pilot.list_workspace_invitations(_request('ws-b'))['invitations']}
    assert b_emails == {'b1@example.com', 'b2@example.com'}


def test_empty_invitation_list_returns_empty_not_500(monkeypatch):
    # ws-a has no invitations (only ws-b does).
    _bootstrap(monkeypatch, [_invite('ws-b', 'b@example.com')], workspace_id='ws-a')
    result = pilot.list_workspace_invitations(_request('ws-a'))
    assert result['invitations'] == []
    assert result['workspace'] == {'id': 'ws-a', 'name': 'Acme'}


# --- Structural guard on the shipped source ---------------------------------

def test_list_query_has_no_undefined_alias():
    source = ' '.join(inspect.getsource(pilot.list_workspace_invitations).split())
    # Preserves workspace isolation with a resolvable predicate.
    assert 'WHERE workspace_id = %s' in source
    # The stray alias that caused the production 500 must not return.
    assert 'i.workspace_id' not in source
