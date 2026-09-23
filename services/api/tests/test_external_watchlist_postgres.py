"""External Watchlist end to end, against REAL PostgreSQL and a simulated chain.

Every migration is applied, the API handlers and the worker run for real, and
only two things are simulated: the authenticated session (who is calling) and
the JSON-RPC endpoint (a deterministic in-memory chain). So the SQL, the CHECK
constraints, the triggers, the dedupe keys, the transactions and the audit
chain are exercised exactly as production would exercise them.

What is pinned:

  A  schema     scope/authority/address CHECKs, one active target per address,
                one open backfill per target, append-only telemetry and evidence
  B  flow       founder creates a protocol → worker backfills history in safe
                chunks (a provider size refusal halves the range) → events are
                stored once → findings use careful language and carry the
                three-part analysis → re-running the scan duplicates nothing
  C  review     finding status + outreach candidate, audited, internal only
  D  evidence   sealed package verifies; tampering is detected; the prospect
                report carries no internal ids, scoring or triage state
  E  convert    explicit, confirmed, creates ONE Pilot workspace with a 30-day
                window, copies targets as inert drafts with provenance, restores
                the founder's own workspace, refuses a second conversion
  F  isolation  no customer table (telemetry, detections, alerts, incidents)
                receives external data; a customer tenant is untouched
  G  access     a Pilot/customer user is refused by every endpoint

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_external_watchlist_postgres.py
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from services.api.tests.external_watchlist_support import (
    FakeChain, addr, data_words, make_log, topic_address, topic_uint, tx,
)

_DSN = os.environ.get('DECODA_MIGRATION_TEST_DSN')
_PSQL = shutil.which('psql')


def _real_psycopg():
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_DSN and _PSQL),
        reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have psql on PATH',
    ),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'

FOUNDER = str(uuid.UUID('11111111-1111-4111-8111-111111111111'))
CUSTOMER = str(uuid.UUID('22222222-2222-4222-8222-222222222222'))
CUSTOMER_WS = str(uuid.UUID('33333333-3333-4333-8333-333333333333'))
FOUNDER_WS = str(uuid.UUID('44444444-4444-4444-8444-444444444444'))

TOKEN = addr(0xA11CE)
SAFE = addr(0x5AFE)
ADMIN = addr(0xAD)
NEW_MINTER = addr(0xBEEF)
NEW_IMPL = addr(0x1111)
HOLDER = addr(0x7777)
TIP = 1_000_000


def _psql(*args: str) -> None:
    proc = subprocess.run([_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', *args], capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, f'psql failed:\n{proc.stdout}\n{proc.stderr}'


def _connect():
    return psycopg.connect(_DSN, row_factory=psycopg.rows.dict_row)


@pytest.fixture(scope='module')
def migrated() -> None:
    _psql('-c', 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
    for path in sorted(_MIGRATIONS.glob('*.sql')):
        _psql('-f', str(path))


@pytest.fixture()
def db(migrated):
    with _connect() as connection:
        for table in (
            'external_watchlist_conversions', 'external_watchlist_evidence', 'external_watchlist_findings',
            'external_watchlist_baselines', 'external_watchlist_events', 'external_watchlist_backfills',
            'external_watchlist_targets', 'external_watchlists', 'external_watchlist_worker_state',
        ):
            connection.execute(f'DELETE FROM {table}')
        connection.execute("SELECT set_config('app.retention_worker', 'on', true)")
        connection.execute('DELETE FROM audit_logs')
        connection.execute('DELETE FROM targets')
        connection.execute('DELETE FROM assets')
        connection.execute('DELETE FROM organization_memberships')
        connection.execute('UPDATE users SET current_workspace_id = NULL')
        connection.execute('DELETE FROM workspace_members')
        connection.execute('DELETE FROM workspaces')
        connection.execute('DELETE FROM organizations')
        connection.execute('DELETE FROM users')
        for user_id, email, internal in ((FOUNDER, 'founder@decoda.test', True), (CUSTOMER, 'analyst@pilot.test', False)):
            connection.execute(
                """INSERT INTO users (id, email, password_hash, full_name, email_verified_at, is_internal_admin)
                   VALUES (%s, %s, 'x', 'Test User', NOW(), %s)""",
                (user_id, email, internal),
            )
        for workspace_id, owner, name in ((FOUNDER_WS, FOUNDER, 'Decoda HQ'), (CUSTOMER_WS, CUSTOMER, 'Pilot Customer')):
            connection.execute(
                'INSERT INTO workspaces (id, name, slug, created_by_user_id) VALUES (%s, %s, %s, %s)',
                (workspace_id, name, name.lower().replace(' ', '-'), owner),
            )
            connection.execute(
                "INSERT INTO workspace_members (id, workspace_id, user_id, role) VALUES (%s, %s, %s, 'owner')",
                (str(uuid.uuid4()), workspace_id, owner),
            )
        connection.execute('UPDATE users SET current_workspace_id = %s WHERE id = %s', (FOUNDER_WS, FOUNDER))
        connection.execute('UPDATE users SET current_workspace_id = %s WHERE id = %s', (CUSTOMER_WS, CUSTOMER))
        connection.commit()
        yield connection


# ── wiring ───────────────────────────────────────────────────────────────────
def _chain() -> FakeChain:
    """A Base-like chain with 7 days of protocol history before TIP."""
    genesis = int(datetime.now(timezone.utc).timestamp()) - TIP * 2
    logs = []
    index = 0
    # 30 routine transfers build the token's baseline.
    for n in range(30):
        block = TIP - 250_000 + n * 1000
        logs.append(make_log(address=TOKEN, topics=[
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            topic_address(HOLDER), topic_address(addr(0x9000 + n)),
        ], data=data_words(1_000_000 + n), block=block, tx_hash=tx(10_000 + n), log_index=0))
    # An unusually large transfer AFTER the baseline exists.
    logs.append(make_log(address=TOKEN, topics=[
        '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
        topic_address(HOLDER), topic_address(addr(0xFEED)),
    ], data=data_words(500_000_000), block=TIP - 100_000, tx_hash=tx(20_000), log_index=1))
    # Administrative events.
    logs.append(make_log(address=TOKEN, topics=[
        '0x2f8788117e7eff1d82e926ec794901d17c78024a50270940304540a733656f0d',
        '0x9f2df0fed2c77648de5860a4cc508cd0818c85b8b8a1ab4ceeef8d981c8956a6',
        topic_address(NEW_MINTER), topic_address(ADMIN),
    ], block=TIP - 90_000, tx_hash=tx(30_000), log_index=0))
    logs.append(make_log(address=TOKEN, topics=[
        '0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b', topic_address(NEW_IMPL),
    ], block=TIP - 80_000, tx_hash=tx(30_001), log_index=0))
    logs.append(make_log(address=TOKEN, topics=[
        '0x62e78cea01bee320cd4e420270b5ea74000d11b0c9f74754ebdbfc544b05a258',
    ], data=data_words(ADMIN), block=TIP - 70_000, tx_hash=tx(30_002), log_index=0))
    # Outside the 7-day window: must never be read by a 7-day backfill.
    logs.append(make_log(address=TOKEN, topics=[
        '0x62e78cea01bee320cd4e420270b5ea74000d11b0c9f74754ebdbfc544b05a258',
    ], data=data_words(ADMIN), block=TIP - 400_000, tx_hash=tx(99_999), log_index=0))
    return FakeChain(
        tip=TIP, genesis_ts=genesis, block_seconds=2, logs=logs,
        code={TOKEN: '0x6080604052', SAFE: '0x6080604052'},
        tx_senders={tx(30_000): ADMIN, tx(30_001): ADMIN, tx(30_002): ADMIN, tx(20_000): HOLDER},
        max_range=40_000,
    )


@pytest.fixture()
def wired(monkeypatch, db):
    from services.api.app import pilot
    from services.api.app.domains.external_watchlist import rpc

    chain = _chain()
    state = {'user': FOUNDER}

    @contextmanager
    def _pg():
        with _connect() as connection:
            yield connection

    users = {
        FOUNDER: {'id': FOUNDER, 'email': 'founder@decoda.test', 'email_verified': True},
        CUSTOMER: {'id': CUSTOMER, 'email': 'analyst@pilot.test', 'email_verified': True},
    }
    monkeypatch.setenv('EXTERNAL_WATCHLIST_ENABLED', 'true')
    monkeypatch.setenv('EXTERNAL_WATCHLIST_RPC_URL_8453', 'https://fake-rpc.local/key-never-dialed')
    monkeypatch.delenv('DECODA_INTERNAL_ADMIN_EMAILS', raising=False)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: dict(users[state['user']]))
    monkeypatch.setattr(pilot, 'database_url', lambda: _DSN)
    monkeypatch.setattr(
        rpc, 'build_client',
        lambda network, *, budget=None, request_path=False, urls_resolver=None: rpc.ReadOnlyRpcClient(chain, budget=budget),
    )
    return SimpleNamespace(chain=chain, state=state)


def _request() -> SimpleNamespace:
    return SimpleNamespace(headers={}, client=SimpleNamespace(host='127.0.0.1'))


def _worker_config(**overrides: Any) -> dict[str, Any]:
    from services.api.app.domains.external_watchlist import config as ewc

    cfg = ewc.worker_config()
    cfg.update({
        'enabled': True, 'backfill_chunk_blocks': 100_000, 'min_chunk_blocks': 1_000, 'rpc_pacing_seconds': 0.0,
        'retry_backoff_seconds': 0.0, 'max_rpc_calls_per_cycle': 5_000, 'max_blocks_per_poll': 5_000,
        'interval_seconds': 30,
    })
    cfg.update(overrides)
    return cfg


def _run_worker(**overrides: Any) -> dict[str, Any]:
    from services.api.app.domains.external_watchlist import worker

    return worker.run_worker_once(_worker_config(**overrides), sleep=lambda _s: None)


def _create(**body: Any) -> dict[str, Any]:
    from services.api.app.domains.external_watchlist import endpoints

    payload = {'name': 'Spiko', 'website_url': 'https://spiko.io', 'network': 'base', 'address': TOKEN.upper().replace('0X', '0x'),
               'label': 'EUTBL token', 'target_type': 'contract', 'backfill_days': 7}
    payload.update(body)
    return endpoints.create_watchlist(payload, _request())


# ── A: schema ────────────────────────────────────────────────────────────────
def test_a_schema_refuses_other_scopes_authority_and_unnormalized_addresses(db) -> None:
    watchlist_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO external_watchlists (id, name, slug, disclaimer_version) VALUES (%s, 'P', 'p', 'v1')""",
        (watchlist_id,),
    )
    db.commit()
    bad_rows = [
        ("monitoring_scope", "'workspace'"),
        ("execution_authority", "'FULL'"),
    ]
    for column, value in bad_rows:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(f"UPDATE external_watchlists SET {column} = {value} WHERE id = %s", (watchlist_id,))
        db.rollback()
    for address in ('0xABCDEF0000000000000000000000000000000001', '0x0000000000000000000000000000000000000000', '0x1234'):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """INSERT INTO external_watchlist_targets (id, watchlist_id, target_type, network, chain_id, address)
                   VALUES (%s, %s, 'contract', 'base-mainnet', 8453, %s)""",
                (str(uuid.uuid4()), watchlist_id, address),
            )
        db.rollback()
    target_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO external_watchlist_targets (id, watchlist_id, target_type, network, chain_id, address)
           VALUES (%s, %s, 'contract', 'base-mainnet', 8453, %s)""",
        (target_id, watchlist_id, TOKEN),
    )
    db.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            """INSERT INTO external_watchlist_targets (id, watchlist_id, target_type, network, chain_id, address)
               VALUES (%s, %s, 'wallet', 'base-mainnet', 8453, %s)""",
            (str(uuid.uuid4()), watchlist_id, TOKEN),
        )
    db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute('UPDATE external_watchlist_targets SET execution_authority = %s WHERE id = %s', ('SIGN', target_id))
    db.rollback()
    for _ in range(2):
        db.execute(
            """INSERT INTO external_watchlist_backfills (id, watchlist_id, target_id, requested_days, network, chain_id)
               VALUES (%s, %s, %s, 7, 'base-mainnet', 8453)""",
            (str(uuid.uuid4()), watchlist_id, target_id),
        ) if _ == 0 else None
    db.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            """INSERT INTO external_watchlist_backfills (id, watchlist_id, target_id, requested_days, network, chain_id)
               VALUES (%s, %s, %s, 30, 'base-mainnet', 8453)""",
            (str(uuid.uuid4()), watchlist_id, target_id),
        )
    db.rollback()
    event_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO external_watchlist_events (id, watchlist_id, target_id, network, chain_id, contract_address,
               block_number, tx_hash, log_index, event_name, event_type, event_category, topic0, decode_status,
               payload_sha256, ingest_source, observed_at, observed_at_source)
           VALUES (%s, %s, %s, 'base-mainnet', 8453, %s, 1, %s, 0, 'Paused', 'paused', 'emergency_control', %s,
                   'decoded', %s, 'live_poll', NOW(), 'block_timestamp')""",
        (event_id, watchlist_id, target_id, TOKEN, tx(1), '0x' + '1' * 64, 'a' * 64),
    )
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
        db.execute("UPDATE external_watchlist_events SET event_name = 'Unpaused' WHERE id = %s", (event_id,))
    db.rollback()


# ── B: create, backfill, detect ──────────────────────────────────────────────
def test_b_create_backfill_detect_and_dedupe(wired, db) -> None:
    created = _create()
    watchlist = created['watchlist']
    assert watchlist['name'] == 'Spiko'
    assert watchlist['monitoring_scope'] == 'external_public'
    assert watchlist['execution_authority'] == 'NONE'
    target = created['targets'][0]
    assert target['address'] == TOKEN  # normalised to lowercase
    assert target['live_start_block'] == TIP + 1
    assert target['backfill']['status'] == 'pending'

    # The API made no eth_getLogs call: backfill never runs in a request.
    assert not any(method == 'eth_getLogs' for method, _ in wired.chain.calls)

    audit = db.execute(
        "SELECT action, workspace_id, metadata FROM audit_logs WHERE action LIKE 'external_watchlist.%%' ORDER BY created_at"
    ).fetchall()
    actions = [row['action'] for row in audit]
    assert actions == ['external_watchlist.created', 'external_watchlist.target_added', 'external_watchlist.backfill_triggered']
    assert all(row['workspace_id'] is None for row in audit)
    assert all(row['metadata']['monitoring_scope'] == 'external_public' for row in audit)

    summary = _run_worker()
    assert summary['backfill_results'].get('completed') == 1, summary

    job = db.execute('SELECT * FROM external_watchlist_backfills').fetchone()
    assert job['status'] == 'completed'
    assert job['scanned_blocks'] == job['total_blocks'] > 0
    # The provider refused 100k-block ranges (max 40k): the chunk was halved, never skipped.
    assert job['chunk_size'] <= 40_000
    assert job['window_end_block'] == TIP
    assert 7 * 43_200 - 5_000 <= TIP - job['estimated_start_block'] <= 7 * 43_200 + 5_000

    events = db.execute('SELECT * FROM external_watchlist_events ORDER BY block_number, log_index').fetchall()
    assert len(events) == 34  # 30 routine + 1 large transfer + RoleGranted + Upgraded + Paused
    assert all(e['monitoring_scope'] == 'external_public' and e['ingest_source'] == 'historical_backfill' for e in events)
    assert tx(99_999) not in {e['tx_hash'] for e in events}  # outside the 7-day window
    assert all(e['observed_at_source'] in ('block_timestamp', 'estimated_from_block') for e in events)

    findings = {f['rule_key']: f for f in db.execute('SELECT * FROM external_watchlist_findings').fetchall()}
    assert set(findings) == {
        'token_operation.large_transfer', 'access_control.role_granted', 'upgradeability.upgraded', 'emergency_control.paused',
    }
    role = findings['access_control.role_granted']
    assert role['title'] == 'Privileged role granted: MINTER_ROLE'
    assert role['finding_class'] == 'privileged_configuration_change'
    assert role['initiator'] == ADMIN
    assert role['monitoring_scope'] == 'external_public' and role['execution_authority'] == 'NONE'
    assert set(role['ai_analysis']) >= {'observed_fact', 'decoda_interpretation', 'operational_authorization'}
    assert 'does not establish whether the change was operationally authorized' in role['ai_analysis']['operational_authorization']
    assert 'successfully confirmed on-chain' in role['ai_analysis']['observed_fact']
    for finding in findings.values():
        text = ' '.join([finding['title'], finding['explanation'], *[str(v) for v in finding['ai_analysis'].values()]]).lower()
        for word in ('attack', 'hack', 'exploit', 'compromise', 'malicious'):
            assert word not in text
    upgrade = findings['upgradeability.upgraded']
    assert upgrade['new_state'] == {'implementation': NEW_IMPL}

    from services.api.app.domains.external_watchlist import endpoints

    # A second click while a scan is open is refused by the database's
    # one-open-backfill rule, not by a race in application code.
    endpoints.trigger_backfill(watchlist['id'], {'days': 7}, _request())
    with pytest.raises(Exception) as already_open:
        endpoints.trigger_backfill(watchlist['id'], {'days': 7}, _request())
    assert already_open.value.status_code == 409
    # Re-running the same 7 days reads NOTHING again: the window is covered.
    logs_calls_before = sum(1 for method, _ in wired.chain.calls if method == 'eth_getLogs')
    _run_worker()
    rerun = db.execute('SELECT * FROM external_watchlist_backfills ORDER BY created_at').fetchall()[-1]
    assert rerun['status'] == 'completed' and rerun['status_reason'] == 'window_already_covered'
    assert rerun['total_blocks'] == 0
    assert sum(1 for method, _ in wired.chain.calls if method == 'eth_getLogs') == logs_calls_before

    # Extending to 14 days scans only the older, uncovered range — and picks up
    # exactly the one event that lies there, once.
    endpoints.trigger_backfill(watchlist['id'], {'days': 14}, _request())
    _run_worker()
    jobs = db.execute('SELECT * FROM external_watchlist_backfills ORDER BY created_at').fetchall()
    assert jobs[-1]['status'] == 'completed'
    assert jobs[-1]['plan_ranges'][-1][1] < jobs[0]['plan_ranges'][0][0]
    assert db.execute('SELECT COUNT(*) AS n FROM external_watchlist_events').fetchone()['n'] == 35
    assert db.execute("SELECT COUNT(*) AS n FROM external_watchlist_events WHERE tx_hash = %s", (tx(99_999),)).fetchone()['n'] == 1
    assert db.execute('SELECT COUNT(*) AS n FROM external_watchlist_findings').fetchone()['n'] == 5


def test_b2_live_poll_reads_new_blocks_and_marks_status_live(wired, db) -> None:
    created = _create(backfill_days=0)
    watchlist_id = created['watchlist']['id']
    assert created['watchlist']['status'] == 'degraded'  # awaiting first poll: never 'live' by default
    wired.chain.tip = TIP + 200
    wired.chain.logs.append(make_log(address=TOKEN, topics=[
        '0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0',
        topic_address(ADMIN), topic_address(NEW_MINTER),
    ], block=TIP + 50, tx_hash=tx(40_000), log_index=3))
    wired.chain.tx_senders[tx(40_000)] = ADMIN
    _run_worker()
    from services.api.app.domains.external_watchlist import endpoints

    detail = endpoints.get_watchlist(watchlist_id, _request())
    assert detail['watchlist']['status'] == 'live'
    assert detail['overview']['events_total'] == 1
    event = db.execute('SELECT * FROM external_watchlist_events').fetchone()
    assert event['ingest_source'] == 'live_poll' and event['event_name'] == 'OwnershipTransferred'
    finding = db.execute('SELECT * FROM external_watchlist_findings').fetchone()
    assert finding['title'] == 'Contract ownership transferred'
    assert finding['finding_class'] == 'administrative_change'
    target = db.execute('SELECT * FROM external_watchlist_targets').fetchone()
    assert target['last_processed_block'] == TIP + 200 - 10  # confirmed tip (Base: 10 confirmations)
    assert target['last_successful_poll_at'] is not None and target['last_event_at'] is not None

    # Pause: status is paused and the worker stops reading the chain for it.
    endpoints.update_watchlist(watchlist_id, {'monitoring_enabled': False}, _request())
    calls_before = len(wired.chain.calls)
    db.execute("UPDATE external_watchlist_targets SET last_polled_at = NOW() - INTERVAL '1 hour'")
    db.commit()
    _run_worker()
    assert len(wired.chain.calls) == calls_before
    assert endpoints.get_watchlist(watchlist_id, _request())['watchlist']['status'] == 'paused'


# ── C / D: review, evidence, prospect report ────────────────────────────────
def _backfilled(wired) -> tuple[str, dict[str, Any]]:
    created = _create()
    _run_worker()
    from services.api.app.domains.external_watchlist import endpoints

    findings = endpoints.list_findings(created['watchlist']['id'], _request())['findings']
    role = next(f for f in findings if f['finding_type'] == 'privileged_role_granted')
    return created['watchlist']['id'], role


def test_c_review_status_and_outreach_candidate_are_audited(wired, db) -> None:
    from services.api.app.domains.external_watchlist import endpoints

    watchlist_id, role = _backfilled(wired)
    result = endpoints.update_finding(watchlist_id, role['id'], {'status': 'outreach_candidate', 'note': 'Share with team'}, _request())
    assert result['finding']['status'] == 'outreach_candidate'
    assert result['finding']['status_label'] == 'Outreach Candidate'
    actions = [row['action'] for row in db.execute(
        "SELECT action FROM audit_logs WHERE action LIKE 'external_watchlist.%%' ORDER BY created_at").fetchall()]
    assert 'external_watchlist.finding_status_changed' in actions
    assert 'external_watchlist.outreach_candidate_marked' in actions
    filtered = endpoints.list_findings(watchlist_id, _request(), status_filter='outreach_candidate')
    assert [f['id'] for f in filtered['findings']] == [role['id']]


def test_d_evidence_package_verifies_detects_tampering_and_report_is_sanitized(wired, db) -> None:
    from services.api.app.domains.external_watchlist import endpoints
    from services.api.app.domains.external_watchlist import evidence as evidence_builder

    watchlist_id, role = _backfilled(wired)
    package = endpoints.generate_evidence(watchlist_id, role['id'], _request())['evidence']
    assert package['verification']['valid'] is True
    document = package['files']['evidence.json']
    assert document['monitoring_scope'] == 'external_public'
    assert document['protocol']['name'] == 'Spiko'
    assert document['monitored_address'] == TOKEN
    assert document['chain']['chain_id'] == 8453
    assert document['transaction_hash'] == tx(30_000)
    assert document['decoded_event']['event_name'] == 'RoleGranted'
    assert document['telemetry_used'] and document['telemetry_used'][0]['payload_sha256']
    assert set(document['ai_analysis']) >= {'observed_fact', 'decoda_interpretation', 'operational_authorization'}
    assert document['disclaimer'].startswith('This report was generated from publicly available blockchain telemetry')
    assert len(package['evidence_sha256']) == 64

    stored = db.execute('SELECT * FROM external_watchlist_evidence WHERE id = %s', (package['id'],)).fetchone()
    tampered = dict(stored)
    tampered['files'] = {'evidence.json': {**stored['files']['evidence.json'], 'block_number': 1}}
    assert evidence_builder.verify_package(tampered)['valid'] is False
    with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
        db.execute("UPDATE external_watchlist_evidence SET evidence_sha256 = %s WHERE id = %s", ('0' * 64, package['id']))
    db.rollback()

    report = endpoints.generate_prospect_report(watchlist_id, role['id'], _request())['report']
    assert report['heading'] == 'Administrative Change Detected'
    assert report['protocol'] == 'Spiko' and report['network'] == 'Base Mainnet'
    assert report['transaction'] == tx(30_000)
    assert report['evidence_verification']['status'] == 'verified'
    serialized = str(report).lower()
    for internal in (watchlist_id, role['id'], FOUNDER, 'confidence', 'score', 'outreach', 'rpc', 'fake-rpc',
                     'severity', 'dedupe', 'lease'):
        assert internal.lower() not in serialized, internal
    listing = endpoints.list_evidence(watchlist_id, _request())
    assert {row['package_type'] for row in listing['evidence']} == {'evidence_package', 'prospect_report'}
    actions = [row['action'] for row in db.execute(
        "SELECT action FROM audit_logs WHERE action LIKE 'external_watchlist.%%'").fetchall()]
    assert actions.count('external_watchlist.evidence_generated') == 1
    assert 'external_watchlist.prospect_report_generated' in actions


# ── E: conversion ────────────────────────────────────────────────────────────
def test_e_conversion_creates_one_pilot_workspace_with_inert_provenanced_targets(wired, db) -> None:
    from services.api.app.domains.external_watchlist import endpoints

    watchlist_id, _role = _backfilled(wired)
    detail = endpoints.get_watchlist(watchlist_id, _request())
    target_id = detail['targets'][0]['id']

    with pytest.raises(Exception) as unconfirmed:
        endpoints.convert_to_pilot(watchlist_id, {'target_ids': [target_id]}, _request())
    assert unconfirmed.value.detail['code'] == 'CONFIRMATION_REQUIRED'
    assert db.execute('SELECT COUNT(*) AS n FROM organizations').fetchone()['n'] == 0

    result = endpoints.convert_to_pilot(watchlist_id, {'confirm': True, 'target_ids': [target_id]}, _request())['conversion']
    assert result['customer_authorized'] is False and result['monitoring_started'] is False
    workspace_id = result['workspace']['id']
    organization = db.execute('SELECT * FROM organizations WHERE id = %s', (result['organization']['id'],)).fetchone()
    assert organization['plan'] == 'pilot' and organization['status'] == 'active'
    window = organization['evaluation_expires_at'] - datetime.now(timezone.utc)
    assert timedelta(days=29, hours=23) < window <= timedelta(days=30, minutes=1)

    copied = db.execute('SELECT * FROM targets WHERE workspace_id = %s', (workspace_id,)).fetchall()
    assert len(copied) == 1
    assert copied[0]['enabled'] is False and copied[0]['monitoring_enabled'] is False
    provenance = copied[0]['target_metadata']['provenance']
    assert provenance['origin_monitoring_scope'] == 'external_public'
    assert provenance['customer_authorized'] is False
    assert provenance['external_target_id'] == target_id
    asset = db.execute('SELECT * FROM assets WHERE workspace_id = %s', (workspace_id,)).fetchone()
    assert asset['identifier'] == TOKEN and asset['enabled'] is False

    founder = db.execute('SELECT current_workspace_id FROM users WHERE id = %s', (FOUNDER,)).fetchone()
    assert str(founder['current_workspace_id']) == FOUNDER_WS  # not silently switched
    assert db.execute('SELECT COUNT(*) AS n FROM monitoring_configs WHERE workspace_id = %s', (workspace_id,)).fetchone()['n'] == 0
    # No invitation is sent by conversion.
    assert db.execute('SELECT COUNT(*) AS n FROM workspace_invitations WHERE workspace_id = %s', (workspace_id,)).fetchone()['n'] == 0

    history = endpoints.get_watchlist(watchlist_id, _request())
    assert history['conversion']['workspace_id'] == workspace_id
    assert history['overview']['events_total'] == 34  # external provenance kept

    with pytest.raises(Exception) as again:
        endpoints.convert_to_pilot(watchlist_id, {'confirm': True}, _request())
    assert again.value.status_code == 409
    assert db.execute('SELECT COUNT(*) AS n FROM organizations').fetchone()['n'] == 1
    workspace_audit = db.execute(
        "SELECT action FROM audit_logs WHERE workspace_id = %s ORDER BY created_at", (workspace_id,),
    ).fetchall()
    assert 'external_watchlist.targets_copied' in [row['action'] for row in workspace_audit]


# ── F: isolation ─────────────────────────────────────────────────────────────
def test_f_no_customer_table_receives_external_data(wired, db) -> None:
    _backfilled(wired)
    for table in ('telemetry_events', 'threat_detections', 'alerts', 'incidents', 'targets', 'assets', 'monitoring_configs'):
        count = db.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n']
        assert count == 0, f'{table} received external data'
    customer_audit = db.execute('SELECT COUNT(*) AS n FROM audit_logs WHERE workspace_id = %s', (CUSTOMER_WS,)).fetchone()['n']
    assert customer_audit == 0


# ── G: access ────────────────────────────────────────────────────────────────
def test_g_customer_is_refused_everywhere_and_nothing_is_written(wired, db) -> None:
    from services.api.app.domains.external_watchlist import endpoints

    watchlist_id, role = _backfilled(wired)
    before = db.execute('SELECT COUNT(*) AS n FROM audit_logs').fetchone()['n']
    wired.state['user'] = CUSTOMER
    calls = [
        lambda: endpoints.get_console_config(_request()),
        lambda: endpoints.list_watchlists(_request()),
        lambda: endpoints.create_watchlist({'name': 'X', 'network': 'base'}, _request()),
        lambda: endpoints.get_watchlist(watchlist_id, _request()),
        lambda: endpoints.update_watchlist(watchlist_id, {'monitoring_enabled': False}, _request()),
        lambda: endpoints.delete_watchlist(watchlist_id, _request()),
        lambda: endpoints.trigger_backfill(watchlist_id, {'days': 30}, _request()),
        lambda: endpoints.list_events(watchlist_id, _request()),
        lambda: endpoints.list_findings(watchlist_id, _request()),
        lambda: endpoints.get_finding(watchlist_id, role['id'], _request()),
        lambda: endpoints.update_finding(watchlist_id, role['id'], {'status': 'dismissed'}, _request()),
        lambda: endpoints.generate_evidence(watchlist_id, role['id'], _request()),
        lambda: endpoints.generate_prospect_report(watchlist_id, role['id'], _request()),
        lambda: endpoints.list_evidence(watchlist_id, _request()),
        lambda: endpoints.convert_to_pilot(watchlist_id, {'confirm': True}, _request()),
        lambda: endpoints.refuse_execution(watchlist_id, 'execute', _request()),
    ]
    for call in calls:
        with pytest.raises(Exception) as refusal:
            call()
        assert refusal.value.status_code == 403
        assert refusal.value.detail['code'] == 'INTERNAL_ADMIN_REQUIRED'
    assert db.execute('SELECT COUNT(*) AS n FROM audit_logs').fetchone()['n'] == before
    assert db.execute('SELECT status FROM external_watchlist_findings WHERE id = %s', (role['id'],)).fetchone()['status'] == 'new'
    assert db.execute('SELECT COUNT(*) AS n FROM organizations').fetchone()['n'] == 0
