from __future__ import annotations

from datetime import datetime, timezone

from services.api.app.activity_providers import ActivityEvent
from services.api.app.monitoring_runner import _asset_detection_summary


def _event(event_type: str) -> ActivityEvent:
    return ActivityEvent(
        event_id='evt-1',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc',
        cursor='1:0xabc:0',
        payload={'event_type': event_type, 'tx_hash': '0xabc', 'block_number': 10, 'log_index': 0},
    )


def _asset() -> dict:
    return {
        'id': 'a1',
        'identifier': 'USTB',
        'asset_symbol': 'USTB',
        'expected_counterparties': [],
        'treasury_ops_wallets': [],
        'custody_wallets': [],
        'oracle_sources': ['oracle-a'],
        'venue_labels': ['venue-a'],
        'expected_flow_patterns': [],
        'expected_approval_patterns': {},
        'expected_liquidity_baseline': {'baseline_outflow_volume': 1000},
    }


def test_asset_detection_produces_detector_bundle() -> None:
    outcome = _asset_detection_summary(asset=_asset(), event=_event('transfer'))
    assert outcome['detection_family'] in {'counterparty', 'flow_pattern', 'approval_pattern', 'liquidity_venue', 'oracle_integrity'}
    assert isinstance(outcome['detector_results'], list)
    assert len(outcome['detector_results']) == 5
