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
