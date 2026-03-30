from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal['low', 'medium', 'high', 'critical']
Action = Literal['allow', 'review', 'block']
AnalysisKind = Literal['contract', 'transaction', 'market']

DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
SAFE_TRANSACTION_EXAMPLE = json.loads((DATA_DIR / 'safe_transaction.json').read_text())
FLASH_LOAN_TRANSACTION_EXAMPLE = json.loads((DATA_DIR / 'flash_loan_transaction.json').read_text())
ADMIN_PRIVILEGE_TRANSACTION_EXAMPLE = json.loads((DATA_DIR / 'admin_privilege_abuse.json').read_text())
NORMAL_MARKET_EXAMPLE = json.loads((DATA_DIR / 'normal_market_behavior.json').read_text())
SPOOFING_MARKET_EXAMPLE = json.loads((DATA_DIR / 'spoofing_market_behavior.json').read_text())
WASH_TRADING_MARKET_EXAMPLE = json.loads((DATA_DIR / 'wash_trading_market_behavior.json').read_text())
CONTRACT_ANALYSIS_EXAMPLE = json.loads((DATA_DIR / 'sample_contract.json').read_text())


class PatternMatch(BaseModel):
    pattern_id: str
    label: str
    weight: int = Field(ge=1, le=100)
    severity: Severity
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class FunctionSummary(BaseModel):
    name: str
    summary: str
    risk_flags: list[str] = Field(default_factory=list)


class ContractAnalysisRequest(BaseModel):
    contract_name: str
    address: str | None = None
    verified_source: bool = False
    audit_count: int = Field(default=0, ge=0)
    created_days_ago: int = Field(default=0, ge=0)
    admin_roles: list[str] = Field(default_factory=list)
    calling_actor: str | None = None
    function_summaries: list[FunctionSummary] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    flags: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        'json_schema_extra': {
            'examples': [CONTRACT_ANALYSIS_EXAMPLE]
        }
    }


class TransactionAnalysisRequest(BaseModel):
    wallet: str
    actor: str
    action_type: str
    protocol: str
    amount: float = Field(default=0.0, ge=0)
    asset: str | None = None
    call_sequence: list[str] = Field(default_factory=list)
    flags: dict[str, bool] = Field(default_factory=dict)
    counterparty_reputation: int = Field(default=50, ge=0, le=100)
    actor_role: str | None = None
    expected_actor_roles: list[str] = Field(default_factory=list)
    burst_actions_last_5m: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        'json_schema_extra': {
            'examples': [
                SAFE_TRANSACTION_EXAMPLE,
                FLASH_LOAN_TRANSACTION_EXAMPLE,
                ADMIN_PRIVILEGE_TRANSACTION_EXAMPLE,
            ]
        }
    }


class Candle(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)


class WalletActivity(BaseModel):
    cluster_id: str
    trade_count: int = Field(ge=0)
    net_volume: float


class MarketAnalysisRequest(BaseModel):
    asset: str
    venue: str
    timeframe_minutes: int = Field(default=15, ge=1)
    current_volume: float = Field(default=0.0, ge=0)
    baseline_volume: float = Field(default=0.0, ge=0)
    participant_diversity: int = Field(default=0, ge=0)
    dominant_cluster_share: float = Field(default=0.0, ge=0, le=1)
    order_flow_summary: dict[str, int] = Field(default_factory=dict)
    candles: list[Candle] = Field(default_factory=list)
    wallet_activity: list[WalletActivity] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        'json_schema_extra': {
            'examples': [
                NORMAL_MARKET_EXAMPLE,
                SPOOFING_MARKET_EXAMPLE,
                WASH_TRADING_MARKET_EXAMPLE,
            ]
        }
    }


class AnalysisResponse(BaseModel):
    analysis_type: AnalysisKind
    score: int = Field(ge=0, le=100)
    severity: Severity
    matched_patterns: list[PatternMatch]
    explanation: str
    recommended_action: Action
    reasons: list[str]
    source: Literal['live', 'fallback', 'degraded'] = 'live'
    rule_pack_version: str = 'evm-rules-v2'
    confidence: float = Field(default=0.0, ge=0, le=1)
    coverage: float = Field(default=0.0, ge=0, le=1)
    coverage_limits: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreatDashboardCard(BaseModel):
    label: str
    value: str
    detail: str
    tone: str


class DetectionRecord(BaseModel):
    id: str
    category: AnalysisKind
    title: str
    score: int = Field(ge=0, le=100)
    severity: Severity
    action: Action
    source: Literal['live', 'fallback']
    explanation: str
    patterns: list[str]


class ThreatDashboardResponse(BaseModel):
    source: Literal['live', 'fallback']
    generated_at: str
    summary: dict[str, Any]
    cards: list[ThreatDashboardCard]
    active_alerts: list[DetectionRecord]
    recent_detections: list[DetectionRecord]
    sample_scenarios: dict[str, Any]
    message: str
