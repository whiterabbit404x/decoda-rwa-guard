from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

DbErrorClassification = Literal[
    'quota_exceeded',
    'network_unreachable',
    'db_unavailable',
    'auth_error',
    'unknown_db_error',
]

_QUOTA_PATTERNS = (
    'exceeded the compute time quota',
    'compute time quota exceeded',
    'neon quota exceeded',
)

_NETWORK_PATTERNS = (
    'network is unreachable',
    'could not connect',
    'connection refused',
    'connection timed out',
    'name or service not known',
    'temporary failure in name resolution',
    'no route to host',
    'socket',
)

_AUTH_PATTERNS = (
    'password authentication failed',
    'authentication failed',
    'invalid password',
    'no pg_hba.conf entry',
    'permission denied',
    'role does not exist',
)

_DB_UNAVAILABLE_PATTERNS = (
    'the database system is starting up',
    'database is unavailable',
    'database unavailable',
    'database temporarily unavailable',
    'remaining connection slots are reserved',
    'too many connections',
    'server closed the connection unexpectedly',
)

_KEYVALUE_HOST_PATTERN = re.compile(r'(?:^|\s)host\s*=\s*([^\s]+)')


def _normalize_message(exc: Exception) -> str:
    message_parts: list[str] = []
    stack: list[BaseException | None] = [exc]
    seen: set[int] = set()

    while stack:
        current = stack.pop()
        if current is None:
            continue
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        normalized = ' '.join(str(current).strip().lower().split())
        if normalized:
            message_parts.append(normalized)
        stack.extend([getattr(current, '__cause__', None), getattr(current, '__context__', None)])
        for arg in getattr(current, 'args', ()) or ():
            if isinstance(arg, BaseException):
                stack.append(arg)

    return ' '.join(message_parts)


def classify_db_error(exc: Exception) -> DbErrorClassification:
    message = _normalize_message(exc)

    if any(pattern in message for pattern in _QUOTA_PATTERNS):
        return 'quota_exceeded'

    if any(pattern in message for pattern in _NETWORK_PATTERNS):
        return 'network_unreachable'

    if any(pattern in message for pattern in _AUTH_PATTERNS):
        return 'auth_error'

    if any(pattern in message for pattern in _DB_UNAVAILABLE_PATTERNS):
        return 'db_unavailable'

    return 'unknown_db_error'


def db_error_reason_label(classification: DbErrorClassification) -> str:
    labels: dict[DbErrorClassification, str] = {
        'quota_exceeded': 'Database quota exhausted',
        'network_unreachable': 'Database network unreachable',
        'db_unavailable': 'Database temporarily unavailable',
        'auth_error': 'Database authentication failed',
        'unknown_db_error': 'Unknown database error',
    }
    return labels.get(classification, 'Unknown database error')


def db_error_classification_context(exc: Exception, *, raw_snippet_limit: int = 180) -> dict[str, str]:
    classification = classify_db_error(exc)
    context: dict[str, str] = {
        'classification': classification,
        'reason': db_error_reason_label(classification),
    }
    normalized_message = _normalize_message(exc)
    if (
        any(pattern in normalized_message for pattern in _QUOTA_PATTERNS)
        and any(pattern in normalized_message for pattern in _NETWORK_PATTERNS)
    ):
        context['classification_source'] = 'normalized_message'
        raw_error = ' '.join(str(exc).strip().split())
        if raw_error:
            if len(raw_error) > raw_snippet_limit:
                raw_error = f'{raw_error[:raw_snippet_limit]}…'
            context['raw_error_snippet'] = raw_error
    return context


def extract_db_host_from_dsn(dsn: str | None) -> str | None:
    if not dsn:
        return None

    parsed = urlparse(dsn)
    if parsed.hostname:
        return parsed.hostname

    match = _KEYVALUE_HOST_PATTERN.search(dsn)
    if match:
        return match.group(1).strip("'\"")

    return None
