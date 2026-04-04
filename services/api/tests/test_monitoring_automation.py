from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.activity_providers import ActivityEvent
from services.api.app.monitoring_runner import _asset_detection_summary, _enforce_asset_detectors


def test_target_validation_rejects_demo_scenario_fields() -> None:
    payload = {
        'name': 'Treasury Wallet',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'monitoring_demo_scenario': 'flash_loan_like',
    }
    with pytest.raises(HTTPException):
        pilot._validate_target_payload(payload)


def _event(payload: dict[str, str]) -> ActivityEvent:
    return ActivityEvent(
        event_id='evt-1',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc',
        cursor='1:0xabc:0',
        payload=payload,
    )


def _detector(summary: dict, family: str) -> dict:
    return next(item for item in summary['detector_results'] if item['detector_family'] == family)


def test_counterparty_detector_flags_unknown_counterparty() -> None:
    summary = _asset_detection_summary(
        asset={
            'id': 'a1',
            'identifier': 'USTB',
            'asset_symbol': 'USTB',
            'expected_counterparties': ['0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'],
            'treasury_ops_wallets': ['0x1111111111111111111111111111111111111111'],
            'custody_wallets': [],
            'expected_flow_patterns': [],
            'expected_approval_patterns': {},
            'expected_liquidity_baseline': {},
            'oracle_sources': [],
        },
        event=_event({'from': '0x1111111111111111111111111111111111111111', 'to': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'amount': '1000', 'event_type': 'transfer'}),
    )
    result = _detector(summary, 'counterparty')
    assert result['detector_status'] == 'anomaly_detected'


def test_approval_detector_flags_unexpected_unlimited_spender_high() -> None:
    summary = _asset_detection_summary(
        asset={
            'id': 'a1',
            'identifier': 'USTB',
            'asset_symbol': 'USTB',
            'expected_counterparties': [],
            'treasury_ops_wallets': [],
            'custody_wallets': [],
            'expected_flow_patterns': [],
            'expected_approval_patterns': {'allowed_spenders': ['0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'], 'max_amount': 1000},
            'expected_liquidity_baseline': {},
            'oracle_sources': [],
        },
        event=_event({'owner': '0x1111111111111111111111111111111111111111', 'spender': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'kind_hint': 'erc20_approval', 'event_type': 'approval', 'amount': str(2**255), 'is_unlimited_approval': True}),
    )
    result = _detector(summary, 'approval_pattern')
    assert result['detector_status'] == 'anomaly_detected'
    assert result['severity'] == 'high'


def test_liquidity_detector_marks_insufficient_real_evidence_when_missing_data() -> None:
    detectors = _enforce_asset_detectors(
        {
            'id': 'a1',
            'identifier': 'USTB',
            'asset_symbol': 'USTB',
            'expected_counterparties': [],
            'treasury_ops_wallets': [],
            'custody_wallets': [],
            'expected_flow_patterns': [],
            'expected_approval_patterns': {},
            'expected_liquidity_baseline': {},
            'oracle_sources': [],
        },
        _event({'event_type': 'transfer', 'amount': '10'}),
    )
    liquidity = next(item for item in detectors if item['detector_family'] == 'liquidity_venue')
    assert liquidity['detector_status'] == 'insufficient_real_evidence'


def test_oracle_detector_flags_stale_cadence_and_divergence() -> None:
    now = datetime.now(timezone.utc)
    summary = _asset_detection_summary(
        asset={
            'id': 'a1',
            'identifier': 'USTB',
            'asset_symbol': 'USTB',
            'expected_counterparties': [],
            'treasury_ops_wallets': [],
            'custody_wallets': [],
            'expected_flow_patterns': [],
            'expected_approval_patterns': {},
            'expected_liquidity_baseline': {'baseline_outflow_volume': 10},
            'oracle_sources': ['a', 'b'],
            'expected_oracle_freshness_seconds': 30,
            'expected_oracle_update_cadence_seconds': 30,
        },
        event=_event(
            {
                'event_type': 'oracle_update',
                'oracle_observations': [
                    {'source': 'a', 'observed_at': (now - timedelta(seconds=120)).isoformat(), 'price': 100, 'update_interval_seconds': 60},
                    {'source': 'b', 'observed_at': now.isoformat(), 'price': 200, 'update_interval_seconds': 10},
                ],
            }
        ),
    )
    oracle = _detector(summary, 'oracle_integrity')
    assert oracle['detector_status'] == 'anomaly_detected'
    assert 'stale_oracle' in oracle['anomaly_reason']
    assert 'cadence_violation' in oracle['anomaly_reason']
    assert 'source_divergence' in oracle['anomaly_reason']


def test_liquidity_detector_flags_outflow_burst_venue_and_concentration() -> None:
    summary = _asset_detection_summary(
        asset={
            'id': 'a1',
            'identifier': 'USTB',
            'asset_symbol': 'USTB',
            'expected_counterparties': [],
            'treasury_ops_wallets': ['0x1111111111111111111111111111111111111111'],
            'custody_wallets': [],
            'expected_flow_patterns': [{'source_class': 'treasury_ops', 'destination_class': 'approved_external'}],
            'expected_approval_patterns': {},
            'expected_liquidity_baseline': {
                'baseline_outflow_volume': 100,
                'baseline_transfer_count': 2,
                'baseline_unique_counterparties': 4,
                'max_concentration_ratio': 0.3,
            },
            'venue_labels': ['0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'],
            'oracle_sources': [],
        },
        event=_event(
            {
                'event_type': 'transfer',
                'from': '0x1111111111111111111111111111111111111111',
                'to': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'liquidity_observations': [{
                    'rolling_volume': 350,
                    'transfer_count': 10,
                    'unique_counterparties': 1,
                    'concentration_ratio': 0.9,
                }],
                'venue_observations': [{'venue_distribution': {'unknown': 0.8}}],
            }
        ),
    )
    liquidity = _detector(summary, 'liquidity_venue')
    assert liquidity['detector_status'] == 'anomaly_detected'
    assert 'abnormal_outflow' in liquidity['anomaly_reason']
    assert 'burst_activity' in liquidity['anomaly_reason']
    assert 'unexpected_venue_shift' in liquidity['anomaly_reason']
    assert 'concentration_spike' in liquidity['anomaly_reason']
