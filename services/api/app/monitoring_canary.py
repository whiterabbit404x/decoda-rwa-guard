"""Strict production canary mode for the monitoring worker.

The first production canary runs the bounded scanner against a SINGLE allowlisted target
so a real, live poll can be observed under tight safety limits BEFORE the full worker is
enabled. Canary mode is resolved from environment configuration ONLY — never a hard-coded
target — following the same dependency-free discipline (only ``os`` / ``dataclasses`` /
``logging``) as ``worker_enable.py`` and ``monitoring_runtime_mode.py``, so the worker
entrypoint and the due-selection loop can import it without dragging in heavier modules.

Two switches:
  * ``MONITORING_CANARY_ENABLED`` — truthy turns canary mode on. Canary mode is ALSO
    implied on whenever a non-empty allowlist is configured, so setting the allowlist
    alone is enough for the first canary.
  * ``MONITORING_CANARY_TARGET_ALLOWLIST`` — comma-separated target UUIDs (the repository's
    established comma-separated env-list pattern). When canary mode is active, ONLY these
    targets are polled; every other configured target stays configured but is not polled.

Fail-closed: canary mode ON with an EMPTY allowlist polls NOTHING — a canary with no
allowed target is a misconfiguration, never an accidental full-fleet poll.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os

CANARY_ENABLED_ENV = 'MONITORING_CANARY_ENABLED'
CANARY_TARGET_ALLOWLIST_ENV = 'MONITORING_CANARY_TARGET_ALLOWLIST'

_TRUTHY = {'1', 'true', 'yes', 'on'}


def _split_ids(raw: str | None) -> tuple[str, ...]:
    """Split a comma-separated target-id list into trimmed, lowercased, non-empty ids."""
    return tuple(part.strip().lower() for part in str(raw or '').split(',') if part.strip())


@dataclass(frozen=True)
class CanaryConfig:
    """Resolved, immutable snapshot of the canary posture."""

    enabled: bool
    allowed_target_ids: frozenset[str]

    @property
    def allowed_target_count(self) -> int:
        return len(self.allowed_target_ids)

    def is_target_allowed(self, target_id: object) -> bool:
        """True when a target may be polled under the current canary posture.

        Canary OFF → every target is allowed (normal production). Canary ON → only the
        allowlisted targets are allowed; an empty allowlist allows nothing (fail-closed).
        """
        if not self.enabled:
            return True
        return str(target_id or '').strip().lower() in self.allowed_target_ids

    def to_dict(self) -> dict[str, object]:
        return {
            'enabled': self.enabled,
            'allowed_target_count': self.allowed_target_count,
            'allowed_target_ids': sorted(self.allowed_target_ids),
        }


def resolve_canary_config() -> CanaryConfig:
    """Resolve the current canary posture from environment configuration."""
    allowlist = frozenset(_split_ids(os.getenv(CANARY_TARGET_ALLOWLIST_ENV)))
    explicit = (os.getenv(CANARY_ENABLED_ENV) or '').strip().lower() in _TRUTHY
    # Active when explicitly enabled OR when an allowlist is configured.
    enabled = explicit or bool(allowlist)
    return CanaryConfig(enabled=enabled, allowed_target_ids=allowlist)


def canary_mode_enabled() -> bool:
    """True when the monitoring worker is running in bounded canary mode."""
    return resolve_canary_config().enabled


def log_canary_mode_resolved(target_logger: logging.Logger | None = None) -> CanaryConfig:
    """Emit the canonical ``monitoring_canary_mode_resolved`` startup line and return config.

    Called once at worker startup so which posture is active — and how many targets the
    canary is scoped to — is provable from logs alone. No secrets: only the boolean and
    the allowed-target COUNT (never the ids, which are workspace data).
    """
    config = resolve_canary_config()
    (target_logger or logging.getLogger(__name__)).info(
        'event=monitoring_canary_mode_resolved enabled=%s allowed_target_count=%s',
        str(config.enabled).lower(),
        config.allowed_target_count,
    )
    return config
