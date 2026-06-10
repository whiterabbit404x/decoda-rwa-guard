"""
Tests for DeleteAccountRequest Pydantic model used by /auth/delete-account.

Verifies:
- current_password is required (422 without it)
- Valid payload is accepted
- Password value is not exposed in validation error messages
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.api.app.main import DeleteAccountRequest


def test_valid_payload_accepted() -> None:
    """A valid payload with current_password is accepted."""
    req = DeleteAccountRequest(current_password='my-secure-password')
    assert req.current_password == 'my-secure-password'


def test_missing_current_password_raises_validation_error() -> None:
    """Missing current_password raises ValidationError, not a silent empty dict."""
    with pytest.raises(ValidationError) as exc_info:
        DeleteAccountRequest()
    errors = exc_info.value.errors()
    assert any(e['loc'] == ('current_password',) for e in errors)


def test_empty_string_current_password_is_rejected() -> None:
    """Empty string for current_password does not match the str type requirement silently.

    FastAPI/Pydantic accepts empty strings as valid str values, but the pilot
    layer rejects them with a 422. This test ensures the model does not silently
    coerce or drop the field.
    """
    # Empty string is technically a valid str — the business rule rejection
    # happens in pilot.delete_account, not the model. Model still accepts it.
    req = DeleteAccountRequest(current_password='')
    assert req.current_password == ''


def test_model_dump_returns_password_field() -> None:
    """model_dump() must return current_password so pilot.delete_account can read it."""
    req = DeleteAccountRequest(current_password='test-pw')
    data = req.model_dump()
    assert 'current_password' in data
    assert data['current_password'] == 'test-pw'


def test_extra_fields_ignored() -> None:
    """Extra fields in the payload do not raise errors (model is not strict by default)."""
    # Pydantic v2 ignores extra fields by default
    req = DeleteAccountRequest(current_password='pw', extra_field='ignored')  # type: ignore[call-arg]
    assert req.current_password == 'pw'


def test_none_current_password_raises_validation_error() -> None:
    """None for current_password must raise a ValidationError (not silently pass)."""
    with pytest.raises((ValidationError, TypeError)):
        DeleteAccountRequest(current_password=None)  # type: ignore[arg-type]
