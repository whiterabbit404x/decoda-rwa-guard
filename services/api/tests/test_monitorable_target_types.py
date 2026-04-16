from services.api.app.monitorable_target_types import (
    is_monitorable_target_type,
    monitorable_target_types_sql_clause,
)


def test_is_monitorable_target_type_normalizes_whitespace_and_case() -> None:
    assert is_monitorable_target_type(' Wallet ') is True
    assert is_monitorable_target_type(' CONTRACT\t') is True


def test_monitorable_target_types_sql_clause_trims_legacy_target_values() -> None:
    clause = monitorable_target_types_sql_clause('t.target_type')
    assert "LOWER(BTRIM(COALESCE(t.target_type, '')))" in clause
