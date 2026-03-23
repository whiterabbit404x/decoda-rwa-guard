from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'
SEED_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'seed.py'

sys.path.insert(0, str(REPO_ROOT))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to load module {name} from {path}.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def api_main():
    return _load_module('phase1_api_live_backend_main', API_MAIN_PATH)


@pytest.fixture()
def pilot_module():
    return _load_module('phase1_api_live_backend_pilot', PILOT_PATH)


@pytest.fixture()
def seed_module():
    return _load_module('phase1_api_live_backend_seed', SEED_PATH)


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_run_migrations_replays_idempotent_foundation_sql_and_records_versions_once(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY);')
    executed_statements: list[str] = []
    inserted_versions: list[str] = []

    class _Connection:
        def execute(self, statement, params=None):
            executed_statements.append(statement)
            normalized = ' '.join(str(statement).split())
            if 'SELECT version FROM schema_migrations' in normalized:
                return _Result([{'version': migration_path.name}])
            if 'INSERT INTO schema_migrations' in normalized:
                inserted_versions.append(params[0])
                return _Result()
            return _Result()

        def commit(self):
            executed_statements.append('COMMIT')

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)

    applied = pilot_module.run_migrations()

    assert applied == []
    assert any('CREATE TABLE IF NOT EXISTS users' in statement for statement in executed_statements)
    assert inserted_versions == [migration_path.name]


def test_seed_script_pilot_demo_runs_migrations_and_seeds_demo_login(seed_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        seed_module,
        'parse_args',
        lambda: type(
            'Args',
            (),
            {
                'pilot_demo': True,
                'demo_email': 'demo@decoda.app',
                'demo_password': 'PilotDemoPass123!',
                'demo_workspace': 'Decoda Demo Workspace',
                'demo_full_name': 'Decoda Demo User',
            },
        )(),
    )
    monkeypatch.setattr(seed_module, 'seed_local_state', lambda: {'service': 'api'})
    monkeypatch.setattr(seed_module, 'pretty_json', lambda value: str(value))
    monkeypatch.setattr(seed_module, 'run_migrations', lambda: calls.append(('migrate', None)) or ['0001_pilot_foundation.sql'])
    monkeypatch.setattr(
        seed_module,
        'seed_demo_workspace',
        lambda email, password, workspace, full_name: calls.append(('seed_demo_workspace', (email, password, workspace, full_name)))
        or {'seeded': True, 'email': email},
    )
    monkeypatch.setattr(seed_module, 'demo_seed_status', lambda email: {'present': True, 'status': 'present', 'email': email})

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        seed_module.seed()

    output = stdout.getvalue()
    assert ('migrate', None) in calls
    assert (
        'seed_demo_workspace',
        ('demo@decoda.app', 'PilotDemoPass123!', 'Decoda Demo Workspace', 'Decoda Demo User'),
    ) in calls
    assert 'Applied migrations before seeding live pilot data:' in output
    assert 'demo_seed_status' in output


def test_embedded_loader_isolates_top_level_app_package_namespaces(api_main) -> None:
    api_main.load_embedded_service_main.cache_clear()

    compliance_module = api_main.load_embedded_service_main('compliance-service')
    reconciliation_module = api_main.load_embedded_service_main('reconciliation-service')

    compliance_ns = api_main.embedded_service_namespace('compliance-service')
    reconciliation_ns = api_main.embedded_service_namespace('reconciliation-service')

    assert compliance_module.__name__ == f'{compliance_ns}.main'
    assert reconciliation_module.__name__ == f'{reconciliation_ns}.main'
    assert sys.modules[f'{compliance_ns}.engine'] is not sys.modules[f'{reconciliation_ns}.engine']
    assert 'app.schemas' not in sys.modules
    assert 'app.engine' not in sys.modules


def test_embedded_adapters_return_live_payloads_for_all_services(api_main) -> None:
    api_main.load_embedded_service_main.cache_clear()

    risk_payload = api_main.execute_embedded_risk_evaluation(api_main.DEFAULT_RISK_SAMPLE_REQUEST)
    threat_payload = api_main.execute_embedded_threat_dashboard()
    compliance_payload = api_main.execute_embedded_compliance_dashboard()
    resilience_payload = api_main.execute_embedded_resilience_dashboard()

    assert risk_payload['recommendation'] in {'ALLOW', 'REVIEW', 'BLOCK'}
    assert threat_payload['summary']['average_score'] >= 0
    assert compliance_payload['summary']['latest_transfer_decision']
    assert resilience_payload['summary']['backstop_decision']


def test_embedded_dashboard_fallback_still_works_when_loader_fails(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, 'load_embedded_service_main', lambda service_slug: (_ for _ in ()).throw(RuntimeError(f'{service_slug} unavailable')))

    payload = api_main.fetch_threat_dashboard()

    assert payload is None
    assert 'threat_engine' in api_main.DEPENDENCY_RUNTIME_STATUS
    assert api_main.DEPENDENCY_RUNTIME_STATUS['threat_engine']['last_used_mode'] == 'fallback'
    assert 'unavailable' in str(api_main.DEPENDENCY_RUNTIME_STATUS['threat_engine']['last_error'])
