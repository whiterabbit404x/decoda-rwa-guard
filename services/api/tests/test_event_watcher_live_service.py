from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_event_watcher_live_mode_requires_rpc(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test:test@localhost:5432/test')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    module = _load_module(ROOT / 'event-watcher' / 'app' / 'main.py', 'event_watcher_main')
    with pytest.raises(RuntimeError, match='requires chain connectivity'):
        module.startup()


def test_event_watcher_live_mode_requires_database(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    module = _load_module(ROOT / 'event-watcher' / 'app' / 'main.py', 'event_watcher_main_db')
    with pytest.raises(RuntimeError, match='requires DATABASE_URL'):
        module.startup()


def test_event_watcher_checkpoint_update_endpoint(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test:test@localhost:5432/test')
    module = _load_module(ROOT / 'event-watcher' / 'app' / 'main.py', 'event_watcher_main_checkpoint')
    module.startup()
    module.update_checkpoint({'last_block': 123, 'last_log_cursor': '123:tx:3'})
    status = module.status()
    assert status['checkpoints']['last_block'] == 123


def test_reconnect_triggers_backfill(monkeypatch):
    ingestor_module = _load_module(ROOT / 'event-watcher' / 'app' / 'evm_ingestor.py', 'evm_ingestor_module')
    ingestor = ingestor_module.EvmIngestor(chain_network='ethereum', rpc_url='http://rpc', ws_url='ws://rpc', watcher_name='watcher-test')
    called = {'backfill': 0}

    async def _leader():
        return True

    async def _ws_boom():
        raise RuntimeError('ws disconnected')

    async def _backfill(_from: int, _to: int):
        called['backfill'] += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(ingestor, '_ensure_leader_lease', _leader)
    monkeypatch.setattr(ingestor, '_ws_subscribe', _ws_boom)
    monkeypatch.setattr(ingestor, '_backfill', _backfill)
    monkeypatch.setattr(ingestor, '_record_heartbeat', lambda: None)
    monkeypatch.setattr(ingestor, '_rpc_call', lambda method, params: hex(12) if method == 'eth_blockNumber' else None)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.run_forever())
    assert called['backfill'] >= 1
