from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any

from .schemas import (
    ContractMetadata,
    DecodedFunctionCall,
    MarketEvent,
    RiskEvaluationRequest,
    RiskEvaluationResponse,
    TransactionPayload,
    TriggeredRule,
    WalletReputation,
)

SUSPICIOUS_FUNCTIONS = {
    'delegatecall': ('critical', 28, 'Low-level delegatecall requested in decoded interaction.'),
    'upgradeTo': ('high', 18, 'Upgrade-style function call can change contract behavior at runtime.'),
    'setOwner': ('high', 16, 'Privileged ownership mutation detected before transaction execution.'),
    'emergencyWithdraw': ('high', 18, 'Emergency withdrawal path can bypass normal accounting checks.'),
    'sweep': ('medium', 12, 'Asset sweep function requested against a smart contract.'),
    'withdrawAll': ('high', 16, 'Full balance withdrawal pattern increases drain risk.'),
    'flashLoan': ('critical', 22, 'Flash-loan related function call detected.'),
    'multicall': ('medium', 10, 'Bundled multicall interaction can obscure combined intent.'),
}

STATIC_FLAG_RULES = {
    'uses_delegatecall': ('critical', 20, 'Contract source indicates delegatecall usage.'),
    'selfdestruct_enabled': ('critical', 24, 'Contract can self-destruct or equivalent shutdown path exists.'),
    'unrestricted_mint': ('critical', 22, 'Contract metadata indicates unrestricted mint capability.'),
    'hidden_owner': ('high', 16, 'Opaque owner/admin control path present in source summary.'),
    'external_call_in_loop': ('high', 14, 'External call inside loop may enable re-entrancy or griefing.'),
    'oracle_dependency_unbounded': ('medium', 10, 'Unbounded oracle dependency can amplify manipulated inputs.'),
    'obfuscated_storage': ('medium', 8, 'Obfuscated storage or proxy slot behavior reduces transparency.'),
}


def build_rule(rule_id: str, category: str, score_impact: int, severity: str, summary: str, evidence: dict[str, Any]) -> TriggeredRule:
    return TriggeredRule(
        rule_id=rule_id,
        category=category,  # type: ignore[arg-type]
        score_impact=score_impact,
        severity=severity,  # type: ignore[arg-type]
        summary=summary,
        evidence=evidence,
    )


class RiskEngine:
    def evaluate(self, request: RiskEvaluationRequest) -> RiskEvaluationResponse:
        category_rules = {
            'pre_transaction': self._score_pre_transaction(
                request.transaction_payload,
                request.decoded_function_call,
                request.wallet_reputation,
                request.contract_metadata,
            ),
            'static': self._score_static(request.contract_metadata),
            'runtime': self._score_runtime(
                request.transaction_payload,
                request.decoded_function_call,
                request.recent_market_events,
            ),
            'market': self._score_market(request.recent_market_events),
        }

        triggered_rules = [rule for rules in category_rules.values() for rule in rules]
        raw_score = sum(rule.score_impact for rule in triggered_rules)
        if request.wallet_reputation.known_safe:
            raw_score -= 8
        if request.wallet_reputation.kyc_verified:
            raw_score -= 5
        if request.contract_metadata.verified_source:
            raw_score -= 4
        if request.contract_metadata.audit_count >= 2:
            raw_score -= 4
        risk_score = max(0, min(100, raw_score))
        recommendation = self._recommendation(risk_score)
        explanation = self._explanation(triggered_rules, recommendation, risk_score)
        category_scores = {
            category: max(0, min(100, sum(rule.score_impact for rule in rules)))
            for category, rules in category_rules.items()
        }
        return RiskEvaluationResponse(
            risk_score=risk_score,
            triggered_rules=triggered_rules,
            explanation=explanation,
            recommendation=recommendation,
            category_scores=category_scores,
        )

    def _score_pre_transaction(
        self,
        payload: TransactionPayload,
        call: DecodedFunctionCall,
        wallet: WalletReputation,
        contract: ContractMetadata,
    ) -> list[TriggeredRule]:
        rules: list[TriggeredRule] = []
        fn_name = call.function_name.strip()
        if fn_name in SUSPICIOUS_FUNCTIONS:
            severity, impact, summary = SUSPICIOUS_FUNCTIONS[fn_name]
            rules.append(build_rule(
                f'pre:{fn_name}', 'pre_transaction', impact, severity, summary,
                {'function_name': fn_name, 'contract_name': call.contract_name},
            ))

        privileged_keywords = {'owner', 'admin', 'implementation', 'router'}
        touched_privileged_args = sorted(k for k in call.arguments if any(word in k.lower() for word in privileged_keywords))
        if touched_privileged_args:
            rules.append(build_rule(
                'pre:privileged-args', 'pre_transaction', 12, 'medium',
                'Call arguments include privileged control fields.',
                {'arguments': touched_privileged_args},
            ))

        if payload.value >= 1_000_000:
            rules.append(build_rule(
                'pre:high-value', 'pre_transaction', 14, 'high',
                'Transaction notional exceeds the Phase 1 high-value threshold.',
                {'value': payload.value},
            ))

        if wallet.score < 35 or wallet.prior_flags >= 2 or wallet.sanctions_hits > 0:
            rules.append(build_rule(
                'pre:wallet-reputation', 'pre_transaction', 20, 'high',
                'Wallet reputation is weak relative to defensive transaction policy.',
                {
                    'wallet_score': wallet.score,
                    'prior_flags': wallet.prior_flags,
                    'sanctions_hits': wallet.sanctions_hits,
                },
            ))

        if contract.created_days_ago < 14 and not contract.verified_source:
            rules.append(build_rule(
                'pre:new-unverified-contract', 'pre_transaction', 15, 'high',
                'Destination contract is both recently deployed and unverified.',
                {'created_days_ago': contract.created_days_ago},
            ))

        return rules

    def _score_static(self, contract: ContractMetadata) -> list[TriggeredRule]:
        rules: list[TriggeredRule] = []
        for flag, enabled in contract.static_flags.items():
            if not enabled or flag not in STATIC_FLAG_RULES:
                continue
            severity, impact, summary = STATIC_FLAG_RULES[flag]
            rules.append(build_rule(
                f'static:{flag}', 'static', impact, severity, summary, {'flag': flag}
            ))

        if contract.proxy and contract.audit_count == 0:
            rules.append(build_rule(
                'static:unaudited-proxy', 'static', 12, 'medium',
                'Proxy contract without audits increases implementation-switch risk.',
                {'proxy': True, 'audit_count': contract.audit_count},
            ))

        if 'mixer' in {category.lower() for category in contract.categories}:
            rules.append(build_rule(
                'static:mixer-category', 'static', 25, 'critical',
                'Contract category is associated with obfuscation or laundering workflows.',
                {'categories': contract.categories},
            ))

        return rules

    def _score_runtime(
        self,
        payload: TransactionPayload,
        call: DecodedFunctionCall,
        events: list[MarketEvent],
    ) -> list[TriggeredRule]:
        rules: list[TriggeredRule] = []
        liquidity_drops = [event.liquidity_change for event in events if event.liquidity_change is not None and event.liquidity_change < 0]
        if liquidity_drops and min(liquidity_drops) <= -0.35:
            rules.append(build_rule(
                'runtime:liquidity-drain', 'runtime', 24, 'critical',
                'Observed recent liquidity contraction matches flash-loan drain behavior.',
                {'largest_liquidity_drop': min(liquidity_drops)},
            ))

        if call.function_name == 'flashLoan' or payload.metadata.get('contains_flash_loan_hop'):
            rules.append(build_rule(
                'runtime:flash-loan-hop', 'runtime', 22, 'critical',
                'Transaction path includes an explicit flash-loan hop indicator.',
                {'contains_flash_loan_hop': bool(payload.metadata.get('contains_flash_loan_hop'))},
            ))

        event_types = Counter(event.event_type for event in events)
        if event_types.get('swap', 0) >= 3 and event_types.get('borrow', 0) >= 1 and event_types.get('repay', 0) >= 1:
            rules.append(build_rule(
                'runtime:borrow-swap-repay', 'runtime', 16, 'high',
                'Borrow/swap/repay burst suggests atomic leverage or drain sequencing.',
                {'event_counts': dict(event_types)},
            ))

        if payload.token_transfers and len(payload.token_transfers) >= 4:
            rules.append(build_rule(
                'runtime:transfer-fanout', 'runtime', 10, 'medium',
                'High token transfer fan-out can indicate rapid liquidity extraction.',
                {'transfer_count': len(payload.token_transfers)},
            ))

        return rules

    def _score_market(self, events: list[MarketEvent]) -> list[TriggeredRule]:
        rules: list[TriggeredRule] = []
        if not events:
            return rules

        volumes = [event.volume for event in events if event.volume is not None]
        prices = [event.price for event in events if event.price is not None]
        cancellation_rates = [event.cancellation_rate for event in events if event.cancellation_rate is not None]
        trader_counts = Counter(event.trader_id for event in events if event.trader_id)
        event_types = Counter(event.event_type for event in events)

        if volumes:
            avg_volume = mean(volumes)
            max_volume = max(volumes)
            if avg_volume > 0 and max_volume / avg_volume >= 3.0:
                rules.append(build_rule(
                    'market:volume-spike', 'market', 14, 'high',
                    'Recent market data shows an outsized volume spike versus local baseline.',
                    {'avg_volume': round(avg_volume, 4), 'max_volume': max_volume},
                ))

        if prices and len(prices) >= 3:
            price_range = max(prices) - min(prices)
            baseline = max(min(prices), 0.0001)
            end_reversal = abs(prices[-1] - prices[0]) / baseline
            if price_range / baseline >= 0.12 and end_reversal <= 0.03:
                rules.append(build_rule(
                    'market:spoofing-reversal', 'market', 18, 'high',
                    'Price moved sharply and reverted quickly, consistent with spoofing pressure.',
                    {'price_range': round(price_range, 6), 'start_price': prices[0], 'end_price': prices[-1]},
                ))

        if cancellation_rates and max(cancellation_rates) >= 0.8:
            rules.append(build_rule(
                'market:cancel-burst', 'market', 12, 'medium',
                'Elevated order cancellation ratio suggests quote stuffing or spoofing.',
                {'max_cancellation_rate': max(cancellation_rates)},
            ))

        if trader_counts:
            dominant_trader, dominant_count = trader_counts.most_common(1)[0]
            if dominant_count / len([event for event in events if event.trader_id]) >= 0.6 and event_types.get('trade', 0) >= 4:
                rules.append(build_rule(
                    'market:wash-trading-concentration', 'market', 20, 'critical',
                    'Single participant dominates recent trade flow, consistent with wash trading.',
                    {'dominant_trader': dominant_trader, 'trade_share': round(dominant_count / max(1, event_types.get('trade', 1)), 4)},
                ))

        self_trade_markers = sum(1 for event in events if event.metadata.get('self_trade_suspected'))
        if self_trade_markers >= 2:
            rules.append(build_rule(
                'market:self-trade-markers', 'market', 15, 'high',
                'Multiple self-trade markers were observed in recent event metadata.',
                {'self_trade_markers': self_trade_markers},
            ))

        return rules

    @staticmethod
    def _recommendation(risk_score: int) -> str:
        if risk_score >= 75:
            return 'BLOCK'
        if risk_score >= 45:
            return 'REVIEW'
        return 'ALLOW'

    @staticmethod
    def _explanation(triggered_rules: list[TriggeredRule], recommendation: str, risk_score: int) -> str:
        if not triggered_rules:
            return f'No defensive heuristics triggered. Aggregate score {risk_score} leads to recommendation {recommendation}.'
        top_rules = sorted(triggered_rules, key=lambda rule: rule.score_impact, reverse=True)[:3]
        rule_summaries = '; '.join(rule.summary for rule in top_rules)
        return (
            f'Aggregate score {risk_score} produced recommendation {recommendation}. '
            f'Primary drivers: {rule_summaries}'
        )
