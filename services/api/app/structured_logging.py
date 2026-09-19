"""Structured JSON logging for the API service."""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from services.api.app.observability import current_span_id, current_trace_id

#: Substring hints kept from the original scrubber for backward compatibility —
#: a key containing any of these is masked. Deliberately retained ALONGSIDE the
#: canonical sanitizer rather than replaced by it: this is the last line of
#: defense on a log record and has been catching keys like
#: ``secret_encryption_key`` for a long time. It is NOT the primary control;
#: redaction happens at ingestion (see services/api/app/telemetry_privacy.py).
_SECRET_FIELDS = frozenset({
    'password', 'secret', 'token', 'key', 'authorization', 'auth',
    'credential', 'api_key', 'access_token', 'refresh_token',
    'secret_encryption_key', 'auth_token_secret', 'private_key',
})


def _scrub(obj: Any, depth: int = 0) -> Any:
    """Mask secret-looking KEYS and secret-shaped VALUES on a log record.

    Key masking alone was not enough. ``error_message`` matches none of the hints
    above, and a transport exception routinely quotes the URL that failed — an
    RPC URL that carries the provider API key in its path or query. So values are
    also run through the canonical sanitizer's text rules, which strip bearer
    tokens, JWTs, PEM blocks, URL userinfo, and secret query parameters while
    leaving transaction hashes and addresses intact.
    """
    if depth > 5:
        return obj
    if isinstance(obj, dict):
        return {
            k: '***' if any(s in k.lower() for s in _SECRET_FIELDS) else _scrub(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return _scrub_text(obj)
    return obj


#: Cheap pre-check. Every credential shape the sanitizer looks for contains at
#: least one of these, so a log line without any of them skips the regex work
#: entirely — logging is a hot path and the overwhelming majority of records are
#: plain operational text.
_SECRET_SHAPE_HINTS: tuple[str, ...] = ('://', '@', 'eyJ', '-----BEGIN', 'earer ', 'asic ', 'oken ')


def _scrub_text(value: str) -> str:
    """Strip credential shapes from one log string. Never raises.

    Private network identifiers are deliberately NOT redacted here: the addresses
    that appear in Decoda's own operational logs are Decoda's own infrastructure,
    and masking them would cost real diagnostic value for no privacy gain. The
    private-network rules apply to ingested customer telemetry, where the address
    belongs to the customer — that is a different boundary, configured per
    workspace.

    A logging formatter that raised would lose the record entirely, so any
    failure falls back to the original text, which the key-level masking above
    has already been applied to.
    """
    if not any(hint in value for hint in _SECRET_SHAPE_HINTS):
        return value
    try:
        return _log_sanitizer()(value)
    except Exception:  # pragma: no cover - logging must never fail
        return value


@lru_cache(maxsize=1)
def _log_sanitizer():
    """The text sanitizer bound to the log-record policy, resolved once."""
    from services.api.app import telemetry_privacy

    policy = telemetry_privacy.PrivacyPolicy(redact_private_ips=False, redact_emails=False)
    return lambda value: telemetry_privacy.sanitize_text(value, policy=policy)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = 'api'):
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        payload: dict[str, Any] = {
            'timestamp': self.formatTime(record, '%Y-%m-%dT%H:%M:%S'),
            'level': record.levelname,
            'service': self._service,
            'logger': record.name,
            'message': record.message,
            'trace_id': getattr(record, 'trace_id', None) or current_trace_id() or None,
            'span_id': getattr(record, 'span_id', None) or current_span_id() or None,
        }
        for extra_key in ('workspace_id', 'duration_ms', 'status', 'route', 'operation', 'error_type', 'error_message', 'severity', 'runbook_url', 'parent_span_id', 'attributes', 'context', 'correlation_id'):
            if hasattr(record, extra_key):
                payload[extra_key] = getattr(record, extra_key)
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(_scrub(payload))


def configure_logging(service: str = 'api') -> None:
    """Configure structured JSON logging.

    Only adds a StreamHandler if no handlers are present on the root logger.
    Never modifies existing handlers (preserves pytest caplog behavior).
    """
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    use_json = os.getenv('LOG_FORMAT', 'json').lower() == 'json'

    root = logging.getLogger()

    # Only set level if not already explicitly configured (respects pytest/test settings)
    if root.level == logging.WARNING or root.level == 0:
        root.setLevel(getattr(logging, log_level, logging.INFO))

    # Only add a handler if there are none — never overwrite existing handlers
    # (this preserves pytest's caplog handler and test log-level controls)
    if not root.handlers:
        handler = logging.StreamHandler()
        if use_json:
            handler.setFormatter(JsonFormatter(service=service))
        root.addHandler(handler)
