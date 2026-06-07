"""Dependency-free structured metrics, trace context, and error reporting helpers."""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar('trace_id', default='')
_span_id: contextvars.ContextVar[str] = contextvars.ContextVar('span_id', default='')
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)
_lock = threading.Lock()


def _labels(labels: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items() if value is not None))


def current_trace_id() -> str:
    return _trace_id.get()


def current_span_id() -> str:
    return _span_id.get()


def bind_trace(trace_id: str | None = None, span_id: str | None = None) -> tuple[contextvars.Token[str], contextvars.Token[str]]:
    return _trace_id.set((trace_id or uuid.uuid4().hex)[:64]), _span_id.set((span_id or uuid.uuid4().hex[:16])[:32])


def reset_trace(tokens: tuple[contextvars.Token[str], contextvars.Token[str]]) -> None:
    _trace_id.reset(tokens[0])
    _span_id.reset(tokens[1])


def increment(name: str, value: float = 1, **labels: Any) -> None:
    with _lock:
        _counters[(name, _labels(labels))] += value


def gauge(name: str, value: float, **labels: Any) -> None:
    with _lock:
        _gauges[(name, _labels(labels))] = value


def observe(name: str, value: float, **labels: Any) -> None:
    with _lock:
        values = _histograms[(name, _labels(labels))]
        values.append(value)
        if len(values) > 2048:
            del values[:1024]


@dataclass
class Span:
    name: str
    started: float
    trace_id: str
    span_id: str
    attributes: dict[str, Any]


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    trace_id = current_trace_id() or uuid.uuid4().hex
    parent_span_id = current_span_id()
    tokens = bind_trace(trace_id, uuid.uuid4().hex[:16])
    item = Span(name, time.perf_counter(), trace_id, current_span_id(), attributes)
    increment('decoda_trace_spans_total', span=name, status='started')
    try:
        yield item
    except Exception as exc:
        increment('decoda_trace_spans_total', span=name, status='error')
        report_error(exc, operation=name, **attributes)
        raise
    else:
        increment('decoda_trace_spans_total', span=name, status='ok')
    finally:
        duration = time.perf_counter() - item.started
        observe('decoda_trace_span_duration_seconds', duration, span=name)
        logger.info('trace_span', extra={
            'trace_id': trace_id, 'span_id': item.span_id, 'parent_span_id': parent_span_id,
            'operation': name, 'duration_ms': round(duration * 1000, 3), 'status': 'finished',
            'attributes': attributes,
        })
        reset_trace(tokens)


def report_error(exc: BaseException, *, operation: str, severity: str = 'error', **context: Any) -> None:
    increment('decoda_errors_total', operation=operation, error_type=type(exc).__name__, severity=severity)
    logger.error('actionable_error', extra={
        'trace_id': current_trace_id(), 'span_id': current_span_id(), 'operation': operation,
        'error_type': type(exc).__name__, 'error_message': str(exc), 'severity': severity,
        'context': context, 'runbook_url': os.getenv('OBSERVABILITY_RUNBOOK_URL', '/system-health'),
    }, exc_info=exc)


def prometheus_metrics() -> str:
    lines: list[str] = []
    with _lock:
        counters = dict(_counters)
        gauges = dict(_gauges)
        histograms = {key: list(values) for key, values in _histograms.items()}
    def suffix(label_set: tuple[tuple[str, str], ...]) -> str:
        if not label_set:
            return ''
        escaped = [f'{key}="{value.replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))}"' for key, value in label_set]
        return '{' + ','.join(escaped) + '}'
    for (name, label_set), value in sorted(counters.items()):
        lines.append(f'{name}{suffix(label_set)} {value}')
    for (name, label_set), value in sorted(gauges.items()):
        lines.append(f'{name}{suffix(label_set)} {value}')
    for (name, label_set), values in sorted(histograms.items()):
        if values:
            lines.append(f'{name}_count{suffix(label_set)} {len(values)}')
            lines.append(f'{name}_sum{suffix(label_set)} {sum(values)}')
            lines.append(f'{name}_max{suffix(label_set)} {max(values)}')
    return '\n'.join(lines) + '\n'


def trace_headers() -> dict[str, str]:
    return {'X-Trace-ID': current_trace_id(), 'X-Span-ID': current_span_id()}

def send_external_oncall_alert(alert_type: str, summary: str, **details: Any) -> bool:
    """Deliver monitoring-system failures independently of workspace notification storage."""
    from urllib.request import Request, urlopen
    url = os.getenv('MONITORING_ONCALL_URL', '').strip()
    if not url:
        increment('decoda_self_monitoring_alerts_total', alert_type=alert_type, outcome='unconfigured')
        logger.critical('self_monitoring_alert_unrouted', extra={'operation': alert_type, 'severity': 'critical', 'context': details, 'runbook_url': os.getenv('OBSERVABILITY_RUNBOOK_URL', '/system-health')})
        return False
    payload = json.dumps({'event_type': f'monitoring.{alert_type}', 'severity': 'critical', 'summary': summary, 'details': details, 'trace_id': current_trace_id()}).encode()
    headers = {'Content-Type': 'application/json', 'X-Decoda-Monitoring-Alert': alert_type}
    token = os.getenv('MONITORING_ONCALL_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        with urlopen(Request(url, data=payload, headers=headers, method='POST'), timeout=8) as response:
            response.read(1024)
        increment('decoda_self_monitoring_alerts_total', alert_type=alert_type, outcome='delivered')
        return True
    except Exception as exc:
        increment('decoda_self_monitoring_alerts_total', alert_type=alert_type, outcome='failed')
        report_error(exc, operation='self_monitoring.delivery', alert_type=alert_type)
        return False
