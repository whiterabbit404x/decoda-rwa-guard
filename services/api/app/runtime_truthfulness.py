"""
Runtime truthfulness helpers — Session 13.

Pure, side-effect-free functions for signal freshness, contradiction detection,
and safe status derivation.  Callers must not infer telemetry from heartbeat,
poll from telemetry, or any downstream signal from its upstream.
"""
from __future__ import annotations

from datetime import datetime, timezone

FRESHNESS_THRESHOLDS_SECONDS: dict[str, int] = {
    'heartbeat': 300,       # 5 minutes
    'poll': 600,            # 10 minutes
    'telemetry': 900,       # 15 minutes
    'detection': 1800,      # 30 minutes
    'alert': 1800,          # 30 minutes
    'incident': 3600,       # 60 minutes
    'response_action': 3600,  # 60 minutes
    'evidence_export': 86400,  # 24 hours
}

CANONICAL_FRESHNESS_VALUES = frozenset({'current', 'stale', 'unavailable', 'unknown'})
_UNKNOWN_EVIDENCE_SOURCES = frozenset({'unavailable', 'unknown', 'none', ''})


def compute_signal_freshness(
    timestamp: str | datetime | None,
    now: datetime,
    threshold_seconds: int,
) -> str:
    """
    Returns 'current', 'stale', 'unavailable', or 'unknown'.

    - 'unavailable': no timestamp provided
    - 'unknown': timestamp could not be parsed or is in the future
    - 'current': age <= threshold_seconds
    - 'stale': age > threshold_seconds
    """
    if timestamp is None:
        return 'unavailable'
    try:
        if isinstance(timestamp, str):
            ts = datetime.fromisoformat(timestamp)
        elif isinstance(timestamp, datetime):
            ts = timestamp
        else:
            return 'unknown'
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now_aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        age = (now_aware - ts).total_seconds()
        if age < 0:
            return 'unknown'
        return 'current' if age <= threshold_seconds else 'stale'
    except (ValueError, TypeError, AttributeError, OverflowError):
        return 'unknown'


def build_signal_freshness(
    *,
    last_heartbeat_at: str | datetime | None = None,
    last_poll_at: str | datetime | None = None,
    last_telemetry_at: str | datetime | None = None,
    last_detection_at: str | datetime | None = None,
    last_alert_at: str | datetime | None = None,
    last_incident_at: str | datetime | None = None,
    last_response_action_at: str | datetime | None = None,
    last_evidence_export_at: str | datetime | None = None,
    now: datetime,
    thresholds: dict[str, int] | None = None,
) -> dict[str, str]:
    """Returns per-signal freshness dict.  Each value is in CANONICAL_FRESHNESS_VALUES."""
    t = thresholds if thresholds is not None else FRESHNESS_THRESHOLDS_SECONDS
    return {
        'heartbeat': compute_signal_freshness(last_heartbeat_at, now, t['heartbeat']),
        'poll': compute_signal_freshness(last_poll_at, now, t['poll']),
        'telemetry': compute_signal_freshness(last_telemetry_at, now, t['telemetry']),
        'detection': compute_signal_freshness(last_detection_at, now, t['detection']),
        'alert': compute_signal_freshness(last_alert_at, now, t['alert']),
        'incident': compute_signal_freshness(last_incident_at, now, t['incident']),
        'response_action': compute_signal_freshness(last_response_action_at, now, t['response_action']),
        'evidence_export': compute_signal_freshness(last_evidence_export_at, now, t['evidence_export']),
    }


def detect_runtime_contradictions(
    *,
    runtime_status: str = 'unknown',
    freshness_status: str = 'unknown',
    monitoring_mode: str | None = None,
    evidence_source: str = 'unknown',
    configured_systems: int = 0,
    reporting_systems: int = 0,
    protected_assets: int = 0,
    provider_ready: bool = True,
    last_telemetry_at: str | datetime | None = None,
    last_detection_at: str | datetime | None = None,
    last_alert_at: str | datetime | None = None,
    last_incident_at: str | datetime | None = None,
    last_response_action_at: str | datetime | None = None,
    last_evidence_export_at: str | datetime | None = None,
    signal_freshness: dict[str, str] | None = None,
) -> list[str]:
    """
    Returns sorted list of contradiction flag names per Session 13 spec.

    These flags are additive — the existing canonical summary may already
    contain other contradiction flags computed from different logic.
    """
    flags: list[str] = []
    sf = signal_freshness or {}

    # 1. Healthy claimed but no systems are actually reporting
    if runtime_status == 'healthy' and reporting_systems == 0:
        flags.append('healthy_without_reporting_systems')

    # 2. Freshness claimed current but telemetry timestamp is missing
    if freshness_status in {'current', 'fresh'} and last_telemetry_at is None:
        flags.append('current_without_telemetry')

    # 3. Offline claimed while telemetry signal is current
    if runtime_status == 'offline' and sf.get('telemetry') == 'current':
        flags.append('offline_with_current_telemetry')

    # 4. Live monitoring mode but evidence is from simulator
    if monitoring_mode == 'live' and evidence_source == 'simulator':
        flags.append('live_mode_with_simulator_evidence')

    # 5. Live provider evidence claimed but provider is not ready
    if evidence_source == 'live_provider' and not provider_ready:
        flags.append('live_evidence_without_provider_ready')

    # 6. Configured systems exist but no protected assets are registered
    if configured_systems > 0 and protected_assets == 0:
        flags.append('systems_without_protected_assets')

    # 7. Reporting count exceeds configured count (data integrity issue)
    if configured_systems >= 0 and reporting_systems > configured_systems > 0:
        flags.append('reporting_exceeds_configured')

    # 8. Detection present but no telemetry (cannot detect without observed data)
    if last_detection_at is not None and last_telemetry_at is None:
        flags.append('detection_without_telemetry')

    # 9. Alert present but no detection (alerts must originate from detections)
    if last_alert_at is not None and last_detection_at is None:
        flags.append('alert_without_detection')

    # 10. Incident present but no alert (incidents must escalate from alerts)
    if last_incident_at is not None and last_alert_at is None:
        flags.append('incident_without_alert')

    # 11. Response action exists but no incident or alert to act on
    if last_response_action_at is not None and last_incident_at is None and last_alert_at is None:
        flags.append('response_action_without_case')

    # 12. Evidence exported but source truthfulness is not established
    if last_evidence_export_at is not None and evidence_source in _UNKNOWN_EVIDENCE_SOURCES:
        flags.append('evidence_export_without_source_truthfulness')

    return sorted(set(flags))


def derive_runtime_status(
    *,
    contradiction_flags: list[str],
    reporting_systems: int,
    last_telemetry_at: str | datetime | None,
    workspace_configured: bool,
    raw_runtime_status: str,
) -> str:
    """
    Returns safe runtime_status.  Never returns 'healthy' when contradictions exist
    or when no systems are reporting.
    """
    if contradiction_flags:
        if raw_runtime_status in {'healthy', 'live'}:
            return 'limited'
        return raw_runtime_status
    if reporting_systems == 0 and raw_runtime_status == 'healthy':
        return 'limited'
    if last_telemetry_at is None and raw_runtime_status == 'healthy':
        return 'limited'
    return raw_runtime_status


_SIMULATOR_EVIDENCE_SOURCES = frozenset({
    'simulator', 'guided_simulator', 'demo', 'replay', 'synthetic',
    'fixture', 'test_fixture', 'mock', 'sample', 'seeded',
})
_FALLBACK_EVIDENCE_SOURCES = frozenset({'fallback', 'degraded'})
_NON_LIVE_EVIDENCE_SOURCES = _SIMULATOR_EVIDENCE_SOURCES | _FALLBACK_EVIDENCE_SOURCES

_LIVE_EVIDENCE_SOURCES = frozenset({
    'live', 'live_provider', 'provider', 'rpc', 'indexer', 'compliance_feed',
})


def classify_evidence_state(evidence_source: str | None) -> str:
    """Return canonical evidence-state label for provenance tracking.

    Returns one of: REAL_EVIDENCE | SIMULATOR_EVIDENCE | FALLBACK_EVIDENCE |
    FIXTURE_EVIDENCE | UNKNOWN_EVIDENCE.
    """
    normalized = str(evidence_source or '').strip().lower()
    if normalized in _LIVE_EVIDENCE_SOURCES:
        return 'REAL_EVIDENCE'
    if normalized in _SIMULATOR_EVIDENCE_SOURCES - {'fixture', 'test_fixture'}:
        return 'SIMULATOR_EVIDENCE'
    if normalized in {'fixture', 'test_fixture'}:
        return 'FIXTURE_EVIDENCE'
    if normalized in _FALLBACK_EVIDENCE_SOURCES:
        return 'FALLBACK_EVIDENCE'
    return 'UNKNOWN_EVIDENCE'


def validate_evidence_for_live_proof(
    *,
    evidence_source: str | None,
    app_env: str | None = None,
    live_mode: str | None = None,
) -> dict[str, Any]:
    """Validate that evidence is suitable for a verified live proof bundle.

    In production (APP_ENV=production/prod/staging), any non-live evidence
    source is rejected.  Simulator and fallback evidence may only be exported
    as degraded_diagnostic, never as verified live.

    Returns a dict with keys: valid, evidence_state, verified_live,
    exportable_as_verified, error.
    """
    normalized = str(evidence_source or '').strip().lower()
    env = str(app_env or '').strip().lower()
    mode = str(live_mode or '').strip().lower()

    evidence_state = classify_evidence_state(normalized)
    verified_live = evidence_state == 'REAL_EVIDENCE'
    exportable_as_verified = verified_live

    if normalized in _NON_LIVE_EVIDENCE_SOURCES:
        if evidence_state == 'FALLBACK_EVIDENCE':
            msg = (
                f'Cannot produce verified live proof bundle from fallback evidence '
                f'(source={normalized!r}). '
                'Fallback data must never be exported as verified live evidence.'
            )
        elif evidence_state == 'SIMULATOR_EVIDENCE':
            msg = (
                f'Cannot produce verified live proof bundle from simulator evidence '
                f'(source={normalized!r}). '
                'Simulator data must never pass as production live evidence.'
            )
        else:
            msg = (
                f'Cannot produce verified live proof bundle from non-live evidence '
                f'(source={normalized!r}).'
            )
        return {
            'valid': False,
            'evidence_state': evidence_state,
            'verified_live': False,
            'exportable_as_verified': False,
            'error': msg,
        }

    is_production = env in {'production', 'prod', 'staging'}
    is_sim_mode = mode in {'false', '0', 'no', 'off', 'simulator', 'demo'}
    if is_production and is_sim_mode:
        return {
            'valid': False,
            'evidence_state': 'SIMULATOR_EVIDENCE',
            'verified_live': False,
            'exportable_as_verified': False,
            'error': (
                'Cannot produce verified live proof bundle from simulator or fallback evidence. '
                'APP_ENV=production requires LIVE_MODE=true and live provider evidence.'
            ),
        }

    if not verified_live:
        return {
            'valid': False,
            'evidence_state': evidence_state,
            'verified_live': False,
            'exportable_as_verified': False,
            'error': (
                f'Evidence source {normalized!r} is not a recognised live provider. '
                'Cannot export as verified live evidence.'
            ),
        }

    return {
        'valid': True,
        'evidence_state': 'REAL_EVIDENCE',
        'verified_live': True,
        'exportable_as_verified': True,
        'error': None,
    }


def derive_confidence_status(
    *,
    contradiction_flags: list[str],
    evidence_source: str,
    signal_freshness: dict[str, str],
) -> str:
    """
    Returns safe confidence_status based on contradictions, evidence source, and signal freshness.

    - 'unavailable': no live evidence source, or telemetry missing/unknown
    - 'low': live source but telemetry stale, or provider exists but signals partial
    - 'medium': telemetry current but only one of heartbeat/poll is current
    - 'high': telemetry, heartbeat, and poll all current with no contradictions

    Never returns 'high' unless all strict criteria pass.
    """
    if contradiction_flags:
        return 'low' if evidence_source not in _UNKNOWN_EVIDENCE_SOURCES else 'unavailable'
    if evidence_source in _UNKNOWN_EVIDENCE_SOURCES:
        return 'unavailable'
    telemetry = signal_freshness.get('telemetry')
    if telemetry in {'unavailable', 'unknown', None}:
        return 'unavailable'
    if telemetry == 'stale':
        return 'low'
    # telemetry is 'current' — upgrade based on heartbeat + poll presence
    heartbeat = signal_freshness.get('heartbeat')
    poll = signal_freshness.get('poll')
    if heartbeat == 'current' and poll == 'current':
        return 'high'
    if heartbeat == 'current' or poll == 'current':
        return 'medium'
    return 'low'
