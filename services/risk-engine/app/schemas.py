from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Recommendation = Literal['ALLOW', 'REVIEW', 'BLOCK']
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
RISK_EVALUATION_EXAMPLE = json.loads((DATA_DIR / 'sample_risk_request.json').read_text())
NORMAL_EVENTS_EXAMPLE = json.loads((DATA_DIR / 'normal_market_events.json').read_text())
SUSPICIOUS_EVENTS_EXAMPLE = json.loads((DATA_DIR / 'suspicious_market_events.json').read_text())


class TransactionPayload(BaseModel):
    tx_hash: str | None = Field(default=None, examples=['0xphase1sample'])
    from_address: str = Field(examples=['0x1111111111111111111111111111111111111111'])
    to_address: str = Field(examples=['0x2222222222222222222222222222222222222222'])
    value: float = Field(default=0.0, examples=[2500000.0])
    gas_price: float | None = Field(default=None, examples=[95.0])
    gas_limit: int | None = Field(default=None, examples=[350000])
    chain_id: int = Field(default=1, examples=[1])
    calldata_size: int | None = Field(default=None, examples=[256])
    token_transfers: list[dict[str, Any]] = Field(
        default_factory=list,
        examples=[[{'token': 'USTB', 'amount': 2500000}]],
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{'contains_flash_loan_hop': True, 'entrypoint': 'router'}],
    )


class DecodedFunctionCall(BaseModel):
    function_name: str = Field(examples=['flashLoan'])
    contract_name: str | None = Field(default=None, examples=['LiquidityRouter'])
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{'assets': ['USTB'], 'amounts': [2500000], 'receiver': '0x3333333333333333333333333333333333333333'}],
    )
    selectors: list[str] = Field(default_factory=list, examples=[['0xa9059cbb']])


class WalletReputation(BaseModel):
    address: str | None = Field(default=None, examples=['0x1111111111111111111111111111111111111111'])
    score: int = Field(50, ge=0, le=100, examples=[28])
    prior_flags: int = Field(default=0, examples=[3])
    account_age_days: int = Field(default=0, examples=[21])
    kyc_verified: bool = Field(default=False, examples=[False])
    sanctions_hits: int = Field(default=0, examples=[0])
    known_safe: bool = Field(default=False, examples=[False])
    recent_counterparties: int = Field(default=0, examples=[37])
    metadata: dict[str, Any] = Field(default_factory=dict, examples=[{'analyst_tag': 'watchlist'}])


class ContractMetadata(BaseModel):
    address: str | None = Field(default=None, examples=['0x2222222222222222222222222222222222222222'])
    contract_name: str | None = Field(default=None, examples=['LiquidityRouter'])
    verified_source: bool = Field(default=False, examples=[False])
    proxy: bool = Field(default=False, examples=[True])
    created_days_ago: int = Field(default=0, examples=[90])
    tvl: float | None = Field(default=None, examples=[12450000.0])
    audit_count: int = Field(default=0, examples=[0])
    categories: list[str] = Field(default_factory=list, examples=[['dex-router']])
    static_flags: dict[str, bool] = Field(default_factory=dict, examples=[{'hidden_owner': False}])
    metadata: dict[str, Any] = Field(default_factory=dict, examples=[{'upgradeability': 'mutable'}])


class MarketEvent(BaseModel):
    timestamp: str = Field(examples=['2026-03-18T08:58:00Z'])
    event_type: str = Field(examples=['liquidity_drop'])
    asset: str | None = Field(default=None, examples=['USTB'])
    venue: str | None = Field(default=None, examples=['synthetic-exchange'])
    price: float | None = Field(default=None, examples=[1.0])
    volume: float | None = Field(default=None, examples=[2500000.0])
    side: str | None = Field(default=None, examples=['sell'])
    trader_id: str | None = Field(default=None, examples=['desk-17'])
    order_id: str | None = Field(default=None, examples=['order-991'])
    cancellation_rate: float | None = Field(default=None, examples=[0.84])
    liquidity_change: float | None = Field(default=None, examples=[-0.71])
    metadata: dict[str, Any] = Field(default_factory=dict, examples=[{'pool': 'USTB/USDC'}])


class RiskEvaluationRequest(BaseModel):
    transaction_payload: TransactionPayload
    decoded_function_call: DecodedFunctionCall
    wallet_reputation: WalletReputation
    contract_metadata: ContractMetadata
    recent_market_events: list[MarketEvent] = Field(default_factory=list)

    model_config = {
        'json_schema_extra': {
            'examples': [
                RISK_EVALUATION_EXAMPLE,
                {
                    **RISK_EVALUATION_EXAMPLE,
                    'recent_market_events': NORMAL_EVENTS_EXAMPLE,
                },
                {
                    **RISK_EVALUATION_EXAMPLE,
                    'recent_market_events': SUSPICIOUS_EVENTS_EXAMPLE,
                },
            ]
        }
    }


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
