"""Contract lock: migration 0118 == worker_status detected_by classification.

Migration 0118 (0118_backfill_wallet_transfer_detected_by.sql) stamps
payload_json.detected_by onto wallet-transfer telemetry rows persisted before
the payload stamps existed, using pure SQL that mirrors
worker_status.classify_wallet_transfer_detected_by /
_canonical_detected_by_or_none. If the app's canonical value table or tier
order changes, the migration would stamp rows differently than the API
classifies them — these tests lock the two in sync (update both together).

Also locks the migration's truthfulness guards:
  * live wallet rows only (never simulator/replay),
  * rows still missing detected_by only (idempotent),
  * a real tx_hash required (retention-purged rows untouched),
  * provenance marker detected_by_source='backfill_migration_0118',
  * payload_hash recomputed for the new payload,
  * no realtime tag invented — bare rows resolve to stable_rpc_polling,
  * foreign writers left untouched (still render explicit Unknown).
"""
from __future__ import annotations

import re
from pathlib import Path

from services.api.app.worker_status import (
    classify_wallet_transfer_detected_by,
    resolve_telemetry_detected_by,
    DETECTED_BY_BASIS_PROVIDER_TYPE,
    DETECTED_BY_BASIS_STABLE_INFERENCE,
    REALTIME_DETECTED_BY,
    STABLE_DETECTED_BY,
    STABLE_PROVIDER_TYPES,
)

MIGRATION = Path(__file__).resolve().parents[1] / 'migrations' / '0118_backfill_wallet_transfer_detected_by.sql'


def _sql() -> str:
    return MIGRATION.read_text(encoding='utf-8')


def test_migration_file_exists_and_is_discovered_after_0117():
    assert MIGRATION.is_file(), f'missing migration file: {MIGRATION}'
    versions = sorted(p.name for p in MIGRATION.parent.glob('*.sql'))
    assert MIGRATION.name in versions
    # 0118 must sort immediately after the 0117 migration. A NEW migration always
    # goes in a higher-numbered file (0119+), so this stays true as the tree grows,
    # while still failing if someone renumbers or removes 0118. Do NOT edit 0118's
    # contents once shipped — add a follow-up migration instead.
    prior = sorted(p.name for p in MIGRATION.parent.glob('0117_*.sql'))
    assert prior, 'expected a 0117_* migration to precede 0118'
    assert versions.index(MIGRATION.name) == versions.index(prior[-1]) + 1, (
        'expected 0118 to be discovered immediately after 0117; add a follow-up '
        'migration (0119+) instead of editing 0118 once it has shipped'
    )


def test_migration_canonical_value_table_matches_worker_status():
    sql = _sql()
    # Realtime tags map to themselves — every member of the app constant must
    # appear verbatim in the SQL IN(...) list.
    for tag in REALTIME_DETECTED_BY:
        assert f"'{tag}'" in sql, f'realtime tag {tag} missing from migration mapping'
    # tx_hash_import alias resolves to the canonical import tag.
    assert "v = 'tx_hash_import'" in sql
    assert "RETURN 'realtime_tx_import'" in sql
    # Stable ingestion spellings collapse to stable_rpc_polling.
    for src in ('polling', 'rpc_polling', 'evm_rpc', 'rpc_backfill', 'stable_rpc_polling'):
        assert f"'{src}'" in sql, f'stable ingestion source {src} missing from migration mapping'
    assert f"RETURN '{STABLE_DETECTED_BY}'" in sql


def test_migration_stable_provider_case_matches_worker_status():
    """The stable-family provider_type CASE mirrors STABLE_PROVIDER_TYPES:
    'evm_rpc' resolves through the canonical function; the remaining names plus
    blank/NULL resolve through the CASE inference arm."""
    sql = _sql()
    case_arm = re.search(r"CASE\s+WHEN lower\(btrim\(coalesce\(provider_type, ''\)\)\) IN\s*\(([^)]*)\)", sql)
    assert case_arm, 'stable-family provider_type CASE arm missing'
    arm_values = {v.strip().strip("'") for v in case_arm.group(1).split(',')}
    expected = {p for p in STABLE_PROVIDER_TYPES if p != 'evm_rpc'} | {''}
    assert arm_values == expected, f'CASE arm {arm_values} != app constant {expected}'
    assert "'evm_rpc'" in sql  # covered by the canonical mapping function


def test_migration_truthfulness_guards_present():
    sql = _sql()
    assert "event_type IN ('wallet_transfer_detected', 'native_transfer')" in sql
    assert "evidence_source = 'live'" in sql
    assert "COALESCE(payload_json->>'detected_by', '') = ''" in sql
    assert "COALESCE(payload_json->>'tx_hash', payload_json->>'hash') IS NOT NULL" in sql
    assert "'backfill_migration_0118'" in sql
    assert "payload_hash = encode(digest(np.new_payload::text, 'sha256'), 'hex')" in sql
    assert 'DROP FUNCTION IF EXISTS _decoda_canonical_detected_by_0118(text);' in sql
    # The resolution reads every payload fact tier the app reads.
    for fact in (
        "payload_json->>'detected_by'",
        "payload_json->'details'->>'detected_by'",
        "payload_json->'metadata'->>'detected_by'",
        "payload_json->>'source_type'",
        "payload_json->'details'->>'source_type'",
        "payload_json->'metadata'->>'source_type'",
        "payload_json->>'ingestion_source'",
        "payload_json->>'ingestion_method'",
    ):
        assert fact in sql, f'payload fact {fact} missing from migration resolution'


def test_python_classifier_agrees_with_migration_examples():
    """Representative rows: the value the migration stamps must equal what the
    API classifier returns for the same row, so backfilled rows and read-time
    classification can never disagree."""
    # The production row: bare payload, stable-poller provider.
    detected, basis = classify_wallet_transfer_detected_by(
        payload={'tx_hash': '0xef5324', 'block_number': 48150235},
        provider_type='evm_activity_provider',
        event_type='wallet_transfer_detected',
        evidence_source='live',
    )
    assert detected == 'stable_rpc_polling'
    assert basis == DETECTED_BY_BASIS_PROVIDER_TYPE

    # details.detected_by carries a realtime tag.
    assert resolve_telemetry_detected_by(
        {'details': {'detected_by': 'realtime_backfill'}}
    ) == 'realtime_backfill'

    # tx-import spelling maps to the canonical import tag.
    assert resolve_telemetry_detected_by({'source_type': 'tx_hash_import'}) == 'realtime_tx_import'

    # realtime provider_type column maps to itself.
    detected, _ = classify_wallet_transfer_detected_by(
        payload={'tx_hash': '0x1'}, provider_type='realtime_websocket',
        event_type='wallet_transfer_detected', evidence_source='live',
    )
    assert detected == 'realtime_websocket'

    # bare row, no provider at all: stable inference — never a realtime claim.
    detected, basis = classify_wallet_transfer_detected_by(
        payload={'tx_hash': '0x1'}, provider_type=None,
        event_type='wallet_transfer_detected', evidence_source='live',
    )
    assert detected == 'stable_rpc_polling'
    assert basis == DETECTED_BY_BASIS_STABLE_INFERENCE

    # foreign writer: untouched by the migration AND unclassified by the API.
    detected, _ = classify_wallet_transfer_detected_by(
        payload={'tx_hash': '0x1'}, provider_type='guided_workflow',
        event_type='wallet_transfer_detected', evidence_source='live',
    )
    assert detected is None
