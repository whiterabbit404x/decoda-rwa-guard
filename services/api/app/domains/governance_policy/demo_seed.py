"""Demo-only seeding for the Screen 11 governance policy scenario.

This module exists so a non-production demo workspace can DEMONSTRATE the
deterministic policy architecture end to end — open an active policy, read its
constraints, run the simulator, and see a DENY with a real reason code.

Every row it writes is stamped ``origin = 'demo_seed'``, which means the UI
labels the policy as seeded rather than presenting it as customer-authored
configuration. The policy itself is not fake: the deterministic engine evaluates
it exactly as it evaluates a customer policy, and its decisions are real
decisions about the inputs supplied. What the marker prevents is claiming that a
customer wrote it.

It is a hard no-op when the runtime is production, and it never overwrites an
existing policy: it seeds only its own dedicated policy key, and only when that
key is absent from the workspace.

Mirrors domains/asset_integrity/demo_seed.py, which seeds the Screen 3 scenario
under the same gate.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from services.api.app import pilot
from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import service

logger = logging.getLogger(__name__)

#: The policy this module owns. Keyed so it is never confused with a
#: customer-authored policy.
DEMO_POLICY_KEY = 'POL-MINT-DEMO'
DEMO_POLICY_NAME = 'RWA Mint Policy (demo)'


def seed_demo_policy(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    allowed: bool,
    now: Any = None,
) -> dict[str, Any]:
    """Seed the demo mint policy. No-op outside demo runtimes."""
    if not allowed:
        return {'seeded': False, 'reason': 'production_runtime'}
    if not service.storage_ready(connection):
        return {'seeded': False, 'reason': 'schema_provisioning'}

    existing = connection.execute(
        f'SELECT id FROM {service.POLICIES_TABLE} WHERE workspace_id = %s AND policy_key = %s',
        (workspace_id, DEMO_POLICY_KEY),
    ).fetchone()
    if existing is not None:
        return {'seeded': False, 'reason': 'already_present', 'policy_key': DEMO_POLICY_KEY}

    now = now or pilot.utc_now()
    policy_id = str(uuid.uuid4())
    connection.execute(
        f'''INSERT INTO {service.POLICIES_TABLE} (
                id, workspace_id, policy_key, name, operation, status, version,
                required_business_event, settlement_requirement,
                allowed_window_start_utc, allowed_window_end_utc,
                maximum_daily_amount_usd, required_roles, violation_action, origin,
                created_by_user_id, updated_by_user_id, created_at, updated_at
            ) VALUES (
                %s::uuid, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                %s::uuid, %s::uuid, %s, %s
            )''',
        (
            policy_id, workspace_id, DEMO_POLICY_KEY, DEMO_POLICY_NAME,
            gpc.OPERATION_MINT, gpc.STATUS_ACTIVE, 1,
            gpc.BUSINESS_EVENT_SUBSCRIPTION, gpc.REQUIREMENT_CLEARED,
            '08:00', '18:00', '10000000.00',
            json.dumps([gpc.ROLE_TREASURY_OPERATOR, gpc.ROLE_COMPLIANCE_APPROVER]),
            gpc.VIOLATION_ACTION_DENY, gpc.ORIGIN_DEMO_SEED,
            user_id, user_id, now, now,
        ),
    )
    # Version 1 is the policy AS CREATED. Recording it means the history view has
    # a real first row instead of an unexplained gap before the first edit.
    connection.execute(
        f'''INSERT INTO {service.VERSIONS_TABLE} (
                id, workspace_id, policy_id, version, status, snapshot,
                previous_values, new_values, change_summary, changed_by_user_id, changed_at
            ) VALUES (%s::uuid, %s, %s::uuid, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::uuid, %s)''',
        (
            str(uuid.uuid4()), workspace_id, policy_id, 1, gpc.STATUS_ACTIVE,
            json.dumps({
                'policy_key': DEMO_POLICY_KEY,
                'name': DEMO_POLICY_NAME,
                'operation': gpc.OPERATION_MINT,
                'status': gpc.STATUS_ACTIVE,
                'version': 1,
                'required_business_event': gpc.BUSINESS_EVENT_SUBSCRIPTION,
                'settlement_requirement': gpc.REQUIREMENT_CLEARED,
                'allowed_window_utc': {'start': '08:00', 'end': '18:00'},
                'maximum_daily_amount_usd': '10000000.00',
                'required_roles': [gpc.ROLE_TREASURY_OPERATOR, gpc.ROLE_COMPLIANCE_APPROVER],
                'violation_action': gpc.VIOLATION_ACTION_DENY,
                'origin': gpc.ORIGIN_DEMO_SEED,
            }),
            json.dumps({}), json.dumps({}),
            'Policy created (demo scenario seed).', user_id, now,
        ),
    )
    logger.info(
        'event=governance_policy_demo_seeded workspace_id=%s policy_key=%s', workspace_id, DEMO_POLICY_KEY,
    )
    return {'seeded': True, 'policy_id': policy_id, 'policy_key': DEMO_POLICY_KEY, 'origin': gpc.ORIGIN_DEMO_SEED}
