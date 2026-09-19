"""The canonical Pilot data-retention and deletion lifecycle.

Why this module exists
----------------------
The deletion engine (``data_retention.py`` + ``retention_worker.py``) has always
been real: it hard-deletes, it anonymises, it removes object-storage artifacts,
it honours legal holds, and it is leased, retried and idempotent. What it had no
way to know was *when* — ``schedule_requests`` only sweeps a workspace that has a
row in ``workspace_retention_policies``, and nothing in the product ever wrote
one. A Pilot workspace therefore kept everything, forever, while the Privacy page
said "until contractual retention ends".

This module is the one place the answer is written down:

    * how long each data class is kept while a Pilot is running
    * what happens the moment the Pilot ends
    * how long the read/export grace window lasts
    * when the data becomes eligible for deletion
    * what survives that deletion, and for exactly how long

Nothing else in the codebase may hard-code a retention period or a grace length.

What this module does NOT do
----------------------------
It does not delete anything. Every period here becomes a row in
``workspace_retention_policies`` or a scheduled row in
``data_deletion_requests``; the existing worker is what performs the deletion,
under the existing legal-hold check. That separation is deliberate: there is one
deletion code path in this system, and it is the audited one.

Truthfulness rules kept here
----------------------------
  * A period is never invented at a call site. Every number is in this file.
  * An OPEN-ENDED Pilot is never given a fake end date, and never a countdown.
    ``pilot_data_lifecycle`` returns ``ended_at = None`` and schedules nothing.
  * A Pilot whose end date is not recorded gets NO grace window and NO scheduled
    deletion. Guessing a historical end date would be inventing the one fact the
    whole schedule hangs off.
  * A seeded default is reported as ``source = 'pilot_default'``, never as
    configuration the customer chose.
  * Reactivating or upgrading before the deletion runs cancels it. Deletion
    eligibility is a consequence of a live lifecycle fact, not a one-way latch.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from services.api.app import entitlements as ent

logger = logging.getLogger(__name__)


# ── the grace window ─────────────────────────────────────────────────────────
#: Days between a Pilot ending and its operational data becoming eligible for
#: deletion. During this window the workspace stays readable and evidence stays
#: exportable — that is the whole point of it. It is the ONE grace constant.
PILOT_GRACE_PERIOD_DAYS = 30


# ── per-class retention while a Pilot is RUNNING ─────────────────────────────
#: Age-based caps, applied by the existing retention worker to records older than
#: the stated number of days. They are NOT all the same on purpose: raw chain
#: telemetry, a security case file, and an accountability log answer different
#: questions and are needed for different lengths of time.
#:
#: Read ``RETENTION_RATIONALE`` below for why each number is what it is. Changing
#: a number here changes what new workspaces are seeded with; it does not rewrite
#: a period a customer already configured (their row carries source='workspace').
PILOT_RETENTION_DAYS: dict[str, int] = {
    'telemetry': 90,
    'detections': 180,
    'alerts': 180,
    'incidents': 365,
    'exports': 365,
    'audit_logs': 365,
    'user_data': 30,
}

#: How each class is removed. ``anonymize`` keeps the row and destroys the
#: content; ``hard_delete`` removes the row. Only two classes are anonymised, and
#: each for a stated reason (see RETENTION_RATIONALE).
PILOT_DELETION_MODES: dict[str, str] = {
    'telemetry': 'hard_delete',
    'detections': 'hard_delete',
    'alerts': 'hard_delete',
    'incidents': 'hard_delete',
    'exports': 'hard_delete',
    'audit_logs': 'anonymize',
    'user_data': 'anonymize',
}

#: Why each period is what it is. Surfaced by ``GET /workspace/retention-policies``
#: so the customer-facing screen states the reason rather than a bare number, and
#: asserted by the tests so a period cannot be changed without its justification.
RETENTION_RATIONALE: dict[str, str] = {
    'telemetry': (
        'Raw monitored-chain observations and their payloads. The highest-volume '
        'and most sensitive class, and the one nothing needs long: 90 days covers '
        'a full evaluation quarter and any re-run of a detection against the data '
        'that produced it.'
    ),
    'detections': (
        'Detection records must outlive the telemetry beneath them, or the alert '
        'and incident above them stop being explainable. Twice the telemetry '
        'period, and no longer.'
    ),
    'alerts': (
        'Alerts and findings share the same chain position as detections — in this '
        'schema a finding IS an alert row — so they expire together. A finding '
        'that outlived its alert would be an orphaned security claim.'
    ),
    'incidents': (
        'The customer’s own security case file: timeline, investigation output, '
        'evidence snapshots and response history. Kept for a full annual audit '
        'cycle because that is the cycle customers are asked about it in.'
    ),
    'exports': (
        'Evidence packages the customer generated, including the object-storage '
        'artifact. Matched to incidents so an incident and the evidence exported '
        'from it expire together rather than one outliving the other.'
    ),
    'audit_logs': (
        'Security and accountability record — who did what, when. Anonymised '
        'rather than deleted: actor identity, IP address and metadata payload are '
        'destroyed. The hash-chain fields are never rewritten, so the chain still '
        'links and every later row still verifies; the anonymised row itself can '
        'no longer be re-derived from its own contents, because the contents it '
        'was sealed over are gone. An abuse or security question can still be '
        'answered at the shape level without keeping personal data. One year, '
        'then removed.'
    ),
    'user_data': (
        'Individual erasure. Anonymised rather than deleted because the person’s '
        'authorship of workspace records must not silently disappear from another '
        'customer’s audit trail; sessions and tokens are revoked immediately.'
    ),
}

#: Every data class the retention engine governs, in workflow order.
PILOT_DATA_CLASSES: tuple[str, ...] = (
    'telemetry', 'detections', 'alerts', 'incidents', 'exports', 'audit_logs', 'user_data',
)

#: The classes an end-of-Pilot purge removes OUTRIGHT once the grace window
#: closes — the customer's operational data, whatever its age.
PILOT_PURGE_OPERATIONAL_CLASSES: tuple[str, ...] = (
    'telemetry', 'detections', 'alerts', 'incidents', 'exports',
)

#: How long the minimised security record survives the operational purge, counted
#: from the recorded end of the Pilot. At the grace deadline the audit log is
#: ANONYMISED with the operational data (actor, IP and metadata destroyed); this
#: is when the remaining skeleton — action name, entity, timestamp, hash chain —
#: is hard-deleted as well. It exists so that a security or abuse question about
#: the evaluation can still be answered after the customer content is gone, and
#: it is bounded rather than indefinite.
PILOT_SECURITY_RECORD_DAYS = 365

#: Days added to a seeded policy before the age-based sweep may act on a
#: PRE-EXISTING workspace. A workspace created after this change has no records
#: old enough to delete, so its policy is effective immediately; a workspace that
#: predates it gets this notice window so that deploying a retention policy is
#: never itself a bulk deletion. Mirrors the interval in migration 0154.
EXISTING_WORKSPACE_GRACE_DAYS = 30

#: Plans that receive the Pilot default policy. Scale and Enterprise retention is
#: a contract term; seeding a Pilot-length period for a paying tenant would be
#: the same silent retention decision this module exists to remove.
SEEDED_PLANS: frozenset[str] = frozenset({ent.PLAN_PILOT})

#: Request type for a scheduled end-of-Pilot purge. Distinguishable in the
#: console and the audit trail from an age-based sweep and from a customer's own
#: deletion request, because the three have different causes.
REQUEST_TYPE_PILOT_PURGE = 'pilot_end_purge'

#: Reason recorded when a dated evaluation reached its own deadline rather than
#: a founder ending it by hand.
END_REASON_EVALUATION_EXPIRED = 'evaluation_expired'

#: Columns added by migration 0154. Probed before any lifecycle write, so an API
#: that rolled out ahead of the migration reports the condition instead of
#: half-applying a lifecycle.
_ORGANIZATION_LIFECYCLE_COLUMNS = ('pilot_ended_at', 'pilot_grace_ends_at', 'pilot_end_reason')
_POLICY_COLUMNS = ('effective_from', 'source')


def _dumps(value: Any) -> str:
    """Compact JSON for a jsonb parameter. Local, so this module never has to
    import ``pilot`` just to serialise a list of data-class names."""
    return json.dumps(value, separators=(',', ':'), sort_keys=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    """Best-effort UTC datetime from a row value. Never raises."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    moment = _as_datetime(value)
    return moment.isoformat() if moment else None


# ── policy shape ─────────────────────────────────────────────────────────────
def default_policies(plan: Any = ent.PLAN_PILOT) -> list[dict[str, Any]]:
    """The canonical default policy rows for a plan, or ``[]`` when none applies.

    Returning ``[]`` for Scale and Enterprise is the honest answer, not an
    omission: this module has no opinion about a contracted tenant's retention,
    and a caller must not invent one from the Pilot table.
    """
    if ent.normalize_plan(plan) not in SEEDED_PLANS:
        return []
    return [
        {
            'data_class': data_class,
            'retention_days': PILOT_RETENTION_DAYS[data_class],
            'deletion_mode': PILOT_DELETION_MODES[data_class],
            'enabled': True,
            'source': 'pilot_default',
            'rationale': RETENTION_RATIONALE[data_class],
        }
        for data_class in PILOT_DATA_CLASSES
    ]


def grace_deadline(ended_at: datetime | None) -> datetime | None:
    """End of the read/export grace window, or ``None`` when the Pilot has not ended."""
    moment = _as_datetime(ended_at)
    return moment + timedelta(days=PILOT_GRACE_PERIOD_DAYS) if moment else None


def security_record_deadline(ended_at: datetime | None) -> datetime | None:
    """When the minimised audit skeleton is removed, or ``None`` before the end."""
    moment = _as_datetime(ended_at)
    return moment + timedelta(days=PILOT_SECURITY_RECORD_DAYS) if moment else None


# ── schema readiness ─────────────────────────────────────────────────────────
def _columns_present(connection: Any, table: str, columns: tuple[str, ...]) -> bool:
    try:
        row = connection.execute(
            '''SELECT COUNT(*) AS present FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name::text = %s::text
                 AND column_name::text = ANY(%s::text[])''',
            (table, list(columns)),
        ).fetchone()
    except Exception:  # pragma: no cover - a failed probe is reported as not ready
        logger.warning('pilot_retention_schema_probe_failed table=%s', table, exc_info=True)
        return False
    return int((row or {}).get('present') or 0) == len(columns)


def policy_schedule_columns_ready(connection: Any) -> bool:
    """Whether ``workspace_retention_policies`` carries 0154's scheduling columns.

    Probed on its own (rather than through ``schema_ready``) because the sweep
    scheduler only needs this half, and it must keep working on a deployment
    whose API is ahead of its migrations.
    """
    return _columns_present(connection, 'workspace_retention_policies', _POLICY_COLUMNS)


def schema_ready(connection: Any) -> bool:
    """Whether migration 0154 has been applied on this deployment.

    Every lifecycle write checks this first. A deployment whose API rolled out
    ahead of the migration reports the condition and changes nothing, rather than
    recording half a lifecycle that the worker would then act on.
    """
    return (
        _columns_present(connection, 'organizations', _ORGANIZATION_LIFECYCLE_COLUMNS)
        and _columns_present(connection, 'workspace_retention_policies', _POLICY_COLUMNS)
    )


# ── seeding (Phase 4) ────────────────────────────────────────────────────────
def ensure_workspace_retention_policies(
    connection: Any,
    *,
    workspace_id: str,
    plan: Any = ent.PLAN_PILOT,
    effective_from: datetime | None = None,
) -> int:
    """Seed the default policy for one workspace. Returns rows inserted.

    ``ON CONFLICT DO NOTHING`` is the important part: a workspace whose owner
    already configured retention keeps exactly what they configured, so this is
    safe to call on every provisioning path and safe to call twice.

    ``effective_from`` defaults to ``None`` — effective immediately, which is
    correct for a workspace being created right now because it holds no record
    old enough for any period here to reach. Callers backfilling an EXISTING
    workspace pass a future date so that turning retention on is not itself a
    deletion.
    """
    policies = default_policies(plan)
    if not policies:
        return 0
    if not schema_ready(connection):
        logger.warning(
            'pilot_retention_seed_skipped reason=schema_not_migrated workspace_id=%s', workspace_id,
        )
        return 0
    inserted = 0
    for policy in policies:
        cursor = connection.execute(
            '''
            INSERT INTO workspace_retention_policies
                (workspace_id, data_class, retention_days, deletion_mode, enabled, source,
                 effective_from, updated_by_user_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, TRUE, %s, %s, NULL, NOW(), NOW())
            ON CONFLICT (workspace_id, data_class) DO NOTHING
            ''',
            (str(workspace_id), policy['data_class'], policy['retention_days'],
             policy['deletion_mode'], policy['source'], effective_from),
        )
        inserted += max(int(getattr(cursor, 'rowcount', 0) or 0), 0)
    if inserted:
        logger.info(
            'pilot_retention_policy_seeded workspace_id=%s rows=%s effective_from=%s',
            workspace_id, inserted, _iso(effective_from),
        )
    return inserted


def backfill_missing_policies(connection: Any, *, limit: int = 500) -> int:
    """Seed Pilot workspaces that still have no policy. Returns rows inserted.

    The worker runs this every cycle so a workspace created by an API process
    that predates this change, or created while the migration was still rolling
    out, does not stay outside retention forever. Newly seeded rows carry the
    same notice window the migration uses, so a workspace healed here is never
    swept on the cycle that healed it.
    """
    if not schema_ready(connection):
        return 0
    effective_from = _utc_now() + timedelta(days=EXISTING_WORKSPACE_GRACE_DAYS)
    rows = connection.execute(
        '''
        SELECT w.id AS workspace_id
        FROM workspaces w
        JOIN organizations o ON o.id = w.organization_id
        WHERE o.plan = %s
          AND NOT EXISTS (
              SELECT 1 FROM workspace_retention_policies p WHERE p.workspace_id = w.id
          )
        ORDER BY w.created_at
        LIMIT %s
        ''',
        (ent.PLAN_PILOT, max(1, int(limit))),
    ).fetchall()
    inserted = 0
    for row in rows or []:
        inserted += ensure_workspace_retention_policies(
            connection, workspace_id=str(row['workspace_id']),
            plan=ent.PLAN_PILOT, effective_from=effective_from,
        )
    return inserted


# ── end-of-Pilot lifecycle (Phase 5) ─────────────────────────────────────────
def _organization_workspaces(connection: Any, organization_id: str) -> list[str]:
    rows = connection.execute(
        'SELECT id FROM workspaces WHERE organization_id = %s ORDER BY created_at, id',
        (str(organization_id),),
    ).fetchall()
    return [str(row['id']) for row in (rows or [])]


def _schedule_purge_request(
    connection: Any,
    *,
    workspace_id: str,
    data_classes: list[str],
    modes: dict[str, str],
    due_at: datetime,
    ended_at: datetime,
    reason: str,
    idempotency_suffix: str,
) -> bool:
    """Queue ONE end-of-Pilot deletion, due at ``due_at``. Returns True if queued.

    The request is inserted ``approved`` with ``next_attempt_at = due_at``, which
    is exactly how the existing worker's ``claim_request`` already schedules work:
    it claims nothing whose ``next_attempt_at`` is in the future. Nothing new
    polls, and nothing new deletes.

    ``cutoff_at`` is the same moment, so the operation removes what existed when
    the deadline passed and states that in its report, rather than a moving
    "everything up to whenever the worker happened to run".

    The idempotency key carries ``ended_at``, so re-ending a Pilot after a
    reactivation queues a fresh schedule instead of colliding with the cancelled
    one.

    Every parameter is cast explicitly. This is an ``INSERT ... SELECT`` rather
    than an ``INSERT ... VALUES``, and PostgreSQL does not resolve an unknown
    parameter in a SELECT output list from the insert target's column type the
    way it does for VALUES — an uncast placeholder lands as ``text`` against a
    ``uuid``/``timestamptz`` column and the statement fails at runtime, where no
    fake-connection test would catch it.
    """
    key = f'pilot-{idempotency_suffix}:{workspace_id}:{ended_at.isoformat()}'
    cursor = connection.execute(
        '''
        INSERT INTO data_deletion_requests
            (id, workspace_id, request_type, data_classes, cutoff_at, status, reason,
             requested_by_user_id, result, idempotency_key, next_attempt_at, requested_at, updated_at)
        SELECT %s::uuid, %s::uuid, %s::text, %s::jsonb, %s::timestamptz, 'approved', %s::text,
               actor.user_id, %s::jsonb, %s::text, %s::timestamptz, NOW(), NOW()
        FROM (SELECT wm.user_id FROM workspace_members wm
              WHERE wm.workspace_id = %s::uuid
              ORDER BY CASE WHEN wm.role IN ('owner', 'workspace_owner') THEN 0 ELSE 1 END,
                       wm.created_at
              LIMIT 1) actor
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        ''',
        (str(uuid.uuid4()), str(workspace_id), REQUEST_TYPE_PILOT_PURGE,
         _dumps(data_classes), due_at, reason,
         _dumps({'deletion_modes': modes, 'pilot_ended_at': ended_at.isoformat(),
                      'grace_period_days': PILOT_GRACE_PERIOD_DAYS}),
         key, due_at, str(workspace_id)),
    )
    return max(int(getattr(cursor, 'rowcount', 0) or 0), 0) > 0


def schedule_pilot_end_deletions(
    connection: Any, *, organization_id: str, ended_at: datetime, grace_ends_at: datetime,
) -> dict[str, Any]:
    """Queue the two end-of-Pilot deletions for every workspace in the tenant.

    Two, not one, because operational data and the security record do not have
    the same period and must not be given one:

      1. at ``grace_ends_at`` — telemetry, detections, alerts, incidents and
         evidence exports are hard-deleted, and the audit log is ANONYMISED in
         the same operation, so the customer's content leaves together.
      2. at ``ended_at + PILOT_SECURITY_RECORD_DAYS`` — the remaining anonymised
         audit skeleton is hard-deleted, so the security record is bounded rather
         than indefinite.

    Legal holds are not consulted here. They are checked by the worker at the
    moment of deletion, which is the only check that can be current — a hold
    placed during the grace window must still block a deletion queued before it.
    """
    operational_modes = {name: PILOT_DELETION_MODES[name] for name in PILOT_PURGE_OPERATIONAL_CLASSES}
    operational_modes['audit_logs'] = 'anonymize'
    queued = {'operational': 0, 'security_record': 0, 'workspaces': 0}
    security_due = security_record_deadline(ended_at)
    for workspace_id in _organization_workspaces(connection, organization_id):
        queued['workspaces'] += 1
        if _schedule_purge_request(
            connection, workspace_id=workspace_id,
            data_classes=[*PILOT_PURGE_OPERATIONAL_CLASSES, 'audit_logs'],
            modes=operational_modes, due_at=grace_ends_at, ended_at=ended_at,
            reason=(
                'Pilot ended; operational data deleted after the '
                f'{PILOT_GRACE_PERIOD_DAYS}-day read/export grace period.'
            ),
            idempotency_suffix='purge',
        ):
            queued['operational'] += 1
        if security_due and _schedule_purge_request(
            connection, workspace_id=workspace_id, data_classes=['audit_logs'],
            modes={'audit_logs': 'hard_delete'}, due_at=security_due, ended_at=ended_at,
            reason=(
                'Pilot ended; anonymised security record removed after '
                f'{PILOT_SECURITY_RECORD_DAYS} days.'
            ),
            idempotency_suffix='security-record',
        ):
            queued['security_record'] += 1
    if queued['operational'] < queued['workspaces']:
        # A workspace with no members has no actor to attribute the request to,
        # and data_deletion_requests.requested_by_user_id is NOT NULL. Say so — a
        # workspace silently left off the schedule is exactly the failure mode
        # this module exists to prevent.
        logger.warning(
            'pilot_end_purge_not_queued_for_every_workspace organization_id=%s workspaces=%s queued=%s',
            organization_id, queued['workspaces'], queued['operational'],
        )
    return queued


def resume_blocked_pilot_purges(connection: Any, *, workspace_id: str) -> int:
    """Re-queue end-of-Pilot purges that a now-released legal hold was blocking.

    ``execute_request`` parks a held request in ``blocked_by_legal_hold``, and
    ``claim_request`` only ever claims ``approved`` or ``running``. Without this,
    a hold placed during a Pilot's grace window would CANCEL its deletion
    permanently rather than defer it, and the product would keep data it had told
    the customer it would delete.

    A customer's OWN deletion request is deliberately not resumed here: that one
    is a human decision and a human re-makes it. A scheduled policy is not.

    The engine re-checks holds at execution, so this cannot outrun a hold that is
    still in force — such a request simply parks again. ``attempt_count`` is
    reset because being blocked is a clean answer, not a failed attempt, and must
    not consume the retry budget.
    """
    cursor = connection.execute(
        """UPDATE data_deletion_requests
           SET status = 'approved', attempt_count = 0, error_message = NULL,
               lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
           WHERE workspace_id = %s AND request_type = %s AND status = 'blocked_by_legal_hold'""",
        (str(workspace_id), REQUEST_TYPE_PILOT_PURGE),
    )
    resumed = max(int(getattr(cursor, 'rowcount', 0) or 0), 0)
    if resumed:
        logger.info('pilot_end_purges_resumed workspace_id=%s resumed=%s', workspace_id, resumed)
    return resumed


def cancel_pilot_end_deletions(connection: Any, *, organization_id: str) -> int:
    """Cancel every end-of-Pilot deletion that has not started. Returns the count.

    Only ``approved`` rows are cancelled. A request the worker has already leased
    (``running``) or finished is left exactly as it is: this function must not be
    able to rewrite the record of a deletion that happened.
    """
    cursor = connection.execute(
        '''
        UPDATE data_deletion_requests d
        SET status = 'cancelled',
            error_message = NULL,
            lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = NOW()
        FROM workspaces w
        WHERE w.id = d.workspace_id
          AND w.organization_id = %s
          AND d.request_type = %s
          AND d.status = 'approved'
        ''',
        (str(organization_id), REQUEST_TYPE_PILOT_PURGE),
    )
    cancelled = max(int(getattr(cursor, 'rowcount', 0) or 0), 0)
    if cancelled:
        logger.info(
            'pilot_end_deletions_cancelled organization_id=%s cancelled=%s', organization_id, cancelled,
        )
    return cancelled


def record_pilot_end(
    connection: Any,
    *,
    organization_id: str,
    ended_at: datetime | None = None,
    reason: str | None = None,
    ended_by_user_id: str | None = None,
) -> dict[str, Any]:
    """Record that a Pilot ended and queue its deletions. Idempotent.

    Returns the lifecycle facts plus what was queued. An organization whose end
    is already recorded is returned unchanged — ending a Pilot twice must not
    restart the customer's grace window or move a deletion date they were told.
    """
    if not schema_ready(connection):
        logger.warning(
            'pilot_end_not_recorded reason=schema_not_migrated organization_id=%s', organization_id,
        )
        return {'recorded': False, 'reason': 'schema_not_migrated'}
    organization = connection.execute(
        'SELECT id, plan, status, pilot_ended_at, pilot_grace_ends_at FROM organizations WHERE id = %s',
        (str(organization_id),),
    ).fetchone()
    if not organization:
        return {'recorded': False, 'reason': 'organization_not_found'}
    if ent.normalize_plan(organization.get('plan')) not in SEEDED_PLANS:
        return {'recorded': False, 'reason': 'not_a_pilot'}
    existing = _as_datetime(organization.get('pilot_ended_at'))
    if existing is not None:
        return {
            'recorded': False, 'reason': 'already_ended',
            'pilot_ended_at': existing.isoformat(),
            'pilot_grace_ends_at': _iso(organization.get('pilot_grace_ends_at')),
        }
    moment = _as_datetime(ended_at) or _utc_now()
    grace_ends_at = grace_deadline(moment)
    connection.execute(
        '''
        UPDATE organizations
        SET pilot_ended_at = %s, pilot_grace_ends_at = %s, pilot_end_reason = %s,
            pilot_ended_by_user_id = %s, updated_at = NOW()
        WHERE id = %s AND pilot_ended_at IS NULL
        ''',
        (moment, grace_ends_at, (reason or END_REASON_EVALUATION_EXPIRED)[:500],
         str(ended_by_user_id) if ended_by_user_id else None, str(organization_id)),
    )
    queued = schedule_pilot_end_deletions(
        connection, organization_id=str(organization_id), ended_at=moment, grace_ends_at=grace_ends_at,
    )
    logger.info(
        'pilot_end_recorded organization_id=%s ended_at=%s grace_ends_at=%s queued=%s',
        organization_id, moment.isoformat(), grace_ends_at.isoformat(), queued,
    )
    return {
        'recorded': True,
        'pilot_ended_at': moment.isoformat(),
        'pilot_grace_ends_at': grace_ends_at.isoformat(),
        'security_record_deleted_at': _iso(security_record_deadline(moment)),
        'queued': queued,
    }


def clear_pilot_end(connection: Any, *, organization_id: str) -> dict[str, Any]:
    """Undo a recorded end because the tenant continued. Cancels pending deletions.

    Called when a Pilot is reactivated, extended, given a future deadline, or
    upgraded to a paid plan. Deletion eligibility follows the live lifecycle: a
    customer who came back must not be deleted on a schedule set when they
    appeared to be leaving.
    """
    if not schema_ready(connection):
        return {'cleared': False, 'reason': 'schema_not_migrated'}
    cancelled = cancel_pilot_end_deletions(connection, organization_id=str(organization_id))
    cursor = connection.execute(
        '''
        UPDATE organizations
        SET pilot_ended_at = NULL, pilot_grace_ends_at = NULL, pilot_end_reason = NULL,
            pilot_ended_by_user_id = NULL, updated_at = NOW()
        WHERE id = %s AND pilot_ended_at IS NOT NULL
        ''',
        (str(organization_id),),
    )
    cleared = max(int(getattr(cursor, 'rowcount', 0) or 0), 0) > 0
    if cleared or cancelled:
        logger.info(
            'pilot_end_cleared organization_id=%s cleared=%s cancelled_requests=%s',
            organization_id, cleared, cancelled,
        )
    return {'cleared': cleared, 'cancelled_requests': cancelled}


def stamp_expired_pilots(connection: Any, *, limit: int = 100) -> dict[str, Any]:
    """Record the end of dated Pilots whose deadline has passed. Worker step.

    A dated Pilot expires by the clock, so no request handler is running at the
    moment it happens and nothing would otherwise write the fact down. The end
    timestamp recorded is ``evaluation_expires_at`` — the deadline the customer
    was actually given — not the moment the worker noticed, so the grace window
    runs from the real end.

    An OPEN-ENDED Pilot is never touched here: it has no deadline, so it has not
    ended, and inventing one would be the exact failure this whole module exists
    to prevent.
    """
    if not schema_ready(connection):
        return {'stamped': 0, 'reason': 'schema_not_migrated'}
    rows = connection.execute(
        '''
        SELECT id, evaluation_expires_at
        FROM organizations
        WHERE plan = %s
          AND pilot_ended_at IS NULL
          AND evaluation_expires_at IS NOT NULL
          AND evaluation_expires_at < NOW()
        ORDER BY evaluation_expires_at
        LIMIT %s
        ''',
        (ent.PLAN_PILOT, max(1, int(limit))),
    ).fetchall()
    stamped = 0
    for row in rows or []:
        result = record_pilot_end(
            connection, organization_id=str(row['id']),
            ended_at=_as_datetime(row.get('evaluation_expires_at')),
            reason=END_REASON_EVALUATION_EXPIRED,
        )
        if result.get('recorded'):
            stamped += 1
    return {'stamped': stamped}


# ── presentation (Phases 10 and 11) ──────────────────────────────────────────
#: The lifecycle columns, named once. Read on demand rather than folded into
#: ``_ORGANIZATION_COLUMNS`` so the per-request organization read that every
#: authenticated call performs does not grow a column list that a deployment
#: mid-migration would fail on.
LIFECYCLE_COLUMNS: tuple[str, ...] = (
    'pilot_ended_at', 'pilot_grace_ends_at', 'pilot_end_reason', 'pilot_ended_by_user_id',
)


def lifecycle_columns_ready(connection: Any) -> bool:
    """Whether ``organizations`` carries migration 0154's end-of-Pilot columns."""
    return _columns_present(connection, 'organizations', _ORGANIZATION_LIFECYCLE_COLUMNS)


def read_lifecycle_columns(connection: Any, organization_id: str) -> dict[str, Any]:
    """The recorded end-of-Pilot facts for one organization, or ``{}``.

    ``{}`` on a deployment that has not run migration 0154 — which
    ``pilot_data_lifecycle`` then reports as an ACTIVE Pilot with no dates,
    because that is exactly what is known there. It does not guess.
    """
    if not lifecycle_columns_ready(connection):
        return {}
    row = connection.execute(
        f'SELECT {", ".join(LIFECYCLE_COLUMNS)} FROM organizations WHERE id = %s',
        (str(organization_id),),
    ).fetchone()
    return dict(row) if row else {}


def lifecycle_for_organization(
    connection: Any,
    organization: Mapping[str, Any] | None,
    *,
    legal_hold_active: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``pilot_data_lifecycle`` for an organization row that lacks the columns.

    The organization mappings the rest of the codebase passes around carry the
    plan and the evaluation window but not the lifecycle columns; this fetches
    those and hands the merged row to the pure function, so there is still only
    one place that turns recorded facts into a rendered lifecycle.
    """
    org = dict(organization or {})
    if not org.get('id'):
        return pilot_data_lifecycle(org, now=now, legal_hold_active=legal_hold_active)
    org.update(read_lifecycle_columns(connection, str(org['id'])))
    return pilot_data_lifecycle(org, now=now, legal_hold_active=legal_hold_active)

#: Lifecycle states the UI renders. Distinct from ``entitlements.lifecycle_state``
#: on purpose: that one answers "what may this tenant do", this one answers "what
#: is happening to this tenant's data".
DATA_STATE_ACTIVE = 'ACTIVE_PILOT'
DATA_STATE_GRACE = 'GRACE_PERIOD'
DATA_STATE_DELETION_DUE = 'DELETION_DUE'
DATA_STATE_ENDED_UNDATED = 'ENDED_END_DATE_NOT_RECORDED'
DATA_STATE_NOT_APPLICABLE = 'NOT_APPLICABLE'

DATA_STATE_LABELS: dict[str, str] = {
    DATA_STATE_ACTIVE: 'Pilot active',
    DATA_STATE_GRACE: 'Pilot ended — read and export available',
    DATA_STATE_DELETION_DUE: 'Pilot ended — deletion due',
    DATA_STATE_ENDED_UNDATED: 'Pilot ended — end date not recorded',
    DATA_STATE_NOT_APPLICABLE: 'No Pilot retention schedule',
}


def pilot_data_lifecycle(
    organization: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    legal_hold_active: bool = False,
) -> dict[str, Any]:
    """What is scheduled to happen to this tenant's data, as plain facts.

    Pure: it reads an ``organizations`` row and returns data. Every date it
    returns is one that was recorded, never one it computed from a plan default,
    so a screen cannot render a deletion date that no scheduled request matches.

    An ACTIVE open-ended Pilot returns ``ends_at = None`` and every deadline
    ``None`` — there is no countdown to show, and the UI must show none.
    """
    org = organization or {}
    moment = now or _utc_now()
    plan = ent.normalize_plan(org.get('plan'))
    if plan not in SEEDED_PLANS:
        return {
            'state': DATA_STATE_NOT_APPLICABLE,
            'label': DATA_STATE_LABELS[DATA_STATE_NOT_APPLICABLE],
            'plan': plan,
            'ended_at': None,
            'grace_ends_at': None,
            'scheduled_deletion_at': None,
            'security_record_deleted_at': None,
            'grace_period_days': PILOT_GRACE_PERIOD_DAYS,
            'legal_hold_active': bool(legal_hold_active),
            'deletion_blocked_by_legal_hold': False,
            'export_available': True,
        }

    ended_at = _as_datetime(org.get('pilot_ended_at'))
    grace_ends_at = _as_datetime(org.get('pilot_grace_ends_at'))
    expired = ent.evaluation_expired(org, now=moment)

    if ended_at is None:
        # Expired by lifecycle but with no recorded end date: the one honest
        # answer is to say so. No grace window is implied and no deletion is
        # scheduled, because there is no date to hang either off.
        state = DATA_STATE_ENDED_UNDATED if expired else DATA_STATE_ACTIVE
        return {
            'state': state,
            'label': DATA_STATE_LABELS[state],
            'plan': plan,
            'ended_at': None,
            'grace_ends_at': None,
            'scheduled_deletion_at': None,
            'security_record_deleted_at': None,
            'grace_period_days': PILOT_GRACE_PERIOD_DAYS,
            'legal_hold_active': bool(legal_hold_active),
            'deletion_blocked_by_legal_hold': False,
            'export_available': True,
        }

    state = DATA_STATE_GRACE if grace_ends_at and moment < grace_ends_at else DATA_STATE_DELETION_DUE
    return {
        'state': state,
        'label': DATA_STATE_LABELS[state],
        'plan': plan,
        'ended_at': ended_at.isoformat(),
        'grace_ends_at': _iso(grace_ends_at),
        'scheduled_deletion_at': _iso(grace_ends_at),
        'security_record_deleted_at': _iso(security_record_deadline(ended_at)),
        'end_reason': org.get('pilot_end_reason'),
        'grace_period_days': PILOT_GRACE_PERIOD_DAYS,
        'legal_hold_active': bool(legal_hold_active),
        'deletion_blocked_by_legal_hold': bool(legal_hold_active),
        # Read and export stay available for the whole grace window; that is what
        # the window is for. They are not withdrawn at the deadline either — the
        # deletion is what removes them, and claiming otherwise before it has run
        # would be describing a state the data is not in.
        'export_available': True,
    }
