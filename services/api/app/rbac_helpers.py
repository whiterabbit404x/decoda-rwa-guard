"""RBAC role/permission helpers extracted from pilot.py.

Contains role constants, permission grants, and workspace role normalization.
No reverse dependency on pilot.py.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

ROLE_VALUES: frozenset[str] = frozenset({
    'owner', 'admin', 'analyst', 'viewer',
    'workspace_owner', 'workspace_admin', 'workspace_member',
})

ROLE_CANONICAL_MAP: dict[str, str] = {
    'workspace_owner': 'owner',
    'workspace_admin': 'admin',
    'workspace_member': 'analyst',
    'owner': 'owner',
    'admin': 'admin',
    'analyst': 'analyst',
    'viewer': 'viewer',
}

WORKSPACE_PERMISSIONS: frozenset[str] = frozenset({
    'monitoring.configure',
    'evidence.export',
    'members.manage',
    'webhooks.manage',
    'incidents.decide',
    'response.propose',
    'response.approve',
    'response.execute',
    'identity.manage',
    'security.manage',
})

DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    'owner': frozenset(WORKSPACE_PERMISSIONS),
    'admin': frozenset(WORKSPACE_PERMISSIONS),
    'analyst': frozenset({'monitoring.configure', 'evidence.export', 'incidents.decide', 'response.propose'}),
    'viewer': frozenset(),
}


def _normalize_workspace_role(role: str) -> str:
    normalized = ROLE_CANONICAL_MAP.get(role.strip().lower(), '')
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid workspace role.')
    return normalized


def _workspace_permission_granted(
    connection: Any,
    workspace_id: str,
    role: str,
    permission: str,
) -> bool:
    if permission not in WORKSPACE_PERMISSIONS:
        raise ValueError(f'Unknown workspace permission: {permission}')
    canonical_role = _normalize_workspace_role(role)
    row = connection.execute(
        'SELECT granted FROM workspace_role_permissions WHERE workspace_id = %s AND role = %s AND permission = %s',
        (workspace_id, canonical_role, permission),
    ).fetchone()
    if row is not None:
        return bool(row['granted'])
    return permission in DEFAULT_ROLE_PERMISSIONS[canonical_role]
