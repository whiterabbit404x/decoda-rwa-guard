"""Screen 9: evidence-export authorization regression tests.

Locks in the backend half of the "Create Evidence Package button is disabled for a
workspace admin" fix:

  * Workspace owners/admins receive the canonical ``evidence.export`` permission.
  * A MISSING ``workspace_role_permissions`` row falls back to the role default
    (granted for owner/admin) — it must never default to *denied*.
  * An explicit ``granted`` row overrides the default in either direction.
  * Read-only (viewer) roles are denied.
  * Direct unauthorized creation still raises 403 PERMISSION_DENIED.
  * The role is resolved from server-side workspace membership (``resolve_workspace``),
    never from a browser-supplied field.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


# ── Helpers ──────────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _RbacConnection:
    """Connection that answers each RBAC-relevant query deterministically.

    ``permission_row`` is what the ``workspace_role_permissions`` lookup returns
    (``None`` => no override row => role default applies).
    """

    def __init__(self, permission_row=None):
        self._permission_row = permission_row

    def execute(self, stmt, params=None):
        s = ' '.join(str(stmt).split())
        if 'workspace_role_permissions' in s:
            return _Row(row=self._permission_row)
        if 'workspace_auth_policies' in s:
            return _Row(row=None)  # -> mfa_enforcement 'optional'
        if 'workspace_retention_policies' in s:
            return _Row(rows=[])
        if 'export_jobs' in s:
            return _Row(rows=[])
        return _Row(row=None, rows=[])

    def commit(self):
        pass


def _request(workspace_id: str = 'ws-1', headers: dict | None = None):
    hdrs = {'x-workspace-id': workspace_id}
    hdrs.update(headers or {})
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda key, default=None: hdrs.get(key, default)),
        query_params=SimpleNamespace(get=lambda key, default=None: None),
    )


def _patch_auth(monkeypatch, *, role: str, workspace_id: str = 'ws-1', permission_row=None):
    """Patch the shared auth surface so a call resolves to the given membership role."""
    conn = _RbacConnection(permission_row=permission_row)

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda *_: {'workspace_id': workspace_id, 'role': role},
    )
    return conn


# ── _workspace_permission_granted: missing row must NOT default to denied ─────

def test_owner_missing_permission_row_defaults_to_granted():
    conn = _RbacConnection(permission_row=None)
    assert pilot._workspace_permission_granted(conn, 'ws-1', 'owner', 'evidence.export') is True


def test_admin_missing_permission_row_defaults_to_granted():
    conn = _RbacConnection(permission_row=None)
    assert pilot._workspace_permission_granted(conn, 'ws-1', 'admin', 'evidence.export') is True


def test_viewer_missing_permission_row_defaults_to_denied():
    conn = _RbacConnection(permission_row=None)
    assert pilot._workspace_permission_granted(conn, 'ws-1', 'viewer', 'evidence.export') is False


def test_explicit_denied_row_overrides_admin_default():
    """A workspace policy that explicitly revokes the permission is honored."""
    conn = _RbacConnection(permission_row={'granted': False})
    assert pilot._workspace_permission_granted(conn, 'ws-1', 'admin', 'evidence.export') is False


def test_explicit_granted_row_overrides_viewer_default():
    """A workspace policy that explicitly grants the permission is honored."""
    conn = _RbacConnection(permission_row={'granted': True})
    assert pilot._workspace_permission_granted(conn, 'ws-1', 'viewer', 'evidence.export') is True


def test_role_alias_is_normalized_before_lookup():
    """A membership role stored as an alias (workspace_admin) still grants via the
    canonical 'admin' default when no override row exists."""
    conn = _RbacConnection(permission_row=None)
    assert pilot._workspace_permission_granted(conn, 'ws-1', 'workspace_admin', 'evidence.export') is True


def test_unknown_permission_raises_value_error():
    conn = _RbacConnection(permission_row=None)
    with pytest.raises(ValueError):
        pilot._workspace_permission_granted(conn, 'ws-1', 'owner', 'not.a.real.permission')


def test_canonical_role_defaults_include_evidence_export_for_owner_and_admin():
    """The canonical default map is the single source of truth for role → permission."""
    assert 'evidence.export' in pilot.DEFAULT_ROLE_PERMISSIONS['owner']
    assert 'evidence.export' in pilot.DEFAULT_ROLE_PERMISSIONS['admin']
    assert 'evidence.export' not in pilot.DEFAULT_ROLE_PERMISSIONS['viewer']


# ── GET /exports: can_export reflects the server-resolved role ────────────────

def test_list_exports_can_export_true_for_owner(monkeypatch):
    _patch_auth(monkeypatch, role='owner')
    result = pilot.list_exports(_request())
    assert result['can_export'] is True


def test_list_exports_can_export_true_for_admin_without_override_row(monkeypatch):
    # No explicit workspace_role_permissions row -> admin default grants it.
    _patch_auth(monkeypatch, role='admin', permission_row=None)
    result = pilot.list_exports(_request())
    assert result['can_export'] is True


def test_list_exports_can_export_false_for_viewer(monkeypatch):
    _patch_auth(monkeypatch, role='viewer')
    result = pilot.list_exports(_request())
    assert result['can_export'] is False


def test_list_exports_admin_explicitly_revoked_is_denied(monkeypatch):
    _patch_auth(monkeypatch, role='admin', permission_row={'granted': False})
    result = pilot.list_exports(_request())
    assert result['can_export'] is False


def test_list_exports_role_comes_from_membership_not_browser(monkeypatch):
    """A browser-supplied role header must not influence can_export — only the
    server-resolved membership role (viewer here) counts."""
    _patch_auth(monkeypatch, role='viewer')
    spoofed = _request(headers={'x-workspace-role': 'owner', 'x-role': 'admin'})
    result = pilot.list_exports(spoofed)
    assert result['can_export'] is False


# ── Creation gate: unauthorized direct API requests still return 403 ──────────

def test_require_permission_denies_viewer_with_403(monkeypatch):
    conn = _patch_auth(monkeypatch, role='viewer')
    with pytest.raises(HTTPException) as exc_info:
        pilot._require_workspace_permission(conn, _request(), 'evidence.export')
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'PERMISSION_DENIED'
    assert exc_info.value.detail['permission'] == 'evidence.export'


def test_require_permission_allows_owner(monkeypatch):
    conn = _patch_auth(monkeypatch, role='owner')
    user, workspace_context = pilot._require_workspace_permission(conn, _request(), 'evidence.export')
    assert workspace_context['role'] == 'owner'
    assert workspace_context['workspace_id'] == 'ws-1'


def test_require_permission_allows_admin_with_missing_override_row(monkeypatch):
    # Regression: a missing override row must not deny an admin the creation gate.
    conn = _patch_auth(monkeypatch, role='admin', permission_row=None)
    user, workspace_context = pilot._require_workspace_permission(conn, _request(), 'evidence.export')
    assert workspace_context['workspace_id'] == 'ws-1'


def test_require_permission_denies_admin_when_explicitly_revoked(monkeypatch):
    conn = _patch_auth(monkeypatch, role='admin', permission_row={'granted': False})
    with pytest.raises(HTTPException) as exc_info:
        pilot._require_workspace_permission(conn, _request(), 'evidence.export')
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'PERMISSION_DENIED'
