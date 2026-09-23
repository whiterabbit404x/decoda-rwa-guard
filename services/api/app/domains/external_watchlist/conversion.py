"""Convert an external protocol into a Pilot workspace — an explicit founder act.

    External Watchlist → Create Pilot Workspace → Copy selected targets
      → 30-day Pilot lifecycle (existing Pilot logic) → invite customer separately
      → historical external provenance kept

What this deliberately does, all in ONE transaction:

  * provisions the Pilot tenant through ``pilot.provision_pilot_organization``
    (the one function that brings a Pilot organization into existence), with
    the founder as its initial owner, and restores the founder's own active
    workspace afterwards so they are not silently switched into it;
  * sets the 30-day evaluation window with ``organizations.extend_evaluation``,
    the same call the /admin/customers "Extend 30d" control makes;
  * copies the selected public addresses into that workspace as INERT drafts —
    asset and target rows with ``enabled = FALSE`` and ``monitoring_enabled =
    FALSE`` — under the existing Pilot plan limits, each carrying provenance;
  * records the conversion and marks the watchlist converted.

What it deliberately does NOT do:

  * start any monitoring in the customer workspace. Copied targets stay off
    until someone in that workspace turns them on;
  * invite anyone. The customer is invited separately through the workspace's
    own invitation flow;
  * record the protocol as customer-authorized. Authorization is established by
    the customer accepting that invitation and enabling monitoring themselves —
    never by this action;
  * touch any tenant other than the one it just created.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.external_watchlist import service

PILOT_EVALUATION_DAYS = 30

#: External target type → the customer workspace's own vocabulary.
_CUSTOMER_TARGET_TYPE = {'contract': 'contract', 'oracle': 'oracle', 'wallet': 'wallet', 'multisig': 'wallet'}
_CUSTOMER_ASSET_TYPE = {'contract': 'contract', 'oracle': 'oracle', 'wallet': 'wallet', 'multisig': 'wallet'}


def _provenance(watchlist: dict[str, Any], target: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    return {
        'origin': 'external_watchlist',
        'origin_monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'monitoring_scope': ewc.MONITORING_SCOPE_WORKSPACE,
        'external_watchlist_id': str(watchlist['id']),
        'external_target_id': str(target['id']),
        'external_target_type': target['target_type'],
        'copied_at': now.isoformat(),
        'customer_authorized': False,
        'note': (
            'Copied from Decoda independent public monitoring. Monitoring in this workspace starts '
            'only when this workspace enables it.'
        ),
    }


def _copy_target(
    connection: Any, *, workspace_id: str, founder_id: str, watchlist: dict[str, Any], target: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    # Existing Pilot plan limits apply exactly as if the customer had typed these in.
    pilot.enforce_plan_creation_limit(connection, workspace_id, ent.LIMIT_MONITORED_CONTRACTS)
    provenance = _provenance(watchlist, target, now=now)
    label = target.get('label') or f"{watchlist['name']} {target['target_type']}"
    asset_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO assets (id, workspace_id, name, description, asset_type, chain_network, identifier,
                            risk_tier, enabled, token_contract_address, notes, created_by_user_id, updated_by_user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'medium', FALSE, %s, %s, %s, %s)
        """,
        (
            asset_id, workspace_id, str(label)[:120],
            'Copied from Decoda independent public monitoring (External Watchlist).',
            _CUSTOMER_ASSET_TYPE[target['target_type']], target['network'], target['address'],
            target['address'] if target['target_type'] in ('contract', 'oracle') else None,
            provenance['note'], founder_id, founder_id,
        ),
    )
    pilot.enforce_plan_creation_limit(connection, workspace_id, ent.LIMIT_MONITORING_TARGETS)
    workspace_target_id = str(uuid.uuid4())
    customer_type = _CUSTOMER_TARGET_TYPE[target['target_type']]
    connection.execute(
        """
        INSERT INTO targets (
            id, workspace_id, name, target_type, chain_network, contract_identifier, wallet_address,
            asset_type, owner_notes, enabled, asset_id, chain_id, target_metadata, monitoring_enabled,
            monitored_by_workspace_id, is_active, created_by_user_id, updated_by_user_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s::uuid, %s, %s::jsonb, FALSE, %s, TRUE, %s, %s)
        """,
        (
            workspace_target_id, workspace_id, str(label)[:120], customer_type, target['network'],
            target['address'] if customer_type != 'wallet' else None,
            target['address'] if customer_type == 'wallet' else None,
            _CUSTOMER_ASSET_TYPE[target['target_type']], provenance['note'], asset_id, int(target['chain_id']),
            json.dumps({'provenance': provenance}), workspace_id, founder_id, founder_id,
        ),
    )
    return {
        'external_target_id': str(target['id']),
        'workspace_asset_id': asset_id,
        'workspace_target_id': workspace_target_id,
        'address': target['address'],
        'network': target['network'],
        'external_target_type': target['target_type'],
        'workspace_target_type': customer_type,
        'monitoring_enabled': False,
    }


def convert(
    connection: Any, *, watchlist: dict[str, Any], targets: list[dict[str, Any]], founder: dict[str, Any],
    request: Any, workspace_name: str | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    founder_id = str(founder['id'])
    previous = service._row(connection.execute(
        'SELECT current_workspace_id FROM users WHERE id = %s', (founder_id,),
    ).fetchone()) or {}
    provisioned = pilot.provision_pilot_organization(
        connection, user_id=founder_id, organization_name=(workspace_name or watchlist['name']), request=request,
    )
    organization = provisioned['organization']
    workspace_id = str(provisioned['workspace_id'])
    # provision_pilot_organization makes the new workspace the creator's active
    # one. The founder did not ask to be moved into a customer's workspace.
    connection.execute(
        'UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s',
        (previous.get('current_workspace_id'), founder_id),
    )
    organization = org_service.extend_evaluation(
        connection, organization_id=str(organization['id']), days=PILOT_EVALUATION_DAYS, now=moment,
    )
    copied = [
        _copy_target(connection, workspace_id=workspace_id, founder_id=founder_id, watchlist=watchlist,
                     target=target, now=moment)
        for target in targets
    ]
    expires_at = organization.get('evaluation_expires_at')
    record = service.insert_conversion(
        connection, watchlist_id=str(watchlist['id']), organization_id=str(organization['id']),
        workspace_id=workspace_id, copied_targets=copied, evaluation_days=PILOT_EVALUATION_DAYS,
        evaluation_expires_at=expires_at, converted_by=founder_id,
    )
    service.mark_converted(connection, str(watchlist['id']), workspace_id)
    # The customer workspace's own audit trail says where these targets came
    # from, so provenance survives into the tenant that now owns them.
    pilot.log_audit(
        connection,
        action='external_watchlist.targets_copied',
        entity_type='workspace',
        entity_id=workspace_id,
        request=request,
        user_id=founder_id,
        workspace_id=workspace_id,
        metadata={
            'origin': 'external_watchlist',
            'origin_monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
            'protocol_name': watchlist['name'],
            'copied_target_count': len(copied),
            'monitoring_enabled': False,
            'customer_authorized': False,
        },
    )
    return {
        'conversion_id': str(record.get('id')),
        'organization': {
            'id': str(organization['id']),
            'name': organization.get('name'),
            'plan': ent.normalize_plan(organization.get('plan')),
            'status': ent.normalize_status(organization.get('status')),
        },
        'workspace': {'id': workspace_id, 'name': provisioned.get('workspace_name')},
        'evaluation': ent.evaluation_payload(organization),
        'evaluation_days': PILOT_EVALUATION_DAYS,
        'copied_targets': copied,
        'customer_authorized': False,
        'monitoring_started': False,
        'next_steps': [
            'Invite the customer from the new workspace\'s team settings (Settings → Team). '
            'This action did not send any invitation.',
            'Copied targets are inactive drafts. Monitoring starts only when the workspace enables it.',
            'The protocol is not recorded as customer-authorized until the customer accepts the invitation.',
        ],
    }
