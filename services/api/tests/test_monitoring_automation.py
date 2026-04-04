from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.activity_providers import ActivityEvent
from services.api.app.monitoring_runner import _enforce_asset_detectors


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


def test_counterparty_detector_flags_unknown_counterparty() -> None:
    event = ActivityEvent(
        event_id='evt-1',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc',
        cursor='1:0xabc:0',
        payload={
            'from': '0x1111111111111111111111111111111111111111',
            'to': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'amount': '1000',
            'event_type': 'transfer',
        },
    )
    detection = _enforce_asset_detectors(
        {
            'expected_counterparties': ['0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'],
            'treasury_ops_wallets': [],
            'custody_wallets': [],
            'baseline_status': 'observed',
        },
        event,
    )
    assert detection['detection_family'] == 'counterparty_anomaly'


def test_approval_detector_flags_unexpected_spender_high() -> None:
    event = ActivityEvent(
        event_id='evt-2',
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc',
        cursor='1:0xdef:1',
        payload={
            'owner': '0x1111111111111111111111111111111111111111',
            'spender': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'kind_hint': 'erc20_approval',
            'event_type': 'approval',
        },
    )
    detection = _enforce_asset_detectors(
        {
            'expected_counterparties': ['0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'],
            'treasury_ops_wallets': [],
            'custody_wallets': [],
            'baseline_status': 'observed',
        },
        event,
    )
    assert detection['detection_family'] == 'approval_anomaly'
    assert detection['severity'] == 'high'
