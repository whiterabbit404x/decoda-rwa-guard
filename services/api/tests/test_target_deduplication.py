"""
Tests for monitoring target deduplication.

Rule: workspace_id + asset_id + name + target_type must be unique among
non-deleted targets. A second identical POST must return 409.
"""
from __future__ import annotations

import os
import pathlib

import pytest

_PILOT_SRC = pathlib.Path('services/api/app/pilot.py').read_text()


def _create_target_src() -> str:
    # Extract just the create_target function body
    start = _PILOT_SRC.index('def create_target(')
    # find the next top-level def after create_target
    next_def = _PILOT_SRC.find('\ndef ', start + 1)
    return _PILOT_SRC[start:next_def] if next_def != -1 else _PILOT_SRC[start:]


# ---------------------------------------------------------------------------
# Backend: deduplication query present in create_target
# ---------------------------------------------------------------------------

def test_create_target_checks_for_duplicate_before_insert():
    src = _create_target_src()
    assert 'duplicate_row' in src, "create_target must query for a duplicate before INSERT"


def test_create_target_raises_409_on_duplicate():
    src = _create_target_src()
    assert 'HTTP_409_CONFLICT' in src, "create_target must raise HTTP 409 when a duplicate is found"


def test_create_target_duplicate_query_uses_workspace_asset_name_type():
    src = _create_target_src()
    assert "workspace_id = %s AND asset_id = %s AND name = %s AND target_type = %s" in src, (
        "duplicate check must filter on workspace_id, asset_id, name, and target_type"
    )


def test_create_target_duplicate_query_excludes_deleted():
    src = _create_target_src()
    assert "deleted_at IS NULL" in src


def test_create_target_409_detail_names_the_target():
    src = _create_target_src()
    assert 'already exists for this asset' in src, (
        "409 detail must clearly state the target already exists"
    )


def test_create_target_guard_runs_before_insert():
    src = _create_target_src()
    dup_pos = src.index('duplicate_row')
    insert_pos = src.index('INSERT INTO targets')
    assert dup_pos < insert_pos, "Duplicate check must appear before the INSERT statement"


# ---------------------------------------------------------------------------
# Migration: unique index definition exists
# ---------------------------------------------------------------------------

def test_migration_unique_index_exists():
    migration_path = pathlib.Path('services/api/migrations/0101_targets_unique_name_per_asset.sql')
    assert migration_path.exists(), "Migration 0101 for target uniqueness must exist"
    content = migration_path.read_text()
    assert 'CREATE UNIQUE INDEX' in content
    assert 'workspace_id' in content
    assert 'asset_id' in content
    assert 'name' in content
    assert 'target_type' in content
    assert 'deleted_at IS NULL' in content


def _migration_0101_content() -> str:
    return pathlib.Path('services/api/migrations/0101_targets_unique_name_per_asset.sql').read_text()


# ---------------------------------------------------------------------------
# Migration 0101: duplicate-cleanup phase runs BEFORE index creation
# ---------------------------------------------------------------------------

def test_migration_0101_soft_deletes_duplicates_not_hard_deletes():
    content = _migration_0101_content().upper()
    assert 'UPDATE TARGETS' in content, "0101 must UPDATE (soft-delete) duplicates, not DELETE them"
    assert 'DELETE FROM TARGETS' not in content, "0101 must not hard-delete target rows"


def test_migration_0101_sets_deleted_at_for_duplicates():
    content = _migration_0101_content()
    assert 'deleted_at' in content, "0101 must set deleted_at on duplicate rows so they are excluded from the partial index"
    assert 'NOW()' in content, "0101 must set deleted_at = NOW() (not a constant)"


def test_migration_0101_cleanup_uses_row_number():
    content = _migration_0101_content().upper()
    assert 'ROW_NUMBER()' in content, "0101 must use ROW_NUMBER() to rank duplicates"


def test_migration_0101_cleanup_partitions_by_correct_columns():
    content = _migration_0101_content().upper()
    assert 'PARTITION BY WORKSPACE_ID' in content
    assert 'ASSET_ID' in content


def test_migration_0101_cleanup_keeps_oldest_row():
    content = _migration_0101_content().upper()
    assert 'ORDER BY CREATED_AT ASC' in content, "0101 must keep the oldest row (ORDER BY created_at ASC, rn=1)"


def test_migration_0101_cleanup_targets_rn_greater_than_one():
    content = _migration_0101_content().upper()
    assert 'RN > 1' in content, "0101 must UPDATE only duplicate rows where rn > 1"


def test_migration_0101_cleanup_runs_before_index_creation():
    content = _migration_0101_content()
    update_pos = content.upper().index('UPDATE TARGETS')
    index_pos = content.upper().index('CREATE UNIQUE INDEX')
    assert update_pos < index_pos, "Duplicate cleanup UPDATE must appear before CREATE UNIQUE INDEX"


def test_migration_0101_idempotent_guard_on_update():
    content = _migration_0101_content()
    # The UPDATE must guard against re-processing already-soft-deleted rows
    update_section = content[content.upper().index('UPDATE TARGETS'):]
    assert 'deleted_at IS NULL' in update_section, (
        "UPDATE in 0101 must include AND deleted_at IS NULL so re-running is safe"
    )


def test_migration_0101_has_verification_block():
    content = _migration_0101_content().upper()
    assert 'DO $$' in content or 'DO $' in content, "0101 must include a DO $$ verification block"
    assert 'RAISE EXCEPTION' in content, "0101 verification block must RAISE EXCEPTION if duplicates remain"
