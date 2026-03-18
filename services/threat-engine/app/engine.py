from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import Any, Iterable

from .schemas import (
    AnalysisResponse,
    ContractAnalysisRequest,
    DetectionRecord,
    MarketAnalysisRequest,
    PatternMatch,
    ThreatDashboardCard,
    ThreatDashboardResponse,
    TransactionAnalysisRequest,
)


@dataclass(frozen=True)
class RuleResult:
    pattern_id: str
    label: str
    weight: int
    severity: str
    reason: str
    evidence: dict[str, Any]

    def to_pattern(self) -> PatternMatch:
        return PatternMatch(
            pattern_id=self.pattern_id,
            label=self.label,
            weight=self.weight,
            severity=self.severity,  # type: ignore[arg-type]
            reason=self.reason,
            evidence=self.evidence,
        )


class ThreatEngine:
    def analyze_contract(self, request: ContractAnalysisRequest) -> AnalysisResponse:
        findings_lower = [item.lower() for item in request.findings]
        function_names = [summary.name.lower() for summary in request.function_summaries]
        risk_flags = {flag.lower() for summary in request.function_summaries for flag in summary.risk_flags}
        admin_roles = {role.lower() for role in request.admin_roles}
        actor = (request.calling_actor or '').lower()

        matches: list[RuleResult] = []

        if request.flags.get('delegatecall') or any('delegatecall' in finding for finding in findings_lower):
            matches.append(self._rule(
                'contract:delegatecall-path',
                'Low-level privilege pivot',
                18,
                'high',
                'Delegatecall appears in the execution path, which can bypass expected storage or permission boundaries.',
                {'flag': request.flags.get('delegatecall', False)},
            ))

        if request.flags.get('unsafe_admin_action') or any('privileged-admin' in flag for flag in risk_flags):
            matches.append(self._rule(
                'contract:privilege-escalation',
                'Unsafe admin action',
                26,
                'critical',
                'Privileged logic can be exercised by an unexpected actor or without a governance checkpoint.',
                {'calling_actor': request.calling_actor, 'admin_roles': request.admin_roles},
            ))

        if (
            request.flags.get('high_value_drain_path')
            or any(name in {'sweepfunds', 'withdrawall'} for name in function_names)
            or any('drain-path' in flag for flag in risk_flags)
        ):
            matches.append(self._rule(
                'contract:liquidity-drain-path',
                'Rapid drain path',
                24,
                'critical',
                'The contract exposes a full-balance or high-value drain path that could accelerate treasury outflows.',
                {'functions': function_names},
            ))

        borrow_swap_repay = {'borrow', 'swap', 'repay'}.issubset(set(function_names) | set(' '.join(findings_lower).split()))
        if request.flags.get('burst_risk_actions') or borrow_swap_repay or any('borrow / swap / repay' in finding for finding in findings_lower):
            matches.append(self._rule(
                'contract:atomic-sequence',
                'Flash-loan style sequence',
                20,
                'high',
                'Borrow / swap / repay behavior in one logic flow resembles an atomic exploit setup.',
                {'findings': request.findings},
            ))

        if request.flags.get('untrusted_external_call') or any('untrusted' in finding for finding in findings_lower):
            matches.append(self._rule(
                'contract:untrusted-integration',
                'Untrusted contract interaction',
                14,
                'medium',
                'Execution depends on an untrusted external contract, increasing the chance of malicious sequencing or callback abuse.',
                {'findings': request.findings},
            ))

        if request.created_days_ago <= 14 and not request.verified_source:
            matches.append(self._rule(
                'contract:new-unverified',
                'New unverified deployment',
                10,
                'medium',
                'The contract is recently deployed and source verification is missing.',
                {'created_days_ago': request.created_days_ago},
            ))

        if request.audit_count == 0:
            matches.append(self._rule(
                'contract:no-audit',
                'No external audit',
                8,
                'medium',
                'No external audit evidence is attached to this contract summary.',
                {'audit_count': request.audit_count},
            ))

        if admin_roles and actor and actor not in admin_roles:
            matches.append(self._rule(
                'contract:unexpected-actor',
                'Unexpected actor context',
                12,
                'high',
                'The observed caller is not in the expected admin role set for privileged contract functions.',
                {'calling_actor': request.calling_actor, 'admin_roles': request.admin_roles},
            ))

        return self._build_response('contract', matches)

    def analyze_transaction(self, request: TransactionAnalysisRequest) -> AnalysisResponse:
        sequence = [step.lower() for step in request.call_sequence]
        expected_roles = {role.lower() for role in request.expected_actor_roles}
        actor_role = (request.actor_role or '').lower()
        matches: list[RuleResult] = []

        if request.flags.get('contains_flash_loan'):
            matches.append(self._rule(
                'transaction:flash-loan-indicator',
                'Flash-loan indicator',
                28,
                'critical',
                'The transaction declares a flash-loan hop or equivalent atomic borrow step.',
                {'call_sequence': request.call_sequence},
            ))

        if {'borrow', 'swap', 'repay'}.issubset(set(sequence)):
            matches.append(self._rule(
                'transaction:borrow-swap-repay',
                'Borrow / swap / repay burst',
                18,
                'high',
                'Repeated borrow + swap + repay steps in one flow match a common flash-loan exploit pattern.',
                {'call_sequence': request.call_sequence},
            ))

        if request.flags.get('rapid_drain_indicator') or request.amount >= 1_000_000:
            matches.append(self._rule(
                'transaction:rapid-drain',
                'High-value drain attempt',
                22,
                'critical',
                'The transaction amount or flags suggest a sudden treasury drain or liquidity extraction attempt.',
                {'amount': request.amount},
            ))

        if request.flags.get('unexpected_admin_call'):
            matches.append(self._rule(
                'transaction:privilege-escalation',
                'Unexpected admin action',
                24,
                'critical',
                'An unsafe or unexpected admin action is present in the transaction flow.',
                {'actor': request.actor, 'actor_role': request.actor_role},
            ))

        if expected_roles and actor_role and actor_role not in expected_roles:
            matches.append(self._rule(
                'transaction:role-mismatch',
                'Role mismatch',
                16,
                'high',
                'The actor role is outside the expected role set for this protocol action.',
                {'actor_role': request.actor_role, 'expected_actor_roles': request.expected_actor_roles},
            ))

        if request.flags.get('untrusted_contract'):
            matches.append(self._rule(
                'transaction:untrusted-contract',
                'Untrusted contract interaction',
                14,
                'high',
                'The transaction path interacts with a contract that is not on the trusted allowlist.',
                {'protocol': request.protocol},
            ))

        if request.counterparty_reputation < 35:
            matches.append(self._rule(
                'transaction:weak-reputation',
                'Weak counterparty reputation',
                11,
                'medium',
                'Counterparty reputation is below the defensive threshold used for treasury actions.',
                {'counterparty_reputation': request.counterparty_reputation},
            ))

        if request.burst_actions_last_5m >= 4:
            matches.append(self._rule(
                'transaction:burst-actions',
                'Burst of high-risk actions',
                13,
                'high',
                'Multiple risky actions were attempted within a short time window.',
                {'burst_actions_last_5m': request.burst_actions_last_5m},
            ))

        return self._build_response('transaction', matches)

    def analyze_market(self, request: MarketAnalysisRequest) -> AnalysisResponse:
        matches: list[RuleResult] = []
        order_flow = Counter(request.order_flow_summary)
        baseline = max(request.baseline_volume, 1.0)
        volume_ratio = request.current_volume / baseline

        if volume_ratio >= 3.0 and request.participant_diversity <= 5:
            matches.append(self._rule(
                'market:volume-spike',
                'Abnormal volume spike',
                22,
                'high',
                'Volume spiked far above baseline while genuine participant diversity remained low.',
                {
                    'current_volume': request.current_volume,
                    'baseline_volume': request.baseline_volume,
                    'participant_diversity': request.participant_diversity,
                },
            ))

        if order_flow['rapid_cancellations'] >= 8 and order_flow['large_orders'] >= 10:
            matches.append(self._rule(
                'market:spoofing-orders',
                'Spoofing-like order behavior',
                24,
                'critical',
                'Large order placement followed by rapid cancellations resembles spoofing or quote stuffing.',
                dict(request.order_flow_summary),
            ))

        if order_flow['circular_trade_loops'] >= 3 or order_flow['self_trade_markers'] >= 3:
            matches.append(self._rule(
                'market:wash-trading',
                'Wash-trading-like loops',
                26,
                'critical',
                'Repeated circular trades or self-trade markers indicate non-economic volume generation.',
                dict(request.order_flow_summary),
            ))

        volatility_ratio, swing_count = self._volatility_signature(request.candles)
        if volatility_ratio >= 0.08 or order_flow['rapid_swings'] >= 5:
            matches.append(self._rule(
                'market:volatility-spike',
                'Abnormal rapid swings',
                18,
                'high',
                'Short-interval price swings exceed the expected treasury-token stability band.',
                {'volatility_ratio': round(volatility_ratio, 4), 'rapid_swings': order_flow['rapid_swings'], 'swing_count': swing_count},
            ))

        if request.dominant_cluster_share >= 0.55:
            matches.append(self._rule(
                'market:cluster-concentration',
                'Wallet cluster concentration',
                16,
                'high',
                'A single wallet cluster controls a suspiciously large share of activity.',
                {'dominant_cluster_share': request.dominant_cluster_share},
            ))

        if request.wallet_activity:
            top_cluster = max(request.wallet_activity, key=lambda item: item.trade_count)
            if top_cluster.trade_count >= 10 and len(request.wallet_activity) <= 3:
                matches.append(self._rule(
                    'market:repeat-cluster-activity',
                    'Repeated cluster recycling',
                    12,
                    'medium',
                    'A small cluster of wallets is repeatedly recycling flow through the venue.',
                    {'top_cluster': top_cluster.cluster_id, 'trade_count': top_cluster.trade_count},
                ))

        return self._build_response('market', matches, anomaly_types=[match.label for match in matches])

    def build_dashboard(self, scenarios: dict[str, Any]) -> ThreatDashboardResponse:
        safe_tx = self.analyze_transaction(scenarios['safe_transaction'])
        flash_tx = self.analyze_transaction(scenarios['flash_loan_transaction'])
        admin_tx = self.analyze_transaction(scenarios['admin_privilege_transaction'])
        normal_market = self.analyze_market(scenarios['normal_market'])
        spoof_market = self.analyze_market(scenarios['spoofing_market'])
        wash_market = self.analyze_market(scenarios['wash_trading_market'])
        contract = self.analyze_contract(scenarios['contract'])

        detections = [flash_tx, admin_tx, spoof_market, wash_market, contract, safe_tx, normal_market]
        recent = [
            self._record('det-001', 'transaction', 'Suspicious flash-loan-like transaction', flash_tx),
            self._record('det-002', 'transaction', 'Admin privilege abuse scenario', admin_tx),
            self._record('det-003', 'market', 'Spoofing-like treasury token market', spoof_market),
            self._record('det-004', 'market', 'Wash-trading-like treasury token market', wash_market),
            self._record('det-005', 'contract', 'Proxy router contract scan', contract),
            self._record('det-006', 'transaction', 'Safe treasury settlement', safe_tx),
        ]
        active_alerts = [record for record in recent if record.score >= 50][:5]
        avg_score = round(mean(item.score for item in detections), 1)
        block_count = sum(item.recommended_action == 'block' for item in detections)
        review_count = sum(item.recommended_action == 'review' for item in detections)

        cards = [
            ThreatDashboardCard(label='Threat score', value=str(contract.score), detail='Contract scan composite score from deterministic rules.', tone=contract.severity),
            ThreatDashboardCard(label='Active alerts', value=str(len(active_alerts)), detail='Critical and high-confidence exploit or anomaly detections.', tone='high' if active_alerts else 'low'),
            ThreatDashboardCard(label='Blocked / reviewed', value=f'{block_count}/{review_count}', detail='Action decisions produced by the explainable scoring layer.', tone='medium'),
            ThreatDashboardCard(label='Market anomaly avg', value=str(round((spoof_market.score + wash_market.score + normal_market.score) / 3, 1)), detail='Average anomaly score across bundled treasury-token scenarios.', tone='high' if spoof_market.score >= 50 or wash_market.score >= 50 else 'low'),
        ]

        return ThreatDashboardResponse(
            source='live',
            generated_at='2026-03-18T10:00:00Z',
            summary={
                'average_score': avg_score,
                'critical_or_high_alerts': len(active_alerts),
                'blocked_actions': block_count,
                'review_actions': review_count,
                'market_anomaly_types': sorted({label for item in (spoof_market, wash_market) for label in item.metadata.get('anomaly_types', [])}),
            },
            cards=cards,
            active_alerts=active_alerts,
            recent_detections=recent,
            sample_scenarios={
                'safe_transaction': 'Safe transaction',
                'flash_loan_transaction': 'Suspicious flash-loan-like transaction',
                'admin_privilege_transaction': 'Admin privilege abuse scenario',
                'normal_market': 'Normal market behavior',
                'spoofing_market': 'Spoofing-like market behavior',
                'wash_trading_market': 'Wash-trading-like market behavior',
            },
            message='Threat dashboard is driven by deterministic weighted rules so each score remains explainable and demoable.',
        )

    @staticmethod
    def _rule(pattern_id: str, label: str, weight: int, severity: str, reason: str, evidence: dict[str, Any]) -> RuleResult:
        return RuleResult(pattern_id=pattern_id, label=label, weight=weight, severity=severity, reason=reason, evidence=evidence)

    def _build_response(self, analysis_type: str, matches: Iterable[RuleResult], anomaly_types: list[str] | None = None) -> AnalysisResponse:
        materialized = list(matches)
        raw_score = sum(match.weight for match in materialized)
        score = max(0, min(100, raw_score))
        severity = self._severity(score)
        action = self._action(score)
        reasons = [match.reason for match in materialized]
        explanation = (
            f'{analysis_type.title()} analysis produced score {score} ({severity}) and action {action}. '
            + ('Primary drivers: ' + '; '.join(reasons[:3]) if reasons else 'No suspicious patterns matched the current rule set.')
        )
        metadata: dict[str, Any] = {'normalized_score': score}
        if anomaly_types is not None:
            metadata['anomaly_types'] = anomaly_types
        return AnalysisResponse(
            analysis_type=analysis_type,  # type: ignore[arg-type]
            score=score,
            severity=severity,  # type: ignore[arg-type]
            matched_patterns=[match.to_pattern() for match in materialized],
            explanation=explanation,
            recommended_action=action,  # type: ignore[arg-type]
            reasons=reasons,
            metadata=metadata,
        )

    @staticmethod
    def _severity(score: int) -> str:
        if score >= 75:
            return 'critical'
        if score >= 50:
            return 'high'
        if score >= 25:
            return 'medium'
        return 'low'

    @staticmethod
    def _action(score: int) -> str:
        if score >= 70:
            return 'block'
        if score >= 35:
            return 'review'
        return 'allow'

    @staticmethod
    def _volatility_signature(candles: list[Any]) -> tuple[float, int]:
        if not candles:
            return 0.0, 0
        baseline = max(min(candle.low for candle in candles), 0.0001)
        total_range = max(candle.high for candle in candles) - min(candle.low for candle in candles)
        swing_count = sum(1 for candle in candles if abs(candle.close - candle.open) / max(candle.open, 0.0001) >= 0.015)
        return total_range / baseline, swing_count

    @staticmethod
    def _record(record_id: str, category: str, title: str, response: AnalysisResponse) -> DetectionRecord:
        return DetectionRecord(
            id=record_id,
            category=category,  # type: ignore[arg-type]
            title=title,
            score=response.score,
            severity=response.severity,
            action=response.recommended_action,
            source='live',
            explanation=response.explanation,
            patterns=[item.label for item in response.matched_patterns],
        )
