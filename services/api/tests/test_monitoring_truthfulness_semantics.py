from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import monitoring_runner
from services.api.app.activity_providers import ActivityEvent, ActivityProviderResult


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
    assert payload['evidence_state'] == 'no_evidence'
    assert payload['truthfulness_state'] == 'unknown_risk'
    assert payload['claim_safe'] is False


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


def test_activity_provider_requires_explicit_detection_outcome() -> None:
    result = ActivityProviderResult(
        mode='live',
        status='no_evidence',
        evidence_state='NO_EVIDENCE',
        truthfulness_state='UNKNOWN_RISK',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=False,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=None,
        checkpoint=None,
        checkpoint_age_seconds=None,
        degraded_reason='no_provider_data',
        error_code=None,
        source_type='rpc_polling',
        reason_code='NO_PROVIDER_EVIDENCE',
        claim_safe=False,
        detection_outcome='NO_EVIDENCE',
    )
    assert result.detection_outcome == 'NO_EVIDENCE'


def test_process_monitoring_target_preserves_failed_state(monkeypatch):
    class _Conn:
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'SELECT id, name FROM workspaces' in normalized:
                return _Result({'id': 'workspace-1', 'name': 'Workspace'})
            return _Result(None)

    target = {
        'id': 'target-1',
        'workspace_id': 'workspace-1',
        'name': 'Treasury Wallet',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'monitoring_checkpoint_at': None,
        'last_checked_at': None,
        'monitoring_checkpoint_cursor': None,
        'watcher_last_observed_block': None,
        'updated_by_user_id': 'user-1',
        'created_by_user_id': 'user-1',
    }
    monkeypatch.setattr(
        monitoring_runner,
        'fetch_target_activity_result',
        lambda *_args, **_kwargs: ActivityProviderResult(
            mode='live',
            status='failed',
            evidence_state='FAILED_EVIDENCE',
            truthfulness_state='UNKNOWN_RISK',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=False,
            recent_real_event_count=0,
            last_real_event_at=None,
            events=[],
            latest_block=None,
            checkpoint=None,
            checkpoint_age_seconds=None,
            degraded_reason='provider_error',
            error_code='TimeoutError',
            source_type='rpc_polling',
            reason_code='PROVIDER_FAILED',
            claim_safe=False,
            detection_outcome='ANALYSIS_FAILED',
        ),
    )
    result = monitoring_runner.process_monitoring_target(_Conn(), target, triggered_by_user_id='user-1')
    assert result['status'] == 'insufficient_real_evidence'
    assert result['source_status'] == 'failed'


def test_process_monitoring_target_persists_live_coverage_without_target_events(monkeypatch):
    persisted: dict[str, object] = {'called': False}

    class _Conn:
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'SELECT id, name FROM workspaces' in normalized:
                return _Result({'id': 'workspace-1', 'name': 'Workspace'})
            return _Result(None)

    target = {
        'id': 'target-1',
        'workspace_id': 'workspace-1',
        'name': 'Treasury Wallet',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'monitoring_checkpoint_at': None,
        'last_checked_at': None,
        'monitoring_checkpoint_cursor': None,
        'watcher_last_observed_block': None,
        'updated_by_user_id': 'user-1',
        'created_by_user_id': 'user-1',
    }

    monkeypatch.setattr(
        monitoring_runner,
        'fetch_target_activity_result',
        lambda *_args, **_kwargs: ActivityProviderResult(
            mode='live',
            status='live',
            evidence_state='REAL_EVIDENCE',
            truthfulness_state='NOT_CLAIM_SAFE',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=True,
            recent_real_event_count=0,
            last_real_event_at=None,
            events=[],
            latest_block=123,
            checkpoint='coverage:123',
            checkpoint_age_seconds=0,
            degraded_reason=None,
            error_code=None,
            source_type='rpc_polling',
            reason_code='PROVIDER_COVERAGE_VERIFIED',
            claim_safe=False,
            detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
        ),
    )

    def _persist(*_args, **_kwargs):
        persisted['called'] = True

    monkeypatch.setattr(monitoring_runner, '_persist_live_coverage_telemetry', _persist)

    result = monitoring_runner.process_monitoring_target(_Conn(), target, triggered_by_user_id='user-1')
    assert persisted['called'] is True
    assert result['events_ingested'] == 0
    assert result['source_status'] == 'active'
    assert result['degraded_reason'] is None
    assert result['status'] == 'no_real_data'
    assert result['live_coverage_telemetry_at'] is not None


def test_process_monitoring_target_degrades_non_live_provider_source(monkeypatch):
    persisted: dict[str, object] = {'called': False}

    class _Conn:
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'SELECT id, name FROM workspaces' in normalized:
                return _Result({'id': 'workspace-1', 'name': 'Workspace'})
            return _Result(None)

    target = {
        'id': 'target-1',
        'workspace_id': 'workspace-1',
        'name': 'Treasury Wallet',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'monitoring_checkpoint_at': None,
        'last_checked_at': None,
        'monitoring_checkpoint_cursor': None,
        'watcher_last_observed_block': None,
        'updated_by_user_id': 'user-1',
        'created_by_user_id': 'user-1',
    }

    monkeypatch.setattr(
        monitoring_runner,
        'fetch_target_activity_result',
        lambda *_args, **_kwargs: ActivityProviderResult(
            mode='live',
            status='live',
            evidence_state='REAL_EVIDENCE',
            truthfulness_state='NOT_CLAIM_SAFE',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=True,
            recent_real_event_count=0,
            last_real_event_at=None,
            events=[],
            latest_block=123,
            checkpoint='coverage:123',
            checkpoint_age_seconds=0,
            degraded_reason=None,
            error_code=None,
            source_type='demo',
            reason_code='PROVIDER_COVERAGE_VERIFIED',
            claim_safe=False,
            detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
        ),
    )

    def _persist(*_args, **_kwargs):
        persisted['called'] = True

    monkeypatch.setattr(monitoring_runner, '_persist_live_coverage_telemetry', _persist)

    result = monitoring_runner.process_monitoring_target(_Conn(), target, triggered_by_user_id='user-1')
    assert persisted['called'] is False
    assert result['source_status'] == 'degraded'
    assert result['degraded_reason'] == 'provider_source_not_live:demo'
    assert result['status'] == 'insufficient_real_evidence'
    assert result['live_coverage_telemetry_at'] is None
