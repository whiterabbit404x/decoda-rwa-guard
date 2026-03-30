from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / 'event-watcher' / 'app' / 'main.py'
    spec = importlib.util.spec_from_file_location('event_watcher_main', module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_event_watcher_live_mode_requires_rpc(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test:test@localhost:5432/test')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    module = _load_module()
    with pytest.raises(RuntimeError, match='requires chain connectivity'):
        module.startup()


def test_event_watcher_live_mode_requires_database(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    module = _load_module()
    with pytest.raises(RuntimeError, match='requires DATABASE_URL'):
        module.startup()


def test_event_watcher_checkpoint_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('EVM_WS_URL', 'ws://rpc')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test:test@localhost:5432/test')
    monkeypatch.setenv('EVENT_WATCHER_CHECKPOINT_PATH', str(tmp_path / 'checkpoint.json'))
    module = _load_module()
    module.startup()
    module.update_checkpoint({'last_block': 123, 'last_log_cursor': '123:tx:3'})
    status = module.status()
    assert status['checkpoints']['last_block'] == 123
    assert status['source_status'] in {'degraded', 'polling', 'websocket', 'rpc_backfill'}
    checkpoint_payload = json.loads((tmp_path / 'checkpoint.json').read_text())
    assert checkpoint_payload['last_block'] == 123


def test_event_watcher_health_degraded_in_demo(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    module = _load_module()
    module.startup()
    module.STATE['degraded'] = True
    module.STATE['degraded_reason'] = 'demo_mode'
    payload = module.health()
    assert payload['status'] == 'degraded'
    assert payload['degraded'] is True
