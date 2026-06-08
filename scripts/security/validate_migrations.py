#!/usr/bin/env python3
"""Validate SQL migrations and prevent changes to locked legacy numbering anomalies."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

MIGRATION_NAME = re.compile(r"^(?P<version>\d{4})_[a-z0-9][a-z0-9_]*\.sql$")
FORBIDDEN_TRANSACTION_CONTROL = re.compile(
    r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", re.IGNORECASE | re.MULTILINE
)


def validate_migrations(migrations_dir: Path, baseline_path: Path) -> list[str]:
    errors: list[str] = []
    migrations = sorted(migrations_dir.glob("*.sql"))
    if not migrations:
        return [f"no SQL migrations found in {migrations_dir}"]

    versions: list[int] = []
    for migration in migrations:
        match = MIGRATION_NAME.fullmatch(migration.name)
        if not match:
            errors.append(
                f"{migration.name}: expected NNNN_lowercase_description.sql naming"
            )
            continue
        versions.append(int(match.group("version")))
        content = migration.read_text(encoding="utf-8")
        if not content.strip():
            errors.append(f"{migration.name}: migration is empty")
        if "\x00" in content:
            errors.append(f"{migration.name}: contains a NUL byte")
        if FORBIDDEN_TRANSACTION_CONTROL.search(content):
            errors.append(
                f"{migration.name}: transaction control is owned by the migration runner"
            )

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected_duplicates = baseline["duplicate_versions"]
    expected_missing = baseline["missing_versions"]
    duplicates = sorted(version for version, count in Counter(versions).items() if count > 1)
    if duplicates != expected_duplicates:
        errors.append(
            "migration duplicate-version set changed: "
            f"expected legacy {expected_duplicates}, found {duplicates}"
        )

    if versions:
        expected = set(range(min(versions), max(versions) + 1))
        missing = sorted(expected.difference(versions))
        if missing != expected_missing:
            errors.append(
                "migration missing-version set changed: "
                f"expected legacy {expected_missing}, found {missing}"
            )
        if min(versions) != 1:
            errors.append(f"migration history must start at 0001, found {min(versions):04d}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=Path("services/api/migrations"),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("scripts/security/migration_baseline.json"),
    )
    args = parser.parse_args()
    errors = validate_migrations(args.migrations_dir, args.baseline)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    count = len(list(args.migrations_dir.glob("*.sql")))
    print(f"Validated {count} ordered SQL migrations in {args.migrations_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
