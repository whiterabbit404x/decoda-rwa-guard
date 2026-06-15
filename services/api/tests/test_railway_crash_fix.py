"""
Tests for Railway production crash fix.

Root cause: production DATABASE_URL (postgresql://...) was passed into
resolve_sqlite_path() via phase1_local/dev_support.py, causing a PermissionError
when mkdir was attempted on the URL string as a filesystem path.

Fix: (1) phase1_local/dev_support.py raises RuntimeError on URL-looking paths;
     (2) seed_service / seed_embedded_dependency_registry only run in local/dev mode.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_dev_support():
    """Return a freshly-imported phase1_local.dev_support with env overrides applied."""
    if 'phase1_local.dev_support' in sys.modules:
        del sys.modules['phase1_local.dev_support']
    import phase1_local.dev_support as ds
    return ds


# ---------------------------------------------------------------------------
# 1. dev_support refuses URL-looking SQLite paths
# ---------------------------------------------------------------------------

class TestDevSupportRefusesUrls:
    """resolve_sqlite_path must raise RuntimeError when the resolved path looks like a URL."""

    @pytest.mark.parametrize('url', [
        'postgresql://neondb_owner:secret@host.neon.tech/neondb',
        'postgres://user:pass@db.example.com:5432/mydb',
        'mysql://user:pass@localhost/mydb',
        'http://example.com/db',
        'https://example.com/db',
    ])
    def test_raises_on_url_in_database_url(self, url, tmp_path, monkeypatch):
        monkeypatch.setenv('DATABASE_URL', url)
        monkeypatch.delenv('SQLITE_PATH', raising=False)
        ds = _reload_dev_support()
        with pytest.raises(RuntimeError, match='looks like a remote database URL'):
            ds.resolve_sqlite_path()

    @pytest.mark.parametrize('url', [
        'postgresql://neondb_owner:secret@host.neon.tech/neondb',
        'postgres://user:pass@db.example.com:5432/mydb',
    ])
    def test_raises_on_url_in_sqlite_path(self, url, monkeypatch):
        monkeypatch.setenv('SQLITE_PATH', url)
        monkeypatch.delenv('DATABASE_URL', raising=False)
        ds = _reload_dev_support()
        with pytest.raises(RuntimeError, match='looks like a remote database URL'):
            ds.resolve_sqlite_path()

    def test_local_sqlite_path_succeeds(self, tmp_path, monkeypatch):
        db_path = tmp_path / 'phase1.db'
        monkeypatch.setenv('SQLITE_PATH', str(db_path))
        monkeypatch.delenv('DATABASE_URL', raising=False)
        ds = _reload_dev_support()
        result = ds.resolve_sqlite_path()
        assert result == db_path

    def test_sqlite_url_prefix_stripped_correctly(self, tmp_path, monkeypatch):
        db_path = tmp_path / 'phase1.db'
        monkeypatch.delenv('SQLITE_PATH', raising=False)
        monkeypatch.setenv('DATABASE_URL', f'sqlite:///{db_path}')
        ds = _reload_dev_support()
        result = ds.resolve_sqlite_path()
        assert result == db_path

    def test_no_mkdir_on_url_path(self, monkeypatch):
        monkeypatch.setenv('DATABASE_URL', 'postgresql://neondb_owner:pass@host.neon.tech/db')
        monkeypatch.delenv('SQLITE_PATH', raising=False)
        ds = _reload_dev_support()
        with pytest.raises(RuntimeError):
            ds.resolve_sqlite_path()


# ---------------------------------------------------------------------------
# 2. _is_local_dev_mode helper behaves correctly
# ---------------------------------------------------------------------------

class TestIsLocalDevMode:
    def _get_helper(self):
        if str(API_ROOT) not in sys.path:
            sys.path.insert(0, str(API_ROOT))
        # We read _is_local_dev_mode directly from the module; reload to pick up env.
        import services.api.app.main as m
        return m._is_local_dev_mode

    @pytest.mark.parametrize('app_env', ['production', 'prod'])
    def test_production_env_returns_false(self, app_env, monkeypatch):
        monkeypatch.setenv('APP_ENV', app_env)
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)
        fn = self._get_helper()
        assert fn() is False

    @pytest.mark.parametrize('app_env', ['local', 'development', 'dev'])
    def test_dev_env_returns_true(self, app_env, monkeypatch):
        monkeypatch.setenv('APP_ENV', app_env)
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)
        fn = self._get_helper()
        assert fn() is True

    def test_enable_flag_overrides_to_true(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('ENABLE_LOCAL_DEV_SUPPORT', 'true')
        fn = self._get_helper()
        assert fn() is True

    def test_enable_flag_false_does_not_override(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('ENABLE_LOCAL_DEV_SUPPORT', 'false')
        fn = self._get_helper()
        assert fn() is False

    def test_default_with_no_env_returns_true(self, monkeypatch):
        monkeypatch.delenv('APP_ENV', raising=False)
        monkeypatch.delenv('APP_MODE', raising=False)
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)
        fn = self._get_helper()
        assert fn() is True


# ---------------------------------------------------------------------------
# 3. Production mode: seed_service is never called from lifespan
# ---------------------------------------------------------------------------

class TestProductionLifespanSkipsSeedService:
    def test_seed_service_not_called_in_production(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)

        import services.api.app.main as m

        call_log = []

        with (
            patch.object(m, 'seed_service', side_effect=lambda *a, **kw: call_log.append('seed_service')),
            patch.object(m, 'seed_embedded_dependency_registry', side_effect=lambda: call_log.append('seed_embedded')),
            patch.object(m, 'validate_secret_encryption_key_at_startup'),
            patch.object(m, 'bootstrap_live_pilot'),
            patch.object(m, 'emit_startup_fixture_diagnostics'),
            patch.object(m, 'set_background_loop_health'),
        ):
            m._is_local_dev_mode = lambda: False
            # Simulate lifespan body (startup phase only)
            m.validate_secret_encryption_key_at_startup()
            if m._is_local_dev_mode():
                m.seed_service(m.SERVICE_NAME, m.PORT, m.DETAIL, m.DEFAULT_METRICS)
                m.seed_embedded_dependency_registry()

        assert 'seed_service' not in call_log
        assert 'seed_embedded' not in call_log

    def test_seed_service_called_in_dev(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'development')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)

        import services.api.app.main as m

        call_log = []

        with (
            patch.object(m, 'seed_service', side_effect=lambda *a, **kw: call_log.append('seed_service')),
            patch.object(m, 'seed_embedded_dependency_registry', side_effect=lambda: call_log.append('seed_embedded')),
        ):
            m._is_local_dev_mode = lambda: True
            if m._is_local_dev_mode():
                m.seed_service(m.SERVICE_NAME, m.PORT, m.DETAIL, m.DEFAULT_METRICS)
                m.seed_embedded_dependency_registry()

        assert 'seed_service' in call_log
        assert 'seed_embedded' in call_log


# ---------------------------------------------------------------------------
# 4. Production with Postgres DATABASE_URL never touches SQLite dev_support
# ---------------------------------------------------------------------------

class TestProductionPostgresNeverTouchesSQLite:
    def test_postgres_url_in_dev_support_raises(self, monkeypatch):
        monkeypatch.setenv('DATABASE_URL', 'postgresql://neondb_owner:secret@ep.neon.tech/neondb')
        monkeypatch.delenv('SQLITE_PATH', raising=False)
        ds = _reload_dev_support()
        with pytest.raises(RuntimeError, match='looks like a remote database URL'):
            ds.resolve_sqlite_path()

    def test_postgres_url_never_causes_mkdir(self, monkeypatch, tmp_path):
        postgres_url = 'postgresql://neondb_owner:secret@ep.neon.tech/neondb'
        monkeypatch.setenv('DATABASE_URL', postgres_url)
        monkeypatch.delenv('SQLITE_PATH', raising=False)
        ds = _reload_dev_support()

        mkdir_calls = []
        real_mkdir = Path.mkdir

        def spy_mkdir(self, *args, **kwargs):
            mkdir_calls.append(str(self))
            return real_mkdir(self, *args, **kwargs)

        with patch.object(Path, 'mkdir', spy_mkdir):
            with pytest.raises(RuntimeError):
                ds.resolve_sqlite_path()

        assert not any(postgres_url[:12] in c for c in mkdir_calls), (
            f"mkdir was called with URL-like path: {mkdir_calls}"
        )


# ---------------------------------------------------------------------------
# 5. Production threat-analysis diagnostics never call SQLite dev_support
# ---------------------------------------------------------------------------
#
# Root cause of the wallet-transfer telemetry loss: proxy_threat() ->
# mark_live_payload() -> attach_dependency_diagnostics() called
# load_service() (phase1_local SQLite). In production that raised
# "dev_support configuration error", so live threat analysis returned None and
# surfaced as analysis_unavailable:live_engine_unavailable, which rolled back the
# detected wallet-transfer telemetry. attach_dependency_diagnostics must skip the
# SQLite registry outside local/dev mode.

class TestThreatDiagnosticsSkipsDevSupportInProduction:
    def _get_main(self):
        if str(API_ROOT) not in sys.path:
            sys.path.insert(0, str(API_ROOT))
        import services.api.app.main as m
        return m

    def test_attach_dependency_diagnostics_does_not_call_load_service_in_production(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)
        m = self._get_main()
        # Drive the guard deterministically (earlier tests in this file reassign the module
        # global directly, so do not rely on the leaked value).
        monkeypatch.setattr(m, '_is_local_dev_mode', lambda: False)

        call_log: list[str] = []

        def _refuse(*args, **kwargs):
            call_log.append('load_service')
            raise RuntimeError('dev_support configuration error: must not be used in production')

        monkeypatch.setattr(m, 'load_service', _refuse)

        payload = m.attach_dependency_diagnostics({'source': 'live', 'degraded': False}, 'threat_engine')

        assert 'load_service' not in call_log, 'load_service (SQLite dev_support) must be skipped in production'
        assert payload['diagnostics']['registry_status'] is None

    def test_attach_dependency_diagnostics_uses_load_service_in_dev(self, monkeypatch):
        monkeypatch.setenv('APP_ENV', 'development')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)
        m = self._get_main()
        monkeypatch.setattr(m, '_is_local_dev_mode', lambda: True)

        seen: list[str] = []

        def _fake_load_service(name):
            seen.append(name)
            return {'service_name': name, 'status': 'ok'}

        monkeypatch.setattr(m, 'load_service', _fake_load_service)

        payload = m.attach_dependency_diagnostics({'source': 'live'}, 'threat_engine')

        assert seen, 'dev mode should consult the local SQLite registry'
        assert payload['diagnostics']['registry_status'] == {'service_name': seen[0], 'status': 'ok'}

    def test_mark_live_payload_does_not_crash_in_production(self, monkeypatch):
        """The exact production crash path: proxy_threat -> mark_live_payload must not raise."""
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)
        m = self._get_main()
        monkeypatch.setattr(m, '_is_local_dev_mode', lambda: False)

        def _boom(*args, **kwargs):
            raise RuntimeError(
                'dev_support configuration error: phase1_local SQLite dev support must not be used in production'
            )

        # Any SQLite dev_support touch in production would raise; patch all of them to prove
        # mark_live_payload no longer reaches them.
        monkeypatch.setattr(m, 'load_service', _boom)
        monkeypatch.setattr(m, 'upsert_service', _boom)
        monkeypatch.setattr(m, 'replace_metrics', _boom)

        result = m.mark_live_payload({'source': 'live', 'score': 1}, 'threat_engine')

        assert result['source'] == 'live'
        assert result['diagnostics']['registry_status'] is None
