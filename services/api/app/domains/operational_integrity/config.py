"""Canonical vocabulary + environment configuration for Operational Integrity.

Single source of truth shared by the matcher, the service, the summary/endpoint
layer and the tests. Follows the repository convention (``_env_*`` helpers with
fail-closed defaults, mirroring domains/asset_integrity/config.py).

Nothing here is customer-configurable prose: every constant is a machine key the
frontend maps to a label, so a status can never drift between screens.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

# Stamped onto every detection this lane writes, so an auditor can reproduce the
# verdict under the exact rules that produced it.
MATCHER_VERSION = 'op-integrity-v1'

# --------------------------------------------------------------------------
# Categories — first-class, stored on threat_detections.category.
# --------------------------------------------------------------------------
CATEGORY_CYBER_SECURITY = 'CYBER_SECURITY'
CATEGORY_OPERATIONAL_INTEGRITY = 'OPERATIONAL_INTEGRITY'
CATEGORIES = (CATEGORY_CYBER_SECURITY, CATEGORY_OPERATIONAL_INTEGRITY)
DEFAULT_CATEGORY = CATEGORY_CYBER_SECURITY

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_CYBER_SECURITY: 'Cyber Security',
    CATEGORY_OPERATIONAL_INTEGRITY: 'Operational Integrity',
}


def normalize_category(value: Any) -> str | None:
    """Accept 'operational_integrity' / 'OPERATIONAL_INTEGRITY'; reject the rest."""
    key = str(value or '').strip().upper()
    return key if key in CATEGORIES else None


# --------------------------------------------------------------------------
# Detection types in this lane. Stored lower-case on threat_detections
# (matching the existing column convention); the canonical event object exposes
# the upper-case form.
# --------------------------------------------------------------------------
UNMATCHED_ISSUANCE = 'unmatched_issuance'
SETTLEMENT_TIMEOUT = 'settlement_timeout'
NAV_VALUATION_DRIFT = 'nav_valuation_drift'
TRANSFER_AGENT_MISMATCH = 'transfer_agent_mismatch'
UNAUTHORIZED_ADMIN_CHANGE = 'unauthorized_admin_change'

DETECTION_TYPES = (
    UNMATCHED_ISSUANCE,
    SETTLEMENT_TIMEOUT,
    NAV_VALUATION_DRIFT,
    TRANSFER_AGENT_MISMATCH,
    UNAUTHORIZED_ADMIN_CHANGE,
)

DETECTION_TYPE_LABELS: dict[str, str] = {
    UNMATCHED_ISSUANCE: 'Unmatched Issuance',
    SETTLEMENT_TIMEOUT: 'Settlement Timeout',
    NAV_VALUATION_DRIFT: 'NAV / Valuation Drift',
    TRANSFER_AGENT_MISMATCH: 'Transfer-Agent Mismatch',
    UNAUTHORIZED_ADMIN_CHANGE: 'Unauthorized Admin Change',
}

# --------------------------------------------------------------------------
# Deterministic reason codes. Reused verbatim from the Screen 3 reconciliation
# vocabulary where the meaning is identical, so ONE event object can carry the
# same reason code from reconciliation through detection to evidence.
# --------------------------------------------------------------------------
NO_MATCHING_AUTHORIZED_ISSUANCE = 'NO_MATCHING_AUTHORIZED_ISSUANCE'
NO_MATCHING_AUTHORIZED_REDEMPTION = 'NO_MATCHING_AUTHORIZED_REDEMPTION'
AMOUNT_MISMATCH = 'AMOUNT_MISMATCH'
REFERENCE_MISMATCH = 'REFERENCE_MISMATCH'
SETTLEMENT_NOT_COMPLETE = 'SETTLEMENT_NOT_COMPLETE'
OUTSIDE_AUTHORIZED_WINDOW = 'OUTSIDE_AUTHORIZED_WINDOW'
MATCHED_AUTHORIZED_ISSUANCE = 'MATCHED_AUTHORIZED_ISSUANCE'
MATCHED_AUTHORIZED_REDEMPTION = 'MATCHED_AUTHORIZED_REDEMPTION'
SETTLEMENT_DEADLINE_EXCEEDED = 'SETTLEMENT_DEADLINE_EXCEEDED'
AUTHORITATIVE_SOURCE_MISSING = 'AUTHORITATIVE_SOURCE_MISSING'
AUTHORITATIVE_SOURCE_UNAVAILABLE = 'AUTHORITATIVE_SOURCE_UNAVAILABLE'
AUTHORITATIVE_SOURCE_STALE = 'AUTHORITATIVE_SOURCE_STALE'
OPERATION_NOT_DECODED = 'OPERATION_NOT_DECODED'

# The reason codes that assert a real, evidenced operational anomaly. Everything
# else is either "operationally authorized" or a truthful statement that truth
# could not be established — which must never be rendered as an anomaly and must
# never be rendered as healthy.
ANOMALY_REASON_CODES = frozenset({
    NO_MATCHING_AUTHORIZED_ISSUANCE,
    NO_MATCHING_AUTHORIZED_REDEMPTION,
    AMOUNT_MISMATCH,
    REFERENCE_MISMATCH,
    SETTLEMENT_NOT_COMPLETE,
    OUTSIDE_AUTHORIZED_WINDOW,
    SETTLEMENT_DEADLINE_EXCEEDED,
})

INDETERMINATE_REASON_CODES = frozenset({
    AUTHORITATIVE_SOURCE_MISSING,
    AUTHORITATIVE_SOURCE_UNAVAILABLE,
    AUTHORITATIVE_SOURCE_STALE,
    OPERATION_NOT_DECODED,
})

# --------------------------------------------------------------------------
# Deterministic severity per detection type. Configuration, never an LLM call.
# --------------------------------------------------------------------------
_DEFAULT_SEVERITY: dict[str, str] = {
    UNMATCHED_ISSUANCE: 'critical',
    SETTLEMENT_TIMEOUT: 'high',
    NAV_VALUATION_DRIFT: 'high',
    TRANSFER_AGENT_MISMATCH: 'medium',
    UNAUTHORIZED_ADMIN_CHANGE: 'medium',
}

_SEVERITY_ENV = {
    UNMATCHED_ISSUANCE: 'OPERATIONAL_INTEGRITY_SEVERITY_UNMATCHED_ISSUANCE',
    SETTLEMENT_TIMEOUT: 'OPERATIONAL_INTEGRITY_SEVERITY_SETTLEMENT_TIMEOUT',
    NAV_VALUATION_DRIFT: 'OPERATIONAL_INTEGRITY_SEVERITY_NAV_DRIFT',
    TRANSFER_AGENT_MISMATCH: 'OPERATIONAL_INTEGRITY_SEVERITY_TRANSFER_AGENT_MISMATCH',
    UNAUTHORIZED_ADMIN_CHANGE: 'OPERATIONAL_INTEGRITY_SEVERITY_UNAUTHORIZED_ADMIN_CHANGE',
}

_VALID_SEVERITIES = ('low', 'medium', 'high', 'critical')


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
    return str(os.getenv(name, 'true' if default else 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}


def severity_for(detection_type: str, config: dict[str, Any] | None = None) -> str:
    """Deterministic severity for a detection type. Policy/config decides; the
    AI layer has no input and cannot change it."""
    cfg = config or engine_config()
    return str((cfg.get('severity_by_type') or {}).get(detection_type) or 'medium')


def engine_config() -> dict[str, Any]:
    """Resolve Operational Integrity configuration from the environment."""
    severity_by_type: dict[str, str] = {}
    for dtype, env_name in _SEVERITY_ENV.items():
        value = str(os.getenv(env_name, '') or '').strip().lower()
        severity_by_type[dtype] = value if value in _VALID_SEVERITIES else _DEFAULT_SEVERITY[dtype]
    return {
        # Evaluate operational integrity as part of the threat-detection worker
        # cycle. Off by default: the deterministic engine stays callable on
        # demand and in tests without a running worker.
        'enabled': _env_flag('OPERATIONAL_INTEGRITY_ENABLED', default=False),
        # Max on-chain issuance events reconciled per workspace per cycle.
        'batch_size': max(1, _env_int('OPERATIONAL_INTEGRITY_BATCH_SIZE', 200)),
        # How far back a cycle looks for unreconciled issuance telemetry.
        'lookback_seconds': max(60, _env_int('OPERATIONAL_INTEGRITY_LOOKBACK_SECONDS', 24 * 60 * 60)),
        # How many authorization records the matcher considers per event.
        'authorization_limit': max(1, _env_int('OPERATIONAL_INTEGRITY_AUTHORIZATION_LIMIT', 50)),
        # How far from the on-chain event an authorization may sit and still
        # explain it.
        'match_window_seconds': max(60, _env_int('OPERATIONAL_INTEGRITY_MATCH_WINDOW_SECONDS', 86400)),
        # Base-unit tolerance treated as no variance (rounding/dust). Default 0 —
        # an RWA issuance is exact.
        'amount_tolerance_units': _env_decimal('OPERATIONAL_INTEGRITY_AMOUNT_TOLERANCE_UNITS', '0'),
        # Authoritative business state older than this cannot support a verdict:
        # the result is INDETERMINATE, never an anomaly.
        'authoritative_stale_seconds': max(60, _env_int('OPERATIONAL_INTEGRITY_AUTHORITATIVE_STALE_SECONDS', 3600)),
        # Default settlement deadline used ONLY when neither the authorization
        # record nor the asset/workspace configuration supplies one.
        'settlement_deadline_seconds': max(60, _env_int('OPERATIONAL_INTEGRITY_SETTLEMENT_DEADLINE_SECONDS', 2 * 24 * 60 * 60)),
        # NAV drift tolerance (fraction). Only meaningful once an authoritative
        # NAV source exists; the detector stays unsupported until then.
        'nav_drift_tolerance': _env_decimal('OPERATIONAL_INTEGRITY_NAV_DRIFT_TOLERANCE', '0.005'),
        'severity_by_type': severity_by_type,
        # Confidence of a DETERMINISTIC verdict. This is evidence strength, not a
        # model score: a fully-evidenced match/no-match is near-certain, and the
        # value is fixed by configuration rather than produced by any model.
        'deterministic_confidence': _env_decimal('OPERATIONAL_INTEGRITY_DETERMINISTIC_CONFIDENCE', '0.991'),
        'matcher_version': MATCHER_VERSION,
    }


# --------------------------------------------------------------------------
# Detector support — what this platform can ACTUALLY evaluate today.
#
# Truthfulness rule (CLAUDE.md §3): a detector whose authoritative source does
# not exist is reported supported=False with a reason and NEVER produces a
# detection. The operator sees exactly what is and is not being evaluated; a
# count of 0 never implies a detector ran.
# --------------------------------------------------------------------------
def detector_support() -> dict[str, dict[str, Any]]:
    return {
        UNMATCHED_ISSUANCE: {
            'supported': True,
            'category': CATEGORY_OPERATIONAL_INTEGRITY,
            'evidence_quality': 'event_logs',
            'reason': (
                'Evaluated from on-chain issuance telemetry reconciled against the authorized '
                'issuance records held for the asset.'
            ),
            'requires': ('asset_authorized_issuances',),
        },
        SETTLEMENT_TIMEOUT: {
            'supported': True,
            'category': CATEGORY_OPERATIONAL_INTEGRITY,
            'evidence_quality': 'normalized_telemetry',
            'reason': (
                'Evaluated from authorized operations whose settlement deadline has passed without '
                'reaching a cleared settlement state.'
            ),
            'requires': ('asset_authorized_issuances',),
        },
        TRANSFER_AGENT_MISMATCH: {
            'supported': True,
            'category': CATEGORY_OPERATIONAL_INTEGRITY,
            'evidence_quality': 'event_logs',
            'reason': (
                'Evaluated when an authoritative record exists for the operation but its amount or '
                'business reference does not reconcile with the on-chain event.'
            ),
            'requires': ('asset_authorized_issuances',),
        },
        NAV_VALUATION_DRIFT: {
            'supported': False,
            'category': CATEGORY_OPERATIONAL_INTEGRITY,
            'evidence_quality': 'normalized_telemetry',
            'reason': (
                'Requires an authoritative NAV / valuation feed for the asset. No such source is '
                'collected, so valuation drift is not evaluated.'
            ),
            'requires': ('asset_nav_observations',),
        },
        UNAUTHORIZED_ADMIN_CHANGE: {
            'supported': False,
            'category': CATEGORY_OPERATIONAL_INTEGRITY,
            'evidence_quality': 'decoded_call',
            'reason': (
                'Privileged admin events are captured, but classifying one as operationally '
                'unauthorized requires an authoritative record of permitted administrative changes. '
                'No such source is configured, so a privileged event is reported by the '
                'privileged-action detector rather than asserted to be unauthorized.'
            ),
            'requires': ('authoritative_admin_authorizations',),
        },
    }


def supported_detection_types() -> list[str]:
    return [k for k, v in detector_support().items() if v.get('supported')]


def is_supported(detection_type: str) -> bool:
    return bool(detector_support().get(detection_type, {}).get('supported'))
