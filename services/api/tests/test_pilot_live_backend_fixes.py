from __future__ import annotations

import importlib.util
import io
import json
import sys
import uuid
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

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


def test_run_migrations_applies_only_pending_versions(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY);')
    executed_statements: list[str] = []

    class _Connection:
        def execute(self, statement, params=None):
            executed_statements.append(statement)
            normalized = ' '.join(str(statement).split())
            if 'SELECT pg_try_advisory_lock' in normalized:
                return _Result([{'locked': True}])
            if 'SELECT pg_advisory_unlock' in normalized:
                return _Result()
            if 'SELECT version FROM schema_migrations' in normalized:
                return _Result([{'version': migration_path.name}])
            return _Result()

        def commit(self):
            executed_statements.append('COMMIT')

        def rollback(self):
            executed_statements.append('ROLLBACK')

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)

    applied = pilot_module.run_migrations()

    assert applied == []
    assert not any('CREATE TABLE IF NOT EXISTS users' in str(statement) for statement in executed_statements)
    assert not any('INSERT INTO schema_migrations' in str(statement) for statement in executed_statements)
    assert any('SELECT pg_advisory_unlock' in statement for statement in executed_statements)


def test_run_migrations_skips_and_waits_for_readiness_when_lock_not_acquired(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY);')
    executed_statements: list[str] = []

    class _Connection:
        def execute(self, statement, params=None):
            executed_statements.append(str(statement))
            normalized = ' '.join(str(statement).split())
            if 'SELECT pg_try_advisory_lock' in normalized:
                return _Result([{'locked': False}])
            if 'SELECT pg_advisory_unlock' in normalized:
                raise AssertionError('unlock should not run if lock was never acquired')
            if 'SELECT version FROM schema_migrations' in normalized:
                return _Result([{'version': migration_path.name}])
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            return _Result()

        def rollback(self):
            executed_statements.append('ROLLBACK')

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)
    monkeypatch.setenv('MIGRATION_LOCK_WAIT_SECONDS', '0')
    monkeypatch.setenv('MIGRATION_READINESS_WAIT_SECONDS', '0')

    applied = pilot_module.run_migrations()

    assert applied == []
    assert all('CREATE TABLE IF NOT EXISTS users' not in statement for statement in executed_statements)


def test_run_migrations_retries_transient_deadlock_before_success(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY);')
    executed_statements: list[str] = []
    deadlock_injected = {'raised': False}
    rollbacks = {'count': 0}
    commits = {'count': 0}

    class DeadlockDetected(Exception):
        pass

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed_statements.append(normalized)
            if 'SELECT pg_try_advisory_lock' in normalized:
                return _Result([{'locked': True}])
            if 'SELECT pg_advisory_unlock' in normalized:
                return _Result()
            if 'SELECT version FROM schema_migrations' in normalized:
                return _Result([])
            if 'CREATE TABLE IF NOT EXISTS users' in normalized and not deadlock_injected['raised']:
                deadlock_injected['raised'] = True
                raise DeadlockDetected('deadlock detected')
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            return _Result()

        def rollback(self):
            rollbacks['count'] += 1

        def commit(self):
            commits['count'] += 1

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)
    monkeypatch.setenv('MIGRATION_RETRY_ATTEMPTS', '2')
    monkeypatch.setenv('MIGRATION_RETRY_BACKOFF_SECONDS', '0')

    applied = pilot_module.run_migrations()

    assert applied == [migration_path.name]
    assert deadlock_injected['raised'] is True
    assert rollbacks['count'] >= 2
    assert commits['count'] == 1
    assert sum(1 for statement in executed_statements if 'CREATE TABLE IF NOT EXISTS users' in statement) == 2


def test_run_migrations_rolls_back_before_unlock_when_migration_fails(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE will_fail;')
    events: list[str] = []

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT pg_try_advisory_lock' in normalized:
                events.append('lock')
                return _Result([{'locked': True}])
            if 'SELECT pg_advisory_unlock' in normalized:
                events.append('unlock')
                return _Result()
            if 'CREATE TABLE will_fail' in normalized:
                events.append('migration')
                raise ValueError('boom')
            return _Result()

        def rollback(self):
            events.append('rollback')

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)
    monkeypatch.setenv('MIGRATION_RETRY_ATTEMPTS', '1')

    with pytest.raises(pilot_module.MigrationExecutionError):
        pilot_module.run_migrations()

    assert events.index('rollback') < events.index('unlock')


def test_run_migrations_raises_when_lock_not_acquired_and_readiness_times_out(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY);')

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT pg_try_advisory_lock' in normalized:
                return _Result([{'locked': False}])
            if 'SELECT version FROM schema_migrations' in normalized:
                return _Result([])
            return _Result()

        def rollback(self):
            return None

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)
    monkeypatch.setenv('MIGRATION_LOCK_WAIT_SECONDS', '0')
    monkeypatch.setenv('MIGRATION_READINESS_WAIT_SECONDS', '0')

    with pytest.raises(RuntimeError, match='schema readiness was not reached'):
        pilot_module.run_migrations()


def test_run_migrations_retries_deadlock_and_exits_when_unrecoverable(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY);')
    rollbacks = {'count': 0}

    class DeadlockDetected(Exception):
        pass

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT pg_try_advisory_lock' in normalized:
                return _Result([{'locked': True}])
            if 'SELECT pg_advisory_unlock' in normalized:
                return _Result()
            if 'CREATE TABLE IF NOT EXISTS users' in normalized:
                raise DeadlockDetected('deadlock detected')
            return _Result()

        def rollback(self):
            rollbacks['count'] += 1

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)
    monkeypatch.setenv('MIGRATION_RETRY_ATTEMPTS', '2')
    monkeypatch.setenv('MIGRATION_RETRY_BACKOFF_SECONDS', '0')

    with pytest.raises(pilot_module.MigrationExecutionError):
        pilot_module.run_migrations()

    assert rollbacks['count'] >= 2


def test_run_migrations_verifies_core_tables_after_applying_sql(pilot_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    migration_path = tmp_path / '0001_pilot_foundation.sql'
    migration_path.write_text('CREATE TABLE IF NOT EXISTS users (id UUID PRIMARY KEY);')
    commits = {'count': 0}

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT pg_try_advisory_lock' in normalized:
                return _Result([{'locked': True}])
            if 'SELECT pg_advisory_unlock' in normalized:
                return _Result()
            if 'SELECT version FROM schema_migrations' in normalized:
                return _Result([])
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([{'table_name': 'workspaces'}])
            return _Result()

        def commit(self):
            commits['count'] += 1

        def rollback(self):
            return None

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'migration_dir', lambda: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.run_migrations()

    assert commits['count'] == 1
    assert 'Pilot auth schema is not initialized.' in str(exc_info.value.detail)
    assert 'workspaces' in str(exc_info.value.detail)


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
    monkeypatch.setattr(seed_module, 'pilot_schema_status', lambda: {'ready': True, 'status': 'ready', 'missing_tables': []})
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
    assert 'pilot_schema_status' in output
    assert 'demo_seed_status' in output




def test_seed_demo_workspace_creates_demo_user_workspace_and_membership(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, object]] = []

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            if 'SELECT id, current_workspace_id FROM users WHERE email = %s' in normalized:
                return _Result([])
            if 'SELECT wm.workspace_id, w.name, w.slug' in normalized:
                return _Result([])
            if 'SELECT 1 FROM workspaces WHERE slug = %s' in normalized:
                return _Result([])
            if 'SELECT id, email, full_name, current_workspace_id, created_at, updated_at, last_sign_in_at, email_verified_at, mfa_enabled_at FROM users WHERE id = %s' in normalized:
                return _Result([{'id': 'user-1', 'email': 'demo@decoda.app', 'full_name': 'Decoda Demo User', 'current_workspace_id': 'workspace-1', 'created_at': 'now', 'updated_at': 'now', 'last_sign_in_at': 'now'}])
            if 'SELECT wm.workspace_id, wm.role, wm.created_at, w.name, w.slug FROM workspace_members wm JOIN workspaces w ON w.id = wm.workspace_id WHERE wm.user_id = %s' in normalized:
                return _Result([{'workspace_id': 'workspace-1', 'role': 'owner', 'created_at': 'now', 'name': 'Decoda Demo Workspace', 'slug': 'decoda-demo-workspace'}])
            return _Result()

        def commit(self):
            executed.append(('COMMIT', None))

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'build_user_response', lambda connection, user_id: {'id': user_id, 'email': 'demo@decoda.app'})
    monkeypatch.setattr(
        pilot_module,
        'ensure_monitored_system_for_target',
        lambda *_args, **_kwargs: {'status': 'ok', 'monitored_system_id': 'sys-1'},
    )

    payload = pilot_module.seed_demo_workspace('demo@decoda.app', 'PilotDemoPass123!', 'Decoda Demo Workspace', 'Decoda Demo User')

    assert payload['email'] == 'demo@decoda.app'
    assert payload['user_created'] is True
    assert payload['workspace_created'] is True
    assert payload['membership_created'] is True
    assert payload['monitoring_bootstrap']['bootstrapped'] is True
    assert payload['monitoring_bootstrap']['evidence_source'] == 'simulator'
    target_insert = next(params for statement, params in executed if 'INSERT INTO targets' in statement)
    assert target_insert is not None
    assert target_insert[3] is not None
    assert '"proof_mode":true' in target_insert[3].lower()
    assert target_insert[4] is not None
    alert_insert = next(params for statement, params in executed if 'INSERT INTO alerts' in statement)
    assert alert_insert[5] == 'high'
    incident_insert = next(params for statement, params in executed if 'INSERT INTO incidents' in statement)
    assert incident_insert[6] == 'high'
    evidence_insert = next(params for statement, params in executed if 'INSERT INTO evidence' in statement)
    assert evidence_insert[11] == 'high'
    assert float(evidence_insert[12]) > 0.8
    assert any('INSERT INTO users' in statement and 'email_verified_at' in statement for statement, _ in executed)
    assert any('INSERT INTO workspaces' in statement for statement, _ in executed)
    assert any('INSERT INTO workspace_members' in statement for statement, _ in executed)
    assert any('INSERT INTO assets' in statement for statement, _ in executed)
    assert any('INSERT INTO targets' in statement for statement, _ in executed)
    assert any('INSERT INTO evidence' in statement for statement, _ in executed)


def test_seed_demo_workspace_backfills_existing_demo_login(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, object]] = []

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            if 'SELECT id, current_workspace_id FROM users WHERE email = %s' in normalized:
                return _Result([{'id': 'user-1', 'current_workspace_id': None}])
            if 'SELECT wm.workspace_id, w.name, w.slug' in normalized:
                return _Result([])
            if 'SELECT 1 FROM workspaces WHERE slug = %s' in normalized:
                return _Result([])
            return _Result()

        def commit(self):
            executed.append(('COMMIT', None))

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'build_user_response', lambda connection, user_id: {'id': user_id, 'email': 'demo@decoda.app'})
    monkeypatch.setattr(
        pilot_module,
        'ensure_monitored_system_for_target',
        lambda *_args, **_kwargs: {'status': 'ok', 'monitored_system_id': 'sys-1'},
    )

    payload = pilot_module.seed_demo_workspace('demo@decoda.app', 'PilotDemoPass123!', 'Decoda Demo Workspace', 'Decoda Demo User')

    assert payload['email'] == 'demo@decoda.app'
    assert payload['password'] == 'PilotDemoPass123!'
    assert payload['workspace_created'] is True
    assert payload['monitoring_bootstrap']['bootstrapped'] is True
    assert any('INSERT INTO workspaces' in statement for statement, _ in executed)
    assert any('INSERT INTO workspace_members' in statement for statement, _ in executed)
    assert any(
        'UPDATE users' in statement and 'email_verified_at = COALESCE(email_verified_at, NOW())' in statement
        for statement, _ in executed
    )


def test_seed_demo_workspace_marks_seeded_demo_user_as_verified_for_signin(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, object]] = []

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            if 'SELECT id, current_workspace_id FROM users WHERE email = %s' in normalized:
                return _Result([])
            if 'SELECT wm.workspace_id, w.name, w.slug' in normalized:
                return _Result([])
            if 'SELECT 1 FROM workspaces WHERE slug = %s' in normalized:
                return _Result([])
            return _Result()

        def commit(self):
            executed.append(('COMMIT', None))

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'build_user_response', lambda connection, user_id: {'id': user_id, 'email': 'demo@decoda.app'})
    monkeypatch.setattr(
        pilot_module,
        'ensure_monitored_system_for_target',
        lambda *_args, **_kwargs: {'status': 'ok', 'monitored_system_id': 'sys-1'},
    )

    pilot_module.seed_demo_workspace('demo@decoda.app', 'PilotDemoPass123!', 'Decoda Demo Workspace', 'Decoda Demo User')

    assert any(
        'INSERT INTO users' in statement and 'email_verified_at' in statement and 'NULL, NOW(), NOW(), NOW(), NOW()' in statement
        for statement, _ in executed
    )


def test_signup_user_inserts_user_with_null_workspace_then_backfills_current_workspace(
    pilot_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    executed: list[tuple[str, object]] = []

    class _Request:
        headers = {}
        client = None

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            if 'SELECT id FROM users WHERE email = %s' in normalized:
                return _Result([])
            if 'SELECT 1 FROM workspaces WHERE slug = %s' in normalized:
                return _Result([])
            return _Result()

        def commit(self):
            executed.append(('COMMIT', None))

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'hash_password', lambda _: 'hashed-password')
    monkeypatch.setattr(pilot_module, 'build_user_response', lambda connection, user_id: {'id': user_id})
    monkeypatch.setattr(pilot_module, 'create_access_token', lambda user_id, session_version=1: f'token-{user_id}')
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(pilot_module, '_create_user_token', lambda *args, **kwargs: 'verify-token')
    ids = iter(['user-1', 'workspace-1', 'membership-1'])
    monkeypatch.setattr(pilot_module.uuid, 'uuid4', lambda: next(ids))

    payload = pilot_module.signup_user(
        {
            'email': 'new@decoda.app',
            'password': 'PilotDemoPass123!',
            'full_name': 'Decoda User',
            'workspace_name': 'Decoda Workspace',
        },
        _Request(),
    )

    assert payload['verification_required'] is True
    user_insert = next(params for statement, params in executed if 'INSERT INTO users' in statement)
    assert user_insert == ('user-1', 'new@decoda.app', 'hashed-password', 'Decoda User', None)
    assert any('INSERT INTO workspaces' in statement for statement, _ in executed)
    assert any('INSERT INTO workspace_members' in statement for statement, _ in executed)
    assert ('UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s', ('workspace-1', 'user-1')) in executed


def test_demo_seed_status_requires_workspace_and_membership_for_present_state(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            if 'SELECT users.id, users.current_workspace_id, workspaces.slug,' in normalized:
                return _Result(
                    [
                        {
                            'id': 'user-1',
                            'current_workspace_id': 'workspace-1',
                            'slug': None,
                            'has_membership': True,
                            'has_current_workspace_membership': False,
                        }
                    ]
                )
            raise AssertionError(f'unexpected SQL: {statement}')

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'live_mode_enabled', lambda: True)

    status_payload = pilot_module.demo_seed_status('demo@decoda.app')

    assert status_payload['present'] is False
    assert status_payload['user_present'] is True
    assert status_payload['workspace_present'] is False
    assert status_payload['membership_present'] is True

def test_run_startup_migrations_if_enabled_respects_env_flag(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RUN_MIGRATIONS_ON_STARTUP', 'true')
    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'run_migrations', lambda: ['0001_pilot_foundation.sql'])

    payload = pilot_module.run_startup_migrations_if_enabled()

    assert payload == {
        'enabled': True,
        'ran': True,
        'applied_versions': ['0001_pilot_foundation.sql'],
        'process_role': 'api',
        'reason': 'RUN_MIGRATIONS_ON_STARTUP is enabled',
    }


def test_run_startup_migrations_if_enabled_skips_non_api_roles(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RUN_MIGRATIONS_ON_STARTUP', 'true')
    payload = pilot_module.run_startup_migrations_if_enabled(process_role='worker')

    assert payload == {
        'enabled': False,
        'ran': False,
        'applied_versions': [],
        'process_role': 'worker',
        'reason': 'schema init is disabled for non-api processes',
    }


def test_bootstrap_live_pilot_records_startup_status(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api_main,
        'run_startup_migrations_if_enabled',
        lambda process_role='api': {
            'enabled': True,
            'ran': True,
            'applied_versions': ['0001_pilot_foundation.sql', '0002_pilot_auth_sessions.sql'],
            'process_role': process_role,
            'reason': 'RUN_MIGRATIONS_ON_STARTUP is enabled',
        },
    )

    payload = api_main.bootstrap_live_pilot()

    assert payload['enabled'] is True
    assert payload['applied_versions'] == ['0001_pilot_foundation.sql', '0002_pilot_auth_sessions.sql']
    assert api_main.STARTUP_BOOTSTRAP_STATUS == payload


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


def test_migration_sql_creates_core_pilot_auth_tables() -> None:
    foundation_sql = (REPO_ROOT / 'services' / 'api' / 'migrations' / '0001_pilot_foundation.sql').read_text()
    auth_sessions_sql = (REPO_ROOT / 'services' / 'api' / 'migrations' / '0002_pilot_auth_sessions.sql').read_text()

    assert 'CREATE TABLE IF NOT EXISTS users' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS workspaces' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS workspace_members' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS analysis_runs' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS alerts' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS governance_actions' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS incidents' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS audit_logs' in foundation_sql
    assert 'CREATE TABLE IF NOT EXISTS auth_sessions' in auth_sessions_sql
    assert 'CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_sessions_token_hash_unique' in auth_sessions_sql


def test_signin_user_success_with_uuid_user_id(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Request:
        headers = {}
        client = None

    user_uuid = uuid.uuid4()
    executed: list[str] = []

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append(normalized)
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            if 'SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email = %s' in normalized:
                return _Result([{'id': user_uuid, 'password_hash': 'stored-hash', 'email_verified_at': datetime(2026, 3, 1, tzinfo=timezone.utc), 'session_version': 1, 'mfa_totp_secret': None, 'mfa_enabled_at': None}])
            return _Result()

        def commit(self):
            executed.append('COMMIT')

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'verify_password', lambda password, encoded: encoded == 'stored-hash')
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pilot_module,
        'build_user_response',
        lambda connection, user_id: {'id': user_id, 'memberships': [], 'current_workspace': None},
    )
    monkeypatch.setattr(pilot_module, 'create_access_token', lambda user_id, session_version=1: f'token-for-{user_id}')
    monkeypatch.setattr(pilot_module, '_store_session', lambda *args, **kwargs: None)

    payload = pilot_module.signin_user({'email': 'demo@decoda.app', 'password': 'PilotDemoPass123!'}, _Request())

    assert payload['token_type'] == 'bearer'
    assert payload['access_token'] == f'token-for-{user_uuid}'
    assert payload['user']['id'] == str(user_uuid)
    assert any('UPDATE users SET last_sign_in_at = NOW(), updated_at = NOW() WHERE id = %s' in statement for statement in executed)


def test_signin_user_success_returns_json_safe_payload(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Request:
        headers = {}
        client = None

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT required.table_name FROM unnest' in normalized:
                return _Result([])
            if 'SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email = %s' in normalized:
                return _Result([{'id': 'user-123', 'password_hash': 'stored-hash', 'email_verified_at': datetime(2026, 3, 1, tzinfo=timezone.utc), 'session_version': 1, 'mfa_totp_secret': None, 'mfa_enabled_at': None}])
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)
    monkeypatch.setattr(pilot_module, 'verify_password', lambda password, encoded: encoded == 'stored-hash')
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pilot_module,
        'build_user_response',
        lambda connection, user_id: {
            'id': user_id,
            'email': 'demo@decoda.app',
            'current_workspace': None,
            'memberships': [],
            'created_at': '2026-03-24T00:00:00+00:00',
            'updated_at': '2026-03-24T00:00:00+00:00',
            'last_sign_in_at': '2026-03-24T00:00:00+00:00',
        },
    )
    monkeypatch.setattr(pilot_module, 'create_access_token', lambda user_id, session_version=1: f'token-for-{user_id}')
    monkeypatch.setattr(pilot_module, '_store_session', lambda *args, **kwargs: None)

    payload = pilot_module.signin_user({'email': 'demo@decoda.app', 'password': 'PilotDemoPass123!'}, _Request())
    assert payload['user']['id'] == 'user-123'
    json.dumps(payload)


def test_build_user_response_handles_null_current_workspace_id(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    user_uuid = uuid.uuid4()
    workspace_uuid = uuid.uuid4()
    timestamp = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    executed: list[str] = []

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append(normalized)
            if 'SELECT id, email, full_name, current_workspace_id, created_at, updated_at, last_sign_in_at, email_verified_at, mfa_enabled_at FROM users WHERE id = %s' in normalized:
                return _Result(
                    [
                        {
                            'id': user_uuid,
                            'email': 'demo@decoda.app',
                            'full_name': 'Decoda Demo',
                            'current_workspace_id': None,
                            'created_at': timestamp,
                            'updated_at': timestamp,
                            'last_sign_in_at': timestamp,
                            'email_verified_at': timestamp,
                            'mfa_enabled_at': None,
                        }
                    ]
                )
            if 'SELECT wm.workspace_id, wm.role, wm.created_at, w.name, w.slug FROM workspace_members wm JOIN workspaces w ON w.id = wm.workspace_id WHERE wm.user_id = %s' in normalized:
                return _Result([{'workspace_id': workspace_uuid, 'role': 'workspace_member', 'created_at': timestamp, 'name': 'Workspace', 'slug': 'workspace'}])
            if 'UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s' in normalized:
                return _Result([])
            raise AssertionError(f'unexpected SQL: {statement}')

    user_payload = pilot_module.build_user_response(_Connection(), str(user_uuid))

    assert user_payload['id'] == str(user_uuid)
    assert user_payload['current_workspace_id'] == str(workspace_uuid)
    assert user_payload['current_workspace'] == {'id': str(workspace_uuid), 'name': 'Workspace', 'slug': 'workspace'}
    assert user_payload['memberships'][0]['workspace_id'] == str(workspace_uuid)
    assert any('UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s' in statement for statement in executed)


def test_create_access_token_coerces_non_string_user_id(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot_module, 'token_secret', lambda: 'super-secret-token')

    token = pilot_module.create_access_token(uuid.uuid4())
    payload = pilot_module.decode_access_token(token)

    assert isinstance(payload['sub'], str)
    assert payload['sub']


def test_log_audit_serializes_uuid_datetime_metadata(pilot_module) -> None:
    captured: list[tuple[str, object]] = []
    now = datetime(2026, 3, 24, 12, 30, tzinfo=timezone.utc)
    request_id = uuid.uuid4()

    class _Connection:
        def execute(self, statement, params=None):
            captured.append((statement, params))
            return _Result()

    pilot_module.log_audit(
        _Connection(),
        action='auth.signin',
        entity_type='user',
        entity_id='user-1',
        request=None,
        user_id='user-1',
        workspace_id=None,
        metadata={'request_id': request_id, 'at': now, 'nested': {'workspace_id': uuid.uuid4()}},
    )

    metadata_json = captured[0][1][-1]
    metadata_payload = json.loads(metadata_json)
    assert metadata_payload['request_id'] == str(request_id)
    assert metadata_payload['at'] == now.isoformat()
    assert isinstance(metadata_payload['nested']['workspace_id'], str)
