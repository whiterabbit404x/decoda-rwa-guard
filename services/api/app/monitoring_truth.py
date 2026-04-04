from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
