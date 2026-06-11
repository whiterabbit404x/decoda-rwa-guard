"""
Tests that the Create Target form prevents double-submit and the backend 409 is surfaced.

Frontend guard:  targets-manager.tsx must disable the button while a request is in
flight (creating state).  Backend guard:  create_target() returns 409 on duplicate.
"""
from __future__ import annotations

import pathlib

_TSX_SRC = pathlib.Path('apps/web/app/targets-manager.tsx').read_text()
_PILOT_SRC = pathlib.Path('services/api/app/pilot.py').read_text()


def _create_target_tsx() -> str:
    start = _TSX_SRC.index('async function createTarget(')
    end = _TSX_SRC.find('\n  async function', start + 1)
    return _TSX_SRC[start:end] if end != -1 else _TSX_SRC[start:]


def _create_target_pilot() -> str:
    start = _PILOT_SRC.index('def create_target(')
    end = _PILOT_SRC.find('\ndef ', start + 1)
    return _PILOT_SRC[start:end] if end != -1 else _PILOT_SRC[start:]


# ---------------------------------------------------------------------------
# Frontend: in-flight guard
# ---------------------------------------------------------------------------

def test_frontend_guard_checks_creating_state_before_fetch():
    src = _create_target_tsx()
    creating_guard_pos = src.index('if (creating) return')
    fetch_pos = src.index("fetch('/api/targets'")
    assert creating_guard_pos < fetch_pos, (
        "creating guard must appear before the fetch call to block double-submit"
    )


def test_frontend_sets_creating_true_before_fetch():
    src = _create_target_tsx()
    set_creating_true_pos = src.index('setCreating(true)')
    fetch_pos = src.index("fetch('/api/targets'")
    assert set_creating_true_pos < fetch_pos, (
        "setCreating(true) must be called before the fetch to disable the button immediately"
    )


def test_frontend_resets_creating_in_finally():
    src = _create_target_tsx()
    assert 'finally' in src, "createTarget must use a finally block to reset creating state"
    finally_pos = src.index('finally')
    set_creating_false_pos = src.index('setCreating(false)')
    assert set_creating_false_pos > finally_pos, (
        "setCreating(false) must be inside the finally block so it always resets"
    )


def test_frontend_button_disabled_while_creating():
    assert 'disabled={creating}' in _TSX_SRC, (
        "Create target button must carry disabled={creating} to prevent double-click"
    )


def test_frontend_button_shows_creating_label():
    assert "'Creating...'" in _TSX_SRC or '"Creating..."' in _TSX_SRC, (
        "Button label must change to 'Creating...' while in-flight so the user knows a request is pending"
    )


# ---------------------------------------------------------------------------
# Frontend: 409 conflict surfaced to user
# ---------------------------------------------------------------------------

def test_frontend_handles_409_conflict():
    src = _create_target_tsx()
    assert 'response.status === 409' in src or "status_code=status.HTTP_409_CONFLICT" in src or '409' in src, (
        "createTarget must detect a 409 response and surface an error message"
    )


def test_frontend_409_sets_message():
    src = _create_target_tsx()
    assert 'setMessage' in src, "createTarget must call setMessage to show the 409 error"
    # The handler must show a user-facing message when a duplicate is rejected
    assert 'already exists' in src or 'payload.detail' in src, (
        "409 handler must display a meaningful error ('already exists' or payload.detail)"
    )


# ---------------------------------------------------------------------------
# Backend: duplicate rejection (sanity re-check)
# ---------------------------------------------------------------------------

def test_backend_create_target_rejects_duplicate_with_409():
    src = _create_target_pilot()
    assert 'HTTP_409_CONFLICT' in src


def test_backend_duplicate_check_covers_all_four_dimensions():
    src = _create_target_pilot()
    assert 'workspace_id = %s AND asset_id = %s AND name = %s AND target_type = %s' in src
