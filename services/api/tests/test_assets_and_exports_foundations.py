from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.api.app import pilot


def test_validate_asset_payload_accepts_workspace_asset_shape() -> None:
    payload = {
        'name': 'Core Treasury Wallet',
        'description': 'Primary treasury signer',
        'asset_type': 'wallet',
        'chain_network': 'ethereum-mainnet',
        'identifier': '0x1111111111111111111111111111111111111111',
        'asset_class': 'treasury_token',
        'issuer_name': 'US Treasury',
        'asset_symbol': 'USTB',
        'asset_identifier': 'US912810',
        'token_contract_address': '0x1111111111111111111111111111111111111111',
        'custody_wallets': ['0x1111111111111111111111111111111111111111'],
        'treasury_ops_wallets': ['0x2222222222222222222222222222222222222222'],
        'expected_counterparties': ['0x3333333333333333333333333333333333333333'],
        'baseline_status': 'configured',
        'baseline_source': 'manual',
        'risk_tier': 'high',
        'owner_team': 'finance',
        'notes': 'Operational hot wallet',
        'enabled': True,
        'tags': ['treasury', 'hot-wallet'],
    }
    validated = pilot._validate_asset_payload(payload)
    assert validated['name'] == 'Core Treasury Wallet'
    assert validated['asset_type'] == 'wallet'
    assert validated['tags'] == ['treasury', 'hot-wallet']
    assert validated['asset_class'] == 'treasury_token'


def test_validate_asset_payload_rejects_unknown_asset_type() -> None:
    with pytest.raises(HTTPException):
        pilot._validate_asset_payload({
            'name': 'Broken',
            'asset_type': 'unknown',
            'chain_network': 'ethereum-mainnet',
            'identifier': 'abc',
        })
