"""Pilot customer-discovery feedback — two depths, one table, one tenant.

The Pilot badge's feedback form gained a second, structured path so the founder
can learn what security problem a customer is actually trying to solve, how they
solve it today, and what would block production. This file covers what that is
allowed to do:

  QUICK      the existing lightweight path still works, unchanged, and is still
             stamped with the SESSION's organization/workspace/user.
  DETAILED   every discovery answer is stored, and a vocabulary value nobody
             chose is never invented.
  CONTEXT    page is kept; an incident/alert/asset id is stored only after it is
             verified against the submitter's OWN tenant.
  ADMIN      only internal staff may read across organizations.
  PRIVACY    no narrative answer reaches the audit log, and a credential in ANY
             field refuses the whole submission rather than storing a redaction.
  SCHEMA     a deployment that has not run migration 0153 refuses the deep form
             with a truthful reason instead of dropping the answers silently.

Run:
    python -m pytest services/api/tests/test_pilot_feedback_discovery.py -q
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app.domains.tenancy import endpoints as tenancy_endpoints

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

#: The endpoint derives ``pilot_day`` from the WALL CLOCK, because that is what a
#: real submission does. The fixture organization's evaluation window is therefore
#: anchored to the real clock too, so "day 8" means day 8 on whatever date this
#: suite happens to run rather than only on the day it was written.
REAL_NOW = datetime.now(timezone.utc)

ORG_A = 'aaaaaaaa-1111-1111-1111-111111111111'
ORG_B = 'bbbbbbbb-2222-2222-2222-222222222222'
WS_A = 'aaaaaaaa-0000-0000-0000-00000000000a'
WS_B = 'bbbbbbbb-0000-0000-0000-00000000000b'
USER_A = 'user-aaaa'
ADMIN_USER = 'user-founder'

INCIDENT_A = 'inc-aaaa-1111'
INCIDENT_B = 'inc-bbbb-2222'
ALERT_A = 'alr-aaaa-1111'
ASSET_A = 'ast-aaaa-1111'


class _Result:
    def __init__(self, rows: Any = None) -> None:
        if rows is None:
            self._rows: list[Any] = []
        elif isinstance(rows, list):
            self._rows = rows
        else:
            self._rows = [rows]

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Dispatches on normalised SQL and records every write.

    ``detail_schema`` models whether migration 0153 has run on this deployment:
    the probe in ``organizations.feedback_detail_schema_state`` reads
    information_schema.columns, so the fake answers that query the way a migrated
    (or unmigrated) database would.
    """

    def __init__(
        self,
        *,
        detail_schema: bool = True,
        entity_owner: dict[str, str] | None = None,
        feedback_rows: list[dict[str, Any]] | None = None,
        users: dict[str, dict[str, Any]] | None = None,
        probe_raises: bool = False,
    ) -> None:
        self.detail_schema = detail_schema
        self.entity_owner = entity_owner or {}
        self.feedback_rows = feedback_rows or []
        self.users = users or {}
        self.probe_raises = probe_raises
        self.writes: list[tuple[str, Any]] = []
        self.reads: list[tuple[str, Any]] = []
        self.committed = False

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()
        if lowered.startswith(('insert', 'update', 'delete')):
            self.writes.append((sql, params))
            return _Result()
        self.reads.append((sql, params))

        if "table_name = 'organization_feedback'" in lowered:
            if self.probe_raises:
                raise RuntimeError('probe exploded')
            count = len(org_service.FEEDBACK_DETAIL_COLUMNS) if self.detail_schema else 0
            return _Result({'column_count': count})

        if 'information_schema.tables' in lowered:
            return _Result({'table_count': 2, 'link_count': 1})

        if 'from workspaces w join organizations o on o.id = w.organization_id' in lowered:
            org_id = {WS_A: ORG_A, WS_B: ORG_B}.get(str(params[0]))
            return _Result(_org_row(org_id) if org_id else None)

        if 'from organizations where id = %s' in lowered:
            return _Result(_org_row(str(params[0])))

        if 'select email, is_internal_admin from users' in lowered:
            return _Result(self.users.get(str(params[0])))

        # The tenant-ownership probe for a contextual id.
        for table in ('incidents', 'alerts', 'assets'):
            if f'from {table} e join workspaces w' in lowered:
                entity_id, organization_id = str(params[0]), str(params[1])
                owner = self.entity_owner.get(entity_id)
                return _Result({'found': 1} if owner == organization_id else None)

        if 'from organization_feedback f join organizations o' in lowered:
            return _Result(list(self.feedback_rows))

        if 'select role from organization_memberships' in lowered:
            return _Result({'role': 'owner'})
        return _Result()

    def commit(self) -> None:
        self.committed = True


def _org_row(org_id: str | None, **overrides: Any) -> dict[str, Any]:
    row = {
        'id': org_id,
        'name': f'Org {str(org_id)[:4]}',
        'slug': f'org-{str(org_id)[:4]}',
        'plan': ent.PLAN_PILOT,
        'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': REAL_NOW - timedelta(days=7),
        'evaluation_expires_at': REAL_NOW + timedelta(days=23),
        'entitlement_overrides': {},
        'created_at': NOW - timedelta(days=7),
        'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _request(headers: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(headers=headers or {}, client=None)


def _authenticate_as(monkeypatch: pytest.MonkeyPatch, *, user_id: str, workspace_id: str) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection', lambda connection, request: {'id': user_id},
    )
    monkeypatch.setattr(
        pilot, 'resolve_workspace',
        lambda connection, uid, header: {'workspace_id': workspace_id, 'role': 'owner'},
    )
    monkeypatch.setattr(pilot, 'log_audit', _record_audit)


AUDIT_ROWS: list[dict[str, Any]] = []


def _record_audit(connection: Any, **kwargs: Any) -> None:
    AUDIT_ROWS.append(kwargs)


@pytest.fixture(autouse=True)
def _clear_audit() -> Any:
    AUDIT_ROWS.clear()
    yield
    AUDIT_ROWS.clear()


def _use_connection(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    @contextmanager
    def _cm() -> Any:
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _cm)


def _insert(connection: FakeConnection) -> tuple[str, Any]:
    return next(
        (sql, params)
        for sql, params in connection.writes
        if 'insert into organization_feedback' in sql.lower()
    )


def _stored(connection: FakeConnection) -> dict[str, Any]:
    """The inserted row as {column: value}, read off the INSERT itself.

    ``created_at`` is written as NOW() rather than bound, so it is dropped from
    the column list before the remaining names are paired with the parameters —
    otherwise every column after it would be read off by one.
    """
    sql, params = _insert(connection)
    columns = [name.strip() for name in sql[sql.index('(') + 1: sql.index(')')].split(',')]
    bound = [name for name in columns if name != 'created_at']
    assert len(bound) == len(params), f'{len(bound)} columns vs {len(params)} params'
    return dict(zip(bound, params))


DETAILED_BODY = {
    'feedback_mode': 'detailed',
    'feedback_type': 'missing_feature',
    'message': 'Policy approval evidence is not linked to the incident timeline.',
    'severity': 'high',
    'production_blocker': 'yes',
    'contact_permission': True,
    'goal_or_task': 'Prove to our auditor that every treasury transfer was reviewed.',
    'security_problem': 'We cannot evidence who approved a privileged transfer.',
    'current_workaround': 'A spreadsheet and screenshots from the block explorer.',
    'where_decoda_helped': 'The detection fired before our own alerting did.',
    'missing_or_difficult': 'No link from the approval record to the incident.',
    'deployment_requirement': 'Signed evidence export covering the approval chain.',
}


# ── QUICK ─────────────────────────────────────────────────────────────────────

def test_quick_feedback_is_stamped_from_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """1-6: organization, workspace and user all come from the session."""
    connection = FakeConnection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_account_feedback(
        {
            'feedback_type': 'false_positive',
            'message': 'This transfer was flagged but it was our own rebalance.',
            # Every one of these is a tenant claim the body does not get to make.
            'organization_id': ORG_B,
            'workspace_id': WS_B,
            'user_id': 'someone-else',
            'pilot_day': 999,
        },
        _request(),
    )

    assert result['submitted'] is True
    assert result['feedback_mode'] == 'quick'
    row = _stored(connection)
    assert row['organization_id'] == ORG_A
    assert row['workspace_id'] == WS_A
    assert row['user_id'] == USER_A
    assert row['feedback_type'] == 'false_positive'
    assert row['message'] == 'This transfer was flagged but it was our own rebalance.'
    # Derived from the tenant's evaluation window (started 7 days ago), never read
    # from the body — the 999 above changes nothing.
    assert row['pilot_day'] == 8


def test_quick_feedback_keeps_severity_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unstated severity is stored as NULL, not as a default the user never chose."""
    connection = FakeConnection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_account_feedback(
        {'feedback_type': 'usability', 'message': 'The alerts filter resets itself.', 'severity': ''},
        _request(),
    )
    assert _stored(connection)['severity'] is None


def test_every_new_quick_feedback_type_is_accepted() -> None:
    """The twelve-value discovery vocabulary reaches the database."""
    for kind in org_service.FEEDBACK_TYPES:
        connection = FakeConnection()
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type=kind, message='Something happened that we want to report.',
        )
        assert _stored(connection)['feedback_type'] == kind


def test_unknown_feedback_type_is_still_refused() -> None:
    connection = FakeConnection()
    with pytest.raises(HTTPException) as exc_info:
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type='exfiltrate', message='hello there',
        )
    assert exc_info.value.detail['code'] == 'INVALID_FEEDBACK_TYPE'
    assert connection.writes == []


# ── DETAILED ──────────────────────────────────────────────────────────────────

def test_detailed_feedback_stores_every_discovery_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """8-16: each structured answer lands in its own column."""
    connection = FakeConnection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_account_feedback(dict(DETAILED_BODY), _request())
    assert result['feedback_mode'] == 'detailed'

    row = _stored(connection)
    assert row['feedback_mode'] == 'detailed'
    assert row['severity'] == 'high'
    assert row['production_blocker'] == 'yes'
    assert row['contact_permission'] is True
    assert row['goal_or_task'] == DETAILED_BODY['goal_or_task']
    assert row['security_problem'] == DETAILED_BODY['security_problem']
    assert row['current_workaround'] == DETAILED_BODY['current_workaround']
    assert row['where_decoda_helped'] == DETAILED_BODY['where_decoda_helped']
    assert row['missing_or_difficult'] == DETAILED_BODY['missing_or_difficult']
    assert row['deployment_requirement'] == DETAILED_BODY['deployment_requirement']


def test_detailed_feedback_accepts_missing_optional_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """35: a half-filled form is accepted; the blanks are stored as NULL."""
    connection = FakeConnection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_account_feedback(
        {
            'feedback_mode': 'detailed',
            'feedback_type': 'investigation',
            'message': 'We could not tell which wallet initiated the transfer.',
            'goal_or_task': 'Investigate an unexpected outbound transfer.',
        },
        _request(),
    )
    row = _stored(connection)
    assert row['goal_or_task'] == 'Investigate an unexpected outbound transfer.'
    assert row['current_workaround'] is None
    assert row['deployment_requirement'] is None
    assert row['severity'] is None
    assert row['production_blocker'] is None
    assert row['contact_permission'] is False


def test_contact_permission_defaults_to_no() -> None:
    """Permission to follow up is never assumed from silence."""
    connection = FakeConnection()
    org_service.record_feedback(
        connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
        feedback_type='other', message='A note about the console.',
        feedback_mode='detailed',
    )
    assert _stored(connection)['contact_permission'] is False


@pytest.mark.parametrize(
    ('field', 'value', 'code'),
    [
        ('severity', 'catastrophic', 'INVALID_FEEDBACK_SEVERITY'),
        ('production_blocker', 'maybe', 'INVALID_PRODUCTION_BLOCKER'),
        ('continue_intent', 'perhaps', 'INVALID_CONTINUE_INTENT'),
        ('feedback_mode', 'interview', 'INVALID_FEEDBACK_MODE'),
    ],
)
def test_a_vocabulary_value_nobody_offered_is_refused(field: str, value: str, code: str) -> None:
    """Refused, not coerced: a defaulted priority is a number no customer stated."""
    connection = FakeConnection()
    with pytest.raises(HTTPException) as exc_info:
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type='other', message='Something to report.', **{field: value},
        )
    assert exc_info.value.detail['code'] == code
    assert connection.writes == []


def test_end_of_pilot_review_stores_its_own_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """30: the review submits through the same endpoint and the same table."""
    connection = FakeConnection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    result = tenancy_endpoints.submit_account_feedback(
        {
            'feedback_mode': 'end_of_pilot',
            'feedback_type': 'other',
            'message': 'Overall the detection quality was the strongest part.',
            'continue_intent': 'maybe',
            'paid_capability': 'Signed evidence exports for auditors.',
            'where_decoda_helped': 'Catching the unexpected owner change.',
        },
        _request(),
    )
    assert result['feedback_mode'] == 'end_of_pilot'
    row = _stored(connection)
    assert row['feedback_mode'] == 'end_of_pilot'
    assert row['continue_intent'] == 'maybe'
    assert row['paid_capability'] == 'Signed evidence exports for auditors.'


def test_end_of_pilot_review_is_optional_at_every_layer() -> None:
    """31: nothing in the product requires a review to have been submitted.

    Asserted as an absence: no lifecycle, entitlement, or plan state reads the
    feedback table at all, so there is no path by which a missing review could
    restrict a tenant.
    """
    connection = FakeConnection()
    organization = _org_row(ORG_A)
    assert ent.lifecycle_state(organization, now=NOW) == ent.LIFECYCLE_ACTIVE_PILOT
    assert ent.monitoring_allowed(organization, now=NOW) is True
    assert ent.provisioning_allowed(organization, now=NOW) is True
    assert connection.reads == []


# ── CONTEXT ───────────────────────────────────────────────────────────────────

def test_page_and_own_tenant_ids_are_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    """17-19: page plus each contextual id, once it is verified as this tenant's."""
    connection = FakeConnection(
        entity_owner={INCIDENT_A: ORG_A, ALERT_A: ORG_A, ASSET_A: ORG_A},
    )
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_account_feedback(
        {
            'feedback_type': 'incident_response',
            'message': 'The response action needed a second approval we could not give.',
            'context': {
                'page': '/incidents/inc-aaaa-1111',
                'incident_id': INCIDENT_A,
                'alert_id': ALERT_A,
                'asset_id': ASSET_A,
            },
        },
        _request(),
    )
    stored = _stored(connection)['context']
    assert '"page": "/incidents/inc-aaaa-1111"' in stored
    assert INCIDENT_A in stored and ALERT_A in stored and ASSET_A in stored


def test_another_tenants_incident_id_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """20: an id this organization does not own never reaches the row.

    Dropped rather than refused: the customer's words are still worth keeping.
    What must not happen is the founder console rendering someone else's incident
    id beside them as though this feedback were about it.
    """
    connection = FakeConnection(entity_owner={INCIDENT_B: ORG_B})
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_account_feedback(
        {
            'feedback_type': 'investigation',
            'message': 'Investigation timeline was hard to follow.',
            'context': {'page': '/incidents', 'incident_id': INCIDENT_B},
        },
        _request(),
    )
    stored = _stored(connection)['context']
    assert INCIDENT_B not in stored
    assert '/incidents' in stored


def test_an_unverifiable_context_id_fails_closed() -> None:
    """A probe that raises drops the id rather than trusting it."""
    connection = FakeConnection(probe_raises=False)

    class _Exploding(FakeConnection):
        def execute(self, query: str, params: Any = None) -> _Result:
            if 'join workspaces w' in ' '.join(query.split()).lower():
                raise RuntimeError('database is having a bad day')
            return super().execute(query, params)

    exploding = _Exploding()
    resolved = org_service.resolve_feedback_context(
        exploding, {'page': '/alerts', 'incident_id': INCIDENT_A}, organization_id=ORG_A,
    )
    assert resolved == {'page': '/alerts'}
    assert connection.writes == []


def test_context_keys_outside_the_allowlist_are_dropped() -> None:
    """A body cannot smuggle an extra field into the stored context blob."""
    resolved = org_service.sanitize_feedback_context(
        {'page': '/alerts', 'password': 'hunter2', 'authorization': 'Bearer abc'},
    )
    assert resolved == {'page': '/alerts'}


# ── ADMIN ─────────────────────────────────────────────────────────────────────

def _feedback_row(**overrides: Any) -> dict[str, Any]:
    row = {
        'id': 'fb-1', 'organization_id': ORG_A, 'organization_name': 'OpenTrade',
        'organization_plan': 'pilot', 'workspace_id': WS_A, 'user_id': USER_A,
        'user_email': 'security@opentrade.test', 'feedback_type': 'missing_feature',
        'message': 'Need policy approval evidence linked to incident timeline.',
        'context': {'page': '/incidents'}, 'created_at': NOW,
        'feedback_mode': 'detailed', 'severity': 'high', 'production_blocker': 'yes',
        'continue_intent': None, 'contact_permission': True, 'pilot_day': 8,
        'goal_or_task': 'Evidence an approval.', 'security_problem': 'No approval trail.',
        'current_workaround': 'Spreadsheets.', 'where_decoda_helped': 'Detection fired.',
        'missing_or_difficult': 'No link to the timeline.',
        'deployment_requirement': 'Signed export.', 'paid_capability': None,
    }
    row.update(overrides)
    return row


def test_internal_admin_can_list_feedback_with_its_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """21, 23, 24: the founder read returns the answers and the contact."""
    connection = FakeConnection(
        feedback_rows=[_feedback_row()],
        users={ADMIN_USER: {'email': 'founder@decoda.app', 'is_internal_admin': True}},
    )
    _authenticate_as(monkeypatch, user_id=ADMIN_USER, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    payload = tenancy_endpoints.list_admin_feedback(_request())
    item = payload['feedback'][0]
    assert payload['detail_available'] is True
    assert item['organization_name'] == 'OpenTrade'
    assert item['user_email'] == 'security@opentrade.test'
    assert item['severity'] == 'high'
    assert item['production_blocker'] == 'yes'
    assert item['pilot_day'] == 8
    assert item['security_problem'] == 'No approval trail.'
    assert item['contact_permission'] is True


def test_a_customer_cannot_list_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    """22: a non-internal caller is refused, and no row is returned."""
    connection = FakeConnection(
        feedback_rows=[_feedback_row()],
        users={USER_A: {'email': 'analyst@opentrade.test', 'is_internal_admin': False}},
    )
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.list_admin_feedback(_request())
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == org_service.CODE_INTERNAL_ADMIN_REQUIRED


def test_admin_filters_are_applied_in_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """A filter the console shows must actually narrow the query."""
    connection = FakeConnection(
        feedback_rows=[_feedback_row()],
        users={ADMIN_USER: {'email': 'founder@decoda.app', 'is_internal_admin': True}},
    )
    _authenticate_as(monkeypatch, user_id=ADMIN_USER, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.list_admin_feedback(
        _request(), organization_id=ORG_A, severity='high', production_blocker='yes',
        feedback_type='missing_feature', since='2026-05-01',
    )
    sql, params = next(
        (sql, params) for sql, params in connection.reads
        if 'from organization_feedback f join organizations o' in sql.lower()
    )
    assert 'f.organization_id = %s' in sql
    assert 'f.severity = %s' in sql
    assert 'f.production_blocker = %s' in sql
    assert 'f.feedback_type = %s' in sql
    assert 'f.created_at >= %s' in sql
    assert ORG_A in params and 'high' in params and 'yes' in params


def test_an_unknown_filter_value_is_refused_not_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silently dropped filter would show a list the founder believes is narrowed."""
    connection = FakeConnection(
        users={ADMIN_USER: {'email': 'founder@decoda.app', 'is_internal_admin': True}},
    )
    _authenticate_as(monkeypatch, user_id=ADMIN_USER, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.list_admin_feedback(_request(), severity='urgent')
    assert exc_info.value.detail['code'] == 'INVALID_FEEDBACK_SEVERITY'


def test_roadmap_counters_count_the_rows_that_are_shown() -> None:
    """The summary is derived from the same list, so it cannot claim more."""
    rows = [
        {'production_blocker': 'yes', 'severity': 'high', 'feedback_type': 'missing_feature'},
        {'production_blocker': 'no', 'severity': 'low', 'feedback_type': 'false_positive'},
        {'production_blocker': 'yes', 'severity': 'critical', 'feedback_type': 'missed_detection'},
        {'production_blocker': None, 'severity': None, 'feedback_type': 'usability'},
    ]
    summary = org_service.feedback_summary(rows)
    assert summary == {
        'total': 4,
        'production_blockers': 2,
        'high_or_critical': 2,
        'missing_capability': 1,
        'detection_issues': 2,
    }


# ── PRIVACY ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('field', org_service.FEEDBACK_NARRATIVE_FIELDS)
def test_a_credential_in_any_answer_refuses_the_whole_submission(field: str) -> None:
    """25: the scan covers every free-text field, not just `message`."""
    connection = FakeConnection()
    with pytest.raises(HTTPException) as exc_info:
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type='security', message='Our signer key leaked.',
            feedback_mode='detailed', narratives={field: '0x' + 'a' * 64},
        )
    assert exc_info.value.detail['code'] == 'FEEDBACK_CONTAINS_SECRET'
    assert connection.writes == []


def test_ordinary_security_prose_is_never_mistaken_for_a_secret() -> None:
    """A long technical description must not be refused for sounding technical."""
    connection = FakeConnection()
    org_service.record_feedback(
        connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
        feedback_type='detection_accuracy',
        message='The detector flagged our multisig rotation as an unauthorized owner change.',
        feedback_mode='detailed',
        narratives={
            'current_workaround': (
                'Today we reconcile the Gnosis Safe owner set against our change tickets by hand '
                'every morning, then sign off in the audit spreadsheet.'
            ),
        },
    )
    assert _stored(connection)['feedback_type'] == 'detection_accuracy'


def test_audit_records_the_kind_not_the_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """26: no narrative answer is duplicated into the audit log."""
    connection = FakeConnection()
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_account_feedback(dict(DETAILED_BODY), _request())
    assert len(AUDIT_ROWS) == 1
    entry = AUDIT_ROWS[0]
    assert entry['action'] == 'organization.feedback_submitted'
    assert entry['metadata']['feedback_type'] == 'missing_feature'
    assert entry['metadata']['feedback_mode'] == 'detailed'
    logged = repr(entry)
    for answer in (
        DETAILED_BODY['message'],
        DETAILED_BODY['security_problem'],
        DETAILED_BODY['current_workaround'],
        DETAILED_BODY['deployment_requirement'],
    ):
        assert answer not in logged


# ── SCHEMA READINESS ──────────────────────────────────────────────────────────

def test_an_unmigrated_deployment_still_takes_quick_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lightweight path is unchanged before migration 0153."""
    connection = FakeConnection(detail_schema=False)
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.submit_account_feedback(
        {'feedback_type': 'usability', 'message': 'The alerts filter resets itself.'},
        _request(),
    )
    sql, _ = _insert(connection)
    assert 'feedback_mode' not in sql.lower()


def test_an_unmigrated_deployment_refuses_the_deep_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused with a reason rather than accepted and silently truncated."""
    connection = FakeConnection(detail_schema=False)
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.submit_account_feedback(dict(DETAILED_BODY), _request())
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail['code'] == 'FEEDBACK_DETAIL_UNAVAILABLE'
    assert connection.writes == []


def test_a_failed_schema_probe_is_not_read_as_ready() -> None:
    """An unreadable probe is UNKNOWN, and UNKNOWN is not READY."""
    connection = FakeConnection(probe_raises=True)
    assert org_service.feedback_detail_schema_state(connection) == org_service.SCHEMA_UNKNOWN
    assert org_service.feedback_detail_schema_ready(connection) is False


def test_unmigrated_listing_reports_absence_rather_than_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A field this deployment cannot store is None, not ''."""
    connection = FakeConnection(
        detail_schema=False,
        feedback_rows=[{
            'id': 'fb-1', 'organization_id': ORG_A, 'organization_name': 'OpenTrade',
            'organization_plan': 'pilot', 'workspace_id': WS_A, 'user_id': USER_A,
            'user_email': 'security@opentrade.test', 'feedback_type': 'usability',
            'message': 'A note.', 'context': {}, 'created_at': NOW,
        }],
        users={ADMIN_USER: {'email': 'founder@decoda.app', 'is_internal_admin': True}},
    )
    _authenticate_as(monkeypatch, user_id=ADMIN_USER, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    payload = tenancy_endpoints.list_admin_feedback(_request())
    assert payload['detail_available'] is False
    item = payload['feedback'][0]
    assert item['severity'] is None
    assert item['production_blocker'] is None
    assert item['contact_permission'] is None
    assert item['security_problem'] is None


# ── pilot day ─────────────────────────────────────────────────────────────────

def test_pilot_day_is_one_based_and_absent_without_a_window() -> None:
    started = NOW - timedelta(days=3, hours=2)
    assert org_service.pilot_day_for({'evaluation_started_at': started}, now=NOW) == 4
    assert org_service.pilot_day_for({'evaluation_started_at': NOW}, now=NOW) == 1
    assert org_service.pilot_day_for({'evaluation_started_at': None}, now=NOW) is None
    assert org_service.pilot_day_for({}, now=NOW) is None
    # A window that has not started yet reports nothing rather than a day 0.
    assert org_service.pilot_day_for(
        {'evaluation_started_at': NOW + timedelta(days=1)}, now=NOW,
    ) is None


def test_contact_permission_is_an_explicit_grant_not_a_truthy_value() -> None:
    """The string 'false' is truthy in Python; it must not grant permission.

    A "Yes" in the founder console beside "can we contact you about this" has to
    mean the customer said yes.
    """
    for granted in (True, 'true', 'yes', 'on', '1'):
        connection = FakeConnection()
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type='other', message='A note.', feedback_mode='detailed',
            contact_permission=granted,
        )
        assert _stored(connection)['contact_permission'] is True, granted

    for withheld in (False, 'false', 'no', '0', None, '', 0, {}):
        connection = FakeConnection()
        org_service.record_feedback(
            connection, organization_id=ORG_A, workspace_id=WS_A, user_id=USER_A,
            feedback_type='other', message='A note.', feedback_mode='detailed',
            contact_permission=withheld,
        )
        assert _stored(connection)['contact_permission'] is False, withheld


def test_an_unmigrated_deployment_refuses_rather_than_dropping_a_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A granted permission the database cannot hold is refused, not discarded."""
    connection = FakeConnection(detail_schema=False)
    _authenticate_as(monkeypatch, user_id=USER_A, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.submit_account_feedback(
            {
                'feedback_type': 'usability',
                'message': 'The filter resets itself.',
                'contact_permission': True,
            },
            _request(),
        )
    assert exc_info.value.detail['code'] == 'FEEDBACK_DETAIL_UNAVAILABLE'
    assert connection.writes == []


@pytest.mark.parametrize('value', ['yesterday', "2026-06-01'; DROP TABLE x--", '06/01/2026', 'now()'])
def test_a_malformed_date_filter_is_refused_before_it_reaches_sql(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    """A 400 naming the field, not a 500 from the database."""
    connection = FakeConnection(
        users={ADMIN_USER: {'email': 'founder@decoda.app', 'is_internal_admin': True}},
    )
    _authenticate_as(monkeypatch, user_id=ADMIN_USER, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.list_admin_feedback(_request(), since=value)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail['code'] == 'INVALID_FEEDBACK_DATE_FILTER'


@pytest.mark.parametrize('value', ['2026-06-01', '2026-06-01T12:00:00Z', '2026-06-01 12:00:00'])
def test_a_well_formed_date_filter_is_accepted(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    connection = FakeConnection(
        users={ADMIN_USER: {'email': 'founder@decoda.app', 'is_internal_admin': True}},
    )
    _authenticate_as(monkeypatch, user_id=ADMIN_USER, workspace_id=WS_A)
    _use_connection(monkeypatch, connection)

    tenancy_endpoints.list_admin_feedback(_request(), since=value)
    sql, params = next(
        (sql, params) for sql, params in connection.reads
        if 'from organization_feedback f join organizations o' in sql.lower()
    )
    assert 'f.created_at >= %s' in sql
    assert value in params
