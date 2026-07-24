"""Asset Risk Assessor worker cycle.

Idempotent and distributed-safe:
  * Due assets are enqueued as jobs; the partial unique index guarantees at most
    one active (queued/running) job per asset, so repeated enqueues and repeated
    "Run assessment" clicks never create duplicate concurrent work.
  * Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` and a lease, so multiple
    worker replicas never assess the same asset at once. An expired lease is
    reclaimed (crash recovery).
  * A single asset failing (e.g. a provider error) is caught, retried with
    bounded attempts, and never stops the rest of the batch.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from typing import Any

from services.api.app import pilot
from services.api.app.domains.asset_risk import config as arc
from services.api.app.domains.asset_risk import service

logger = logging.getLogger(__name__)


def _worker_id() -> str:
    instance = (os.getenv('RAILWAY_REPLICA_ID') or os.getenv('HOSTNAME') or socket.gethostname() or 'local').strip()
    return f'asset-risk-{instance[:64]}:{os.getpid()}'


def enqueue_assessment(
    connection: Any,
    *,
    workspace_id: str,
    asset_id: str,
    trigger_source: str = 'worker',
    requested_by_user_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue a queued job unless an active one already exists. Idempotent."""
    existing = connection.execute(
        '''
        SELECT id, status FROM asset_risk_jobs
        WHERE workspace_id = %s AND asset_id = %s AND status IN ('queued', 'running')
        ORDER BY created_at DESC LIMIT 1
        ''',
        (workspace_id, asset_id),
    ).fetchone()
    if existing is not None:
        return {'enqueued': False, 'job_id': str(existing['id']), 'status': str(existing['status'])}
    job_id = str(uuid.uuid4())
    try:
        connection.execute(
            '''
            INSERT INTO asset_risk_jobs (id, workspace_id, asset_id, status, trigger_source, requested_by_user_id, queued_at)
            VALUES (%s, %s, %s, 'queued', %s, %s, NOW())
            ''',
            (job_id, workspace_id, asset_id, trigger_source, requested_by_user_id),
        )
    except Exception:
        # Lost a race on the partial unique index — an active job now exists.
        row = connection.execute(
            "SELECT id, status FROM asset_risk_jobs WHERE workspace_id = %s AND asset_id = %s AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
            (workspace_id, asset_id),
        ).fetchone()
        if row is not None:
            return {'enqueued': False, 'job_id': str(row['id']), 'status': str(row['status'])}
        raise
    return {'enqueued': True, 'job_id': job_id, 'status': 'queued'}


def _enqueue_due_assets(connection: Any, *, config: dict[str, Any]) -> int:
    """Enqueue jobs for assets whose latest assessment is missing or stale.
    Never-assessed assets are prioritized first."""
    rows = connection.execute(
        '''
        SELECT a.id, a.workspace_id
        FROM assets a
        LEFT JOIN LATERAL (
            SELECT assessed_at FROM asset_risk_assessments r
            WHERE r.workspace_id = a.workspace_id AND r.asset_id = a.id
            ORDER BY r.assessed_at DESC LIMIT 1
        ) last ON TRUE
        WHERE a.deleted_at IS NULL AND a.enabled = TRUE
          AND (last.assessed_at IS NULL OR last.assessed_at < NOW() - (%s || ' seconds')::interval)
          AND NOT EXISTS (
              SELECT 1 FROM asset_risk_jobs j
              WHERE j.workspace_id = a.workspace_id AND j.asset_id = a.id AND j.status IN ('queued', 'running')
          )
        ORDER BY last.assessed_at ASC NULLS FIRST
        LIMIT %s
        ''',
        (str(int(config['assessment_stale_seconds'])), int(config['batch_size'])),
    ).fetchall()
    enqueued = 0
    for row in rows:
        result = enqueue_assessment(
            connection, workspace_id=str(row['workspace_id']), asset_id=str(row['id']), trigger_source='worker'
        )
        if result['enqueued']:
            enqueued += 1
    return enqueued


def _claim_next_job(connection: Any, *, lease_owner: str, lease_seconds: int) -> dict[str, Any] | None:
    row = connection.execute(
        '''
        UPDATE asset_risk_jobs
        SET status = 'running', lease_owner = %s, lease_expires_at = NOW() + (%s || ' seconds')::interval,
            started_at = COALESCE(started_at, NOW()), heartbeat_at = NOW(),
            attempts = attempts + 1, updated_at = NOW()
        WHERE id = (
            SELECT id FROM asset_risk_jobs
            WHERE status = 'queued'
               OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < NOW())
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, workspace_id, asset_id, trigger_source, attempts, max_attempts
        ''',
        (lease_owner, str(int(lease_seconds))),
    ).fetchone()
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------
# Heartbeat + stuck-job reconciliation
# --------------------------------------------------------------------------
def record_heartbeat(connection: Any, *, worker_name: str, cycle_summary: dict[str, Any] | None = None) -> None:
    """Persist a liveness heartbeat for the background assessor. Written only by an
    ENABLED worker cycle, so a missing/stale row truthfully means "not healthy".
    Best-effort: a heartbeat failure never stops the cycle."""
    if not service._table_exists(connection, 'asset_risk_worker_state'):
        return
    summary = cycle_summary or {}
    failed = int(summary.get('failed') or 0)
    try:
        connection.execute(
            '''
            INSERT INTO asset_risk_worker_state (worker_name, heartbeat_at, last_cycle_at, last_completed_at, consecutive_failures, updated_at)
            VALUES (%s, NOW(), NOW(), CASE WHEN %s > 0 THEN NOW() ELSE NULL END, %s, NOW())
            ON CONFLICT (worker_name) DO UPDATE SET
                heartbeat_at = NOW(),
                last_cycle_at = NOW(),
                last_completed_at = CASE WHEN %s > 0 THEN NOW() ELSE asset_risk_worker_state.last_completed_at END,
                consecutive_failures = CASE WHEN %s > 0 THEN asset_risk_worker_state.consecutive_failures + 1 ELSE 0 END,
                updated_at = NOW()
            ''',
            (worker_name, int(summary.get('processed') or 0), failed, int(summary.get('processed') or 0), failed),
        )
        connection.commit()
    except Exception:
        logger.exception('event=asset_risk_worker_heartbeat_failed')


def reconcile_stuck_jobs(connection: Any, *, config: dict[str, Any], worker_healthy: bool, now: Any = None) -> dict[str, Any]:
    """Move jobs that can no longer make progress to a terminal state.

    A queued job older than the configured timeout with no healthy worker to claim
    it — or a running job whose lease expired with no healthy worker to reclaim it —
    becomes ``blocked`` with failure_code ``assessment_worker_unavailable`` rather
    than pending forever. When a healthy worker exists it reclaims expired running
    jobs itself (see _claim_next_job), so this only acts when worker_healthy is
    false. Idempotent and safe to call from the read path."""
    if worker_healthy or not service._table_exists(connection, 'asset_risk_jobs'):
        return {'blocked': 0}
    timeout = int(config.get('queued_job_timeout_seconds') or 900)
    try:
        rows = connection.execute(
            '''
            UPDATE asset_risk_jobs
            SET status = 'blocked', failure_code = 'assessment_worker_unavailable',
                last_error = 'No healthy assessor worker claimed the job before the timeout.',
                lease_owner = NULL, lease_expires_at = NULL, completed_at = NOW(), updated_at = NOW()
            WHERE (
                (status = 'queued' AND queued_at IS NOT NULL AND queued_at < NOW() - (%s || ' seconds')::interval)
                OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < NOW() - (%s || ' seconds')::interval)
            )
            RETURNING id, workspace_id, asset_id, status
            ''',
            (str(timeout), str(timeout)),
        ).fetchall()
    except Exception:
        logger.exception('event=asset_assessment_reconcile_failed')
        return {'blocked': 0}
    for row in rows:
        service.log_assessment_event(
            'asset_assessment_blocked', workspace_id=row.get('workspace_id'), asset_id=row.get('asset_id'),
            job_id=row.get('id'), failure_code='assessment_worker_unavailable', worker_healthy=False,
        )
        service.log_assessment_event(
            'asset_assessment_stale_job_recovered', workspace_id=row.get('workspace_id'),
            asset_id=row.get('asset_id'), job_id=row.get('id'), result='blocked',
        )
    return {'blocked': len(rows)}


def run_asset_risk_worker_once(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """One worker cycle. Enqueues due assets, then processes up to batch_size jobs."""
    cfg = config or arc.assessor_config()
    lease_owner = _worker_id()
    processed = 0
    failed = 0
    enqueued = 0

    # Enqueue in its own short transaction so claiming is not blocked by it. A
    # running (enabled) cycle is proof of life, so the same short transaction also
    # persists a heartbeat — letting the API report worker_healthy from a fact rather
    # than an env var.
    with pilot.pg_connection() as connection:
        record_heartbeat(connection, worker_name=lease_owner)
        try:
            enqueued = _enqueue_due_assets(connection, config=cfg)
            connection.commit()
        except Exception:
            logger.exception('event=asset_risk_enqueue_failed')

    for _ in range(int(cfg['batch_size'])):
        claimed: dict[str, Any] | None = None
        with pilot.pg_connection() as connection:
            claimed = _claim_next_job(connection, lease_owner=lease_owner, lease_seconds=int(cfg['job_lease_seconds']))
            if claimed is None:
                connection.commit()
                break
            job_id = str(claimed['id'])
            workspace_id = str(claimed['workspace_id'])
            asset_id = str(claimed['asset_id'])
            trigger_source = str(claimed['trigger_source'] or 'worker')
            try:
                asset_row = connection.execute(
                    'SELECT * FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
                    (asset_id, workspace_id),
                ).fetchone()
                if asset_row is None:
                    connection.execute(
                        "UPDATE asset_risk_jobs SET status = 'cancelled', completed_at = NOW(), lease_owner = NULL, updated_at = NOW() WHERE id = %s",
                        (job_id,),
                    )
                    connection.commit()
                    continue
                service.log_assessment_event(
                    'asset_assessment_started', workspace_id=workspace_id, asset_id=asset_id,
                    job_id=job_id, execution_mode='background', worker_healthy=True,
                )
                outcome = service.assess_asset(
                    connection, workspace_id=workspace_id, asset_row=dict(asset_row),
                    config=cfg, trigger_source=trigger_source,
                )
                connection.execute(
                    "UPDATE asset_risk_jobs SET status = 'completed', completed_at = NOW(), heartbeat_at = NOW(), lease_owner = NULL, last_error = NULL, failure_code = NULL, updated_at = NOW() WHERE id = %s",
                    (job_id,),
                )
                connection.commit()
                processed += 1
                result_status = str(outcome.get('status') or 'completed')
                completion_event = 'asset_assessment_partial' if result_status in ('partial', 'degraded') else 'asset_assessment_completed'
                service.log_assessment_event(
                    completion_event, workspace_id=workspace_id, asset_id=asset_id,
                    assessment_id=outcome.get('assessment_id'), job_id=job_id, execution_mode='background',
                    result=result_status, risk_score=outcome.get('risk_score'), risk_level=outcome.get('risk_level'),
                    findings=outcome.get('findings_count'),
                )
            except Exception as exc:  # noqa: BLE001 - one asset must not stop the batch
                failed += 1
                attempts = int(claimed.get('attempts') or 1)
                max_attempts = int(claimed.get('max_attempts') or cfg['max_attempts'])
                terminal = attempts >= max_attempts
                next_status = 'failed' if terminal else 'queued'
                failure_code = 'assessment_error' if terminal else None
                try:
                    connection.execute(
                        'UPDATE asset_risk_jobs SET status = %s, last_error = %s, failure_code = %s, lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW() WHERE id = %s',
                        (next_status, str(exc)[:500], failure_code, job_id),
                    )
                    connection.commit()
                except Exception:
                    logger.exception('event=asset_risk_job_status_update_failed job_id=%s', job_id)
                service.log_assessment_event(
                    'asset_assessment_failed', workspace_id=workspace_id, asset_id=asset_id, job_id=job_id,
                    execution_mode='background', attempts=attempts, terminal=terminal,
                    failure_code=(failure_code or 'retrying'), error=type(exc).__name__,
                )

    return {'enqueued': enqueued, 'processed': processed, 'failed': failed}


def resolve_startup_state(config: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    cfg = config or arc.assessor_config()
    if not cfg['enabled']:
        return 'disabled', []
    errors = arc.blocking_configuration_errors(cfg)
    if errors:
        return 'configuration_error', errors
    return 'enabled', []
