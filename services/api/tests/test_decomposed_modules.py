"""Tests for the Phase 1 pilot.py decomposition modules.

Covers audit_helpers, startup_validation, and rbac_helpers to verify:
- Extracted functions behave identically to the originals
- Re-exports from pilot.py still work
- No circular imports
- Edge cases handled correctly
"""
from __future__ import annotations

import os
from datetime import timezone
from typing import Any
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# audit_helpers
# ---------------------------------------------------------------------------

def test_utc_now_returns_aware_datetime() -> None:
    from services.api.app.audit_helpers import utc_now
    dt = utc_now()
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc


def test_utc_now_iso_is_string() -> None:
    from services.api.app.audit_helpers import utc_now_iso
    iso = utc_now_iso()
    assert isinstance(iso, str)
    assert 'T' in iso


def test_json_safe_value_primitives() -> None:
    from services.api.app.audit_helpers import _json_safe_value
    assert _json_safe_value(None) is None
    assert _json_safe_value(True) is True
    assert _json_safe_value(42) == 42
    assert _json_safe_value('hello') == 'hello'


def test_json_safe_value_uuid() -> None:
    import uuid
    from services.api.app.audit_helpers import _json_safe_value
    uid = uuid.uuid4()
    result = _json_safe_value(uid)
    assert result == str(uid)


def test_json_safe_value_nested_dict() -> None:
    from services.api.app.audit_helpers import _json_safe_value
    import uuid
    uid = uuid.uuid4()
    obj = {'key': uid, 'nested': {'x': 1}}
    result = _json_safe_value(obj)
    assert result == {'key': str(uid), 'nested': {'x': 1}}


def test_json_dumps_returns_compact_json() -> None:
    from services.api.app.audit_helpers import _json_dumps
    result = _json_dumps({'a': 1, 'b': 'c'})
    assert ',' in result  # compact
    assert ' ' not in result  # no spaces in compact JSON


def test_log_audit_inserts_row() -> None:
    from services.api.app.audit_helpers import log_audit
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.return_value = None
    log_audit(
        mock_connection,
        action='test.action',
        entity_type='test',
        entity_id='entity-123',
        request=None,
        user_id='user-456',
        workspace_id='workspace-789',
        metadata={'key': 'value'},
    )
    assert mock_connection.execute.called
    # Verify the INSERT was called
    call_args = mock_connection.execute.call_args_list[-1][0]
    assert 'INSERT INTO audit_logs' in call_args[0]


def test_log_audit_handles_null_workspace() -> None:
    from services.api.app.audit_helpers import log_audit
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.return_value = None
    # Should not raise with None workspace_id
    log_audit(
        mock_connection,
        action='test.action',
        entity_type='test',
        entity_id='entity-123',
        request=None,
        user_id=None,
        workspace_id=None,
    )
    assert mock_connection.execute.called


def test_pilot_reexports_audit_helpers() -> None:
    from services.api.app import pilot
    assert hasattr(pilot, 'utc_now')
    assert hasattr(pilot, '_json_dumps')
    assert hasattr(pilot, '_json_safe_value')
    assert hasattr(pilot, 'log_audit')


# ---------------------------------------------------------------------------
# startup_validation
# ---------------------------------------------------------------------------

def test_env_flag_default_false() -> None:
    from services.api.app.startup_validation import env_flag
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('NONEXISTENT_FLAG_XYZ', None)
        assert env_flag('NONEXISTENT_FLAG_XYZ') is False


def test_env_flag_default_true() -> None:
    from services.api.app.startup_validation import env_flag
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('NONEXISTENT_FLAG_XYZ', None)
        assert env_flag('NONEXISTENT_FLAG_XYZ', default=True) is True


def test_env_flag_set_true() -> None:
    from services.api.app.startup_validation import env_flag
    for truthy in ('1', 'true', 'yes', 'on', 'TRUE', 'YES'):
        with patch.dict(os.environ, {'TEST_FLAG': truthy}):
            assert env_flag('TEST_FLAG') is True, f'expected True for {truthy!r}'


def test_env_flag_set_false() -> None:
    from services.api.app.startup_validation import env_flag
    for falsy in ('0', 'false', 'no', 'off', 'FALSE', 'NO'):
        with patch.dict(os.environ, {'TEST_FLAG': falsy}):
            assert env_flag('TEST_FLAG') is False, f'expected False for {falsy!r}'


def test_database_url_returns_none_when_unset() -> None:
    from services.api.app.startup_validation import database_url
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('DATABASE_URL', None)
        assert database_url() is None


def test_database_url_returns_value() -> None:
    from services.api.app.startup_validation import database_url
    with patch.dict(os.environ, {'DATABASE_URL': 'postgres://localhost/test'}):
        assert database_url() == 'postgres://localhost/test'


def test_resolve_db_backend_no_url() -> None:
    from services.api.app.startup_validation import resolve_db_backend
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('DATABASE_URL', None)
        assert resolve_db_backend() == 'sqlite'


def test_resolve_db_backend_postgres() -> None:
    from services.api.app.startup_validation import resolve_db_backend
    with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://user:pass@db.neon.tech/mydb'}):
        backend = resolve_db_backend()
        assert backend == 'postgres_hosted_neon'


def test_resolve_db_backend_local_postgres() -> None:
    from services.api.app.startup_validation import resolve_db_backend
    with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://localhost/test'}):
        assert resolve_db_backend() == 'postgres_local'


def test_live_mode_disabled_without_postgres() -> None:
    from services.api.app.startup_validation import live_mode_enabled
    with patch.dict(os.environ, {'LIVE_MODE_ENABLED': 'true', 'DATABASE_URL': ''}):
        # No postgres URL → live mode not actually enabled
        assert live_mode_enabled() is False


def test_live_mode_enabled_with_postgres() -> None:
    from services.api.app.startup_validation import live_mode_enabled
    with patch.dict(os.environ, {
        'LIVE_MODE_ENABLED': 'true',
        'DATABASE_URL': 'postgresql://localhost/test',
    }):
        assert live_mode_enabled() is True


def test_billing_provider_empty() -> None:
    from services.api.app.startup_validation import billing_provider
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('BILLING_PROVIDER', None)
        assert billing_provider() == ''


def test_billing_provider_paddle() -> None:
    from services.api.app.startup_validation import billing_provider
    with patch.dict(os.environ, {'BILLING_PROVIDER': 'PADDLE'}):
        assert billing_provider() == 'paddle'


def test_billing_runtime_status_not_configured() -> None:
    from services.api.app.startup_validation import billing_runtime_status
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('BILLING_PROVIDER', None)
        status = billing_runtime_status()
        assert status['status'] == 'not_configured'
        assert status['available'] is False


def test_pilot_reexports_startup_validation() -> None:
    from services.api.app import pilot
    assert hasattr(pilot, 'env_flag')
    assert hasattr(pilot, 'database_url')
    assert hasattr(pilot, 'live_mode_enabled')
    assert hasattr(pilot, 'billing_runtime_status')
    assert hasattr(pilot, 'validate_runtime_configuration')


# ---------------------------------------------------------------------------
# rbac_helpers
# ---------------------------------------------------------------------------

def test_normalize_workspace_role_owner() -> None:
    from services.api.app.rbac_helpers import _normalize_workspace_role
    assert _normalize_workspace_role('workspace_owner') == 'owner'
    assert _normalize_workspace_role('owner') == 'owner'


def test_normalize_workspace_role_admin() -> None:
    from services.api.app.rbac_helpers import _normalize_workspace_role
    assert _normalize_workspace_role('workspace_admin') == 'admin'
    assert _normalize_workspace_role('admin') == 'admin'


def test_normalize_workspace_role_analyst() -> None:
    from services.api.app.rbac_helpers import _normalize_workspace_role
    assert _normalize_workspace_role('workspace_member') == 'analyst'
    assert _normalize_workspace_role('analyst') == 'analyst'


def test_normalize_workspace_role_viewer() -> None:
    from services.api.app.rbac_helpers import _normalize_workspace_role
    assert _normalize_workspace_role('viewer') == 'viewer'


def test_normalize_workspace_role_invalid_raises() -> None:
    from services.api.app.rbac_helpers import _normalize_workspace_role
    from fastapi import HTTPException
    try:
        _normalize_workspace_role('superadmin')
        assert False, 'should have raised'
    except HTTPException as exc:
        assert exc.status_code == 400


def test_workspace_permission_granted_default_owner() -> None:
    from services.api.app.rbac_helpers import _workspace_permission_granted
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.return_value = None
    # Owner should have all permissions by default
    assert _workspace_permission_granted(mock_connection, 'ws-1', 'owner', 'monitoring.configure') is True
    assert _workspace_permission_granted(mock_connection, 'ws-1', 'owner', 'security.manage') is True


def test_workspace_permission_granted_default_viewer_none() -> None:
    from services.api.app.rbac_helpers import _workspace_permission_granted
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.return_value = None
    # Viewer has no permissions by default
    assert _workspace_permission_granted(mock_connection, 'ws-1', 'viewer', 'monitoring.configure') is False


def test_workspace_permission_granted_db_override() -> None:
    from services.api.app.rbac_helpers import _workspace_permission_granted
    mock_connection = MagicMock()
    # DB row says viewer IS granted monitoring.configure
    mock_row = {'granted': True}
    mock_connection.execute.return_value.fetchone.return_value = mock_row
    assert _workspace_permission_granted(mock_connection, 'ws-1', 'viewer', 'monitoring.configure') is True


def test_workspace_permission_granted_unknown_raises() -> None:
    from services.api.app.rbac_helpers import _workspace_permission_granted
    mock_connection = MagicMock()
    mock_connection.execute.return_value.fetchone.return_value = None
    try:
        _workspace_permission_granted(mock_connection, 'ws-1', 'owner', 'nonexistent.perm')
        assert False, 'should have raised'
    except ValueError:
        pass


def test_workspace_permissions_set_contents() -> None:
    from services.api.app.rbac_helpers import WORKSPACE_PERMISSIONS
    assert 'monitoring.configure' in WORKSPACE_PERMISSIONS
    assert 'security.manage' in WORKSPACE_PERMISSIONS
    assert 'identity.manage' in WORKSPACE_PERMISSIONS
    assert len(WORKSPACE_PERMISSIONS) >= 10


def test_pilot_reexports_rbac_helpers() -> None:
    from services.api.app import pilot
    assert hasattr(pilot, 'WORKSPACE_PERMISSIONS')
    assert hasattr(pilot, 'DEFAULT_ROLE_PERMISSIONS')
    assert hasattr(pilot, '_normalize_workspace_role')
    assert hasattr(pilot, '_workspace_permission_granted')
    # Verify behavior through pilot re-export
    assert pilot._normalize_workspace_role('owner') == 'owner'


def test_no_circular_imports() -> None:
    """Verify the three new modules can be imported independently without pilot.py."""
    import importlib
    # These should work without loading pilot.py first
    ah = importlib.import_module('services.api.app.audit_helpers')
    sv = importlib.import_module('services.api.app.startup_validation')
    rh = importlib.import_module('services.api.app.rbac_helpers')
    assert ah.utc_now is not None
    assert sv.env_flag is not None
    assert rh._normalize_workspace_role is not None
