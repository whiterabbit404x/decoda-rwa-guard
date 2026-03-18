from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Recommendation = Literal['ALLOW', 'REVIEW', 'BLOCK']


class TransactionPayload(BaseModel):
    tx_hash: str | None = None
    from_address: str
    to_address: str
    value: float = 0.0
    gas_price: float | None = None
    gas_limit: int | None = None
    chain_id: int = 1
    calldata_size: int | None = None
    token_transfers: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecodedFunctionCall(BaseModel):
    function_name: str
    contract_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    selectors: list[str] = Field(default_factory=list)


class WalletReputation(BaseModel):
    address: str | None = None
    score: int = Field(50, ge=0, le=100)
    prior_flags: int = 0
    account_age_days: int = 0
    kyc_verified: bool = False
    sanctions_hits: int = 0
    known_safe: bool = False
    recent_counterparties: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContractMetadata(BaseModel):
    address: str | None = None
    contract_name: str | None = None
    verified_source: bool = False
    proxy: bool = False
    created_days_ago: int = 0
    tvl: float | None = None
    audit_count: int = 0
    categories: list[str] = Field(default_factory=list)
    static_flags: dict[str, bool] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarketEvent(BaseModel):
    timestamp: str
    event_type: str
    asset: str | None = None
    venue: str | None = None
    price: float | None = None
    volume: float | None = None
    side: str | None = None
    trader_id: str | None = None
    order_id: str | None = None
    cancellation_rate: float | None = None
    liquidity_change: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskEvaluationRequest(BaseModel):
    transaction_payload: TransactionPayload
    decoded_function_call: DecodedFunctionCall
    wallet_reputation: WalletReputation
    contract_metadata: ContractMetadata
    recent_market_events: list[MarketEvent] = Field(default_factory=list)


class TriggeredRule(BaseModel):
    rule_id: str
    category: Literal['pre_transaction', 'static', 'runtime', 'market']
    score_impact: int
    severity: Literal['low', 'medium', 'high', 'critical']
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class RiskEvaluationResponse(BaseModel):
    risk_score: int = Field(ge=0, le=100)
    triggered_rules: list[TriggeredRule]
    explanation: str
    recommendation: Recommendation
    category_scores: dict[str, int]


class ScenarioSummary(BaseModel):
    scenario: str
    description: str
    sample_path: str
