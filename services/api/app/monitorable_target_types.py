from __future__ import annotations

from typing import Any

MONITORABLE_TARGET_TYPES: tuple[str, ...] = ('wallet', 'contract')


def normalize_target_type(value: Any) -> str:
    return str(value or '').strip().lower()


def is_monitorable_target_type(value: Any) -> bool:
    return normalize_target_type(value) in MONITORABLE_TARGET_TYPES


def monitorable_target_types_sql_clause(column: str = 'target_type') -> str:
    allowed = ', '.join(f"'{target_type}'" for target_type in MONITORABLE_TARGET_TYPES)
    return f"LOWER(COALESCE({column}, '')) IN ({allowed})"
