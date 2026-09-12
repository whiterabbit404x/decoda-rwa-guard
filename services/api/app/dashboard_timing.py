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

import contextvars
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterator

from services.api.app.observability import observe

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
