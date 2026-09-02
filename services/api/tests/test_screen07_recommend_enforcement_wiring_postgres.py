"""Screen 7 "Recommend Action" -> Screen 8 enforcement wiring, on a REAL database.

The chain a customer actually walks, with the REAL endpoint at every step and no
fixture standing in for one:

    incident
      -> POST /incidents/{id}/response-actions/recommend
         (pilot.recommend_response_action_for_incident)
      -> response_actions row persisted
      -> deterministic enforcement evaluation
      -> governance_policy_evaluations row, simulation = FALSE
      -> Screen 8's execution gate reads back THAT row
      -> execution stays locked

The defect these tests pin
-------------------------
The producer asked "which ACTIVE policy governs THIS OPERATION?" while Screen 8's
gate asked the wider "is this workspace/asset inside ANY ACTIVE policy?". For an
incident opened from an ordinary operational alert — no threat detection behind
it, so no operation to match a policy against — the first returned nothing and
wrote nothing, while the second said yes and therefore reported
POLICY_EVALUATION_MISSING / LOCKED. Every recommended action was parked there
permanently: the only thing that could clear the state was the row the producer
had declined to write, and no operator action produced it.

Both halves are pinned below: the operation-bearing chain reaches a real
policy-backed verdict with the policy id and version stored, and the chain with
no resolvable operation reaches an explicit fail-closed DENY instead of silence.
Neither can reach an executable gate.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_screen07_recommend_enforcement_wiring_postgres.py

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
           'psql on PATH to run the Screen 7 recommend-action enforcement harness',
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'


def _reset_schema() -> None:
    """Return the scratch database to EMPTY.

    Run before and after this module. The migration set is not re-appliable over
    data: several migrations tighten a CHECK constraint that later migrations
    widen again (0056 then 0065 on ``response_actions.execution_state``), so a
    row this harness leaves behind makes the NEXT harness's migration run fail on
    a constraint that is only briefly narrow. Resetting on the way out keeps the
    real-schema suites independent of the order they run in.
    """
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
    """The minimal request surface the recommend endpoint reads."""

    def __init__(self, token: str, workspace_id: str, path: str):
        self.headers = {'authorization': f'Bearer {token}', 'x-workspace-id': workspace_id,
                        'user-agent': 'recommend-enforcement-harness'}
        self.scope = {'path': path, 'type': 'http'}
        self.client = type('C', (), {'host': '127.0.0.1'})()
        self.url = type('U', (), {'path': path})()
        self.method = 'POST'
        self.query_params: dict[str, str] = {}


@pytest.fixture(scope='module')
def live():
    """Point the app at the scratch database and put it in live mode."""
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


def _seed_tenant(conn, pilot) -> tuple[str, str, str]:
    workspace_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        'INSERT INTO users (id, email, password_hash, full_name, session_version, created_at, updated_at) '
        "VALUES (%s, %s, 'x', 'Operator', 1, NOW(), NOW())",
        (user_id, f'{user_id[:8]}@example.test'),
    )
    conn.execute(
        'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) '
        "VALUES (%s, 'Recommend WS', %s, %s, NOW())",
        (workspace_id, f'rec-{workspace_id[:8]}', user_id),
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


def _run_recommend_chain(*, with_detection: bool, active_policy: bool = True) -> dict:
    """One incident, recommended through the REAL endpoint. Returns what persisted.

    ``with_detection`` decides whether a Screen 5 threat detection stands behind
    the incident and names the governed operation — the single fact that decides
    whether a policy can be matched at all.
    """
    from psycopg.rows import dict_row

    from services.api.app import pilot

    now = pilot.utc_now()
    asset_id, target_id, policy_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    alert_id, incident_id = str(uuid.uuid4()), str(uuid.uuid4())

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
        if active_policy:
            # A policy the workspace really authored, capped only, so the observed
            # facts can satisfy it and the ALLOW half of the proof is reachable.
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
            # The Screen 5 detection, carrying the governed operation and linked
            # through the columns the Screen 5 writer owns.
            conn.execute(
                '''INSERT INTO threat_detections (
                       id, workspace_id, cluster_key, detection_type, severity, status, title,
                       explanation, operation, observed_amount, primary_asset_id, tx_hash,
                       provenance, linked_alert_id, linked_incident_id,
                       detected_at, created_at, updated_at
                   ) VALUES (%s, %s, %s, 'supply_variance', 'high', 'open',
                             'Unexplained supply variance',
                             'Observed supply exceeds the authorized total.',
                             'mint', %s, %s, %s, '{}'::jsonb, %s, %s, %s, NOW(), NOW())''',
                (str(uuid.uuid4()), workspace_id, f'supply-variance-{asset_id}',
                 Decimal('1000000'), asset_id, '0x' + 'ab' * 32, alert_id, incident_id, now),
            )
        conn.commit()

    # ---- THE endpoint Screen 7's "Recommend Action" calls -------------------
    result = pilot.recommend_response_action_for_incident(
        incident_id,
        _Request(token, workspace_id, f'/incidents/{incident_id}/response-actions/recommend'),
    )

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        actions = conn.execute(
            'SELECT id, workspace_id, incident_id, alert_id, action_type, mode, execution_state '
            'FROM response_actions WHERE workspace_id = %s ORDER BY created_at ASC',
            (workspace_id,),
        ).fetchall()
        evaluations = conn.execute(
            '''SELECT id, workspace_id, policy_id, policy_key, policy_version, decision, simulation,
                      reason_codes, operation, asset_id, incident_id, canonical_event_id,
                      input_snapshot->>'response_action_id' AS snapshot_action_id,
                      input_snapshot->'fact_sources' AS fact_sources
               FROM governance_policy_evaluations WHERE workspace_id = %s''',
            (workspace_id,),
        ).fetchall()
        gates, lifecycles = {}, {}
        for row in actions:
            stored = pilot._json_safe_value(dict(conn.execute(
                'SELECT * FROM response_actions WHERE id = %s::uuid AND workspace_id = %s',
                (str(row['id']), workspace_id),
            ).fetchone()))
            lifecycles[str(row['id'])] = pilot.response_action_lifecycle(stored)
            gates[str(row['id'])] = pilot.response_action_execution_gate(
                conn, stored, workspace_id=workspace_id,
                workspace_context={'workspace_id': workspace_id, 'role': 'owner'},
            )
    return {
        'workspace_id': workspace_id, 'user_id': user_id, 'token': token,
        'asset_id': asset_id, 'policy_id': policy_id if active_policy else None,
        'alert_id': alert_id, 'incident_id': incident_id, 'result': result,
        'actions': actions, 'evaluations': evaluations, 'gates': gates,
        'lifecycles': lifecycles, 'gate': gates[str(result['response_action_id'])],
    }


@_needs_pg
def test_recommend_persists_an_action_and_a_policy_backed_enforcement_row(live):
    """The full requested chain, when the operation IS established.

    create/recommend -> response_action -> enforcement evaluation ->
    governance_policy_evaluations simulation=FALSE, policy id + version stored ->
    Screen 8 reads back the exact row.
    """
    out = _run_recommend_chain(with_detection=True)

    # -- the response actions the endpoint persisted ------------------------
    assert out['result']['created_count'] == len(out['result']['action_types'])
    assert len(out['actions']) == out['result']['created_count']
    assert {str(a['incident_id']) for a in out['actions']} == {out['incident_id']}
    assert {a['mode'] for a in out['actions']} == {'recommended'}

    # -- one ENFORCEMENT evaluation per action, all workspace-scoped ---------
    assert len(out['evaluations']) == len(out['actions'])
    by_action = {row['snapshot_action_id']: row for row in out['evaluations']}
    assert set(by_action) == {str(a['id']) for a in out['actions']}

    for action_id, row in by_action.items():
        assert str(row['workspace_id']) == out['workspace_id']
        # (5) an ENFORCEMENT row, never a simulation relabelled.
        assert row['simulation'] is False
        # (7) a real verdict with reason codes.
        assert row['decision'] in ('ALLOW', 'DENY')
        assert list(row['reason_codes'])
        # (6) the policy and the version that decided.
        assert str(row['policy_id']) == out['policy_id']
        assert row['policy_version'] == 1
        # (3) canonical references match the action it governs.
        assert str(row['incident_id']) == out['incident_id']
        assert str(row['asset_id']) == out['asset_id']
        assert row['operation'] == 'MINT'
        assert row['fact_sources']['response_action_id'] == action_id
        assert row['fact_sources']['alert_id'] == out['alert_id']

    # -- (8) Screen 8 retrieves the exact persisted row, for EVERY action ----
    for action_id, gate in out['gates'].items():
        stored = by_action[action_id]
        assert gate['policy_decision'] == stored['decision']
        assert gate['policy_decision'] in ('ALLOW', 'DENY')
        # The specific row written FOR THIS ACTION, not a sibling's verdict.
        assert str(gate['evaluation_id']) == str(stored['id'])
        assert str(gate['policy_id']) == out['policy_id']
        assert gate['policy_version'] == 1
        assert 'POLICY_EVALUATION_MISSING' not in gate['reason_codes']

    # A policy verdict is ONE input to the gate, never the whole of it: every
    # action whose playbook profile requires a sign-off stays locked on the
    # quorum even with the policy satisfied.
    needing_approval = [
        action_id for action_id, lifecycle in out['lifecycles'].items()
        if lifecycle.get('requires_approval')
    ]
    assert needing_approval, 'this plan should contain at least one action requiring approval'
    for action_id in needing_approval:
        gate = out['gates'][action_id]
        assert gate['can_execute'] is False
        assert 'HUMAN_QUORUM_INCOMPLETE' in gate['reason_codes']


@_needs_pg
def test_recommend_records_a_fail_closed_deny_when_no_operation_can_be_established(live):
    """The reported symptom, on the real endpoint: DENY recorded, never silence.

    No threat detection stands behind this incident, so no operation resolves and
    no policy can be matched to one — while the workspace HAS an active policy, so
    Screen 8's scope probe considers the action governed. That combination used to
    write nothing and leave the gate at NOT_EVALUATED / POLICY_EVALUATION_MISSING
    forever.
    """
    out = _run_recommend_chain(with_detection=False)

    assert len(out['evaluations']) == len(out['actions'])
    for row in out['evaluations']:
        assert str(row['workspace_id']) == out['workspace_id']
        # Recorded, and recorded as ENFORCEMENT.
        assert row['simulation'] is False
        # Fail-closed, with the reason named rather than implied.
        assert row['decision'] == 'DENY'
        assert list(row['reason_codes']) == ['POLICY_NOT_FOUND', 'OPERATION_NOT_ESTABLISHED']
        # No policy decided it, so none is named as having decided it — the
        # policies that WERE in force are recorded as references instead.
        assert row['policy_id'] is None
        assert row['policy_version'] is None
        scope = row['fact_sources']['scope_policies']
        assert [p['policy_id'] for p in scope] == [out['policy_id']]
        assert scope[0]['policy_version'] == 1
        # Still tied to the canonical chain.
        assert str(row['incident_id']) == out['incident_id']
        assert str(row['asset_id']) == out['asset_id']

    # A policy DENY locks EVERY action in the plan, whether or not its playbook
    # profile would otherwise have required a sign-off.
    for gate in out['gates'].values():
        assert gate['policy_decision'] == 'DENY'
        assert gate['decision'] == 'DENIED'
        assert 'POLICY_DENIED' in gate['reason_codes']
        assert 'OPERATION_NOT_ESTABLISHED' in gate['reason_codes']
        # The symptom is gone: the gate no longer reports a missing evaluation.
        assert 'POLICY_EVALUATION_MISSING' not in gate['reason_codes']
        assert gate['can_execute'] is False


@_needs_pg
def test_recommend_writes_nothing_when_no_policy_governs_the_workspace(live):
    """The other half of the branch stays as it was: nothing governs, nothing written.

    Here the gate reports NOT_APPLICABLE from the same probe the producer used,
    so no evaluation is owed and a recorded refusal would invent one.
    """
    out = _run_recommend_chain(with_detection=False, active_policy=False)

    assert out['evaluations'] == []
    for action_id, gate in out['gates'].items():
        assert gate['policy_decision'] == 'NOT_APPLICABLE'
        assert gate['policy_id'] is None
        # The action is not parked waiting for a row nothing will write.
        assert 'POLICY_EVALUATION_MISSING' not in gate['reason_codes']
        # Executability here is decided by the quorum and the adapter, not by
        # policy: an action that requires a sign-off still has none.
        if out['lifecycles'][action_id].get('requires_approval'):
            assert gate['can_execute'] is False
            assert 'HUMAN_QUORUM_INCOMPLETE' in gate['reason_codes']


@_needs_pg
def test_re_recommending_evaluates_pre_existing_actions_without_duplicating(live):
    """Recommending again evaluates every action in the plan, exactly once.

    Recommending is idempotent, so the second call creates nothing. It must still
    evaluate what it resolved — an action created before an evaluation could be
    produced for it has no other endpoint that would ever revisit it — and the
    evaluator's own idempotency must stop that from appending a second decision.
    """
    from psycopg.rows import dict_row

    from services.api.app import pilot

    out = _run_recommend_chain(with_detection=True)
    workspace_id, incident_id = out['workspace_id'], out['incident_id']
    first = len(out['evaluations'])
    assert first == len(out['actions'])

    # Delete the evaluations to model actions created before the producer could
    # write one, then recommend again: the same endpoint must repair them.
    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        conn.execute('DELETE FROM governance_policy_evaluations WHERE workspace_id = %s', (workspace_id,))
        conn.commit()

    repaired = pilot.recommend_response_action_for_incident(
        incident_id,
        _Request(out['token'], workspace_id, f'/incidents/{incident_id}/response-actions/recommend'),
    )
    assert repaired['created_count'] == 0, 'recommending again must not create duplicate actions'

    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        after_repair = conn.execute(
            'SELECT COUNT(*) AS total FROM governance_policy_evaluations '
            'WHERE workspace_id = %s AND simulation = FALSE', (workspace_id,),
        ).fetchone()['total']
    assert after_repair == first, 'every planned action must be evaluated, not only new ones'

    # A third call changes nothing: unchanged facts under an unchanged policy
    # version return `already_evaluated` and write no second decision.
    pilot.recommend_response_action_for_incident(
        incident_id,
        _Request(out['token'], workspace_id, f'/incidents/{incident_id}/response-actions/recommend'),
    )
    with psycopg.connect(_DSN, row_factory=dict_row) as conn:
        final = conn.execute(
            'SELECT COUNT(*) AS total FROM governance_policy_evaluations '
            'WHERE workspace_id = %s AND simulation = FALSE', (workspace_id,),
        ).fetchone()['total']
    assert final == first, 'idempotency must hold across repeated recommend calls'
