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

import logging

import pytest

from services.api.app import dashboard_timing


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
