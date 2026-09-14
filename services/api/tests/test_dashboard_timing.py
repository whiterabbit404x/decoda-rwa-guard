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


# ── the event-loop lag probe ─────────────────────────────────────────────────
#
# The phases above measure work inside a handler. They cannot see time a request
# lost because the single uvicorn event loop was busy running synchronous work
# and could not accept the socket or write the response. That time lands in the
# same gap as proxy latency, so "the backend was fast but the browser waited"
# had two explanations and no way to choose between them. These pin the probe
# that separates them -- and, just as importantly, pin that adding it did not
# cost the timing endpoint its no-I/O guarantee.


def _lag_samples() -> list[float]:
    from services.api.app import observability

    with observability._lock:
        return list(observability._histograms.get((dashboard_timing.EVENT_LOOP_LAG_METRIC, ()), []))


@pytest.fixture
def no_lag_samples():
    """Drop any samples a previous test left, so counts here are this test's."""
    from services.api.app import observability

    with observability._lock:
        observability._histograms.pop((dashboard_timing.EVENT_LOOP_LAG_METRIC, ()), None)
    yield
    with observability._lock:
        observability._histograms.pop((dashboard_timing.EVENT_LOOP_LAG_METRIC, ()), None)


def test_probe_measures_a_real_synchronous_block(timing_enabled, no_lag_samples):
    """A sync block on the loop shows up as lag. This is the whole instrument.

    Deliberately induced the real way -- `time.sleep` on the loop thread, which
    is exactly what `_alert_event_loop` does with `pg_connection()` and its Redis
    calls -- rather than by patching the clock. A probe that only detects a
    simulated block proves nothing about the one we are hunting.
    """
    import asyncio
    import time

    async def _scenario() -> float:
        sample = asyncio.ensure_future(dashboard_timing.sample_event_loop_lag(0.01))
        await asyncio.sleep(0)  # let the probe reach its own sleep first
        time.sleep(0.15)  # block the loop, as synchronous background work does
        return await sample

    lag = asyncio.run(_scenario())

    assert lag >= 0.1, f'a 150ms block on the loop measured as {lag:.3f}s of lag'


def test_an_unblocked_loop_reports_near_zero_lag(timing_enabled, no_lag_samples):
    """The converse. Without it, a probe that always returns a big number passes above."""
    import asyncio

    lag = asyncio.run(dashboard_timing.sample_event_loop_lag(0.01))

    assert lag < 0.1, f'an idle loop reported {lag:.3f}s of lag'


def test_recording_a_sample_reaches_the_metric_registry(timing_enabled, no_lag_samples):
    dashboard_timing.record_event_loop_lag(0.4)
    dashboard_timing.record_event_loop_lag(1.25)

    assert _lag_samples() == [0.4, 1.25]


def test_a_negative_sample_is_clamped_rather_than_recorded(timing_enabled, no_lag_samples):
    """Clock skew must not produce negative lag, which would understate a sum."""
    dashboard_timing.record_event_loop_lag(-5.0)

    assert _lag_samples() == [0.0]


def test_samples_are_not_logged_individually(timing_enabled, no_lag_samples, caplog):
    """Four samples a second must not become four log lines a second."""
    with caplog.at_level(logging.DEBUG, logger='decoda.dashboard.timing'):
        for _ in range(10):
            dashboard_timing.record_event_loop_lag(0.01)

    assert caplog.records == [], f'probe logged {len(caplog.records)} line(s) for routine samples'


def test_only_a_sample_past_the_threshold_is_logged(monkeypatch, timing_enabled, no_lag_samples, caplog):
    monkeypatch.setenv('DASHBOARD_EVENT_LOOP_LAG_WARN_SECONDS', '1.0')

    with caplog.at_level(logging.WARNING, logger='decoda.dashboard.timing'):
        dashboard_timing.record_event_loop_lag(0.5)
        dashboard_timing.record_event_loop_lag(2.5)

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1, f'expected one threshold line, got {messages}'
    assert 'dashboard_event_loop_lag_exceeded' in messages[0]
    assert 'lag_seconds=2.500' in messages[0]


def test_the_threshold_log_can_be_disabled(monkeypatch, timing_enabled, no_lag_samples, caplog):
    monkeypatch.setenv('DASHBOARD_EVENT_LOOP_LAG_WARN_SECONDS', '0')

    with caplog.at_level(logging.WARNING, logger='decoda.dashboard.timing'):
        dashboard_timing.record_event_loop_lag(99.0)

    assert caplog.records == []
    assert _lag_samples() == [99.0], 'disabling the log must not disable the measurement'


@pytest.mark.parametrize(
    ('configured', 'expected'),
    [(None, 0.25), ('0.5', 0.5), ('0.001', 0.05), ('600', 5.0), ('not-a-number', 0.25)],
)
def test_probe_interval_is_clamped_to_a_sane_range(monkeypatch, configured, expected):
    """A misconfigured interval must not become a busy loop or a useless one."""
    if configured is None:
        monkeypatch.delenv('DASHBOARD_EVENT_LOOP_PROBE_INTERVAL_SECONDS', raising=False)
    else:
        monkeypatch.setenv('DASHBOARD_EVENT_LOOP_PROBE_INTERVAL_SECONDS', configured)

    assert dashboard_timing.event_loop_lag_interval_seconds() == expected


def test_probe_is_inert_when_timing_is_disabled(monkeypatch, no_lag_samples):
    """The same switch that silences the rest of the instrumentation stops this."""
    import asyncio

    monkeypatch.setenv('DASHBOARD_TIMING_ENABLED', 'false')

    asyncio.run(asyncio.wait_for(dashboard_timing.run_event_loop_lag_probe(), timeout=5))

    assert _lag_samples() == []


def test_the_probe_performs_no_io(monkeypatch, timing_enabled, no_lag_samples):
    """Zero DB, zero Redis, zero network, zero disk.

    Asserted by making each of those raise. The probe runs on the event loop of
    the process serving every request, so an I/O call here would block the very
    thing it exists to measure -- and would break the timing endpoint's no-I/O
    guarantee the moment a sample landed.

    Raw ``socket.socket`` is deliberately NOT patched: asyncio builds its own
    self-pipe from it, so forbidding it breaks the test harness rather than the
    probe. The four entry points below are the ones this codebase actually
    reaches the network through.
    """
    import asyncio
    import socket
    import urllib.request

    from services.api.app import pilot
    from services.api.app.domains import alert_stream

    def _forbidden(name):
        def _raise(*_args, **_kwargs):
            raise AssertionError(f'event-loop lag probe performed {name} I/O')
        return _raise

    monkeypatch.setattr(pilot, 'pg_connection', _forbidden('database'))
    monkeypatch.setattr(alert_stream, '_get_sync_client', _forbidden('redis'))
    monkeypatch.setattr(socket, 'create_connection', _forbidden('outbound tcp'))
    monkeypatch.setattr(urllib.request, 'urlopen', _forbidden('http'))

    async def _one_cycle() -> None:
        task = asyncio.ensure_future(dashboard_timing.run_event_loop_lag_probe())
        await asyncio.sleep(0.12)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    monkeypatch.setenv('DASHBOARD_EVENT_LOOP_PROBE_INTERVAL_SECONDS', '0.05')
    asyncio.run(_one_cycle())

    assert _lag_samples(), 'the probe recorded nothing, so this proved nothing'


def test_lag_is_exposed_on_the_timing_metrics_endpoint(api_main, timing_enabled, no_lag_samples):
    """Exposed by the EXISTING endpoint, with no endpoint change.

    `dashboard_timing_metrics()` filters on the `decoda_dashboard_` prefix, so
    the series name alone is what puts it on the wire. If the metric were ever
    renamed out from under that prefix, the capture harness would silently see
    nothing -- which is why this asserts the rendered body, not the registry.
    """
    dashboard_timing.record_event_loop_lag(0.75)

    body = api_main.ops_dashboard_timing_metrics().body.decode()

    assert 'decoda_dashboard_event_loop_lag_seconds_count 1' in body
    assert 'decoda_dashboard_event_loop_lag_seconds_sum 0.75' in body
    assert 'decoda_dashboard_event_loop_lag_seconds_max 0.75' in body


def test_lag_samples_do_not_cost_the_endpoint_its_no_database_guarantee(
    api_main, monkeypatch, timing_enabled, no_lag_samples
):
    """The guard at the top of this section, re-run WITH lag samples present."""
    dashboard_timing.record_event_loop_lag(0.2)
    attempts = _count_connection_attempts(api_main, monkeypatch)

    response = api_main.ops_dashboard_timing_metrics()

    assert attempts == [], f'timing snapshot opened {len(attempts)} database connection(s)'
    assert b'decoda_dashboard_event_loop_lag_seconds_sum' in response.body


def test_lag_samples_do_not_cost_the_endpoint_its_no_redis_guarantee(
    api_main, monkeypatch, timing_enabled, no_lag_samples
):
    called: list[str] = []

    def _probe(name):
        def _record(*_args, **_kwargs):
            called.append(name)
            return {}
        return _record

    monkeypatch.setattr(api_main, 'alert_delivery_health', _probe('alert_delivery_health'))
    monkeypatch.setattr(api_main.alert_stream, 'subscriber_health', _probe('subscriber_health'))

    dashboard_timing.record_event_loop_lag(0.2)
    response = api_main.ops_dashboard_timing_metrics()

    assert called == [], f'timing snapshot reached health/Redis paths: {called}'
    assert b'decoda_dashboard_event_loop_lag_seconds_sum' in response.body
