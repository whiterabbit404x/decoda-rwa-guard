"""Workspace-scoped persistence for the telemetry privacy policy (Phases 9 & 18).

:mod:`services.api.app.telemetry_privacy` is deliberately framework- and
database-free so workers, request handlers, and tests can all import it. This
module is the thin layer that loads one workspace's stored policy out of
``workspace_telemetry_privacy_policies`` (migration 0155) and turns it into a
:class:`~services.api.app.telemetry_privacy.PrivacyPolicy`.

Three properties matter here:

*Workspace-scoped.* Every query is keyed by ``workspace_id``. There is no
cross-tenant read, and the policy object carries the workspace id as its
pseudonymization ``scope``, so one workspace's pseudonyms never collide with
another's.

*Stricter only.* A stored row supplies exclusion and redaction rules that are
ADDED to Decoda's mandatory ones. The mandatory credential rules live in code
(``telemetry_privacy.MANDATORY_CREDENTIAL_KEYS``) and are never read from this
table, so nothing a customer can store reaches them. :func:`validate_policy_input`
rejects an attempt to name a mandatory credential field as an *allowed* metadata
field rather than silently accepting a rule that would not have applied.

*Fail-safe.* If the table is missing (migration not yet applied), the row is
absent, or the read raises, the caller gets
:data:`~services.api.app.telemetry_privacy.DEFAULT_POLICY` — every mandatory rule
still applies and only the customer's own additions are lost. A privacy read
never degrades to "no redaction".
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from fastapi import HTTPException, status

from services.api.app import telemetry_privacy as privacy

logger = logging.getLogger(__name__)

#: Permission required to read or change a workspace's privacy policy. Reuses the
#: existing security-settings permission rather than inventing a parallel one.
PRIVACY_MANAGE_PERMISSION = 'security.manage'

#: The audit action recorded on every change (Phase 18).
POLICY_CHANGED_ACTION = 'workspace.telemetry_privacy_policy_changed'

_SELECT_SQL = '''
SELECT workspace_id, excluded_fields, redacted_fields, allowed_metadata_fields,
       redact_private_ips, redact_emails, private_network_mode, version, updated_at
FROM workspace_telemetry_privacy_policies
WHERE workspace_id = %s
'''


def load_policy(connection: Any, workspace_id: str | None) -> privacy.PrivacyPolicy:
    """The effective policy for one workspace. Never raises, never fails open.

    ``workspace_id`` is also the pseudonymization scope, so the same private
    address observed in two workspaces yields two different pseudonyms.
    """
    scope = str(workspace_id or 'default')
    if not workspace_id or connection is None:
        return privacy.PrivacyPolicy.from_settings(None, scope=scope)
    try:
        row = connection.execute(_SELECT_SQL, (str(workspace_id),)).fetchone()
    except Exception:
        # Most often: this deployment has not applied migration 0155 yet. Decoda's
        # mandatory rules are unaffected; only the workspace's own additions are.
        logger.info(
            'telemetry_privacy_policy_unavailable workspace_id=%s result=defaults_applied',
            workspace_id,
        )
        return privacy.PrivacyPolicy.from_settings(None, scope=scope)
    return privacy.PrivacyPolicy.from_settings(_row_to_settings(row), scope=scope)


def _row_to_settings(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    return {
        'excluded_fields': data.get('excluded_fields') or [],
        'redacted_fields': data.get('redacted_fields') or [],
        'allowed_metadata_fields': data.get('allowed_metadata_fields') or [],
        'redact_private_ips': data.get('redact_private_ips'),
        'redact_emails': data.get('redact_emails'),
        'private_network_mode': data.get('private_network_mode'),
    }


def describe_policy(row: Any) -> dict[str, Any]:
    """The customer-facing view of a stored policy row (or of its absence).

    ``mandatory_rules_enforced`` is stated here rather than implied so the
    Settings screen can say truthfully that credential stripping applies whether
    or not the workspace has configured anything.
    """
    data = dict(row) if row else {}
    return {
        'configured': bool(row),
        'excluded_fields': list(data.get('excluded_fields') or []),
        'redacted_fields': list(data.get('redacted_fields') or []),
        'allowed_metadata_fields': list(data.get('allowed_metadata_fields') or []),
        'redact_private_ips': bool(data.get('redact_private_ips', True)),
        'redact_emails': bool(data.get('redact_emails', False)),
        'private_network_mode': str(data.get('private_network_mode') or privacy.NETWORK_MODE_MASK),
        'version': int(data.get('version') or 1),
        'supported_private_network_modes': list(privacy.NETWORK_MODES),
        'mandatory_rules_enforced': True,
        'mandatory_rule_summary': (
            'Authorization and proxy-authorization headers, cookies, bearer and '
            'access tokens, API keys, client and webhook secrets, passwords, '
            'private keys, seed phrases, and session identifiers are stripped '
            'before persistence regardless of this policy.'
        ),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _field_name_list(value: Any, *, field: str) -> list[str]:
    """One validated list of field names, normalized for comparison.

    Stored normalized (lowercase, alphanumeric only) because that is exactly the
    form :func:`telemetry_privacy.normalize_key` compares against — storing the
    customer's spelling would mean a rule for ``API-Key`` silently not matching a
    payload field named ``api_key``.
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = [part for part in value.split(',')]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={'code': 'INVALID_PRIVACY_FIELD_LIST', 'message': f'{field} must be a list of field names.'},
        )
    if len(items) > privacy.MAX_POLICY_FIELD_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                'code': 'TOO_MANY_PRIVACY_FIELDS',
                'message': f'{field} may list at most {privacy.MAX_POLICY_FIELD_NAMES} field names.',
            },
        )
    out: list[str] = []
    for item in items:
        text = str(item or '').strip()
        if not text:
            continue
        if len(text) > privacy.MAX_POLICY_FIELD_NAME_CHARS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    'code': 'PRIVACY_FIELD_NAME_TOO_LONG',
                    'message': f'{field} entries must be {privacy.MAX_POLICY_FIELD_NAME_CHARS} characters or fewer.',
                },
            )
        normalized = privacy.normalize_key(text)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def validate_policy_input(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate a policy submission server-side. Rejects weakening attempts.

    A customer may add exclusions and redactions freely. What is refused is an
    ``allowed_metadata_fields`` entry naming a mandatory credential field: that
    request reads as "keep my Authorization header", it would never be honoured,
    and accepting it would leave a stored rule that says otherwise.
    """
    data = payload if isinstance(payload, Mapping) else {}
    excluded = _field_name_list(data.get('excluded_fields'), field='excluded_fields')
    redacted = _field_name_list(data.get('redacted_fields'), field='redacted_fields')
    allowed = _field_name_list(data.get('allowed_metadata_fields'), field='allowed_metadata_fields')

    conflicting = sorted(name for name in allowed if name in privacy.MANDATORY_CREDENTIAL_KEYS)
    if conflicting:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                'code': 'MANDATORY_REDACTION_CANNOT_BE_DISABLED',
                'message': (
                    'These fields are always stripped as credentials and cannot be '
                    'added to allowed_metadata_fields: ' + ', '.join(conflicting) + '.'
                ),
                'fields': conflicting,
            },
        )

    mode = str(data.get('private_network_mode') or privacy.NETWORK_MODE_MASK).strip().lower()
    if mode not in privacy.NETWORK_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                'code': 'INVALID_PRIVATE_NETWORK_MODE',
                'message': 'private_network_mode must be one of ' + ', '.join(privacy.NETWORK_MODES) + '.',
            },
        )
    return {
        'excluded_fields': excluded,
        'redacted_fields': redacted,
        'allowed_metadata_fields': allowed,
        'redact_private_ips': _bool_field(data.get('redact_private_ips'), default=True),
        'redact_emails': _bool_field(data.get('redact_emails'), default=False),
        'private_network_mode': mode,
    }


def _bool_field(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'1', 'true', 'yes', 'on'}:
            return True
        if text in {'0', 'false', 'no', 'off'}:
            return False
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={'code': 'INVALID_PRIVACY_FLAG', 'message': 'Privacy flags must be true or false.'},
    )


# ---------------------------------------------------------------------------
# Change summary for the audit record (Phase 18)
# ---------------------------------------------------------------------------
def changed_settings(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """WHICH settings changed, described without reproducing sensitive values.

    A customer excludes a field because its CONTENT is sensitive; the field NAME
    is what is being configured and is safe to record. List *contents* are
    therefore summarised by count and by the names added/removed, and never by
    any value those fields carried.
    """
    summary: dict[str, Any] = {}
    for name in ('redact_private_ips', 'redact_emails', 'private_network_mode'):
        if before.get(name) != after.get(name):
            summary[name] = {'from': before.get(name), 'to': after.get(name)}
    for name in ('excluded_fields', 'redacted_fields', 'allowed_metadata_fields'):
        old = set(before.get(name) or [])
        new = set(after.get(name) or [])
        if old != new:
            summary[name] = {
                'added': sorted(new - old),
                'removed': sorted(old - new),
                'count_from': len(old),
                'count_to': len(new),
            }
    return summary


# ---------------------------------------------------------------------------
# Request-level handlers (wired in main.py)
# ---------------------------------------------------------------------------
def get_privacy_settings(request: Any) -> dict[str, Any]:
    """This workspace's telemetry privacy policy, plus what is always enforced."""
    from services.api.app import pilot

    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(
            connection, user['id'], request.headers.get('x-workspace-id'),
        )
        workspace_id = workspace_context['workspace_id']
        role = pilot._normalize_workspace_role(str(workspace_context['role']))
        try:
            row = connection.execute(_SELECT_SQL, (str(workspace_id),)).fetchone()
            available = True
        except Exception:
            # Migration 0155 not applied. Say so rather than rendering "no
            # exclusions configured", which would read as a choice the customer made.
            row, available = None, False
        described = describe_policy(row)
        described.update({
            'workspace_id': workspace_id,
            'policy_storage_available': available,
            'can_manage': pilot._workspace_permission_granted(
                connection, workspace_id, role, PRIVACY_MANAGE_PERMISSION,
            ),
        })
        return described


def update_privacy_settings(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Replace this workspace's privacy policy, version-guarded and audited."""
    from services.api.app import pilot

    pilot.require_live_mode()
    if 'expected_version' not in (payload or {}):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={'code': 'EXPECTED_VERSION_REQUIRED', 'message': 'expected_version is required.'},
        )
    try:
        expected_version = int((payload or {}).get('expected_version'))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={'code': 'EXPECTED_VERSION_INVALID', 'message': 'expected_version must be an integer.'},
        ) from None
    proposed = validate_policy_input(payload)
    with pilot.pg_connection() as connection:
        user, workspace_context = pilot._require_workspace_permission(
            connection, request, PRIVACY_MANAGE_PERMISSION,
        )
        return perform_policy_update(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            proposed=proposed,
            expected_version=expected_version,
            request=request,
        )


def perform_policy_update(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    proposed: dict[str, Any],
    expected_version: int,
    request: Any | None,
) -> dict[str, Any]:
    """Core policy write: seed-then-version-guarded-update, then audit.

    Same optimistic-concurrency shape as ``governance.perform_settings_update``,
    so a stale browser tab is rejected with 409 instead of silently overwriting a
    newer policy. The audit row records WHICH settings changed and never the
    contents of the fields being excluded.
    """
    from services.api.app import pilot

    try:
        current_row = connection.execute(_SELECT_SQL, (str(workspace_id),)).fetchone()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                'code': 'PRIVACY_POLICY_STORAGE_UNAVAILABLE',
                'message': 'Telemetry privacy policy storage is not available in this deployment yet.',
            },
        ) from exc
    before = describe_policy(current_row)
    now = pilot.utc_now()
    connection.execute(
        '''
        INSERT INTO workspace_telemetry_privacy_policies (workspace_id, version, created_at, updated_at)
        VALUES (%s, 1, %s, %s)
        ON CONFLICT (workspace_id) DO NOTHING
        ''',
        (workspace_id, now, now),
    )
    updated = connection.execute(
        '''
        UPDATE workspace_telemetry_privacy_policies
        SET excluded_fields = %s, redacted_fields = %s, allowed_metadata_fields = %s,
            redact_private_ips = %s, redact_emails = %s, private_network_mode = %s,
            version = version + 1, updated_at = %s, updated_by_user_id = %s
        WHERE workspace_id = %s AND version = %s
        RETURNING version
        ''',
        (
            proposed['excluded_fields'],
            proposed['redacted_fields'],
            proposed['allowed_metadata_fields'],
            proposed['redact_private_ips'],
            proposed['redact_emails'],
            proposed['private_network_mode'],
            now,
            user_id,
            workspace_id,
            expected_version,
        ),
    ).fetchone()
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': 'STALE_VERSION',
                'message': 'The privacy policy changed since you opened this page. Refresh before retrying.',
                'current_version': int(before.get('version') or 1),
            },
        )
    new_version = int(updated['version'])
    pilot.log_audit(
        connection,
        action=POLICY_CHANGED_ACTION,
        entity_type='workspace',
        entity_id=str(workspace_id),
        request=request,
        user_id=user_id,
        workspace_id=str(workspace_id),
        metadata={
            'changed': changed_settings(before, proposed),
            'version_from': expected_version,
            'version_to': new_version,
            'mandatory_rules_enforced': True,
        },
    )
    connection.commit()
    result = describe_policy({**proposed, 'version': new_version})
    result.update({'workspace_id': str(workspace_id), 'policy_storage_available': True, 'configured': True})
    return result
