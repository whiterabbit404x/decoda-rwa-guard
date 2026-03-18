from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas import (
    Decision,
    GovernanceActionRequest,
    GovernanceActionRecord,
    GovernanceStatus,
    ResidencyDecision,
    ResidencyScreeningRequest,
    ResidencyScreeningResponse,
    TransferRuleResult,
    TransferScreeningRequest,
    TransferScreeningResponse,
)
from app.store import ComplianceStore

DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
SCENARIOS: dict[str, dict[str, str]] = {
    'compliant-transfer-approved': {'description': 'Compliant transfer that should be approved.', 'file': 'compliant_transfer_approved.json', 'type': 'transfer'},
    'blocked-transfer-sanctions': {'description': 'Transfer blocked because sanctions screening failed.', 'file': 'blocked_transfer_sanctions.json', 'type': 'transfer'},
    'blocked-transfer-blocklist': {'description': 'Transfer blocked because a wallet is blocklisted.', 'file': 'blocked_transfer_blocklist.json', 'type': 'transfer'},
    'review-transfer-incomplete-kyc': {'description': 'Transfer sent to review because KYC is incomplete.', 'file': 'review_transfer_incomplete_kyc.json', 'type': 'transfer'},
    'review-transfer-restricted-jurisdiction': {'description': 'Transfer sent to review due to restricted jurisdiction policy.', 'file': 'review_transfer_restricted_jurisdiction.json', 'type': 'transfer'},
    'denied-residency-restricted-region': {'description': 'Residency request denied due to restricted processing region.', 'file': 'denied_residency_restricted_region.json', 'type': 'residency'},
    'governance-freeze-wallet': {'description': 'Governance action freezing a wallet.', 'file': 'governance_freeze_wallet.json', 'type': 'governance'},
    'governance-pause-asset': {'description': 'Governance action pausing asset transfers.', 'file': 'governance_pause_asset.json', 'type': 'governance'},
    'governance-allowlist-wallet': {'description': 'Governance action allowlisting a wallet.', 'file': 'governance_allowlist_wallet.json', 'type': 'governance'},
    'transfer-blocked-because-asset-paused': {'description': 'Transfer blocked because the asset is paused.', 'file': 'transfer_blocked_asset_paused.json', 'type': 'transfer'},
}


class ComplianceEngine:
    def __init__(self, store: ComplianceStore | None = None) -> None:
        self.store = store or ComplianceStore()

    def screen_transfer(self, request: TransferScreeningRequest) -> TransferScreeningResponse:
        state = self.store.load_policy_state()
        policy = request.asset_transfer_policy
        triggered_rules: list[TransferRuleResult] = []
        reasons: list[str] = []
        severity = 'low'
        decision = Decision.approved

        def apply_rule(rule_id: str, outcome: str, summary: str) -> None:
            nonlocal decision, severity
            triggered_rules.append(TransferRuleResult(rule_id=rule_id, outcome=outcome, summary=summary))
            if outcome != 'pass':
                reasons.append(summary)
            if outcome == 'block':
                decision = Decision.blocked
                severity = 'critical'
            elif outcome == 'review' and decision != Decision.blocked:
                decision = Decision.review
                severity = 'high' if severity in {'low', 'medium'} else severity
            elif outcome == 'pass' and severity == 'low':
                severity = 'low'

        sanctioned = request.sender_sanctions_flag or request.receiver_sanctions_flag
        apply_rule(
            'sanctions-screen',
            'block' if sanctioned else 'pass',
            'Sanctions/watchlist screening failed for one or more wallets.' if sanctioned else 'No sanctions/watchlist hits detected.',
        )

        blocklisted = any(wallet in state['blocklisted_wallets'] for wallet in (request.sender_wallet, request.receiver_wallet))
        apply_rule(
            'wallet-blocklist',
            'block' if blocklisted else 'pass',
            'A participating wallet is currently blocklisted by governance policy.' if blocklisted else 'No participating wallets are blocklisted.',
        )

        frozen = any(wallet in state['frozen_wallets'] for wallet in (request.sender_wallet, request.receiver_wallet))
        apply_rule(
            'wallet-freeze',
            'block' if frozen else 'pass',
            'A participating wallet is currently frozen by governance action.' if frozen else 'No participating wallets are frozen.',
        )

        asset_paused = request.asset_id in state['paused_assets'] or policy.get('asset_status') == 'paused'
        apply_rule(
            'asset-transfer-status',
            'block' if asset_paused else 'pass',
            'Asset transfers are currently paused for this asset.' if asset_paused else 'Asset transfer status is active.',
        )

        kyc_incomplete = request.sender_kyc_status != 'verified' or request.receiver_kyc_status != 'verified'
        apply_rule(
            'kyc-status',
            'review' if kyc_incomplete else 'pass',
            'One or more wallets have incomplete or pending KYC status.' if kyc_incomplete else 'Sender and receiver KYC controls are complete.',
        )

        review_wallet = any(wallet in state['review_required_wallets'] for wallet in (request.sender_wallet, request.receiver_wallet))
        apply_rule(
            'wallet-review-flag',
            'review' if review_wallet else 'pass',
            'A participating wallet requires manual compliance review.' if review_wallet else 'No manual wallet review flags are active.',
        )

        restricted_jurisdictions = set(policy.get('restricted_jurisdictions', []))
        review_jurisdictions = set(policy.get('review_jurisdictions', []))
        jurisdictions = {request.sender_jurisdiction, request.receiver_jurisdiction}
        jurisdiction_block = bool(jurisdictions & restricted_jurisdictions and policy.get('restricted_jurisdiction_action', 'review') == 'block')
        jurisdiction_review = bool(jurisdictions & (restricted_jurisdictions | review_jurisdictions)) and not jurisdiction_block
        apply_rule(
            'jurisdiction-policy',
            'block' if jurisdiction_block else 'review' if jurisdiction_review else 'pass',
            'A participating jurisdiction is restricted by asset policy.' if jurisdiction_block else 'A participating jurisdiction requires manual review.' if jurisdiction_review else 'Jurisdiction controls passed.',
        )

        requires_accreditation = bool(policy.get('requires_accreditation', False))
        accredited = request.sender_accreditation_status == 'approved' and request.receiver_accreditation_status == 'approved'
        accreditation_outcome = 'pass'
        accreditation_summary = 'Accreditation/authorization controls passed.'
        if requires_accreditation and not accredited:
            accreditation_outcome = 'review'
            accreditation_summary = 'One or more parties are missing the required accreditation/authorization status.'
            if 'denied' in {request.sender_accreditation_status, request.receiver_accreditation_status}:
                accreditation_outcome = 'block'
                accreditation_summary = 'A participating party failed the required accreditation/authorization control.'
        apply_rule('accreditation-status', accreditation_outcome, accreditation_summary)

        allowed_assets = set(policy.get('allowed_assets', []))
        asset_restricted = bool(allowed_assets) and request.asset_id not in allowed_assets
        apply_rule(
            'asset-transfer-policy',
            'block' if asset_restricted else 'pass',
            'Asset is not authorized for transfer under the current wrapper policy.' if asset_restricted else 'Asset-level transfer policy passed.',
        )

        amount_review_threshold = float(policy.get('amount_review_threshold', 500000))
        amount_block_threshold = float(policy.get('amount_block_threshold', 1500000))
        amount_action = policy.get('amount_threshold_action', 'review')
        amount_outcome = 'pass'
        amount_summary = 'Transfer amount is within approved thresholds.'
        if request.amount >= amount_block_threshold:
            amount_outcome = 'block' if amount_action == 'block' else 'review'
            amount_summary = 'Transfer amount breached the hard threshold for this asset policy.'
        elif request.amount >= amount_review_threshold:
            amount_outcome = 'review'
            amount_summary = 'Transfer amount breached the review threshold for this asset policy.'
        apply_rule('amount-threshold', amount_outcome, amount_summary)

        sender_tags = set(request.wallet_tags.get('sender', []))
        receiver_tags = set(request.wallet_tags.get('receiver', []))
        allowlisted = (
            request.sender_wallet in state['allowlisted_wallets']
            or request.receiver_wallet in state['allowlisted_wallets']
            or 'allowlisted' in sender_tags
            or 'allowlisted' in receiver_tags
        )
        apply_rule(
            'wallet-allowlist',
            'pass' if allowlisted else 'review' if policy.get('require_known_wallet', False) else 'pass',
            'At least one participating wallet is allowlisted or tagged as trusted.' if allowlisted else 'Wallets are not explicitly allowlisted, but policy does not require it.' if not policy.get('require_known_wallet', False) else 'Wallet is not on the required allowlist.',
        )

        if decision == Decision.approved:
            severity = 'low'
        elif decision == Decision.review and severity != 'critical':
            severity = 'high' if any(rule.outcome == 'review' for rule in triggered_rules) else 'medium'

        recommended_action = {
            Decision.approved: 'Proceed with wrapped transfer execution.',
            Decision.review: 'Escalate to compliance operations for manual approval.',
            Decision.blocked: 'Reject the transfer and record an exception in governance audit logs.',
        }[decision]
        wrapper_status = {
            Decision.approved: 'wrapper-clear',
            Decision.review: 'wrapper-hold',
            Decision.blocked: 'wrapper-blocked',
        }[decision]
        summary = f"Decision {decision.value}: {reasons[0] if reasons else 'all required compliance controls passed.'}"

        return TransferScreeningResponse(
            decision=decision,
            risk_level=severity,
            reasons=reasons or ['All required compliance controls passed.'],
            triggered_rules=triggered_rules,
            recommended_action=recommended_action,
            wrapper_status=wrapper_status,
            explainability_summary=summary,
            policy_snapshot={
                'allowlisted_wallets': len(state['allowlisted_wallets']),
                'blocklisted_wallets': len(state['blocklisted_wallets']),
                'frozen_wallets': len(state['frozen_wallets']),
                'review_required_wallets': len(state['review_required_wallets']),
                'paused_assets': list(state['paused_assets']),
            },
        )

    def screen_residency(self, request: ResidencyScreeningRequest) -> ResidencyScreeningResponse:
        state = self.store.load_policy_state()
        approved_regions = set(request.approved_regions or state['approved_cloud_regions'])
        restricted_regions = set(request.restricted_regions or state['restricted_regions'])
        friendly_regions = set(state['friendly_regions'])
        violations: list[str] = []

        if request.requested_processing_region in restricted_regions:
            violations.append('Requested processing region is on the restricted region list.')
        if request.requested_processing_region not in approved_regions:
            violations.append('Requested processing region is not on the approved cloud region list.')
        if request.sensitivity_level in {'restricted', 'sovereign'} and request.requested_processing_region not in friendly_regions:
            violations.append('Sensitivity level requires processing in a friendly or sovereign-aligned region.')
        if request.sensitivity_level == 'sovereign' and not request.cloud_environment.startswith('sovereign'):
            violations.append('Sovereign data requires a sovereign cloud environment.')

        if violations:
            decision = ResidencyDecision.denied if any('restricted region list' in item or 'sovereign cloud environment' in item for item in violations) else ResidencyDecision.review
        else:
            decision = ResidencyDecision.allowed

        governance_status = GovernanceStatus.normal
        if request.asset_id in state['paused_assets']:
            governance_status = GovernanceStatus.restricted
        elif request.sensitivity_level in {'restricted', 'sovereign'}:
            governance_status = GovernanceStatus.watch

        if decision == ResidencyDecision.allowed:
            routing_recommendation = f"Route processing to {request.requested_processing_region} in {request.cloud_environment}."
            allowed_region_outcome = request.requested_processing_region
        else:
            fallback_region = next(iter(sorted(approved_regions & friendly_regions)), 'manual-sovereign-review')
            routing_recommendation = f"Route processing to {fallback_region} or request governance override."
            allowed_region_outcome = fallback_region

        summary = 'Residency controls passed without violations.' if not violations else '; '.join(violations)
        return ResidencyScreeningResponse(
            residency_decision=decision,
            policy_violations=violations,
            routing_recommendation=routing_recommendation,
            governance_status=governance_status,
            explainability_summary=summary,
            allowed_region_outcome=allowed_region_outcome,
        )

    def get_policy_state(self) -> dict[str, Any]:
        state = self.store.load_policy_state()
        actions = self.store.load_actions()
        return {
            **state,
            'action_count': len(actions),
            'latest_action_id': actions[-1]['action_id'] if actions else None,
        }

    def list_actions(self) -> list[dict[str, Any]]:
        return list(reversed(self.store.load_actions()))

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        for action in self.store.load_actions():
            if action['action_id'] == action_id:
                return action
        return None

    def apply_governance_action(self, request: GovernanceActionRequest) -> GovernanceActionRecord:
        state = self.store.load_policy_state()
        effects: list[str] = []
        target = request.target_id
        asset_id = request.related_asset_id or request.target_id

        def add_unique(key: str, value: str, effect: str) -> None:
            if value not in state[key]:
                state[key].append(value)
            effects.append(effect)

        def remove_if_present(key: str, value: str, effect: str) -> None:
            if value in state[key]:
                state[key].remove(value)
            effects.append(effect)

        match request.action_type:
            case 'freeze_wallet':
                add_unique('frozen_wallets', target, f'Wallet {target} frozen.')
            case 'unfreeze_wallet':
                remove_if_present('frozen_wallets', target, f'Wallet {target} unfrozen.')
            case 'allowlist_wallet':
                add_unique('allowlisted_wallets', target, f'Wallet {target} added to allowlist.')
                if target in state['blocklisted_wallets']:
                    state['blocklisted_wallets'].remove(target)
                    effects.append(f'Wallet {target} removed from blocklist because allowlist took precedence in demo policy.')
            case 'blocklist_wallet':
                add_unique('blocklisted_wallets', target, f'Wallet {target} added to blocklist.')
                if target in state['allowlisted_wallets']:
                    state['allowlisted_wallets'].remove(target)
                    effects.append(f'Wallet {target} removed from allowlist because blocklist took precedence in demo policy.')
            case 'mark_wallet_review_required':
                add_unique('review_required_wallets', target, f'Wallet {target} marked as review-required.')
            case 'pause_asset_transfers':
                add_unique('paused_assets', asset_id, f'Asset {asset_id} transfer activity paused.')
            case 'resume_asset_transfers':
                remove_if_present('paused_assets', asset_id, f'Asset {asset_id} transfer activity resumed.')

        state['allowlisted_wallets'] = sorted(set(state['allowlisted_wallets']))
        state['blocklisted_wallets'] = sorted(set(state['blocklisted_wallets']))
        state['frozen_wallets'] = sorted(set(state['frozen_wallets']))
        state['review_required_wallets'] = sorted(set(state['review_required_wallets']))
        state['paused_assets'] = sorted(set(state['paused_assets']))
        self.store.save_policy_state(state)
        record = self.store.create_action_record(request.model_dump(), effects)
        return GovernanceActionRecord.model_validate(record)

    def dashboard(self) -> dict[str, Any]:
        policy_state = self.get_policy_state()
        latest_actions = self.list_actions()[:5]
        transfer_eval = self.screen_transfer(TransferScreeningRequest.model_validate(self.load_scenario_data('compliant-transfer-approved')))
        residency_eval = self.screen_residency(ResidencyScreeningRequest.model_validate(self.load_scenario_data('denied-residency-restricted-region')))
        cards = [
            {
                'label': 'Transfer decision',
                'value': transfer_eval.decision.value,
                'detail': transfer_eval.explainability_summary,
                'tone': transfer_eval.risk_level,
            },
            {
                'label': 'Compliance risk',
                'value': transfer_eval.risk_level,
                'detail': f"{len(transfer_eval.triggered_rules)} rule evaluations recorded.",
                'tone': transfer_eval.risk_level,
            },
            {
                'label': 'Governance actions',
                'value': str(policy_state['action_count']),
                'detail': 'Immutable-style local audit trail entries stored on disk.',
                'tone': 'medium',
            },
            {
                'label': 'Residency decision',
                'value': residency_eval.residency_decision.value,
                'detail': residency_eval.routing_recommendation,
                'tone': 'high' if residency_eval.residency_decision != ResidencyDecision.allowed else 'low',
            },
        ]
        return {
            'source': 'live',
            'degraded': False,
            'generated_at': latest_actions[0]['created_at'] if latest_actions else '2026-03-18T00:00:00Z',
            'summary': {
                'allowlisted_wallet_count': len(policy_state['allowlisted_wallets']),
                'blocklisted_wallet_count': len(policy_state['blocklisted_wallets']),
                'frozen_wallet_count': len(policy_state['frozen_wallets']),
                'review_required_wallet_count': len(policy_state['review_required_wallets']),
                'paused_asset_count': len(policy_state['paused_assets']),
                'latest_transfer_decision': transfer_eval.decision.value,
                'latest_residency_decision': residency_eval.residency_decision.value,
                'triggered_rule_count': len([rule for rule in transfer_eval.triggered_rules if rule.outcome != 'pass']),
            },
            'cards': cards,
            'transfer_screening': transfer_eval.model_dump(),
            'residency_screening': residency_eval.model_dump(),
            'policy_state': policy_state,
            'latest_governance_actions': latest_actions,
            'asset_transfer_status': [
                {'asset_id': asset, 'status': 'paused' if asset in policy_state['paused_assets'] else 'active'}
                for asset in sorted(set(policy_state['paused_assets'] + ['USTB-2026', 'USTB-2027']))
            ],
            'sample_scenarios': {name: details['description'] for name, details in SCENARIOS.items()},
            'message': 'Compliance dashboard data loaded from deterministic local policy wrappers and governance ledger.',
        }

    def load_scenario_data(self, name: str) -> dict[str, Any]:
        details = SCENARIOS[name]
        return __import__('json').loads((DATA_DIR / details['file']).read_text())

    def list_scenarios(self) -> list[dict[str, str]]:
        return [
            {'scenario': name, 'description': details['description'], 'sample_path': str(DATA_DIR / details['file']), 'scenario_type': details['type']}
            for name, details in SCENARIOS.items()
        ]

    def scenario(self, name: str) -> dict[str, Any] | None:
        details = SCENARIOS.get(name)
        if details is None:
            return None
        return {
            'scenario': name,
            'description': details['description'],
            'scenario_type': details['type'],
            'data': self.load_scenario_data(name),
        }
