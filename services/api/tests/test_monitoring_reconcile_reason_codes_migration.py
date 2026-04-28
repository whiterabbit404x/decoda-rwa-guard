from pathlib import Path


def test_reason_codes_migration_uses_function_based_check_constraint() -> None:
    migration = Path('services/api/migrations/0070_monitoring_reconcile_reason_codes.sql').read_text(encoding='utf-8')

    assert 'CREATE OR REPLACE FUNCTION monitoring_reason_codes_jsonb_is_valid(input jsonb)' in migration
    assert 'CHECK (monitoring_reason_codes_jsonb_is_valid(reason_codes));' in migration
    assert 'jsonb_array_elements(reason_codes)' not in migration
