"""
Tests for the Base chain repair migration (0103).

The migration must:
- Exist at the expected path
- NOT reference updated_at on monitored_systems (that column does not exist)
- Update monitored_systems.chain only (no timestamp column)
- Update targets.chain_network AND targets.updated_at (that column exists)
- Filter by LOWER(COALESCE(...)) so alias variants are all caught
- Be idempotent: rows already set to 'base' are excluded by the WHERE clause
- Use deleted_at IS NULL guards on all tables
"""
from __future__ import annotations

import pathlib
import re

_MIGRATION_PATH = pathlib.Path(
    'services/api/migrations/0103_repair_base_monitored_systems_chain.sql'
)


def _content() -> str:
    return _MIGRATION_PATH.read_text()


def _upper() -> str:
    return _content().upper()


# ---------------------------------------------------------------------------
# Existence
# ---------------------------------------------------------------------------

def test_migration_file_exists():
    assert _MIGRATION_PATH.exists(), "Migration 0103 must exist"


# ---------------------------------------------------------------------------
# monitored_systems: must NOT set updated_at
# ---------------------------------------------------------------------------

def _split_statements(content: str) -> dict[str, str]:
    """Return a dict mapping table name (lower) -> full UPDATE statement text."""
    statements: dict[str, str] = {}
    # Split on UPDATE <table> boundaries (word boundary so 'updated_at' is not matched)
    for match in re.finditer(
        r'(?i)\bUPDATE\s+(\w+)\b(.*?)(?=\bUPDATE\s+\w+\b|\Z)',
        content,
        re.DOTALL,
    ):
        table = match.group(1).lower()
        statements[table] = match.group(0)
    return statements


def test_monitored_systems_update_does_not_set_updated_at():
    """monitored_systems has no updated_at column; the migration must not touch it."""
    statements = _split_statements(_content())
    assert 'monitored_systems' in statements, "Migration must contain UPDATE monitored_systems"
    ms_block = statements['monitored_systems']
    assert 'updated_at' not in ms_block.lower(), (
        "monitored_systems UPDATE must not reference updated_at — "
        "that column does not exist on the table"
    )


def test_monitored_systems_update_sets_chain():
    statements = _split_statements(_content())
    assert 'monitored_systems' in statements
    ms_block = statements['monitored_systems']
    assert 'chain' in ms_block.lower(), (
        "monitored_systems UPDATE must SET chain = 'base'"
    )


# ---------------------------------------------------------------------------
# targets: must set chain_network (and may set updated_at)
# ---------------------------------------------------------------------------

def test_targets_update_sets_chain_network():
    content = _upper()
    assert 'UPDATE TARGETS' in content, "Migration must UPDATE targets"
    assert 'CHAIN_NETWORK' in content, "Targets UPDATE must set chain_network"


def test_targets_update_sets_updated_at():
    """targets has updated_at; the migration is expected to stamp it."""
    statements = _split_statements(_content())
    assert 'targets' in statements, "Migration must contain UPDATE targets"
    targets_block = statements['targets']
    assert 'updated_at' in targets_block.lower(), (
        "targets UPDATE should set updated_at = NOW() — that column exists"
    )


# ---------------------------------------------------------------------------
# Correctness: aliases and soft-delete guards
# ---------------------------------------------------------------------------

def test_migration_normalises_ethereum_aliases():
    content = _upper()
    for alias in ('ETHEREUM-MAINNET', 'MAINNET', 'ETH-MAINNET', 'ETHEREUM', 'ETH'):
        assert alias in content, f"Migration must handle alias '{alias}'"


def test_migration_targets_base_assets():
    content = _upper()
    assert 'BASE' in content, "Migration must identify Base assets"
    assert 'BASE-MAINNET' in content, "Migration must handle 'base-mainnet' alias"


def test_migration_uses_deleted_at_guard():
    content = _upper()
    assert 'DELETED_AT IS NULL' in content, (
        "Migration must skip soft-deleted rows"
    )


# ---------------------------------------------------------------------------
# Idempotency: rows already correct are skipped
# ---------------------------------------------------------------------------

def test_migration_excludes_already_correct_rows():
    """The WHERE clause must restrict ms.chain to wrong alias values only,
    making the migration safe to re-run (already-'base' rows are excluded)."""
    statements = _split_statements(_upper())
    assert 'monitored_systems' in statements
    ms_where = statements['monitored_systems']
    assert 'ETHEREUM-MAINNET' in ms_where or 'ETH-MAINNET' in ms_where, (
        "monitored_systems WHERE must restrict ms.chain to wrong alias values"
    )
