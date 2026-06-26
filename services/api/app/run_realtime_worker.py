"""Base real-time ingestion worker entrypoint.

Starts only when BASE_REALTIME_ENABLED=true.
Default: disabled (BASE_REALTIME_ENABLED=false).

Required env vars when enabled:
  BASE_WS_RPC_URL           WebSocket RPC endpoint for Base
  BASE_REALTIME_CONFIRMATIONS     (default 1)
  BASE_REALTIME_MAX_EVENTS_PER_MINUTE (default 1000)
  BASE_REALTIME_FALLBACK_TO_POLLING   (default true — polling always runs independently)

The 300s polling worker continues to run as backup/backfill regardless of this worker.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse as _urlparse

logger = logging.getLogger(__name__)

_BASE_CHAIN_ID = 8453


def _resolve_bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or '').strip().lower()
    if raw in ('1', 'true', 'yes'):
        return True
    if raw in ('0', 'false', 'no'):
        return False
    return default


def _resolve_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or '').strip()
    try:
        return max(0, int(raw)) if raw else default
    except (TypeError, ValueError):
        return default


def _default_watcher_name() -> str:
    instance = (
        os.getenv('RAILWAY_REPLICA_ID') or os.getenv('HOSTNAME') or socket.gethostname() or 'local'
    ).strip()
    return f'base-realtime-worker-{instance[:60]}'


def _safe_rpc_host(url: str) -> str:
    """Return only the hostname from a URL — never the path, key, or credentials."""
    try:
        return _urlparse(url).hostname or 'unknown'
    except Exception:
        return 'unknown'


def _resolve_config() -> dict[str, object]:
    """Return resolved config dict. Never exposes secrets or full URLs in logs."""
    enabled = _resolve_bool_env('BASE_REALTIME_ENABLED', default=False)
    provider_mode = (os.getenv('BASE_REALTIME_PROVIDER') or 'websocket').strip().lower()
    ws_url = (os.getenv('BASE_WS_RPC_URL') or '').strip()
    rpc_url = (
        os.getenv('EVM_RPC_URL_8453')
        or os.getenv('BASE_EVM_RPC_URL')
        or os.getenv('EVM_RPC_URL')
        or ''
    ).strip()
    webhook_secret_set = bool((os.getenv('BASE_WEBHOOK_SECRET') or '').strip())
    confirmations = _resolve_int_env('BASE_REALTIME_CONFIRMATIONS', 1)
    max_events_per_minute = _resolve_int_env('BASE_REALTIME_MAX_EVENTS_PER_MINUTE', 1000)
    fallback_to_polling = _resolve_bool_env('BASE_REALTIME_FALLBACK_TO_POLLING', default=True)
    watcher_name = (os.getenv('BASE_REALTIME_WATCHER_NAME') or _default_watcher_name()).strip()

    return {
        'enabled': enabled,
        'provider_mode': provider_mode,
        'ws_url': ws_url,
        'ws_url_host': _safe_rpc_host(ws_url) if ws_url else 'not_configured',
        'rpc_url': rpc_url,
        'rpc_url_host': _safe_rpc_host(rpc_url) if rpc_url else 'not_configured',
        'webhook_secret_set': webhook_secret_set,
        'confirmations': confirmations,
        'max_events_per_minute': max_events_per_minute,
        'fallback_to_polling': fallback_to_polling,
        'watcher_name': watcher_name,
    }


def _check_realtime_config(config: dict[str, object]) -> tuple[bool, str]:
    """Return (can_start, reason). Fail-closed on bad config."""
    if not config['enabled']:
        return False, 'BASE_REALTIME_ENABLED_not_true'
    if config['provider_mode'] == 'websocket' and not config['ws_url']:
        return False, 'missing_BASE_WS_RPC_URL'
    if config['provider_mode'] == 'webhook' and not config['webhook_secret_set']:
        return False, 'missing_BASE_WEBHOOK_SECRET'
    if not config['rpc_url']:
        return False, 'missing_rpc_url_for_Base'
    return True, 'ok'


def _start_health_server(port: int) -> None:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == '/health':
                body = b'{"status":"ok","service":"base-realtime-worker"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # suppress access logs

    server = HTTPServer(('0.0.0.0', port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name='realtime-health-server')
    t.start()
    logger.info('realtime_health_server_started host=0.0.0.0 port=%s path=/health', port)


async def _run_ingestor(config: dict[str, object]) -> None:
    """Async entry point for the WebSocket ingestor loop."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    # Count monitored targets at startup for the startup log
    workspace_target_count: int = 0
    try:
        from services.api.app.pilot import ensure_pilot_schema, pg_connection
        with pg_connection() as conn:
            ensure_pilot_schema(conn)
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM targets "
                "WHERE deleted_at IS NULL AND monitoring_enabled = TRUE "
                "AND enabled = TRUE AND is_active = TRUE "
                "AND COALESCE(chain_network, 'base') = 'base'"
            ).fetchone()
            workspace_target_count = int((row or {}).get('cnt') or (row[0] if row else 0) or 0)
    except Exception:
        workspace_target_count = -1  # unknown

    logger.info(
        'realtime_worker_started chain_id=%s provider_mode=%s '
        'ws_host=%s rpc_host=%s confirmations=%s '
        'max_events_per_minute=%s workspace_target_count=%s watcher=%s',
        _BASE_CHAIN_ID,
        config['provider_mode'],
        config['ws_url_host'],
        config['rpc_url_host'],
        config['confirmations'],
        config['max_events_per_minute'],
        workspace_target_count,
        config['watcher_name'],
    )

    ingestor = BaseRealtimeIngestor(
        rpc_url=str(config['rpc_url']),
        ws_url=str(config['ws_url']),
        watcher_name=str(config['watcher_name']),
        confirmations_required=int(config['confirmations']),
        max_events_per_minute=int(config['max_events_per_minute']),
    )

    await ingestor.run_forever()


def main() -> int:
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    logger.info('base_realtime_worker starting')

    _health_port_raw = (os.getenv('REALTIME_WORKER_PORT') or os.getenv('PORT') or '').strip()
    _health_port = int(_health_port_raw) if _health_port_raw.isdigit() else 8006
    _start_health_server(_health_port)

    config = _resolve_config()
    can_start, reason = _check_realtime_config(config)

    if not can_start:
        logger.info(
            'realtime_disabled reason=%s '
            'set BASE_REALTIME_ENABLED=true and BASE_WS_RPC_URL to enable',
            reason,
        )
        # Stay alive (health server responds) but do nothing.
        # This lets Railway keep the container up without burning RPC budget.
        while True:
            logger.debug('realtime_worker_idle reason=%s', reason)
            time.sleep(60)

    if config['provider_mode'] != 'websocket':
        logger.error(
            'realtime_provider_not_supported provider_mode=%s '
            'only websocket is implemented; set BASE_REALTIME_PROVIDER=websocket',
            config['provider_mode'],
        )
        sys.exit(1)

    try:
        asyncio.run(_run_ingestor(config))
    except KeyboardInterrupt:
        logger.info('base_realtime_worker interrupted')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
