from __future__ import annotations

from datetime import datetime, timezone

from services.api.app.activity_providers import ActivityEvent
from services.api.app.monitoring_runner import _asset_detection_summary, _enforce_asset_detectors


def _event(event_type: str, **extra: object) -> ActivityEvent:
    payload = {
        'event_type': event_type,
        'tx_hash': '0xabc',
        'block_number': 10,
        'log_index': 0,
        'target_id': 't1',
        'chain_id': 1,
        'from': '0xtreasury',
        'to': '0xcp1',
        'metadata': {'provider_name': 'evm_activity_provider', 'evidence_origin': 'real', 'production_claim_eligible': True},
    }
    payload.update(extra)
    return ActivityEvent(
        event_id='evt-1',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc',
        cursor='1:0xabc:0',
        payload=payload,
    )


def _asset() -> dict:
    return {
        'id': 'a1',
        'identifier': 'USTB',
        'asset_identifier': 'USTB',
        'asset_symbol': 'USTB',
        'chain_id': 1,
        'token_contract_address': '0xtoken',
        'expected_counterparties': ['0xcp1'],
        'treasury_ops_wallets': ['0xtreasury'],
        'custody_wallets': ['0xcustody'],
        'oracle_sources': ['oracle-a', 'oracle-b'],
        'venue_labels': ['venue-a'],
        'expected_flow_patterns': [{'source_class': 'treasury_ops', 'destination_class': 'approved_external_counterparty'}],
        'expected_approval_patterns': {'allowed_spenders': ['0xspender-ok'], 'max_amount': 1000},
        'expected_liquidity_baseline': {'baseline_outflow_volume': 1000, 'baseline_transfer_count': 10, 'baseline_unique_counterparties': 6, 'max_concentration_ratio': 0.5},
        'expected_oracle_freshness_seconds': 120,
        'expected_oracle_update_cadence_seconds': 120,
        'baseline_status': 'ready',
        'baseline_confidence': 0.95,
        'baseline_coverage': 0.91,
    }


def test_asset_detection_produces_detector_bundle() -> None:
    outcome = _asset_detection_summary(asset=_asset(), event=_event('transfer'))
    assert outcome['detection_family'] in {'counterparty', 'flow_pattern', 'approval_pattern', 'liquidity_venue', 'oracle_integrity'}
    assert isinstance(outcome['detector_results'], list)
    assert len(outcome['detector_results']) == 5


def test_counterparty_protection_treasury_to_unknown_external_triggers_violation() -> None:
    event = _event('transfer', to='0xunknown', amount='200000')
    detector = [item for item in _enforce_asset_detectors(asset=_asset(), event=event) if item['detector_family'] == 'counterparty'][0]
    assert detector['detector_status'] == 'anomaly_detected'
    assert detector['anomaly_reason'] == 'treasury_ops_to_unknown_external'
    assert detector['severity'] == 'high'


def test_approval_protection_unlimited_approval_is_high_severity() -> None:
    event = _event(
        'approval',
        kind_hint='erc20_approval',
        spender='0xspender-bad',
        contract_address='0xtoken',
        approval_amount=str(2**255),
        is_unlimited_approval=True,
    )
    detector = [item for item in _enforce_asset_detectors(asset=_asset(), event=event) if item['detector_family'] == 'approval_pattern'][0]
    assert detector['detector_status'] == 'anomaly_detected'
    assert detector['anomaly_reason'] == 'unexpected_unlimited_approval_on_protected_asset'
    assert detector['severity'] == 'high'


def test_liquidity_detector_fails_closed_when_telemetry_insufficient() -> None:
    event = _event('transfer', liquidity_observations=[{'status': 'insufficient_real_evidence'}], market_observations=[{'status': 'insufficient_real_evidence'}], venue_observations=[{'venue_distribution': {}}], oracle_observations=[{'source_name': 'oracle-a', 'status': 'ok', 'observed_value': 1, 'observed_at': datetime.now(timezone.utc).isoformat()}])
    detector = [item for item in _enforce_asset_detectors(asset=_asset(), event=event) if item['detector_family'] == 'liquidity_venue'][0]
    assert detector['detector_status'] == 'insufficient_real_evidence'


def test_oracle_integrity_detects_divergence_and_cadence_violation() -> None:
    now = datetime.now(timezone.utc).isoformat()
    event = _event(
        'transfer',
        oracle_observations=[
            {'source_name': 'oracle-a', 'status': 'ok', 'observed_value': 100, 'observed_at': now, 'update_interval_seconds': 30},
            {'source_name': 'oracle-b', 'status': 'ok', 'observed_value': 130, 'observed_at': now, 'update_interval_seconds': 180},
        ],
        market_observations=[{'status': 'ok', 'source_name': 'market-a'}],
        liquidity_observations=[{'status': 'ok', 'rolling_volume': 1200, 'rolling_transfer_count': 15, 'unique_counterparties': 8, 'concentration_ratio': 0.4, 'abnormal_outflow_ratio': 0.1, 'burst_score': 1.3, 'route_distribution': {'treasury_ops->approved_external_counterparty': 1.0}}],
        venue_observations=[{'venue_distribution': {'venue-a': 1.0}}],
    )
    detector = [item for item in _enforce_asset_detectors(asset=_asset(), event=event) if item['detector_family'] == 'oracle_integrity'][0]
    assert detector['detector_status'] == 'anomaly_detected'
    assert 'source_divergence' in detector['anomaly_reason']
