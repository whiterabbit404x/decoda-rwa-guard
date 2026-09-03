"""Screen 8 security regression: an ALLOW belongs to ONE response action.

The fail-open this pins
-----------------------
``response_gate.service.latest_policy_evaluation`` resolves an action's
enforcement decision by matching on the response action id OR the canonical
event id OR the incident id OR the asset id. The last three are SHARED
lifecycle identifiers: two actions recommended for one incident, and two
actions touching one asset, match the same rows.

So an Action B that was never evaluated could be shown Action A's verdict. When
that verdict was an ALLOW, B reached AUTHORIZED on an evaluation that had
never examined it — a policy decision reached about a DIFFERENT operation,
silently borrowed to unlock this one.

An ALLOW authorizes the action it was reached FOR. Nothing else.

Both directions are pinned below:

  * Scenario 1 — same incident, so the ``incident_id`` match applies.
  * Scenario 2 — same asset, different incident, so the ``asset_id`` match does.

and the two properties that must survive the fix:

  * an action WITH its own ALLOW is still authorized by it (the fix must not
    close the legitimate path);
  * a SHARED DENY still blocks, because fail-closed is conservative in the
    direction that denies.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_screen08_inherited_allow_postgres.py

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
    """The actual driver, not the conftest stub (see the sibling harnesses)."""
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

_needs_pg = pytest.mark.skipif(
    not (_DSN and _PSQL),
    reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have '
           'psql on PATH to run the Screen 8 inherited-ALLOW security harness',
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


# --------------------------------------------------------------------------
# Fixture builders — canonical rows only, written directly so the scenario is
# stated exactly rather than coaxed out of the producer.
# --------------------------------------------------------------------------
def _seed_tenant(conn, pilot) -> tuple[str, str, str]:
    workspace_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        'INSERT INTO users (id, email, password_hash, full_name, session_version, created_at, updated_at) '
        "VALUES (%s, %s, 'x', 'Operator', 1, NOW(), NOW())",
        (user_id, f'{user_id[:8]}@example.test'),
    )
    conn.execute(
        'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) '
        "VALUES (%s, 'Inherited ALLOW WS', %s, %s, NOW())",
        (workspace_id, f'inh-{workspace_id[:8]}', user_id),
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
    return workspace_id, user_id, token


def _seed_asset(conn, *, workspace_id: str, user_id: str) -> tuple[str, str]:
    asset_id, target_id = str(uuid.uuid4()), str(uuid.uuid4())
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
    return asset_id, target_id


def _seed_policy(conn, *, workspace_id: str, user_id: str, asset_id: str) -> str:
    policy_id = str(uuid.uuid4())
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
    return policy_id


def _seed_incident(conn, *, workspace_id: str, user_id: str, target_id: str) -> tuple[str, str]:
    alert_id, incident_id = str(uuid.uuid4()), str(uuid.uuid4())
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
    return alert_id, incident_id


def _seed_action(conn, *, workspace_id: str, user_id: str, incident_id: str, alert_id: str,
                 action_type: str = 'freeze_wallet') -> str:
    action_id = str(uuid.uuid4())
    conn.execute(
        '''INSERT INTO response_actions (
               id, workspace_id, incident_id, alert_id, action_type, mode, status,
               execution_state, execution_metadata, created_by_user_id, created_at
           ) VALUES (%s, %s, %s::uuid, %s::uuid, %s, 'recommended', 'pending',
                     'simulated', '{}'::jsonb, %s, NOW())''',
        (action_id, workspace_id, incident_id, alert_id, action_type, user_id),
    )
    return action_id


def _seed_evaluation(conn, *, workspace_id: str, policy_id: str | None, asset_id: str | None,
                     incident_id: str | None, response_action_id: str | None,
                     decision: str, canonical_event_id: str | None = None) -> str:
    """One ENFORCEMENT evaluation (simulation = FALSE), stamped with the action it judged."""
    evaluation_id = str(uuid.uuid4())
    conn.execute(
        '''INSERT INTO governance_policy_evaluations (
               id, workspace_id, policy_id, policy_key, policy_version, asset_id, incident_id,
               canonical_event_id, operation, decision, reason_codes, required_approvals, checks,
               amount_usd, input_snapshot, simulation, engine_version, evaluated_at, required_roles
           ) VALUES (%s::uuid, %s, %s::uuid, %s, %s, %s::uuid, %s::uuid, %s, 'MINT', %s,
                     %s::jsonb, '[]'::jsonb, '[]'::jsonb, NULL, %s::jsonb, FALSE,
                     'policy-engine-v1', NOW(), '[]'::jsonb)''',
        (evaluation_id, workspace_id, policy_id, 'POL-TEST', 1, asset_id, incident_id,
         canonical_event_id, decision,
         json.dumps(['POLICY_SATISFIED'] if decision == 'ALLOW' else ['POLICY_DENIED']),
         json.dumps({'response_action_id': response_action_id} if response_action_id else {})),
    )
    return evaluation_id


def _gate_for(conn, pilot, *, workspace_id: str, action_id: str) -> dict:
    stored = pilot._json_safe_value(dict(conn.execute(
        'SELECT * FROM response_actions WHERE id = %s::uuid AND workspace_id = %s',
        (action_id, workspace_id),
    ).fetchone()))
    return pilot.response_action_execution_gate(
        conn, stored, workspace_id=workspace_id,
        workspace_context={'workspace_id': workspace_id, 'role': 'owner'},
    )


# --------------------------------------------------------------------------
# Scenario 1 — same incident
# --------------------------------------------------------------------------
@_needs_pg
def test_action_b_cannot_inherit_action_a_allow_through_a_shared_incident(live):
    """Action A holds an ALLOW. Action B, never evaluated, must not borrow it."""
    from psycopg.rows import dict_row

    from services.api.app import pilot

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        workspace_id, user_id, _ = _seed_tenant(conn, pilot)
        asset_id, target_id = _seed_asset(conn, workspace_id=workspace_id, user_id=user_id)
        policy_id = _seed_policy(conn, workspace_id=workspace_id, user_id=user_id, asset_id=asset_id)
        alert_id, incident_id = _seed_incident(
            conn, workspace_id=workspace_id, user_id=user_id, target_id=target_id,
        )
        action_a = _seed_action(conn, workspace_id=workspace_id, user_id=user_id,
                                incident_id=incident_id, alert_id=alert_id,
                                action_type='freeze_wallet')
        action_b = _seed_action(conn, workspace_id=workspace_id, user_id=user_id,
                                incident_id=incident_id, alert_id=alert_id,
                                action_type='pause_mint_redeem')
        # ONLY Action A is evaluated. The row names A in its snapshot and carries
        # the shared incident/asset ids every action on this incident matches.
        allow_id = _seed_evaluation(
            conn, workspace_id=workspace_id, policy_id=policy_id, asset_id=asset_id,
            incident_id=incident_id, response_action_id=action_a, decision='ALLOW',
        )
        conn.commit()

        gate_a = _gate_for(conn, pilot, workspace_id=workspace_id, action_id=action_a)
        gate_b = _gate_for(conn, pilot, workspace_id=workspace_id, action_id=action_b)

    # -- A keeps its own ALLOW: the fix must not close the legitimate path ----
    assert gate_a['policy_decision'] == 'ALLOW'
    assert str(gate_a['evaluation_id']) == allow_id

    # -- B must NOT be authorized by a verdict reached for A -----------------
    assert gate_b['policy_decision'] != 'ALLOW', (
        'Action B inherited Action A\'s ALLOW through the shared incident id'
    )
    assert gate_b['policy_decision'] == 'NOT_EVALUATED'
    assert gate_b['decision'] != 'AUTHORIZED'
    assert gate_b['can_execute'] is False
    assert 'POLICY_EVALUATION_MISSING' in gate_b['reason_codes']
    # And B is never handed A's evaluation as if it were its own.
    assert gate_b['evaluation_id'] != allow_id


# --------------------------------------------------------------------------
# Scenario 2 — same asset, different incident
# --------------------------------------------------------------------------
@_needs_pg
def test_action_b_cannot_inherit_an_allow_through_a_shared_asset(live):
    """The asset id is shared across incidents; an ALLOW still travels with neither."""
    from psycopg.rows import dict_row

    from services.api.app import pilot

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        workspace_id, user_id, _ = _seed_tenant(conn, pilot)
        asset_id, target_id = _seed_asset(conn, workspace_id=workspace_id, user_id=user_id)
        policy_id = _seed_policy(conn, workspace_id=workspace_id, user_id=user_id, asset_id=asset_id)
        alert_a, incident_a = _seed_incident(
            conn, workspace_id=workspace_id, user_id=user_id, target_id=target_id,
        )
        alert_b, incident_b = _seed_incident(
            conn, workspace_id=workspace_id, user_id=user_id, target_id=target_id,
        )
        action_a = _seed_action(conn, workspace_id=workspace_id, user_id=user_id,
                                incident_id=incident_a, alert_id=alert_a)
        action_b = _seed_action(conn, workspace_id=workspace_id, user_id=user_id,
                                incident_id=incident_b, alert_id=alert_b)
        allow_id = _seed_evaluation(
            conn, workspace_id=workspace_id, policy_id=policy_id, asset_id=asset_id,
            incident_id=incident_a, response_action_id=action_a, decision='ALLOW',
        )
        conn.commit()

        gate_b = _gate_for(conn, pilot, workspace_id=workspace_id, action_id=action_b)

    assert gate_b['policy_decision'] != 'ALLOW', (
        'Action B inherited an ALLOW through the shared asset id'
    )
    assert gate_b['decision'] != 'AUTHORIZED'
    assert gate_b['can_execute'] is False
    assert gate_b['evaluation_id'] != allow_id


# --------------------------------------------------------------------------
# The conservative direction stays as it is.
# --------------------------------------------------------------------------
@_needs_pg
def test_a_shared_deny_still_blocks_a_sibling_action(live):
    """Fail-closed is asymmetric on purpose: a shared DENY may still deny."""
    from psycopg.rows import dict_row

    from services.api.app import pilot

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        workspace_id, user_id, _ = _seed_tenant(conn, pilot)
        asset_id, target_id = _seed_asset(conn, workspace_id=workspace_id, user_id=user_id)
        policy_id = _seed_policy(conn, workspace_id=workspace_id, user_id=user_id, asset_id=asset_id)
        alert_id, incident_id = _seed_incident(
            conn, workspace_id=workspace_id, user_id=user_id, target_id=target_id,
        )
        action_a = _seed_action(conn, workspace_id=workspace_id, user_id=user_id,
                                incident_id=incident_id, alert_id=alert_id)
        action_b = _seed_action(conn, workspace_id=workspace_id, user_id=user_id,
                                incident_id=incident_id, alert_id=alert_id,
                                action_type='pause_mint_redeem')
        _seed_evaluation(
            conn, workspace_id=workspace_id, policy_id=policy_id, asset_id=asset_id,
            incident_id=incident_id, response_action_id=action_a, decision='DENY',
        )
        conn.commit()

        gate_b = _gate_for(conn, pilot, workspace_id=workspace_id, action_id=action_b)

    assert gate_b['can_execute'] is False
    assert gate_b['decision'] != 'AUTHORIZED'
    assert gate_b['policy_decision'] in ('DENY', 'NOT_EVALUATED')
