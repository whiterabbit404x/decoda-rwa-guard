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

import logging
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

    now = now or pilot.utc_now()
    # The SAME writer the Create Policy endpoint uses, so a seeded policy and an
    # authored one differ in exactly one field — origin — and nowhere else. The
    # guarded INSERT makes "already present" a return value rather than a race.
    outcome = service.create_policy(
        connection,
        workspace_id=workspace_id,
        values={
            'policy_key': DEMO_POLICY_KEY,
            'name': DEMO_POLICY_NAME,
            'operation': gpc.OPERATION_MINT,
            'status': gpc.STATUS_ACTIVE,
            'required_business_event': gpc.BUSINESS_EVENT_SUBSCRIPTION,
            'settlement_requirement': gpc.REQUIREMENT_CLEARED,
            'allowed_window_start_utc': '08:00',
            'allowed_window_end_utc': '18:00',
            'maximum_daily_amount_usd': '10000000.00',
            'required_roles': [gpc.ROLE_TREASURY_OPERATOR, gpc.ROLE_COMPLIANCE_APPROVER],
            'violation_action': gpc.VIOLATION_ACTION_DENY,
        },
        user_id=user_id,
        now=now,
        origin=gpc.ORIGIN_DEMO_SEED,
    )
    if outcome['status'] == 'duplicate':
        return {'seeded': False, 'reason': 'already_present', 'policy_key': DEMO_POLICY_KEY}

    logger.info(
        'event=governance_policy_demo_seeded workspace_id=%s policy_key=%s', workspace_id, DEMO_POLICY_KEY,
    )
    return {'seeded': True, 'policy_id': outcome['policy_id'], 'policy_key': DEMO_POLICY_KEY, 'origin': gpc.ORIGIN_DEMO_SEED}
