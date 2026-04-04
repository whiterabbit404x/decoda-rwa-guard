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


def test_asset_detection_flags_missing_baseline() -> None:
    outcome = _asset_detection_summary(asset={'baseline_status': 'missing', 'name': 'USTB'}, event=_event('transfer'))
    assert outcome['detection_family'] == 'baseline_gap'
    assert 'baseline is missing' in outcome['anomaly_basis']


def test_asset_detection_flags_approval_abuse_family() -> None:
    outcome = _asset_detection_summary(asset={'baseline_status': 'observed', 'name': 'USTB'}, event=_event('approval_changed'))
    assert outcome['detection_family'] == 'treasury_approval_abuse'


def test_asset_detection_flags_oracle_family() -> None:
    outcome = _asset_detection_summary(asset={'baseline_status': 'observed', 'name': 'USTB'}, event=_event('oracle_update'))
    assert outcome['detection_family'] == 'oracle_integrity_anomaly'
