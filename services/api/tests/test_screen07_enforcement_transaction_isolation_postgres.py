"""One failed evaluation must not cost the rest of the plan its evaluations.

Two defects are pinned here, both on a REAL PostgreSQL database because both are
about transaction state that no in-process fake reproduces.

1. TRANSACTION POISONING
   Catching a database exception in Python does not undo what it did to the
   TRANSACTION. PostgreSQL aborts the whole transaction on a failed statement,
   and every statement after it fails with InFailedSqlTransaction until someone
   rolls back. The producer caught its own failed read and carried on — so from
   that point it was issuing a sequence of guaranteed failures, and so was the
   NEXT action in the recommend plan. One unreadable fact for Action A silently
   cost Action B its evaluation too.

   The fix is a SAVEPOINT around each read that is allowed to fail, and another
   around each action's evaluation, so a rollback is never wider than the
   statement that caused it. Specifically NOT a `connection.rollback()`: that is
   the connection-wide undo, and this code runs inside a request doing other
   work.

2. PRODUCER-ONLY READ FAILURE
   This producer reads MORE facts than Screen 8's gate does — the gate never
   touches `threat_detections` or `asset_authorized_issuances`. So a read failure
   HERE left the gate seeing a perfectly healthy chain, finding no evaluation,
   and reporting POLICY_EVALUATION_MISSING forever: the only thing that could
   clear that state was the row the producer had declined to write, and no
   operator action produced it.

   The fix records a deterministic fail-closed DENY naming the fact that could
   not be read. Never an ALLOW, and never silence.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_screen07_enforcement_transaction_isolation_postgres.py

Skipped entirely when that DSN is absent, so the default suite stays hermetic.
"""

from __future__ import annotations

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
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

_needs_pg = pytest.mark.skipif(
    not (_DSN and _PSQL),
    reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have '
           'psql on PATH to run the enforcement transaction-isolation harness',
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'


def _reset_schema() -> None:
    proc = subprocess.run(
        [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1',
         '-c', 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'],
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f'schema reset failed:\n{proc.stdout}\n{proc.stderr}'


def _apply_all_migrations() -> None:
    for path in sorted(_MIGRATIONS.glob('*.sql')):
        proc = subprocess.run(
            [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', '-f', str(path)],
            capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, f'{path.name} failed:\n{proc.stdout}\n{proc.stderr}'


class _Request:
    def __init__(self, token: str, workspace_id: str, path: str):
        self.headers = {'authorization': f'Bearer {token}', 'x-workspace-id': workspace_id,
                        'user-agent': 'enforcement-isolation-harness'}
        self.scope = {'path': path, 'type': 'http'}
        self.client = type('C', (), {'host': '127.0.0.1'})()
        self.url = type('U', (), {'path': path})()
        self.method = 'POST'
        self.query_params: dict[str, str] = {}


@pytest.fixture(scope='module')
def live():
    _reset_schema()
    _apply_all_migrations()
    previous = {k: os.environ.get(k) for k in ('DATABASE_URL', 'LIVE_MODE_ENABLED', 'APP_MODE')}
    os.environ.update({'DATABASE_URL': _DSN, 'LIVE_MODE_ENABLED': 'true', 'APP_MODE': 'live'})
    os.environ.setdefault('AUTH_TOKEN_SECRET', 'x' * 48)
    os.environ.setdefault('TOKEN_SECRET', 'x' * 48)
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _reset_schema()


def _seed_chain(conn, pilot, *, with_detection: bool = True) -> dict:
    """A workspace with one governed asset, an alert, an incident, and a detection."""
    workspace_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    asset_id, target_id, policy_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    alert_id, incident_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        'INSERT INTO users (id, email, password_hash, full_name, session_version, created_at, updated_at) '
        "VALUES (%s, %s, 'x', 'Operator', 1, NOW(), NOW())",
        (user_id, f'{user_id[:8]}@example.test'),
    )
    conn.execute(
        'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) '
        "VALUES (%s, 'Isolation WS', %s, %s, NOW())",
        (workspace_id, f'iso-{workspace_id[:8]}', user_id),
    )
    conn.execute(
        'INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) '
        "VALUES (%s, %s, %s, 'owner', NOW())",
        (str(uuid.uuid4()), workspace_id, user_id),
    )
    conn.execute('UPDATE users SET current_workspace_id = %s WHERE id = %s', (workspace_id, user_id))
    token = pilot.create_access_token(user_id, 1)
    conn.execute(
        'INSERT INTO auth_sessions (id, user_id, session_token_hash, expires_at, created_at, updated_at) '
        'VALUES (%s, %s, %s, %s, NOW(), NOW())',
        (str(uuid.uuid4()), user_id, pilot._auth_token_hash(token),
         datetime.now(timezone.utc) + timedelta(hours=8)),
    )
    conn.execute(
        'INSERT INTO assets (id, workspace_id, name, asset_type, chain_network, identifier, '
        "created_by_user_id, updated_by_user_id) VALUES (%s, %s, 'Treasury Token', "
        "'tokenized_treasury', 'base', %s, %s, %s)",
        (asset_id, workspace_id, f'0x{asset_id[:8]}', user_id, user_id),
    )
    conn.execute(
        'INSERT INTO targets (id, workspace_id, name, target_type, chain_network, asset_id, '
        "created_by_user_id, updated_by_user_id) VALUES (%s, %s, 'Vault', 'contract', 'base', %s, %s, %s)",
        (target_id, workspace_id, asset_id, user_id, user_id),
    )
    conn.execute(
        '''INSERT INTO governance_policies (
               id, workspace_id, policy_key, name, operation, status, version, asset_id,
               required_business_event, settlement_requirement, maximum_daily_amount_usd,
               required_roles, violation_action, created_by_user_id, updated_by_user_id,
               created_at, updated_at
           ) VALUES (%s, %s, %s, 'RWA Mint Policy', 'MINT', 'ACTIVE', 1, %s,
                     NULL, NULL, %s, '[]'::jsonb, 'DENY', %s, %s, NOW(), NOW())''',
        (policy_id, workspace_id, f'POL-{workspace_id[:8]}', asset_id,
         Decimal('50000000.00'), user_id, user_id),
    )
    conn.execute(
        '''INSERT INTO alerts (id, workspace_id, user_id, target_id, alert_type, severity, status,
               title, source_service, summary, created_at, updated_at)
           VALUES (%s, %s, %s, %s, 'unauthorized_mint', 'high', 'open', 'Unauthorized mint detected',
                   'threat-engine', 'Unexplained supply increase', NOW(), NOW())''',
        (alert_id, workspace_id, user_id, target_id),
    )
    conn.execute(
        '''INSERT INTO incidents (id, workspace_id, user_id, event_type, severity, status, summary,
               title, target_id, source_alert_id, created_at, updated_at)
           VALUES (%s, %s, %s, 'incident.unauthorized_mint', 'high', 'open',
                   'Unexplained supply increase', 'Unauthorized mint detected', %s, %s, NOW(), NOW())''',
        (incident_id, workspace_id, user_id, target_id, alert_id),
    )
    conn.execute('UPDATE alerts SET incident_id = %s WHERE id = %s', (incident_id, alert_id))
    if with_detection:
        conn.execute(
            '''INSERT INTO threat_detections (
                   id, workspace_id, cluster_key, detection_type, severity, status, title,
                   explanation, operation, observed_amount, primary_asset_id, tx_hash,
                   provenance, linked_alert_id, linked_incident_id,
                   detected_at, created_at, updated_at
               ) VALUES (%s, %s, %s, 'supply_variance', 'high', 'open',
                         'Unexplained supply variance',
                         'Observed supply exceeds the authorized total.',
                         'mint', %s, %s, %s, '{}'::jsonb, %s, %s, NOW(), NOW(), NOW())''',
            (str(uuid.uuid4()), workspace_id, f'supply-variance-{asset_id}',
             Decimal('1000000'), asset_id, '0x' + 'ab' * 32, alert_id, incident_id),
        )
    conn.commit()
    return {
        'workspace_id': workspace_id, 'user_id': user_id, 'token': token,
        'asset_id': asset_id, 'policy_id': policy_id,
        'alert_id': alert_id, 'incident_id': incident_id,
    }


def _recommend(pilot, seed: dict) -> dict:
    return pilot.recommend_response_action_for_incident(
        seed['incident_id'],
        _Request(seed['token'], seed['workspace_id'],
                 f"/incidents/{seed['incident_id']}/response-actions/recommend"),
    )


def _stored(workspace_id: str) -> dict:
    from psycopg.rows import dict_row
    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        actions = conn.execute(
            'SELECT id, action_type FROM response_actions WHERE workspace_id = %s ORDER BY created_at ASC',
            (workspace_id,),
        ).fetchall()
        evaluations = conn.execute(
            '''SELECT id, decision, simulation, reason_codes, policy_id,
                      input_snapshot->>'response_action_id' AS snapshot_action_id
               FROM governance_policy_evaluations WHERE workspace_id = %s''',
            (workspace_id,),
        ).fetchall()
        incident = conn.execute(
            'SELECT id, status FROM incidents WHERE workspace_id = %s', (workspace_id,),
        ).fetchone()
    return {'actions': actions, 'evaluations': evaluations, 'incident': incident}


# --------------------------------------------------------------------------
# §18 — one action's evaluation failure must not poison the next
# --------------------------------------------------------------------------
@_needs_pg
def test_one_actions_evaluation_failure_does_not_poison_the_rest_of_the_plan(live, monkeypatch):
    """Action A's evaluation-local SQL fails. Action B must still be evaluated.

    The failure is injected as a genuinely invalid statement issued inside A's
    evaluation, which is what really aborts a PostgreSQL transaction — not a
    Python exception raised beside it.
    """
    from psycopg.rows import dict_row

    from services.api.app import pilot
    from services.api.app.domains.governance_policy import enforcement

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        seed = _seed_chain(conn, pilot)

    real_resolve = enforcement.resolve_action_facts
    poisoned: list[str] = []

    def _resolve_with_a_real_sql_failure(connection, *, workspace_id, action):
        action_id = str(action.get('id') or '')
        # Poison exactly ONE action — the first the plan reaches.
        if not poisoned:
            poisoned.append(action_id)
            try:
                # A real aborting statement, inside the evaluation's own scope.
                connection.execute('SELECT * FROM a_table_that_does_not_exist')
            except Exception:
                pass
        return real_resolve(connection, workspace_id=workspace_id, action=action)

    monkeypatch.setattr(enforcement, 'resolve_action_facts', _resolve_with_a_real_sql_failure)
    result = _recommend(pilot, seed)
    monkeypatch.undo()

    assert poisoned, 'the failure was never injected'
    stored = _stored(seed['workspace_id'])

    # -- both response actions stay persisted -------------------------------
    assert len(stored['actions']) == len(result['action_types'])
    assert len(stored['actions']) >= 2, 'this plan should contain more than one action'

    # -- incident state was not rolled back ---------------------------------
    assert stored['incident'] is not None
    assert stored['incident']['status'] == 'open'

    # -- Action B evaluated normally despite A's failure ---------------------
    evaluated = {row['snapshot_action_id'] for row in stored['evaluations']}
    others = [str(a['id']) for a in stored['actions'] if str(a['id']) != poisoned[0]]
    assert others, 'no sibling action to prove isolation with'
    unevaluated = [a for a in others if a not in evaluated]
    assert unevaluated == [], (
        f'actions {unevaluated} lost their evaluation because another action failed first'
    )

    # -- every evaluation that WAS written is a real enforcement row ---------
    for row in stored['evaluations']:
        assert row['simulation'] is False
        assert row['decision'] in ('ALLOW', 'DENY')

    # -- the diagnostic payload reports what happened per action ------------
    diagnostics = result['enforcement_evaluations']
    assert set(diagnostics) == {str(a['id']) for a in stored['actions']}
    assert set(diagnostics.values()) <= set(enforcement.STATUSES)


@_needs_pg
def test_a_previously_recorded_evaluation_survives_a_later_failure(live, monkeypatch):
    """A's successful evaluation must not be rolled back by B's failure."""
    from psycopg.rows import dict_row

    from services.api.app import pilot
    from services.api.app.domains.governance_policy import enforcement

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        seed = _seed_chain(conn, pilot)

    real_resolve = enforcement.resolve_action_facts
    seen: list[str] = []

    def _fail_on_the_second_action(connection, *, workspace_id, action):
        seen.append(str(action.get('id') or ''))
        if len(seen) == 2:
            try:
                connection.execute('SELECT * FROM another_missing_table')
            except Exception:
                pass
        return real_resolve(connection, workspace_id=workspace_id, action=action)

    monkeypatch.setattr(enforcement, 'resolve_action_facts', _fail_on_the_second_action)
    _recommend(pilot, seed)
    monkeypatch.undo()

    assert len(seen) >= 2
    poisoned = seen[1]
    stored = _stored(seed['workspace_id'])
    evaluated = {row['snapshot_action_id'] for row in stored['evaluations']}
    # The FIRST action was evaluated before the failure and must still be there.
    assert seen[0] in evaluated, "an earlier action's recorded evaluation was rolled back"
    # Every action EXCEPT the poisoned one still got its own evaluation. The
    # poisoned one records nothing, which is the honest outcome for an evaluation
    # whose own facts could not be read — what must not happen is that failure
    # spreading to the siblings.
    siblings = {str(a['id']) for a in stored['actions']} - {poisoned}
    assert siblings - evaluated == set(), (
        'a later action lost its evaluation because an earlier one failed'
    )


# --------------------------------------------------------------------------
# §17 — the producer-only read failure
# --------------------------------------------------------------------------
@_needs_pg
def test_a_producer_only_detection_read_failure_is_explicitly_fail_closed(live, monkeypatch):
    """The exact production asymmetry, reproduced.

    The asset resolves through alerts -> targets, so Screen 8's gate needs no
    detection at all and sees a healthy chain. Only the PRODUCER reads
    `threat_detections`, and here that read fails.
    """
    from psycopg.rows import dict_row

    from services.api.app import pilot
    from services.api.app.domains.governance_policy import enforcement

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        seed = _seed_chain(conn, pilot)

    # The failure is injected into the REAL read: `resolve_threat_detection` runs
    # its own statement, inside its own savepoint, and that statement genuinely
    # fails. The table still EXISTS, so `to_regclass` finds it and the code takes
    # the same path it does in production — this is an unreadable fact, not an
    # absent one, and the two must not be confused.
    monkeypatch.setattr(
        enforcement, '_DETECTION_COLUMNS',
        'id, operation, observed_amount, primary_asset_id, tx_hash, provenance, '
        'column_that_does_not_exist',
    )
    result = _recommend(pilot, seed)
    monkeypatch.undo()

    stored = _stored(seed['workspace_id'])

    # -- the response actions remain persisted ------------------------------
    assert len(stored['actions']) == len(result['action_types'])

    # -- the failure did NOT leave the action silently unevaluated ----------
    evaluated = {row['snapshot_action_id'] for row in stored['evaluations']}
    assert evaluated == {str(a['id']) for a in stored['actions']}, (
        'a producer-only read failure left actions with no evaluation at all'
    )

    # -- and what was recorded is an explicit fail-closed refusal -----------
    for row in stored['evaluations']:
        assert row['simulation'] is False
        assert row['decision'] == 'DENY'
        codes = list(row['reason_codes'])
        assert 'AUTHORITATIVE_FACTS_UNAVAILABLE' in codes
        assert 'DETECTION_FACTS_UNAVAILABLE' in codes
        # No policy reached this verdict, so none is named as having reached it.
        assert row['policy_id'] is None

    # -- Screen 8 never becomes AUTHORIZED ----------------------------------
    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        for action in stored['actions']:
            action_row = pilot._json_safe_value(dict(conn.execute(
                'SELECT * FROM response_actions WHERE id = %s::uuid AND workspace_id = %s',
                (str(action['id']), seed['workspace_id']),
            ).fetchone()))
            gate = pilot.response_action_execution_gate(
                conn, action_row, workspace_id=seed['workspace_id'],
                workspace_context={'workspace_id': seed['workspace_id'], 'role': 'owner'},
            )
            assert gate['can_execute'] is False
            assert gate['decision'] != 'AUTHORIZED'
            assert gate['policy_decision'] == 'DENY'
            # The permanent silent state is gone.
            assert 'POLICY_EVALUATION_MISSING' not in gate['reason_codes']
            assert 'DETECTION_FACTS_UNAVAILABLE' in gate['reason_codes']

    assert set(result['enforcement_evaluations'].values()) == {
        enforcement.STATUS_RECORDED_FAIL_CLOSED,
    }


@_needs_pg
def test_the_refusal_is_replaced_by_a_real_verdict_once_the_fact_is_readable(live, monkeypatch):
    """A transient outage must not freeze the action on a stale refusal.

    The digest covers WHICH facts were unreadable, so a re-evaluation after the
    outage clears produces a NEW decision rather than idempotently returning the
    refusal forever.
    """
    from psycopg.rows import dict_row

    from services.api.app import pilot
    from services.api.app.domains.governance_policy import enforcement

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        seed = _seed_chain(conn, pilot)

    monkeypatch.setattr(
        enforcement, '_DETECTION_COLUMNS',
        'id, operation, observed_amount, primary_asset_id, tx_hash, provenance, '
        'column_that_does_not_exist',
    )
    _recommend(pilot, seed)
    monkeypatch.undo()

    during_outage = _stored(seed['workspace_id'])
    assert {r['decision'] for r in during_outage['evaluations']} == {'DENY'}

    # The outage clears. Recommending again re-evaluates every action in the plan.
    _recommend(pilot, seed)
    after = _stored(seed['workspace_id'])

    # A real, policy-backed verdict now exists for every action.
    by_action: dict[str, list[dict]] = {}
    for row in after['evaluations']:
        by_action.setdefault(str(row['snapshot_action_id']), []).append(row)
    for action in after['actions']:
        rows = by_action[str(action['id'])]
        assert any(r['policy_id'] is not None for r in rows), (
            'the action stayed frozen on the fail-closed refusal after the fact became readable'
        )
        assert any(
            'DETECTION_FACTS_UNAVAILABLE' not in list(r['reason_codes']) for r in rows
        )
