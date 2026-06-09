from __future__ import annotations

import base64
import copy
import json
from argparse import Namespace
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
    verify,
    verify_signature,
)

SHA = "a" * 40
DEPLOYMENT_ID = "deploy-123"
CI_RUN_ID = "98765"
IMAGE_REF = "ghcr.io/example/decoda@sha256:" + "b" * 64
PENTEST_EVIDENCE = "SEC-2026"


def evidence(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    observed = now.isoformat().replace("+00:00", "Z")
    details = {
        "deployed_commit_sha": {"sha": SHA},
        "deployed_image_digest": {"image_ref": IMAGE_REF},
        "database_migrations": {"pending_count": 0, "applied_head": "0102_release.sql"},
        "worker_heartbeat": {"healthy": True},
        "event_bus_durability": {
            "backend": "redis-streams",
            "persistence_enabled": True,
            "round_trip_verified": True,
        },
        "tenant_isolation": {"cross_tenant_access_denied": True, "tenants_tested": 2},
        "provider_freshness": {
            "mode": "live",
            "providers_verified": 1,
            "all_fresh": True,
            "oldest_age_seconds": 12,
            "maximum_age_seconds": 60,
        },
        "billing_readiness": {
            "provider_mode": "live",
            "transaction_verified": True,
            "webhook_verified": True,
            "receipt_id": "billing-1",
        },
        "alert_latency": {"p95_ms": 900, "maximum_p95_ms": 2000},
        "backup_restoration": {"restored": True, "integrity_verified": True},
        "failover": {"completed": True, "rto_seconds": 45, "target_rto_seconds": 120},
        "notification_delivery": {
            "delivered": True,
            "receipt_id": "receipt-1",
            "channels": ["email"],
            "provider_receipt_verified": True,
        },
    }
    return {
        "schema_version": 2,
        "strict_mode": True,
        "environment": "production",
        "commit_sha": SHA,
        "deployment_id": DEPLOYMENT_ID,
        "ci_run_id": CI_RUN_ID,
        "image_ref": IMAGE_REF,
        "collected_at": observed,
        "probes": {
            name: {
                "status": "pass",
                "evidence_id": f"probe-{name}",
                "observed_at": observed,
                "details": details[name],
            }
            for name in REQUIRED_PROBES
        },
        "security_gates": {
            name: {"status": "pass", "evidence_id": f"gate-{name}"}
            for name in REQUIRED_SECURITY_GATES
        },
        "safe_to_sell_broadly_today": True,
    }


def validate(value: dict, now: datetime | None = None) -> None:
    validate_evidence(
        value,
        expected_sha=SHA,
        deployment_id=DEPLOYMENT_ID,
        ci_run_id=CI_RUN_ID,
        image_ref=IMAGE_REF,
        now=now,
    )


def test_strict_complete_evidence_builds_commit_bound_attestation() -> None:
    source = evidence()
    validate(source)
    attestation = build_attestation(source, penetration_test_evidence=PENTEST_EVIDENCE)
    assert attestation["commit_sha"] == SHA
    assert attestation["deployment_id"] == DEPLOYMENT_ID
    assert attestation["image_ref"] == IMAGE_REF
    assert attestation["penetration_test_evidence"] == PENTEST_EVIDENCE
    assert attestation["ci_run_id"] == CI_RUN_ID
    assert attestation["strict_mode"] is True
    assert attestation["safe_to_sell_broadly_today"] is True
    assert set(attestation["runtime_probes"]) == REQUIRED_PROBES
    assert set(attestation["security_gates"]) == REQUIRED_SECURITY_GATES


@pytest.mark.parametrize(
    "field,value",
    [
        ("strict_mode", False),
        ("environment", "staging"),
        ("safe_to_sell_broadly_today", False),
    ],
)
def test_non_strict_or_non_runtime_evidence_cannot_emit_broad_sale_claim(
    field: str, value: object
) -> None:
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


def test_rejects_mismatched_deployed_image_digest() -> None:
    source = evidence()
    source["probes"]["deployed_image_digest"]["details"]["image_ref"] = (
        "ghcr.io/example/decoda@sha256:" + "c" * 64
    )
    with pytest.raises(AttestationError, match="runtime deployed image"):
        validate(source)


def test_rejects_non_live_billing_provider() -> None:
    source = evidence()
    source["probes"]["billing_readiness"]["details"]["provider_mode"] = "test"
    with pytest.raises(AttestationError, match="billing provider must be live"):
        validate(source)


def test_rejects_email_without_verified_provider_receipt() -> None:
    source = evidence()
    source["probes"]["notification_delivery"]["details"][
        "provider_receipt_verified"
    ] = False
    with pytest.raises(AttestationError, match="email provider receipt"):
        validate(source)


def test_rejects_missing_independent_pentest_reference() -> None:
    source = evidence()
    validate(source)
    with pytest.raises(AttestationError, match="penetration-test evidence"):
        build_attestation(source, penetration_test_evidence="not-performed")


def test_ed25519_signature_detects_tampering() -> None:
    source = evidence()
    validate(source)
    attestation = build_attestation(source, penetration_test_evidence=PENTEST_EVIDENCE)
    key = base64.b64encode(bytes(range(32))).decode()
    signature = sign_attestation(attestation, key)
    verify_signature(attestation, signature)
    tampered = copy.deepcopy(attestation)
    tampered["deployment_id"] = "different"
    with pytest.raises(
        AttestationError, match="digest mismatch|signature verification failed"
    ):
        verify_signature(tampered, signature)


def test_verifier_rejects_attestation_for_a_different_image_digest(
    tmp_path: Path,
) -> None:
    source = evidence()
    validate(source)
    attestation = build_attestation(source, penetration_test_evidence=PENTEST_EVIDENCE)
    signature = sign_attestation(
        attestation, base64.b64encode(bytes(range(32))).decode()
    )
    directory = immutable_output_dir(tmp_path, SHA, DEPLOYMENT_ID)
    directory.mkdir(parents=True)
    (directory / "attestation.json").write_text(json.dumps(attestation))
    (directory / "signature.json").write_text(json.dumps(signature))

    assert (
        verify(
            Namespace(
                directory=str(directory),
                commit_sha=SHA,
                deployment_id=DEPLOYMENT_ID,
                image_ref=IMAGE_REF,
            )
        )
        == 0
    )
    with pytest.raises(AttestationError, match="attestation image"):
        verify(
            Namespace(
                directory=str(directory),
                commit_sha=SHA,
                deployment_id=DEPLOYMENT_ID,
                image_ref="ghcr.io/example/decoda@sha256:" + "c" * 64,
            )
        )


def test_immutable_path_is_commit_and_deployment_bound(tmp_path: Path) -> None:
    assert (
        immutable_output_dir(tmp_path, SHA, DEPLOYMENT_ID)
        == tmp_path / "artifacts" / "release-attestations" / SHA / DEPLOYMENT_ID
    )


def test_non_strict_builder_refuses_to_emit_safe_to_sell_claim() -> None:
    source = evidence()
    source["strict_mode"] = False
    with pytest.raises(AttestationError, match="non-strict"):
        build_attestation(source, penetration_test_evidence=PENTEST_EVIDENCE)
