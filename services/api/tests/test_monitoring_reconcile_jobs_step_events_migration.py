from pathlib import Path


def test_reconcile_jobs_step_events_migration_adds_transition_guardrails() -> None:
    migration = Path('services/api/migrations/0071_monitoring_reconcile_jobs_step_events.sql').read_text(encoding='utf-8')

    assert 'ADD COLUMN IF NOT EXISTS transition_version INTEGER NOT NULL DEFAULT 0;' in migration
    assert 'ADD COLUMN IF NOT EXISTS step_name TEXT;' in migration
    assert 'CREATE OR REPLACE FUNCTION monitoring_reconcile_status_transition_allowed(previous_status text, next_status text)' in migration
    assert 'CREATE OR REPLACE FUNCTION enforce_monitoring_reconcile_run_transition()' in migration
    assert 'CREATE TRIGGER trg_monitoring_reconcile_run_transition' in migration
