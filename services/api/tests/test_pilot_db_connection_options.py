from __future__ import annotations

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
