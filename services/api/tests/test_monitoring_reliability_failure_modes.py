from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from services.api.app.monitoring_reliability import (
    MonitoringSLOs,
    evaluate_monitoring_slos,
    rpc_recovery_action,
    run_external_synthetic_check,
)


class _Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class SyntheticConnection:
    def __init__(self):
        self.stages: set[str] = set()
        self.status = "running"
        self.failure_stage = None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO monitoring_synthetic_stages"):
            self.stages.add(params[1])
        elif normalized.startswith("SELECT stage FROM monitoring_synthetic_stages"):
            return _Result(rows=[{"stage": stage} for stage in self.stages])
        elif normalized.startswith("UPDATE monitoring_synthetic_checks SET status=%s"):
            self.status, self.failure_stage = params[0], params[1]
        elif "SET status='failed'" in normalized:
            self.status, self.failure_stage = "failed", "execution"
        return _Result()


def _healthy_metrics(**updates):
    metrics = {
        "ingestion_age_seconds": 10,
        "detection_latency_seconds": 5,
        "provider_availability_percent": 100,
        "queue_depth": 0,
        "active_workers": 2,
        "oldest_required_worker_heartbeat_age_seconds": 10,
    }
    metrics.update(updates)
    return metrics


def test_worker_termination_is_visible_and_redundancy_is_required():
    result = evaluate_monitoring_slos(_healthy_metrics(active_workers=1))
    assert result["compliant"] is False
    assert result["checks"]["worker_heartbeat"]["compliant"] is False
    assert result["checks"]["worker_heartbeat"]["minimum_active_workers"] == 2


def test_delayed_telemetry_fails_ingestion_freshness():
    result = evaluate_monitoring_slos(_healthy_metrics(ingestion_age_seconds=121), MonitoringSLOs())
    assert result["failed"] == ["ingestion_freshness"]


def test_rpc_outage_resumes_from_finalized_checkpoint_and_deduplicates():
    action = rpc_recovery_action(checkpoint_block=1_000, observed_block=1_005, provider_failed=True)
    assert action == {
        "action": "failover_and_resume",
        "resume_block": 988,
        "invalidate_unfinalized": False,
        "deduplicate": True,
    }


def test_chain_reorganization_rewinds_beyond_reorg_and_finality_window():
    action = rpc_recovery_action(checkpoint_block=1_000, observed_block=1_004, reorg_depth=5, finality_blocks=12)
    assert action["action"] == "rewind_and_replay"
    assert action["resume_block"] == 988
    assert action["invalidate_unfinalized"] is True
    assert action["deduplicate"] is True


def test_duplicate_delivery_and_recovery_are_enforced_by_schema():
    migration = Path("services/api/migrations/0093_monitoring_reliability_slos.sql").read_text()
    assert "idempotency_key TEXT NOT NULL UNIQUE" in migration
    assert "lease_expires_at TIMESTAMPTZ" in migration
    assert "dead_letter" in migration
    assert "max_attempts BETWEEN 1 AND 20" in migration


def test_database_interruption_fails_slo_closed_without_claiming_success():
    result = evaluate_monitoring_slos({})
    assert result["compliant"] is False
    assert set(result["failed"]) == {
        "ingestion_freshness",
        "detection_latency",
        "provider_availability",
        "queue_depth",
        "worker_heartbeat",
    }


def test_external_synthetic_known_event_traverses_every_persisted_stage():
    connection = SyntheticConnection()

    def traverse(_check_id, mark):
        for stage in ("ingestion", "detection", "alerting", "incident_creation", "evidence_persistence"):
            mark(stage)
            mark(stage)  # duplicate delivery is idempotent

    result = run_external_synthetic_check(connection, traverser=traverse)
    assert result["status"] == "passed"
    assert result["missing_stages"] == []
    assert connection.status == "passed"


def test_external_synthetic_check_identifies_event_loss_stage():
    connection = SyntheticConnection()

    def traverse(_check_id, mark):
        for stage in ("ingestion", "detection", "alerting", "incident_creation"):
            mark(stage)

    result = run_external_synthetic_check(connection, traverser=traverse)
    assert result["status"] == "failed"
    assert result["missing_stages"] == ["evidence_persistence"]
    assert connection.failure_stage == "evidence_persistence"


def test_rpc_client_fails_over_without_losing_requested_call(monkeypatch):
    from services.api.app import evm_activity_provider

    calls: list[tuple[str, str]] = []

    class Client:
        def __init__(self, url):
            self.url = url

        def call(self, method, params):
            calls.append((self.url, method))
            if self.url == "https://primary.invalid":
                raise RuntimeError("primary unavailable")
            return "0x10"

    monkeypatch.setattr(evm_activity_provider, "JsonRpcClient", Client)
    client = evm_activity_provider.FailoverJsonRpcClient(["https://primary.invalid", "https://secondary.invalid"])
    assert client.call("eth_blockNumber", []) == "0x10"
    assert calls == [
        ("https://primary.invalid", "eth_blockNumber"),
        ("https://secondary.invalid", "eth_blockNumber"),
    ]
    assert client.active_index == 1
