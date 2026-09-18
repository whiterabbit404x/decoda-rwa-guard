"""The Pilot retention / deletion lifecycle.

These tests exist to keep one promise: Decoda does not state a retention period
it does not apply, and does not state a deletion it does not perform.

They therefore assert on the SQL the engine emits and the rows it queues, not on
a mocked "it worked" — a test that accepted a stubbed return value would pass
just as happily against the bug this whole change fixes (a policy table nothing
ever wrote to, so a worker that swept nothing, under a Privacy page that promised
deletion).
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.api.app import data_retention, entitlements as ent, organizations as org_service, pilot_retention

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
WORKSPACE = 'workspace-a'
ORGANIZATION = 'org-a'


class Result:
    def __init__(self, *, rows=None, row=None, rowcount=0):
        self._rows = rows or []
        self._row = row
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class RecordingConnection:
    """psycopg-shaped, records every statement so a test can assert on real SQL."""

    def __init__(self, handler=None):
        self.calls: list[tuple[str, tuple]] = []
        self.handler = handler

    def execute(self, sql, params=()):
        normalized = ' '.join(str(sql).split())
        self.calls.append((normalized, tuple(params or ())))
        if self.handler:
            result = self.handler(normalized, params)
            if result is not None:
                return result
        return Result()

    def commit(self):
        return None

    def sql_containing(self, needle: str) -> list[tuple[str, tuple]]:
        return [call for call in self.calls if needle in call[0]]


def schema_ready_handler(extra=None):
    """A connection handler where migration 0154 IS applied."""

    def handler(sql, params):
        if 'information_schema.columns' in sql:
            return Result(row={'present': len(list(params[1]))})
        if extra:
            return extra(sql, params)
        return None

    return handler


def organization_row(**overrides):
    row = {
        'id': ORGANIZATION,
        'plan': ent.PLAN_PILOT,
        'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=40),
        'evaluation_expires_at': None,
        'pilot_ended_at': None,
        'pilot_grace_ends_at': None,
        'pilot_end_reason': None,
    }
    row.update(overrides)
    return row


# ── 1. a new Pilot is provisioned WITH retention ─────────────────────────────
def test_1_new_pilot_workspace_receives_the_full_default_policy():
    connection = RecordingConnection(schema_ready_handler(lambda sql, params: Result(rowcount=1)))

    inserted = pilot_retention.ensure_workspace_retention_policies(
        connection, workspace_id=WORKSPACE, plan=ent.PLAN_PILOT,
    )

    assert inserted == len(pilot_retention.PILOT_DATA_CLASSES) == 7
    seeded = {call[1][1]: call[1] for call in connection.sql_containing('INSERT INTO workspace_retention_policies')}
    assert set(seeded) == set(pilot_retention.PILOT_DATA_CLASSES)
    for data_class, params in seeded.items():
        assert params[0] == WORKSPACE, 'every policy row is workspace-scoped'
        assert params[2] == pilot_retention.PILOT_RETENTION_DAYS[data_class]
        assert params[3] == pilot_retention.PILOT_DELETION_MODES[data_class]
        assert params[4] == 'pilot_default', 'a seeded period is never reported as the customer’s own'
        assert params[5] is None, 'a brand-new workspace holds nothing old enough to delete'
    for sql, _ in connection.sql_containing('INSERT INTO workspace_retention_policies'):
        assert 'ON CONFLICT (workspace_id, data_class) DO NOTHING' in sql, (
            'seeding must never overwrite a period the customer configured'
        )


def test_1b_every_provisioning_path_seeds_retention():
    source = Path('services/api/app/pilot.py').read_text(encoding='utf-8')
    # The approved-Pilot path, the additional-workspace path, and the demo path.
    assert source.count('pilot_retention.ensure_workspace_retention_policies(') == 3
    for marker in ('def provision_pilot_organization(', 'def create_workspace_for_user(', 'def seed_demo_workspace('):
        assert marker in source


def test_1c_worker_heals_a_workspace_that_was_provisioned_without_one():
    rows = [{'workspace_id': 'workspace-legacy'}]

    def extra(sql, params):
        if 'NOT EXISTS ( SELECT 1 FROM workspace_retention_policies' in sql:
            assert params[0] == ent.PLAN_PILOT, 'only Pilot workspaces are seeded'
            return Result(rows=rows)
        if 'INSERT INTO workspace_retention_policies' in sql:
            return Result(rowcount=1)
        return None

    connection = RecordingConnection(schema_ready_handler(extra))
    assert pilot_retention.backfill_missing_policies(connection) == 7
    effective = {call[1][5] for call in connection.sql_containing('INSERT INTO workspace_retention_policies')}
    assert len(effective) == 1
    starts_at = effective.pop()
    assert starts_at is not None and starts_at > datetime.now(timezone.utc), (
        'a healed workspace must not be swept on the cycle that healed it'
    )


# ── 2. an ACTIVE open-ended Pilot is not scheduled for anything ──────────────
def test_2_active_open_ended_pilot_has_no_deadline_and_no_deletion():
    lifecycle = pilot_retention.pilot_data_lifecycle(organization_row(), now=NOW)

    assert lifecycle['state'] == pilot_retention.DATA_STATE_ACTIVE
    assert lifecycle['ended_at'] is None
    assert lifecycle['grace_ends_at'] is None
    assert lifecycle['scheduled_deletion_at'] is None
    assert lifecycle['security_record_deleted_at'] is None
    assert lifecycle['export_available'] is True


def test_2b_reconciling_an_active_pilot_records_no_end(monkeypatch):
    recorded: list = []
    monkeypatch.setattr(pilot_retention, 'record_pilot_end', lambda *a, **k: recorded.append(k) or {})
    connection = RecordingConnection(schema_ready_handler())

    org_service.reconcile_pilot_retention(connection, organization_row())

    assert recorded == []
    assert connection.sql_containing('SET pilot_ended_at = NULL'), 'the live state is reasserted, never invented'


def test_2c_a_dated_but_unexpired_pilot_is_still_active():
    lifecycle = pilot_retention.pilot_data_lifecycle(
        organization_row(evaluation_expires_at=NOW + timedelta(days=10)), now=NOW,
    )
    assert lifecycle['state'] == pilot_retention.DATA_STATE_ACTIVE
    assert lifecycle['scheduled_deletion_at'] is None


# ── 3-6. ending a Pilot starts the grace window and queues the deletions ─────
def _record_end(connection=None, ended_at=NOW):
    def extra(sql, params):
        if sql.startswith('SELECT id, plan, status, pilot_ended_at'):
            return Result(row=organization_row())
        if 'FROM workspaces WHERE organization_id' in sql:
            return Result(rows=[{'id': WORKSPACE}])
        if 'INSERT INTO data_deletion_requests' in sql:
            return Result(rowcount=1)
        return None

    connection = connection or RecordingConnection(schema_ready_handler(extra))
    return connection, pilot_retention.record_pilot_end(
        connection, organization_id=ORGANIZATION, ended_at=ended_at, reason='ended_by_internal_admin',
    )


def test_3_ending_a_pilot_records_the_end_and_a_30_day_grace_window():
    connection, result = _record_end()

    assert result['recorded'] is True
    assert result['pilot_ended_at'] == NOW.isoformat()
    assert result['pilot_grace_ends_at'] == (NOW + timedelta(days=30)).isoformat()
    assert pilot_retention.PILOT_GRACE_PERIOD_DAYS == 30
    update = connection.sql_containing('SET pilot_ended_at = %s, pilot_grace_ends_at = %s')[0]
    assert update[1][0] == NOW and update[1][1] == NOW + timedelta(days=30)
    assert 'AND pilot_ended_at IS NULL' in update[0], 'ending twice must not move a date the customer was given'


def test_4_data_stays_readable_and_exportable_during_the_grace_window():
    lifecycle = pilot_retention.pilot_data_lifecycle(
        organization_row(status=ent.STATUS_EXPIRED, pilot_ended_at=NOW - timedelta(days=5),
                          pilot_grace_ends_at=NOW + timedelta(days=25)),
        now=NOW,
    )
    assert lifecycle['state'] == pilot_retention.DATA_STATE_GRACE
    assert lifecycle['export_available'] is True
    assert lifecycle['scheduled_deletion_at'] == (NOW + timedelta(days=25)).isoformat()


def test_5_no_deletion_is_due_before_the_deadline():
    connection, _ = _record_end()
    queued = connection.sql_containing('INSERT INTO data_deletion_requests')
    assert len(queued) == 2
    grace_end = NOW + timedelta(days=30)
    operational = queued[0][1]
    # cutoff_at (index 4) and next_attempt_at (index 8) are both the grace deadline:
    # nothing before it is deleted, and the worker cannot claim it before it.
    assert operational[4] == grace_end
    assert operational[8] == grace_end
    assert "'approved'" in queued[0][0], 'the request is queued approved and waits on its date'
    claim_sql = ' '.join(Path('services/api/app/data_retention.py').read_text(encoding='utf-8').split())
    assert 'next_attempt_at <= NOW()' in claim_sql, (
        'the worker must not claim a request whose deadline is in the future'
    )


def test_6_the_queued_operational_purge_covers_the_customer_operational_classes():
    connection, _ = _record_end()
    operational = connection.sql_containing('INSERT INTO data_deletion_requests')[0][1]
    assert json.loads(operational[3]) == [*pilot_retention.PILOT_PURGE_OPERATIONAL_CLASSES, 'audit_logs']
    assert operational[2] == pilot_retention.REQUEST_TYPE_PILOT_PURGE
    modes = json.loads(operational[6])['deletion_modes']
    assert modes == {
        'telemetry': 'hard_delete', 'detections': 'hard_delete', 'alerts': 'hard_delete',
        'incidents': 'hard_delete', 'exports': 'hard_delete', 'audit_logs': 'anonymize',
    }


def test_6b_a_dated_pilot_that_expired_is_ended_at_its_own_deadline_not_at_notice_time(monkeypatch):
    expiry = NOW - timedelta(days=3)
    captured: dict = {}

    def fake_record(connection, *, organization_id, ended_at=None, reason=None, ended_by_user_id=None):
        captured.update({'ended_at': ended_at, 'reason': reason})
        return {'recorded': True}

    monkeypatch.setattr(pilot_retention, 'record_pilot_end', fake_record)

    def extra(sql, params):
        if 'evaluation_expires_at < NOW()' in sql:
            return Result(rows=[{'id': ORGANIZATION, 'evaluation_expires_at': expiry}])
        return None

    connection = RecordingConnection(schema_ready_handler(extra))
    assert pilot_retention.stamp_expired_pilots(connection) == {'stamped': 1}
    assert captured['ended_at'] == expiry, 'the grace window runs from the deadline the customer was given'
    assert captured['reason'] == pilot_retention.END_REASON_EVALUATION_EXPIRED


def test_6c_an_open_ended_pilot_is_never_stamped_with_an_invented_end_date():
    sql = ' '.join(
        call[0] for call in RecordingConnection(schema_ready_handler(lambda s, p: Result(rows=[]))).calls
    )
    connection = RecordingConnection(schema_ready_handler(lambda s, p: Result(rows=[])))
    pilot_retention.stamp_expired_pilots(connection)
    scan = connection.sql_containing('evaluation_expires_at < NOW()')[0][0]
    assert 'evaluation_expires_at IS NOT NULL' in scan, (
        'a Pilot with no deadline has not ended and must never be selected here'
    )
    assert sql == ''


# ── 7-11. what the purge actually deletes ────────────────────────────────────
def purge_request(classes, modes, cutoff=NOW):
    return {
        'id': 'request-1', 'workspace_id': WORKSPACE, 'data_classes': classes,
        'subject_user_id': None, 'cutoff_at': cutoff,
        'result': {'deletion_modes': modes},
    }


def _execute(classes, modes, handler=None, cutoff=NOW):
    def base(sql, params):
        if 'FROM workspace_legal_holds' in sql or 'FROM retention_external_artifacts' in sql:
            return Result(rows=[])
        if handler:
            return handler(sql, params)
        if sql.startswith('DELETE FROM'):
            return Result(rowcount=1)
        return None

    connection = RecordingConnection(base)
    result = data_retention.execute_request(
        connection, purge_request(classes, modes, cutoff), worker_name='retention-worker',
    )
    return connection, result


def _deleted_tables(connection) -> set[str]:
    return {
        call[0].split()[2].strip('"') for call in connection.calls if call[0].startswith('DELETE FROM')
    }


def test_7_operational_telemetry_is_deleted_workspace_scoped():
    connection, result = _execute(['telemetry'], {'telemetry': 'hard_delete'})

    assert 'telemetry_events' in _deleted_tables(connection)
    for sql, params in connection.calls:
        if sql.startswith('DELETE FROM'):
            assert 'workspace_id = %s' in sql and params[0] == WORKSPACE, (
                'no deletion statement may run unscoped across tenants'
            )
            assert params[1] == NOW
    assert result['operations']['telemetry']['mode'] == 'hard_delete'


def test_8_alerts_findings_and_detections_are_deleted():
    connection, result = _execute(
        ['alerts', 'detections'], {'alerts': 'hard_delete', 'detections': 'hard_delete'},
    )
    deleted = _deleted_tables(connection)
    # `alerts` carries findings in this schema: finding_actions.finding_id and
    # finding_decisions.finding_id both reference alerts.id and cascade.
    assert {'alerts', 'evidence', 'asset_risk_findings', 'detections', 'threat_detections'} <= deleted
    assert result['operations']['alerts']['tables']['asset_risk_findings'] == 1
    # `evidence` expires WITH its alert, not with raw telemetry: an alert that
    # outlived its own evidence would read as "no evidence found".
    assert 'evidence' not in dict(data_retention.CASCADE_TABLES['telemetry'])
    assert 'evidence' in dict(data_retention.CASCADE_TABLES['alerts'])


def test_8b_alerts_is_a_real_retention_data_class_end_to_end():
    assert 'alerts' in pilot_retention.PILOT_DATA_CLASSES
    assert 'alerts' in data_retention.DATA_TARGETS
    assert 'alerts' in data_retention.ANONYMIZE_SQL
    migration = Path('services/api/migrations/0154_pilot_retention_lifecycle.sql').read_text(encoding='utf-8')
    assert migration.count(
        "CHECK (data_class IN ('telemetry','detections','alerts','incidents','audit_logs','exports','user_data'))"
    ) == 2, 'both data_class CHECK constraints must accept the new class'


def test_9_incidents_are_deleted_with_their_response_history():
    connection, _ = _execute(['incidents'], {'incidents': 'hard_delete'})
    assert {'incidents', 'response_actions', 'action_history'} <= _deleted_tables(connection)
    # action_history's timestamp column is named after a SQL keyword, so the
    # cascade DELETE quotes identifiers rather than relying on it parsing bare.
    history = [sql for sql, _ in connection.calls if 'action_history' in sql][0]
    assert history == 'DELETE FROM "action_history" WHERE workspace_id = %s AND "timestamp" < %s'


def test_9b_anonymize_mode_redacts_the_record_and_deletes_nothing():
    connection, result = _execute(['incidents'], {'incidents': 'anonymize'})
    assert _deleted_tables(connection) == set()
    assert result['operations']['incidents']['mode'] == 'anonymize'
    assert any("UPDATE incidents SET summary = '[retained incident]'" in sql for sql, _ in connection.calls)


def test_10_and_11_evidence_exports_remove_the_object_and_the_metadata(monkeypatch):
    deleted_objects: list[str] = []

    class Storage:
        def delete_bytes(self, *, object_key):
            deleted_objects.append(object_key)

    monkeypatch.setattr(data_retention, 'load_export_storage', lambda: Storage())

    def handler(sql, params):
        if 'FROM export_jobs' in sql:
            return Result(rows=[{'id': 'export-1', 'storage_backend': 's3',
                                 'storage_object_key': 'workspace-a/EV-2026-001.zip'}])
        return Result(rowcount=1)

    connection, result = _execute(['exports'], {'exports': 'hard_delete'}, handler=handler)

    assert deleted_objects == ['workspace-a/EV-2026-001.zip'], 'the stored evidence file is removed'
    update = connection.sql_containing('UPDATE export_jobs SET deleted_at = NOW()')[0]
    assert 'storage_object_key = NULL' in update[0] and 'output_path = NULL' in update[0], (
        'the metadata must not keep pointing at an object that no longer exists'
    )
    assert update[1] == ('export-1', WORKSPACE)
    assert result['operations']['exports']['records_affected'] == 1


def test_11c_the_receipt_states_what_an_object_store_delete_can_guarantee(monkeypatch):
    """S3 Object Lock in COMPLIANCE mode keeps a locked version after a successful
    delete_object. The receipt has to say so rather than claim the bytes are gone."""
    class LockedStorage:
        backend_name = 's3'

        def delete_bytes(self, *, object_key):
            return None

        def object_lock_status(self):
            return {'object_lock_enabled': True, 'retention_mode': 'COMPLIANCE', 'worm': True}

    monkeypatch.setattr(data_retention, 'load_export_storage', lambda: LockedStorage())

    def handler(sql, params):
        if 'FROM export_jobs' in sql:
            return Result(rows=[{'id': 'export-1', 'storage_backend': 's3', 'storage_object_key': 'k'}])
        return Result(rowcount=1)

    connection, _ = _execute(['exports'], {'exports': 'hard_delete'}, handler=handler)
    event = [call for call in connection.sql_containing('INSERT INTO data_deletion_events')
             if call[1][4] == 'storage_delete'][0]
    storage = json.loads(event[1][8])['storage']
    assert storage['removed_from_active_storage'] is True
    assert storage['versions_may_persist_under_object_lock'] is True
    assert storage['retention_mode'] == 'COMPLIANCE'


def test_11b_an_export_row_is_only_marked_deleted_after_its_object_is_gone(monkeypatch):
    """No orphaned object storage: the object delete precedes the DB update, and a
    failure rolls the whole request back so the retry re-selects the same row."""
    order: list[str] = []

    class Storage:
        def delete_bytes(self, *, object_key):
            order.append('storage')

    monkeypatch.setattr(data_retention, 'load_export_storage', lambda: Storage())

    def handler(sql, params):
        if 'FROM export_jobs' in sql:
            return Result(rows=[{'id': 'export-1', 'storage_backend': 's3', 'storage_object_key': 'k'}])
        if 'UPDATE export_jobs SET deleted_at' in sql:
            order.append('database')
        return Result(rowcount=1)

    _execute(['exports'], {'exports': 'hard_delete'}, handler=handler)
    assert order == ['storage', 'database']
    select = [call for call in _execute(['exports'], {'exports': 'hard_delete'}, handler=handler)[0].calls
              if 'FROM export_jobs' in call[0]][0][0]
    assert 'deleted_at IS NULL' in select, 'a retry must re-select rows whose object delete did not commit'


# ── 12. the security record has its OWN, longer period ───────────────────────
def test_12_audit_records_follow_a_separate_period_and_a_separate_request():
    connection, _ = _record_end()
    queued = connection.sql_containing('INSERT INTO data_deletion_requests')
    assert len(queued) == 2, 'operational data and the security record are not given one schedule'

    operational, security = queued[0][1], queued[1][1]
    assert json.loads(operational[6])['deletion_modes']['audit_logs'] == 'anonymize'
    assert operational[8] == NOW + timedelta(days=pilot_retention.PILOT_GRACE_PERIOD_DAYS)

    assert json.loads(security[3]) == ['audit_logs']
    assert json.loads(security[6])['deletion_modes'] == {'audit_logs': 'hard_delete'}
    assert security[8] == NOW + timedelta(days=pilot_retention.PILOT_SECURITY_RECORD_DAYS)
    assert pilot_retention.PILOT_SECURITY_RECORD_DAYS == 365
    assert pilot_retention.PILOT_SECURITY_RECORD_DAYS > pilot_retention.PILOT_GRACE_PERIOD_DAYS


def test_12b_audit_anonymization_destroys_the_actor_and_the_payload():
    statement = data_retention.ANONYMIZE_SQL['audit_logs']
    assert 'user_id = NULL' in statement
    assert 'ip_address = NULL' in statement
    assert "metadata = jsonb_build_object('_retention_anonymized', true)" in statement


# ── 13-14. legal holds ───────────────────────────────────────────────────────
def test_13_an_applicable_legal_hold_blocks_the_whole_request():
    def handler(sql, params):
        if 'FROM workspace_legal_holds' in sql:
            return Result(rows=[{'id': 'hold-1', 'data_classes': ['incidents']}])
        return None

    connection = RecordingConnection(handler)
    result = data_retention.execute_request(
        connection,
        purge_request(['telemetry', 'incidents'], {'telemetry': 'hard_delete', 'incidents': 'hard_delete'}),
        worker_name='retention-worker',
    )

    assert result == {'id': 'request-1', 'status': 'blocked_by_legal_hold', 'blocking_legal_hold_ids': ['hold-1']}
    assert _deleted_tables(connection) == set(), 'a hold on one class fences the whole sweep'
    assert connection.sql_containing("SET status = 'blocked_by_legal_hold'")


def test_13b_a_hold_placed_after_the_purge_was_queued_still_blocks_it():
    """The hold check runs at DELETION time, not at scheduling time — the only
    check that can be current for a request queued 30 days earlier."""
    source = Path('services/api/app/pilot_retention.py').read_text(encoding='utf-8')
    assert 'workspace_legal_holds' not in source, (
        'scheduling must not snapshot hold state; execute_request is where it is checked'
    )
    engine = Path('services/api/app/data_retention.py').read_text(encoding='utf-8')
    assert engine.index('holds = blocking_holds(') < engine.index("elif data_class == 'exports'")


def test_14_releasing_the_hold_lets_the_same_request_complete():
    connection, result = _execute(['telemetry'], {'telemetry': 'hard_delete'})
    assert result['status'] == 'completed'
    assert 'telemetry_events' in _deleted_tables(connection)


def test_14b_releasing_a_hold_resumes_a_blocked_end_of_pilot_purge(monkeypatch):
    """A hold placed during the grace window must DEFER the purge, not cancel it."""
    from services.api.app import pilot

    connection = RecordingConnection(
        lambda sql, params: Result(row={'id': 'hold-1'}, rowcount=1)
        if 'workspace_legal_holds' in sql else Result(rowcount=2)
    )

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', fake_connection)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        pilot, '_require_workspace_permission',
        lambda *a, **k: ({'id': 'user-a'}, {'workspace_id': WORKSPACE}),
    )
    audited: dict = {}
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: audited.update(k))

    result = pilot.release_workspace_legal_hold('hold-1', {'reason': 'matter closed'}, _Request())

    assert result == {'id': 'hold-1', 'status': 'released', 'resumed_pilot_purges': 2}
    resume = connection.sql_containing("status = 'approved', attempt_count = 0")[0]
    assert resume[1] == (WORKSPACE, pilot_retention.REQUEST_TYPE_PILOT_PURGE)
    assert "status = 'blocked_by_legal_hold'" in resume[0]
    assert audited['metadata']['resumed_pilot_purges'] == 2


def test_14c_a_customers_own_blocked_request_is_not_auto_resumed():
    """Only the scheduled policy resumes. A human decision is re-made by a human."""
    connection = RecordingConnection(lambda sql, params: Result(rowcount=0))
    pilot_retention.resume_blocked_pilot_purges(connection, workspace_id=WORKSPACE)
    resume = connection.calls[0]
    assert resume[1][1] == pilot_retention.REQUEST_TYPE_PILOT_PURGE
    assert 'workspace_data' not in resume[0] and 'user_data' not in resume[0]


# ── 15. continuing cancels the schedule ──────────────────────────────────────
@pytest.mark.parametrize(
    'organization',
    [
        organization_row(status=ent.STATUS_ACTIVE, pilot_ended_at=NOW),
        organization_row(plan=ent.PLAN_SCALE, pilot_ended_at=NOW),
        organization_row(plan=ent.PLAN_ENTERPRISE, pilot_ended_at=NOW),
        organization_row(status=ent.STATUS_SUSPENDED, pilot_ended_at=NOW),
        organization_row(evaluation_expires_at=NOW + timedelta(days=60), pilot_ended_at=NOW),
    ],
    ids=['reactivated', 'upgraded_to_scale', 'upgraded_to_enterprise', 'suspended', 'deadline_extended'],
)
def test_15_continuing_before_the_deadline_cancels_the_queued_deletion(organization):
    connection = RecordingConnection(schema_ready_handler(lambda s, p: Result(rowcount=1)))

    org_service.reconcile_pilot_retention(connection, organization)

    cancel = connection.sql_containing("SET status = 'cancelled'")[0]
    assert cancel[1] == (ORGANIZATION, pilot_retention.REQUEST_TYPE_PILOT_PURGE)
    assert "d.status = 'approved'" in cancel[0], (
        'a deletion already running or completed must never be rewritten as cancelled'
    )
    assert connection.sql_containing('SET pilot_ended_at = NULL')


def test_15b_every_founder_lifecycle_write_reconciles():
    source = Path('services/api/app/organizations.py').read_text(encoding='utf-8')
    for marker in ('def extend_evaluation(', 'def set_evaluation_expiry(', 'def set_status(', 'def set_plan('):
        assert marker in source
    assert source.count('reconcile_pilot_retention(connection, updated)') == 5


# ── 16-18. immediate customer deletion ───────────────────────────────────────
def _deletion_payload(**overrides):
    payload = {'data_classes': ['telemetry'], 'request_type': 'workspace_data',
               'reason': 'Customer requested immediate deletion.', 'confirm': 'DELETE'}
    payload.update(overrides)
    return payload


class _Request:
    headers: dict = {}


def test_16_an_authorized_confirmed_request_is_recorded(monkeypatch):
    from fastapi import HTTPException

    from services.api.app import pilot

    seen: dict = {}

    def permission(connection, request, permission_name, require_reauthentication=False, **_kwargs):
        seen['permission'] = permission_name
        seen['reauthentication'] = require_reauthentication
        return {'id': 'user-a'}, {'workspace_id': WORKSPACE}

    connection = RecordingConnection(lambda sql, params: Result(rows=[]))

    @contextmanager
    def fake_connection():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', fake_connection)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', permission)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: seen.setdefault('audited', k))

    result = pilot.create_data_deletion_request(_deletion_payload(), _Request())

    assert result['status'] == 'pending'
    assert seen['permission'] == 'security.manage'
    assert seen['reauthentication'] is True
    assert seen['audited']['metadata']['request_type'] == 'workspace_data'
    insert = connection.sql_containing('INSERT INTO data_deletion_requests')[0]
    assert insert[1][1] == WORKSPACE


def test_17_an_unauthorized_caller_cannot_request_deletion(monkeypatch):
    from fastapi import HTTPException, status as http_status

    from services.api.app import pilot

    def deny(*_args, **_kwargs):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN,
                            detail={'code': 'PERMISSION_DENIED', 'permission': 'security.manage'})

    @contextmanager
    def fake_connection():
        yield RecordingConnection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', fake_connection)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', deny)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_data_deletion_request(_deletion_payload(), _Request())
    assert exc_info.value.status_code == 403


def test_18_confirmation_and_recent_reauthentication_are_both_required(monkeypatch):
    from fastapi import HTTPException

    from services.api.app import pilot

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)

    # The confirmation is checked BEFORE any connection is opened, so an
    # unconfirmed request cannot even reach the database.
    for payload in (_deletion_payload(confirm=''), _deletion_payload(confirm='delete'),
                    _deletion_payload(confirm=True)):
        with pytest.raises(HTTPException) as exc_info:
            pilot.create_data_deletion_request(payload, _Request())
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail['code'] == 'DELETION_CONFIRMATION_REQUIRED'

    source = Path('services/api/app/pilot.py').read_text(encoding='utf-8')
    for function in ('create_data_deletion_request', 'approve_and_execute_data_deletion_request',
                     'create_workspace_legal_hold', 'release_workspace_legal_hold',
                     'update_workspace_retention_policies'):
        body = source.split(f'def {function}(', 1)[1].split('\ndef ', 1)[0]
        assert 'require_reauthentication=True' in body, f'{function} must keep its reauthentication gate'


# ── 19-20. retry safety and receipt contents ─────────────────────────────────
def test_19_scheduling_and_execution_are_both_idempotent():
    connection, _ = _record_end()
    keys = [call[1][7] for call in connection.sql_containing('INSERT INTO data_deletion_requests')]
    assert keys == [
        f'pilot-purge:{WORKSPACE}:{NOW.isoformat()}',
        f'pilot-security-record:{WORKSPACE}:{NOW.isoformat()}',
    ]
    for sql, _ in connection.sql_containing('INSERT INTO data_deletion_requests'):
        assert 'ON CONFLICT (idempotency_key)' in sql

    # Re-ending an already-ended Pilot changes nothing.
    def extra(sql, params):
        if sql.startswith('SELECT id, plan, status, pilot_ended_at'):
            return Result(row=organization_row(pilot_ended_at=NOW, pilot_grace_ends_at=NOW + timedelta(days=30)))
        return None

    repeat = RecordingConnection(schema_ready_handler(extra))
    again = pilot_retention.record_pilot_end(repeat, organization_id=ORGANIZATION, ended_at=NOW + timedelta(days=1))
    assert again == {'recorded': False, 'reason': 'already_ended',
                     'pilot_ended_at': NOW.isoformat(),
                     'pilot_grace_ends_at': (NOW + timedelta(days=30)).isoformat()}
    assert repeat.sql_containing('INSERT INTO data_deletion_requests') == []


def test_19b_deletion_events_are_idempotent_per_operation():
    connection, _ = _execute(['telemetry'], {'telemetry': 'hard_delete'})
    for sql, _ in connection.sql_containing('INSERT INTO data_deletion_events'):
        assert 'ON CONFLICT (idempotency_key)' in sql


def test_20_the_deletion_receipt_contains_no_deleted_customer_payload():
    secret = '0xDEADBEEFcustomerpayload'

    def handler(sql, params):
        if sql.startswith('DELETE FROM telemetry_events'):
            return Result(rowcount=3)
        if sql.startswith('DELETE FROM'):
            return Result(rowcount=0)
        return None

    connection, result = _execute(['telemetry'], {'telemetry': 'hard_delete'}, handler=handler)

    event = connection.sql_containing('INSERT INTO data_deletion_events')[0][1]
    details = json.loads(event[8])
    assert set(details) <= {
        'cutoff_at', 'worker_name', 'external_artifacts_deleted', 'subject_user_id', 'tables',
    }
    assert secret not in json.dumps(details)
    # The receipt is a hash plus counts — enough to prove the deletion, not to
    # reconstruct anything that was deleted.
    assert len(result['deletion_report_sha256']) == 64
    report = json.dumps(result['deletion_report'])
    assert secret not in report
    assert result['deletion_report']['operations']['telemetry']['records_affected'] == 3


# ── 21-22. migration safety and no regression for paid plans ─────────────────
def test_21_the_migration_seeds_without_deleting_and_never_invents_an_end_date():
    migration = Path('services/api/migrations/0154_pilot_retention_lifecycle.sql').read_text(encoding='utf-8')

    assert 'DELETE FROM' not in migration.upper().replace('HARD_DELETE', ''), (
        'a retention migration must not itself delete customer data'
    )
    assert 'DROP TABLE' not in migration.upper()
    assert "ON CONFLICT (workspace_id, data_class) DO NOTHING" in migration
    # Existing workspaces get a notice window before any age-based sweep reaches them.
    assert "NOW() + INTERVAL '30 days'" in migration
    # The end date is only ever copied from a recorded expiry.
    assert 'pilot_ended_at = evaluation_expires_at' in migration
    assert 'evaluation_expires_at IS NOT NULL' in migration
    assert 'pilot_ended_at IS NULL' in migration


def test_21b_the_sweep_cannot_act_on_a_policy_before_its_effective_date():
    ready = RecordingConnection(schema_ready_handler(lambda s, p: Result(rowcount=0)))
    data_retention.schedule_requests(ready)
    sweep = ready.sql_containing('INSERT INTO data_deletion_requests')[0][0]
    assert 'p.effective_from IS NULL OR p.effective_from <= NOW()' in sweep

    # And on a deployment that has not migrated yet, the gate is simply absent
    # rather than the statement failing.
    not_ready = RecordingConnection(lambda sql, params: Result(row={'present': 0}))
    data_retention.schedule_requests(not_ready)
    legacy = not_ready.sql_containing('INSERT INTO data_deletion_requests')[0][0]
    assert 'effective_from' not in legacy
    assert 'p.enabled = TRUE' in legacy


def test_21c_migration_defaults_match_the_module():
    """A migration cannot import Python, so the seed values are written twice.
    This is what stops the two copies from ever drifting."""
    migration = Path('services/api/migrations/0154_pilot_retention_lifecycle.sql').read_text(encoding='utf-8')
    seeded = {
        name: (int(days), mode)
        for name, days, mode in re.findall(r"\('(\w+)',\s*(\d+),\s*'(\w+)'\)", migration)
    }
    assert seeded == {
        data_class: (pilot_retention.PILOT_RETENTION_DAYS[data_class],
                     pilot_retention.PILOT_DELETION_MODES[data_class])
        for data_class in pilot_retention.PILOT_DATA_CLASSES
    }


def test_22_scale_and_enterprise_are_not_given_a_pilot_period():
    assert pilot_retention.default_policies(ent.PLAN_SCALE) == []
    assert pilot_retention.default_policies(ent.PLAN_ENTERPRISE) == []
    connection = RecordingConnection(schema_ready_handler())
    assert pilot_retention.ensure_workspace_retention_policies(
        connection, workspace_id=WORKSPACE, plan=ent.PLAN_SCALE,
    ) == 0
    assert connection.sql_containing('INSERT INTO workspace_retention_policies') == []

    migration = Path('services/api/migrations/0154_pilot_retention_lifecycle.sql').read_text(encoding='utf-8')
    assert "WHERE o.plan = 'pilot'" in migration

    backfill = RecordingConnection(schema_ready_handler(lambda s, p: Result(rows=[])))
    pilot_retention.backfill_missing_policies(backfill)
    scan = backfill.sql_containing('NOT EXISTS ( SELECT 1 FROM workspace_retention_policies')[0]
    assert scan[1][0] == ent.PLAN_PILOT

    lifecycle = pilot_retention.pilot_data_lifecycle(organization_row(plan=ent.PLAN_SCALE), now=NOW)
    assert lifecycle['state'] == pilot_retention.DATA_STATE_NOT_APPLICABLE
    assert lifecycle['scheduled_deletion_at'] is None


# ── truthfulness of what the product SAYS ────────────────────────────────────
def test_an_expired_pilot_with_no_recorded_end_gets_no_invented_schedule():
    lifecycle = pilot_retention.pilot_data_lifecycle(
        organization_row(status=ent.STATUS_EXPIRED), now=NOW,
    )
    assert lifecycle['state'] == pilot_retention.DATA_STATE_ENDED_UNDATED
    assert lifecycle['grace_ends_at'] is None
    assert lifecycle['scheduled_deletion_at'] is None
    assert lifecycle['export_available'] is True


def test_a_legal_hold_is_stated_wherever_a_deletion_date_is():
    lifecycle = pilot_retention.pilot_data_lifecycle(
        organization_row(status=ent.STATUS_EXPIRED, pilot_ended_at=NOW - timedelta(days=1),
                          pilot_grace_ends_at=NOW + timedelta(days=29)),
        now=NOW, legal_hold_active=True,
    )
    assert lifecycle['deletion_blocked_by_legal_hold'] is True


def test_every_data_class_has_a_documented_reason_for_its_period():
    assert set(pilot_retention.RETENTION_RATIONALE) == set(pilot_retention.PILOT_DATA_CLASSES)
    assert set(pilot_retention.PILOT_RETENTION_DAYS) == set(pilot_retention.PILOT_DATA_CLASSES)
    assert set(pilot_retention.PILOT_DELETION_MODES) == set(pilot_retention.PILOT_DATA_CLASSES)
    for data_class, text in pilot_retention.RETENTION_RATIONALE.items():
        assert len(text) > 80, f'{data_class} has no real justification recorded'


def test_the_privacy_page_states_the_periods_the_product_applies():
    privacy = Path('apps/web/app/privacy/page.tsx').read_text(encoding='utf-8')
    for data_class, days in pilot_retention.PILOT_RETENTION_DAYS.items():
        if data_class == 'user_data':
            continue
        assert f'{days} days' in privacy, f'{data_class}: privacy page does not state its {days}-day period'
    assert f'{pilot_retention.PILOT_GRACE_PERIOD_DAYS} days' in privacy
    assert str(pilot_retention.PILOT_SECURITY_RECORD_DAYS) in privacy
    # The vague phrasing this change replaced, and the claim it must never make.
    assert 'until contractual retention ends' not in privacy
    assert 'zero residual' not in privacy.lower()
    assert 'permanently erased' not in privacy.lower()
    assert 'backup' in privacy.lower()


def test_no_module_hard_codes_a_retention_period_outside_pilot_retention():
    engine = Path('services/api/app/data_retention.py').read_text(encoding='utf-8')
    assert 'days => 30' not in engine and 'INTERVAL \'30 days\'' not in engine
    worker = Path('services/api/app/pilot.py').read_text(encoding='utf-8')
    assert '_RETENTION_DEFAULT_DAYS = pilot_retention.PILOT_RETENTION_DAYS' in worker
    assert '_RETENTION_DATA_CLASSES = pilot_retention.PILOT_DATA_CLASSES' in worker


def test_the_worker_cycle_runs_the_lifecycle_steps(monkeypatch):
    from services.api.app import pilot

    calls: list[str] = []

    @contextmanager
    def fake_connection():
        yield RecordingConnection(lambda sql, params: Result(row={'count': 0}))

    monkeypatch.setattr(pilot, 'pg_connection', fake_connection)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(pilot_retention, 'stamp_expired_pilots',
                        lambda _c, **_k: calls.append('stamp') or {'stamped': 2})
    monkeypatch.setattr(pilot_retention, 'backfill_missing_policies',
                        lambda _c, **_k: calls.append('backfill') or 7)
    monkeypatch.setattr('services.api.app.data_retention.schedule_requests',
                        lambda _c: calls.append('schedule') or 0)
    monkeypatch.setattr('services.api.app.data_retention.claim_request', lambda _c, worker_name: None)

    summary = pilot.run_retention_worker_cycle(worker_name='w', batch_size=1)

    assert calls == ['stamp', 'backfill', 'schedule']
    assert summary['pilots_ended'] == 2
    assert summary['policies_backfilled'] == 7
