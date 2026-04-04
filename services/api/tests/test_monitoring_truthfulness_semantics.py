from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import monitoring_runner
from services.api.app.activity_providers import ActivityEvent


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM alert_suppression_rules' in normalized:
            return _Result(None)
        if 'FROM alerts' in normalized:
            return _Result(None)
        return _Result(None)


def _target() -> dict[str, object]:
    return {
        'id': 'target-1',
        'workspace_id': 'workspace-1',
        'name': 'Treasury Wallet',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
    }


def test_no_confirmed_anomaly_outcome_requires_real_evidence_wording(monkeypatch):
    captured: dict[str, object] = {}

    def _capture(*_args, **kwargs):
        captured['response'] = kwargs['response_payload']
        return 'run-1'

    monkeypatch.setattr(monitoring_runner, 'persist_analysis_run', _capture)
    monkeypatch.setattr(
        monitoring_runner,
        '_threat_call',
        lambda *_args, **_kwargs: (
            {
                'analysis_type': 'transaction',
                'score': 12,
                'severity': 'low',
                'matched_patterns': [],
                'explanation': 'No confirmed anomaly detected in observed evidence.',
                'recommended_action': 'review',
                'reasons': [],
                'source': 'live',
                'degraded': False,
                'metadata': {},
            },
            {'live_invocation_succeeded': True},
        ),
    )

    event = ActivityEvent(
        event_id='evt-1',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc_backfill',
        cursor='1:0xabc:0',
        payload={'block_number': 1, 'tx_hash': '0xabc'},
    )
    result = monitoring_runner._process_single_event(
        _Conn(),
        target=_target(),
        workspace={'id': 'workspace-1', 'name': 'Workspace'},
        user_id='user-1',
        monitoring_run_id='run-monitor',
        event=event,
        configured_scenario=None,
    )
    assert result['analysis_run_id'] == 'run-1'
    metadata = (captured['response'] or {}).get('metadata', {})
    assert metadata.get('detection_outcome') == 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'


def test_monitoring_runtime_status_marks_provider_degraded_when_no_evidence(monkeypatch):
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'operational_mode': 'HYBRID', 'mode': 'hybrid', 'source_type': 'polling', 'degraded': False},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {
            'status': 'FAIL',
            'sales_claims_allowed': False,
            'checks': {'evm_rpc_reachable': True},
            'recent_evidence_state': 'no_evidence',
            'recent_truthfulness_state': 'unknown_risk',
            'recent_real_event_count': 0,
            'last_real_event_at': None,
        },
    )
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['provider_health'] == 'degraded'
    assert payload['recent_evidence_state'] == 'no_evidence'


def test_monitoring_runtime_status_forces_degraded_mode_when_live_has_zero_real_events(monkeypatch):
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'operational_mode': 'LIVE', 'mode': 'live', 'source_type': 'polling', 'degraded': False},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {
            'status': 'FAIL',
            'sales_claims_allowed': False,
            'checks': {'evm_rpc_reachable': True},
            'recent_evidence_state': 'real',
            'recent_truthfulness_state': 'unknown_risk',
            'recent_real_event_count': 0,
            'last_real_event_at': None,
        },
    )
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['mode'] == 'DEGRADED'
    assert payload['provider_health'] == 'degraded'
