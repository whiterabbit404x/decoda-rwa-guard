from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / 'event-watcher' / 'app' / 'main.py'
    spec = importlib.util.spec_from_file_location('event_watcher_main', module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_event_watcher_live_mode_requires_rpc(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    module = _load_module()
    try:
        module.startup()
    except RuntimeError as exc:
        assert 'requires EVM_RPC_URL' in str(exc)
    else:
        raise AssertionError('Expected RuntimeError when live mode has no EVM_RPC_URL')


def test_event_watcher_checkpoint_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('EVM_WS_URL', 'ws://rpc')
    monkeypatch.setenv('EVENT_WATCHER_CHECKPOINT_PATH', str(tmp_path / 'checkpoint.json'))
    module = _load_module()
    module.startup()
    module.update_checkpoint({'last_block': 123, 'last_log_cursor': '123:tx:3'})
    status = module.status()
    assert status['checkpoints']['last_block'] == 123
    assert status['live_source'] == 'websocket'
