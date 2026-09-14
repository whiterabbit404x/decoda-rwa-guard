"""Request-scoped timing for the dashboard critical path.

Added to attribute the dashboard's initial-load latency to a specific phase.
``monitoring_runtime_status`` already profiles its own DB checkpoints
(``monitoring_runtime_status_query_profile``), but that measurement excludes
connection establishment and the outbound RPC reachability probe -- which is
exactly where a fixed per-request cost was suspected. This collector spans the
whole request, so the critical path can be read off one log line instead of
being inferred.

Measurement only. It records durations, counts and hit/miss flags; it never
changes a value the customer sees and never influences a status verdict. A
collector exists only for a request that explicitly opens one, so code outside
the dashboard endpoints is unaffected.

The collector lives in a :mod:`contextvars` variable. FastAPI runs sync
endpoints in a worker thread with a *copy* of the caller's context, and the
collector is opened inside the handler body, so every synchronous call the
handler makes -- including deep inside ``monitoring_runner`` and ``pilot`` --
sees the same collector without threading a parameter through.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator

from services.api.app.observability import increment, observe

logger = logging.getLogger('decoda.dashboard.timing')

_ACTIVE: contextvars.ContextVar['DashboardTiming | None'] = contextvars.ContextVar(
    'decoda_dashboard_timing', default=None
)


def timing_enabled() -> bool:
    """Instrumentation is on unless explicitly disabled.

    The cost is a handful of ``perf_counter()`` reads and one INFO line per
    dashboard request, which is negligible next to the work being measured.
    """
    return str(os.getenv('DASHBOARD_TIMING_ENABLED', 'true')).strip().lower() not in {
        '0',
        'false',
        'no',
        'off',
    }


@dataclass
class DashboardTiming:
    """Durations and counters for one dashboard request."""

    route: str
    started_at: float = field(default_factory=perf_counter)
    phases_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)

    def add_phase(self, name: str, duration_ms: float) -> None:
        self.phases_ms[name] = round(self.phases_ms.get(name, 0.0) + duration_ms, 2)

    def add_count(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def set_flag(self, name: str, value: Any) -> None:
        self.flags[name] = value

    def total_ms(self) -> float:
        return round((perf_counter() - self.started_at) * 1000.0, 2)


def active_timing() -> DashboardTiming | None:
    """The collector for the current request, or ``None`` outside one."""
    return _ACTIVE.get()


@contextmanager
def dashboard_timing(route: str) -> Iterator[DashboardTiming | None]:
    """Open a collector for one request and emit the summary on exit.

    Yields ``None`` when instrumentation is disabled, so callers can use this
    unconditionally. The summary is emitted even when the handler raises, so a
    failing slow request is still attributable.
    """
    if not timing_enabled():
        yield None
        return

    collector = DashboardTiming(route=route)
    token = _ACTIVE.set(collector)
    try:
        yield collector
    finally:
        _ACTIVE.reset(token)
        try:
            _emit(collector)
        except Exception:  # pragma: no cover - instrumentation must never break a request
            logger.debug('dashboard_timing_emit_failed route=%s', route)


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time a block into the active collector. A no-op when none is active."""
    collector = _ACTIVE.get()
    if collector is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        collector.add_phase(name, (perf_counter() - started) * 1000.0)


def record_phase(name: str, duration_ms: float) -> None:
    """Record an already-measured duration. A no-op when no collector is active."""
    collector = _ACTIVE.get()
    if collector is not None:
        collector.add_phase(name, duration_ms)


def record_count(name: str, value: int = 1) -> None:
    collector = _ACTIVE.get()
    if collector is not None:
        collector.add_count(name, value)


def record_flag(name: str, value: Any) -> None:
    collector = _ACTIVE.get()
    if collector is not None:
        collector.set_flag(name, value)


def record_db_connect(duration_ms: float) -> None:
    """One Postgres connection established (there is no pool; each is a fresh handshake)."""
    collector = _ACTIVE.get()
    if collector is None:
        return
    collector.add_phase('db_connect_ms', duration_ms)
    collector.add_count('db_connect_count')


def record_db_query(duration_ms: float) -> None:
    collector = _ACTIVE.get()
    if collector is None:
        return
    collector.add_phase('db_query_ms', duration_ms)
    collector.add_count('db_query_count')


@contextmanager
def timed_db_query() -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    finally:
        record_db_query((perf_counter() - started) * 1000.0)


def _emit(collector: DashboardTiming) -> None:
    total_ms = collector.total_ms()
    phases = dict(sorted(collector.phases_ms.items(), key=lambda item: item[1], reverse=True))

    logger.info(
        'dashboard_timing route=%s total_ms=%s phases=%s counters=%s flags=%s',
        collector.route,
        total_ms,
        phases,
        collector.counters,
        collector.flags,
    )

    observe('decoda_dashboard_request_seconds', total_ms / 1000.0, route=collector.route)
    for name, value in phases.items():
        observe(
            'decoda_dashboard_phase_seconds',
            value / 1000.0,
            route=collector.route,
            phase=name,
        )

    # Counters and flags go to /metrics as well as the log line. Durations alone
    # do not answer the questions that matter here -- how many connections a
    # request opened, whether a fast phase was a cache hit, whether a caller led
    # or joined the single-flight -- and /metrics is reachable from a capture
    # harness that has no access to the API's stdout. Every label below is
    # low-cardinality (a phase name, a role, a boolean), so this does not grow
    # the series count with traffic.
    for name, count in collector.counters.items():
        increment(
            'decoda_dashboard_counter_total',
            count,
            route=collector.route,
            counter=name,
        )
    for name, value in collector.flags.items():
        increment(
            'decoda_dashboard_flag_total',
            1,
            route=collector.route,
            flag=name,
            state=str(value),
        )


# ---------------------------------------------------------------------------
# Event-loop lag probe (measurement only)
# ---------------------------------------------------------------------------
#
# The phases above measure work *inside* a handler. They cannot see the other
# half of a slow request: time the request spent waiting because the single
# uvicorn event loop was busy running synchronous work and could not accept the
# socket, dispatch to the threadpool, or write the response.
#
# That time lands in the gap between the browser's request duration and this
# collector's `total_ms` -- the same gap proxy and network latency land in. With
# no way to tell those apart, "the backend was fast but the browser waited 4s"
# has two explanations and no way to choose. `decoda_http_request_duration_seconds`
# does not settle it either: it is measured from inside the loop, after the
# request has already been accepted, so it under-reports a stall at both ends.
#
# A lag probe settles it. When synchronous work holds the loop, a sleep cannot
# resume on schedule, and the overshoot IS the block. Nothing else produces it.
#
# Cost and safety: one `perf_counter()` read and one in-memory list append per
# sample, four times a second by default. `observe()` touches a dict under a
# briefly-held lock -- no database, no Redis, no network, no disk, nothing
# persisted. The series carries no labels, so it cannot grow the series count.
# Individual samples are never logged; only a sample past the warn threshold is,
# which at the default interval is bounded to a handful of lines per second in
# the pathological case and none at all in the healthy one.

EVENT_LOOP_LAG_METRIC = 'decoda_dashboard_event_loop_lag_seconds'

_EVENT_LOOP_PROBE_INTERVAL_DEFAULT = 0.25
_EVENT_LOOP_PROBE_INTERVAL_MIN = 0.05
_EVENT_LOOP_PROBE_INTERVAL_MAX = 5.0
_EVENT_LOOP_LAG_WARN_DEFAULT = 1.0


def _float_env(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, '')).strip() or default)
    except (TypeError, ValueError):
        return default


def event_loop_lag_interval_seconds() -> float:
    """How often to sample, from ``DASHBOARD_EVENT_LOOP_PROBE_INTERVAL_SECONDS``.

    Default 0.25s: frequent enough to resolve the sub-second blocks worth
    finding, infrequent enough that a five-minute capture stays inside the
    2048-sample window ``observe()`` retains.
    """
    return min(
        _EVENT_LOOP_PROBE_INTERVAL_MAX,
        max(
            _EVENT_LOOP_PROBE_INTERVAL_MIN,
            _float_env('DASHBOARD_EVENT_LOOP_PROBE_INTERVAL_SECONDS', _EVENT_LOOP_PROBE_INTERVAL_DEFAULT),
        ),
    )


def event_loop_lag_warn_seconds() -> float:
    """Lag past which ONE line is logged. Non-positive disables the log entirely."""
    return _float_env('DASHBOARD_EVENT_LOOP_LAG_WARN_SECONDS', _EVENT_LOOP_LAG_WARN_DEFAULT)


def record_event_loop_lag(lag_seconds: float) -> None:
    """Record one lag sample. In-memory only; never raises into the caller."""
    try:
        observe(EVENT_LOOP_LAG_METRIC, max(0.0, float(lag_seconds)))
    except Exception:  # pragma: no cover - instrumentation must never break the loop
        return
    warn_at = event_loop_lag_warn_seconds()
    if warn_at > 0 and lag_seconds >= warn_at:
        logger.warning(
            'dashboard_event_loop_lag_exceeded lag_seconds=%.3f threshold_seconds=%.3f',
            lag_seconds,
            warn_at,
        )


async def sample_event_loop_lag(interval_seconds: float) -> float:
    """Sleep ``interval_seconds`` and return how much longer than that it took.

    Separated from the loop below so a test can measure one sample against a
    real block without waiting on a running probe.
    """
    started = perf_counter()
    await asyncio.sleep(interval_seconds)
    return max(0.0, (perf_counter() - started) - interval_seconds)


async def run_event_loop_lag_probe() -> None:
    """Sample event-loop lag until cancelled. A no-op when timing is disabled.

    Deliberately does nothing but measure: it reads no request state, touches no
    connection, and changes nothing a customer sees.
    """
    if not timing_enabled():
        return
    interval = event_loop_lag_interval_seconds()
    while True:
        try:
            record_event_loop_lag(await sample_event_loop_lag(interval))
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - a probe must never kill itself
            logger.debug('dashboard_event_loop_lag_sample_failed', exc_info=True)
            await asyncio.sleep(interval)
