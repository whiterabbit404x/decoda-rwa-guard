"""
System Health snapshot builder for GET /ops/system-health.

Collects live infrastructure facts (DB, Redis, RPC, worker heartbeat, telemetry,
detection, alert delivery) and assembles a SaaS-grade status response.

Status vocabulary:
  healthy     - checked and passing
  degraded    - configured and partially working but stale / slow / incomplete
  failing     - configured, check ran, but failed
  unavailable - not configured or check cannot be run
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKER_HEARTBEAT_STALE_SECONDS = int(os.getenv('WORKER_HEARTBEAT_TTL_SECONDS', '180')) * 2
TELEMETRY_STALE_SECONDS = 3600        # 1 hour  → degraded
DETECTION_STALE_SECONDS = 86400 * 2   # 48 hours → degraded
POLL_INTERVAL_SECONDS = max(10, int(os.getenv('MONITOR_POLL_INTERVAL_SECONDS', '30')))


def _age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds()
    except Exception:
        return None


def _human_age(ts: str | None) -> str:
    age = _age_seconds(ts)
    if age is None:
        return 'never'
    age = max(0, age)
    if age < 60:
        return f'{int(age)}s ago'
    if age < 3600:
        return f'{int(age // 60)}m ago'
    if age < 86400:
        return f'{int(age // 3600)}h ago'
    return f'{int(age // 86400)}d ago'


def _sanitize_error(exc: Exception) -> str:
    """Return a safe, non-secret error class name."""
    return type(exc).__name__


def _component(
    status: str,
    message: str,
    *,
    age: str | None = None,
    last_event: str | None = None,
    metric: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    return {
        'status': status,
        'message': message,
        'age': age,
        'last_event': last_event,
        'metric': metric,
        'action': action,
    }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_api() -> dict[str, Any]:
    return _component(
        'healthy',
        'API is responding.',
        metric='active',
    )


def _check_database(connection: Any) -> dict[str, Any]:
    try:
        connection.execute('SELECT 1').fetchone()
        return _component('healthy', 'Database is reachable.')
    except Exception as exc:
        return _component(
            'failing',
            f'Database query failed ({_sanitize_error(exc)}).',
            action='Verify DATABASE_URL and check database connectivity.',
        )


def _check_redis() -> dict[str, Any]:
    redis_url = os.getenv('REDIS_URL', '').strip()
    upstash_url = os.getenv('UPSTASH_REDIS_REST_URL', '').strip()
    upstash_token = os.getenv('UPSTASH_REDIS_REST_TOKEN', '').strip()
    configured = bool(redis_url or (upstash_url and upstash_token))
    if not configured:
        return _component(
            'unavailable',
            'Redis is not configured.',
            action='Set REDIS_URL (or UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN) to enable Redis.',
        )
    try:
        from services.api.app.domains.rate_limit import rate_limit_connectivity
        health = rate_limit_connectivity()
        if health.get('connected'):
            return _component('healthy', 'Redis ping succeeded.', metric=health.get('backend', 'redis'))
        return _component(
            'failing',
            'Redis is configured but ping failed.',
            action='Verify REDIS_URL and Redis server health.',
        )
    except Exception as exc:
        return _component(
            'failing',
            f'Redis check failed ({_sanitize_error(exc)}).',
            action='Verify Redis connectivity.',
        )


def _check_rpc() -> dict[str, Any]:
    # Use the same URL resolution as the worker so System Health and the worker
    # always agree on which env var supplies the RPC endpoint.
    try:
        from services.api.app.evm_activity_provider import _resolve_evm_rpc_url as _resolve_url
        rpc_url = _resolve_url()
    except Exception:
        rpc_url = (os.getenv('STAGING_EVM_RPC_URL') or os.getenv('EVM_RPC_URL') or '').strip()

    if not rpc_url:
        return _component(
            'unavailable',
            'Base RPC URL is not configured.',
            action=(
                'Set EVM_RPC_URL or STAGING_EVM_RPC_URL in the worker service. '
                'For Base mainnet also set EVM_RPC_URL_8453 (or BASE_EVM_RPC_URL) and EVM_CHAIN_ID=8453.'
            ),
        )

    # Sanitize: only show the host portion, never the path/key.
    try:
        from urllib.parse import urlparse as _up
        rpc_host = _up(rpc_url).hostname or 'unconfigured'
    except Exception:
        rpc_host = 'configured'

    payload = b'{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
    try:
        req = UrlRequest(
            rpc_url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urlopen(req, timeout=8) as resp:
            import json as _json
            body = _json.loads(resp.read())
            if isinstance(body, dict) and body.get('error'):
                rpc_err = body['error']
                err_msg = str(rpc_err.get('message', '')) if isinstance(rpc_err, dict) else str(rpc_err)
                err_code = rpc_err.get('code', 0) if isinstance(rpc_err, dict) else 0
                if err_code in (-32000, -32003) or 'unauthorized' in err_msg.lower() or 'invalid key' in err_msg.lower():
                    reason = 'unauthorized_key'
                    action = 'Provider rejected the API key. Check EVM_RPC_URL includes a valid key.'
                else:
                    reason = 'provider_error'
                    action = 'Check EVM_RPC_URL and provider status.'
                return _component(
                    'failing',
                    f'eth_blockNumber provider error on {rpc_host}: {reason}.',
                    action=action,
                )
            block_hex = body.get('result')
            if block_hex:
                block_num = int(block_hex, 16)
                return _component(
                    'healthy',
                    f'eth_blockNumber succeeded (host: {rpc_host}).',
                    metric=f'block #{block_num}',
                )
            return _component(
                'failing',
                f'eth_blockNumber returned no result (host: {rpc_host}).',
                action='Check EVM_RPC_URL and provider quota.',
            )
    except HTTPError as exc:
        if exc.code in (401, 403):
            reason = f'unauthorized_key (HTTP {exc.code})'
            action = f'Provider returned HTTP {exc.code}. Check EVM_RPC_URL includes a valid API key.'
        elif exc.code == 429:
            reason = f'rate_limited (HTTP {exc.code})'
            action = 'Provider is rate-limiting. Check EVM_RPC_URL quota or reduce polling frequency.'
        else:
            reason = f'http_{exc.code}'
            action = f'Provider returned HTTP {exc.code}. Check EVM_RPC_URL and provider status.'
        return _component(
            'failing',
            f'eth_blockNumber failed on {rpc_host}: {reason}.',
            action=action,
        )
    except (URLError, OSError) as exc:
        err_str = str(getattr(exc, 'reason', exc)).lower()
        if 'timed out' in err_str or 'timeout' in err_str:
            reason = 'timeout'
            action = 'RPC endpoint did not respond within 8s. Check EVM_RPC_URL and provider availability.'
        elif any(s in err_str for s in ('name or service not known', 'nodename nor servname', 'getaddrinfo failed', 'name resolution')):
            reason = 'bad_url_or_hostname'
            action = 'RPC hostname cannot be resolved. Verify EVM_RPC_URL hostname is correct.'
        elif 'connection refused' in err_str:
            reason = 'connection_refused'
            action = 'RPC connection refused. Check EVM_RPC_URL and provider availability.'
        else:
            reason = 'network_error'
            action = 'Check EVM_RPC_URL connectivity in the Railway worker service.'
        return _component(
            'failing',
            f'eth_blockNumber failed on {rpc_host}: {reason}.',
            action=action,
        )
    except Exception as exc:
        return _component(
            'failing',
            f'RPC probe error on {rpc_host}: {_sanitize_error(exc)}.',
            action='Check EVM_RPC_URL in the worker service.',
        )


def _check_worker(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    worker_enabled = os.getenv('WORKER_ENABLED', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "WHERE workspace_id = %s ORDER BY last_heartbeat_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "ORDER BY last_heartbeat_at DESC LIMIT 1"
            ).fetchone()
    except Exception:
        row = None

    last_hb = None
    if row:
        val = row.get('last_heartbeat_at') if isinstance(row, dict) else (row[0] if row else None)
        if val is not None:
            last_hb = val.isoformat() if isinstance(val, datetime) else str(val)

    age = _age_seconds(last_hb)
    if last_hb is None:
        if worker_enabled:
            return _component(
                'failing',
                'Worker heartbeat not received. Worker is configured but not reporting.',
                action='Check the worker service is running and WORKER_ENABLED=true.',
            )
        return _component('unavailable', 'Worker is disabled (WORKER_ENABLED=false).')

    if age is not None and age <= POLL_INTERVAL_SECONDS * 2:
        return _component(
            'healthy',
            f'Worker heartbeat is fresh ({_human_age(last_hb)}).',
            age=_human_age(last_hb),
            last_event=last_hb,
        )
    if age is not None and age <= WORKER_HEARTBEAT_STALE_SECONDS:
        return _component(
            'degraded',
            f'Worker heartbeat is recent but approaching stale ({_human_age(last_hb)}).',
            age=_human_age(last_hb),
            last_event=last_hb,
        )
    return _component(
        'degraded',
        f'Worker heartbeat is stale ({_human_age(last_hb)}). Worker may have stopped.',
        age=_human_age(last_hb),
        last_event=last_hb,
        action='Check the worker service logs in Railway.',
    )


def _check_live_polling(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    """Check last monitoring poll time (monitoring_polls or monitoring_runs)."""
    last_poll = None
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT poll_started_at FROM monitoring_polls "
                "WHERE workspace_id = %s ORDER BY poll_started_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT poll_started_at FROM monitoring_polls "
                "ORDER BY poll_started_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('poll_started_at') if isinstance(row, dict) else (row[0] if row else None)
            if val is not None:
                last_poll = val.isoformat() if isinstance(val, datetime) else str(val)
    except Exception:
        pass

    if last_poll is None:
        try:
            if workspace_id:
                row = connection.execute(
                    "SELECT started_at FROM monitoring_runs "
                    "WHERE workspace_id = %s ORDER BY started_at DESC LIMIT 1",
                    (workspace_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT started_at FROM monitoring_runs "
                    "ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            if row:
                val = row.get('started_at') if isinstance(row, dict) else (row[0] if row else None)
                if val is not None:
                    last_poll = val.isoformat() if isinstance(val, datetime) else str(val)
        except Exception:
            pass

    if last_poll is None:
        return _component('unavailable', 'No polling records found.', action='Ensure worker is running and targets are configured.')
    age = _age_seconds(last_poll)
    if age is not None and age <= POLL_INTERVAL_SECONDS * 3:
        return _component('healthy', f'Live polling is active ({_human_age(last_poll)}).', age=_human_age(last_poll), last_event=last_poll)
    return _component('degraded', f'Last poll is stale ({_human_age(last_poll)}).', age=_human_age(last_poll), last_event=last_poll, action='Check worker polling loop.')


def _check_telemetry(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events "
                "WHERE workspace_id = %s ORDER BY observed_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('observed_at') if isinstance(row, dict) else (row[0] if row else None)
            last_ts = val.isoformat() if isinstance(val, datetime) else str(val) if val else None
        else:
            last_ts = None
    except Exception:
        return _component('unavailable', 'Telemetry table not accessible.', action='Check database migrations.')

    if last_ts is None:
        return _component('unavailable', 'No telemetry events received.', action='Check worker, RPC connectivity, and monitoring targets.')

    age = _age_seconds(last_ts)
    if age is not None and age <= TELEMETRY_STALE_SECONDS:
        return _component('healthy', f'Telemetry is fresh ({_human_age(last_ts)}).', age=_human_age(last_ts), last_event=last_ts)
    return _component(
        'degraded',
        f'Last telemetry is stale ({_human_age(last_ts)}). Worker may be running but chain data is not flowing.',
        age=_human_age(last_ts),
        last_event=last_ts,
        action='Check EVM_RPC_URL connectivity and whether monitored addresses have on-chain activity.',
    )


def _check_detection(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    last_ts = None
    for table, col in [('detection_events', 'created_at'), ('detections', 'created_at')]:
        try:
            if workspace_id:
                row = connection.execute(
                    f"SELECT {col} FROM {table} WHERE workspace_id = %s ORDER BY {col} DESC LIMIT 1",
                    (workspace_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT {col} FROM {table} ORDER BY {col} DESC LIMIT 1"
                ).fetchone()
            if row:
                val = row.get(col) if isinstance(row, dict) else (row[0] if row else None)
                if val is not None:
                    last_ts = val.isoformat() if isinstance(val, datetime) else str(val)
                    break
        except Exception:
            continue

    if last_ts is None:
        return _component(
            'unavailable',
            'No detection events found.',
            action='Detection requires live telemetry. Check telemetry ingestion first.',
        )

    age = _age_seconds(last_ts)
    if age is not None and age <= DETECTION_STALE_SECONDS:
        return _component('healthy', f'Detection is recent ({_human_age(last_ts)}).', age=_human_age(last_ts), last_event=last_ts)
    return _component(
        'degraded',
        f'Last detection is stale ({_human_age(last_ts)}). Telemetry may be flowing but no detections triggered.',
        age=_human_age(last_ts),
        last_event=last_ts,
        action='Check detection rules and whether monitored wallets have relevant on-chain activity.',
    )


def _check_alert_delivery() -> dict[str, Any]:
    try:
        from services.api.app.domains import alert_delivery
        snapshot = alert_delivery.health_snapshot()
        ready = bool(snapshot.get('ready'))
        outbox = snapshot.get('outbox') or {}
        pending = outbox.get('pending') or 0
        dead_letter = outbox.get('dead_letter') or 0
        if ready:
            msg = 'Alert delivery is healthy.'
            if pending:
                msg += f' Outbox pending: {pending}.'
            if dead_letter:
                msg += f' Dead-letter: {dead_letter}.'
            status = 'healthy' if not dead_letter else 'degraded'
            return _component(status, msg, metric=f'pending={pending}, dead_letter={dead_letter}')
        return _component(
            'degraded',
            'Alert delivery is not ready.',
            action='Check Redis connectivity for alert stream delivery.',
        )
    except Exception as exc:
        return _component(
            'unavailable',
            f'Alert delivery check failed ({_sanitize_error(exc)}).',
        )


# ---------------------------------------------------------------------------
# Live chain monitoring section
# ---------------------------------------------------------------------------

def _build_live_chain_monitoring(
    connection: Any, workspace_id: str | None, rpc_check: dict[str, Any] | None = None
) -> dict[str, Any]:
    worker_enabled = os.getenv('WORKER_ENABLED', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
    try:
        from services.api.app.evm_activity_provider import _resolve_evm_rpc_url as _resolve_url
        rpc_url = _resolve_url()
    except Exception:
        rpc_url = (os.getenv('STAGING_EVM_RPC_URL') or os.getenv('EVM_RPC_URL') or '').strip()
    rpc_configured = bool(rpc_url)
    chain_id_str = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    expected_chain_id = int(chain_id_str) if chain_id_str.isdigit() else 8453

    # Heartbeat
    last_heartbeat_at: str | None = None
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "WHERE workspace_id = %s ORDER BY last_heartbeat_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "ORDER BY last_heartbeat_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('last_heartbeat_at') if isinstance(row, dict) else (row[0] if row else None)
            if val is not None:
                last_heartbeat_at = val.isoformat() if isinstance(val, datetime) else str(val)
    except Exception:
        pass

    heartbeat_age = _age_seconds(last_heartbeat_at)

    # Last poll
    last_poll_at: str | None = None
    last_successful_poll_at: str | None = None
    latest_polled_block: int | None = None
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT poll_started_at, poll_finished_at, status FROM monitoring_polls "
                "WHERE workspace_id = %s ORDER BY poll_started_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT poll_started_at, poll_finished_at, status FROM monitoring_polls "
                "ORDER BY poll_started_at DESC LIMIT 1"
            ).fetchone()
        if row:
            d = dict(row) if hasattr(row, 'keys') else {}
            ps = d.get('poll_started_at') or (row[0] if len(row) > 0 else None)
            pf = d.get('poll_finished_at') or (row[1] if len(row) > 1 else None)
            st = d.get('status') or (row[2] if len(row) > 2 else None)
            last_poll_at = ps.isoformat() if isinstance(ps, datetime) else str(ps) if ps else None
            if st == 'success' and pf:
                last_successful_poll_at = pf.isoformat() if isinstance(pf, datetime) else str(pf)
    except Exception:
        pass

    # Telemetry counts
    last_telemetry_at: str | None = None
    recent_telemetry_1h: int = 0
    recent_telemetry_24h: int = 0
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events WHERE workspace_id = %s ORDER BY observed_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('observed_at') if isinstance(row, dict) else (row[0] if row else None)
            if val is not None:
                last_telemetry_at = val.isoformat() if isinstance(val, datetime) else str(val)
        # Recent counts
        if workspace_id:
            r1 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE workspace_id = %s AND observed_at >= NOW() - INTERVAL '1 hour'",
                (workspace_id,),
            ).fetchone()
            r24 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE workspace_id = %s AND observed_at >= NOW() - INTERVAL '24 hours'",
                (workspace_id,),
            ).fetchone()
        else:
            r1 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE observed_at >= NOW() - INTERVAL '1 hour'"
            ).fetchone()
            r24 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE observed_at >= NOW() - INTERVAL '24 hours'"
            ).fetchone()
        recent_telemetry_1h = int((r1 or {}).get('cnt') or (r1[0] if r1 else 0) or 0)
        recent_telemetry_24h = int((r24 or {}).get('cnt') or (r24[0] if r24 else 0) or 0)
    except Exception:
        pass

    # Detection counts
    last_detection_at: str | None = None
    recent_detections_1h: int = 0
    recent_detections_24h: int = 0
    for table, col in [('detection_events', 'created_at'), ('detections', 'created_at')]:
        try:
            if workspace_id:
                row = connection.execute(
                    f"SELECT {col} FROM {table} WHERE workspace_id = %s ORDER BY {col} DESC LIMIT 1",
                    (workspace_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT {col} FROM {table} ORDER BY {col} DESC LIMIT 1"
                ).fetchone()
            if row:
                val = row.get(col) if isinstance(row, dict) else (row[0] if row else None)
                if val is not None:
                    last_detection_at = val.isoformat() if isinstance(val, datetime) else str(val)
                    if workspace_id:
                        r1 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE workspace_id = %s AND {col} >= NOW() - INTERVAL '1 hour'",
                            (workspace_id,),
                        ).fetchone()
                        r24 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE workspace_id = %s AND {col} >= NOW() - INTERVAL '24 hours'",
                            (workspace_id,),
                        ).fetchone()
                    else:
                        r1 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE {col} >= NOW() - INTERVAL '1 hour'"
                        ).fetchone()
                        r24 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE {col} >= NOW() - INTERVAL '24 hours'"
                        ).fetchone()
                    recent_detections_1h = int((r1 or {}).get('cnt') or (r1[0] if r1 else 0) or 0)
                    recent_detections_24h = int((r24 or {}).get('cnt') or (r24[0] if r24 else 0) or 0)
                    break
        except Exception:
            continue

    # Build diagnosis
    hb_age = _age_seconds(last_heartbeat_at)
    hb_fresh = hb_age is not None and hb_age <= POLL_INTERVAL_SECONDS * 2
    tel_age = _age_seconds(last_telemetry_at)
    tel_fresh = tel_age is not None and tel_age <= TELEMETRY_STALE_SECONDS

    # Reuse the already-computed Base RPC probe when provided. The probe makes a
    # blocking on-chain call (up to 8s); recomputing it here would multiply the
    # endpoint's response time and is the main reason the client used to time out.
    if rpc_check is None:
        rpc_check = _check_rpc()
    rpc_healthy = rpc_check['status'] == 'healthy'

    if not worker_enabled:
        diagnosis = 'Worker is disabled (WORKER_ENABLED=false). Live monitoring is inactive.'
    elif not rpc_configured:
        diagnosis = (
            'EVM RPC URL is not configured. Set EVM_RPC_URL (or EVM_RPC_URL_8453 / BASE_EVM_RPC_URL '
            'with EVM_CHAIN_ID=8453) in the worker service.'
        )
    elif not rpc_healthy:
        rpc_msg = rpc_check.get('message', 'RPC probe failed.')
        diagnosis = f'Base RPC is failing: {rpc_msg} Chain data cannot be fetched.'
    elif last_heartbeat_at is None:
        diagnosis = 'RPC is configured but no worker heartbeat received. Worker may not be running.'
    elif not hb_fresh:
        diagnosis = f'Worker heartbeat is stale ({_human_age(last_heartbeat_at)}). Worker may have stopped.'
    elif last_telemetry_at is None:
        diagnosis = 'Worker is healthy and RPC is reachable, but no telemetry has been ingested. Check monitored targets.'
    elif not tel_fresh:
        diagnosis = f'RPC is healthy and worker is polling, but telemetry ingestion is stale ({_human_age(last_telemetry_at)}).'
    elif last_detection_at is None:
        diagnosis = 'Telemetry is flowing, but no detection events were found. Check detection rules and on-chain activity.'
    elif recent_detections_24h == 0:
        diagnosis = 'Telemetry is flowing, but no detections in the last 24h. Monitoring is running but no new events triggered.'
    else:
        diagnosis = 'All monitored systems are operational. Worker is healthy, RPC is reachable, telemetry is flowing, detections are running.'

    return {
        'expected_chain_id': expected_chain_id,
        'rpc_configured': rpc_configured,
        'latest_rpc_block': rpc_check.get('metric'),
        'worker_enabled': worker_enabled,
        'last_heartbeat_at': last_heartbeat_at,
        'heartbeat_age_seconds': int(heartbeat_age) if heartbeat_age is not None else None,
        'heartbeat_age_human': _human_age(last_heartbeat_at),
        'polling_interval_seconds': POLL_INTERVAL_SECONDS,
        'last_poll_at': last_poll_at,
        'last_successful_poll_at': last_successful_poll_at,
        'latest_polled_block': latest_polled_block,
        'last_telemetry_at': last_telemetry_at,
        'last_detection_at': last_detection_at,
        'recent_telemetry_1h': recent_telemetry_1h,
        'recent_telemetry_24h': recent_telemetry_24h,
        'recent_detections_1h': recent_detections_1h,
        'recent_detections_24h': recent_detections_24h,
        'diagnosis': diagnosis,
    }


# ---------------------------------------------------------------------------
# Provider health section
# ---------------------------------------------------------------------------

def _build_providers(
    connection: Any, workspace_id: str | None, rpc_check: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []

    # Base RPC provider — reuse the already-computed probe to avoid a third
    # blocking on-chain call per request.
    rpc = rpc_check if rpc_check is not None else _check_rpc()
    providers.append({
        'name': 'Base RPC (EVM)',
        'type': 'rpc',
        'status': rpc['status'],
        'message': rpc['message'],
        'action': rpc.get('action'),
    })

    # Redis
    redis = _check_redis()
    providers.append({
        'name': 'Redis',
        'type': 'cache/queue',
        'status': redis['status'],
        'message': redis['message'],
        'action': redis.get('action'),
    })

    # Database
    db = _check_database(connection)
    providers.append({
        'name': 'Database',
        'type': 'postgresql',
        'status': db['status'],
        'message': db['message'],
        'action': db.get('action'),
    })

    # Try provider_health_records
    try:
        if workspace_id:
            rows = connection.execute(
                "SELECT provider_type, status, checked_at, latency_ms FROM provider_health_records "
                "WHERE workspace_id = %s ORDER BY checked_at DESC LIMIT 10",
                (workspace_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT provider_type, status, checked_at, latency_ms FROM provider_health_records "
                "ORDER BY checked_at DESC LIMIT 10"
            ).fetchall()
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else {}
            provider_type = d.get('provider_type') or (row[0] if row else None)
            status_raw = d.get('status') or (row[1] if len(row) > 1 else None)
            checked_at = d.get('checked_at') or (row[2] if len(row) > 2 else None)
            latency = d.get('latency_ms') or (row[3] if len(row) > 3 else None)
            if not provider_type:
                continue
            status = 'healthy' if str(status_raw or '').lower() in {'ok', 'healthy', 'pass', 'success'} else 'degraded'
            providers.append({
                'name': str(provider_type),
                'type': 'provider',
                'status': status,
                'message': f'Status: {status_raw}',
                'last_event': checked_at.isoformat() if isinstance(checked_at, datetime) else str(checked_at) if checked_at else None,
                'metric': f'{latency}ms' if latency else None,
            })
    except Exception:
        pass

    return providers


# ---------------------------------------------------------------------------
# Events / timeline section
# ---------------------------------------------------------------------------

def _build_events(connection: Any, workspace_id: str | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    # Recent monitoring worker errors
    try:
        if workspace_id:
            rows = connection.execute(
                "SELECT poll_started_at, error_message FROM monitoring_polls "
                "WHERE workspace_id = %s AND status = 'error' ORDER BY poll_started_at DESC LIMIT 5",
                (workspace_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT poll_started_at, error_message FROM monitoring_polls "
                "WHERE status = 'error' ORDER BY poll_started_at DESC LIMIT 5"
            ).fetchall()
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else {}
            ts = d.get('poll_started_at') or (row[0] if row else None)
            err = d.get('error_message') or (row[1] if len(row) > 1 else None)
            if ts:
                ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                events.append({
                    'time': ts_str,
                    'component': 'Polling Worker',
                    'event': f'Poll error: {str(err or "unknown")[:120]}' if err else 'Poll failed.',
                    'severity': 'high',
                    'kind': 'poll_error',
                })
    except Exception:
        pass

    # Recent provider health failures
    try:
        if workspace_id:
            rows = connection.execute(
                "SELECT checked_at, provider_type, status FROM provider_health_records "
                "WHERE workspace_id = %s AND status NOT IN ('ok','healthy','pass') ORDER BY checked_at DESC LIMIT 5",
                (workspace_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT checked_at, provider_type, status FROM provider_health_records "
                "WHERE status NOT IN ('ok','healthy','pass') ORDER BY checked_at DESC LIMIT 5"
            ).fetchall()
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else {}
            ts = d.get('checked_at') or (row[0] if row else None)
            pt = d.get('provider_type') or (row[1] if len(row) > 1 else None)
            st = d.get('status') or (row[2] if len(row) > 2 else None)
            if ts:
                ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                events.append({
                    'time': ts_str,
                    'component': str(pt or 'Provider'),
                    'event': f'Provider health check returned: {st}',
                    'severity': 'medium',
                    'kind': 'provider_health',
                })
    except Exception:
        pass

    events.sort(key=lambda e: e.get('time') or '', reverse=True)
    return events[:20]


# ---------------------------------------------------------------------------
# Reliability snapshot
# ---------------------------------------------------------------------------

def _build_reliability(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {}

    # Active monitoring targets count
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT COUNT(*) AS cnt FROM monitoring_targets WHERE workspace_id = %s AND COALESCE(is_enabled, true) = true",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(*) AS cnt FROM monitoring_targets WHERE COALESCE(is_enabled, true) = true"
            ).fetchone()
        result['active_targets'] = int((row or {}).get('cnt') or (row[0] if row else 0) or 0)
    except Exception:
        result['active_targets'] = None

    # Monitored chains
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT COUNT(DISTINCT chain_id) AS cnt FROM monitoring_targets WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(DISTINCT chain_id) AS cnt FROM monitoring_targets"
            ).fetchone()
        result['monitored_chains'] = int((row or {}).get('cnt') or (row[0] if row else 0) or 0)
    except Exception:
        result['monitored_chains'] = None

    # RPC success rate from provider_health_records (last 100)
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT COUNT(*) FILTER (WHERE status IN ('ok','healthy','pass')) AS ok_cnt, COUNT(*) AS total "
                "FROM provider_health_records WHERE workspace_id = %s AND provider_type LIKE '%%rpc%%'",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(*) FILTER (WHERE status IN ('ok','healthy','pass')) AS ok_cnt, COUNT(*) AS total "
                "FROM provider_health_records WHERE provider_type LIKE '%%rpc%%'"
            ).fetchone()
        if row:
            d = dict(row) if hasattr(row, 'keys') else {}
            ok = int(d.get('ok_cnt') or (row[0] if row else 0) or 0)
            total = int(d.get('total') or (row[1] if len(row) > 1 else 0) or 0)
            result['rpc_success_rate'] = f'{ok}/{total}' if total > 0 else 'unavailable: no records'
        else:
            result['rpc_success_rate'] = 'unavailable: no records'
    except Exception:
        result['rpc_success_rate'] = 'unavailable: metric not implemented'

    return result


# ---------------------------------------------------------------------------
# Overall status computation
# ---------------------------------------------------------------------------

STATUS_ORDER = {'failing': 0, 'degraded': 1, 'healthy': 2, 'unavailable': 3}


def _aggregate_status(components: dict[str, dict[str, Any]]) -> str:
    statuses = [c.get('status', 'unavailable') for c in components.values()]
    if any(s == 'failing' for s in statuses):
        return 'failing'
    if any(s == 'degraded' for s in statuses):
        return 'degraded'
    if all(s == 'unavailable' for s in statuses):
        return 'unavailable'
    if any(s == 'healthy' for s in statuses):
        return 'degraded' if any(s in {'failing', 'degraded'} for s in statuses) else 'healthy'
    return 'unavailable'


def _build_summary(components: dict[str, dict[str, Any]], chain_monitoring: dict[str, Any]) -> str:
    failing = [k for k, c in components.items() if c.get('status') == 'failing']
    degraded = [k for k, c in components.items() if c.get('status') == 'degraded']
    if not failing and not degraded:
        return 'All monitored systems are operational.'
    parts = []
    if failing:
        parts.append(f'{", ".join(failing).replace("_", " ")} is failing')
    if degraded:
        parts.append(f'{", ".join(degraded).replace("_", " ")} is degraded')
    return '; '.join(parts).capitalize() + '.'


def _build_primary_action(components: dict[str, dict[str, Any]]) -> str | None:
    for key in ('base_rpc', 'worker', 'telemetry', 'database', 'redis', 'detection', 'alert_delivery', 'live_polling'):
        comp = components.get(key, {})
        if comp.get('status') in ('failing', 'degraded') and comp.get('action'):
            return comp['action']
    return None


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_system_health_snapshot(request: Any = None) -> dict[str, Any]:
    from services.api.app.pilot import pg_connection, runtime_environment_identity
    import os

    generated_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    env_raw = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    if env_raw in {'production', 'prod'}:
        environment = 'production'
    elif env_raw in {'staging'}:
        environment = 'staging'
    elif env_raw in {'local', 'development', 'dev'}:
        environment = 'local'
    else:
        environment = 'unknown'

    version: str | None = None
    git_commit: str | None = None
    try:
        from services.api.app.main import BACKEND_BUILD_ID, BACKEND_GIT_COMMIT
        version = BACKEND_BUILD_ID
        git_commit = BACKEND_GIT_COMMIT
    except Exception:
        pass

    # Resolve workspace_id safely
    workspace_id: str | None = None
    if request is not None:
        try:
            workspace_id = str(request.headers.get('x-workspace-id') or '').strip() or None
        except Exception:
            pass

    components: dict[str, dict[str, Any]] = {}
    components['api'] = _check_api()

    try:
        with pg_connection() as connection:
            components['database'] = _check_database(connection)
            components['redis'] = _check_redis()
            components['worker'] = _check_worker(connection, workspace_id)
            # Compute the Base RPC probe once and reuse it everywhere it is needed
            # (component, live chain monitoring, providers) to keep the endpoint fast.
            base_rpc_check = _check_rpc()
            components['base_rpc'] = base_rpc_check
            components['live_polling'] = _check_live_polling(connection, workspace_id)
            components['telemetry'] = _check_telemetry(connection, workspace_id)
            components['detection'] = _check_detection(connection, workspace_id)
            components['alert_delivery'] = _check_alert_delivery()

            chain_monitoring = _build_live_chain_monitoring(connection, workspace_id, rpc_check=base_rpc_check)
            events = _build_events(connection, workspace_id)
            providers = _build_providers(connection, workspace_id, rpc_check=base_rpc_check)
            reliability = _build_reliability(connection, workspace_id)
    except Exception as exc:
        # DB connection failed entirely
        components['database'] = _component(
            'failing',
            f'Database connection failed ({_sanitize_error(exc)}).',
            action='Verify DATABASE_URL is configured correctly.',
        )
        components.setdefault('redis', _check_redis())
        components.setdefault('worker', _component('unavailable', 'Cannot check worker: database unavailable.'))
        components.setdefault('base_rpc', _check_rpc())
        components.setdefault('live_polling', _component('unavailable', 'Cannot check polling: database unavailable.'))
        components.setdefault('telemetry', _component('unavailable', 'Cannot check telemetry: database unavailable.'))
        components.setdefault('detection', _component('unavailable', 'Cannot check detection: database unavailable.'))
        components.setdefault('alert_delivery', _check_alert_delivery())
        chain_monitoring = {
            'expected_chain_id': 8453,
            'rpc_configured': bool((os.getenv('STAGING_EVM_RPC_URL') or os.getenv('EVM_RPC_URL') or '').strip()),
            'latest_rpc_block': None,
            'worker_enabled': os.getenv('WORKER_ENABLED', 'true').strip().lower() not in {'0', 'false', 'no', 'off'},
            'last_heartbeat_at': None,
            'heartbeat_age_seconds': None,
            'heartbeat_age_human': 'unavailable',
            'polling_interval_seconds': POLL_INTERVAL_SECONDS,
            'last_poll_at': None,
            'last_successful_poll_at': None,
            'latest_polled_block': None,
            'last_telemetry_at': None,
            'last_detection_at': None,
            'recent_telemetry_1h': 0,
            'recent_telemetry_24h': 0,
            'recent_detections_1h': 0,
            'recent_detections_24h': 0,
            'diagnosis': 'Database is unavailable. Cannot evaluate chain monitoring status.',
        }
        events = []
        providers = [
            {'name': 'Database', 'type': 'postgresql', 'status': 'failing', 'message': f'Connection failed ({_sanitize_error(exc)}).'},
        ]
        reliability = {}

    overall_status = _aggregate_status(components)
    summary = _build_summary(components, chain_monitoring)
    primary_action = _build_primary_action(components)

    return {
        'generated_at': generated_at,
        'environment': environment,
        'version': version,
        'git_commit': git_commit,
        'overall_status': overall_status,
        'summary': summary,
        'primary_action': primary_action,
        'components': components,
        'live_chain_monitoring': chain_monitoring,
        'events': events,
        'providers': providers,
        'reliability': reliability,
    }
