from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.api.app import activity_providers
from services.api.app.activity_providers import ActivityEvent


def test_hybrid_mode_prefers_live(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda target, since_ts: [ActivityEvent(event_id='e1', kind='transaction', observed_at=datetime.now(timezone.utc), ingestion_source='evm_rpc', cursor='1:a:1', payload={'tx_hash': '0x1'})])
    target = {'id': 't1', 'target_type': 'wallet', 'wallet_address': '0xabc', 'chain_network': 'ethereum'}
    events = activity_providers.fetch_target_activity(target, None)
    assert len(events) == 1
    assert events[0].ingestion_source == 'evm_rpc'


def test_live_mode_no_rpc_fails_fast(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    target = {'id': 't1', 'target_type': 'wallet', 'wallet_address': '0xabc', 'chain_network': 'ethereum'}
    with pytest.raises(RuntimeError, match='EVM_RPC_URL missing'):
        activity_providers.fetch_target_activity(target, None)
