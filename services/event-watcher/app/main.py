from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response

try:
    from phase1_local.dev_support import load_env_file
except Exception:  # pragma: no cover
    def load_env_file() -> None:
        return None
from services.api.app.activity_providers import monitoring_ingestion_runtime
from services.api.app.structured_logging import configure_logging
from services.api.app.observability import bind_trace, reset_trace, current_trace_id, increment, gauge, prometheus_metrics, send_external_oncall_alert
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

try:
    from .evm_ingestor import EvmIngestor
except Exception:  # pragma: no cover
    from evm_ingestor import EvmIngestor

load_env_file()

configure_logging(service='event-watcher')
logger = logging.getLogger(__name__)
SERVICE_NAME = 'event-watcher'
PORT = int(os.getenv('PORT', 8005))
WATCHER_NAME = os.getenv('EVENT_WATCHER_NAME', 'event-watcher')
CHAIN_NETWORK = os.getenv('EVM_CHAIN_NETWORK', 'ethereum')
HEARTBEAT_SECONDS = max(5, int(os.getenv('EVENT_WATCHER_HEARTBEAT_SECONDS', '10')))

app = FastAPI(title=f'{SERVICE_NAME} service')

@app.middleware('http')
async def correlation_middleware(request: Request, call_next):
    trace_id = request.headers.get('X-Trace-ID') or request.headers.get('X-Correlation-ID')
    tokens = bind_trace(trace_id)
    resolved_trace_id = current_trace_id()
    try:
        response = await call_next(request)
    finally:
        reset_trace(tokens)
    response.headers['X-Trace-ID'] = resolved_trace_id
    return response

STATE: dict[str, Any] = {
    'running': False,
    'ready': False,
    'started_at': None,
    'last_heartbeat': None,
    'last_error': None,
    'ingestion_mode': None,
    'source_status': 'degraded',
    'degraded': False,
    'degraded_reason': None,
    'checkpoints': {'last_block': None, 'last_log_cursor': None},
    'metrics': {'events_ingested': 0, 'ws_reconnects': 0, 'rpc_backfills': 0},
}
_RUNTIME_TASK: asyncio.Task[Any] | None = None
_INGESTOR: EvmIngestor | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_runtime() -> dict[str, Any]:
    runtime = monitoring_ingestion_runtime()
    runtime['ws_url'] = bool((os.getenv('EVM_WS_URL') or '').strip())
    return runtime


def _sync_state_from_ingestor() -> None:
    if not _INGESTOR:
        return
    STATE['source_status'] = _INGESTOR.state.get('source_status')
    STATE['degraded'] = bool(_INGESTOR.state.get('degraded'))
    STATE['degraded_reason'] = _INGESTOR.state.get('degraded_reason')
    STATE['checkpoints']['last_block'] = _INGESTOR.state.get('last_processed_block')
    STATE['metrics'] = dict(_INGESTOR.state.get('metrics') or {})
    gauge('decoda_ingestion_lag_blocks', float(STATE['metrics'].get('lag_blocks') or 0), chain=CHAIN_NETWORK)
    gauge('decoda_event_watcher_healthy', 0 if STATE['degraded'] else 1, watcher=WATCHER_NAME)


def startup() -> None:
    runtime = _resolve_runtime()
    if runtime['mode'] == 'live' and runtime['degraded']:
        raise RuntimeError(f"event-watcher live mode requires chain connectivity: {runtime.get('reason')}")
    if runtime['mode'] in {'live', 'hybrid'} and not (os.getenv('DATABASE_URL') or '').strip():
        raise RuntimeError('event-watcher requires DATABASE_URL for durable target loading/checkpointing')
    STATE['running'] = True
    STATE['ready'] = True
    STATE['started_at'] = _utc_now()
    STATE['last_heartbeat'] = _utc_now()
    STATE['ingestion_mode'] = runtime.get('mode')


async def _run_loop() -> None:
    global _INGESTOR
    runtime = _resolve_runtime()
    if runtime.get('mode') == 'demo':
        while True:
            STATE['source_status'] = 'degraded'
            STATE['degraded'] = True
            STATE['degraded_reason'] = 'demo_mode'
            STATE['last_heartbeat'] = _utc_now()
            await asyncio.sleep(HEARTBEAT_SECONDS)
    rpc_url = (os.getenv('EVM_RPC_URL') or '').strip()
    ws_url = (os.getenv('EVM_WS_URL') or '').strip() or None
    _INGESTOR = EvmIngestor(chain_network=CHAIN_NETWORK, rpc_url=rpc_url, ws_url=ws_url, watcher_name=WATCHER_NAME)
    while True:
        try:
            await _INGESTOR.run_forever()
        except Exception as exc:
            increment('decoda_provider_failures_total', provider='evm_rpc', error_type=type(exc).__name__)
            send_external_oncall_alert('stale_telemetry', 'Event watcher provider loop failed and telemetry may become stale.', watcher=WATCHER_NAME, error_type=type(exc).__name__)
            STATE['last_error'] = str(exc)
            STATE['degraded'] = True
            STATE['degraded_reason'] = 'ws_rpc_down'
            await asyncio.sleep(HEARTBEAT_SECONDS)
        finally:
            _sync_state_from_ingestor()
            STATE['last_heartbeat'] = _utc_now()


@app.on_event('startup')
async def startup_event() -> None:
    startup()
    global _RUNTIME_TASK
    _RUNTIME_TASK = asyncio.create_task(_run_loop())


@app.on_event('shutdown')
async def shutdown_event() -> None:
    global _RUNTIME_TASK
    if _RUNTIME_TASK:
        _RUNTIME_TASK.cancel()
        _RUNTIME_TASK = None


@app.get('/health')
def health() -> dict[str, object]:
    _sync_state_from_ingestor()
    STATE['last_heartbeat'] = _utc_now()
    return {
        'status': 'ok' if not STATE.get('degraded') else 'degraded',
        'service': SERVICE_NAME,
        'port': PORT,
        'ready': STATE.get('ready'),
        'running': STATE.get('running'),
        'ingestion_mode': STATE.get('ingestion_mode'),
        'source_status': STATE.get('source_status'),
        'degraded': STATE.get('degraded'),
        'degraded_reason': STATE.get('degraded_reason'),
        'last_error': STATE.get('last_error'),
    }


@app.get('/status')
def status() -> dict[str, Any]:
    _sync_state_from_ingestor()
    STATE['last_heartbeat'] = _utc_now()
    return {**STATE, 'watcher_name': WATCHER_NAME, 'chain_network': CHAIN_NETWORK}


@app.get('/ready')
def ready() -> dict[str, Any]:
    return {'ready': bool(STATE.get('ready')), 'running': bool(STATE.get('running')), 'source_status': STATE.get('source_status')}


@app.post('/internal/checkpoint')
def update_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ('last_block', 'last_log_cursor'):
        if key in payload:
            STATE['checkpoints'][key] = payload[key]
    return {'ok': True, 'checkpoints': STATE['checkpoints']}


@app.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(prometheus_metrics(), media_type='text/plain; version=0.0.4')
