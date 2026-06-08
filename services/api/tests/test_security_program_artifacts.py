from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_migration_validator():
    path = REPO_ROOT / "scripts" / "security" / "validate_migrations.py"
    spec = importlib.util.spec_from_file_location("validate_migrations", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_security_package_covers_required_program_areas() -> None:
    security_dir = REPO_ROOT / "docs" / "security"
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(security_dir.glob("*.md"))
    ).lower()
    required_terms = (
        "access control",
        "change management",
        "vulnerability management",
        "secure development",
        "logging",
        "incident response",
        "availability",
        "backup recovery",
        "vendor management",
        "data retention",
        "cryptographic key management",
        "breach notification",
        "penetration-test",
        "soc 2 type ii",
        "subprocessors",
        "data residency",
        "threat model",
        "software bill of materials",
    )
    for term in required_terms:
        assert term in combined


def test_migration_history_matches_locked_legacy_baseline() -> None:
    validator = _load_migration_validator()
    errors = validator.validate_migrations(
        REPO_ROOT / "services" / "api" / "migrations",
        REPO_ROOT / "scripts" / "security" / "migration_baseline.json",
    )
    assert errors == []


def test_migration_baseline_is_explicitly_non_extensible() -> None:
    baseline_path = REPO_ROOT / "scripts" / "security" / "migration_baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert "New anomalies fail validation" in baseline["description"]
    assert baseline["missing_versions"] == [13]
