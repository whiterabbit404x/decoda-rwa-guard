"""Base real-time ingestion worker entrypoint.

Starts only when BASE_REALTIME_ENABLED=true.
Default: disabled (BASE_REALTIME_ENABLED=false).

Required env vars when enabled:
  BASE_WS_RPC_URL_PRIMARY   WebSocket RPC endpoint for Base (or legacy BASE_WS_RPC_URL)
  BASE_REALTIME_CONFIRMATIONS     (default 1)
  BASE_REALTIME_MAX_EVENTS_PER_MINUTE (default 1000)
  BASE_REALTIME_FALLBACK_TO_POLLING   (default true — polling always runs independently)

Optional provider failover env vars (order: primary WSS -> secondary WSS ->
primary HTTP fast-tail -> stable RPC polling only):
  BASE_WS_RPC_URL_SECONDARY    second WSS endpoint tried after the primary's
                               circuit opens (repeated TLS/1001 provider failures)
  BASE_HTTP_RPC_URL_PRIMARY    HTTPS RPC for JSON-RPC calls + the fast-tail fallback
                               (falls back to EVM_RPC_URL_8453/BASE_EVM_RPC_URL/
                               EVM_RPC_URL, then to the WS URL converted to https)
  BASE_HTTP_RPC_URL_SECONDARY  second HTTPS RPC the fast-tail fails over to after
                               repeated primary HTTP failures

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


def _strip_env_value(raw: str) -> str:
    """Strip whitespace and surrounding single/double quotes from env values."""
    v = raw.strip()
    if len(v) >= 2 and v[0] in ('"', "'") and v[-1] == v[0]:
        v = v[1:-1].strip()
    return v


def _normalize_ws_scheme(url: str) -> str:
    """Normalize WebSocket URL scheme to lowercase (wss:// or ws://)."""
    lower = url.lower()
    if lower.startswith('wss://'):
        return 'wss://' + url[6:]
    if lower.startswith('ws://'):
        return 'ws://' + url[5:]
    return url


def _ws_url_to_http(ws_url: str) -> str:
    """Derive HTTP RPC URL from WebSocket URL for JSON-RPC calls (eth_blockNumber, eth_getLogs)."""
    norm = ws_url.lower()
    if norm.startswith('wss://'):
        return 'https://' + ws_url[6:]
    if norm.startswith('ws://'):
        return 'http://' + ws_url[5:]
    return ws_url


def _resolve_config() -> dict[str, object]:
    """Return resolved config dict. Never exposes secrets or full URLs in logs."""
    enabled = _resolve_bool_env('BASE_REALTIME_ENABLED', default=False)
    provider_mode = _strip_env_value(os.getenv('BASE_REALTIME_PROVIDER') or 'websocket').lower()

    # Accept BASE_WS_RPC_URL_PRIMARY (explicit) > BASE_WS_RPC_URL > BASE_WS_RPC_URL_8453.
    _ws_raw_named_primary = _strip_env_value(os.getenv('BASE_WS_RPC_URL_PRIMARY') or '')
    _ws_raw_primary = _strip_env_value(os.getenv('BASE_WS_RPC_URL') or '')
    _ws_raw_8453 = _strip_env_value(os.getenv('BASE_WS_RPC_URL_8453') or '')
    _ws_raw_secondary = _strip_env_value(os.getenv('BASE_WS_RPC_URL_SECONDARY') or '')

    if _ws_raw_named_primary:
        ws_url = _normalize_ws_scheme(_ws_raw_named_primary)
        _selected_ws_env: str | None = 'BASE_WS_RPC_URL_PRIMARY'
    elif _ws_raw_primary:
        ws_url = _normalize_ws_scheme(_ws_raw_primary)
        _selected_ws_env = 'BASE_WS_RPC_URL'
    elif _ws_raw_8453:
        ws_url = _normalize_ws_scheme(_ws_raw_8453)
        _selected_ws_env = 'BASE_WS_RPC_URL_8453'
    else:
        ws_url = ''
        _selected_ws_env = None

    ws_url_secondary = _normalize_ws_scheme(_ws_raw_secondary) if _ws_raw_secondary else ''

    # HTTP RPC URL: BASE_HTTP_RPC_URL_PRIMARY (explicit) first, then the legacy env
    # vars, then derive from the WS URL.
    _http_raw_primary = _strip_env_value(os.getenv('BASE_HTTP_RPC_URL_PRIMARY') or '')
    rpc_url = (
        _http_raw_primary
        or _strip_env_value(os.getenv('EVM_RPC_URL_8453') or '')
        or _strip_env_value(os.getenv('BASE_EVM_RPC_URL') or '')
        or _strip_env_value(os.getenv('EVM_RPC_URL') or '')
    )
    if not rpc_url and ws_url:
        rpc_url = _ws_url_to_http(ws_url)

    # Secondary HTTP RPC for the fast-tail failover ladder: explicit
    # BASE_HTTP_RPC_URL_SECONDARY, else derived from the secondary WS URL.
    _http_raw_secondary = _strip_env_value(os.getenv('BASE_HTTP_RPC_URL_SECONDARY') or '')
    rpc_url_secondary = _http_raw_secondary or (
        _ws_url_to_http(ws_url_secondary) if ws_url_secondary else ''
    )
    if rpc_url_secondary and rpc_url_secondary == rpc_url:
        # A secondary identical to the primary is not a failover target.
        rpc_url_secondary = ''

    webhook_secret_set = bool(_strip_env_value(os.getenv('BASE_WEBHOOK_SECRET') or ''))
    confirmations = _resolve_int_env('BASE_REALTIME_CONFIRMATIONS', 1)
    max_events_per_minute = _resolve_int_env('BASE_REALTIME_MAX_EVENTS_PER_MINUTE', 1000)
    fallback_to_polling = _resolve_bool_env('BASE_REALTIME_FALLBACK_TO_POLLING', default=True)
    watcher_name = _strip_env_value(os.getenv('BASE_REALTIME_WATCHER_NAME') or _default_watcher_name())
    _subs_raw = _strip_env_value(os.getenv('BASE_REALTIME_SUBSCRIPTIONS') or '').lower()
    subscriptions = 'newHeads_only' if _subs_raw in ('newheads_only', 'newheads-only') else 'newHeads,logs'

    _ws_scheme = _urlparse(ws_url).scheme.lower() if ws_url else 'not_configured'

    return {
        'enabled': enabled,
        'provider_mode': provider_mode,
        'ws_url': ws_url,
        'ws_url_host': _safe_rpc_host(ws_url) if ws_url else 'not_configured',
        'ws_url_scheme': _ws_scheme,
        'ws_url_secondary': ws_url_secondary,
        'ws_url_secondary_host': _safe_rpc_host(ws_url_secondary) if ws_url_secondary else 'not_configured',
        'rpc_url': rpc_url,
        'rpc_url_host': _safe_rpc_host(rpc_url) if rpc_url else 'not_configured',
        'rpc_url_secondary': rpc_url_secondary,
        'rpc_url_secondary_host': _safe_rpc_host(rpc_url_secondary) if rpc_url_secondary else 'not_configured',
        'webhook_secret_set': webhook_secret_set,
        'confirmations': confirmations,
        'max_events_per_minute': max_events_per_minute,
        'fallback_to_polling': fallback_to_polling,
        'watcher_name': watcher_name,
        'subscriptions': subscriptions,
        # Diagnostic presence flags — never secret values.
        'base_realtime_enabled_present': bool(_strip_env_value(os.getenv('BASE_REALTIME_ENABLED') or '')),
        'base_ws_rpc_url_present': bool(_ws_raw_named_primary or _ws_raw_primary),
        'base_ws_rpc_url_8453_present': bool(_ws_raw_8453),
        'base_ws_rpc_url_primary_present': bool(_ws_raw_named_primary),
        'base_ws_rpc_url_secondary_present': bool(_ws_raw_secondary),
        'base_http_rpc_url_primary_present': bool(_http_raw_primary),
        'base_http_rpc_url_secondary_present': bool(_http_raw_secondary),
        'selected_ws_rpc_env_name': _selected_ws_env or 'none',
    }


def _check_realtime_config(config: dict[str, object]) -> tuple[bool, str]:
    """Return (can_start, reason). Fail-closed on bad config."""
    if not config['enabled']:
        return False, 'BASE_REALTIME_ENABLED_not_true'
    if config['provider_mode'] == 'websocket' and not config['ws_url']:
        return False, (
            'missing_ws_url checked_env_names='
            'BASE_WS_RPC_URL_PRIMARY,BASE_WS_RPC_URL,BASE_WS_RPC_URL_8453'
        )
    if config['provider_mode'] == 'webhook' and not config['webhook_secret_set']:
        return False, 'missing_BASE_WEBHOOK_SECRET'
    if not config['rpc_url']:
        return False, (
            'missing_rpc_url_for_Base '
            'checked_env_names=BASE_HTTP_RPC_URL_PRIMARY,BASE_WS_RPC_URL,BASE_WS_RPC_URL_8453,'
            'EVM_RPC_URL_8453,BASE_EVM_RPC_URL,EVM_RPC_URL'
        )
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


def _parse_workspace_target_count(row: object) -> int:
    """Parse COUNT(*) row from DB without KeyError on dict rows.

    sqlite3.Row, psycopg2 DictRow, and plain dicts all have .get(); tuples
    and lists are indexed by position.  The previous inline expression used
    ``row[0]`` as a fallback even when row was a dict, which raises KeyError: 0
    when the count is 0.
    """
    if row is None:
        return 0
    if hasattr(row, 'get'):  # dict-like (sqlite3.Row, psycopg2 DictRow, dict)
        return int(row.get('cnt') or 0)
    try:
        return int(row[0] or 0)  # tuple / list
    except (IndexError, TypeError):
        return 0


async def _run_ingestor(config: dict[str, object]) -> None:
    """Async entry point for the WebSocket ingestor loop."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    # Load monitored targets at startup for the startup log.
    # Mirrors the _watched_targets query in the ingestor (same filter, same chain_id fallback).
    workspace_target_count: int = 0
    _startup_target_ids: list[str] = []
    _startup_workspace_count: int = 0
    try:
        from services.api.app.pilot import ensure_pilot_schema, pg_connection
        with pg_connection() as conn:
            ensure_pilot_schema(conn)
            rows = conn.execute(
                "SELECT id, workspace_id FROM targets "
                "WHERE deleted_at IS NULL "
                "AND target_type IN ('wallet', 'contract') "
                "AND monitoring_enabled = TRUE "
                "AND enabled = TRUE AND is_active = TRUE "
                "AND ("
                "  LOWER(COALESCE(chain_network, 'base')) IN ('base', 'base-mainnet')"
                "  OR chain_id = 8453"
                ")"
            ).fetchall()
            workspace_target_count = len(rows)
            _ws_ids: set[str] = set()
            for r in rows:
                if hasattr(r, 'get'):
                    _startup_target_ids.append(str(r.get('id', '')))
                    _ws_ids.add(str(r.get('workspace_id', '')))
                else:
                    try:
                        _startup_target_ids.append(str(r[0]))
                        _ws_ids.add(str(r[1]))
                    except (IndexError, TypeError):
                        pass
            _startup_workspace_count = len(_ws_ids)
    except Exception:
        workspace_target_count = -1  # unknown; logged below, not re-raised

    logger.info(
        'realtime_targets_loaded count=%s workspace_count=%s chain_id=%s '
        'target_ids=%s watcher=%s',
        workspace_target_count,
        _startup_workspace_count,
        _BASE_CHAIN_ID,
        ','.join(_startup_target_ids[:20]),  # IDs only — no addresses or secrets
        config['watcher_name'],
    )

    logger.info(
        'realtime_worker_started chain_id=%s provider_mode=%s '
        'ws_host=%s rpc_host=%s confirmations=%s '
        'max_events_per_minute=%s workspace_target_count=%s '
        'subscriptions=%s ws_secondary_host=%s rpc_secondary_host=%s watcher=%s',
        _BASE_CHAIN_ID,
        config['provider_mode'],
        config['ws_url_host'],
        config['rpc_url_host'],
        config['confirmations'],
        config['max_events_per_minute'],
        workspace_target_count,
        config.get('subscriptions', 'newHeads,logs'),
        config.get('ws_url_secondary_host', 'not_configured'),
        config.get('rpc_url_secondary_host', 'not_configured'),
        config['watcher_name'],
    )
    if workspace_target_count == 0:
        logger.warning(
            'realtime_no_base_targets chain_id=%s '
            'hint=check_targets_have_chain_network_base_or_base_mainnet_or_chain_id_8453',
            _BASE_CHAIN_ID,
        )

    _ws_secondary = str(config.get('ws_url_secondary') or '') or None
    _rpc_secondary = str(config.get('rpc_url_secondary') or '') or None
    ingestor = BaseRealtimeIngestor(
        rpc_url=str(config['rpc_url']),
        ws_url=str(config['ws_url']),
        watcher_name=str(config['watcher_name']),
        confirmations_required=int(config['confirmations']),
        max_events_per_minute=int(config['max_events_per_minute']),
        subscriptions=str(config.get('subscriptions') or ''),
        ws_url_secondary=_ws_secondary,
        rpc_url_secondary=_rpc_secondary,
    )

    await ingestor.run_forever()


def main() -> int:
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    logger.info('base_realtime_worker starting')

    _railway_port_env = (os.getenv('PORT') or '').strip()
    _realtime_worker_port = (os.getenv('REALTIME_WORKER_PORT') or '').strip()
    _port_raw = _railway_port_env or _realtime_worker_port
    _health_port = int(_port_raw) if _port_raw.isdigit() else 8006
    logger.info(
        'realtime_port_resolution railway_port_env=%s realtime_worker_port=%s selected_port=%s',
        _railway_port_env or 'not_set',
        _realtime_worker_port or 'not_set',
        _health_port,
    )
    _start_health_server(_health_port)

    config = _resolve_config()

    logger.info(
        'base_realtime_env_check '
        'base_realtime_enabled_present=%s '
        'base_ws_rpc_url_present=%s '
        'base_ws_rpc_url_scheme=%s '
        'base_ws_rpc_url_host=%s '
        'base_ws_rpc_url_8453_present=%s '
        'selected_ws_rpc_env_name=%s',
        config['base_realtime_enabled_present'],
        config['base_ws_rpc_url_present'],
        config['ws_url_scheme'],
        config['ws_url_host'],
        config['base_ws_rpc_url_8453_present'],
        config['selected_ws_rpc_env_name'],
    )

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
