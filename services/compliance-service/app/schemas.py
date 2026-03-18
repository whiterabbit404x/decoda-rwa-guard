from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Decision(str, Enum):
    approved = 'approved'
    review = 'review'
    blocked = 'blocked'


class ResidencyDecision(str, Enum):
    allowed = 'allowed'
    review = 'review'
    denied = 'denied'


class GovernanceStatus(str, Enum):
    normal = 'normal'
    watch = 'watch'
    restricted = 'restricted'


class TransferRuleResult(BaseModel):
    rule_id: str
    outcome: Literal['pass', 'review', 'block']
    summary: str


class TransferScreeningRequest(BaseModel):
    asset_id: str = Field(..., examples=['USTB-2026'])
    sender_wallet: str
    receiver_wallet: str
    amount: float = Field(..., ge=0)
    sender_kyc_status: Literal['verified', 'pending', 'incomplete', 'rejected']
    receiver_kyc_status: Literal['verified', 'pending', 'incomplete', 'rejected']
    sender_jurisdiction: str
    receiver_jurisdiction: str
    sender_sanctions_flag: bool
    receiver_sanctions_flag: bool
    sender_accreditation_status: Literal['approved', 'pending', 'denied', 'not_required']
    receiver_accreditation_status: Literal['approved', 'pending', 'denied', 'not_required']
    asset_transfer_policy: dict[str, Any] = Field(default_factory=dict)
    wallet_tags: dict[str, list[str]] = Field(default_factory=dict)


class TransferScreeningResponse(BaseModel):
    decision: Decision
    risk_level: Literal['low', 'medium', 'high', 'critical']
    reasons: list[str]
    triggered_rules: list[TransferRuleResult]
    recommended_action: str
    wrapper_status: str
    explainability_summary: str
    policy_snapshot: dict[str, Any]


class ResidencyScreeningRequest(BaseModel):
    asset_id: str
    requested_processing_region: str
    asset_home_jurisdiction: str
    approved_regions: list[str] = Field(default_factory=list)
    restricted_regions: list[str] = Field(default_factory=list)
    sensitivity_level: Literal['standard', 'sensitive', 'restricted', 'sovereign']
    cloud_environment: str


class ResidencyScreeningResponse(BaseModel):
    residency_decision: ResidencyDecision
    policy_violations: list[str]
    routing_recommendation: str
    governance_status: GovernanceStatus
    explainability_summary: str
    allowed_region_outcome: str


class GovernanceActionRequest(BaseModel):
    action_type: Literal[
        'freeze_wallet',
        'unfreeze_wallet',
        'allowlist_wallet',
        'blocklist_wallet',
        'mark_wallet_review_required',
        'pause_asset_transfers',
        'resume_asset_transfers',
    ]
    target_type: Literal['wallet', 'asset']
    target_id: str
    actor: str
    reason: str
    related_asset_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GovernanceActionRecord(GovernanceActionRequest):
    action_id: str
    created_at: str
    status: Literal['applied']
    attestation_hash: str
    policy_effects: list[str]


TRANSFER_APPROVED_EXAMPLE = {
    'asset_id': 'USTB-2026',
    'sender_wallet': '0xaaa0000000000000000000000000000000000101',
    'receiver_wallet': '0xbbb0000000000000000000000000000000000202',
    'amount': 250000,
    'sender_kyc_status': 'verified',
    'receiver_kyc_status': 'verified',
    'sender_jurisdiction': 'US',
    'receiver_jurisdiction': 'GB',
    'sender_sanctions_flag': False,
    'receiver_sanctions_flag': False,
    'sender_accreditation_status': 'approved',
    'receiver_accreditation_status': 'approved',
    'asset_transfer_policy': {
        'restricted_jurisdictions': ['IR', 'KP'],
        'review_jurisdictions': ['RU'],
        'amount_review_threshold': 500000,
        'amount_block_threshold': 1500000,
        'requires_accreditation': True,
        'allowed_assets': ['USTB-2026', 'USTB-2027'],
    },
    'wallet_tags': {
        'sender': ['treasury-desk', 'allowlisted'],
        'receiver': ['qualified-custodian', 'allowlisted'],
    },
}

TRANSFER_REVIEW_EXAMPLE = {
    **TRANSFER_APPROVED_EXAMPLE,
    'receiver_kyc_status': 'incomplete',
    'receiver_jurisdiction': 'RU',
}

RESIDENCY_ALLOWED_EXAMPLE = {
    'asset_id': 'USTB-2026',
    'requested_processing_region': 'us-east',
    'asset_home_jurisdiction': 'US',
    'approved_regions': ['us-east', 'us-central'],
    'restricted_regions': ['cn-north', 'ru-central'],
    'sensitivity_level': 'sensitive',
    'cloud_environment': 'sovereign-cloud-a',
}

GOVERNANCE_ACTION_EXAMPLE = {
    'action_type': 'freeze_wallet',
    'target_type': 'wallet',
    'target_id': '0xddd0000000000000000000000000000000000404',
    'actor': 'governance-multisig',
    'reason': 'Escalated compliance review after repeated sanctions-adjacent transfers.',
    'related_asset_id': 'USTB-2026',
    'metadata': {'ticket': 'CMP-1042', 'severity': 'high'},
}
