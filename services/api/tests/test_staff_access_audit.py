"""Decoda staff/support access to customer information — attribution and audit.

The security question this file answers, for every staff surface that exists:

    WHO accessed, WHAT customer, WHAT operation, WHEN, WHY (where declared),
    READ or WRITE, and WHAT category of object?

  1   Internal admin customer LIST read produces a staff read record.
  2   Customer DETAIL read produces an organization-scoped record.
  3   Feedback read produces a record.
  4   The existing plan-change write stays audited under its own action.
  5   The existing status-change write stays audited.
  6   The existing Pilot deadline change stays audited.
  7   The staff actor is derived server-side from the authenticated account.
  8   A staff request cannot select a different actor id.
  9   No staff audit metadata carries a credential.
 10   The customer's own workspace CAN see the staff events that concern it.
 11   A customer cannot see staff events for another organization.
 12   Workspace A cannot see Workspace B's activity.
 13   The aggregate list read writes ONE event, not one per customer.
 14   A REFUSED internal-admin request produces no successful-access event.
 15   No staff endpoint mints a customer session or token.
 16   No impersonation / login-as / break-glass mechanism exists.

Run:
    python -m pytest services/api/tests/test_staff_access_audit.py -q
"""

from __future__ import annotations

import inspect
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app import staff_access
from services.api.app.domains.tenancy import endpoints as tenancy_endpoints

NOW = datetime(2026, 9, 18, 10, 42, tzinfo=timezone.utc)

ORG_A = 'aaaaaaaa-1111-1111-1111-111111111111'
ORG_B = 'bbbbbbbb-2222-2222-2222-222222222222'
WS_A = 'aaaaaaaa-0000-0000-0000-00000000000a'
WS_A2 = 'aaaaaaaa-0000-0000-0000-00000000000c'
WS_B = 'bbbbbbbb-0000-0000-0000-00000000000b'
STAFF_USER = 'ffffffff-0000-0000-0000-00000000000f'
STAFF_EMAIL = 'founder@decodasecurity.com'
CUSTOMER_USER = 'cccccccc-0000-0000-0000-00000000000c'


# ── fake connection ───────────────────────────────────────────────────────────

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
    """Dispatches on normalised SQL and KEEPS the audit rows that were written.

    ``pilot.log_audit`` is deliberately NOT patched out in this file: the rows
    under test are the real rows, written by the real chokepoint, so an assertion
    about what a customer can see is an assertion about production behaviour.
    """

    def __init__(
        self,
        *,
        organizations: dict[str, dict[str, Any]] | None = None,
        workspace_org: dict[str, str] | None = None,
        users: dict[str, dict[str, Any]] | None = None,
        schema_ready: bool = True,
    ) -> None:
        self.organizations = organizations or {}
        self.workspace_org = workspace_org or {}
        self.users = users or {}
        self.schema_ready = schema_ready
        self.audit_rows: list[dict[str, Any]] = []
        self.writes: list[tuple[str, Any]] = []
        self.committed = False

    # -- helpers a test reads back ------------------------------------------
    def staff_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.audit_rows if str(r['action']).startswith('staff.')]

    def internal_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.audit_rows if r['workspace_id'] is None]

    def workspace_rows(self, workspace_id: str) -> list[dict[str, Any]]:
        return [r for r in self.audit_rows if r['workspace_id'] == workspace_id]

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()
        values = tuple(params or ())

        if lowered.startswith('insert into audit_logs'):
            self.audit_rows.append({
                'id': values[0], 'workspace_id': values[1], 'user_id': values[2],
                'action': values[3], 'entity_type': values[4], 'entity_id': values[5],
                'ip_address': values[6], 'metadata': json.loads(values[7]),
                'created_at': values[8], 'row_hash': values[9],
                'previous_row_hash': values[10],
            })
            return _Result()
        if lowered.startswith(('insert', 'update', 'delete')):
            self.writes.append((sql, params))
            # Apply the lifecycle writes so a before/after assertion reads two
            # different values rather than the same one twice.
            if lowered.startswith('update organizations set status ='):
                self.organizations[str(values[-1])]['status'] = values[0]
            elif lowered.startswith('update organizations set plan ='):
                org = self.organizations[str(values[-1])]
                org['plan'] = values[0]
                if 'evaluation_started_at = %s' in lowered:
                    org['evaluation_started_at'], org['evaluation_expires_at'] = values[1], values[2]
                else:
                    org['evaluation_started_at'] = org['evaluation_expires_at'] = None
            elif lowered.startswith('update organizations set evaluation_expires_at = null'):
                self.organizations[str(values[-1])]['evaluation_expires_at'] = None
            elif lowered.startswith('update organizations set evaluation_expires_at = %s'):
                self.organizations[str(values[-1])]['evaluation_expires_at'] = values[0]
            return _Result()

        if 'select row_hash from audit_logs' in lowered:
            scope = values[0] if values else None
            chain = [r for r in self.audit_rows if r['workspace_id'] == scope and r['row_hash']]
            return _Result({'row_hash': chain[-1]['row_hash']} if chain else None)

        # The customer's own audit view.
        if 'from audit_logs where workspace_id = %s' in lowered and 'count(*)' in lowered:
            return _Result({'total': len(self.workspace_rows(str(values[0])))})
        if 'from audit_logs where workspace_id = %s' in lowered:
            return _Result([dict(r) for r in reversed(self.workspace_rows(str(values[0])))])

        if 'information_schema' in lowered:
            ready = 2 if self.schema_ready else 0
            return _Result({'table_count': ready, 'link_count': 1 if self.schema_ready else 0,
                            'org_table': 1 if self.schema_ready else 0,
                            'link_column': 1 if self.schema_ready else 0,
                            'column_count': 13 if self.schema_ready else 0,
                            'count': 13 if self.schema_ready else 0})

        if 'select email, is_internal_admin from users' in lowered:
            return _Result(self.users.get(str(values[0])))

        if 'from organizations where id = %s' in lowered:
            org_id = str(values[0])
            return _Result(dict(self.organizations[org_id]) if org_id in self.organizations else None)
        if 'from organizations o' in lowered and 'order by o.created_at desc' in lowered:
            return _Result([_listing_row(org) for org in self.organizations.values()])

        if 'select id from workspaces where organization_id' in lowered:
            return _Result([
                {'id': ws} for ws, org in self.workspace_org.items() if org == str(values[0])
            ])
        if 'from workspaces where organization_id' in lowered:
            return _Result([
                {'id': ws, 'name': 'Workspace', 'slug': 'ws', 'created_at': NOW}
                for ws, org in self.workspace_org.items() if org == str(values[0])
            ])

        if 'count(*) as count from' in lowered:
            return _Result({'count': 0})
        if 'from organization_memberships m join users u' in lowered:
            return _Result([])
        if 'from organization_feedback f' in lowered:
            return _Result([])
        if 'from pilot_requests' in lowered:
            return _Result([])
        return _Result()

    def commit(self) -> None:
        self.committed = True


def _org_row(org_id: str, **overrides: Any) -> dict[str, Any]:
    row = {
        'id': org_id,
        'name': f'Org {org_id[:4]}',
        'slug': f'org-{org_id[:4]}',
        'plan': ent.PLAN_PILOT,
        'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=7),
        'evaluation_expires_at': NOW + timedelta(days=23),
        'entitlement_overrides': {},
        'created_at': NOW - timedelta(days=7),
        'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _listing_row(org: dict[str, Any]) -> dict[str, Any]:
    row = dict(org)
    row.update({
        'primary_contact_email': 'owner@example.com', 'workspace_count': 1,
        'member_count': 1, 'contract_count': 0, 'target_count': 0,
        'evidence_count': 0, 'last_activity_at': NOW, 'feedback_count': 0,
    })
    return row


def _connection(**overrides: Any) -> FakeConnection:
    return FakeConnection(
        organizations={ORG_A: _org_row(ORG_A), ORG_B: _org_row(ORG_B, plan=ent.PLAN_SCALE)},
        workspace_org={WS_A: ORG_A, WS_A2: ORG_A, WS_B: ORG_B},
        users={
            STAFF_USER: {'email': STAFF_EMAIL, 'is_internal_admin': True},
            CUSTOMER_USER: {'email': 'customer@example.com', 'is_internal_admin': False},
        },
        **overrides,
    )


def _request(headers: dict[str, str] | None = None, *, ip: str = '203.0.113.9') -> SimpleNamespace:
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=ip))


def _sign_in(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection, *, user_id: str) -> None:
    """Authenticate as this account. Authorization itself is NOT patched: the
    real ``is_internal_admin`` reads the fake ``users`` row, so a test that
    signs in as a customer really is refused by the real check."""
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(
        pilot, 'authenticate_with_connection',
        lambda *_a, **_k: {'id': user_id, 'email': f'{user_id}@example.com'},
    )
    monkeypatch.setattr(
        pilot, 'resolve_workspace',
        lambda _c, _u, requested=None, **_k: {
            'workspace_id': requested or WS_A, 'role': 'owner',
            'workspace': {'id': requested or WS_A, 'name': 'W', 'slug': 'w'},
        },
    )

    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _pg)


# ── 1 / 13 — the customer directory read ─────────────────────────────────────

def test_1_internal_admin_customer_list_read_produces_a_staff_read_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.list_admin_customers(_request())

    rows = connection.staff_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row['action'] == staff_access.ACTION_CUSTOMER_LIST_VIEWED
    assert row['user_id'] == STAFF_USER                       # WHO
    assert row['metadata']['access_mode'] == 'read'           # READ or WRITE
    assert row['metadata']['object_type'] == 'organization_directory'
    assert row['created_at'] is not None                      # WHEN, server clock
    assert connection.committed is True


def test_13_the_directory_read_writes_one_event_not_one_per_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    connection.organizations['cccccccc-3333-3333-3333-333333333333'] = _org_row(
        'cccccccc-3333-3333-3333-333333333333',
    )
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    payload = tenancy_endpoints.list_admin_customers(_request())

    assert payload['count'] == 3
    assert len(connection.audit_rows) == 1
    # The count is stated instead, so the trail says how much was read.
    assert connection.audit_rows[0]['metadata']['result_count'] == 3


def test_13b_the_directory_read_is_not_mirrored_into_any_customer_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.list_admin_customers(_request())

    assert connection.workspace_rows(WS_A) == []
    assert connection.workspace_rows(WS_B) == []
    assert connection.audit_rows[0]['metadata']['customer_visible'] is False


# ── 2 — the customer-specific detail read ────────────────────────────────────

def test_2_customer_detail_read_produces_an_organization_scoped_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.get_admin_customer(ORG_A, _request())

    internal = [r for r in connection.internal_rows()
                if r['action'] == staff_access.ACTION_CUSTOMER_DETAIL_VIEWED]
    assert len(internal) == 1
    meta = internal[0]['metadata']
    assert meta['organization_id'] == ORG_A            # WHAT customer
    assert meta['access_mode'] == 'read'
    assert 'members' in meta['object_categories']      # WHAT category
    assert 'data_lifecycle' in meta['object_categories']
    assert internal[0]['entity_id'] == ORG_A


def test_2b_a_detail_read_of_an_unknown_organization_records_no_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 first: an access that did not happen leaves no record that it did."""
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.get_admin_customer('dddddddd-4444-4444-4444-444444444444', _request())

    assert exc_info.value.status_code == 404
    assert connection.audit_rows == []


# ── 3 — feedback ─────────────────────────────────────────────────────────────

def test_3_feedback_read_produces_a_record(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.list_admin_feedback(_request(), organization_id=ORG_A)

    internal = [r for r in connection.internal_rows()
                if r['action'] == staff_access.ACTION_FEEDBACK_VIEWED]
    assert len(internal) == 1
    assert internal[0]['metadata']['organization_id'] == ORG_A
    assert internal[0]['metadata']['access_mode'] == 'read'
    # Scoped to one customer, so that customer can see it.
    assert connection.workspace_rows(WS_A)


def test_3b_the_unscoped_feedback_roadmap_view_is_recorded_but_not_mirrored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.list_admin_feedback(_request())

    assert len(connection.audit_rows) == 1
    assert connection.audit_rows[0]['workspace_id'] is None
    assert connection.workspace_rows(WS_A) == []
    assert connection.workspace_rows(WS_B) == []


def test_3c_the_pilot_request_queue_read_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    monkeypatch.setattr(tenancy_endpoints, '_require_pilot_request_schema', lambda *_: None)

    tenancy_endpoints.list_admin_pilot_requests(_request())

    rows = connection.staff_rows()
    assert [r['action'] for r in rows] == [staff_access.ACTION_PILOT_REQUEST_QUEUE_VIEWED]
    assert rows[0]['user_id'] == STAFF_USER
    # Pre-tenant: there is no workspace history for an application to belong to.
    assert rows[0]['workspace_id'] is None


# ── 4 / 5 / 6 — the writes that were already audited stay audited ────────────

def test_4_plan_change_remains_audited_and_is_mirrored_to_the_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.set_admin_customer_plan(ORG_A, {'plan': 'scale'}, _request())

    internal = [r for r in connection.internal_rows() if r['action'] == 'organization.plan_changed']
    assert len(internal) == 1, 'the long-standing internal action name must survive'
    assert internal[0]['metadata']['previous_plan'] == 'pilot'
    assert internal[0]['metadata']['new_plan'] == 'scale'
    assert internal[0]['user_id'] == STAFF_USER
    # …and exactly one internal row: the new chokepoint must not duplicate it.
    assert len(connection.internal_rows()) == 1

    mirror = connection.workspace_rows(WS_A)
    assert [r['action'] for r in mirror] == [staff_access.ACTION_PLAN_CHANGED]
    assert mirror[0]['metadata']['change'] == {'previous_plan': 'pilot', 'new_plan': 'scale'}
    assert mirror[0]['metadata']['access_mode'] == 'write'
    # Provenance: the customer row names the internal row it came from.
    assert mirror[0]['metadata']['staff_access_record_id'] == internal[0]['id']


def test_5_status_change_remains_audited_and_is_mirrored(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.set_admin_customer_status(ORG_A, {'status': 'suspended'}, _request())

    internal = [r for r in connection.internal_rows() if r['action'] == 'organization.status_changed']
    assert len(internal) == 1
    assert internal[0]['metadata']['previous_status'] == 'active'
    assert internal[0]['metadata']['new_status'] == 'suspended'

    mirror = connection.workspace_rows(WS_A)
    assert mirror[0]['action'] == staff_access.ACTION_STATUS_CHANGED
    assert mirror[0]['metadata']['change'] == {
        'previous_status': 'active', 'new_status': 'suspended',
    }


def test_6_pilot_deadline_change_remains_audited_and_is_mirrored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.extend_admin_customer_evaluation(
        ORG_A, {'expires_at': '2026-09-30T00:00:00Z'}, _request(),
    )

    internal = [r for r in connection.internal_rows()
                if r['action'] == 'organization.evaluation_expiry_set']
    assert len(internal) == 1
    assert internal[0]['metadata']['new_expires_at'].startswith('2026-09-30')

    mirror = connection.workspace_rows(WS_A)
    assert mirror[0]['action'] == staff_access.ACTION_PILOT_DEADLINE_CHANGED
    assert mirror[0]['metadata']['change']['new_pilot_end'].startswith('2026-09-30')
    assert mirror[0]['metadata']['summary'] == 'Decoda staff changed the Pilot end date'


def test_6b_removing_a_deadline_reads_as_open_ended_not_as_a_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Pilot with no deadline is a STATE. An empty cell would read as unknown."""
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.extend_admin_customer_evaluation(ORG_A, {'expires_at': None}, _request())

    mirror = connection.workspace_rows(WS_A)
    assert mirror[0]['metadata']['change']['new_pilot_end'] == 'open_ended'


def test_6c_a_write_is_mirrored_into_every_workspace_of_that_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.set_admin_customer_status(ORG_A, {'status': 'suspended'}, _request())

    assert len(connection.workspace_rows(WS_A)) == 1
    assert len(connection.workspace_rows(WS_A2)) == 1
    assert connection.workspace_rows(WS_B) == []


# ── 7 / 8 — actor attribution is server-side ─────────────────────────────────

def test_7_the_staff_actor_is_the_authenticated_account(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.get_admin_customer(ORG_A, _request())

    internal = connection.internal_rows()[0]
    assert internal['user_id'] == STAFF_USER
    assert internal['metadata']['actor_user_id'] == STAFF_USER
    assert internal['metadata']['actor_type'] == staff_access.ACTOR_TYPE_DECODA_STAFF


def test_8_a_request_cannot_select_a_different_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headers naming another user change nothing: the actor comes from the session."""
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.get_admin_customer(
        ORG_A,
        _request({
            'x-user-id': CUSTOMER_USER,
            'x-actor-user-id': CUSTOMER_USER,
            'user_id': CUSTOMER_USER,
        }),
    )

    internal = connection.internal_rows()[0]
    assert internal['user_id'] == STAFF_USER
    assert CUSTOMER_USER not in json.dumps(internal['metadata'])


def test_8b_no_staff_endpoint_accepts_an_actor_parameter() -> None:
    """Structural: there is no argument through which an actor could be named."""
    forbidden = {'user', 'user_id', 'actor', 'actor_user_id', 'is_internal_admin', 'role'}
    for name in (
        'list_admin_customers', 'get_admin_customer', 'list_admin_feedback',
        'list_admin_pilot_requests', 'set_admin_customer_plan',
        'set_admin_customer_status', 'extend_admin_customer_evaluation',
    ):
        parameters = set(inspect.signature(getattr(tenancy_endpoints, name)).parameters)
        assert not parameters & forbidden, f'{name} exposes an actor parameter'


def test_8c_the_chokepoint_refuses_to_record_without_a_server_resolved_actor() -> None:
    connection = _connection()
    with pytest.raises(staff_access.StaffAccessNotRecorded):
        staff_access.record_staff_access(
            connection,
            actor_user_id='',
            action=staff_access.ACTION_CUSTOMER_DETAIL_VIEWED,
            access_type=staff_access.ACCESS_READ,
            object_type='organization',
            organization_id=ORG_A,
        )
    assert connection.audit_rows == []


def test_8d_an_unknown_action_is_refused_rather_than_recorded_unclassified() -> None:
    connection = _connection()
    with pytest.raises(staff_access.StaffAccessNotRecorded):
        staff_access.record_staff_access(
            connection,
            actor_user_id=STAFF_USER,
            action='staff.invented_event',
            access_type=staff_access.ACCESS_READ,
            object_type='organization',
            organization_id=ORG_A,
        )
    assert connection.audit_rows == []


# ── 9 — no credential ever reaches an audit row ──────────────────────────────

_CREDENTIAL_SHAPED = re.compile(
    r'password|totp|recovery_code|private_key|seed_phrase|mnemonic|api_secret|'
    r'bearer|authorization|cookie|session_token|webhook_secret|signing_key',
    re.IGNORECASE,
)


def test_9_no_staff_audit_metadata_carries_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    request = _request({
        'authorization': 'Bearer super-secret-token',
        'cookie': 'session=super-secret-cookie',
        'x-request-id': 'req-1234',
    })

    tenancy_endpoints.get_admin_customer(ORG_A, request)
    tenancy_endpoints.set_admin_customer_plan(ORG_A, {'plan': 'scale'}, request)
    tenancy_endpoints.list_admin_customers(request)

    assert connection.audit_rows
    for row in connection.audit_rows:
        serialized = json.dumps(row['metadata'])
        assert not _CREDENTIAL_SHAPED.search(serialized), serialized
        assert 'super-secret' not in serialized


def test_9b_the_chokepoint_refuses_a_forbidden_metadata_key() -> None:
    connection = _connection()
    for key in ('password', 'bearer_token', 'telemetry_payload', 'internal_note'):
        with pytest.raises(staff_access.StaffAccessNotRecorded):
            staff_access.record_staff_access(
                connection,
                actor_user_id=STAFF_USER,
                action=staff_access.ACTION_PLAN_CHANGED,
                access_type=staff_access.ACCESS_WRITE,
                object_type='organization',
                organization_id=ORG_A,
                change={key: 'value'},
            )
    assert connection.audit_rows == []


def test_9c_a_non_scalar_change_value_is_refused_so_no_payload_can_travel() -> None:
    connection = _connection()
    with pytest.raises(staff_access.StaffAccessNotRecorded):
        staff_access.record_staff_access(
            connection,
            actor_user_id=STAFF_USER,
            action=staff_access.ACTION_PLAN_CHANGED,
            access_type=staff_access.ACCESS_WRITE,
            object_type='organization',
            organization_id=ORG_A,
            change={'previous_plan': {'raw': 'request body'}},
        )
    assert connection.audit_rows == []


def test_9d_the_customer_mirror_carries_no_staff_identity_or_staff_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    tenancy_endpoints.get_admin_customer(ORG_A, _request(ip='198.51.100.77'))

    internal = connection.internal_rows()[0]
    assert internal['ip_address'] == '198.51.100.77'   # kept where it belongs
    mirror = connection.workspace_rows(WS_A)[0]
    assert mirror['user_id'] is None
    assert mirror['ip_address'] is None
    serialized = json.dumps(mirror['metadata'])
    assert STAFF_USER not in serialized
    assert STAFF_EMAIL not in serialized
    assert '198.51.100.77' not in serialized


def test_9e_an_internal_note_stays_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    request = _request({
        staff_access.REASON_HEADER: 'support_case',
        staff_access.REASON_NOTE_HEADER: 'ticket DEC-4471 escalation',
    })

    tenancy_endpoints.get_admin_customer(ORG_A, request)

    internal = connection.internal_rows()[0]
    assert internal['metadata']['reason'] == 'support_case'
    assert internal['metadata']['reason_note'] == 'ticket DEC-4471 escalation'
    mirror = connection.workspace_rows(WS_A)[0]
    # The customer learns WHY in the published vocabulary, not the internal note.
    assert mirror['metadata']['reason'] == 'support_case'
    assert 'DEC-4471' not in json.dumps(mirror['metadata'])


def test_9f_an_unknown_reason_is_refused_rather_than_recorded_as_free_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.get_admin_customer(
            ORG_A, _request({staff_access.REASON_HEADER: 'because i felt like it'}),
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail['code'] == staff_access.CODE_INVALID_ACCESS_REASON
    assert connection.audit_rows == []


def test_9g_a_credential_pasted_into_the_note_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.get_admin_customer(
            ORG_A, _request({staff_access.REASON_NOTE_HEADER: '0x' + 'a' * 64}),
        )
    assert exc_info.value.status_code == 400
    assert connection.audit_rows == []


# ── 10 / 11 / 12 — what the customer sees, and what they must not ────────────

def test_10_the_customer_workspace_can_see_the_staff_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    tenancy_endpoints.set_admin_customer_status(ORG_A, {'status': 'suspended'}, _request())

    # Now the customer reads their OWN workspace audit history.
    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)
    payload = pilot.list_audit_events(_request({'x-workspace-id': WS_A}))

    staff_events = [e for e in payload['events']
                    if e['actor_type'] == staff_access.ACTOR_TYPE_DECODA_STAFF]
    assert len(staff_events) == 1
    event = staff_events[0]
    assert event['actor'] == 'Decoda staff'
    assert event['actor_type_label'] == 'Decoda staff'
    assert event['access_mode_label'] == 'Change'
    assert event['summary'] == 'Decoda staff changed the organization status'
    assert event['source_ip'] is None
    assert STAFF_EMAIL not in json.dumps(event)


def test_10b_a_staff_read_reads_as_read_only_in_the_customer_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    tenancy_endpoints.get_admin_customer(ORG_A, _request())

    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)
    payload = pilot.list_audit_events(_request({'x-workspace-id': WS_A}))

    event = payload['events'][0]
    assert event['access_mode_label'] == 'Read only'
    assert event['summary'] == 'Decoda staff viewed organization support details'


def test_10c_an_ordinary_workspace_event_is_not_labelled_decoda_staff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    pilot.log_audit(
        connection, action='asset.create', entity_type='asset', entity_id='asset-1',
        request=None, user_id=CUSTOMER_USER, workspace_id=WS_A, metadata={},
    )
    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)

    payload = pilot.list_audit_events(_request({'x-workspace-id': WS_A}))

    assert payload['events'][0]['actor_type'] == staff_access.ACTOR_TYPE_WORKSPACE_MEMBER
    assert payload['events'][0]['actor_type_label'] == 'Workspace member'


def test_11_a_customer_cannot_see_staff_events_for_another_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    tenancy_endpoints.set_admin_customer_plan(ORG_B, {'plan': 'enterprise'}, _request())

    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)
    payload = pilot.list_audit_events(_request({'x-workspace-id': WS_A}))

    assert payload['events'] == []
    assert ORG_B not in json.dumps(payload)


def test_12_workspace_a_cannot_see_workspace_b_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    tenancy_endpoints.set_admin_customer_status(ORG_A, {'status': 'suspended'}, _request())
    tenancy_endpoints.set_admin_customer_status(ORG_B, {'status': 'suspended'}, _request())

    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)
    payload_a = pilot.list_audit_events(_request({'x-workspace-id': WS_A}))
    payload_b = pilot.list_audit_events(_request({'x-workspace-id': WS_B}))

    assert {e['metadata']['organization_id'] for e in payload_a['events']} == {ORG_A}
    assert {e['metadata']['organization_id'] for e in payload_b['events']} == {ORG_B}
    assert ORG_B not in json.dumps(payload_a['events'])


def test_12b_the_internal_record_is_never_returned_by_the_customer_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The NULL-workspace internal rows stay out of every workspace history."""
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    tenancy_endpoints.list_admin_customers(_request())
    tenancy_endpoints.get_admin_customer(ORG_A, _request())

    assert len(connection.internal_rows()) == 2
    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)
    for workspace in (WS_A, WS_A2, WS_B):
        events = pilot.list_audit_events(_request({'x-workspace-id': workspace}))['events']
        assert all(e['action'] != staff_access.ACTION_CUSTOMER_LIST_VIEWED for e in events)


def test_12c_the_mirror_joins_the_workspace_hash_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A staff row is a real member of the customer's chain, not a visitor."""
    connection = _connection()
    pilot.log_audit(
        connection, action='asset.create', entity_type='asset', entity_id='asset-1',
        request=None, user_id=CUSTOMER_USER, workspace_id=WS_A, metadata={},
    )
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    tenancy_endpoints.get_admin_customer(ORG_A, _request())

    rows = connection.workspace_rows(WS_A)
    assert len(rows) == 2
    assert rows[1]['previous_row_hash'] == rows[0]['row_hash']
    assert rows[1]['row_hash']


# ── 14 — a refusal is not an access ──────────────────────────────────────────

@pytest.mark.parametrize(
    'call',
    [
        lambda: tenancy_endpoints.list_admin_customers(_request()),
        lambda: tenancy_endpoints.get_admin_customer(ORG_A, _request()),
        lambda: tenancy_endpoints.list_admin_feedback(_request(), organization_id=ORG_A),
        lambda: tenancy_endpoints.list_admin_pilot_requests(_request()),
        lambda: tenancy_endpoints.set_admin_customer_plan(ORG_A, {'plan': 'scale'}, _request()),
        lambda: tenancy_endpoints.set_admin_customer_status(ORG_A, {'status': 'suspended'}, _request()),
        lambda: tenancy_endpoints.extend_admin_customer_evaluation(ORG_A, {'days': 30}, _request()),
    ],
)
def test_14_a_refused_internal_admin_request_records_no_access(
    monkeypatch: pytest.MonkeyPatch, call: Any,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)

    with pytest.raises(HTTPException) as exc_info:
        call()

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == org_service.CODE_INTERNAL_ADMIN_REQUIRED
    assert connection.audit_rows == [], 'a refusal reached no customer data'


# ── 15 / 16 — there is no impersonation, and staff tooling mints no session ──

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = (REPO_ROOT / 'services', REPO_ROOT / 'apps' / 'web' / 'app')
SOURCE_SUFFIXES = ('.py', '.ts', '.tsx')

#: A mechanism, not a mention. Matching on definition and call shapes keeps the
#: search from firing on prose — and keeps it from being silenced by prose, too.
_IMPERSONATION_MECHANISMS = (
    r'\bdef\s+(impersonate|impersonation|act_as|login_as|assume_user|support_login|'
    r'session_override|elevate_session|break_glass)\w*\s*\(',
    r'\b(impersonate|impersonate_user|act_as_user|login_as|assume_user|support_login|'
    r'elevate_session|session_override)\s*\(',
    r'[\'"`]/(?:api/)?(impersonate|login-as|support-login|break-glass|elevate)\b',
    r'\bimpersonation_token\b|\bimpersonated_user_id\b|\bsupport_session\b',
)

#: The only files whose text may mention these words at all, and why. Anything
#: else that starts talking about impersonation fails this test on purpose.
_KEYWORD_ALLOWLIST = {
    # A storage break-glass override (local, non-WORM export storage). Nothing to
    # do with sessions, identity, or tenant access.
    'services/api/app/export_storage.py',
    'services/api/tests/test_export_storage_hardening.py',
    # Prose: "diagnostic evidence ... can never impersonate a scheduled poll".
    'services/api/tests/test_monitoring_sources_diagnostic.py',
    # The public Trust page, which STATES that no impersonation mechanism exists.
    # Saying so is the opposite of building one.
    'apps/web/app/trust/page.tsx',
    # This file.
    'services/api/tests/test_staff_access_audit.py',
}

# ``(?:\b|_)`` on BOTH sides rather than ``\b``: an underscore is a word
# character, so ``\bbreak_glass\b`` would miss ``_is_break_glass_local_allowed``
# entirely and the search would quietly stop covering private helpers. Keeping
# the boundary alternative (rather than dropping it) is what stops ``act_as``
# from firing on ordinary identifiers such as ``contract_asset``.
_KEYWORD_RE = re.compile(
    r'impersonat|(?:\b|_)(?:act_as|login_as|assume_user|support_login|'
    r'session_override|elevate_session|break_glass)(?:\b|_)',
    re.IGNORECASE,
)


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        for path in root.rglob('*'):
            if path.suffix in SOURCE_SUFFIXES and 'node_modules' not in path.parts:
                files.append(path)
    return files


def test_16_no_impersonation_mechanism_exists_anywhere_in_the_repository() -> None:
    patterns = [re.compile(p, re.IGNORECASE) for p in _IMPERSONATION_MECHANISMS]
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding='utf-8', errors='ignore')
        for pattern in patterns:
            if pattern.search(text):
                offenders.append(f'{path.relative_to(REPO_ROOT)}: {pattern.pattern}')
    assert offenders == [], f'an impersonation mechanism appeared: {offenders}'


def test_16b_the_keyword_search_has_no_unexplained_hits() -> None:
    """The broad search, so a new mechanism cannot hide behind a new spelling."""
    unexplained = sorted(
        str(path.relative_to(REPO_ROOT)).replace('\\', '/')
        for path in _source_files()
        if _KEYWORD_RE.search(path.read_text(encoding='utf-8', errors='ignore'))
        and str(path.relative_to(REPO_ROOT)).replace('\\', '/') not in _KEYWORD_ALLOWLIST
    )
    assert unexplained == [], f'unexplained impersonation-shaped code: {unexplained}'


def test_15_no_staff_endpoint_mints_a_customer_session_or_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=STAFF_USER)
    monkeypatch.setattr(tenancy_endpoints, '_require_pilot_request_schema', lambda *_: None)

    def _refuse(*_a: Any, **_k: Any) -> str:
        raise AssertionError('a staff endpoint must never mint a session token')

    monkeypatch.setattr(pilot, 'create_access_token', _refuse)

    tenancy_endpoints.list_admin_customers(_request())
    tenancy_endpoints.get_admin_customer(ORG_A, _request())
    tenancy_endpoints.list_admin_feedback(_request(), organization_id=ORG_A)
    tenancy_endpoints.list_admin_pilot_requests(_request())
    tenancy_endpoints.set_admin_customer_plan(ORG_A, {'plan': 'scale'}, _request())
    tenancy_endpoints.set_admin_customer_status(ORG_A, {'status': 'suspended'}, _request())

    written = ' '.join(sql.lower() for sql, _ in connection.writes)
    assert 'auth_sessions' not in written
    assert 'insert into users' not in written


def test_15b_staff_access_grants_no_workspace_membership() -> None:
    """Internal-admin authorization writes nothing. It is a check, not a grant."""
    connection = _connection()
    request = _request()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            pilot, 'authenticate_with_connection',
            lambda *_a, **_k: {'id': STAFF_USER, 'email': STAFF_EMAIL},
        )
        user = org_service.require_internal_admin(connection, request)

    assert user['id'] == STAFF_USER
    assert connection.writes == []
    assert connection.audit_rows == []


# ── Phase 13 — every internal-admin route has an intentional audit decision ──

def test_every_internal_admin_handler_records_or_audits_its_access() -> None:
    """A new staff route cannot be added without deciding how it is recorded.

    Structural rather than behavioural on purpose: this fails on the day someone
    writes a handler that authorizes internal staff and then reads customer data
    without a record, which is exactly the gap this work closed.
    """
    module_source = Path(tenancy_endpoints.__file__).read_text(encoding='utf-8')
    handlers = {
        name: inspect.getsource(obj)
        for name, obj in vars(tenancy_endpoints).items()
        if inspect.isfunction(obj) and obj.__module__ == tenancy_endpoints.__name__
    }
    authorizing = {
        name: source for name, source in handlers.items()
        if 'require_internal_admin' in source
    }
    assert authorizing, 'the internal-admin surface disappeared'
    undecided = [
        name for name, source in authorizing.items()
        if 'record_staff_access' not in source
        and '_admin_lifecycle_audit' not in source
        and '_pilot_request_audit' not in source
    ]
    assert undecided == [], f'internal-admin handlers with no audit decision: {undecided}'
    # And the chokepoint is the shared one, not a hand-written insert per route.
    assert 'INSERT INTO audit_logs' not in module_source


def test_the_published_event_vocabulary_matches_what_is_actually_recorded() -> None:
    """No event is declared for a capability this repository does not have."""
    module_source = Path(tenancy_endpoints.__file__).read_text(encoding='utf-8')
    for action in staff_access.STAFF_ACTIONS:
        constant = next(
            name for name, value in vars(staff_access).items()
            if name.startswith('ACTION_') and value == action
        )
        assert f'staff_access.{constant}' in module_source, f'{action} is recorded nowhere'
    assert staff_access.CUSTOMER_VISIBLE_ACTIONS <= set(staff_access.STAFF_ACTIONS)
    for action in staff_access.CUSTOMER_VISIBLE_ACTIONS:
        assert action in staff_access.CUSTOMER_ACTION_SUMMARIES, (
            f'{action} reaches a customer with no sentence to render'
        )


def test_14b_an_unauthorized_caller_is_refused_before_the_reason_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403, not 400: the refusal must not confirm which headers staff tooling uses."""
    connection = _connection()
    _sign_in(monkeypatch, connection, user_id=CUSTOMER_USER)

    with pytest.raises(HTTPException) as exc_info:
        tenancy_endpoints.get_admin_customer(
            ORG_A, _request({staff_access.REASON_HEADER: 'not-a-real-reason'}),
        )

    assert exc_info.value.status_code == 403
    assert connection.audit_rows == []


def test_only_the_chokepoint_can_write_the_decoda_staff_label() -> None:
    """The label a customer trusts has exactly two writers, both server-side.

    A customer-facing endpoint cannot stamp ``decoda_staff`` onto an audit row,
    because outside these modules the value is never written at all — and the
    one module that READS it writes no audit rows in the function that does.
    """
    from services.api.app import governance

    writers = {
        'services/api/app/staff_access.py',               # the chokepoint itself
        'services/api/app/domains/tenancy/endpoints.py',  # the lifecycle writes
    }
    readers = {
        'services/api/app/governance.py',                 # renders it, never writes it
        'services/api/app/pilot.py',                      # ditto, via audit_actor_type
    }
    mentions = {
        str(path.relative_to(REPO_ROOT)).replace('\\', '/')
        for path in (REPO_ROOT / 'services' / 'api' / 'app').rglob('*.py')
        if re.search(r"""ACTOR_TYPE_DECODA_STAFF|['"]decoda_staff['"]""",
                     path.read_text(encoding='utf-8', errors='ignore'))
    }
    assert mentions <= writers | readers, f'an unexpected module handles the label: {mentions}'
    assert writers <= mentions, 'the chokepoint disappeared'

    for reader in (governance.list_governance_changes, pilot.list_audit_events):
        source = inspect.getsource(reader)
        assert 'log_audit' not in source, f'{reader.__name__} must only read the trail'
    assert 'ACTOR_TYPE_DECODA_STAFF' in inspect.getsource(governance.list_governance_changes)


def test_a_staff_write_is_not_buried_as_a_routine_change() -> None:
    """The governance change log must not render a staff write as low risk."""
    from services.api.app import governance

    for action in (
        staff_access.ACTION_PLAN_CHANGED,
        staff_access.ACTION_STATUS_CHANGED,
        staff_access.ACTION_PILOT_DEADLINE_CHANGED,
    ):
        assert governance.classify_audit_action(action, {}) == governance.RISK_HIGH
    for action in (
        staff_access.ACTION_CUSTOMER_DETAIL_VIEWED,
        staff_access.ACTION_FEEDBACK_VIEWED,
    ):
        # A read changes nothing; it is carried for visibility, not as a change.
        assert governance.classify_audit_action(action, {}) == governance.RISK_LOW
