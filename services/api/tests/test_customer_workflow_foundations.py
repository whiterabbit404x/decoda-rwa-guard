from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.api.app import pilot


def test_validate_target_payload_accepts_workspace_target_shape() -> None:
    payload = {
        'name': 'Treasury Settlement Router',
        'target_type': 'contract',
        'chain_network': 'ethereum-mainnet',
        'contract_identifier': '0xabc',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'tags': ['treasury', 'critical'],
        'severity_preference': 'high',
        'enabled': True,
    }
    validated = pilot._validate_target_payload(payload)
    assert validated['name'] == 'Treasury Settlement Router'
    assert validated['target_type'] == 'contract'
    assert validated['tags'] == ['treasury', 'critical']


def test_validate_target_payload_rejects_invalid_wallet_address() -> None:
    with pytest.raises(HTTPException):
        pilot._validate_target_payload({
            'name': 'Bad wallet',
            'target_type': 'wallet',
            'chain_network': 'ethereum-mainnet',
            'wallet_address': 'not-an-address',
        })


def test_templates_are_onboarding_only_catalog() -> None:
    payload = pilot.list_templates()
    assert payload['templates']
    assert all('module' in template for template in payload['templates'])
