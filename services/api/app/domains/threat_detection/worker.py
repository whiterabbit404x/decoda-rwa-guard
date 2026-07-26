"""Threat Detection Engineer worker cycle.

Incremental and restart-safe:
  * Each cycle finds workspaces with telemetry newer than their checkpoint and
    processes only the recent correlation window (never a full-history rescan).
  * The per-workspace checkpoint advances inside the same transaction as the
    upserts, so a crash resumes cleanly from the last committed position.
  * One workspace failing is caught and never stops the rest of the cycle.
  * An ENABLED cycle persists a heartbeat, so the API reports worker health from
    a fact rather than an env flag.

Never logs API keys, RPC credentials, or secrets — only ids, counts, and codes.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any

from services.api.app import pilot
from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import service

logger = logging.getLogger(__name__)


def _worker_id() -> str:
    instance = (os.getenv('RAILWAY_REPLICA_ID') or os.getenv('HOSTNAME') or socket.gethostname() or 'local').strip()
    return f'threat-detection-{instance[:64]}:{os.getpid()}'


def record_heartbeat(connection: Any, *, worker_name: str, cycle_summary: dict[str, Any] | None = None) -> None:
    """Persist a liveness heartbeat. Written only by an ENABLED cycle, so a
    missing/stale row truthfully means 'not healthy'. Best-effort."""
    if not service._table_exists(connection, 'threat_detection_worker_state'):
        return
    summary = cycle_summary or {}
    failed = int(summary.get('failed') or 0)
    processed = int(summary.get('workspaces_processed') or 0)
    try:
        connection.execute(
            '''
            INSERT INTO threat_detection_worker_state (worker_name, heartbeat_at, last_cycle_at, last_completed_at, consecutive_failures, updated_at)
            VALUES (%s, NOW(), NOW(), CASE WHEN %s > 0 THEN NOW() ELSE NULL END, %s, NOW())
            ON CONFLICT (worker_name) DO UPDATE SET
                heartbeat_at = NOW(),
                last_cycle_at = NOW(),
                last_completed_at = CASE WHEN %s > 0 THEN NOW() ELSE threat_detection_worker_state.last_completed_at END,
                consecutive_failures = CASE WHEN %s > 0 THEN threat_detection_worker_state.consecutive_failures + 1 ELSE 0 END,
                updated_at = NOW()
            ''',
            (worker_name, processed, failed, processed, failed),
        )
        connection.commit()
    except Exception:
        logger.exception('event=threat_detection_worker_heartbeat_failed')


def _due_workspaces(connection: Any, *, config: dict[str, Any]) -> list[str]:
    """Workspaces with telemetry newer than their checkpoint (bounded)."""
    if not service._table_exists(connection, 'telemetry_events'):
        return []
    correlation_seconds = max(tdc.WINDOW_24H, int(config['low_slow_window_seconds']))
    try:
        rows = connection.execute(
            '''
            SELECT te.workspace_id
            FROM telemetry_events te
            LEFT JOIN threat_detection_checkpoints c ON c.workspace_id = te.workspace_id
            WHERE te.observed_at >= NOW() - (%s || ' seconds')::interval
              AND (c.last_processed_at IS NULL OR te.observed_at > c.last_processed_at)
            GROUP BY te.workspace_id
            ORDER BY MAX(te.observed_at) DESC
            LIMIT %s
            ''',
            (str(int(correlation_seconds)), int(config['workspace_batch_size'])),
        ).fetchall()
    except Exception:
        logger.exception('event=threat_detection_due_workspaces_failed')
        return []
    return [str(r['workspace_id']) for r in rows if r.get('workspace_id')]


def run_threat_detection_worker_once(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """One worker cycle across all due workspaces."""
    cfg = config or tdc.engine_config()
    worker_name = _worker_id()

    with pilot.pg_connection() as connection:
        record_heartbeat(connection, worker_name=worker_name)
        due = _due_workspaces(connection, config=cfg)

    totals = {'workspaces_due': len(due), 'workspaces_processed': 0, 'detections_created': 0, 'detections_updated': 0, 'anomalies': 0, 'alerts_upserted': 0, 'failed': 0}
    for workspace_id in due:
        try:
            stats = service.run_detection_for_workspace(workspace_id, config=cfg)
            totals['workspaces_processed'] += 1
            totals['detections_created'] += int(stats.get('detections_created') or 0)
            totals['detections_updated'] += int(stats.get('detections_updated') or 0)
            totals['anomalies'] += int(stats.get('anomalies') or 0)
            totals['alerts_upserted'] += int(stats.get('alerts_upserted') or 0)
        except Exception as exc:  # noqa: BLE001 - one workspace must not stop the cycle
            totals['failed'] += 1
            service.log_event('threat_detection_workspace_failed', workspace_id=workspace_id, error=type(exc).__name__)

    with pilot.pg_connection() as connection:
        record_heartbeat(connection, worker_name=worker_name, cycle_summary=totals)
    return totals


def resolve_startup_state(config: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    cfg = config or tdc.engine_config()
    if not cfg['enabled']:
        return 'disabled', []
    errors = tdc.blocking_configuration_errors(cfg)
    if errors:
        return 'configuration_error', errors
    return 'enabled', []
