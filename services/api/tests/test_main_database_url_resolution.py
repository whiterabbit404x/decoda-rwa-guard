from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


@pytest.fixture()
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_main_db_resolution', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for database URL resolution tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_masked_database_url_uses_pilot_database_url_not_local_registry_url(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, 'pilot_database_url', lambda: None)
    monkeypatch.setattr(api_main, 'local_database_url', lambda: 'sqlite:////tmp/local-registry.db')
    assert api_main.masked_database_url() is None

    monkeypatch.setattr(api_main, 'pilot_database_url', lambda: 'postgresql://pilot:pilot@db.example.test:5432/app')
    assert api_main.masked_database_url() == '[configured]'


def test_health_database_url_configured_tracks_pilot_database_url(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, 'pilot_database_url', lambda: None)

    payload = api_main.health()

    assert payload['database_url_configured'] is False
