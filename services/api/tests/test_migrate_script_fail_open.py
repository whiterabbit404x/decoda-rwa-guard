from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / 'services' / 'api' / 'scripts' / 'migrate.py'

sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location('migrate_script', SCRIPT_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError('Unable to load migrate.py')
script = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = script
spec.loader.exec_module(script)


def test_migration_fail_open_defaults_true_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv('MIGRATION_FAIL_OPEN', raising=False)
    assert script._migration_fail_open_enabled() is True


def test_migration_fail_open_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv('MIGRATION_FAIL_OPEN', 'false')
    assert script._migration_fail_open_enabled() is False


def test_detects_database_bootstrap_unavailable_errors() -> None:
    error = RuntimeError('ERROR: Your account or project has exceeded the compute time quota.')
    assert script._is_database_bootstrap_unavailable_error(error) is True

    network_error = RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable')
    assert script._is_database_bootstrap_unavailable_error(network_error) is True

    unrelated = RuntimeError('syntax error at or near "CREATE"')
    assert script._is_database_bootstrap_unavailable_error(unrelated) is False
