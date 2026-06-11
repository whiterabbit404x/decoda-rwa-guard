"""
Tests for the duplicate-active-target cleanup migration (0102).

The migration must:
- Exist at the expected path
- UPDATE targets (not DELETE)
- Set enabled, monitoring_enabled, and is_active to FALSE for duplicates
- Use ROW_NUMBER partitioned by workspace_id, asset_id, name, target_type
- Keep the oldest row (ORDER BY created_at ASC) by disabling rows where rn > 1
- Preserve rows where deleted_at IS NOT NULL (i.e. only process non-deleted)
"""
from __future__ import annotations

import pathlib

_MIGRATION_PATH = pathlib.Path('services/api/migrations/0102_disable_duplicate_active_targets.sql')


def _content() -> str:
    return _MIGRATION_PATH.read_text()


def test_migration_file_exists():
    assert _MIGRATION_PATH.exists(), "Migration 0102 must exist"


def test_migration_uses_update_not_delete():
    content = _content().upper()
    assert 'UPDATE TARGETS' in content, "Migration must UPDATE targets rows, not DELETE them"
    assert 'DELETE FROM TARGETS' not in content, "Migration must not DELETE target rows"


def test_migration_disables_enabled():
    content = _content()
    assert 'enabled' in content and 'FALSE' in content.upper(), (
        "Migration must set enabled = FALSE"
    )


def test_migration_disables_monitoring_enabled():
    content = _content()
    assert 'monitoring_enabled' in content, "Migration must set monitoring_enabled = FALSE"


def test_migration_disables_is_active():
    content = _content()
    assert 'is_active' in content, "Migration must set is_active = FALSE"


def test_migration_uses_row_number():
    content = _content().upper()
    assert 'ROW_NUMBER()' in content, "Migration must use ROW_NUMBER() to identify duplicates"


def test_migration_partitions_by_correct_columns():
    content = _content().upper()
    assert 'PARTITION BY WORKSPACE_ID' in content
    assert 'ASSET_ID' in content
    assert 'NAME' in content
    assert 'TARGET_TYPE' in content


def test_migration_keeps_oldest_by_created_at():
    content = _content().upper()
    assert 'ORDER BY CREATED_AT ASC' in content, (
        "Migration must order by created_at ASC so the oldest row (rn=1) is kept"
    )


def test_migration_disables_rn_greater_than_one():
    content = _content().upper()
    assert 'RN > 1' in content, "Migration must disable rows where rn > 1 (keeping rn=1 oldest)"


def test_migration_only_targets_non_deleted_rows():
    content = _content().upper()
    assert 'DELETED_AT IS NULL' in content, (
        "Migration must skip soft-deleted rows (WHERE deleted_at IS NULL)"
    )


def test_migration_touches_active_rows_only():
    content = _content().upper()
    assert ('ENABLED = TRUE' in content or 'IS_ACTIVE = TRUE' in content or 'MONITORING_ENABLED = TRUE' in content), (
        "Migration must only consider rows that are currently active/enabled"
    )
