"""Structured JSON logging for the API service."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

_SECRET_FIELDS = frozenset({
    'password', 'secret', 'token', 'key', 'authorization', 'auth',
    'credential', 'api_key', 'access_token', 'refresh_token',
    'secret_encryption_key', 'auth_token_secret', 'private_key',
})


def _scrub(obj: Any, depth: int = 0) -> Any:
    if depth > 5:
        return obj
    if isinstance(obj, dict):
        return {
            k: '***' if any(s in k.lower() for s in _SECRET_FIELDS) else _scrub(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, depth + 1) for v in obj]
    return obj


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
        }
        for extra_key in ('trace_id', 'workspace_id', 'duration_ms', 'status', 'route'):
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
