"""Screen 11 governance policies against a REAL PostgreSQL (opt-in; integration).

The fake-connection suites cover the decision logic and the write shapes. This
one covers what only a real database can prove:

  * migration 0147 applies on top of the full migration history and is
    idempotent (the startup runner re-applies it safely),
  * its CHECK constraints actually reject an invalid status, operation,
    settlement requirement, decision and negative cap,
  * a money value survives the NUMERIC(38, 2) round trip with every digit
    intact (a float would silently corrupt an issuance limit),
  * one policy can hold only one row per version, so history cannot fork,
  * the daily-issuance total really does exclude simulations and denials,
  * the deterministic engine reaches ALLOW and DENY end to end through real SQL.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_governance_policy_postgres.py

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
    """The actual driver, not the conftest stub (see the sibling harness)."""
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

_needs_pg = pytest.mark.skipif(
    not (_DSN and _PSQL),
    reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have '
           'psql on PATH to run the governance-policy real-schema harness',
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
_MIGRATION_0147 = _MIGRATIONS / '0147_governance_policies.sql'
NOW = datetime(2026, 9, 1, 10, 42, 0, tzinfo=timezone.utc)
# 36 integer digits + 2 decimals — the full NUMERIC(38, 2) range, well past float64.
HUGE_USD = '123456789012345678901234567890123456.78'


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
    uid, ws = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, password_hash, full_name) VALUES (%s, %s, 'x', 'Policy Admin')",
        (uid, f'{uid[:8]}@example.test'),
    )
    conn.execute(
        'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) VALUES (%s, %s, %s, %s, NOW())',
        (ws, label, f'{label}-{ws[:6]}', uid),
    )
    return ws, uid


def _insert_policy(conn, ws: str, uid: str, *, key='POL-MINT-007', cap='10000000.00', status='ACTIVE') -> str:
    policy_id = str(uuid.uuid4())
    conn.execute(
        '''INSERT INTO governance_policies (
               id, workspace_id, policy_key, name, operation, status, version,
               required_business_event, settlement_requirement,
               allowed_window_start_utc, allowed_window_end_utc,
               maximum_daily_amount_usd, required_roles, violation_action, origin,
               created_by_user_id, updated_by_user_id
           ) VALUES (%s, %s, %s, 'RWA Mint Policy', 'MINT', %s, 7,
                     'SUBSCRIPTION', 'CLEARED', '08:00', '18:00', %s, %s::jsonb, 'DENY', 'customer', %s, %s)''',
        (policy_id, ws, key, status, cap,
         json.dumps(['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER']), uid, uid),
    )
    return policy_id


def _insert_evaluation(conn, ws: str, policy_id: str, *, decision='ALLOW', simulation=False,
                       amount='1000000.00', at=None) -> str:
    evaluation_id = str(uuid.uuid4())
    conn.execute(
        '''INSERT INTO governance_policy_evaluations (
               id, workspace_id, policy_id, policy_key, policy_version, operation, decision,
               reason_codes, required_approvals, checks, amount_usd, input_snapshot,
               simulation, engine_version, evaluated_at
           ) VALUES (%s, %s, %s, 'POL-MINT-007', 7, 'MINT', %s,
                     %s::jsonb, '[]'::jsonb, '[]'::jsonb, %s, '{}'::jsonb, %s,
                     'governance-policy-engine-v1', %s)''',
        (evaluation_id, ws, policy_id, decision, json.dumps(['POLICY_SATISFIED']),
         amount, simulation, at or NOW),
    )
    return evaluation_id


# --------------------------------------------------------------------------
# The migration itself
# --------------------------------------------------------------------------
@_needs_pg
def test_migration_0147_creates_the_tables_and_indexes(db):
    for table in ('governance_policies', 'governance_policy_versions', 'governance_policy_evaluations'):
        row = db.execute('SELECT to_regclass(%s) AS oid', (f'public.{table}',)).fetchone()
        assert row['oid'] is not None, f'{table} was not created'
    indexes = {r['indexname'] for r in db.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename LIKE 'governance_polic%'").fetchall()}
    assert 'uq_governance_policies_workspace_key' in indexes
    assert 'uq_governance_policy_versions_policy_version' in indexes
    assert 'idx_governance_policy_evaluations_daily_total' in indexes


@_needs_pg
def test_migration_0147_is_idempotent(db):
    # The startup runner may re-apply a migration; re-running it must not error.
    proc = subprocess.run(
        [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', '-f', str(_MIGRATION_0147)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f're-apply failed:\n{proc.stdout}\n{proc.stderr}'


# --------------------------------------------------------------------------
# The constraints are real
# --------------------------------------------------------------------------
@_needs_pg
@pytest.mark.parametrize('column,value', [
    ('status', 'RETIRED'),
    ('operation', 'AIRDROP'),
    ('settlement_requirement', 'SETTLED_SOMEHOW'),
    ('violation_action', 'WARN'),
    ('origin', 'guessed'),
])
def test_an_invalid_policy_value_is_rejected_by_the_database(db, column, value):
    """The vocabulary is enforced by the schema, not only by application code."""
    ws, uid = _seed_workspace(db, 'reject')
    columns = {'operation': 'MINT', column: value}
    names = ', '.join(columns)
    placeholders = ', '.join(['%s'] * len(columns))
    with pytest.raises(Exception):
        db.execute(
            f'''INSERT INTO governance_policies (id, workspace_id, policy_key, name, {names},
                    created_by_user_id, updated_by_user_id)
                VALUES (%s, %s, 'POL-X', 'X', {placeholders}, %s, %s)''',
            (str(uuid.uuid4()), ws, *columns.values(), uid, uid),
        )


@_needs_pg
def test_a_negative_daily_cap_is_rejected(db):
    ws, uid = _seed_workspace(db, 'negcap')
    with pytest.raises(Exception):
        _insert_policy(db, ws, uid, cap='-1.00')


@_needs_pg
def test_an_evaluation_decision_outside_allow_deny_is_rejected(db):
    ws, uid = _seed_workspace(db, 'baddecision')
    policy_id = _insert_policy(db, ws, uid)
    with pytest.raises(Exception):
        _insert_evaluation(db, ws, policy_id, decision='MAYBE')


@_needs_pg
def test_one_policy_key_per_workspace_but_the_same_key_in_another_workspace_is_fine(db):
    ws_a, uid_a = _seed_workspace(db, 'tenanta')
    ws_b, uid_b = _seed_workspace(db, 'tenantb')
    _insert_policy(db, ws_a, uid_a)
    with pytest.raises(Exception):
        _insert_policy(db, ws_a, uid_a)
    # A different tenant may use the same customer-facing key.
    assert _insert_policy(db, ws_b, uid_b)


@_needs_pg
def test_a_policy_version_cannot_fork(db):
    ws, uid = _seed_workspace(db, 'versionfork')
    policy_id = _insert_policy(db, ws, uid)

    def _version_row():
        db.execute(
            '''INSERT INTO governance_policy_versions (
                   id, workspace_id, policy_id, version, status, snapshot, change_summary, changed_by_user_id)
               VALUES (%s, %s, %s, 8, 'ACTIVE', '{}'::jsonb, 'x', %s)''',
            (str(uuid.uuid4()), ws, policy_id, uid),
        )

    _version_row()
    with pytest.raises(Exception):
        _version_row()


# --------------------------------------------------------------------------
# Money survives the round trip
# --------------------------------------------------------------------------
@_needs_pg
def test_a_full_range_money_value_survives_with_every_digit_intact(db):
    ws, uid = _seed_workspace(db, 'hugemoney')
    _insert_policy(db, ws, uid, cap=HUGE_USD)
    row = db.execute(
        'SELECT maximum_daily_amount_usd AS cap FROM governance_policies WHERE workspace_id = %s', (ws,),
    ).fetchone()
    assert isinstance(row['cap'], Decimal)
    assert str(row['cap']) == HUGE_USD, 'a float somewhere would have corrupted this'


# --------------------------------------------------------------------------
# The daily total counts enforcement only
# --------------------------------------------------------------------------
@_needs_pg
def test_the_daily_total_counts_only_allowed_enforcement_decisions_today(db):
    from services.api.app.domains.governance_policy import service

    ws, uid = _seed_workspace(db, 'dailytotal')
    policy_id = _insert_policy(db, ws, uid)

    _insert_evaluation(db, ws, policy_id, decision='ALLOW', simulation=False, amount='1000000.00')
    _insert_evaluation(db, ws, policy_id, decision='ALLOW', simulation=False, amount='2000000.00')
    # None of these may count.
    _insert_evaluation(db, ws, policy_id, decision='ALLOW', simulation=True, amount='9000000.00')
    _insert_evaluation(db, ws, policy_id, decision='DENY', simulation=False, amount='9000000.00')
    _insert_evaluation(db, ws, policy_id, decision='ALLOW', simulation=False, amount='9000000.00',
                       at=NOW - timedelta(days=2))

    total = service.daily_total_usd(db, workspace_id=ws, policy_id=policy_id, now=NOW)
    assert total == Decimal('3000000.00')


@_needs_pg
def test_another_tenants_evaluations_never_reach_this_workspaces_total(db):
    from services.api.app.domains.governance_policy import service

    ws_a, uid_a = _seed_workspace(db, 'totala')
    ws_b, uid_b = _seed_workspace(db, 'totalb')
    policy_a = _insert_policy(db, ws_a, uid_a)
    policy_b = _insert_policy(db, ws_b, uid_b)
    _insert_evaluation(db, ws_b, policy_b, decision='ALLOW', simulation=False, amount='7000000.00')
    assert service.daily_total_usd(db, workspace_id=ws_a, policy_id=policy_a, now=NOW) == Decimal('0')


# --------------------------------------------------------------------------
# End to end through real SQL
# --------------------------------------------------------------------------
@_needs_pg
def test_the_engine_reaches_deny_and_allow_through_the_real_schema(db):
    from services.api.app.domains.governance_policy import engine, service
    from services.api.app.domains.governance_policy.schemas import EvaluationContext

    ws, uid = _seed_workspace(db, 'endtoend')
    _insert_policy(db, ws, uid)
    policy = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')
    assert policy is not None
    assert policy.maximum_daily_amount_usd == Decimal('10000000.00')
    assert policy.required_roles == ('TREASURY_OPERATOR', 'COMPLIANCE_APPROVER')

    def _context(**kw):
        base = dict(
            operation='MINT', amount_usd=Decimal('5000000'), operator_id=uid,
            operator_has_treasury_role=True, business_event='SUBSCRIPTION',
            settlement_status='CLEARED', compliance_approval=True,
            evaluated_at=NOW, daily_total_usd=Decimal('0'), simulation=True,
        )
        base.update(kw)
        return EvaluationContext(**base)

    allowed = engine.evaluate_policy(policy, _context())
    denied = engine.evaluate_policy(policy, _context(compliance_approval=False))
    assert allowed.decision == 'ALLOW'
    assert denied.decision == 'DENY'
    assert denied.reason_codes == ('COMPLIANCE_APPROVAL_MISSING',)

    # And both records persist through the real INSERT, with the simulation flag intact.
    assert service.record_evaluation(db, workspace_id=ws, decision=denied, context=_context(compliance_approval=False), user_id=uid)
    row = db.execute(
        'SELECT decision, reason_codes, simulation, policy_version, amount_usd FROM governance_policy_evaluations WHERE id = %s',
        (denied.evaluation_id,),
    ).fetchone()
    assert row['decision'] == 'DENY'
    assert row['reason_codes'] == ['COMPLIANCE_APPROVAL_MISSING']
    assert row['simulation'] is True
    assert row['policy_version'] == 7
    assert row['amount_usd'] == Decimal('5000000')


@_needs_pg
def test_a_policy_lookup_is_scoped_to_the_calling_workspace(db):
    from services.api.app.domains.governance_policy import service

    ws_a, uid_a = _seed_workspace(db, 'scopea')
    ws_b, _uid_b = _seed_workspace(db, 'scopeb')
    policy_id = _insert_policy(db, ws_a, uid_a)
    assert service.get_policy(db, workspace_id=ws_a, policy_ref=policy_id) is not None
    # The row exists, but not for this tenant.
    assert service.get_policy(db, workspace_id=ws_b, policy_ref=policy_id) is None
    assert service.get_policy(db, workspace_id=ws_b, policy_ref='POL-MINT-007') is None
    assert service.list_policies(db, workspace_id=ws_b) == []


# --------------------------------------------------------------------------
# The write paths, against real SQL
# --------------------------------------------------------------------------
@_needs_pg
def test_a_material_edit_bumps_the_version_and_appends_history_atomically(db):
    from services.api.app.domains.governance_policy import service

    ws, uid = _seed_workspace(db, 'realedit')
    _insert_policy(db, ws, uid)
    policy = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')

    outcome = service.apply_policy_update(
        db, workspace_id=ws, policy=policy,
        changes={'maximum_daily_amount_usd': Decimal('5000000.00')},
        user_id=uid, expected_version=7, now=NOW,
    )
    assert outcome == {'status': 'updated', 'version': 8, 'material': ['maximum_daily_amount_usd'], 'renamed': False}

    current = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')
    assert current.version == 8
    assert current.maximum_daily_amount_usd == Decimal('5000000.00')

    history = service.list_versions(db, workspace_id=ws, policy_id=policy.policy_id)
    assert [h['version'] for h in history] == [8]
    assert history[0]['previous_values'] == {'maximum_daily_amount_usd': '10000000.00'}
    assert history[0]['new_values'] == {'maximum_daily_amount_usd': '5000000.00'}
    assert 'Maximum issuance changed' in history[0]['change_summary']
    # The snapshot replays the policy AS OF version 8.
    assert history[0]['snapshot']['version'] == 8
    assert history[0]['snapshot']['maximum_daily_amount_usd'] == '5000000.00'


@_needs_pg
def test_a_rename_updates_in_place_without_consuming_a_version(db):
    from services.api.app.domains.governance_policy import service

    ws, uid = _seed_workspace(db, 'realrename')
    _insert_policy(db, ws, uid)
    policy = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')
    service.apply_policy_update(
        db, workspace_id=ws, policy=policy, changes={'name': 'Treasury Mint Policy'},
        user_id=uid, expected_version=7, now=NOW,
    )
    current = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')
    assert current.name == 'Treasury Mint Policy'
    assert current.version == 7
    assert service.list_versions(db, workspace_id=ws, policy_id=policy.policy_id) == []


@_needs_pg
def test_a_concurrent_edit_cannot_be_overwritten_and_cannot_fork_the_history(db):
    """Two editors both holding version 7. Only one may publish version 8.

    This is the race the expected_version check alone cannot close: both
    requests read version 7, so both pass it. The UPDATE's own WHERE clause is
    what stops the second, and the loser must append NO history row — a
    fabricated audit entry for an edit that never happened.
    """
    from services.api.app.domains.governance_policy import service

    ws, uid = _seed_workspace(db, 'realconflict')
    _insert_policy(db, ws, uid)
    # Both editors open the policy at version 7.
    editor_a = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')
    editor_b = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')
    assert editor_a.version == editor_b.version == 7

    first = service.apply_policy_update(
        db, workspace_id=ws, policy=editor_a, changes={'status': 'DISABLED'},
        user_id=uid, expected_version=7, now=NOW,
    )
    assert first['status'] == 'updated' and first['version'] == 8

    second = service.apply_policy_update(
        db, workspace_id=ws, policy=editor_b,
        changes={'maximum_daily_amount_usd': Decimal('1.00')},
        user_id=uid, expected_version=7, now=NOW,
    )
    assert second['status'] == 'conflict'
    assert second['current_version'] == 8

    current = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')
    assert current.status == 'DISABLED'
    assert current.maximum_daily_amount_usd == Decimal('10000000.00')
    assert current.version == 8
    # One version 8, written by the winner. The history did not fork.
    history = service.list_versions(db, workspace_id=ws, policy_id=editor_a.policy_id)
    assert [h['version'] for h in history] == [8]
    assert 'Status changed' in history[0]['change_summary']


@_needs_pg
def test_the_demo_seed_writes_a_labelled_policy_with_a_first_version(db):
    from services.api.app.domains.governance_policy import demo_seed, service

    ws, uid = _seed_workspace(db, 'demoseed')
    result = demo_seed.seed_demo_policy(db, workspace_id=ws, user_id=uid, allowed=True, now=NOW)
    assert result['seeded'] is True

    policy = service.get_policy(db, workspace_id=ws, policy_ref=demo_seed.DEMO_POLICY_KEY)
    assert policy.status == 'ACTIVE'
    assert policy.origin == 'demo_seed', 'a seeded policy must be labelled, never shown as customer configuration'
    assert policy.maximum_daily_amount_usd == Decimal('10000000.00')
    assert policy.required_roles == ('TREASURY_OPERATOR', 'COMPLIANCE_APPROVER')
    assert [h['version'] for h in service.list_versions(db, workspace_id=ws, policy_id=policy.policy_id)] == [1]

    # Idempotent: a second bootstrap never duplicates or overwrites it.
    again = demo_seed.seed_demo_policy(db, workspace_id=ws, user_id=uid, allowed=True, now=NOW)
    assert again['seeded'] is False and again['reason'] == 'already_present'
    assert len(service.list_policies(db, workspace_id=ws)) == 1


@_needs_pg
def test_the_demo_seed_is_a_no_op_in_production(db):
    from services.api.app.domains.governance_policy import demo_seed, service

    ws, uid = _seed_workspace(db, 'prodseed')
    result = demo_seed.seed_demo_policy(db, workspace_id=ws, user_id=uid, allowed=False, now=NOW)
    assert result == {'seeded': False, 'reason': 'production_runtime'}
    assert service.list_policies(db, workspace_id=ws) == []


# ==========================================================================
# The ENDPOINTS against the real database.
#
# Everything above drives the service layer. This block drives the actual
# request handlers — RBAC, tenant resolution, the audit chain and the
# deterministic engine — with only the auth seams stubbed, so what is proven
# here is what a request would really do.
# ==========================================================================
class _Borrowed:
    """A context manager that lends a connection without closing it.

    The endpoints do ``with pilot.pg_connection() as connection``. psycopg
    closes a connection on ``with`` exit, so handing back the real one directly
    would kill it after the first call.
    """

    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self._connection

    def __exit__(self, *exc):
        return False


class _Req:
    def __init__(self):
        self.headers = {}
        self.client = None


def _add_member(conn, ws: str, label: str, role: str) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, password_hash, full_name) VALUES (%s, %s, 'x', %s)",
        (uid, f'{uid[:8]}@example.test', label),
    )
    conn.execute(
        'INSERT INTO workspace_members (id, workspace_id, user_id, role) VALUES (%s, %s, %s, %s)',
        (str(uuid.uuid4()), ws, uid, role),
    )
    return uid


@pytest.fixture(scope='module')
def endpoints_env(db):
    """Real SQL, real RBAC, real engine, real audit chain. Only auth is stubbed.

    NOT stubbed: _require_workspace_permission, _workspace_permission_granted,
    log_audit, and every governance_policy statement. The clock is frozen inside
    the 08:00-18:00 window the mint policy allows, so a run at any hour of the
    day evaluates the same way the policy says it should at 10:42 UTC.
    """
    from services.api.app import pilot
    from services.api.app.domains.governance_policy import endpoints

    monkeypatch = pytest.MonkeyPatch()
    ws_a, owner_a = _seed_workspace(db, 'endpoints-a')
    ws_b, owner_b = _seed_workspace(db, 'endpoints-b')
    admin = _add_member(db, ws_a, 'Policy Admin', 'workspace_admin')
    member = _add_member(db, ws_a, 'Treasury Operator', 'workspace_member')
    admin_b = _add_member(db, ws_b, 'Policy Admin B', 'workspace_admin')

    state = {'ws': ws_a, 'user': admin, 'role': 'workspace_admin'}
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _Borrowed(db))
    monkeypatch.setattr(pilot, 'utc_now', lambda: NOW)
    monkeypatch.setattr(pilot, 'authenticate_with_connection',
                        lambda c, r: {'id': state['user'], 'mfa_enabled': False, 'auth_provider': 'password'})
    monkeypatch.setattr(pilot, 'resolve_workspace',
                        lambda c, u, w: {'workspace_id': state['ws'], 'role': state['role']})
    yield {
        'endpoints': endpoints, 'state': state, 'req': _Req(),
        'ws_a': ws_a, 'ws_b': ws_b, 'admin': admin, 'member': member,
        'admin_b': admin_b, 'owner_a': owner_a, 'owner_b': owner_b,
    }
    monkeypatch.undo()


def _mint_payload(policy_key='POL-MINT-007'):
    return {
        'policy_id': policy_key,
        'name': 'RWA Mint Policy',
        'operation': 'MINT',
        'status': 'ACTIVE',
        'required_business_event': 'SUBSCRIPTION',
        'settlement_requirement': 'CLEARED',
        'allowed_window_utc': {'start': '08:00', 'end': '18:00'},
        'maximum_daily_amount_usd': '10000000.00',
        'required_roles': ['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'],
        'violation_action': 'DENY',
    }


@_needs_pg
def test_the_create_endpoint_persists_a_customer_authored_policy(db, endpoints_env):
    """§10 C: the record is actually created and persisted, by the real endpoint."""
    from services.api.app.domains.governance_policy import service

    env, ws = endpoints_env, endpoints_env['ws_a']
    result = env['endpoints'].create_policy_endpoint(_mint_payload(), env['req'])
    assert result['status'] == 'created'

    # Read it back with raw SQL, not through the service that wrote it.
    row = db.execute(
        'SELECT * FROM governance_policies WHERE workspace_id = %s AND policy_key = %s',
        (ws, 'POL-MINT-007'),
    ).fetchone()
    assert row is not None, 'the policy row must really exist'
    assert row['name'] == 'RWA Mint Policy'
    assert row['operation'] == 'MINT'
    assert row['status'] == 'ACTIVE'
    assert row['version'] == 1
    assert row['origin'] == 'customer', 'authored by a person, not seeded'
    assert row['required_business_event'] == 'SUBSCRIPTION'
    assert row['settlement_requirement'] == 'CLEARED'
    assert (row['allowed_window_start_utc'], row['allowed_window_end_utc']) == ('08:00', '18:00')
    assert row['maximum_daily_amount_usd'] == Decimal('10000000.00'), 'every digit survived NUMERIC(38, 2)'
    assert row['required_roles'] == ['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER']
    assert row['violation_action'] == 'DENY'
    assert str(row['created_by_user_id']) == env['admin']

    versions = db.execute(
        'SELECT version, change_summary FROM governance_policy_versions WHERE policy_id = %s',
        (row['id'],),
    ).fetchall()
    assert [v['version'] for v in versions] == [1]
    assert versions[0]['change_summary'] == 'Policy created.'

    audits = db.execute(
        "SELECT action FROM audit_logs WHERE workspace_id = %s AND entity_type = 'governance_policy'",
        (ws,),
    ).fetchall()
    assert [a['action'] for a in audits] == ['governance_policy.created']


@_needs_pg
def test_a_second_create_of_the_same_key_is_a_409_and_the_connection_survives(db, endpoints_env):
    """§3: reuse, never duplicate. And ON CONFLICT must not poison the
    transaction — a raised UniqueViolation would make the next read fail too."""
    from services.api.app.domains.governance_policy import service

    env = endpoints_env
    with pytest.raises(Exception) as exc:
        env['endpoints'].create_policy_endpoint(_mint_payload(), env['req'])
    assert getattr(exc.value, 'status_code', None) == 409
    assert exc.value.detail['code'] == 'policy_already_exists'

    # The same connection is still usable, and still holds exactly one policy.
    assert len(service.list_policies(db, workspace_id=env['ws_a'])) == 1


@_needs_pg
def test_the_create_endpoint_is_403_without_security_manage(db, endpoints_env):
    """§10 E: RBAC is enforced by the backend, not by a disabled button."""
    from services.api.app.domains.governance_policy import service

    env = endpoints_env
    env['state'].update({'user': env['member'], 'role': 'workspace_member'})
    before = len(service.list_policies(db, workspace_id=env['ws_a']))

    with pytest.raises(Exception) as exc:
        env['endpoints'].create_policy_endpoint(_mint_payload('POL-MINT-999'), env['req'])
    assert getattr(exc.value, 'status_code', None) == 403

    # A malformed payload from the same user is STILL 403, never 400: an
    # unauthorized caller learns nothing about which fields are valid.
    with pytest.raises(Exception) as exc2:
        env['endpoints'].create_policy_endpoint({'policy_id': 'nope!'}, env['req'])
    assert getattr(exc2.value, 'status_code', None) == 403

    assert len(service.list_policies(db, workspace_id=env['ws_a'])) == before

    # And the same role cannot edit the policy it cannot create.
    policy = service.list_policies(db, workspace_id=env['ws_a'])[0]
    with pytest.raises(Exception) as exc3:
        env['endpoints'].update_policy_endpoint(policy.policy_id, {'status': 'DISABLED'}, env['req'])
    assert getattr(exc3.value, 'status_code', None) == 403


@_needs_pg
def test_a_policy_in_another_workspace_cannot_be_read_or_modified(db, endpoints_env):
    """§9: workspace isolation, proven across the real endpoints."""
    from services.api.app.domains.governance_policy import service

    env = endpoints_env
    # Create the SAME policy key in workspace B. The unique index is per
    # workspace, so this must succeed and stay entirely separate.
    env['state'].update({'ws': env['ws_b'], 'user': env['admin_b'], 'role': 'workspace_admin'})
    created_b = env['endpoints'].create_policy_endpoint(_mint_payload(), env['req'])
    b_id = created_b['policy']['policy_id']
    b_before = db.execute('SELECT * FROM governance_policies WHERE id = %s', (b_id,)).fetchone()

    # Back in workspace A, B's policy is invisible and untouchable.
    env['state'].update({'ws': env['ws_a'], 'user': env['admin'], 'role': 'workspace_admin'})
    listed = env['endpoints'].list_policies_endpoint(env['req'])
    assert [p['policy_id'] for p in listed['policies']] != [b_id]
    assert all(p['policy_id'] != b_id for p in listed['policies'])

    for call in (
        lambda: env['endpoints'].policy_detail_endpoint(b_id, env['req']),
        lambda: env['endpoints'].policy_history_endpoint(b_id, env['req']),
        lambda: env['endpoints'].update_policy_endpoint(b_id, {'status': 'DISABLED'}, env['req']),
    ):
        with pytest.raises(Exception) as exc:
            call()
        assert getattr(exc.value, 'status_code', None) == 404, 'another tenant\'s policy is simply not there'

    b_after = db.execute('SELECT * FROM governance_policies WHERE id = %s', (b_id,)).fetchone()
    assert b_after == b_before, "workspace B's policy was not modified"


@_needs_pg
def test_simulation_denies_when_the_compliance_approval_is_missing(db, endpoints_env):
    """§5 / §10 F. The operator is a real workspace member holding
    response.propose, so the Treasury Operator check passes server-side and
    the missing compliance approval is the ONLY thing standing in the way."""
    from services.api.app.domains.governance_policy import service

    env = endpoints_env
    env['state'].update({'user': env['member'], 'role': 'workspace_member'})
    policy = service.get_policy(db, workspace_id=env['ws_a'], policy_ref='POL-MINT-007')

    result = env['endpoints'].simulate_endpoint(policy.policy_id, {
        'operation': 'MINT',
        'amount_usd': '5000000',
        'operator_id': env['member'],
        'business_event': 'SUBSCRIPTION',
        'settlement_status': 'CLEARED',
        'compliance_approval': False,
    }, env['req'])

    assert result['decision'] == 'DENY'
    assert result['reason_codes'] == ['COMPLIANCE_APPROVAL_MISSING']
    assert result['simulation'] is True
    assert result['required_approvals'] == ['COMPLIANCE_APPROVER']
    assert result['policy_version'] == 1
    assert result['can_manage'] is False, 'a member may simulate but not edit'


@_needs_pg
def test_simulation_allows_once_the_compliance_approval_is_present(db, endpoints_env):
    """§10 G. Same input, approval present. That this reaches ALLOW is what
    proves the operator's Treasury authority was really resolved from
    workspace_members plus the canonical permission map."""
    from services.api.app.domains.governance_policy import service

    env = endpoints_env
    env['state'].update({'user': env['member'], 'role': 'workspace_member'})
    policy = service.get_policy(db, workspace_id=env['ws_a'], policy_ref='POL-MINT-007')
    payload = {
        'operation': 'MINT', 'amount_usd': '5000000', 'operator_id': env['member'],
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
        'compliance_approval': True,
    }
    result = env['endpoints'].simulate_endpoint(policy.policy_id, payload, env['req'])
    assert result['decision'] == 'ALLOW'
    assert result['reason_codes'] == ['POLICY_SATISFIED']
    assert result['required_approvals'] == []

    # Negative control: an operator who is NOT a member of this workspace cannot
    # evidence Treasury authority, so the same approved request is denied.
    outsider = env['endpoints'].simulate_endpoint(
        policy.policy_id, {**payload, 'operator_id': env['owner_b']}, env['req'],
    )
    assert outsider['decision'] == 'DENY'
    assert 'TREASURY_OPERATOR_MISSING' in outsider['reason_codes']


@_needs_pg
def test_simulation_writes_only_a_simulation_row_and_executes_nothing(db, endpoints_env):
    """§10 I. A what-if predicts; it must not act, and must not move a counter."""
    from services.api.app.domains.governance_policy import service

    env, ws = endpoints_env, endpoints_env['ws_a']
    policy = service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007')

    rows = db.execute(
        'SELECT simulation, decision FROM governance_policy_evaluations WHERE workspace_id = %s',
        (ws,),
    ).fetchall()
    assert rows, 'the simulations above were recorded'
    assert all(r['simulation'] is True for r in rows), 'no enforcement row was written'
    assert {r['decision'] for r in rows} == {'ALLOW', 'DENY'}

    # The ALLOW moved no production counter.
    assert service.daily_total_usd(db, workspace_id=ws, policy_id=policy.policy_id, now=NOW) == Decimal('0')
    # The policy itself is untouched: still version 1, still one history row.
    assert service.get_policy(db, workspace_id=ws, policy_ref='POL-MINT-007').version == 1
    assert len(service.list_versions(db, workspace_id=ws, policy_id=policy.policy_id)) == 1

    # And nothing downstream was executed.
    for table in ('response_actions', 'incidents', 'alerts'):
        count = db.execute(
            f'SELECT COUNT(*) AS total FROM {table} WHERE workspace_id = %s', (ws,),
        ).fetchone()['total']
        assert count == 0, f'a simulation must not create a {table} row'

    # A simulation is not a governance change: the audit trail still holds only
    # the create.
    audits = db.execute(
        "SELECT action FROM audit_logs WHERE workspace_id = %s AND entity_type = 'governance_policy'",
        (ws,),
    ).fetchall()
    assert [a['action'] for a in audits] == ['governance_policy.created']
