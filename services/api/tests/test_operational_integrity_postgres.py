"""Operational Integrity against a REAL PostgreSQL (opt-in; integration).

The fake-connection suites cover the decision logic. This one covers what only a
real database can prove:

  * migration 0146 applies on top of the full migration history, is idempotent,
    and its ON CONFLICT targets resolve against constraints that actually exist,
  * a uint256-range amount survives the NUMERIC(78, 0) round trip with every
    digit intact (a float would silently corrupt a reconciliation value),
  * repeated telemetry for one transaction updates ONE row, even when the
    verdict changes as business records arrive,
  * the deterministic outcomes hold end to end through real SQL:
      authorized mint      -> no detection,
      unauthorized mint    -> UNMATCHED_ISSUANCE (critical),
      missing source       -> indeterminate, still no detection,
      settlement overdue   -> SETTLEMENT_TIMEOUT,
  * coverage reports the lane that actually delivered the telemetry.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_operational_integrity_postgres.py

Skipped entirely when that DSN is absent, so the default suite stays hermetic.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

_DSN = os.environ.get('DECODA_MIGRATION_TEST_DSN')
_PSQL = shutil.which('psql')


def _real_psycopg():
    """The actual driver, not the conftest stub.

    conftest installs a lightweight ``psycopg`` stub so the hermetic suites can
    import the app without the driver present. This harness talks to a real
    database, so it swaps the stub out first. Only reached on an opt-in
    integration run (a DSN is set), and the real driver is a superset of the
    stub's surface, so nothing else in the session loses anything.
    """
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

_needs_pg = pytest.mark.skipif(
    not (_DSN and _PSQL),
    reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have '
           'psql on PATH to run the operational-integrity real-schema harness',
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
HUGE = '123456789012345678901234567890123456789'  # 39 digits — well past float64


def _apply_all_migrations() -> None:
    for path in sorted(_MIGRATIONS.glob('*.sql')):
        proc = subprocess.run(
            [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', '-f', str(path)],
            capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, f'{path.name} failed:\n{proc.stdout}\n{proc.stderr}'


@pytest.fixture(scope='module')
def db():
    from psycopg.rows import dict_row

    _apply_all_migrations()
    with psycopg.connect(_DSN, row_factory=dict_row, autocommit=True) as conn:
        yield conn


def _seed_workspace(conn, label: str) -> tuple[str, str]:
    """A workspace + asset, mirrored into asset_registry.

    telemetry_events.asset_id FKs to asset_registry(id) while assets is the
    product table; the monitoring runner writes the SAME id into both (see
    migration 0089), so the seed does too."""
    uid, ws, asset = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, password_hash, full_name) VALUES (%s, %s, 'x', 'Test Operator')",
        (uid, f'{uid[:8]}@example.test'),
    )
    conn.execute(
        'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) VALUES (%s, %s, %s, %s, NOW())',
        (ws, label, f'{label}-{ws[:6]}', uid),
    )
    conn.execute(
        '''INSERT INTO assets (id, workspace_id, name, asset_type, chain_network, identifier,
                               created_by_user_id, updated_by_user_id, created_at)
           VALUES (%s, %s, %s, 'tokenized_bond', 'base-mainnet', %s, %s, %s, NOW())''',
        (asset, ws, 'US Treasury Bond #013', '0x' + 'ee' * 20, uid, uid),
    )
    conn.execute(
        '''INSERT INTO asset_registry (id, workspace_id, type, address_or_identifier, chain, created_at)
           VALUES (%s, %s, 'tokenized_rwa', %s, 'base-mainnet', NOW())''',
        (asset, ws, '0x' + 'ee' * 20),
    )
    return ws, asset


def _seed_mint(conn, ws: str, asset: str, tx: str, amount: str = '5000000') -> None:
    conn.execute(
        '''INSERT INTO telemetry_events (id, workspace_id, asset_id, provider_type, event_type,
                                         observed_at, evidence_source, payload_json)
           VALUES (%s, %s, %s, 'evm_rpc', 'erc20_transfer', %s, 'live', %s::jsonb)''',
        (str(uuid.uuid4()), ws, asset, NOW - timedelta(minutes=5), json.dumps({
            'tx_hash': tx, 'from': '0x' + '00' * 20, 'to': '0x' + 'cd' * 20,
            'amount': amount, 'token_decimals': 0, 'token_symbol': 'USTB',
            'block_number': 21_000_000, 'chain_id': 8453,
        })),
    )


def _seed_authoritative(conn, ws: str, asset: str) -> None:
    conn.execute(
        '''INSERT INTO asset_authoritative_state (id, workspace_id, asset_id, expected_total_supply,
                                                  source_name, source_kind, source_status,
                                                  evidence_source, observed_at, created_at)
           VALUES (%s, %s, %s, %s, 'Acme Transfer Agent', 'transfer_agent', 'reported', 'live', %s, NOW())''',
        (str(uuid.uuid4()), ws, asset, Decimal('4500000'), NOW - timedelta(minutes=1)),
    )


def _seed_authorization(conn, ws: str, asset: str, *, amount: str = '5000000',
                        settlement: str = 'settled', authorized_at=None) -> None:
    conn.execute(
        '''INSERT INTO asset_authorized_issuances (id, workspace_id, asset_id, operation, amount,
                                                   settlement_state, source_name, evidence_source,
                                                   authorized_at, created_at, updated_at)
           VALUES (%s, %s, %s, 'mint', %s, %s, 'Acme Transfer Agent', 'live', %s, NOW(), NOW())''',
        (str(uuid.uuid4()), ws, asset, Decimal(amount), settlement,
         authorized_at or (NOW - timedelta(minutes=10))),
    )


def _count(conn, ws: str) -> int:
    return int(conn.execute(
        'SELECT COUNT(*) AS n FROM threat_detections WHERE workspace_id = %s', (ws,)
    ).fetchone()['n'])


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
@pytest.mark.integration
@_needs_pg
def test_migration_0146_adds_the_columns_indexes_and_keeps_the_conflict_targets(db):
    columns = {
        r['column_name']: r for r in db.execute(
            "SELECT column_name, data_type, numeric_precision FROM information_schema.columns "
            "WHERE table_name = 'threat_detections'"
        ).fetchall()
    }
    for name in (
        'category', 'deterministic_reason_code', 'operational_checks', 'matcher_version',
        'observed_amount', 'expected_amount', 'variance_amount', 'amount_decimals', 'amount_unit',
        'operation', 'tx_hash', 'block_number', 'telemetry_source', 'telemetry_stage',
        'telemetry_observed_at', 'preconfirmation_received_at', 'provenance',
    ):
        assert name in columns, name
    # Amounts are exact base units, never a float column.
    for name in ('observed_amount', 'expected_amount', 'variance_amount'):
        assert columns[name]['data_type'] == 'numeric'
        assert columns[name]['numeric_precision'] == 78

    indexes = {r['indexname'] for r in db.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'threat_detections'"
    ).fetchall()}
    assert 'idx_threat_detections_workspace_category_detected' in indexes
    assert 'idx_threat_detections_workspace_tx_hash' in indexes

    # Every ON CONFLICT target the writer uses must be backed by a real constraint.
    constraints = {
        r['def'] for r in db.execute(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid IN ('threat_detections'::regclass, 'threat_detection_evidence'::regclass) "
            "AND contype = 'u'"
        ).fetchall()
    }
    assert 'UNIQUE (workspace_id, cluster_key)' in constraints
    assert 'UNIQUE (detection_id, dedupe_key)' in constraints


@pytest.mark.integration
@_needs_pg
def test_migration_0146_is_idempotent(db):
    # Depends on `db` so the full migration history is already applied — the
    # point is re-running 0146 on a schema that already has it.
    proc = subprocess.run(
        [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', '-f',
         str(_MIGRATIONS / '0146_operational_integrity_detections.sql')],
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr


# --------------------------------------------------------------------------
# The deterministic outcomes, through real SQL
# --------------------------------------------------------------------------
@pytest.mark.integration
@_needs_pg
def test_an_authorized_mint_produces_no_detection(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'authorized')
    _seed_mint(db, ws, asset, '0x' + 'a1' * 32)
    _seed_authoritative(db, ws, asset)
    _seed_authorization(db, ws, asset)

    stats = service.evaluate_workspace(db, workspace_id=ws, config=oic.engine_config(), now=NOW)
    assert stats['events_evaluated'] == 1
    assert stats['authorized'] == 1
    assert _count(db, ws) == 0


@pytest.mark.integration
@_needs_pg
def test_a_valid_mint_with_no_authorization_is_a_critical_unmatched_issuance(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'unauthorized')
    _seed_mint(db, ws, asset, '0x' + 'b1' * 32)
    _seed_authoritative(db, ws, asset)

    stats = service.evaluate_workspace(db, workspace_id=ws, config=oic.engine_config(), now=NOW)
    assert (stats['anomalies'], stats['created']) == (1, 1)

    row = db.execute('SELECT * FROM threat_detections WHERE workspace_id = %s', (ws,)).fetchone()
    assert row['detection_type'] == 'unmatched_issuance'
    assert row['category'] == 'OPERATIONAL_INTEGRITY'
    assert row['severity'] == 'critical'
    assert row['deterministic_reason_code'] == 'NO_MATCHING_AUTHORIZED_ISSUANCE'
    assert str(row['observed_amount']) == '5000000'
    assert str(row['expected_amount']) == '0'
    assert str(row['variance_amount']) == '5000000'
    # The transaction was accepted on-chain; only the BUSINESS checks failed.
    assert row['operational_checks']['signer_validity']['status'] == 'PASS'
    assert row['operational_checks']['on_chain_event']['status'] == 'PASS'
    assert row['operational_checks']['transfer_agent_match']['status'] == 'FAIL'
    assert row['operational_checks']['settlement_match']['status'] == 'FAIL'
    # RPC polling delivers finalized blocks — never a preconfirmation claim.
    assert row['telemetry_source'] == 'rpc_polling'
    assert row['telemetry_stage'] == 'FINALIZED'
    assert row['preconfirmation_received_at'] is None


@pytest.mark.integration
@_needs_pg
def test_reprocessing_the_same_transaction_never_duplicates_the_detection(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'idempotent')
    _seed_mint(db, ws, asset, '0x' + 'c1' * 32)
    _seed_authoritative(db, ws, asset)
    cfg = oic.engine_config()

    first = service.evaluate_workspace(db, workspace_id=ws, config=cfg, now=NOW)
    second = service.evaluate_workspace(db, workspace_id=ws, config=cfg, now=NOW)
    assert first['created'] == 1
    assert second['created'] == 0 and second['updated'] == 1
    assert _count(db, ws) == 1

    # A verdict that CHANGES for the same transaction corrects the row rather
    # than forking a contradictory second one.
    detection_id = db.execute(
        'SELECT id FROM threat_detections WHERE workspace_id = %s', (ws,)
    ).fetchone()['id']
    _seed_authorization(db, ws, asset, settlement='pending')
    service.evaluate_workspace(db, workspace_id=ws, config=cfg, now=NOW)
    assert _count(db, ws) == 1
    corrected = db.execute('SELECT * FROM threat_detections WHERE workspace_id = %s', (ws,)).fetchone()
    assert corrected['id'] == detection_id
    assert corrected['deterministic_reason_code'] == 'SETTLEMENT_NOT_COMPLETE'


@pytest.mark.integration
@_needs_pg
def test_a_uint256_amount_survives_the_numeric_round_trip(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'uint256')
    _seed_mint(db, ws, asset, '0x' + 'd1' * 32, amount=HUGE)
    _seed_authoritative(db, ws, asset)

    service.evaluate_workspace(db, workspace_id=ws, config=oic.engine_config(), now=NOW)
    row = db.execute('SELECT * FROM threat_detections WHERE workspace_id = %s', (ws,)).fetchone()
    # Every digit intact. Through a float this value loses its low 20+ digits.
    assert str(row['observed_amount']) == HUGE
    assert str(row['variance_amount']) == HUGE


@pytest.mark.integration
@_needs_pg
def test_a_missing_authoritative_source_stores_nothing_rather_than_a_false_anomaly(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'nosource')
    _seed_mint(db, ws, asset, '0x' + 'e1' * 32)  # no asset_authoritative_state row

    stats = service.evaluate_workspace(db, workspace_id=ws, config=oic.engine_config(), now=NOW)
    assert stats['indeterminate'] == 1
    assert stats['anomalies'] == 0
    assert _count(db, ws) == 0


@pytest.mark.integration
@_needs_pg
def test_an_overdue_settlement_raises_a_settlement_timeout(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'settlement')
    _seed_authoritative(db, ws, asset)
    _seed_authorization(db, ws, asset, settlement='pending', authorized_at=NOW - timedelta(days=5))

    stats = service.evaluate_workspace(db, workspace_id=ws, config=oic.engine_config(), now=NOW)
    assert stats['settlement_timeouts'] == 1
    row = db.execute('SELECT * FROM threat_detections WHERE workspace_id = %s', (ws,)).fetchone()
    assert row['detection_type'] == 'settlement_timeout'
    assert row['deterministic_reason_code'] == 'SETTLEMENT_DEADLINE_EXCEEDED'


@pytest.mark.integration
@_needs_pg
def test_coverage_reports_the_lane_that_actually_delivered_the_telemetry(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'coverage')
    _seed_mint(db, ws, asset, '0x' + 'f1' * 32)
    _seed_authoritative(db, ws, asset)

    coverage = service.telemetry_coverage(db, workspace_id=ws, now=NOW, config=oic.engine_config())
    assert coverage['state'] == service.COVERAGE_LIVE
    assert coverage['telemetry_source'] == 'rpc_polling'
    assert coverage['telemetry_stage'] == 'FINALIZED'
    assert coverage['preconfirmation_available'] is False
    assert coverage['reasons'] == []


@pytest.mark.integration
@_needs_pg
def test_a_workspace_without_an_authoritative_source_is_never_reported_as_covered(db):
    from services.api.app.domains.operational_integrity import config as oic, service

    ws, asset = _seed_workspace(db, 'partial')
    _seed_mint(db, ws, asset, '0x' + '02' * 32)

    coverage = service.telemetry_coverage(db, workspace_id=ws, now=NOW, config=oic.engine_config())
    assert coverage['state'] == service.COVERAGE_DEGRADED
    assert 'no_authoritative_source' in coverage['reasons']


@pytest.mark.integration
@_needs_pg
def test_the_category_filter_narrows_real_stored_records(db):
    from services.api.app.domains.operational_integrity import config as oic, service
    from services.api.app.domains.threat_detection import config as tdc

    ws, asset = _seed_workspace(db, 'category')
    _seed_mint(db, ws, asset, '0x' + '03' * 32)
    _seed_authoritative(db, ws, asset)
    service.evaluate_workspace(db, workspace_id=ws, config=oic.engine_config(), now=NOW)
    db.execute(
        '''INSERT INTO threat_detections (id, workspace_id, cluster_key, detection_type, title,
                                          severity, confidence, status, category, detected_at)
           VALUES (%s, %s, %s, 'unusual_transfer', 'Unusual Transfer', 'high', 0.7, 'open',
                   'CYBER_SECURITY', NOW())''',
        (str(uuid.uuid4()), ws, f'cyber-{ws[:8]}'),
    )

    operational = int(db.execute(
        '''SELECT COUNT(*) AS n FROM threat_detections
           WHERE workspace_id = %s
             AND COALESCE(NULLIF(category, ''), %s) = %s''',
        (ws, tdc.CATEGORY_CYBER_SECURITY, tdc.CATEGORY_OPERATIONAL_INTEGRITY),
    ).fetchone()['n'])
    assert (operational, _count(db, ws)) == (1, 2)
