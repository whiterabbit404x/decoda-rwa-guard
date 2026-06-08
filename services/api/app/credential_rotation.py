"""Shared policy and audit helpers for automated credential rotation."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

SUPPORTED_CREDENTIAL_TYPES = frozenset({
    'jwt_signing', 'encryption_key', 'evidence_signing', 'api_key',
    'webhook_secret', 'scim_token', 'oidc_client_secret', 'slack_credential',
})

DEFAULT_ROTATION_DAYS = {
    'jwt_signing': 30,
    'encryption_key': 90,
    'evidence_signing': 90,
    'api_key': 90,
    'webhook_secret': 90,
    'scim_token': 90,
    'oidc_client_secret': 90,
    'slack_credential': 90,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def next_rotation_at(credential_type: str, *, rotated_at: datetime | None = None, interval_days: int | None = None) -> datetime:
    if credential_type not in SUPPORTED_CREDENTIAL_TYPES:
        raise ValueError(f'Unsupported credential type: {credential_type}')
    days = interval_days or DEFAULT_ROTATION_DAYS[credential_type]
    if not 1 <= days <= 3650:
        raise ValueError('Rotation interval must be between 1 and 3650 days.')
    return (rotated_at or utc_now()) + timedelta(days=days)


def credential_fingerprint(secret: str | bytes) -> str:
    material = secret if isinstance(secret, bytes) else secret.encode('utf-8')
    return f'sha256:{hashlib.sha256(material).hexdigest()}'


def automation_batch_size() -> int:
    raw = os.getenv('CREDENTIAL_ROTATION_BATCH_SIZE', '50').strip()
    try:
        return max(1, min(int(raw), 500))
    except ValueError:
        return 50


def event_metadata(*, automated: bool, grace_period_hours: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {'automated': automated, 'grace_period_hours': grace_period_hours, **(extra or {})}
