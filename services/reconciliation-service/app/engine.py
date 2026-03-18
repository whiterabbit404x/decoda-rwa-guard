from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import BackstopRequest, IncidentRecord, IncidentRecordRequest, ReconciliationRequest
from app.store import ReconciliationStore

DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
SCENARIOS: dict[str, dict[str, str]] = {
    'healthy-matched-multi-ledger-state': {'description': 'Healthy matched supply across ethereum, avalanche, and private-bank-ledger.', 'file': 'healthy_matched_multi_ledger_state.json', 'type': 'reconcile'},
    'mild-mismatch-warning': {'description': 'Small mismatch with manageable settlement lag.', 'file': 'mild_mismatch_warning.json', 'type': 'reconcile'},
    'critical-supply-divergence-double-count-risk': {'description': 'Critical over-reporting across ledgers indicating double-count risk.', 'file': 'critical_supply_divergence_double_count_risk.json', 'type': 'reconcile'},
    'stale-private-ledger-data': {'description': 'Private ledger data is stale and penalized.', 'file': 'stale_private_ledger_data.json', 'type': 'reconcile'},
    'high-volatility-alert': {'description': 'High volatility produces a deterministic alert decision.', 'file': 'high_volatility_alert.json', 'type': 'backstop'},
    'cyber-triggered-restricted-mode': {'description': 'Cyber + volatility combination restricts controls.', 'file': 'cyber_triggered_restricted_mode.json', 'type': 'backstop'},
    'critical-mismatch-paused-bridge': {'description': 'Critical reconciliation mismatch pauses bridge and settlement.', 'file': 'critical_mismatch_paused_bridge.json', 'type': 'backstop'},
    'incident-record-reconciliation-failure': {'description': 'Incident example for a reconciliation failure.', 'file': 'incident_record_reconciliation_failure.json', 'type': 'incident'},
    'incident-record-market-circuit-breaker': {'description': 'Incident example for a market circuit breaker.', 'file': 'incident_record_market_circuit_breaker.json', 'type': 'incident'},
    'recovery-normal-mode-after-alert': {'description': 'Recovery scenario returning to normal mode after prior alert.', 'file': 'recovery_normal_mode_after_alert.json', 'type': 'backstop'},
}
ALLOWED_LEDGERS = {'ethereum', 'avalanche', 'private-bank-ledger'}
REFERENCE_NOW = datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc)
STALE_WARNING_MINUTES = 45
STALE_CRITICAL_MINUTES = 120
SETTLEMENT_LAG_THRESHOLD = 20000


class ReconciliationEngine:
    def __init__(self, store: ReconciliationStore | None = None) -> None:
        self.store = store or ReconciliationStore()

    def load_scenario_data(self, scenario_name: str) -> dict[str, Any]:
        details = SCENARIOS[scenario_name]
        return json.loads((DATA_DIR / details['file']).read_text())

    def list_scenarios(self) -> list[dict[str, str]]:
        return [
            {
                'scenario': name,
                'description': details['description'],
                'type': details['type'],
                'sample_path': str(DATA_DIR / details['file']),
            }
            for name, details in SCENARIOS.items()
        ]

    def scenario(self, scenario_name: str) -> dict[str, Any] | None:
        details = SCENARIOS.get(scenario_name)
        if details is None:
            return None
        return {
            'scenario': scenario_name,
            'description': details['description'],
            'type': details['type'],
            'data': self.load_scenario_data(scenario_name),
        }

    def reconcile(self, request: ReconciliationRequest) -> dict[str, Any]:
        expected = float(request.expected_total_supply)
        assessments: list[dict[str, Any]] = []
        mismatch_summary: list[str] = []
        recommendations: list[str] = []
        stale_count = 0
        severity_score = 0
        observed_total = 0.0
        normalized_total = 0.0
        settlement_lag_ledgers: list[str] = []
        over_reporting_ledgers: list[str] = []

        for ledger in request.ledgers:
            observed_total += ledger.reported_supply
            normalized_effective = max(ledger.reported_supply - ledger.locked_supply - ledger.pending_settlement, 0)
            weighted_effective = normalized_effective * max(ledger.reconciliation_weight, 0)
            normalized_total += weighted_effective

            staleness_minutes = self._minutes_since(ledger.last_updated_at)
            staleness_penalty = 0.0
            status = 'accepted'
            accepted = True
            explanations: list[str] = [
                f"normalized supply = reported ({ledger.reported_supply:,.0f}) - locked ({ledger.locked_supply:,.0f}) - pending ({ledger.pending_settlement:,.0f})"
            ]

            if ledger.ledger_name not in ALLOWED_LEDGERS:
                status = 'flagged'
                accepted = False
                severity_score += 20
                mismatch_summary.append(f'{ledger.ledger_name} is not in the supported local ledger set.')
                explanations.append('ledger name is outside the deterministic demo allowlist and was excluded from trusted normalization.')

            if staleness_minutes >= STALE_WARNING_MINUTES:
                stale_count += 1
                staleness_penalty = 0.05 if staleness_minutes < STALE_CRITICAL_MINUTES else 0.12
                normalized_total -= weighted_effective * staleness_penalty
                severity_score += 10 if staleness_minutes < STALE_CRITICAL_MINUTES else 18
                status = 'penalized' if status == 'accepted' else status
                explanations.append(f'data is stale by {staleness_minutes} minutes so a {staleness_penalty * 100:.0f}% confidence penalty was applied.')
                mismatch_summary.append(f'{ledger.ledger_name} data is stale by {staleness_minutes} minutes.')
                recommendations.append(f'Refresh {ledger.ledger_name} reconciliation snapshot before releasing additional supply.')

            settlement_lag_flag = ledger.pending_settlement >= SETTLEMENT_LAG_THRESHOLD
            if settlement_lag_flag:
                settlement_lag_ledgers.append(ledger.ledger_name)
                severity_score += 8
                status = 'penalized' if status == 'accepted' else status
                explanations.append(f'pending settlement {ledger.pending_settlement:,.0f} exceeded the lag threshold {SETTLEMENT_LAG_THRESHOLD:,.0f}.')
                mismatch_summary.append(f'{ledger.ledger_name} has elevated settlement lag of {ledger.pending_settlement:,.0f}.')
                recommendations.append(f'Pause or narrow the {ledger.ledger_name} settlement lane until pending transfers clear.')

            over_reported = ledger.reported_supply >= expected * 0.5
            if over_reported:
                over_reporting_ledgers.append(ledger.ledger_name)
                explanations.append('reported supply exceeds the per-ledger over-reporting guardrail, increasing duplicate mint / double-count risk.')

            assessments.append(
                {
                    'ledger_name': ledger.ledger_name,
                    'normalized_effective_supply': round(weighted_effective, 2),
                    'accepted': accepted,
                    'status': status,
                    'staleness_minutes': staleness_minutes,
                    'staleness_penalty': round(staleness_penalty, 4),
                    'settlement_lag_flag': settlement_lag_flag,
                    'over_reported_against_expected': over_reported,
                    'explanation': ' '.join(explanations),
                }
            )

        mismatch_amount = round(normalized_total - expected, 2)
        mismatch_percent = round((abs(mismatch_amount) / expected) * 100, 2) if expected else 0.0

        if mismatch_percent >= 8:
            severity_score += 34
            mismatch_summary.append(f'Normalized effective supply deviates from expected supply by {mismatch_percent:.2f}%.')
            recommendations.append('Suspend minting and run a ledger-by-ledger reconciliation review.')
        elif mismatch_percent >= 2:
            severity_score += 16
            mismatch_summary.append(f'Normalized effective supply deviates from expected supply by {mismatch_percent:.2f}%.')
            recommendations.append('Investigate supply drift before increasing bridge throughput.')
        else:
            recommendations.append('Continue scheduled monitoring and maintain current settlement thresholds.')

        duplicate_risk = len(over_reporting_ledgers) >= 2
        if duplicate_risk:
            severity_score += 24
            mismatch_summary.append(
                f"Multiple ledgers over-reported supply simultaneously ({', '.join(over_reporting_ledgers)}), indicating duplicate mint / double-count risk."
            )
            recommendations.append('Freeze bridge mint/burn operations until duplicate supply sources are resolved.')

        if settlement_lag_ledgers and 'Escalate settlement operations review for lagging ledgers.' not in recommendations:
            recommendations.append('Escalate settlement operations review for lagging ledgers.')

        severity_score = min(int(round(severity_score)), 100)
        status = 'matched'
        if severity_score >= 70 or mismatch_percent >= 8 or duplicate_risk:
            status = 'critical'
        elif severity_score >= 30 or mismatch_percent >= 2 or stale_count > 0 or settlement_lag_ledgers:
            status = 'warning'

        explainability = (
            f"{request.asset_id} reconciliation is {status}. Expected supply {expected:,.0f}, observed {observed_total:,.0f}, "
            f"normalized effective supply {normalized_total:,.0f}, mismatch {mismatch_amount:,.0f} ({mismatch_percent:.2f}%)."
        )

        per_ledger_balances = [
            {
                'ledger_name': ledger.ledger_name,
                'reported_supply': ledger.reported_supply,
                'locked_supply': ledger.locked_supply,
                'pending_settlement': ledger.pending_settlement,
                'effective_supply': round(max(ledger.reported_supply - ledger.locked_supply - ledger.pending_settlement, 0), 2),
                'transfer_count': ledger.transfer_count,
                'last_updated_at': ledger.last_updated_at,
            }
            for ledger in request.ledgers
        ]

        return {
            'asset_id': request.asset_id,
            'reconciliation_status': status,
            'expected_total_supply': expected,
            'observed_total_supply': round(observed_total, 2),
            'normalized_effective_supply': round(normalized_total, 2),
            'mismatch_amount': mismatch_amount,
            'mismatch_percent': mismatch_percent,
            'severity_score': severity_score,
            'duplicate_or_double_count_risk': duplicate_risk,
            'stale_ledger_count': stale_count,
            'settlement_lag_ledgers': settlement_lag_ledgers,
            'mismatch_summary': self._unique_list(mismatch_summary) or ['No reconciliation mismatches detected.'],
            'recommendations': self._unique_list(recommendations),
            'explainability_summary': explainability,
            'per_ledger_balances': per_ledger_balances,
            'ledger_assessments': assessments,
        }

    def evaluate_backstop(self, request: BackstopRequest) -> dict[str, Any]:
        decision = 'normal'
        triggered: list[str] = []
        actions: list[str] = []
        trading_status = 'active'
        bridge_status = 'active'
        settlement_status = 'active'

        if request.volatility_score >= 60:
            triggered.append('soft alert')
            actions.append('Increase monitoring frequency and inform treasury operations.')
            decision = 'alert'

        if request.volatility_score >= 80:
            triggered.extend(['high-volatility mode', 'reduce transfer threshold'])
            actions.append('Reduce transfer thresholds while volatility remains elevated.')
            decision = 'restricted'
            trading_status = 'guarded'

        if request.cyber_alert_score >= 75:
            triggered.append('pause trading')
            actions.append('Route all new trading requests through incident command review.')
            trading_status = 'paused'
            decision = 'paused' if request.volatility_score >= 70 else 'restricted'

        if request.reconciliation_severity >= 70:
            triggered.extend(['pause bridge / settlement lane', 'circuit breaker triggered'])
            actions.append('Pause bridge mint/burn and settlement lanes until reconciliation recovers.')
            bridge_status = 'paused'
            settlement_status = 'paused'
            decision = 'paused'

        if request.oracle_confidence_score <= 45:
            triggered.append('soft alert')
            actions.append('Require dual confirmation for oracle-dependent pricing and collateral checks.')
            if decision == 'normal':
                decision = 'alert'
            elif decision == 'alert':
                decision = 'restricted'

        if request.compliance_incident_score >= 60:
            triggered.append('reduce transfer threshold')
            actions.append('Reduce outbound transfer thresholds until compliance incident volume normalizes.')
            if decision == 'normal':
                decision = 'alert'

        operational_status = {
            'normal': 'normal',
            'alert': 'stressed',
            'restricted': 'restricted',
            'paused': 'paused',
        }[decision]

        if bridge_status == 'active' and decision in {'alert', 'restricted'}:
            bridge_status = 'guarded'
        if settlement_status == 'active' and decision == 'restricted':
            settlement_status = 'guarded'
        if trading_status == 'active' and decision == 'alert':
            trading_status = 'watch'

        if not triggered:
            actions.append('Maintain normal operations and keep baseline telemetry active.')

        explainability = (
            f"Backstop decision {decision} for {request.asset_id}: volatility={request.volatility_score:.0f}, cyber={request.cyber_alert_score:.0f}, "
            f"reconciliation={request.reconciliation_severity:.0f}, oracle confidence={request.oracle_confidence_score:.0f}, compliance incidents={request.compliance_incident_score:.0f}."
        )

        return {
            'asset_id': request.asset_id,
            'backstop_decision': decision,
            'triggered_safeguards': self._unique_list(triggered),
            'recommended_actions': self._unique_list(actions),
            'operational_status': operational_status,
            'trading_status': trading_status,
            'bridge_status': bridge_status,
            'settlement_status': settlement_status,
            'explainability_summary': explainability,
        }

    def record_incident(self, request: IncidentRecordRequest) -> IncidentRecord:
        return IncidentRecord(**self.store.create_incident(request.model_dump()))

    def list_incidents(self) -> list[dict[str, Any]]:
        incidents = self.store.load_incidents()
        return list(reversed(incidents))

    def get_incident(self, event_id: str) -> dict[str, Any] | None:
        for incident in self.store.load_incidents():
            if incident['event_id'] == event_id:
                return incident
        return None

    def dashboard(self) -> dict[str, Any]:
        reconciliation = self.reconcile(ReconciliationRequest(**self.load_scenario_data('mild-mismatch-warning')))
        backstop = self.evaluate_backstop(BackstopRequest(**self.load_scenario_data('high-volatility-alert')))
        incidents = self.list_incidents()[:5]
        return {
            'source': 'live',
            'degraded': False,
            'generated_at': '2026-03-18T12:00:00Z',
            'summary': {
                'reconciliation_status': reconciliation['reconciliation_status'],
                'severity_score': reconciliation['severity_score'],
                'mismatch_amount': reconciliation['mismatch_amount'],
                'stale_ledger_count': reconciliation['stale_ledger_count'],
                'backstop_decision': backstop['backstop_decision'],
                'incident_count': len(self.store.load_incidents()),
            },
            'cards': [
                {'label': 'Reconciliation', 'value': reconciliation['reconciliation_status'], 'detail': reconciliation['explainability_summary'], 'tone': reconciliation['reconciliation_status']},
                {'label': 'Mismatch amount', 'value': f"{reconciliation['mismatch_amount']:,.0f}", 'detail': f"{reconciliation['mismatch_percent']:.2f}% vs expected", 'tone': reconciliation['reconciliation_status']},
                {'label': 'Stale ledgers', 'value': str(reconciliation['stale_ledger_count']), 'detail': 'Ledgers penalized for stale updates.', 'tone': 'warning' if reconciliation['stale_ledger_count'] else 'matched'},
                {'label': 'Backstop', 'value': backstop['backstop_decision'], 'detail': ', '.join(backstop['triggered_safeguards']) or 'No safeguards triggered.', 'tone': backstop['backstop_decision']},
            ],
            'reconciliation_result': reconciliation,
            'backstop_result': backstop,
            'latest_incidents': incidents,
            'sample_scenarios': {name: details['description'] for name, details in SCENARIOS.items()},
            'message': 'Live reconciliation-service dashboard payload loaded successfully.',
        }

    def _minutes_since(self, timestamp: str) -> int:
        value = timestamp.replace('Z', '+00:00')
        delta = REFERENCE_NOW - datetime.fromisoformat(value)
        return max(int(delta.total_seconds() // 60), 0)

    def _unique_list(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered
