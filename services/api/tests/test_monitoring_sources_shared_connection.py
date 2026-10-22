"""The Monitoring Sources read model uses ONE database connection.

There is no connection pool: every ``pg_connection()`` is a fresh TCP+TLS+auth
handshake. ``list_monitoring_sources`` composes the asset, target and
monitored-system listings plus an enrichment step, and used to open a
connection for each -- four per call, five for the Integrations control plane
that composes it. These tests pin the shared-connection behaviour, and that it
changes nothing else: each listing still runs (and authenticates) on its own
terms, an enrichment failure still degrades to the base payload, and the
sharing never outlives its block.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import pilot


class _FakeConnection:
    def __init__(self) -> None:
        self.savepoints = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextmanager
    def transaction(self):
        self.savepoints += 1
        yield self


@pytest.fixture
def fake_db(monkeypatch):
    opened: list[_FakeConnection] = []

    def connect(*_args, **_kwargs):
        connection = _FakeConnection()
        opened.append(connection)
        return connection

    monkeypatch.setattr(pilot, 'database_url', lambda: 'postgresql://bench/fake')
    monkeypatch.setattr(pilot, 'load_psycopg', lambda: (SimpleNamespace(connect=connect), None))
    monkeypatch.setattr(pilot, '_resolve_database_url_for_connection', lambda url: url)
    monkeypatch.setattr(pilot, '_database_connect_options', lambda: {})
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    return opened


def test_nested_pg_connection_reuses_the_shared_one_only_inside_the_block(fake_db):
    with pilot.pg_connection() as outer, pilot._share_pg_connection(outer):
        with pilot.pg_connection() as inner:
            assert inner is outer
    assert len(fake_db) == 1

    with pilot.pg_connection() as after:
        assert after is not fake_db[0]
    assert len(fake_db) == 2


def test_sharing_is_released_when_the_block_raises(fake_db):
    with pytest.raises(RuntimeError):
        with pilot.pg_connection() as outer, pilot._share_pg_connection(outer):
            raise RuntimeError('listing failed')
    assert pilot._SHARED_PG_CONNECTION.get() is None
    with pilot.pg_connection():
        pass
    assert len(fake_db) == 2


def _wire_listings(monkeypatch, seen: list):
    def listing(key):
        def run(request):
            with pilot.pg_connection() as connection:
                seen.append((key, connection))
            return {key: [{'id': f'{key}-1'}]}
        return run

    monkeypatch.setattr(pilot, 'list_assets', listing('assets'))
    monkeypatch.setattr(pilot, 'list_targets', listing('targets'))
    monkeypatch.setattr(pilot, 'list_monitored_systems', listing('systems'))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(
        pilot, 'resolve_workspace_context_for_request',
        lambda connection, request: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}, True),
    )
    monkeypatch.setattr(pilot, '_load_source_settings', lambda connection, workspace_id: {'auto_routing_enabled': True, 'persisted': True})


def test_list_monitoring_sources_opens_one_connection_for_every_listing(fake_db, monkeypatch):
    seen: list = []
    _wire_listings(monkeypatch, seen)
    monkeypatch.setattr(
        pilot, '_build_monitoring_sources_enrichment',
        lambda connection, **kwargs: {'sources': [{'id': 'source-1'}], 'provider_health': None, 'agent': None, 'summary': None, 'decisions': []},
    )

    payload = pilot.list_monitoring_sources(object())

    assert len(fake_db) == 1
    assert [key for key, _ in seen] == ['assets', 'targets', 'systems']
    assert all(connection is fake_db[0] for _, connection in seen)
    assert payload['assets'] == [{'id': 'assets-1'}]
    assert payload['sources'] == [{'id': 'source-1'}]
    assert payload['settings']['auto_routing_enabled'] is True
    # The enrichment ran inside a savepoint on that same connection.
    assert fake_db[0].savepoints == 1


def test_an_enrichment_failure_still_returns_the_listings(fake_db, monkeypatch):
    seen: list = []
    _wire_listings(monkeypatch, seen)

    def broken(connection, **kwargs):
        raise RuntimeError('canonical facts table unavailable')

    monkeypatch.setattr(pilot, '_build_monitoring_sources_enrichment', broken)

    payload = pilot.list_monitoring_sources(object())

    assert payload['targets'] == [{'id': 'targets-1'}]
    assert payload['sources'] == []
    assert payload['provider_health'] is None
    # Settings that did load are still reported, exactly as before the change.
    assert payload['settings']['auto_routing_enabled'] is True
    assert len(fake_db) == 1
