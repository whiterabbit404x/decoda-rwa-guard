from __future__ import annotations

from dataclasses import dataclass
import os


ALLOWED_MONITORING_MODES = {'demo', 'live', 'hybrid', 'degraded'}


class MonitoringModeError(RuntimeError):
    """Raised when runtime mode boundaries are violated."""


@dataclass(frozen=True)
class MonitoringModeRuntime:
    mode: str
    source_type: str
    degraded: bool
    reason: str | None

    @property
    def operational_mode(self) -> str:
        if self.degraded or self.mode == 'degraded':
            return 'DEGRADED'
        if self.mode == 'live':
            return 'LIVE'
        if self.mode == 'hybrid':
            return 'HYBRID'
        return 'DEMO'


def resolve_monitoring_mode(value: str | None = None) -> str:
    mode = str(value if value is not None else os.getenv('MONITORING_INGESTION_MODE', 'hybrid')).strip().lower()
    return mode if mode in ALLOWED_MONITORING_MODES else 'hybrid'


def is_demo_mode(mode: str | None = None) -> bool:
    return resolve_monitoring_mode(mode) == 'demo'


def is_live_mode(mode: str | None = None) -> bool:
    return resolve_monitoring_mode(mode) == 'live'


def is_hybrid_mode(mode: str | None = None) -> bool:
    return resolve_monitoring_mode(mode) == 'hybrid'


def require_real_monitoring(mode: str | None = None) -> None:
    resolved = resolve_monitoring_mode(mode)
    if resolved in {'live', 'hybrid'}:
        return
    raise MonitoringModeError(f'real monitoring required, got mode={resolved}')


def assert_no_demo_fallback(mode: str | None, *, attempted: bool, context: str) -> None:
    if not attempted:
        return
    resolved = resolve_monitoring_mode(mode)
    if resolved in {'live', 'hybrid', 'degraded'}:
        raise MonitoringModeError(f'demo/synthetic fallback blocked in {resolved} mode: {context}')
