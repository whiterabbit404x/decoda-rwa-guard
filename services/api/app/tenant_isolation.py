"""
Canonical object-level authorization helpers for multi-tenant isolation.

Every object lookup by ID must include workspace scope. These helpers
provide consistent patterns for enforcing that rule and for failing
closed when ownership is ambiguous or missing.

Safe 404 vs 403 policy:
  404  Cross-workspace object lookup (object ID belongs to another workspace).
       Using 404 avoids revealing that the object exists in another workspace.
  403  Role-based action denied (object is visible but the role forbids the action).
       Already enforced by _require_workspace_admin / require_ops_rbac_guard.

Body/query workspace override rule:
  The authorized workspace_id is always derived from the authenticated session
  (Bearer token → user → workspace membership → workspace_context['workspace_id']).
  Any workspace_id supplied in a request body or query parameter MUST NOT
  override the session-derived workspace context.  Call
  reject_body_workspace_override() at mutation endpoints that accept a
  workspace_id field in the payload.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, status

_SAFE_IDENTIFIER_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


def validate_identifier(name: str, label: str = 'identifier') -> str:
    """Validate a SQL table or column identifier against a safe allowlist pattern.

    Raises ValueError for any name that does not match the safe pattern.
    Returns the validated name unchanged.
    """
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(
            f'Unsafe SQL {label}: {name!r}. '
            'Identifiers must start with a letter, contain only lowercase letters, digits, and underscores, and be at most 63 characters.'
        )
    return name


validate_table_name = validate_identifier


def require_object_in_workspace(
    connection: Any,
    *,
    table: str,
    object_id: str,
    workspace_id: str,
    id_col: str = 'id',
    deleted_col: str | None = None,
    object_label: str | None = None,
) -> dict[str, Any]:
    """Fetch a row by ID scoped to workspace_id.

    Returns the row as a dict if found.
    Raises HTTP 404 if the row is absent or belongs to a different workspace.
    Optionally filters out soft-deleted rows when deleted_col is provided.
    Never leaks cross-workspace object existence.
    """
    validate_identifier(table, 'table')
    validate_identifier(id_col, 'id_col')
    if deleted_col is not None:
        validate_identifier(deleted_col, 'deleted_col')
    extra = f' AND {deleted_col} IS NULL' if deleted_col else ''
    row = connection.execute(
        f'SELECT * FROM {table} WHERE {id_col} = %s AND workspace_id = %s{extra}',
        (object_id, workspace_id),
    ).fetchone()
    if row is None:
        label = object_label or table.rstrip('s').capitalize()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'{label} not found.',
        )
    return dict(row)


def assert_same_workspace(
    resource_workspace_id: str | None,
    request_workspace_id: str | None,
) -> None:
    """Assert that a fetched resource belongs to the requesting workspace.

    Raises HTTP 404 when the IDs differ or either value is absent/empty.
    Use this after a query that does not include a workspace_id filter,
    e.g. when joining across a pre-fetched foreign key.
    """
    if not resource_workspace_id or not request_workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Resource not found.')
    if str(resource_workspace_id).strip() != str(request_workspace_id).strip():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Resource not found.')


def reject_body_workspace_override(
    body_workspace_id: str | None,
    authorized_workspace_id: str,
) -> None:
    """Reject a request whose body workspace_id disagrees with the session context.

    If the request payload includes a workspace_id field that differs from
    the session-authorized workspace, raise HTTP 403.  This prevents
    body-level workspace substitution attacks.

    If body_workspace_id is None or empty string, the check is skipped
    (body did not include a workspace_id field).
    """
    if not body_workspace_id:
        return
    if str(body_workspace_id).strip() != str(authorized_workspace_id).strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='workspace_id in the request body does not match the authorized workspace.',
        )


def safe_not_found(detail: str = 'Resource not found.') -> HTTPException:
    """Return a safe HTTP 404 exception for cross-workspace object access.

    Prefer raising this rather than a 403 when the reason is that an object
    does not exist within the requesting workspace (to avoid object-existence
    disclosure to unauthorized callers).
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
