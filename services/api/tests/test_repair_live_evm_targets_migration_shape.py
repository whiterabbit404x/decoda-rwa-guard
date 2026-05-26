"""Shape tests for migration 0087_repair_live_evm_targets_for_telemetry.sql.

This migration is run against a real Postgres at deploy time, so we can't
exec it here without a DB.  Instead, verify shape invariants that prove the
migration is safe and fail-closed:

- It exists with the expected filename.
- It is idempotent: uses WHERE NOT EXISTS or ON CONFLICT DO NOTHING for
  every INSERT (no unconditional insert).
- It never inserts telemetry_events, detections, alerts, incidents.
- It never references guided_simulator / demo / mock / simulator markers.
- It never sets live_evidence_ready / live_telemetry_ready / 'Healthy'.
- It uses gen_random_uuid(), not SHA-cast-to-UUID.
- It includes a chain_id = 1 path (not just chain_network).
- It includes `chain` in the monitored_systems INSERT (NOT NULL column).
"""
from __future__ import annotations

from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / 'migrations'
    / '0087_repair_live_evm_targets_for_telemetry.sql'
)


@pytest.fixture(scope='module')
def sql() -> str:
    return MIGRATION_PATH.read_text(encoding='utf-8')


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f'Expected migration at {MIGRATION_PATH}'


def test_migration_uses_gen_random_uuid(sql: str):
    assert 'gen_random_uuid()' in sql, (
        'Migration must use gen_random_uuid(); SHA-cast-to-UUID is not allowed.'
    )


def test_migration_does_not_use_sha_uuid_cast(sql: str):
    lower = sql.lower()
    forbidden = ['md5(', 'sha256(', 'encode(digest(', 'decode(md5(']
    for token in forbidden:
        assert token not in lower, (
            f'Migration must not derive UUIDs from {token}; use gen_random_uuid().'
        )


def test_migration_inserts_are_idempotent(sql: str):
    lower = sql.lower()
    inserts = [seg for seg in lower.split('insert into') if seg.strip()]
    # The first element is the header before any INSERT — skip it.
    inserts = inserts[1:]
    for idx, body in enumerate(inserts):
        assert (
            'on conflict' in body
            or 'not exists' in body
        ), (
            f'INSERT #{idx + 1} must be idempotent (ON CONFLICT / NOT EXISTS).'
        )


def test_migration_never_inserts_telemetry_events(sql: str):
    lower = sql.lower()
    assert 'insert into telemetry_events' not in lower, (
        'Repair migration must NOT insert telemetry_events rows.'
    )


def test_migration_never_inserts_detections_alerts_incidents(sql: str):
    lower = sql.lower()
    for table in ('detections', 'alerts', 'incidents', 'response_actions',
                  'evidence', 'monitoring_event_receipts', 'provider_health_records'):
        assert f'insert into {table}' not in lower, (
            f'Repair migration must NOT insert into {table}.'
        )


def test_migration_no_simulator_or_demo_references(sql: str):
    """Strip comments and verify the executable SQL never assigns simulator/demo values."""
    lines = [
        line for line in sql.splitlines()
        if not line.lstrip().startswith('--')
    ]
    body = '\n'.join(lines).lower()
    for token in ('simulator', 'guided_simulator', 'demo_', 'mock', "'demo'"):
        assert token not in body, (
            f'Repair migration body must not reference {token!r}.'
        )


def test_migration_no_health_or_evidence_ready_flips(sql: str):
    """Strip comments and verify no SET statement flips evidence-ready flags."""
    lines = [
        line for line in sql.splitlines()
        if not line.lstrip().startswith('--')
    ]
    body = '\n'.join(lines).lower()
    for forbidden in (
        'live_evidence_ready',
        'live_telemetry_ready',
        "= 'healthy'",
    ):
        assert forbidden not in body, (
            f'Repair migration body must not flip {forbidden!r}.'
        )


def test_migration_handles_chain_id_one(sql: str):
    """Migration must repair targets that only persist chain_id=1 (no chain_network)."""
    lower = sql.lower()
    assert 'chain_id, 0) = 1' in lower or 'chain_id = 1' in lower, (
        'Migration must repair targets where chain_id=1 even when chain_network is null.'
    )


def test_monitored_systems_insert_includes_chain_column(sql: str):
    """monitored_systems.chain is NOT NULL — the INSERT must include it."""
    lower = sql.lower()
    # Locate the monitored_systems INSERT block
    ms_idx = lower.find('insert into monitored_systems')
    if ms_idx == -1:
        return  # Migration does not insert monitored_systems; OK
    block = lower[ms_idx:ms_idx + 1200]
    assert 'chain' in block.split('values')[0], (
        'monitored_systems INSERT must include the chain column.'
    )


def test_migration_sets_provider_type_evm_rpc(sql: str):
    lower = sql.lower()
    assert "provider_type = 'evm_rpc'" in lower, (
        "Migration must set provider_type='evm_rpc' for Ethereum mainnet targets."
    )


def test_migration_targets_only_enabled_targets(sql: str):
    lower = sql.lower()
    # Every UPDATE / INSERT that touches monitoring_configs/monitored_systems
    # must filter on enabled targets, not all targets.
    assert 'coalesce(t.enabled, false) = true' in lower, (
        'Repair migration must filter on enabled targets only.'
    )
    assert 'coalesce(t.monitoring_enabled, false) = true' in lower, (
        'Repair migration must filter on monitoring_enabled targets only.'
    )
