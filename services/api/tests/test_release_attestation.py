from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.release_attestation import (
    AttestationError,
    REQUIRED_PROBES,
    REQUIRED_SECURITY_GATES,
    build_attestation,
    immutable_output_dir,
    sign_attestation,
    validate_evidence,
    verify_signature,
)

SHA = "a" * 40
DEPLOYMENT_ID = "deploy-123"
CI_RUN_ID = "98765"


def evidence(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    observed = now.isoformat().replace("+00:00", "Z")
    details = {
        "deployed_commit_sha": {"sha": SHA},
        "database_migrations": {"pending_count": 0, "applied_head": "0102_release.sql"},
        "worker_heartbeat": {"healthy": True},
        "event_bus_durability": {"backend": "redis-streams", "persistence_enabled": True, "round_trip_verified": True},
        "tenant_isolation": {"cross_tenant_access_denied": True, "tenants_tested": 2},
        "provider_freshness": {"all_fresh": True, "oldest_age_seconds": 12, "maximum_age_seconds": 60},
        "alert_latency": {"p95_ms": 900, "maximum_p95_ms": 2000},
        "backup_restoration": {"restored": True, "integrity_verified": True},
        "failover": {"completed": True, "rto_seconds": 45, "target_rto_seconds": 120},
        "notification_delivery": {"delivered": True, "receipt_id": "receipt-1", "channels": ["email"]},
    }
    return {
        "schema_version": 1,
        "strict_mode": True,
        "environment": "staging",
        "commit_sha": SHA,
        "deployment_id": DEPLOYMENT_ID,
        "ci_run_id": CI_RUN_ID,
        "collected_at": observed,
        "probes": {name: {"status": "pass", "evidence_id": f"probe-{name}", "observed_at": observed, "details": details[name]} for name in REQUIRED_PROBES},
        "security_gates": {name: {"status": "pass", "evidence_id": f"gate-{name}"} for name in REQUIRED_SECURITY_GATES},
        "safe_to_sell_broadly_today": True,
    }


def validate(value: dict, now: datetime | None = None) -> None:
    validate_evidence(value, expected_sha=SHA, deployment_id=DEPLOYMENT_ID, ci_run_id=CI_RUN_ID, now=now)


def test_strict_complete_evidence_builds_commit_bound_attestation() -> None:
    source = evidence()
    validate(source)
    attestation = build_attestation(source)
    assert attestation["commit_sha"] == SHA
    assert attestation["deployment_id"] == DEPLOYMENT_ID
    assert attestation["ci_run_id"] == CI_RUN_ID
    assert attestation["strict_mode"] is True
    assert attestation["safe_to_sell_broadly_today"] is True
    assert set(attestation["runtime_probes"]) == REQUIRED_PROBES
    assert set(attestation["security_gates"]) == REQUIRED_SECURITY_GATES


@pytest.mark.parametrize("field,value", [("strict_mode", False), ("environment", "ci"), ("safe_to_sell_broadly_today", False)])
def test_non_strict_or_non_runtime_evidence_cannot_emit_broad_sale_claim(field: str, value: object) -> None:
    source = evidence()
    source[field] = value
    with pytest.raises(AttestationError):
        validate(source)


def test_rejects_mismatched_deployed_commit() -> None:
    source = evidence()
    source["probes"]["deployed_commit_sha"]["details"]["sha"] = "b" * 40
    with pytest.raises(AttestationError, match="runtime deployed SHA"):
        validate(source)


@pytest.mark.parametrize("missing", sorted(REQUIRED_PROBES))
def test_rejects_every_missing_runtime_probe(missing: str) -> None:
    source = evidence()
    del source["probes"][missing]
    with pytest.raises(AttestationError, match="missing required runtime probes"):
        validate(source)


@pytest.mark.parametrize("missing", sorted(REQUIRED_SECURITY_GATES))
def test_rejects_every_missing_security_gate(missing: str) -> None:
    source = evidence()
    del source["security_gates"][missing]
    with pytest.raises(AttestationError, match="missing required security gates"):
        validate(source)


def test_rejects_stale_evidence() -> None:
    now = datetime.now(timezone.utc)
    source = evidence(now - timedelta(hours=2))
    with pytest.raises(AttestationError, match="stale evidence"):
        validate(source, now=now)


def test_ed25519_signature_detects_tampering() -> None:
    source = evidence()
    validate(source)
    attestation = build_attestation(source)
    key = base64.b64encode(bytes(range(32))).decode()
    signature = sign_attestation(attestation, key)
    verify_signature(attestation, signature)
    tampered = copy.deepcopy(attestation)
    tampered["deployment_id"] = "different"
    with pytest.raises(AttestationError, match="digest mismatch|signature verification failed"):
        verify_signature(tampered, signature)


def test_immutable_path_is_commit_and_deployment_bound(tmp_path: Path) -> None:
    assert immutable_output_dir(tmp_path, SHA, DEPLOYMENT_ID) == tmp_path / "artifacts" / "release-attestations" / SHA / DEPLOYMENT_ID


def test_non_strict_builder_refuses_to_emit_safe_to_sell_claim() -> None:
    source = evidence()
    source["strict_mode"] = False
    with pytest.raises(AttestationError, match="non-strict"):
        build_attestation(source)
