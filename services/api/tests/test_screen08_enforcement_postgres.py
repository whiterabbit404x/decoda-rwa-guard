"""Screen 8 enforcement, end to end, against a REAL PostgreSQL (opt-in).

The fake-connection suites cover the decision logic and the statement shapes.
This one covers the thing only a real database and the real endpoints can show:
that a response action created the way the product creates one actually acquires
a ``simulation = FALSE`` governance evaluation, and that Screen 8's gate reads
back the row that was written.

The chain exercised is the production one, with no fixture standing in for a
step:

    Screen 3 reconciliation  (reconciliation.evaluate)
      -> canonical event      (asset_integrity.emit_canonical_event)
      -> alert                (threat_detection.investigate_detection)
      -> incident
      -> response action      (pilot.create_enforcement_action)
      -> enforcement decision (governance_policy.enforcement, simulation = FALSE)
      -> execution gate       (pilot.response_action_execution_gate)

Before the operation reached ``threat_detections.operation`` this chain ended at
0 evaluations and a gate reporting NOT_EVALUATED / LOCKED /
POLICY_EVALUATION_MISSING, with no path to an authorization: the on-demand
endpoint resolves its facts the same way.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_screen08_enforcement_postgres.py

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
           'psql on PATH to run the Screen 8 enforcement real-schema harness',
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
NOW = datetime(2026, 9, 1, 10, 42, 0, tzinfo=timezone.utc)


def _apply_all_migrations() -> None:
    for path in sorted(_MIGRATIONS.glob('*.sql')):
        proc = subprocess.run(
            [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', '-f', str(path)],
            capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, f'{path.name} failed:\n{proc.stdout}\n{proc.stderr}'


class _Request:
    """The minimal request surface the create endpoint reads."""

    def __init__(self, token: str, workspace_id: str):
        self.headers = {'authorization': f'Bearer {token}', 'x-workspace-id': workspace_id,
                        'user-agent': 'enforcement-harness'}
        self.scope = {'path': '/response/actions', 'type': 'http'}
        self.client = type('C', (), {'host': '127.0.0.1'})()
        self.url = type('U', (), {'path': '/response/actions'})()
        self.method = 'POST'
        self.query_params: dict[str, str] = {}


@pytest.fixture(scope='module')
def live(monkeypatch_module=None):
    """Point the app at the scratch database and put it in live mode."""
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


def _seed_tenant(conn, pilot) -> tuple[str, str, str]:
    workspace_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        'INSERT INTO users (id, email, password_hash, full_name, session_version, created_at, updated_at) '
        "VALUES (%s, %s, 'x', 'Operator', 1, NOW(), NOW())",
        (user_id, f'{user_id[:8]}@example.test'),
    )
    conn.execute(
        'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) '
        "VALUES (%s, 'Enforcement WS', %s, %s, NOW())",
        (workspace_id, f'enf-{workspace_id[:8]}', user_id),
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


def _run_chain(*, policy_constraints: str) -> dict:
    """One full production chain. Returns the persisted rows and the gate."""
    from psycopg.rows import dict_row

    from services.api.app import pilot
    from services.api.app.domains.asset_integrity import reconciliation as recon
    from services.api.app.domains.asset_integrity import service as ai_service
    from services.api.app.domains.threat_detection import service as td_service

    now = pilot.utc_now()
    asset_id, target_id, policy_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        workspace_id, user_id, token = _seed_tenant(conn, pilot)
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
        # 'strict' requires a settled subscription; 'capped' constrains the amount
        # alone. Both are policies a workspace can author on Screen 11, and which
        # one governs is what decides the honest verdict for the same facts.
        business_event, settlement, cap = (
            ('SUBSCRIPTION', 'CLEARED', None) if policy_constraints == 'strict'
            else (None, None, '50000000.00')
        )
        conn.execute(
            '''INSERT INTO governance_policies (
                   id, workspace_id, policy_key, name, operation, status, version, asset_id,
                   required_business_event, settlement_requirement, maximum_daily_amount_usd,
                   required_roles, violation_action, created_by_user_id, updated_by_user_id,
                   created_at, updated_at
               ) VALUES (%s, %s, %s, 'RWA Mint Policy', 'MINT', 'ACTIVE', 1, %s,
                         %s, %s, %s, '[]'::jsonb, 'DENY', %s, %s, NOW(), NOW())''',
            (policy_id, workspace_id, f'POL-{workspace_id[:8]}', asset_id,
             business_event, settlement, cap, user_id, user_id),
        )

        # -- Screen 3: the real reconciliation engine on a real supply variance.
        result = recon.evaluate(
            onchain=recon.OnChainObservation(
                total_supply=Decimal('11000000'), observed_at=now, block_number=1234,
                last_delta=Decimal('1000000'), last_delta_operation='mint', last_delta_at=now,
                evidence_source='live'),
            authoritative=recon.AuthoritativeState(
                expected_total_supply=Decimal('10000000'), observed_at=now,
                source_name='transfer-agent', evidence_source='live'),
            authorizations=(), now=now,
        )
        assert result.status == recon.UNEXPLAINED_VARIANCE
        # The operation the engine resolved, carried rather than dropped.
        assert result.operation == 'mint'

        evidence_refs = ai_service.build_evidence_refs(
            onchain_row=None, authoritative_row=None, authorization_rows=[], result=result,
        )
        event_id = ai_service.emit_canonical_event(
            conn, workspace_id=workspace_id, asset_id=asset_id, asset_name='Treasury Token',
            result=result,
            onchain_row={'tx_hash': '0x' + 'cd' * 32, 'evidence_source': 'live'},
            authoritative_row=None, evidence_refs=evidence_refs,
            explanation='Unexplained supply variance.', now=now,
        )
        conn.commit()
        detection = conn.execute(
            'SELECT operation, primary_asset_id, tx_hash FROM threat_detections WHERE id = %s',
            (event_id,),
        ).fetchone()
        # The column the governing-policy lookup is keyed on.
        assert detection['operation'] == 'mint'

        # -- Screen 5: open the investigation, which raises the canonical alert.
        alert_id = td_service.investigate_detection(
            conn, workspace_id=workspace_id, detection_id=str(event_id),
            user_id=user_id, now=now, commit=True,
        )['alert_id']

        # -- Screen 7: the incident the alert belongs to.
        incident_id = str(uuid.uuid4())
        conn.execute(
            'INSERT INTO incidents (id, workspace_id, user_id, event_type, severity, status, summary, '
            'title, target_id, source_alert_id, created_at, updated_at) VALUES (%s, %s, %s, '
            "'incident.unexplained_variance', 'high', 'open', 'Unexplained supply variance', "
            "'Unexplained supply variance', %s, %s, NOW(), NOW())",
            (incident_id, workspace_id, user_id, target_id, alert_id),
        )
        conn.execute('UPDATE alerts SET incident_id = %s, target_id = %s WHERE id = %s',
                     (incident_id, target_id, alert_id))
        conn.execute('UPDATE threat_detections SET linked_incident_id = %s WHERE id = %s',
                     (incident_id, event_id))
        conn.commit()

    # -- Screen 8: create the response action through the real endpoint.
    action_id = pilot.create_enforcement_action(
        {'action_type': 'pause_mint_redeem', 'incident_id': incident_id, 'alert_id': alert_id,
         'mode': 'simulated', 'status': 'pending'},
        _Request(token, workspace_id),
    )['id']

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        evaluations = conn.execute(
            '''SELECT id, policy_id, policy_key, policy_version, decision, simulation, reason_codes,
                      operation, asset_id, incident_id, canonical_event_id, amount_usd,
                      input_snapshot->>'response_action_id' AS snapshot_action_id
               FROM governance_policy_evaluations WHERE workspace_id = %s''',
            (workspace_id,),
        ).fetchall()
        action = pilot._json_safe_value(dict(conn.execute(
            'SELECT * FROM response_actions WHERE id = %s::uuid AND workspace_id = %s',
            (action_id, workspace_id),
        ).fetchone()))
        gate = pilot.response_action_execution_gate(
            conn, action, workspace_id=workspace_id,
            workspace_context={'workspace_id': workspace_id, 'role': 'owner'},
        )
    return {
        'workspace_id': workspace_id, 'asset_id': asset_id, 'policy_id': policy_id,
        'incident_id': incident_id, 'action_id': action_id, 'event_id': str(event_id),
        'evaluations': evaluations, 'gate': gate,
    }


@_needs_pg
def test_a_new_response_action_acquires_a_real_enforcement_deny(live):
    """An unauthorized mint reaches Screen 8 as a DENY, not as a missing evaluation."""
    out = _run_chain(policy_constraints='strict')

    assert len(out['evaluations']) == 1, 'exactly one enforcement evaluation must be persisted'
    row = out['evaluations'][0]
    # It is an ENFORCEMENT row, not a Screen 11 what-if.
    assert row['simulation'] is False
    assert row['decision'] == 'DENY'
    # No authorization backs this mint, and none was substituted for it.
    assert sorted(row['reason_codes']) == ['BUSINESS_EVENT_MISSING', 'SETTLEMENT_STATE_UNKNOWN']
    # The canonical identifiers match the action it governs.
    assert row['operation'] == 'MINT'
    assert str(row['asset_id']) == out['asset_id']
    assert str(row['incident_id']) == out['incident_id']
    assert row['snapshot_action_id'] == out['action_id']
    # The policy and the version that decided are recorded.
    assert str(row['policy_id']) == out['policy_id']
    assert row['policy_version'] == 1

    # And Screen 8 reads back THAT row.
    gate = out['gate']
    assert gate['policy_decision'] == 'DENY'
    assert gate['decision'] == 'DENIED'
    assert gate['can_execute'] is False
    assert str(gate['evaluation_id']) == str(row['id'])
    assert str(gate['policy_id']) == out['policy_id']
    assert gate['policy_version'] == 1


@_needs_pg
def test_a_new_response_action_acquires_a_real_enforcement_allow(live):
    """The same chain under a policy the observed facts satisfy reaches ALLOW.

    The execution gate stays LOCKED on the human quorum and the adapter — a
    policy ALLOW is one input to it, never the whole of it.
    """
    out = _run_chain(policy_constraints='capped')

    assert len(out['evaluations']) == 1
    row = out['evaluations'][0]
    assert row['simulation'] is False
    assert row['decision'] == 'ALLOW'
    assert row['reason_codes'] == ['POLICY_SATISFIED']
    assert row['operation'] == 'MINT'
    assert row['snapshot_action_id'] == out['action_id']

    gate = out['gate']
    assert gate['policy_decision'] == 'ALLOW'
    assert str(gate['evaluation_id']) == str(row['id'])
    assert gate['can_execute'] is False
    assert 'HUMAN_QUORUM_INCOMPLETE' in gate['reason_codes']


@_needs_pg
def test_re_evaluating_the_same_action_does_not_write_a_second_decision(live):
    """Idempotency holds against real SQL, not just the fake connection."""
    from psycopg.rows import dict_row

    from services.api.app import pilot

    out = _run_chain(policy_constraints='strict')
    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        status = pilot._record_response_action_enforcement_evaluation(
            conn, workspace_id=out['workspace_id'], action_id=out['action_id'],
        )
        rows = conn.execute(
            'SELECT id FROM governance_policy_evaluations WHERE workspace_id = %s',
            (out['workspace_id'],),
        ).fetchall()
    assert status == 'already_evaluated'
    assert len(rows) == 1
