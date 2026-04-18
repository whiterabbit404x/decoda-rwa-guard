from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / 'services' / 'api' / 'scripts' / 'rotate_exposed_token.py'

sys.path.insert(0, str(REPO_ROOT))


spec = importlib.util.spec_from_file_location('rotate_exposed_token_script', SCRIPT_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError('Unable to load rotate_exposed_token.py')
script = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = script
spec.loader.exec_module(script)


def test_workspace_from_user_prefers_explicit_workspace_override() -> None:
    workspace = script._workspace_from_user({'current_workspace': {'id': 'workspace-from-user'}}, 'workspace-override')
    assert workspace == 'workspace-override'


def test_workspace_from_user_raises_when_missing_workspace() -> None:
    try:
        script._workspace_from_user({}, None)
    except script.IncidentResponseError as exc:
        assert 'Unable to resolve workspace id' in str(exc)
    else:
        raise AssertionError('Expected IncidentResponseError for missing workspace context')


def test_summarize_audit_activity_flags_suspicious_events_within_window() -> None:
    now = datetime.now(timezone.utc)
    audit_logs = [
        {'action': 'auth.signin', 'created_at': (now - timedelta(minutes=5)).isoformat()},
        {'action': 'export.generate', 'created_at': (now - timedelta(minutes=3)).isoformat()},
        {'action': 'auth.password_reset', 'created_at': (now - timedelta(minutes=1)).isoformat()},
        {'action': 'target.update', 'created_at': (now - timedelta(minutes=500)).isoformat()},
    ]

    summary = script._summarize_audit_activity(audit_logs, lookback_minutes=30)

    assert summary['recent_event_count'] == 3
    assert summary['suspicious_event_count'] == 2
    suspicious_actions = {entry['action'] for entry in summary['suspicious_events']}
    assert suspicious_actions == {'export.generate', 'auth.password_reset'}


def test_run_marks_leaked_token_revoked_after_signout_all(monkeypatch) -> None:
    class _Args:
        api_url = 'http://localhost:8000'
        leaked_token = 'leaked-token'
        email = 'user@example.com'
        password = 'password'
        workspace_id = ''
        audit_limit = 50
        audit_lookback_minutes = 60
        timeout = 5
        output = ''

    responses = iter([
        script.ApiResponse(200, {'user': {'id': 'user-1', 'current_workspace': {'id': 'ws-1'}}}),
        script.ApiResponse(200, {'sessions': []}),
        script.ApiResponse(200, {'ok': True}),
        script.ApiResponse(401, {'detail': 'invalid token'}),
        script.ApiResponse(200, {'user': {'id': 'user-1'}}),
        script.ApiResponse(200, {'sessions': []}),
        script.ApiResponse(200, {'status': 'healthy'}),
        script.ApiResponse(200, {'audit_logs': []}),
    ])

    def _fake_json_request(**kwargs):
        return next(responses)

    monkeypatch.setattr(script, '_json_request', _fake_json_request)
    monkeypatch.setattr(
        script,
        '_signin_for_new_token',
        lambda api_url, *, email, password, timeout_seconds: (
            'new-token',
            {'id': 'user-1', 'current_workspace': {'id': 'ws-1'}},
        ),
    )

    report = script.run(_Args())

    revoke_step = report['steps']['revoke_rotate_sessions']
    assert revoke_step['leaked_token_post_rotation_status'] == 401
    assert revoke_step['leaked_token_revoked'] is True
