from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReconciliationStatus(str, Enum):
    matched = 'matched'
    warning = 'warning'
    critical = 'critical'


class OperationalStatus(str, Enum):
    normal = 'normal'
    stressed = 'stressed'
    restricted = 'restricted'
    paused = 'paused'


class BackstopDecision(str, Enum):
    normal = 'normal'
    alert = 'alert'
    restricted = 'restricted'
    paused = 'paused'


class IncidentSeverity(str, Enum):
    low = 'low'
    medium = 'medium'
    high = 'high'
    critical = 'critical'


class LedgerState(BaseModel):
    ledger_name: str = Field(..., examples=['ethereum'])
    reported_supply: float = Field(..., examples=[1000000])
    locked_supply: float = Field(0, examples=[120000])
    pending_settlement: float = Field(0, examples=[15000])
    last_updated_at: str = Field(..., examples=['2026-03-18T11:10:00Z'])
    transfer_count: int = Field(0, examples=[42])
    reconciliation_weight: float = Field(1.0, examples=[1.0])


class ReconciliationRequest(BaseModel):
    asset_id: str = Field(..., examples=['USTB-2026'])
    expected_total_supply: float = Field(..., examples=[1000000])
    ledgers: list[LedgerState]


class LedgerAssessment(BaseModel):
    ledger_name: str
    normalized_effective_supply: float
    accepted: bool
    status: Literal['accepted', 'penalized', 'flagged']
    staleness_minutes: int
    staleness_penalty: float
    settlement_lag_flag: bool
    over_reported_against_expected: bool
    explanation: str


class ReconciliationResponse(BaseModel):
    asset_id: str
    reconciliation_status: ReconciliationStatus
    expected_total_supply: float
    observed_total_supply: float
    normalized_effective_supply: float
    mismatch_amount: float
    mismatch_percent: float
    severity_score: int
    duplicate_or_double_count_risk: bool
    stale_ledger_count: int
    settlement_lag_ledgers: list[str]
    mismatch_summary: list[str]
    recommendations: list[str]
    explainability_summary: str
    per_ledger_balances: list[dict[str, Any]]
    ledger_assessments: list[LedgerAssessment]
    source: Literal['live'] = 'live'
    degraded: bool = False


class BackstopRequest(BaseModel):
    asset_id: str = Field(..., examples=['USTB-2026'])
    volatility_score: float = Field(..., ge=0, le=100, examples=[82])
    cyber_alert_score: float = Field(..., ge=0, le=100, examples=[74])
    reconciliation_severity: float = Field(..., ge=0, le=100, examples=[68])
    oracle_confidence_score: float = Field(..., ge=0, le=100, examples=[45])
    compliance_incident_score: float = Field(..., ge=0, le=100, examples=[61])
    current_market_mode: str = Field('normal', examples=['normal'])


class BackstopResponse(BaseModel):
    asset_id: str
    backstop_decision: BackstopDecision
    triggered_safeguards: list[str]
    recommended_actions: list[str]
    operational_status: OperationalStatus
    trading_status: str
    bridge_status: str
    settlement_status: str
    explainability_summary: str
    source: Literal['live'] = 'live'
    degraded: bool = False


class IncidentRecordRequest(BaseModel):
    event_type: str = Field(..., examples=['reconciliation-failure'])
    trigger_source: str = Field(..., examples=['reconciliation-engine'])
    related_asset_id: str = Field(..., examples=['USTB-2026'])
    affected_assets: list[str] = Field(default_factory=list, examples=[['USTB-2026']])
    affected_ledgers: list[str] = Field(default_factory=list, examples=[['ethereum', 'avalanche']])
    severity: IncidentSeverity = Field(..., examples=['high'])
    status: str = Field(..., examples=['open'])
    summary: str = Field(..., examples=['Critical supply divergence detected during bridge reconciliation.'])
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentRecord(BaseModel):
    event_id: str
    created_at: str
    event_type: str
    trigger_source: str
    related_asset_id: str
    affected_assets: list[str]
    affected_ledgers: list[str]
    severity: IncidentSeverity
    status: str
    summary: str
    metadata: dict[str, Any]
    attestation_hash: str
    fingerprint: str
    source: Literal['live'] = 'live'
    degraded: bool = False


HEALTHY_RECONCILIATION_EXAMPLE = {
    'asset_id': 'USTB-2026',
    'expected_total_supply': 1000000,
    'ledgers': [
        {'ledger_name': 'ethereum', 'reported_supply': 600000, 'locked_supply': 50000, 'pending_settlement': 5000, 'last_updated_at': '2026-03-18T11:55:00Z', 'transfer_count': 82, 'reconciliation_weight': 1.0},
        {'ledger_name': 'avalanche', 'reported_supply': 300000, 'locked_supply': 15000, 'pending_settlement': 3000, 'last_updated_at': '2026-03-18T11:56:00Z', 'transfer_count': 65, 'reconciliation_weight': 1.0},
        {'ledger_name': 'private-bank-ledger', 'reported_supply': 165000, 'locked_supply': 0, 'pending_settlement': 2000, 'last_updated_at': '2026-03-18T11:54:00Z', 'transfer_count': 18, 'reconciliation_weight': 1.0},
    ],
}

CRITICAL_RECONCILIATION_EXAMPLE = {
    'asset_id': 'USTB-2026',
    'expected_total_supply': 1000000,
    'ledgers': [
        {'ledger_name': 'ethereum', 'reported_supply': 740000, 'locked_supply': 10000, 'pending_settlement': 45000, 'last_updated_at': '2026-03-18T11:40:00Z', 'transfer_count': 125, 'reconciliation_weight': 1.0},
        {'ledger_name': 'avalanche', 'reported_supply': 510000, 'locked_supply': 5000, 'pending_settlement': 38000, 'last_updated_at': '2026-03-18T11:42:00Z', 'transfer_count': 118, 'reconciliation_weight': 1.0},
        {'ledger_name': 'private-bank-ledger', 'reported_supply': 210000, 'locked_supply': 0, 'pending_settlement': 12000, 'last_updated_at': '2026-03-18T09:10:00Z', 'transfer_count': 21, 'reconciliation_weight': 1.0},
    ],
}

HIGH_VOLATILITY_BACKSTOP_EXAMPLE = {
    'asset_id': 'USTB-2026',
    'volatility_score': 87,
    'cyber_alert_score': 39,
    'reconciliation_severity': 44,
    'oracle_confidence_score': 52,
    'compliance_incident_score': 28,
    'current_market_mode': 'normal',
}

CYBER_PAUSE_BACKSTOP_EXAMPLE = {
    'asset_id': 'USTB-2026',
    'volatility_score': 71,
    'cyber_alert_score': 89,
    'reconciliation_severity': 81,
    'oracle_confidence_score': 36,
    'compliance_incident_score': 74,
    'current_market_mode': 'restricted',
}

INCIDENT_RECORD_EXAMPLE = {
    'event_type': 'reconciliation-failure',
    'trigger_source': 'reconciliation-engine',
    'related_asset_id': 'USTB-2026',
    'affected_assets': ['USTB-2026'],
    'affected_ledgers': ['ethereum', 'avalanche', 'private-bank-ledger'],
    'severity': 'critical',
    'status': 'open',
    'summary': 'Critical multi-ledger divergence with duplicate mint risk triggered a bridge pause.',
    'metadata': {'scenario': 'critical-supply-divergence-double-count-risk', 'ticket': 'RES-4001'},
}
