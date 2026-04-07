from __future__ import annotations

import json
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


class _FakeRow:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row


class _FakeStorage:
    backend_name = 'local'

    def __init__(self):
        self.content = b''

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self.content = content
        return object_key


class _FakeConnection:
    def __init__(self):
        self.storage_update_called = False

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _FakeRow({'id': 'exp-1', 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-1', 'include_raw_events': True}})
        if 'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s' in normalized:
            return _FakeRow({'id': 'inc-1', 'workspace_id': 'ws-1', 'title': 'Incident', 'severity': 'high'})
        if 'FROM alerts a JOIN detection_metrics dm ON dm.alert_id = a.id' in normalized:
            return _FakeRow([{'id': 'alert-1', 'severity': 'high'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in normalized:
            return _FakeRow([{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {'tx_hash': '0xabc'}}])
        if "UPDATE export_jobs SET status = 'completed'" in normalized:
            self.storage_update_called = True
            return _FakeRow(None)
        if "UPDATE export_jobs SET status = 'failed'" in normalized:
            return _FakeRow(None)
        raise AssertionError(f'unexpected query: {query}')


def test_generate_export_artifact_proof_bundle_contains_expected_files(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: fake_storage)
    connection = _FakeConnection()
    pilot._generate_export_artifact(connection, workspace_id='ws-1', export_id='exp-1')
    payload = json.loads(fake_storage.content.decode('utf-8'))
    row = payload['rows'][0]
    assert sorted(row.keys()) == ['alerts.json', 'detection_metrics.json', 'evidence.json', 'incidents.json', 'summary.json']
    assert row['summary.json']['incident_id'] == 'inc-1'
    assert connection.storage_update_called is True
