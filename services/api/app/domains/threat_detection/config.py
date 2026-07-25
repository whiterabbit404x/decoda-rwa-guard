"""Environment-driven configuration for the Threat Detection Engineer.

Follows the existing repository conventions (``_env_*`` helpers + fail-closed
defaults, mirroring asset_risk.config). All knobs are optional; the deterministic
defaults here are the single source of truth so the worker, the summary builder,
and the tests all agree.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

DETECTOR_VERSION = 'threat-detect-v1'

# Rolling correlation windows (seconds) used by the low-and-slow / fan-out
# detectors. Kept explicit so a cluster_key derived from a window is stable.
WINDOW_15M = 15 * 60
WINDOW_1H = 60 * 60
WINDOW_24H = 24 * 60 * 60


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (ValueError, TypeError):
        return default


def _env_decimal(name: str, default: str) -> Decimal:
    try:
        return Decimal(str(os.getenv(name, default)).strip())
    except Exception:
        return Decimal(default)


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, 'true' if default else 'false')).strip().lower()
    return value in {'1', 'true', 'yes', 'on'}


def engine_config() -> dict[str, Any]:
    """Resolve Threat Detection Engineer configuration from the environment."""
    interval_seconds = max(30, _env_int('THREAT_DETECTION_INTERVAL_SECONDS', 120))
    return {
        # Background worker: incrementally processes newly normalized telemetry.
        # Off by default (the deterministic engine is still callable on demand /
        # in tests without a running worker).
        'enabled': _env_flag('THREAT_DETECTION_ENABLED', default=False),
        'interval_seconds': interval_seconds,
        # A worker whose last heartbeat is older than this is not "healthy".
        # Defaults to two cycles so one missed beat is tolerated. Never below 60s.
        'worker_heartbeat_stale_seconds': max(
            60, _env_int('THREAT_DETECTION_WORKER_HEARTBEAT_STALE_SECONDS', interval_seconds * 3)
        ),
        # Max telemetry rows processed per workspace per cycle (bounded so one
        # cycle can never run unbounded work).
        'batch_size': max(1, _env_int('THREAT_DETECTION_BATCH_SIZE', 500)),
        # Max workspaces touched per cycle.
        'workspace_batch_size': max(1, _env_int('THREAT_DETECTION_WORKSPACE_BATCH_SIZE', 50)),
        # Rolling baseline window (days) for per-asset transfer-amount baselines.
        'baseline_days': max(1, _env_int('THREAT_DETECTION_BASELINE_DAYS', 7)),
        'min_baseline_samples': max(3, _env_int('THREAT_DETECTION_MIN_BASELINE_SAMPLES', 8)),
        # Amount-deviation multiples over the rolling mean that promote an unusual
        # transfer to medium / high.
        'amount_deviation_medium': _env_decimal('THREAT_DETECTION_AMOUNT_DEVIATION_MEDIUM', '4'),
        'amount_deviation_high': _env_decimal('THREAT_DETECTION_AMOUNT_DEVIATION_HIGH', '10'),
        # Coordinated low-and-slow: minimum count of individually small, related
        # events inside a window to form a behavioral cluster, and the cumulative
        # multiple over baseline that makes it material.
        'low_slow_min_events': max(3, _env_int('THREAT_DETECTION_LOW_SLOW_MIN_EVENTS', 5)),
        'low_slow_min_actors': max(2, _env_int('THREAT_DETECTION_LOW_SLOW_MIN_ACTORS', 2)),
        'low_slow_window_seconds': max(300, _env_int('THREAT_DETECTION_LOW_SLOW_WINDOW_SECONDS', WINDOW_1H)),
        # Rapid fan-out: distinct destinations from one source within a window.
        'fan_out_min_destinations': max(3, _env_int('THREAT_DETECTION_FAN_OUT_MIN_DESTINATIONS', 4)),
        'fan_out_window_seconds': max(60, _env_int('THREAT_DETECTION_FAN_OUT_WINDOW_SECONDS', WINDOW_15M)),
        # Mint/burn irregularity: multiple over the rolling mint/burn baseline.
        'mint_burn_deviation_high': _env_decimal('THREAT_DETECTION_MINT_BURN_DEVIATION_HIGH', '5'),
        # Confidence floor a detection must reach before it is promoted out of the
        # anomaly state into an actionable detection.
        'promote_confidence': _env_decimal('THREAT_DETECTION_PROMOTE_CONFIDENCE', '0.5'),
        # A detection is alert-eligible only when it is live, promoted, at least
        # this severe, and at least this confident. Screen 6 owns alert triage;
        # Screen 5 only emits/links an eligible canonical alert.
        'alert_min_confidence': _env_decimal('THREAT_DETECTION_ALERT_MIN_CONFIDENCE', '0.6'),
        # Telemetry freshness ceiling (seconds) used by the summary's data-freshness
        # state. Older than this => stale.
        'telemetry_stale_seconds': max(60, _env_int('THREAT_DETECTION_TELEMETRY_STALE_SECONDS', 900)),
        # A resolved/dismissed detection older than this stops counting as active.
        'active_lookback_days': max(1, _env_int('THREAT_DETECTION_ACTIVE_LOOKBACK_DAYS', 30)),
    }


# Canonical telemetry event types the engine understands. Anything else is
# processed as a generic transfer/telemetry event and never mislabeled.
TRANSFER_EVENT_TYPES = frozenset(
    {'wallet_transfer_detected', 'native_transfer', 'erc20_transfer', 'token_transfer', 'transfer'}
)

# Event types that indicate a privileged / administrative on-chain action. The
# privileged-action detector only fires on decoded evidence like this — never on
# a plain transfer — so a normal transaction never becomes an "admin misuse".
PRIVILEGED_EVENT_TYPES = frozenset(
    {
        'ownership_transferred',
        'owner_changed',
        'role_granted',
        'role_revoked',
        'admin_changed',
        'paused',
        'unpaused',
        'upgraded',
        'implementation_upgraded',
        'proxy_upgraded',
        'minter_added',
        'minter_removed',
        'blacklist_updated',
        'config_changed',
        'parameter_changed',
    }
)

# Canonical detection type vocabulary shown on Screen 5's "Detections by Type".
DETECTION_TYPES = (
    'unusual_transfer',
    'coordinated_activity',
    'mint_burn_irregularity',
    'privileged_action',
    'oracle_deviation',
    'reentrancy_pattern',
    'flash_loan_pattern',
    'other',
)

DETECTION_TYPE_LABELS: dict[str, str] = {
    'unusual_transfer': 'Unusual Transfer',
    'coordinated_activity': 'Coordinated Activity',
    'mint_burn_irregularity': 'Mint/Burn Irregularity',
    'privileged_action': 'Privileged/Admin Action',
    'oracle_deviation': 'Oracle Deviation',
    'reentrancy_pattern': 'Reentrancy Pattern',
    'flash_loan_pattern': 'Flash-Loan Pattern',
    'other': 'Other',
}


def detector_support() -> dict[str, dict[str, Any]]:
    """Which detectors are *supported* by the evidence this platform actually has.

    Truthfulness rule: a detector requiring call traces or a canonical oracle
    observation stream that is not available here is reported ``supported=False``
    with a reason, and NEVER produces a detection. This drives the UI so the
    operator sees exactly what is and is not being evaluated — no silent gaps and
    no false confidence.
    """
    return {
        'unusual_transfer': {
            'supported': True,
            'evidence_quality': 'normalized_telemetry',
            'reason': 'Evaluated from normalized transfer telemetry.',
        },
        'coordinated_activity': {
            'supported': True,
            'evidence_quality': 'normalized_telemetry',
            'reason': 'Evaluated from correlated transfer telemetry across rolling windows.',
        },
        'mint_burn_irregularity': {
            'supported': True,
            'evidence_quality': 'event_logs',
            'reason': 'Evaluated from mint/burn (zero-address transfer) telemetry.',
        },
        'privileged_action': {
            'supported': True,
            'evidence_quality': 'decoded_call',
            'reason': 'Evaluated only when decoded privileged/admin event telemetry is present.',
        },
        'oracle_deviation': {
            'supported': False,
            'evidence_quality': 'normalized_telemetry',
            'reason': 'Requires a canonical oracle observation stream; not available to this screen.',
        },
        'reentrancy_pattern': {
            'supported': False,
            'evidence_quality': 'transaction_trace',
            'reason': 'Requires call traces; the platform stores transfer logs only.',
        },
        'flash_loan_pattern': {
            'supported': False,
            'evidence_quality': 'transaction_trace',
            'reason': 'Requires borrow/repay trace + swap logs; not available from transfer telemetry.',
        },
    }


def supported_detection_types() -> list[str]:
    return [k for k, v in detector_support().items() if v.get('supported')]


def worker_heartbeat_is_fresh(last_heartbeat_at: Any, now: Any, config: dict[str, Any] | None = None) -> bool:
    """Whether a persisted worker heartbeat is fresh enough to call the worker
    healthy. A missing heartbeat is never fresh (fail-closed)."""
    if last_heartbeat_at is None or now is None:
        return False
    cfg = config or engine_config()
    try:
        from datetime import timezone

        hb = last_heartbeat_at
        if getattr(hb, 'tzinfo', None) is None:
            hb = hb.replace(tzinfo=timezone.utc)
        return (now - hb).total_seconds() <= int(cfg['worker_heartbeat_stale_seconds'])
    except Exception:
        return False


def blocking_configuration_errors(config: dict[str, Any] | None = None) -> list[str]:
    """Worker startup validation. The engine needs a database in live mode; it
    does NOT need an AI key (the summary falls back to deterministic text)."""
    from services.api.app import pilot

    cfg = config or engine_config()
    errors: list[str] = []
    if not cfg['enabled']:
        return errors
    if not pilot.database_url():
        errors.append('DATABASE_URL is required for the Threat Detection Engineer worker.')
    return errors
