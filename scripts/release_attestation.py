#!/usr/bin/env python3
"""Create and verify immutable, commit-bound production release attestations."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_REF_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
PENTEST_EVIDENCE_RE = re.compile(r"^(?:https://[^\s]+|[A-Z][A-Z0-9_-]*-[0-9]+)$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RELEASE_ENVIRONMENTS = {"production"}
REQUIRED_PROBES = {
    "deployed_commit_sha",
    "deployed_image_digest",
    "database_migrations",
    "worker_heartbeat",
    "event_bus_durability",
    "tenant_isolation",
    "provider_freshness",
    "billing_readiness",
    "alert_latency",
    "backup_restoration",
    "failover",
    "notification_delivery",
}
REQUIRED_SECURITY_GATES = {
    "dependency_scan",
    "secret_scan",
    "static_analysis",
    "infrastructure_policy",
}
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 3600


class AttestationError(ValueError):
    """Raised when release evidence is incomplete, stale, or contradictory."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AttestationError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttestationError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AttestationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AttestationError(message)


def _validate_identity(
    evidence: dict[str, Any],
    expected_sha: str,
    deployment_id: str,
    ci_run_id: str,
    image_ref: str,
) -> None:
    _require(evidence.get("strict_mode") is True, "strict_mode must be true")
    _require(
        evidence.get("environment") in RELEASE_ENVIRONMENTS,
        "evidence must come from the production environment",
    )
    _require(
        evidence.get("commit_sha") == expected_sha,
        "deployed commit SHA does not match the release commit",
    )
    _require(
        evidence.get("deployment_id") == deployment_id,
        "deployment_id does not match the release deployment",
    )
    _require(
        evidence.get("ci_run_id") == ci_run_id,
        "ci_run_id does not match the current CI run",
    )
    _require(
        evidence.get("image_ref") == image_ref,
        "deployed image digest does not match the release image",
    )


def _validate_freshness(
    evidence: dict[str, Any], now: datetime, max_age_seconds: int
) -> None:
    collected_at = _parse_time(evidence.get("collected_at"), "collected_at")
    age = (now - collected_at).total_seconds()
    _require(age >= -60, "evidence timestamp is in the future")
    _require(
        age <= max_age_seconds,
        f"stale evidence: {int(age)}s old (maximum {max_age_seconds}s)",
    )


def _validate_probe_freshness(
    name: str, probe: dict[str, Any], collected_at: datetime, max_age_seconds: int
) -> None:
    observed_at = _parse_time(probe.get("observed_at"), f"probes.{name}.observed_at")
    age = (collected_at - observed_at).total_seconds()
    _require(age >= -60, f"probes.{name} was observed after evidence collection")
    _require(age <= max_age_seconds, f"probes.{name} is stale ({int(age)}s old)")


def validate_evidence(
    evidence: dict[str, Any],
    *,
    expected_sha: str,
    deployment_id: str,
    ci_run_id: str,
    image_ref: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
) -> None:
    """Fail closed unless every runtime probe and security gate proves strict readiness."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _require(
        SHA_RE.fullmatch(expected_sha) is not None,
        "expected commit SHA must be 40 lowercase hex characters",
    )
    _require(
        ID_RE.fullmatch(deployment_id) is not None,
        "deployment_id contains unsupported characters",
    )
    _require(
        ID_RE.fullmatch(ci_run_id) is not None,
        "ci_run_id contains unsupported characters",
    )
    _require(
        IMAGE_REF_RE.fullmatch(image_ref) is not None,
        "image_ref must be an immutable sha256 digest reference",
    )
    _require(evidence.get("schema_version") == 2, "unsupported evidence schema_version")
    _validate_identity(evidence, expected_sha, deployment_id, ci_run_id, image_ref)
    _validate_freshness(evidence, now, max_age_seconds)
    collected_at = _parse_time(evidence.get("collected_at"), "collected_at")

    probes = evidence.get("probes")
    _require(isinstance(probes, dict), "probes must be an object")
    missing = sorted(REQUIRED_PROBES - set(probes))
    _require(not missing, "missing required runtime probes: " + ", ".join(missing))
    for name in sorted(REQUIRED_PROBES):
        probe = probes[name]
        _require(isinstance(probe, dict), f"probes.{name} must be an object")
        _require(probe.get("status") == "pass", f"probes.{name} did not pass")
        _require(
            isinstance(probe.get("evidence_id"), str) and probe["evidence_id"].strip(),
            f"probes.{name}.evidence_id is required",
        )
        _validate_probe_freshness(name, probe, collected_at, max_age_seconds)

    details = {name: probes[name].get("details") or {} for name in REQUIRED_PROBES}
    _require(
        details["deployed_commit_sha"].get("sha") == expected_sha,
        "runtime deployed SHA probe does not match expected commit",
    )
    _require(
        details["deployed_image_digest"].get("image_ref") == image_ref,
        "runtime deployed image probe does not match expected digest",
    )
    _require(
        details["database_migrations"].get("pending_count") == 0,
        "database migrations are pending",
    )
    _require(
        bool(details["database_migrations"].get("applied_head")),
        "database migration head is missing",
    )
    _require(
        details["worker_heartbeat"].get("healthy") is True,
        "worker heartbeat is unhealthy",
    )
    _require(
        details["event_bus_durability"].get("backend") in {"redis", "redis-streams"},
        "durable Redis event bus is required",
    )
    _require(
        details["event_bus_durability"].get("persistence_enabled") is True,
        "Redis persistence is not enabled",
    )
    _require(
        details["event_bus_durability"].get("round_trip_verified") is True,
        "event-bus durability round trip was not verified",
    )
    _require(
        details["tenant_isolation"].get("cross_tenant_access_denied") is True,
        "tenant-isolation denial probe failed",
    )
    _require(
        int(details["tenant_isolation"].get("tenants_tested", 0)) >= 2,
        "tenant-isolation probe must cover at least two tenants",
    )
    _require(
        details["provider_freshness"].get("mode") == "live",
        "provider freshness must use live providers",
    )
    _require(
        int(details["provider_freshness"].get("providers_verified", 0)) >= 1,
        "at least one live provider must be verified",
    )
    _require(
        details["provider_freshness"].get("all_fresh") is True,
        "one or more providers are stale",
    )
    _require(
        float(details["provider_freshness"].get("oldest_age_seconds", float("inf")))
        <= float(details["provider_freshness"].get("maximum_age_seconds", -1)),
        "provider freshness exceeds its threshold",
    )
    _require(
        details["billing_readiness"].get("provider_mode") == "live",
        "billing provider must be live",
    )
    _require(
        details["billing_readiness"].get("transaction_verified") is True,
        "live billing transaction was not verified",
    )
    _require(
        details["billing_readiness"].get("webhook_verified") is True,
        "billing webhook was not verified",
    )
    _require(
        bool(details["billing_readiness"].get("receipt_id")),
        "billing receipt is missing",
    )
    _require(
        float(details["alert_latency"].get("p95_ms", float("inf")))
        <= float(details["alert_latency"].get("maximum_p95_ms", -1)),
        "alert latency exceeds its threshold",
    )
    _require(
        details["backup_restoration"].get("restored") is True
        and details["backup_restoration"].get("integrity_verified") is True,
        "backup restoration was not proven",
    )
    _require(
        details["failover"].get("completed") is True, "failover drill did not complete"
    )
    _require(
        float(details["failover"].get("rto_seconds", float("inf")))
        <= float(details["failover"].get("target_rto_seconds", -1)),
        "failover RTO exceeds its target",
    )
    _require(
        details["notification_delivery"].get("delivered") is True,
        "notification delivery was not proven",
    )
    _require(
        bool(details["notification_delivery"].get("receipt_id")),
        "notification delivery receipt is missing",
    )
    _require(
        "email" in details["notification_delivery"].get("channels", []),
        "email delivery was not proven",
    )
    _require(
        details["notification_delivery"].get("provider_receipt_verified") is True,
        "email provider receipt was not verified",
    )

    gates = evidence.get("security_gates")
    _require(isinstance(gates, dict), "security_gates must be an object")
    missing_gates = sorted(REQUIRED_SECURITY_GATES - set(gates))
    _require(
        not missing_gates,
        "missing required security gates: " + ", ".join(missing_gates),
    )
    for name in sorted(REQUIRED_SECURITY_GATES):
        gate = gates[name]
        _require(
            isinstance(gate, dict) and gate.get("status") == "pass",
            f"security_gates.{name} did not pass",
        )
        _require(
            bool(gate.get("evidence_id")),
            f"security_gates.{name}.evidence_id is required",
        )

    _require(
        evidence.get("safe_to_sell_broadly_today") is True,
        "strict evidence must explicitly assert safe_to_sell_broadly_today",
    )


def build_attestation(
    evidence: dict[str, Any],
    *,
    penetration_test_evidence: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the unsigned canonical attestation; never emit broad-sale claims from non-strict input."""
    _require(
        evidence.get("strict_mode") is True,
        "cannot emit an attestation from non-strict evidence",
    )
    _require(
        evidence.get("environment") in RELEASE_ENVIRONMENTS,
        "cannot emit an attestation outside the production environment",
    )
    _require(
        evidence.get("safe_to_sell_broadly_today") is True,
        "cannot emit safe_to_sell_broadly_today from rejected evidence",
    )
    _require(
        PENTEST_EVIDENCE_RE.fullmatch(penetration_test_evidence) is not None,
        "approved independent penetration-test evidence URI or ticket is required",
    )
    generated_at = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": 2,
        "attestation_type": "strict-production-release",
        "commit_sha": evidence["commit_sha"],
        "deployment_id": evidence["deployment_id"],
        "image_ref": evidence["image_ref"],
        "penetration_test_evidence": penetration_test_evidence,
        "ci_run_id": evidence["ci_run_id"],
        "environment": evidence["environment"],
        "strict_mode": True,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "evidence_collected_at": evidence["collected_at"],
        "evidence_sha256": hashlib.sha256(_canonical_json(evidence)).hexdigest(),
        "runtime_probes": {
            name: evidence["probes"][name]["evidence_id"]
            for name in sorted(REQUIRED_PROBES)
        },
        "security_gates": {
            name: evidence["security_gates"][name]["evidence_id"]
            for name in sorted(REQUIRED_SECURITY_GATES)
        },
        "safe_to_sell_broadly_today": True,
    }


def _load_private_key(encoded: str):
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise AttestationError("signing key must be valid base64") from exc
    _require(
        len(raw) == 32, "signing key must encode exactly 32 Ed25519 private-key bytes"
    )
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(raw)


def sign_attestation(
    attestation: dict[str, Any], encoded_private_key: str
) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization

    key = _load_private_key(encoded_private_key)
    payload = _canonical_json(attestation)
    public_key = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        "algorithm": "Ed25519",
        "public_key": base64.b64encode(public_key).decode(),
        "signature": base64.b64encode(key.sign(payload)).decode(),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def verify_signature(attestation: dict[str, Any], signature: dict[str, Any]) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    _require(signature.get("algorithm") == "Ed25519", "unsupported signature algorithm")
    payload = _canonical_json(attestation)
    _require(
        signature.get("payload_sha256") == hashlib.sha256(payload).hexdigest(),
        "signed payload digest mismatch",
    )
    try:
        public_key = base64.b64decode(signature["public_key"], validate=True)
        signed = base64.b64decode(signature["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signed, payload)
    except Exception as exc:
        raise AttestationError("attestation signature verification failed") from exc


def immutable_output_dir(root: Path, commit_sha: str, deployment_id: str) -> Path:
    return root / "artifacts" / "release-attestations" / commit_sha / deployment_id


def create(args: argparse.Namespace) -> int:
    evidence = json.loads(Path(args.evidence).read_text())
    validate_evidence(
        evidence,
        expected_sha=args.commit_sha,
        deployment_id=args.deployment_id,
        ci_run_id=args.ci_run_id,
        image_ref=args.image_ref,
        max_age_seconds=args.max_age_seconds,
    )
    attestation = build_attestation(
        evidence, penetration_test_evidence=args.penetration_test_evidence
    )
    signature = sign_attestation(
        attestation,
        args.signing_key or os.getenv("RELEASE_ATTESTATION_SIGNING_KEY", ""),
    )
    output = immutable_output_dir(Path(args.root), args.commit_sha, args.deployment_id)
    _require(not output.exists(), f"immutable attestation already exists: {output}")
    output.mkdir(parents=True)
    (output / "attestation.json").write_text(
        json.dumps(attestation, indent=2, sort_keys=True) + "\n"
    )
    (output / "signature.json").write_text(
        json.dumps(signature, indent=2, sort_keys=True) + "\n"
    )
    print(output)
    return 0


def verify(args: argparse.Namespace) -> int:
    directory = Path(args.directory)
    attestation = json.loads((directory / "attestation.json").read_text())
    signature = json.loads((directory / "signature.json").read_text())
    verify_signature(attestation, signature)
    _require(attestation.get("strict_mode") is True, "attestation is not strict")
    _require(
        attestation.get("safe_to_sell_broadly_today") is True,
        "attestation is not broad-sale authority",
    )
    _require(
        attestation.get("commit_sha") == args.commit_sha,
        "attestation commit does not match expected commit",
    )
    _require(
        attestation.get("deployment_id") == args.deployment_id,
        "attestation deployment does not match expected deployment",
    )
    _require(
        attestation.get("image_ref") == args.image_ref,
        "attestation image does not match expected digest",
    )
    _require(
        IMAGE_REF_RE.fullmatch(args.image_ref) is not None,
        "image_ref must be an immutable sha256 digest reference",
    )
    _require(
        directory.name == attestation.get("deployment_id")
        and directory.parent.name == args.commit_sha,
        "attestation is not stored at its immutable commit/deployment path",
    )
    print("attestation verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--evidence", required=True)
    create_parser.add_argument("--commit-sha", required=True)
    create_parser.add_argument("--deployment-id", required=True)
    create_parser.add_argument("--ci-run-id", required=True)
    create_parser.add_argument("--image-ref", required=True)
    create_parser.add_argument("--penetration-test-evidence", required=True)
    create_parser.add_argument("--root", default=".")
    create_parser.add_argument(
        "--max-age-seconds", type=int, default=DEFAULT_MAX_EVIDENCE_AGE_SECONDS
    )
    create_parser.add_argument("--signing-key")
    create_parser.set_defaults(func=create)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--directory", required=True)
    verify_parser.add_argument("--commit-sha", required=True)
    verify_parser.add_argument("--deployment-id", required=True)
    verify_parser.add_argument("--image-ref", required=True)
    verify_parser.set_defaults(func=verify)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (AttestationError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(f"release attestation rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
