"""Single source of truth for the polling-only vs. real-time monitoring mode.

Decoda's MVP operates on stable scheduled RPC polling ONLY: real-time QuickNode
Streams, WebSocket monitoring, mempool monitoring, and sub-second detection are
intentionally paused and restored later purely through configuration. This module
resolves ONE canonical switch — ``REALTIME_STREAMS_ENABLED`` (default false =>
polling-only) — into the runtime posture that the QuickNode Streams webhook gate,
the worker/API startup logs, and Screen 4 all read, so they can never disagree
about which mode is active.

The subordinate real-time subsystems (WebSocket worker, mempool) keep their own
existing enable flags and are REPORTED truthfully here from those flags rather
than hard-coded — both already default off, so the MVP configuration is
polling-only end to end. This module does not introduce a second overlapping
"mode" flag: ``MONITORING_INGESTION_MODE`` (services/api/app/monitoring_mode.py)
is a different, orthogonal axis (demo/live/hybrid/degraded evidence sourcing) and
is intentionally left untouched.

Kept dependency-free (only ``os`` / ``dataclasses`` / ``logging``) on purpose so the
API request path (``quicknode_streams``), the worker entrypoints, and the status
builders can all import it without dragging in heavier modules — the same
discipline as ``worker_enable.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os

# Canonical master switch. Fail-closed to polling-only (the MVP posture): any
# unset/blank/unknown value means real-time Streams are paused and stable scheduled
# RPC polling is the only detection path. Reversible with no code change: set
# REALTIME_STREAMS_ENABLED=true to restore full real-time Streams processing.
REALTIME_STREAMS_ENABLED_ENV = 'REALTIME_STREAMS_ENABLED'
# Subordinate real-time subsystems, reported truthfully from their OWN existing flags
# so the resolved-mode log reflects reality instead of a hard-coded false. Both
# default off, so the MVP config resolves to polling with every subsystem paused.
WEBSOCKET_ENABLED_ENV = 'BASE_REALTIME_ENABLED'
MEMPOOL_ENABLED_ENV = 'MEMPOOL_MONITORING_ENABLED'

MODE_POLLING = 'polling'
MODE_REALTIME = 'realtime'

_TRUTHY = {'1', 'true', 'yes', 'on'}


def _truthy(name: str) -> bool:
    return (os.getenv(name) or '').strip().lower() in _TRUTHY


@dataclass(frozen=True)
class MonitoringRuntimeMode:
    """Resolved, immutable snapshot of the monitoring runtime posture."""

    mode: str
    scheduled_polling_enabled: bool
    realtime_streams_enabled: bool
    websocket_enabled: bool
    mempool_enabled: bool

    @property
    def polling_only(self) -> bool:
        return self.mode == MODE_POLLING

    def to_dict(self) -> dict[str, object]:
        return {
            'mode': self.mode,
            'scheduled_polling_enabled': self.scheduled_polling_enabled,
            'realtime_streams_enabled': self.realtime_streams_enabled,
            'websocket_enabled': self.websocket_enabled,
            'mempool_enabled': self.mempool_enabled,
        }


def resolve_monitoring_runtime_mode() -> MonitoringRuntimeMode:
    """Resolve the current monitoring runtime mode from environment configuration."""
    realtime_streams = _truthy(REALTIME_STREAMS_ENABLED_ENV)
    return MonitoringRuntimeMode(
        mode=MODE_REALTIME if realtime_streams else MODE_POLLING,
        # Stable scheduled RPC polling is canonical in BOTH modes and always enabled;
        # polling-only mode pauses the real-time lanes, never the polling loop.
        scheduled_polling_enabled=True,
        realtime_streams_enabled=realtime_streams,
        websocket_enabled=_truthy(WEBSOCKET_ENABLED_ENV),
        mempool_enabled=_truthy(MEMPOOL_ENABLED_ENV),
    )


def realtime_streams_enabled() -> bool:
    """True only when ``REALTIME_STREAMS_ENABLED`` is explicitly truthy (fail-closed)."""
    return _truthy(REALTIME_STREAMS_ENABLED_ENV)


def polling_only_mode() -> bool:
    """True when real-time Streams are paused (the MVP default).

    The QuickNode Streams webhook must not perform downstream processing (tx
    normalization, chain-head/lag evaluation, telemetry persistence) in this mode —
    it authenticates the request and safely ignores it.
    """
    return not realtime_streams_enabled()


def log_monitoring_mode_resolved(
    target_logger: logging.Logger | None = None,
) -> MonitoringRuntimeMode:
    """Emit the canonical ``monitoring_mode_resolved`` startup line and return the mode.

    Called once at process startup by each service (API, monitoring worker) so which
    mode is active — and that scheduled polling stays on while streams/websocket/
    mempool are paused — is provable from logs alone.
    """
    resolved = resolve_monitoring_runtime_mode()
    (target_logger or logging.getLogger(__name__)).info(
        'event=monitoring_mode_resolved mode=%s scheduled_polling_enabled=%s '
        'realtime_streams_enabled=%s websocket_enabled=%s mempool_enabled=%s',
        resolved.mode,
        str(resolved.scheduled_polling_enabled).lower(),
        str(resolved.realtime_streams_enabled).lower(),
        str(resolved.websocket_enabled).lower(),
        str(resolved.mempool_enabled).lower(),
    )
    return resolved
