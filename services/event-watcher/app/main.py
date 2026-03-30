from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from phase1_local.dev_support import load_env_file

load_env_file()

SERVICE_NAME = 'event-watcher'
PORT = int(os.getenv('PORT', 8005))
CHECKPOINT_PATH = Path(os.getenv('EVENT_WATCHER_CHECKPOINT_PATH', '/tmp/event-watcher-checkpoint.json'))

app = FastAPI(title=f'{SERVICE_NAME} service')

STATE: dict[str, Any] = {
    'running': False,
    'started_at': None,
    'last_heartbeat': None,
    'last_error': None,
    'ingestion_mode': None,
    'live_source': None,
    'checkpoints': {'last_block': None, 'last_log_cursor': None},
    'metrics': {
        'events_ingested': 0,
        'ws_reconnects': 0,
        'rpc_backfills': 0,
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_checkpoint() -> None:
    if not CHECKPOINT_PATH.exists():
        return
    try:
        payload = json.loads(CHECKPOINT_PATH.read_text())
        if isinstance(payload, dict):
            STATE['checkpoints'].update(payload)
    except Exception:
        STATE['last_error'] = 'checkpoint_load_failed'


def _persist_checkpoint() -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(STATE['checkpoints']))


def _resolve_mode() -> dict[str, Any]:
    mode = str(os.getenv('MONITORING_INGESTION_MODE', 'live')).strip().lower()
    ws_url = (os.getenv('EVM_WS_URL') or '').strip()
    rpc_url = (os.getenv('EVM_RPC_URL') or '').strip()
    if mode == 'demo':
        return {'ingestion_mode': 'demo', 'live_source': 'degraded', 'degraded': False}
    if not rpc_url:
        return {'ingestion_mode': mode, 'live_source': 'degraded', 'degraded': True}
    if ws_url:
        return {'ingestion_mode': mode, 'live_source': 'websocket', 'degraded': False}
    return {'ingestion_mode': mode, 'live_source': 'polling', 'degraded': False}


@app.on_event('startup')
def startup() -> None:
    runtime = _resolve_mode()
    if runtime['ingestion_mode'] == 'live' and runtime['degraded']:
        raise RuntimeError('event-watcher live mode requires EVM_RPC_URL')
    _load_checkpoint()
    STATE['running'] = True
    STATE['started_at'] = _utc_now()
    STATE['last_heartbeat'] = _utc_now()
    STATE['ingestion_mode'] = runtime['ingestion_mode']
    STATE['live_source'] = runtime['live_source']
    _persist_checkpoint()


@app.get('/health')
def health() -> dict[str, object]:
    STATE['last_heartbeat'] = _utc_now()
    return {
        'status': 'ok' if not STATE.get('last_error') else 'degraded',
        'service': SERVICE_NAME,
        'port': PORT,
        'ingestion_mode': STATE.get('ingestion_mode'),
        'live_source': STATE.get('live_source'),
        'degraded': STATE.get('live_source') == 'degraded',
        'last_error': STATE.get('last_error'),
    }


@app.get('/status')
def status() -> dict[str, Any]:
    STATE['last_heartbeat'] = _utc_now()
    return {
        **STATE,
        'checkpoint_path': str(CHECKPOINT_PATH),
    }


@app.post('/internal/checkpoint')
def update_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ('last_block', 'last_log_cursor'):
        if key in payload:
            STATE['checkpoints'][key] = payload[key]
    _persist_checkpoint()
    return {'ok': True, 'checkpoints': STATE['checkpoints']}
