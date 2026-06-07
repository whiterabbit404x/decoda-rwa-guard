"""Durable monitoring delivery, SLO evaluation, and synthetic-path checks.

The helpers in this module intentionally keep synthetic control-plane records in
separate tables.  They never manufacture workspace telemetry or evidence and
therefore cannot make live monitoring appear healthy.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MonitoringSLOs:
    ingestion_freshness_seconds: int = 120
    detection_latency_seconds: int = 60
    provider_availability_percent: float = 99.0
    max_queue_depth: int = 1000
    worker_heartbeat_seconds: int = 90
    minimum_active_workers: int = 2
    synthetic_completion_seconds: int = 180

    @classmethod
    def from_env(cls) -> "MonitoringSLOs":
        return cls(
            ingestion_freshness_seconds=_env_int("MONITORING_SLO_INGESTION_FRESHNESS_SECONDS", 120),
            detection_latency_seconds=_env_int("MONITORING_SLO_DETECTION_LATENCY_SECONDS", 60),
            provider_availability_percent=float(os.getenv("MONITORING_SLO_PROVIDER_AVAILABILITY_PERCENT", "99.0")),
            max_queue_depth=_env_int("MONITORING_SLO_MAX_QUEUE_DEPTH", 1000),
            worker_heartbeat_seconds=_env_int("MONITORING_SLO_WORKER_HEARTBEAT_SECONDS", 90),
            minimum_active_workers=_env_int("MONITORING_SLO_MINIMUM_ACTIVE_WORKERS", 2),
            synthetic_completion_seconds=_env_int("MONITORING_SLO_SYNTHETIC_COMPLETION_SECONDS", 180),
        )

    def public_dict(self) -> dict[str, int | float]:
        return {
            "ingestion_freshness_seconds": self.ingestion_freshness_seconds,
            "detection_latency_seconds": self.detection_latency_seconds,
            "provider_availability_percent": self.provider_availability_percent,
            "max_queue_depth": self.max_queue_depth,
            "worker_heartbeat_seconds": self.worker_heartbeat_seconds,
            "minimum_active_workers": self.minimum_active_workers,
            "synthetic_completion_seconds": self.synthetic_completion_seconds,
        }


def _measure(name: str, actual: float | int | None, target: float | int, compliant: bool, unit: str) -> dict[str, Any]:
    return {"name": name, "actual": actual, "target": target, "unit": unit, "compliant": bool(compliant)}


def evaluate_monitoring_slos(metrics: Mapping[str, Any], slos: MonitoringSLOs | None = None) -> dict[str, Any]:
    """Evaluate measurable SLOs. Missing measurements fail closed."""
    slos = slos or MonitoringSLOs.from_env()
    ingestion_age = metrics.get("ingestion_age_seconds")
    detection_latency = metrics.get("detection_latency_seconds")
    provider_availability = metrics.get("provider_availability_percent")
    queue_depth = metrics.get("queue_depth")
    active_workers = metrics.get("active_workers")
    heartbeat_age = metrics.get("oldest_required_worker_heartbeat_age_seconds")
    checks = {
        "ingestion_freshness": _measure("ingestion_freshness", ingestion_age, slos.ingestion_freshness_seconds, ingestion_age is not None and float(ingestion_age) <= slos.ingestion_freshness_seconds, "seconds"),
        "detection_latency": _measure("detection_latency", detection_latency, slos.detection_latency_seconds, detection_latency is not None and float(detection_latency) <= slos.detection_latency_seconds, "seconds"),
        "provider_availability": _measure("provider_availability", provider_availability, slos.provider_availability_percent, provider_availability is not None and float(provider_availability) >= slos.provider_availability_percent, "percent"),
        "queue_depth": _measure("queue_depth", queue_depth, slos.max_queue_depth, queue_depth is not None and int(queue_depth) <= slos.max_queue_depth, "jobs"),
        "worker_heartbeat": {
            **_measure("worker_heartbeat", heartbeat_age, slos.worker_heartbeat_seconds, heartbeat_age is not None and float(heartbeat_age) <= slos.worker_heartbeat_seconds and int(active_workers or 0) >= slos.minimum_active_workers, "seconds"),
            "active_workers": int(active_workers or 0),
            "minimum_active_workers": slos.minimum_active_workers,
        },
    }
    failed = [name for name, check in checks.items() if not check["compliant"]]
    return {"compliant": not failed, "failed": failed, "objectives": slos.public_dict(), "checks": checks}


def monitoring_slo_snapshot(connection: Any, *, now: datetime | None = None, slos: MonitoringSLOs | None = None) -> dict[str, Any]:
    """Read aggregate runtime measurements without returning provider URLs or payloads."""
    now = now or datetime.now(timezone.utc)
    slos = slos or MonitoringSLOs.from_env()
    row = connection.execute(
        """
        SELECT
          EXTRACT(EPOCH FROM (NOW() - MAX(te.ingested_at))) AS ingestion_age_seconds,
          MAX(EXTRACT(EPOCH FROM (de.created_at - te.observed_at))) FILTER (WHERE de.created_at >= NOW() - INTERVAL '15 minutes') AS detection_latency_seconds
        FROM telemetry_events te
        LEFT JOIN detection_events de ON de.telemetry_event_id = te.id
        WHERE te.evidence_source = 'live'
        """
    ).fetchone() or {}
    provider = connection.execute(
        """
        SELECT COALESCE(100.0 * COUNT(*) FILTER (WHERE status IN ('ok','healthy','active')) / NULLIF(COUNT(*), 0), 0) AS availability
        FROM provider_health_records WHERE checked_at >= NOW() - INTERVAL '15 minutes'
        """
    ).fetchone() or {}
    jobs = connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM monitoring_delivery_jobs WHERE status IN ('queued','leased') AND available_at <= NOW())
          + (SELECT COUNT(*) FROM targets WHERE monitoring_enabled=TRUE AND enabled=TRUE AND is_active=TRUE
             AND monitoring_dead_lettered_at IS NULL
             AND (last_checked_at IS NULL OR last_checked_at < NOW() - (GREATEST(monitoring_interval_seconds, 30) * INTERVAL '1 second'))) AS depth,
          (SELECT COUNT(*) FROM monitoring_delivery_jobs WHERE status='dead_letter')
          + (SELECT COUNT(*) FROM targets WHERE monitoring_dead_lettered_at IS NOT NULL) AS dead_letters
        """
    ).fetchone() or {}
    workers = connection.execute(
        """
        SELECT COUNT(*) FILTER (WHERE last_heartbeat_at >= NOW() - (%s * INTERVAL '1 second')) AS active_workers,
               EXTRACT(EPOCH FROM (NOW() - MIN(last_heartbeat_at) FILTER (WHERE last_heartbeat_at >= NOW() - (%s * INTERVAL '1 second')))) AS heartbeat_age
        FROM monitoring_worker_state
        """,
        (slos.worker_heartbeat_seconds, slos.worker_heartbeat_seconds),
    ).fetchone() or {}
    synthetic = connection.execute(
        """SELECT status, failure_stage, started_at, completed_at
        FROM monitoring_synthetic_checks ORDER BY started_at DESC LIMIT 1"""
    ).fetchone()
    metrics = {
        "ingestion_age_seconds": _number(row, "ingestion_age_seconds"),
        "detection_latency_seconds": _number(row, "detection_latency_seconds"),
        "provider_availability_percent": _number(provider, "availability"),
        "queue_depth": int(_number(jobs, "depth") or 0),
        "dead_letter_count": int(_number(jobs, "dead_letters") or 0),
        "active_workers": int(_number(workers, "active_workers") or 0),
        "oldest_required_worker_heartbeat_age_seconds": _number(workers, "heartbeat_age"),
        "measured_at": now.isoformat().replace("+00:00", "Z"),
    }
    snapshot = {**evaluate_monitoring_slos(metrics, slos), "metrics": metrics}
    snapshot["synthetic_check"] = (
        {
            "status": synthetic.get("status"),
            "failure_stage": synthetic.get("failure_stage"),
            "started_at": _iso(synthetic.get("started_at")),
            "completed_at": _iso(synthetic.get("completed_at")),
        }
        if isinstance(synthetic, Mapping)
        else {"status": "never_run", "failure_stage": None, "started_at": None, "completed_at": None}
    )
    snapshot["fault_tolerance"] = {
        "durable_queue": "postgresql",
        "redis_required_for_delivery": False,
        "expired_leases_recovered": True,
        "bounded_retries": True,
        "dead_letter_queue": True,
        "minimum_worker_replicas": slos.minimum_active_workers,
    }
    return snapshot


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value) if value else None


def _number(row: Any, key: str) -> float | None:
    value = row.get(key) if isinstance(row, Mapping) else None
    return float(value) if value is not None else None


def enqueue_monitoring_job(connection: Any, *, job_type: str, idempotency_key: str, payload: Mapping[str, Any], max_attempts: int = 5) -> str:
    job_id = str(uuid.uuid4())
    row = connection.execute(
        """
        INSERT INTO monitoring_delivery_jobs (id, job_type, idempotency_key, payload, max_attempts)
        VALUES (%s::uuid, %s, %s, %s::jsonb, %s)
        ON CONFLICT (idempotency_key) DO UPDATE SET updated_at = NOW()
        RETURNING id
        """,
        (job_id, job_type, idempotency_key, _json(payload), max(1, min(int(max_attempts), 20))),
    ).fetchone()
    return str(row["id"] if isinstance(row, Mapping) else row[0])


def lease_monitoring_jobs(connection: Any, *, worker_id: str, limit: int = 20, lease_seconds: int = 60) -> list[dict[str, Any]]:
    """Atomically recover expired leases and claim work using SKIP LOCKED."""
    rows = connection.execute(
        """
        WITH candidates AS (
          SELECT id FROM monitoring_delivery_jobs
          WHERE ((status = 'queued' AND available_at <= NOW()) OR (status = 'leased' AND lease_expires_at <= NOW()))
            AND attempts < max_attempts
          ORDER BY available_at, created_at
          FOR UPDATE SKIP LOCKED LIMIT %s
        )
        UPDATE monitoring_delivery_jobs j
        SET status='leased', leased_by=%s, leased_at=NOW(), lease_expires_at=NOW() + (%s * INTERVAL '1 second'),
            attempts=j.attempts + 1, updated_at=NOW()
        FROM candidates c WHERE j.id=c.id
        RETURNING j.*
        """,
        (max(1, min(int(limit), 200)), worker_id, max(10, int(lease_seconds))),
    ).fetchall()
    return [dict(row) for row in rows]


def complete_monitoring_job(connection: Any, *, job_id: str, worker_id: str) -> bool:
    row = connection.execute(
        """UPDATE monitoring_delivery_jobs SET status='succeeded', completed_at=NOW(), lease_expires_at=NULL, updated_at=NOW()
        WHERE id=%s::uuid AND status='leased' AND leased_by=%s RETURNING id""",
        (job_id, worker_id),
    ).fetchone()
    return row is not None


def fail_monitoring_job(connection: Any, *, job_id: str, worker_id: str, error_code: str, retry_base_seconds: int = 5) -> str:
    """Retry with bounded exponential backoff, then atomically dead-letter."""
    row = connection.execute(
        """
        UPDATE monitoring_delivery_jobs SET
          status=CASE WHEN attempts >= max_attempts THEN 'dead_letter' ELSE 'queued' END,
          available_at=CASE WHEN attempts >= max_attempts THEN available_at ELSE NOW() + (LEAST(300, %s * POWER(2, GREATEST(attempts-1,0))) * INTERVAL '1 second') END,
          dead_lettered_at=CASE WHEN attempts >= max_attempts THEN NOW() ELSE NULL END,
          leased_by=NULL, leased_at=NULL, lease_expires_at=NULL, last_error_code=%s, updated_at=NOW()
        WHERE id=%s::uuid AND status='leased' AND leased_by=%s RETURNING status
        """,
        (max(1, int(retry_base_seconds)), str(error_code)[:120], job_id, worker_id),
    ).fetchone()
    return str((row or {}).get("status") if isinstance(row, Mapping) else row[0] if row else "not_owned")


def rpc_recovery_action(*, checkpoint_block: int | None, observed_block: int | None, reorg_depth: int = 0, provider_failed: bool = False, finality_blocks: int = 12) -> dict[str, Any]:
    """Define explicit checkpoint behavior for reorg and provider failover."""
    checkpoint = max(0, int(checkpoint_block or 0))
    observed = max(0, int(observed_block or checkpoint))
    if reorg_depth > 0:
        rewind_to = max(0, min(checkpoint, observed) - max(int(reorg_depth), int(finality_blocks)))
        return {"action": "rewind_and_replay", "resume_block": rewind_to, "invalidate_unfinalized": True, "deduplicate": True}
    if provider_failed:
        return {"action": "failover_and_resume", "resume_block": max(0, checkpoint - int(finality_blocks)), "invalidate_unfinalized": False, "deduplicate": True}
    return {"action": "continue", "resume_block": checkpoint, "invalidate_unfinalized": False, "deduplicate": True}


def run_external_synthetic_check(connection: Any, *, traverser: Callable[[str, Callable[[str], None]], None], timeout_seconds: int | None = None) -> dict[str, Any]:
    """Run an isolated known-event canary through all persisted pipeline stages."""
    check_id = str(uuid.uuid4())
    required = ("ingestion", "detection", "alerting", "incident_creation", "evidence_persistence")
    connection.execute("INSERT INTO monitoring_synthetic_checks (id, status, started_at) VALUES (%s::uuid, 'running', NOW())", (check_id,))

    def mark(stage: str) -> None:
        if stage not in required:
            raise ValueError(f"unknown synthetic stage: {stage}")
        connection.execute(
            """INSERT INTO monitoring_synthetic_stages (check_id, stage, completed_at) VALUES (%s::uuid, %s, NOW())
            ON CONFLICT (check_id, stage) DO NOTHING""",
            (check_id, stage),
        )

    try:
        traverser(check_id, mark)
        rows = connection.execute("SELECT stage FROM monitoring_synthetic_stages WHERE check_id=%s::uuid", (check_id,)).fetchall()
        completed = {str(row["stage"] if isinstance(row, Mapping) else row[0]) for row in rows}
        missing = [stage for stage in required if stage not in completed]
        status = "passed" if not missing else "failed"
        connection.execute(
            "UPDATE monitoring_synthetic_checks SET status=%s, completed_at=NOW(), failure_stage=%s WHERE id=%s::uuid",
            (status, missing[0] if missing else None, check_id),
        )
        return {"check_id": check_id, "status": status, "completed_stages": sorted(completed), "missing_stages": missing, "timeout_seconds": timeout_seconds or MonitoringSLOs.from_env().synthetic_completion_seconds}
    except Exception:
        connection.execute("UPDATE monitoring_synthetic_checks SET status='failed', completed_at=NOW(), failure_stage='execution' WHERE id=%s::uuid", (check_id,))
        raise


def _json(payload: Mapping[str, Any]) -> str:
    import json
    return json.dumps(dict(payload), separators=(",", ":"), sort_keys=True, default=str)
