"""The dashboard timing collector records what the critical path is read from.

``dashboard_timing`` is the only thing standing between "the dashboard takes
6-12s" and an attribution of those seconds to a phase. It shipped without tests,
so these pin the properties the measurement depends on:

  * a phase/count/flag recorded inside a request reaches the emitted summary,
  * the summary is emitted even when the handler raises, so a slow FAILING
    request is still attributable,
  * instrumentation is inert when disabled and outside a request, and
  * ``record_db_connect`` moves BOTH the duration and the count, because the
    per-request connection count is the thing that shows there is no pool.

Measurement-only code still has to be correct: a collector that silently drops a
phase produces a timing table that looks complete and is wrong.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

from services.api.app import dashboard_timing

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _no_active_collector():
    """Each test starts with no collector, and leaves none behind."""
    assert dashboard_timing.active_timing() is None
    yield
    assert dashboard_timing.active_timing() is None


@pytest.fixture
def timing_enabled(monkeypatch):
    monkeypatch.delenv('DASHBOARD_TIMING_ENABLED', raising=False)
    return None


def _emitted_summary(caplog):
    """The single `dashboard_timing` line the collector emits on exit."""
    lines = [
        record for record in caplog.records
        if record.name == 'decoda.dashboard.timing' and 'dashboard_timing route=' in record.getMessage()
    ]
    assert len(lines) == 1, f'expected exactly one summary line, got {len(lines)}'
    return lines[0].getMessage()


def test_phases_counts_and_flags_reach_the_summary(timing_enabled, caplog):
    with caplog.at_level(logging.INFO, logger='decoda.dashboard.timing'):
        with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
            dashboard_timing.record_phase('runtime_status_ms', 1234.5)
            dashboard_timing.record_count('db_query_count', 3)
            dashboard_timing.record_flag('runtime_status_cache', 'miss')

    message = _emitted_summary(caplog)
    assert 'route=ops_dashboard_executive_summary' in message
    assert 'runtime_status_ms' in message
    assert "'db_query_count': 3" in message
    assert "'runtime_status_cache': 'miss'" in message


def test_summary_is_emitted_when_the_handler_raises(timing_enabled, caplog):
    """A request that fails slowly is exactly the one worth attributing."""
    with caplog.at_level(logging.INFO, logger='decoda.dashboard.timing'):
        with pytest.raises(RuntimeError):
            with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
                dashboard_timing.record_phase('runtime_status_ms', 99.0)
                raise RuntimeError('handler blew up')

    assert 'runtime_status_ms' in _emitted_summary(caplog)


def test_record_db_connect_moves_duration_and_count(timing_enabled):
    """Connection COUNT is the signal that every call is a fresh handshake."""
    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary') as collector:
        dashboard_timing.record_db_connect(120.0)
        dashboard_timing.record_db_connect(80.0)

        assert collector is not None
        assert collector.phases_ms['db_connect_ms'] == pytest.approx(200.0)
        assert collector.counters['db_connect_count'] == 2


def test_timed_db_query_records_a_query_and_its_duration(timing_enabled):
    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary') as collector:
        with dashboard_timing.timed_db_query():
            pass

        assert collector is not None
        assert collector.counters['db_query_count'] == 1
        assert collector.phases_ms['db_query_ms'] >= 0.0


def test_repeated_phase_accumulates_rather_than_overwrites(timing_enabled):
    """`ckpt.*` phases repeat within one request; the later one must not win."""
    with dashboard_timing.dashboard_timing('ops_monitoring_runtime_status') as collector:
        dashboard_timing.record_phase('ckpt.count_open_alerts', 10.0)
        dashboard_timing.record_phase('ckpt.count_open_alerts', 15.0)

        assert collector is not None
        assert collector.phases_ms['ckpt.count_open_alerts'] == pytest.approx(25.0)


def test_phase_context_manager_times_the_block(timing_enabled):
    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary') as collector:
        with dashboard_timing.phase('rpc_probe_ms'):
            pass

        assert collector is not None
        assert 'rpc_probe_ms' in collector.phases_ms


def test_disabled_instrumentation_yields_no_collector_and_emits_nothing(monkeypatch, caplog):
    monkeypatch.setenv('DASHBOARD_TIMING_ENABLED', 'false')

    with caplog.at_level(logging.INFO, logger='decoda.dashboard.timing'):
        with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary') as collector:
            assert collector is None
            # Recording against no collector must be a no-op, not a crash: these
            # helpers sit on hot paths shared with non-dashboard requests.
            dashboard_timing.record_phase('runtime_status_ms', 5.0)
            dashboard_timing.record_db_connect(5.0)
            dashboard_timing.record_flag('runtime_status_cache', 'hit')

    assert not [
        record for record in caplog.records
        if 'dashboard_timing route=' in record.getMessage()
    ]


@pytest.mark.parametrize('value', ['0', 'false', 'no', 'off', 'FALSE', ' Off '])
def test_disable_values_are_recognized(monkeypatch, value):
    monkeypatch.setenv('DASHBOARD_TIMING_ENABLED', value)
    assert dashboard_timing.timing_enabled() is False


@pytest.mark.parametrize('value', ['1', 'true', 'yes', 'on', 'anything-else'])
def test_instrumentation_is_on_unless_explicitly_disabled(monkeypatch, value):
    monkeypatch.setenv('DASHBOARD_TIMING_ENABLED', value)
    assert dashboard_timing.timing_enabled() is True


def test_recording_outside_a_request_is_inert(timing_enabled):
    """Code paths shared with non-dashboard requests must not need a collector."""
    dashboard_timing.record_phase('runtime_status_ms', 5.0)
    dashboard_timing.record_count('db_query_count')
    dashboard_timing.record_flag('runtime_status_cache', 'hit')
    dashboard_timing.record_db_connect(5.0)
    dashboard_timing.record_db_query(5.0)

    assert dashboard_timing.active_timing() is None


def test_counters_and_flags_reach_prometheus(timing_enabled):
    """The capture harness reads /metrics, not the API's stdout.

    Durations alone cannot answer "how many connections did this request open"
    or "was that a cache hit", so the counters and flags have to be scrapeable
    and not log-only.
    """
    from services.api.app import observability

    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
        dashboard_timing.record_db_connect(120.0)
        dashboard_timing.record_db_connect(80.0)
        dashboard_timing.record_flag('runtime_status_cache', 'miss')
        dashboard_timing.record_flag('runtime_status_single_flight', 'leader')

    scraped = observability.prometheus_metrics()
    assert 'decoda_dashboard_counter_total' in scraped
    assert 'counter="db_connect_count"' in scraped
    assert 'flag="runtime_status_cache"' in scraped
    assert 'state="miss"' in scraped
    assert 'flag="runtime_status_single_flight"' in scraped
    assert 'state="leader"' in scraped
    # The phase histogram still carries the duration side of the same request.
    assert 'decoda_dashboard_phase_seconds_sum' in scraped


def test_emit_failure_never_breaks_the_request(timing_enabled, monkeypatch):
    """Instrumentation is not allowed to turn a working request into a 500."""
    def _boom(_collector):
        raise RuntimeError('observability backend down')

    monkeypatch.setattr(dashboard_timing, '_emit', _boom)

    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
        dashboard_timing.record_phase('runtime_status_ms', 1.0)


@pytest.fixture()
def api_main():
    """A freshly loaded API module, matching the pattern the route tests use.

    Loaded under its own module name so this test never depends on import order
    with the rest of the suite. Its `import` statements still resolve through
    ``sys.modules``, so it shares this module's ``dashboard_timing`` -- and
    therefore the same contextvar the collector lives in.
    """
    spec = importlib.util.spec_from_file_location('phase1_api_dashboard_timing_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for dashboard timing route tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_status_route_opens_a_collector(api_main, monkeypatch, timing_enabled, caplog):
    """The parallel runtime-status leg must be attributable, not silently dropped.

    ``phase()`` and ``record_flag()`` are no-ops outside a request that opened a
    collector. This endpoint opened none, so everything ``monitoring_runtime_status``
    records -- the RPC probe, the DB checkpoints, the cache hit/miss, the
    single-flight role -- vanished whenever it was reached directly. The dashboard
    fetches it on every load, so that was a whole leg of the load path measuring as
    nothing at all, which reads identically to a leg that cost nothing.
    """
    from starlette.requests import Request

    def _fake_runtime_status(_request):
        # Stands in for the real computation, which records exactly this way.
        dashboard_timing.record_flag('runtime_status_cache', 'miss')
        dashboard_timing.record_phase('rpc_probe_ms', 12.5)
        return {'workspace_monitoring_summary': {}, 'monitoring_status': 'offline'}

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda fn: fn())
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', _fake_runtime_status)
    monkeypatch.setattr(
        api_main,
        'get_background_loop_health',
        lambda: {
            'loop_running': True,
            'last_successful_cycle': '2026-04-28T09:00:00Z',
            'consecutive_failures': 0,
            'next_retry_at': None,
            'backoff_seconds': 0,
        },
    )

    request = Request({'type': 'http', 'method': 'GET', 'path': '/ops/monitoring/runtime-status', 'headers': []})

    with caplog.at_level(logging.INFO, logger='decoda.dashboard.timing'):
        api_main.ops_monitoring_runtime_status(request)

    message = _emitted_summary(caplog)
    assert 'route=ops_monitoring_runtime_status' in message
    assert "'runtime_status_cache': 'miss'" in message
    assert 'rpc_probe_ms' in message


# ── the lightweight timing snapshot the capture harness scrapes ──────────────
#
# The harness scrapes a metrics endpoint before AND after every load. `/metrics`
# calls `alert_delivery_health()`, which opens a `pg_connection()`, so scraping it
# put two fresh Postgres connections into every measured load -- on a capture whose
# entire purpose is to measure per-request connection cost. These pin the property
# that makes the snapshot usable as a measurement instrument: it opens none.


def _count_connection_attempts(api_main, monkeypatch):
    """Install a `pg_connection` probe that COUNTS rather than raises.

    It has to count. `alert_delivery_health` (`main.py`) wraps its connection in
    `except Exception`, so a probe that raised would be swallowed by the very
    function this endpoint exists to avoid -- the assertion would pass whether or
    not the database was touched. The counter cannot be absorbed;
    ``test_the_no_database_guard_is_not_vacuous`` proves it still fires.
    """
    attempts: list[str] = []

    def _probe(*_args, **_kwargs):
        attempts.append('pg_connection')
        raise RuntimeError('no database is available in this test')

    from services.api.app import pilot

    # Both namespaces: `main` calls its own imported name, and anything it
    # delegates to would reach `pilot`'s.
    monkeypatch.setattr(api_main, 'pg_connection', _probe)
    monkeypatch.setattr(pilot, 'pg_connection', _probe)
    return attempts


def test_timing_metrics_endpoint_opens_no_database_connection(api_main, monkeypatch, timing_enabled):
    """Zero `pg_connection()` calls. This is the whole point of the endpoint."""
    attempts = _count_connection_attempts(api_main, monkeypatch)

    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
        dashboard_timing.record_db_connect(120.0)
        dashboard_timing.record_flag('runtime_status_cache', 'miss')

    response = api_main.ops_dashboard_timing_metrics()

    assert attempts == [], f'timing snapshot opened {len(attempts)} database connection(s)'
    assert response.status_code == 200
    assert response.media_type == 'text/plain; version=0.0.4'
    assert b'decoda_dashboard_counter_total' in response.body


def test_the_no_database_guard_is_not_vacuous(api_main, monkeypatch, timing_enabled):
    """The probe above must be able to fail, or it proves nothing.

    `/metrics` is the endpoint the harness used to scrape, and the reason it was
    unusable as a measurement instrument. Running it through the SAME probe shows
    the counter fires on a path that does open a connection.
    """
    attempts = _count_connection_attempts(api_main, monkeypatch)

    api_main.metrics()

    assert attempts, '/metrics should have attempted a database connection'


def test_timing_metrics_endpoint_touches_no_redis_or_health_snapshot(api_main, monkeypatch, timing_enabled):
    """No Redis, no RPC, and none of the health helpers that reach either.

    These probes also count rather than raise: `metrics()` catches broadly around
    the delivery snapshot, so a raising probe could be absorbed there too.
    """
    called: list[str] = []

    def _probe(name):
        def _record(*_args, **_kwargs):
            called.append(name)
            return {}
        return _record

    monkeypatch.setattr(api_main, 'alert_delivery_health', _probe('alert_delivery_health'))
    monkeypatch.setattr(api_main.alert_stream, 'subscriber_health', _probe('subscriber_health'))

    with dashboard_timing.dashboard_timing('ops_monitoring_runtime_status'):
        dashboard_timing.record_phase('rpc_probe_ms', 3.0)

    response = api_main.ops_dashboard_timing_metrics()

    assert called == [], f'timing snapshot reached health/Redis paths: {called}'
    assert response.status_code == 200


def test_timing_snapshot_contains_only_dashboard_series(timing_enabled):
    """A subset, not a rename: every line is a `decoda_dashboard_*` series."""
    from services.api.app import observability

    observability.increment('decoda_unrelated_probe_total', 1, source='timing-snapshot-test')
    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
        dashboard_timing.record_db_connect(42.0)

    subset = observability.dashboard_timing_metrics()

    assert subset.strip(), 'the snapshot should not be empty once a request has been recorded'
    for line in subset.strip().split('\n'):
        assert line.startswith(observability.DASHBOARD_TIMING_METRIC_PREFIX), line


def test_full_metrics_still_carries_everything(timing_enabled):
    """`/metrics` behaviour is preserved: the subset is additive, not a swap."""
    from services.api.app import observability

    observability.increment('decoda_unrelated_probe_total', 1, source='timing-snapshot-test')
    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
        dashboard_timing.record_db_connect(42.0)

    full = observability.prometheus_metrics()
    subset = observability.dashboard_timing_metrics()

    assert 'decoda_unrelated_probe_total' in full
    assert 'decoda_unrelated_probe_total' not in subset
    # The series the harness actually reads are in both.
    for series in (
        'decoda_dashboard_counter_total',
        'decoda_dashboard_phase_seconds_sum',
        'decoda_dashboard_request_seconds_sum',
    ):
        assert series in full
        assert series in subset


def test_snapshot_is_parseable_as_the_same_exposition_format(timing_enabled):
    """One parser reads both endpoints, so the harness needed no parser change."""
    from services.api.app import observability

    with dashboard_timing.dashboard_timing('ops_dashboard_executive_summary'):
        dashboard_timing.record_db_connect(10.0)
        dashboard_timing.record_flag('response_cache_hit', False)

    for line in observability.dashboard_timing_metrics().strip().split('\n'):
        name, _, value = line.rpartition(' ')
        assert name, f'no metric name in {line!r}'
        float(value)  # raises if the value column is not a number
