from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


PROVIDER_UNAVAILABLE_REASON = 'provider_unavailable'


@dataclass(frozen=True)
class ReportingSubCounts:
    """Truthful split of an aggregate reporting-systems count (CLAUDE.md).

    The runtime endpoint's legacy ``reporting_systems`` value falls back to legacy/receipt
    coverage rows, so it can be > 0 during a provider outage while ZERO systems are freshly
    reporting live. These explicit sub-counts keep customer-facing status honest:

      * ``fresh_live_reporting_systems``   — systems reporting live telemetry inside the
        freshness window right now (live evidence source required).
      * ``historically_reporting_systems`` — systems that have EVER produced coverage
        evidence (always >= fresh_live).
      * ``replay_only_systems``            — historical minus fresh_live: systems whose
        only evidence is replay/historical.

    ``status_reason`` is ``fresh_coverage_window_Ns`` ONLY when a system is genuinely
    reporting live inside the window; otherwise ``provider_unavailable``. It can never be
    ``fresh_coverage_window`` while the evidence source is replay/none.
    """

    configured_systems: int
    fresh_live_reporting_systems: int
    historically_reporting_systems: int
    replay_only_systems: int
    status_reason: str
    fresh_coverage_window_claimed: bool


def derive_reporting_sub_counts(
    *,
    configured_systems: int,
    fresh_live_reporting_systems: int,
    historically_reporting_systems: int,
    telemetry_window_seconds: int,
    evidence_source: str,
) -> ReportingSubCounts:
    """Derive the truthful reporting sub-counts and status reason.

    Fail-closed: a fresh coverage window is only claimed when there is at least one
    fresh-live reporting system AND the evidence source is live. Replay, historical, none,
    or a degraded/unavailable provider can never present as a fresh live coverage window.
    """
    configured = max(int(configured_systems or 0), 0)
    raw_fresh_live = max(int(fresh_live_reporting_systems or 0), 0)
    normalized_evidence = str(evidence_source or '').strip().lower()
    fresh_coverage_window_claimed = raw_fresh_live > 0 and normalized_evidence == 'live'
    fresh_live = raw_fresh_live if fresh_coverage_window_claimed else 0
    historical = max(int(historically_reporting_systems or 0), fresh_live)
    replay_only = max(historical - fresh_live, 0)
    status_reason = (
        f'fresh_coverage_window_{int(telemetry_window_seconds)}s'
        if fresh_coverage_window_claimed
        else PROVIDER_UNAVAILABLE_REASON
    )
    return ReportingSubCounts(
        configured_systems=configured,
        fresh_live_reporting_systems=fresh_live,
        historically_reporting_systems=historical,
        replay_only_systems=replay_only,
        status_reason=status_reason,
        fresh_coverage_window_claimed=fresh_coverage_window_claimed,
    )


def should_run_historical_backfill(
    *,
    backfill_completed: bool,
    new_historical_rows: bool = False,
    rule_version_changed: bool = False,
    replay_requested: bool = False,
    cursor_recovery_needed: bool = False,
) -> bool:
    """Decide whether a per-target/rule-set historical backfill should run this cycle.

    Section 7: a COMPLETED historical backfill must not rescan all old telemetry every
    scheduled cycle (the production symptom: the same 16 old rows re-deduplicated every
    5 minutes). Once a completion marker/cursor is persisted, the backfill re-runs only
    when one of the explicit triggers is present:

      * ``new_historical_rows``     — historical rows exist beyond the persisted cursor.
      * ``rule_version_changed``    — the rule set changed, so old rows must be re-evaluated.
      * ``replay_requested``        — an operator explicitly requested a replay.
      * ``cursor_recovery_needed``  — the cursor/completion marker was lost and must be rebuilt.

    A backfill that has never completed always runs.
    """
    if not backfill_completed:
        return True
    return bool(
        new_historical_rows
        or rule_version_changed
        or replay_requested
        or cursor_recovery_needed
    )


MONITORING_MODES = {'DEMO', 'LIVE', 'HYBRID', 'DEGRADED'}
EVIDENCE_STATES = {'REAL_EVIDENCE', 'NO_EVIDENCE', 'DEGRADED_EVIDENCE', 'FAILED_EVIDENCE', 'DEMO_EVIDENCE'}
TRUTHFULNESS_STATES = {'CLAIM_SAFE', 'NOT_CLAIM_SAFE', 'UNKNOWN_RISK'}
DETECTION_OUTCOMES = {
    'DETECTION_CONFIRMED',
    'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    'NO_EVIDENCE',
    'MONITORING_DEGRADED',
    'ANALYSIS_FAILED',
    'DEMO_ONLY',
}


@dataclass(frozen=True)
class MonitoringTruthResult:
    mode: str
    status: str
    evidence_state: str
    truthfulness_state: str
    claim_safe: bool
    synthetic: bool
    evidence_present: bool
    recent_real_event_count: int
    last_real_event_at: datetime | None
    latest_block: int | None
    last_checkpoint_at: datetime | None
    checkpoint_age_seconds: int | None
    provider_name: str
    provider_kind: str
    degraded_reason: str | None
    error_code: str | None

    def validate(self) -> None:
        if self.mode not in MONITORING_MODES:
            raise ValueError(f'invalid mode: {self.mode}')
        if self.evidence_state not in EVIDENCE_STATES:
            raise ValueError(f'invalid evidence_state: {self.evidence_state}')
        if self.truthfulness_state not in TRUTHFULNESS_STATES:
            raise ValueError(f'invalid truthfulness_state: {self.truthfulness_state}')
        if self.mode in {'LIVE', 'HYBRID'} and self.synthetic:
            raise ValueError('LIVE/HYBRID cannot be synthetic')
        if self.mode == 'DEMO' and not self.synthetic:
            raise ValueError('DEMO mode must be synthetic')
        if self.mode in {'LIVE', 'HYBRID'} and self.recent_real_event_count <= 0:
            if self.evidence_state == 'REAL_EVIDENCE':
                raise ValueError('REAL_EVIDENCE requires recent_real_event_count > 0')
            if self.claim_safe:
                raise ValueError('claim_safe must be false when recent_real_event_count == 0')


def api_mode(mode: str) -> str:
    normalized = str(mode or '').strip().upper()
    if normalized in MONITORING_MODES:
        return normalized
    return 'HYBRID'


def api_evidence_state(value: str) -> str:
    normalized = str(value or '').strip().upper()
    return normalized if normalized in EVIDENCE_STATES else 'NO_EVIDENCE'


def api_truthfulness_state(value: str) -> str:
    normalized = str(value or '').strip().upper()
    return normalized if normalized in TRUTHFULNESS_STATES else 'UNKNOWN_RISK'


def ui_evidence_state(value: str) -> str:
    mapping = {
        'REAL_EVIDENCE': 'real',
        'NO_EVIDENCE': 'no_evidence',
        'DEGRADED_EVIDENCE': 'degraded',
        'FAILED_EVIDENCE': 'failed',
        'DEMO_EVIDENCE': 'demo',
    }
    return mapping.get(api_evidence_state(value), 'no_evidence')


def ui_truthfulness_state(value: str) -> str:
    mapping = {
        'CLAIM_SAFE': 'claim_safe',
        'NOT_CLAIM_SAFE': 'not_claim_safe',
        'UNKNOWN_RISK': 'unknown_risk',
    }
    return mapping.get(api_truthfulness_state(value), 'unknown_risk')
