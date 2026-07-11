"""Worker startup-state tests: disabled vs configuration_error vs enabled.

A Procfile entry alone does not create a running Railway service; the worker runs
as its own dedicated service (railway-ai-triage-worker.json). These tests pin the
three startup states so the disabled state is clearly observable and an enabled
-but-misconfigured worker fails loudly (non-zero exit) instead of idling.
"""
from __future__ import annotations

from services.api.app import run_ai_triage_worker as worker


def _cfg(**over):
    base = {'enabled': False, 'provider': '', 'has_api_key': False, 'model': ''}
    base.update(over)
    return base


def test_disabled_state_when_flag_off():
    state, detail = worker.resolve_startup_state(_cfg(enabled=False, provider='openai'))
    assert state == 'disabled'
    assert detail == []


def test_configuration_error_when_openai_without_key():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='openai', has_api_key=False, model='m'))
    assert state == 'configuration_error'
    assert detail


def test_configuration_error_when_openai_without_model():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='openai', has_api_key=True, model=''))
    assert state == 'configuration_error'
    assert detail


def test_configuration_error_when_unknown_provider():
    state, _ = worker.resolve_startup_state(_cfg(enabled=True, provider='mystery', has_api_key=True, model='m'))
    assert state == 'configuration_error'


def test_enabled_state_when_openai_fully_configured():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='openai', has_api_key=True, model='gpt-x'))
    assert state == 'enabled'
    assert detail == []


def test_enabled_state_with_mock_needs_no_key():
    state, detail = worker.resolve_startup_state(_cfg(enabled=True, provider='mock', has_api_key=False, model=''))
    assert state == 'enabled'
    assert detail == []


def test_worker_once_disabled_does_not_crash(monkeypatch):
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'false')
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])
    assert worker.main() == 0


def test_worker_exits_nonzero_on_configuration_error(monkeypatch):
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'true')
    monkeypatch.setenv('AI_PROVIDER', 'openai')
    monkeypatch.delenv('AI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setenv('AI_MODEL_TRIAGE', '')
    monkeypatch.setattr('sys.argv', ['run_ai_triage_worker', '--once'])
    assert worker.main() == 1
