from __future__ import annotations

import os

from services.api.app import pilot


def test_database_connect_options_use_safe_defaults(monkeypatch) -> None:
    monkeypatch.delenv('DB_CONNECT_TIMEOUT_SECONDS', raising=False)
    monkeypatch.delenv('DB_KEEPALIVES', raising=False)
    monkeypatch.delenv('DB_KEEPALIVES_IDLE_SECONDS', raising=False)
    monkeypatch.delenv('DB_KEEPALIVES_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('DB_KEEPALIVES_COUNT', raising=False)

    options = pilot._database_connect_options()

    assert options == {
        'connect_timeout': 10,
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 5,
    }


def test_database_connect_options_invalid_values_fall_back(monkeypatch) -> None:
    monkeypatch.setenv('DB_CONNECT_TIMEOUT_SECONDS', '0')
    monkeypatch.setenv('DB_KEEPALIVES', '-1')
    monkeypatch.setenv('DB_KEEPALIVES_IDLE_SECONDS', 'abc')
    monkeypatch.setenv('DB_KEEPALIVES_INTERVAL_SECONDS', '0')
    monkeypatch.setenv('DB_KEEPALIVES_COUNT', '0')

    options = pilot._database_connect_options()

    assert options == {
        'connect_timeout': 10,
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 5,
    }


def test_resolve_database_url_prefers_ipv4_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv('DB_PREFER_IPV4', 'true')
    monkeypatch.setattr(
        pilot.socket,
        'getaddrinfo',
        lambda *_args, **_kwargs: [(pilot.socket.AF_INET, pilot.socket.SOCK_STREAM, 0, '', ('203.0.113.10', 5432))],
    )

    resolved = pilot._resolve_database_url_for_connection('postgresql://user:pass@db.example.com:5432/app')

    assert resolved == 'postgresql://user:pass@203.0.113.10:5432/app'


def test_resolve_database_url_keeps_original_when_resolution_fails(monkeypatch) -> None:
    monkeypatch.setenv('DB_PREFER_IPV4', 'true')
    monkeypatch.setattr(
        pilot.socket,
        'getaddrinfo',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('resolution failed')),
    )
    url = 'postgresql://user:pass@db.example.com:5432/app'

    assert pilot._resolve_database_url_for_connection(url) == url


def test_pg_connection_passes_hardening_options(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class _ConnCtx:
        def __enter__(self):
            return {'ok': True}

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    class _Psycopg:
        @staticmethod
        def connect(db_url, **kwargs):
            calls.append((db_url, kwargs))
            return _ConnCtx()

    row_factory = object()
    monkeypatch.setenv('DATABASE_URL', 'postgresql://db.example.com/app')
    monkeypatch.setattr(pilot, 'load_psycopg', lambda: (_Psycopg, row_factory))
    monkeypatch.setattr(pilot, '_resolve_database_url_for_connection', lambda value: f'{value}?resolved=1')
    monkeypatch.setattr(
        pilot,
        '_database_connect_options',
        lambda: {
            'connect_timeout': 9,
            'keepalives': 1,
            'keepalives_idle': 33,
            'keepalives_interval': 11,
            'keepalives_count': 4,
        },
    )

    with pilot.pg_connection() as connection:
        assert connection == {'ok': True}

    assert len(calls) == 1
    db_url, kwargs = calls[0]
    assert db_url == 'postgresql://db.example.com/app?resolved=1'
    assert kwargs == {
        'row_factory': row_factory,
        'connect_timeout': 9,
        'keepalives': 1,
        'keepalives_idle': 33,
        'keepalives_interval': 11,
        'keepalives_count': 4,
    }


def test_runtime_mode_config_summary_selects_sqlite_local_postgres_and_neon(monkeypatch) -> None:
    original_app_mode = os.getenv('APP_MODE')

    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')

    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('APP_MODE', 'demo')
    sqlite_summary = pilot.runtime_mode_config_summary()
    assert sqlite_summary['backend_classification'] == 'sqlite'
    assert sqlite_summary['resolved_app_mode'] == 'demo'
    assert sqlite_summary['live_mode_enabled'] is False
    assert sqlite_summary['postgres_required_for_live_mode'] is True

    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://pilot:pilot@localhost:5432/decoda')
    local_postgres_summary = pilot.runtime_mode_config_summary()
    assert local_postgres_summary['backend_classification'] == 'postgres_local'
    assert local_postgres_summary['resolved_app_mode'] == 'live'
    assert local_postgres_summary['live_mode_enabled'] is True
    assert local_postgres_summary['auth_worker_persistence_enabled'] is True

    monkeypatch.setenv('DATABASE_URL', 'postgresql://pilot:pilot@ep-blue-river-123456.us-east-2.aws.neon.tech:5432/decoda')
    neon_summary = pilot.runtime_mode_config_summary()
    assert neon_summary['backend_classification'] == 'postgres_hosted_neon'
    assert neon_summary['live_mode_enabled'] is True
    assert neon_summary['auth_worker_persistence_enabled'] is True

    if original_app_mode is None:
        monkeypatch.delenv('APP_MODE', raising=False)
    else:
        monkeypatch.setenv('APP_MODE', original_app_mode)
