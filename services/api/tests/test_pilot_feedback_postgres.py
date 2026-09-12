"""Pilot feedback writes and reads, against REAL PostgreSQL.

The hermetic suite (test_pilot_feedback_discovery.py) drives these functions
through a fake connection. That proves which parameters are bound and which
tenant they came from — it cannot prove the statements themselves run. A column
name that does not exist, an INSERT whose value list is one short, a filter
clause that does not compile: every one of those passes a fake and fails in
production.

So this executes the real ``record_feedback``, ``resolve_feedback_context`` and
``list_feedback`` against a migrated database, and checks the rows that come
back out.

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_pilot_feedback_postgres.py -q

Skipped entirely when that DSN is absent, so the default suite stays hermetic.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import uuid

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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_DSN and _PSQL),
        reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) and have '
               'psql on PATH to run the feedback storage tests',
    ),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'


def _psql(*args: str) -> None:
    proc = subprocess.run(
        [_PSQL, _DSN, '-q', '-v', 'ON_ERROR_STOP=1', *args],
        capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, f'psql failed:\n{proc.stdout}\n{proc.stderr}'


@pytest.fixture(scope='module')
def live():
    """A migrated database with two tenants, one incident each."""
    from psycopg.rows import dict_row

    _psql('-c', 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
    for path in sorted(_MIGRATIONS.glob('*.sql')):
        _psql('-f', str(path))

    ids = {
        'org': str(uuid.uuid4()), 'workspace': str(uuid.uuid4()), 'user': str(uuid.uuid4()),
        'incident': str(uuid.uuid4()),
        'rival_org': str(uuid.uuid4()), 'rival_workspace': str(uuid.uuid4()),
        'rival_incident': str(uuid.uuid4()),
    }
    with psycopg.connect(_DSN, autocommit=True, row_factory=dict_row) as connection:
        connection.execute(
            "INSERT INTO organizations (id, name, slug, evaluation_started_at)"
            " VALUES (%s, 'OpenTrade', 'opentrade', NOW() - INTERVAL '7 days')",
            (ids['org'],),
        )
        connection.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Rival Capital', 'rival')",
            (ids['rival_org'],),
        )
        connection.execute(
            "INSERT INTO users (id, email, password_hash, full_name)"
            " VALUES (%s, 'security@opentrade.test', 'x', 'Security Lead')",
            (ids['user'],),
        )
        for key, org_key, slug in (
            ('workspace', 'org', 'ot'), ('rival_workspace', 'rival_org', 'rv'),
        ):
            connection.execute(
                'INSERT INTO workspaces (id, name, slug, created_by_user_id, organization_id)'
                ' VALUES (%s, %s, %s, %s, %s)',
                (ids[key], slug.upper(), slug, ids['user'], ids[org_key]),
            )
        connection.execute(
            'INSERT INTO organization_memberships (id, organization_id, user_id, role)'
            " VALUES (%s, %s, %s, 'owner')",
            (str(uuid.uuid4()), ids['org'], ids['user']),
        )
        for incident_key, workspace_key in (
            ('incident', 'workspace'), ('rival_incident', 'rival_workspace'),
        ):
            connection.execute(
                'INSERT INTO incidents (id, workspace_id, user_id, event_type, severity, status,'
                " summary, title) VALUES (%s, %s, %s, 'transfer', 'high', 'open', 's', 'Transfer')",
                (ids[incident_key], ids[workspace_key], ids['user']),
            )
        yield {'connection': connection, 'ids': ids}


@pytest.fixture
def stored(live):
    """Three submissions — one per depth — written by the real code path."""
    from services.api.app import organizations as org

    connection, ids = live['connection'], live['ids']
    connection.execute('DELETE FROM organization_feedback')
    organization = org.get_organization(connection, ids['org'])
    pilot_day = org.pilot_day_for(organization)

    context = org.resolve_feedback_context(
        connection,
        {'page': '/incidents/x', 'incident_id': ids['incident'], 'alert_id': ids['rival_incident']},
        organization_id=ids['org'],
    )
    detailed = org.record_feedback(
        connection, organization_id=ids['org'], workspace_id=ids['workspace'], user_id=ids['user'],
        feedback_type='missing_feature',
        message='Need policy approval evidence linked to the incident timeline.',
        context=context, feedback_mode='detailed', severity='high', production_blocker='yes',
        contact_permission=True, pilot_day=pilot_day,
        narratives={
            'goal_or_task': 'Prove to our auditor that every transfer was reviewed.',
            'security_problem': 'We cannot evidence who approved a privileged transfer.',
            'current_workaround': 'A spreadsheet and block-explorer screenshots.',
            'deployment_requirement': 'A signed export covering the approval chain.',
        },
    )
    quick = org.record_feedback(
        connection, organization_id=ids['org'], workspace_id=ids['workspace'], user_id=ids['user'],
        feedback_type='false_positive', message='Our own rebalance was flagged as an exfiltration.',
    )
    review = org.record_feedback(
        connection, organization_id=ids['org'], workspace_id=ids['workspace'], user_id=ids['user'],
        feedback_type='other', message='Detection quality was the strongest part.',
        feedback_mode='end_of_pilot', continue_intent='maybe',
        narratives={'paid_capability': 'Signed evidence exports for auditors.'},
    )
    return {
        'detailed': detailed['id'], 'quick': quick['id'], 'review': review['id'],
        'context': context, 'pilot_day': pilot_day,
    }


def test_the_discovery_columns_are_detected_on_a_migrated_database(live) -> None:
    from services.api.app import organizations as org

    assert org.feedback_detail_schema_state(live['connection']) == org.SCHEMA_READY
    assert org.feedback_detail_schema_ready(live['connection']) is True


def test_pilot_day_is_derived_from_the_tenants_own_window(live, stored) -> None:
    # The fixture organization started its evaluation seven days ago.
    assert stored['pilot_day'] == 8


def test_only_this_tenants_context_id_survives_verification(live, stored) -> None:
    """The rival's incident id is dropped; ours and the page are kept."""
    assert stored['context'] == {
        'page': '/incidents/x', 'incident_id': live['ids']['incident'],
    }


def test_every_discovery_answer_round_trips_through_the_database(live, stored) -> None:
    from services.api.app import organizations as org

    row = next(
        item for item in org.list_feedback(live['connection'], limit=50)
        if item['id'] == stored['detailed']
    )
    assert row['feedback_mode'] == 'detailed'
    assert row['severity'] == 'high'
    assert row['production_blocker'] == 'yes'
    assert row['contact_permission'] is True
    assert row['pilot_day'] == 8
    assert row['security_problem'] == 'We cannot evidence who approved a privileged transfer.'
    assert row['current_workaround'] == 'A spreadsheet and block-explorer screenshots.'
    assert row['deployment_requirement'] == 'A signed export covering the approval chain.'
    assert row['detail_available'] is True
    # Joined facts the console shows next to the answer.
    assert row['organization_name'] == 'OpenTrade'
    assert row['organization_plan'] == 'pilot'
    assert row['user_email'] == 'security@opentrade.test'
    assert row['context'] == {'page': '/incidents/x', 'incident_id': live['ids']['incident']}


def test_the_end_of_pilot_answers_round_trip_too(live, stored) -> None:
    from services.api.app import organizations as org

    row = next(
        item for item in org.list_feedback(live['connection'], limit=50)
        if item['id'] == stored['review']
    )
    assert row['feedback_mode'] == 'end_of_pilot'
    assert row['continue_intent'] == 'maybe'
    assert row['paid_capability'] == 'Signed evidence exports for auditors.'


@pytest.mark.parametrize(
    ('filters', 'expected_key'),
    [
        ({'production_blocker': 'yes'}, 'detailed'),
        ({'severity': 'high'}, 'detailed'),
        ({'feedback_mode': 'end_of_pilot'}, 'review'),
        ({'feedback_type': 'false_positive'}, 'quick'),
    ],
)
def test_each_filter_narrows_the_query_in_sql(live, stored, filters, expected_key) -> None:
    from services.api.app import organizations as org

    rows = org.list_feedback(live['connection'], **filters)
    assert [row['id'] for row in rows] == [stored[expected_key]], filters


def test_a_filter_that_matches_nothing_returns_nothing(live, stored) -> None:
    from services.api.app import organizations as org

    assert org.list_feedback(live['connection'], severity='low') == []
    assert org.list_feedback(live['connection'], feedback_type='policy_controls') == []


def test_another_organizations_listing_is_empty(live, stored) -> None:
    """The scoped read returns only its own tenant's rows."""
    from services.api.app import organizations as org

    assert org.list_feedback(live['connection'], organization_id=live['ids']['rival_org']) == []
    own = org.list_feedback(live['connection'], organization_id=live['ids']['org'])
    assert len(own) == 3
    assert {row['organization_id'] for row in own} == {live['ids']['org']}


def test_the_roadmap_counters_match_the_stored_rows(live, stored) -> None:
    from services.api.app import organizations as org

    summary = org.feedback_summary(org.list_feedback(live['connection'], limit=50))
    assert summary == {
        'total': 3,
        'production_blockers': 1,
        'high_or_critical': 1,
        'missing_capability': 1,
        'detection_issues': 1,
    }


def test_the_customer_tables_feedback_count_sees_every_depth(live, stored) -> None:
    """The count in /admin/customers is one COUNT(*) over the one table."""
    count = live['connection'].execute(
        'SELECT COUNT(*) AS c FROM organization_feedback f WHERE f.organization_id = %s',
        (live['ids']['org'],),
    ).fetchone()['c']
    assert int(count) == 3


def test_a_credential_never_reaches_the_database(live, stored) -> None:
    from fastapi import HTTPException
    from services.api.app import organizations as org

    before = live['connection'].execute(
        'SELECT COUNT(*) AS c FROM organization_feedback',
    ).fetchone()['c']
    with pytest.raises(HTTPException) as exc_info:
        org.record_feedback(
            live['connection'], organization_id=live['ids']['org'],
            workspace_id=live['ids']['workspace'], user_id=live['ids']['user'],
            feedback_type='security', message='Here is the problem.',
            feedback_mode='detailed',
            narratives={'current_workaround': 'we keep the key 0x' + 'a' * 64 + ' in a vault'},
        )
    assert exc_info.value.detail['code'] == 'FEEDBACK_CONTAINS_SECRET'
    after = live['connection'].execute(
        'SELECT COUNT(*) AS c FROM organization_feedback',
    ).fetchone()['c']
    assert int(after) == int(before)


def test_a_long_answer_is_stored_whole(live, stored) -> None:
    """A real security description runs long; it must not be silently truncated."""
    from services.api.app import organizations as org

    # Trimmed to the cap and stripped, because that is what the service stores:
    # comparing against an untrimmed copy would fail on whitespace, not on length.
    long_answer = ('The approval trail breaks because ' + 'the signer set changes daily. ' * 100)
    long_answer = long_answer[:org.FEEDBACK_MAX_MESSAGE_CHARS].strip()
    recorded = org.record_feedback(
        live['connection'], organization_id=live['ids']['org'],
        workspace_id=live['ids']['workspace'], user_id=live['ids']['user'],
        feedback_type='policy_controls', message='See below.', feedback_mode='detailed',
        narratives={'security_problem': long_answer},
    )
    row = next(
        item for item in org.list_feedback(live['connection'], limit=50)
        if item['id'] == recorded['id']
    )
    assert row['security_problem'] == long_answer
    assert len(row['security_problem']) > 2000
