from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope='module')
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_auth_diag_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for auth diagnostics tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('phase1_api_auth_diag_pilot', PILOT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load pilot module for auth diagnostics tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_details_reports_safe_config_booleans_when_auth_secret_is_missing(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('AUTH_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('JWT_SECRET', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setattr(api_main, 'ALLOWED_ORIGINS', ['https://web.decoda.example', 'https://ops.decoda.example'])
    monkeypatch.setattr(api_main, 'database_url', lambda: None)

    diagnostics = api_main.fixture_diagnostics()

    assert diagnostics['config'] == {
        'app_mode': 'production',
        'live_mode_enabled': False,
        'auth_token_secret_configured': False,
        'database_url_configured': False,
        'allowed_origins': ['https://web.decoda.example', 'https://ops.decoda.example'],
    }


def test_token_secret_raises_clear_error_when_auth_token_secret_is_missing(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('AUTH_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('JWT_SECRET', raising=False)

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.token_secret()

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == 'AUTH_TOKEN_SECRET is not configured.'
