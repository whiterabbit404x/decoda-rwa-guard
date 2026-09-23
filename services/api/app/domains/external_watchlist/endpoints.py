"""Request handlers for the External Watchlist (founder / internal admin only).

Order of checks on EVERY handler, before anything is read:

  1. live mode + schema          (as every internal endpoint)
  2. internal admin              ``organizations.require_internal_admin`` — the
                                 same server-side rule /admin/customers uses. A
                                 customer or Pilot user receives 403 and no data,
                                 whether or not the feature is enabled.
  3. feature flag                EXTERNAL_WATCHLIST_ENABLED; off → 404
  4. migration 0157              missing → 503, never a half-working surface

What these handlers never do: accept or store a private key, seed phrase,
signature, signer, API key or write permission (refused by field name and by
content); call a write or signing RPC method (the read-only gateway refuses
it); create a response action, integration, policy execution, pause control,
remediation or approval for an external target (refused with 403 by
``refuse_execution``); or read a customer tenant's data.

Every founder action is written to the hash-chained audit log with
``workspace_id = NULL`` (an internal action, not inside any customer
workspace), ``monitoring_scope = external_public`` and the staff actor type.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from services.api.app import onboarding_discovery as disc
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app import staff_access
from services.api.app.domains.external_watchlist import abi
from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.external_watchlist import conversion
from services.api.app.domains.external_watchlist import evidence as evidence_builder
from services.api.app.domains.external_watchlist import rpc
from services.api.app.domains.external_watchlist import service
from services.api.app.domains.external_watchlist import status as status_model

try:  # fastapi is stubbed in the offline test runner
    from fastapi import HTTPException, status
except Exception:  # pragma: no cover
    HTTPException = pilot.HTTPException  # type: ignore[assignment]
    status = pilot.status  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CODE_DISABLED = 'EXTERNAL_WATCHLIST_DISABLED'
CODE_SCHEMA_MISSING = 'EXTERNAL_WATCHLIST_SCHEMA_MISSING'
CODE_NOT_FOUND = 'EXTERNAL_WATCHLIST_NOT_FOUND'
CODE_EXECUTION_FORBIDDEN = 'EXTERNAL_EXECUTION_FORBIDDEN'
CODE_CREDENTIALS_REFUSED = 'EXTERNAL_CREDENTIALS_REFUSED'

#: Capabilities an external target can never have. Each is a real route that
#: answers 403 for an internal admin (and 403 for everyone else), so the
#: boundary is enforced — and testable — on the server, not by a hidden button.
FORBIDDEN_CAPABILITIES = (
    'response-actions',
    'execute',
    'transactions',
    'sign',
    'approvals',
    'integrations',
    'policy-execution',
    'pause-contract',
    'remediation',
)

#: Request fields that would carry a credential or a write capability. Refused
#: by name, anywhere in the body, before any value is looked at.
_FORBIDDEN_FIELD_FRAGMENTS = (
    'private', 'secret', 'mnemonic', 'seed', 'passphrase', 'signature', 'signer', 'signed',
    'keystore', 'password', 'api_key', 'apikey', 'walletconnect', 'wallet_connect',
    'write_permission', 'raw_transaction', 'rawtransaction', 'calldata',
)

_MAX_PAGE = 200


# ── shared guards ────────────────────────────────────────────────────────────
def _error(code: int, error_code: str, message: str, **extra: Any) -> Exception:
    return HTTPException(status_code=code, detail={'code': error_code, 'message': message, **extra})


def _require_feature() -> None:
    if not ewc.feature_enabled():
        raise _error(
            status.HTTP_404_NOT_FOUND, CODE_DISABLED,
            'External Watchlist is not enabled on this deployment (EXTERNAL_WATCHLIST_ENABLED).',
        )


def _require_schema(connection: Any) -> None:
    if not service.schema_ready(connection):
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, CODE_SCHEMA_MISSING,
            'External Watchlist is unavailable until migration 0157_external_watchlist.sql is applied.',
        )


def _authorize(connection: Any, request: Any) -> dict[str, Any]:
    pilot.ensure_pilot_schema(connection)
    admin = org_service.require_internal_admin(connection, request)
    _require_feature()
    _require_schema(connection)
    return admin


def _uuid(value: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        raise _error(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, f'{what} not found.') from None


def _page(limit: Any, offset: Any, *, default: int = 50) -> tuple[int, int]:
    try:
        size = int(limit if limit is not None else default)
    except (TypeError, ValueError):
        size = default
    try:
        start = int(offset or 0)
    except (TypeError, ValueError):
        start = 0
    return max(1, min(size, _MAX_PAGE)), max(0, start)


def _page_meta(total: int, limit: int, offset: int, returned: int) -> dict[str, Any]:
    return {'total': total, 'limit': limit, 'offset': offset, 'returned': returned,
            'has_more': offset + returned < total}


def refuse_credentials(value: Any, path: str = 'body') -> None:
    """Refuse any field that could carry a credential or a write capability."""
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).strip().lower()
            if name == 'execution_authority':
                if str(item or '').strip().upper() != ewc.EXECUTION_AUTHORITY_NONE:
                    raise _error(
                        status.HTTP_403_FORBIDDEN, CODE_EXECUTION_FORBIDDEN,
                        'External targets have execution_authority = NONE. It cannot be changed.',
                    )
                continue
            if any(fragment in name for fragment in _FORBIDDEN_FIELD_FRAGMENTS):
                raise _error(
                    status.HTTP_400_BAD_REQUEST, CODE_CREDENTIALS_REFUSED,
                    'External Watchlist never accepts private keys, seed phrases, signatures, signers, API '
                    'keys or write permissions. Remove that field.',
                    field=f'{path}.{key}',
                )
            refuse_credentials(item, f'{path}.{key}')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            refuse_credentials(item, f'{path}[{index}]')


def _refuse_secret_text(**fields: Any) -> None:
    for name, value in fields.items():
        if value and org_service.looks_like_secret(str(value)):
            raise _error(
                status.HTTP_400_BAD_REQUEST, CODE_CREDENTIALS_REFUSED,
                'That looks like a private key, credential, or seed phrase. External Watchlist only '
                'uses public blockchain data — remove it.',
                field=name,
            )


def _audit(connection: Any, request: Any, *, admin: dict[str, Any], action: str, entity_type: str,
           entity_id: Any, metadata: dict[str, Any] | None = None) -> None:
    pilot.log_audit(
        connection,
        action=f'external_watchlist.{action}',
        entity_type=entity_type,
        entity_id=str(entity_id),
        request=request,
        user_id=str(admin['id']),
        workspace_id=None,
        metadata={
            **(metadata or {}),
            'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
            'execution_authority': ewc.EXECUTION_AUTHORITY_NONE,
            'actor_type': staff_access.ACTOR_TYPE_DECODA_STAFF,
        },
    )


def _iso(value: Any) -> Any:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _clean_text(value: Any, *, field: str, limit: int, required: bool = False) -> str | None:
    text = ' '.join(str(value or '').split())
    if not text:
        if required:
            raise _error(status.HTTP_400_BAD_REQUEST, 'FIELD_REQUIRED', f'{field} is required.', field=field)
        return None
    if len(text) > limit:
        raise _error(status.HTTP_400_BAD_REQUEST, 'FIELD_TOO_LONG', f'{field} must be at most {limit} characters.', field=field)
    return text


def _website(value: Any) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    if '://' not in text:
        text = f'https://{text}'
    lowered = text.lower()
    try:
        parsed = urlsplit(text)
        host, _port = parsed.hostname or '', parsed.port
    except ValueError:
        host = ''
    if (not (lowered.startswith('https://') or lowered.startswith('http://')) or any(ch.isspace() for ch in text)
            or not re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)+', host) or parsed.username or parsed.password):
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_WEBSITE', 'Website must be an http(s) URL.', field='website_url')
    if len(text) > 300:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_WEBSITE', 'Website must be at most 300 characters.', field='website_url')
    return text


def _profiles(value: Any) -> list[str]:
    if value is None:
        return list(ewc.DETECTION_PROFILE_KEYS)
    if not isinstance(value, list):
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_DETECTION_PROFILES', 'detection_profiles must be a list.')
    chosen = [str(item).strip() for item in value if str(item).strip()]
    unknown = [item for item in chosen if item not in ewc.DETECTION_PROFILE_KEYS]
    if unknown:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_DETECTION_PROFILES',
                     f'Unknown detection profile(s): {", ".join(unknown)}.')
    return [key for key in ewc.DETECTION_PROFILE_KEYS if key in chosen]


def _backfill_days(value: Any, *, allow_zero: bool = True) -> int:
    if value is None or value == '':
        return ewc.DEFAULT_BACKFILL_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = -1
    options = ewc.BACKFILL_DAY_OPTIONS if allow_zero else tuple(d for d in ewc.BACKFILL_DAY_OPTIONS if d)
    if days not in options:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_BACKFILL_DAYS',
                     f'backfill_days must be one of {", ".join(str(o) for o in options)}.')
    return days


def _network(value: Any) -> str:
    network = ewc.canonical_network(value)
    if network is None:
        raise _error(
            status.HTTP_400_BAD_REQUEST, 'UNSUPPORTED_NETWORK',
            'Unsupported network. External Watchlist monitors the networks Decoda already monitors: '
            + ', '.join(meta['label'] for meta in ewc.SUPPORTED_NETWORKS.values()) + '.',
            supported=list(ewc.SUPPORTED_NETWORKS),
        )
    return network


def _target_input(body: dict[str, Any], *, network_value: Any) -> dict[str, Any]:
    network = _network(network_value)
    raw_address = body.get('address') if body.get('address') is not None else body.get('contract_address')
    try:
        address = disc.validate_contract_address(str(raw_address or ''))
    except disc.AddressValidationError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_ADDRESS', exc.message, field='address', reason=exc.code) from None
    target_type = str(body.get('target_type') or 'contract').strip().lower()
    if target_type not in ewc.TARGET_TYPES:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_TARGET_TYPE',
                     f'target_type must be one of {", ".join(ewc.TARGET_TYPES)}.', field='target_type')
    label = _clean_text(body.get('label'), field='label', limit=120)
    _refuse_secret_text(label=label)
    return {
        'network': network,
        'chain_id': int(ewc.SUPPORTED_NETWORKS[network]['chain_id']),
        'address': address,
        'target_type': target_type,
        'label': label,
    }


# ── RPC probes (bounded, request path) ───────────────────────────────────────
def probe_target(target: dict[str, Any]) -> dict[str, Any]:
    """Validate a public address against its chain. Read-only, hard-bounded.

    Required (refuses on failure): an RPC endpoint exists for the network, it
    serves the expected chain id, the chain tip is readable, and a contract,
    oracle or multisig address actually holds bytecode on that network.
    Best effort (never refuses): proxy implementation, Safe threshold/owners,
    oracle aggregator — used as the "previous state" baseline.
    """
    network = target['network']
    label = ewc.SUPPORTED_NETWORKS[network]['label']
    try:
        client = rpc.build_client(network, request_path=True)
    except rpc.RpcNotConfigured:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, 'RPC_UNAVAILABLE',
                     f'No RPC endpoint is configured for {label}, so this address cannot be monitored yet.') from None
    try:
        rpc.verify_chain(client, int(target['chain_id']))
        tip = rpc.block_number(client)
        code = rpc.get_code(client, target['address'])
    except rpc.ChainMismatch as exc:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, 'RPC_CHAIN_MISMATCH', str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - provider unavailable
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, 'RPC_UNAVAILABLE',
                     f'The {label} RPC endpoint did not answer: {rpc.sanitize_error(exc, 160)}') from None
    has_code = code not in ('', '0x', '0x0')
    if target['target_type'] in ewc.CODE_REQUIRED_TARGET_TYPES and not has_code:
        raise _error(
            status.HTTP_400_BAD_REQUEST, 'NO_CONTRACT_CODE',
            f'No contract bytecode exists at this address on {label}. Check the address and the network.',
            field='address',
        )
    runtime_state: dict[str, Any] = {}
    contract_type = 'externally_owned_account' if not has_code else 'contract'
    try:
        if target['target_type'] == 'multisig':
            threshold = rpc.eth_call(client, target['address'], abi.SELECTOR_GET_THRESHOLD)
            if threshold and len(threshold) >= 66:
                runtime_state['safe'] = {'threshold': int(threshold[-64:], 16)}
                contract_type = 'safe_multisig'
        elif target['target_type'] == 'oracle':
            aggregator = rpc.eth_call(client, target['address'], abi.SELECTOR_AGGREGATOR)
            if aggregator and len(aggregator) >= 66:
                resolved = abi.word_to_address(aggregator[-64:])
                if resolved != abi.ZERO_ADDRESS:
                    runtime_state['oracle_meta'] = {'aggregator': resolved}
                    contract_type = 'chainlink_proxy'
        elif has_code:
            slot = client.call('eth_getStorageAt', [target['address'], abi.EIP1967_IMPLEMENTATION_SLOT, 'latest'])
            implementation = abi.word_to_address(str(slot)[-64:]) if slot and len(str(slot)) >= 42 else None
            if implementation and implementation != abi.ZERO_ADDRESS:
                runtime_state['proxy'] = {'implementation': implementation}
                contract_type = 'proxy_erc1967'
    except rpc.ExternalExecutionForbidden:
        raise
    except Exception:  # noqa: BLE001 - enrichment only
        logger.info('external_watchlist_probe_enrichment_skipped network=%s', network)
    return {
        'tip': tip,
        'has_code': has_code,
        'contract_type': contract_type,
        'runtime_state': runtime_state,
        'rpc_host': client.active_host,
    }


# ── payloads ─────────────────────────────────────────────────────────────────
def _network_label(network: str) -> dict[str, Any]:
    meta = ewc.SUPPORTED_NETWORKS.get(network, {})
    return {'key': network, 'label': meta.get('label', network), 'short_label': meta.get('short_label', network),
            'chain_id': meta.get('chain_id')}


def _aggregate_backfill(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not jobs:
        return None
    progress = [status_model.backfill_progress(job) for job in jobs]
    open_jobs = [p for p in progress if p and p['status'] in ewc.OPEN_BACKFILL_STATUSES]
    scope = open_jobs or [p for p in progress if p]
    total = sum(p['total_blocks'] for p in scope)
    scanned = sum(p['scanned_blocks'] for p in scope)
    days = max((p['requested_days'] for p in scope), default=0)
    if open_jobs:
        state = 'running' if any(p['status'] == 'running' for p in open_jobs) else 'pending'
        fraction = (scanned / total) if total else 0.0
    else:
        order = {'failed': 3, 'partial': 2, 'completed': 1}
        state = max((p['status'] for p in scope), key=lambda s: order.get(s, 0))
        fraction = 1.0 if state == 'completed' else ((scanned / total) if total else 0.0)
    return {
        'status': state,
        'percent': round(fraction * 100, 1),
        'days_analyzed': round(fraction * days, 1),
        'requested_days': days,
        'planned': all(p['planned'] for p in scope),
        'targets': len(scope),
    }


def _target_payload(row: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    network = str(row.get('network') or '')
    return {
        'id': str(row['id']),
        'watchlist_id': str(row['watchlist_id']),
        'target_type': row.get('target_type'),
        'network': _network_label(network),
        'chain_id': row.get('chain_id'),
        'address': row.get('address'),
        'label': row.get('label'),
        'contract_type': row.get('contract_type'),
        'abi_source': row.get('abi_source'),
        'has_code': row.get('has_code'),
        'monitoring_enabled': bool(row.get('monitoring_enabled')),
        'monitoring_scope': row.get('monitoring_scope') or ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'execution_authority': row.get('execution_authority') or ewc.EXECUTION_AUTHORITY_NONE,
        'live_start_block': row.get('live_start_block'),
        'last_processed_block': row.get('last_processed_block'),
        'last_polled_at': _iso(row.get('last_polled_at')),
        'last_successful_poll_at': _iso(row.get('last_successful_poll_at')),
        'last_event_at': _iso(row.get('last_event_at')),
        'last_poll_error': row.get('last_poll_error'),
        'consecutive_failures': int(row.get('consecutive_failures') or 0),
        'explorer_url': ewc.explorer_address_url(network, str(row.get('address') or '')),
        'backfill': _backfill_payload(job),
        'created_at': _iso(row.get('created_at')),
    }


def _backfill_payload(job: dict[str, Any] | None) -> dict[str, Any] | None:
    progress = status_model.backfill_progress(job)
    if progress is None:
        return None
    return {**{key: _iso(value) for key, value in progress.items()}, 'id': str(job['id']), 'target_id': str(job['target_id'])}


def _watchlist_summary(
    row: dict[str, Any], *, targets: list[dict[str, Any]], jobs: list[dict[str, Any]],
    activity: dict[str, Any], heartbeat: Any, now: datetime,
) -> dict[str, Any]:
    derived = status_model.derive_status(
        watchlist=row, targets=targets, latest_backfills=jobs, worker_heartbeat_at=heartbeat, now=now,
    )
    networks = sorted({str(t.get('network')) for t in targets})
    polls = [t.get('last_successful_poll_at') for t in targets if t.get('last_successful_poll_at')]
    latest_event = activity.get('latest_event')
    latest_finding = activity.get('latest_finding')
    return {
        'id': str(row['id']),
        'name': row.get('name'),
        'slug': row.get('slug'),
        'website_url': row.get('website_url'),
        'description': row.get('description'),
        'networks': [_network_label(network) for network in networks],
        'contract_count': sum(1 for t in targets if t.get('target_type') in ewc.CONTRACT_LIKE_TARGET_TYPES),
        'wallet_count': sum(1 for t in targets if t.get('target_type') in ewc.WALLET_LIKE_TARGET_TYPES),
        'target_count': len(targets),
        'status': derived['status'],
        'status_reason': derived.get('reason'),
        'status_reason_label': status_model.STATUS_REASON_LABELS.get(str(derived.get('reason'))),
        'monitoring_enabled': bool(row.get('monitoring_enabled')),
        'backfill_days': row.get('backfill_days'),
        'detection_profiles': row.get('detection_profiles') or [],
        'detection_config': row.get('detection_config') or {},
        'latest_event': (
            {'event_name': latest_event.get('event_name'), 'event_category': latest_event.get('event_category'),
             'observed_at': _iso(latest_event.get('observed_at'))} if latest_event else None
        ),
        'latest_finding': (
            {'title': latest_finding.get('title'), 'severity': latest_finding.get('severity'),
             'finding_class': latest_finding.get('finding_class'),
             'observed_at': _iso(latest_finding.get('observed_at')), 'detected_at': _iso(latest_finding.get('detected_at'))}
            if latest_finding else None
        ),
        'last_scanned_at': _iso(max(polls)) if polls else None,
        'backfill': _aggregate_backfill(jobs),
        'converted_workspace_id': str(row['converted_workspace_id']) if row.get('converted_workspace_id') else None,
        'converted_at': _iso(row.get('converted_at')),
        'created_at': _iso(row.get('created_at')),
        'updated_at': _iso(row.get('updated_at')),
        'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'execution_authority': ewc.EXECUTION_AUTHORITY_NONE,
    }


def _event_payload(row: dict[str, Any]) -> dict[str, Any]:
    network = str(row.get('network') or '')
    return {
        'id': str(row['id']),
        'target_id': str(row['target_id']),
        'target_label': row.get('target_label'),
        'target_address': row.get('target_address'),
        'event_name': row.get('event_name'),
        'event_type': row.get('event_type'),
        'event_category': row.get('event_category'),
        'network': _network_label(network),
        'contract_address': row.get('contract_address'),
        'tx_hash': row.get('tx_hash'),
        'tx_explorer_url': ewc.explorer_tx_url(network, str(row.get('tx_hash') or '')),
        'block_number': row.get('block_number'),
        'log_index': row.get('log_index'),
        'decoded': row.get('decoded') or {},
        'decode_status': row.get('decode_status'),
        'initiator': row.get('initiator'),
        'observed_at': _iso(row.get('observed_at')),
        'observed_at_source': row.get('observed_at_source'),
        'ingest_source': row.get('ingest_source'),
        'payload_sha256': row.get('payload_sha256'),
        'monitoring_scope': row.get('monitoring_scope') or ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
    }


def _finding_payload(row: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    network = str(row.get('network') or '')
    payload = {
        'id': str(row['id']),
        'watchlist_id': str(row['watchlist_id']),
        'target_id': str(row['target_id']) if row.get('target_id') else None,
        'target_label': row.get('target_label'),
        'target_address': row.get('target_address'),
        'target_type': row.get('target_type'),
        'title': row.get('title'),
        'finding_type': row.get('finding_type'),
        'finding_class': row.get('finding_class'),
        'finding_class_label': ewc.FINDING_CLASS_LABELS.get(str(row.get('finding_class'))),
        'rule_key': row.get('rule_key'),
        'detection_profile': row.get('detection_profile'),
        'severity': row.get('severity'),
        'status': row.get('status'),
        'status_label': ewc.FINDING_STATUS_LABELS.get(str(row.get('status'))),
        'network': _network_label(network),
        'contract_address': row.get('contract_address'),
        'tx_hash': row.get('tx_hash'),
        'tx_explorer_url': ewc.explorer_tx_url(network, str(row.get('tx_hash'))) if row.get('tx_hash') else None,
        'contract_explorer_url': (
            ewc.explorer_address_url(network, str(row.get('contract_address'))) if row.get('contract_address') else None
        ),
        'block_number': row.get('block_number'),
        'observed_at': _iso(row.get('observed_at')),
        'initiator': row.get('initiator'),
        'detected_at': _iso(row.get('detected_at')),
        'source_label': 'External Public Monitoring',
        'monitoring_scope': row.get('monitoring_scope') or ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'execution_authority': row.get('execution_authority') or ewc.EXECUTION_AUTHORITY_NONE,
        # The only actions an external finding offers: review states and
        # evidence. There is no response action to offer.
        'available_actions': ['set_status', 'mark_outreach_candidate', 'generate_evidence_package',
                              'generate_prospect_report'],
    }
    if detail:
        payload.update({
            'explanation': row.get('explanation'),
            'decoded': row.get('decoded') or {},
            'previous_state': row.get('previous_state'),
            'new_state': row.get('new_state'),
            'ai_analysis': row.get('ai_analysis') or {},
            'confidence': float(row.get('confidence') or 0),
            'status_note': row.get('status_note'),
            'status_updated_at': _iso(row.get('status_updated_at')),
            'detector_version': row.get('detector_version'),
        })
    return payload


def _evidence_summary(row: dict[str, Any], verification: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'id': str(row['id']),
        'finding_id': str(row['finding_id']) if row.get('finding_id') else None,
        'finding_title': row.get('finding_title'),
        'finding_tx_hash': row.get('finding_tx_hash'),
        'package_type': row.get('package_type'),
        'source_evidence_id': str(row['source_evidence_id']) if row.get('source_evidence_id') else None,
        'manifest_sha256': row.get('manifest_sha256'),
        'evidence_sha256': row.get('evidence_sha256'),
        'signature_algorithm': row.get('signature_algorithm'),
        'disclaimer_version': row.get('disclaimer_version'),
        'generated_at': _iso(row.get('generated_at')),
        'monitoring_scope': row.get('monitoring_scope') or ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'verification': verification,
    }


def _notices() -> dict[str, str]:
    return {
        'watchlist': ewc.WATCHLIST_NOTICE,
        'detail': ewc.DETAIL_NOTICE,
        'evidence': ewc.EVIDENCE_DISCLAIMER,
        'authorization': ewc.AUTHORIZATION_UNKNOWN_STATEMENT,
        'version': ewc.DISCLAIMER_VERSION,
    }


def _load_watchlist(connection: Any, watchlist_id: str, *, for_update: bool = False) -> dict[str, Any]:
    row = service.get_watchlist(connection, _uuid(watchlist_id, 'Protocol'), for_update=for_update)
    if row is None:
        raise _error(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, 'Protocol not found.')
    return row


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── console configuration ────────────────────────────────────────────────────
def get_console_config(request: Any) -> dict[str, Any]:
    """What the founder console may render. Internal admin only, flag-aware.

    Answers even when the feature is OFF (``enabled: false``), because this is
    how the console decides whether to show the External Watchlist entry.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        org_service.require_internal_admin(connection, request)
        enabled = ewc.feature_enabled()
        feature: dict[str, Any] = {'enabled': enabled}
        if enabled:
            feature.update({
                'schema_ready': service.schema_ready(connection),
                'networks': [
                    {**_network_label(key), 'explorer_url': meta['explorer_url'], 'rpc_configured': ewc.rpc_configured(key)}
                    for key, meta in ewc.SUPPORTED_NETWORKS.items()
                ],
                'target_types': list(ewc.TARGET_TYPES),
                'detection_profiles': [dict(profile) for profile in ewc.DETECTION_PROFILES],
                'backfill_day_options': list(ewc.BACKFILL_DAY_OPTIONS),
                'default_backfill_days': ewc.DEFAULT_BACKFILL_DAYS,
                'finding_statuses': [{'key': k, 'label': v} for k, v in ewc.FINDING_STATUS_LABELS.items()],
                'statuses': list(ewc.WATCHLIST_STATUSES),
                'notices': _notices(),
                'pilot_evaluation_days': conversion.PILOT_EVALUATION_DAYS,
            })
        return {'internal_admin': True, 'features': {'external_watchlist': feature}}


# ── watchlists ───────────────────────────────────────────────────────────────
def list_watchlists(
    request: Any, *, q: str | None = None, network: str | None = None, status_filter: str | None = None,
    limit: Any = None, offset: Any = None,
) -> dict[str, Any]:
    pilot.require_live_mode()
    page_size, start = _page(limit, offset)
    network_key = None
    if network:
        network_key = _network(network)
    wanted_status = str(status_filter or '').strip().lower() or None
    if wanted_status and wanted_status not in ewc.WATCHLIST_STATUSES:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_STATUS',
                     f'status must be one of {", ".join(ewc.WATCHLIST_STATUSES)}.')
    search = ' '.join(str(q or '').split())[:120] or None
    with pilot.pg_connection() as connection:
        _authorize(connection, request)
        rows = service.list_watchlist_rows(connection, search=search, network=network_key)
        ids = [str(row['id']) for row in rows]
        targets = service.list_targets(connection, ids)
        jobs = service.latest_backfills(connection, ids)
        activity = service.watchlist_activity(connection, ids)
        heartbeat = service.latest_worker_heartbeat(connection)
        now = _now()
        summaries = [
            _watchlist_summary(
                row,
                targets=[t for t in targets if str(t['watchlist_id']) == str(row['id'])],
                jobs=[j for j in jobs if str(j['watchlist_id']) == str(row['id'])],
                activity=activity.get(str(row['id'])) or {},
                heartbeat=heartbeat, now=now,
            )
            for row in rows
        ]
        if wanted_status:
            summaries = [item for item in summaries if item['status'] == wanted_status]
        total = len(summaries)
        page = summaries[start:start + page_size]
        connection.commit()
        return {
            'watchlists': page,
            'pagination': _page_meta(total, page_size, start, len(page)),
            'filters': {'q': search, 'network': network_key, 'status': wanted_status},
            'worker_heartbeat_at': _iso(heartbeat),
            'notices': _notices(),
            'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        }


def _detail(connection: Any, watchlist: dict[str, Any]) -> dict[str, Any]:
    watchlist_id = str(watchlist['id'])
    targets = service.list_targets(connection, [watchlist_id])
    jobs = service.latest_backfills(connection, [watchlist_id])
    activity = service.watchlist_activity(connection, [watchlist_id]).get(watchlist_id) or {}
    heartbeat = service.latest_worker_heartbeat(connection)
    now = _now()
    summary = _watchlist_summary(watchlist, targets=targets, jobs=jobs, activity=activity, heartbeat=heartbeat, now=now)
    jobs_by_target = {str(job['target_id']): job for job in jobs}
    counts = service.overview_counts(connection, watchlist_id, now=now)
    polls = [t.get('last_successful_poll_at') for t in targets if t.get('last_successful_poll_at')]
    conversion_row = service.get_conversion(connection, watchlist_id)
    return {
        'watchlist': summary,
        'overview': {
            'status': summary['status'],
            'status_reason': summary['status_reason'],
            'status_reason_label': summary['status_reason_label'],
            'contracts_monitored': summary['contract_count'],
            'wallets_monitored': summary['wallet_count'],
            'events_24h': counts['events_24h'],
            'events_total': counts['events_total'],
            'findings_30d': counts['findings_30d'],
            'findings_new': counts['findings_new'],
            'last_block_processed': [
                {'network': _network_label(item['network']), 'block': item['last_processed_block']}
                for item in status_model.rpc_health(targets, now=now)
            ],
            'rpc_health': status_model.rpc_health(targets, now=now),
            'last_successful_poll_at': _iso(max(polls)) if polls else None,
            'worker_heartbeat_at': _iso(heartbeat),
            'backfill': summary['backfill'],
            'recent_activity': [
                {
                    'event_name': event.get('event_name'),
                    'event_category': event.get('event_category'),
                    'contract_address': event.get('contract_address'),
                    'tx_hash': event.get('tx_hash'),
                    'tx_explorer_url': ewc.explorer_tx_url(str(event.get('network')), str(event.get('tx_hash'))),
                    'block_number': event.get('block_number'),
                    'observed_at': _iso(event.get('observed_at')),
                }
                for event in service.recent_events(connection, watchlist_id)
            ],
        },
        'targets': [_target_payload(target, jobs_by_target.get(str(target['id']))) for target in targets],
        'conversion': (
            {
                'workspace_id': str(conversion_row['workspace_id']) if conversion_row.get('workspace_id') else None,
                'organization_id': str(conversion_row['organization_id']) if conversion_row.get('organization_id') else None,
                'converted_at': _iso(conversion_row.get('converted_at')),
                'evaluation_days': conversion_row.get('evaluation_days'),
                'evaluation_expires_at': _iso(conversion_row.get('evaluation_expires_at')),
                'copied_targets': conversion_row.get('copied_targets') or [],
                'customer_authorized': False,
            } if conversion_row else None
        ),
        'notices': _notices(),
        'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        'execution_authority': ewc.EXECUTION_AUTHORITY_NONE,
    }


def create_watchlist(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        refuse_credentials(body)
        name = _clean_text(body.get('name'), field='name', limit=120, required=True)
        website = _website(body.get('website_url') if 'website_url' in body else body.get('website'))
        description = _clean_text(body.get('description'), field='description', limit=2000)
        _refuse_secret_text(name=name, website_url=website, description=description)
        network_value = body.get('network')
        if not str(network_value or '').strip():
            raise _error(status.HTTP_400_BAD_REQUEST, 'FIELD_REQUIRED', 'network is required.', field='network')
        network = _network(network_value)
        backfill_days = _backfill_days(body.get('backfill_days'))
        profiles = _profiles(body.get('detection_profiles'))
        try:
            detection_config = ewc.normalize_detection_config(body.get('detection_config'))
        except ewc.DetectionConfigError as exc:
            raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_DETECTION_CONFIG', str(exc)) from None
        has_address = bool(str(body.get('address') or body.get('contract_address') or '').strip())
        target_input = _target_input(body, network_value=network) if has_address else None

        slug = service.slugify(name)
        existing = service.find_active_watchlist_by_slug(connection, slug)
        if existing is not None:
            raise _error(status.HTTP_409_CONFLICT, 'EXTERNAL_WATCHLIST_DUPLICATE',
                         f'{existing["name"]} is already on the External Watchlist.', watchlist_id=str(existing['id']))
        probe = None
        if target_input is not None:
            elsewhere = service.find_target_elsewhere(
                connection, chain_id=target_input['chain_id'], address=target_input['address'], exclude_watchlist_id=None,
            )
            if elsewhere is not None:
                raise _error(status.HTTP_409_CONFLICT, 'EXTERNAL_TARGET_ALREADY_MONITORED',
                             f'This address is already monitored under {elsewhere["watchlist_name"]}.',
                             watchlist_id=str(elsewhere['watchlist_id']))
            probe = probe_target(target_input)

        initial_status = 'paused' if target_input is None else ('backfilling' if backfill_days else 'degraded')
        watchlist = service.insert_watchlist(
            connection, name=name, slug=slug, website_url=website, description=description,
            backfill_days=backfill_days, detection_profiles=profiles, detection_config=detection_config,
            created_by=str(admin['id']), status=initial_status,
        )
        _audit(connection, request, admin=admin, action='created', entity_type='external_watchlist',
               entity_id=watchlist['id'], metadata={
                   'name': name, 'network': network, 'backfill_days': backfill_days,
                   'detection_profiles': profiles, 'website_url': website,
               })
        if target_input is not None and probe is not None:
            _add_target(connection, request, admin=admin, watchlist=watchlist, target_input=target_input,
                        probe=probe, backfill_days=backfill_days)
        connection.commit()
        logger.info('external_watchlist_created watchlist_id=%s network=%s', watchlist['id'], network)
        return _detail(connection, watchlist)


def _add_target(connection: Any, request: Any, *, admin: dict[str, Any], watchlist: dict[str, Any],
                target_input: dict[str, Any], probe: dict[str, Any], backfill_days: int) -> dict[str, Any]:
    target = service.insert_target(
        connection, watchlist_id=str(watchlist['id']), target_type=target_input['target_type'],
        network=target_input['network'], chain_id=target_input['chain_id'], address=target_input['address'],
        label=target_input['label'], contract_type=probe.get('contract_type'), has_code=probe.get('has_code'),
        live_start_block=int(probe['tip']) + 1, runtime_state=probe.get('runtime_state') or {},
        created_by=str(admin['id']),
    )
    _audit(connection, request, admin=admin, action='target_added', entity_type='external_watchlist_target',
           entity_id=target['id'], metadata={
               'watchlist_id': str(watchlist['id']), 'network': target_input['network'],
               'address': target_input['address'], 'target_type': target_input['target_type'],
               'live_start_block': target.get('live_start_block'),
           })
    if backfill_days:
        job = service.create_backfill(connection, watchlist_id=str(watchlist['id']), target=target,
                                      days=backfill_days, requested_by=str(admin['id']))
        _audit(connection, request, admin=admin, action='backfill_triggered', entity_type='external_watchlist_backfill',
               entity_id=job['id'], metadata={'watchlist_id': str(watchlist['id']), 'target_id': str(target['id']),
                                              'requested_days': backfill_days})
    return target


def get_watchlist(watchlist_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        detail = _detail(connection, watchlist)
        connection.commit()
        return detail


def update_watchlist(watchlist_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        refuse_credentials(body)
        watchlist = _load_watchlist(connection, watchlist_id, for_update=True)
        fields: dict[str, Any] = {}
        if 'name' in body:
            fields['name'] = _clean_text(body.get('name'), field='name', limit=120, required=True)
            slug = service.slugify(fields['name'])
            clash = service.find_active_watchlist_by_slug(connection, slug)
            if clash is not None and str(clash['id']) != str(watchlist['id']):
                raise _error(status.HTTP_409_CONFLICT, 'EXTERNAL_WATCHLIST_DUPLICATE',
                             f'{clash["name"]} is already on the External Watchlist.')
            fields['slug'] = slug
        if 'website_url' in body:
            fields['website_url'] = _website(body.get('website_url'))
        if 'description' in body:
            fields['description'] = _clean_text(body.get('description'), field='description', limit=2000)
        _refuse_secret_text(**{k: v for k, v in fields.items() if k in ('name', 'website_url', 'description')})
        if 'detection_profiles' in body:
            fields['detection_profiles'] = _profiles(body.get('detection_profiles'))
        if 'detection_config' in body:
            try:
                fields['detection_config'] = ewc.normalize_detection_config(body.get('detection_config'))
            except ewc.DetectionConfigError as exc:
                raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_DETECTION_CONFIG', str(exc)) from None
        toggled = None
        if 'monitoring_enabled' in body:
            enabled = body.get('monitoring_enabled')
            if not isinstance(enabled, bool):
                raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_MONITORING_ENABLED', 'monitoring_enabled must be true or false.')
            if enabled != bool(watchlist.get('monitoring_enabled')):
                fields['monitoring_enabled'] = enabled
                toggled = enabled
                if not enabled:
                    fields['status'] = 'paused'
        updated = service.update_watchlist_fields(connection, str(watchlist['id']), fields) or watchlist
        changed = sorted(key for key in fields if key not in ('slug', 'status', 'monitoring_enabled'))
        if changed:
            _audit(connection, request, admin=admin, action='updated', entity_type='external_watchlist',
                   entity_id=watchlist['id'], metadata={'fields': changed})
        if toggled is not None:
            _audit(connection, request, admin=admin,
                   action='monitoring_resumed' if toggled else 'monitoring_paused',
                   entity_type='external_watchlist', entity_id=watchlist['id'], metadata={'scope': 'protocol'})
        connection.commit()
        return _detail(connection, updated)


def delete_watchlist(watchlist_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id, for_update=True)
        service.soft_delete_watchlist(connection, str(watchlist['id']))
        _audit(connection, request, admin=admin, action='deleted', entity_type='external_watchlist',
               entity_id=watchlist['id'], metadata={'name': watchlist.get('name')})
        connection.commit()
        return {'deleted': True, 'id': str(watchlist['id']),
                'retained': 'Observed events, findings and evidence are retained for provenance.'}


# ── targets ──────────────────────────────────────────────────────────────────
def add_target(watchlist_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        refuse_credentials(body)
        watchlist = _load_watchlist(connection, watchlist_id, for_update=True)
        if not str(body.get('network') or '').strip():
            raise _error(status.HTTP_400_BAD_REQUEST, 'FIELD_REQUIRED', 'network is required.', field='network')
        target_input = _target_input(body, network_value=body.get('network'))
        backfill_days = _backfill_days(body.get('backfill_days', watchlist.get('backfill_days')))
        if service.find_active_target(connection, watchlist_id=str(watchlist['id']),
                                      chain_id=target_input['chain_id'], address=target_input['address']):
            raise _error(status.HTTP_409_CONFLICT, 'DUPLICATE_TARGET',
                         'This address is already monitored for this protocol on that network.')
        elsewhere = service.find_target_elsewhere(connection, chain_id=target_input['chain_id'],
                                                  address=target_input['address'],
                                                  exclude_watchlist_id=str(watchlist['id']))
        if elsewhere is not None:
            raise _error(status.HTTP_409_CONFLICT, 'EXTERNAL_TARGET_ALREADY_MONITORED',
                         f'This address is already monitored under {elsewhere["watchlist_name"]}.',
                         watchlist_id=str(elsewhere['watchlist_id']))
        probe = probe_target(target_input)
        target = _add_target(connection, request, admin=admin, watchlist=watchlist, target_input=target_input,
                             probe=probe, backfill_days=backfill_days)
        connection.commit()
        return {'target': _target_payload(target, None), **_detail(connection, watchlist)}


def _load_target(connection: Any, watchlist: dict[str, Any], target_id: str) -> dict[str, Any]:
    target = service.get_target(connection, watchlist_id=str(watchlist['id']), target_id=_uuid(target_id, 'Target'))
    if target is None:
        raise _error(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, 'Target not found.')
    return target


def update_target(watchlist_id: str, target_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        refuse_credentials(body)
        watchlist = _load_watchlist(connection, watchlist_id)
        target = _load_target(connection, watchlist, target_id)
        fields: dict[str, Any] = {}
        if 'label' in body:
            fields['label'] = _clean_text(body.get('label'), field='label', limit=120)
            _refuse_secret_text(label=fields['label'])
        toggled = None
        if 'monitoring_enabled' in body:
            enabled = body.get('monitoring_enabled')
            if not isinstance(enabled, bool):
                raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_MONITORING_ENABLED', 'monitoring_enabled must be true or false.')
            if enabled != bool(target.get('monitoring_enabled')):
                fields['monitoring_enabled'] = enabled
                toggled = enabled
        disallowed = sorted(set(body) - {'label', 'monitoring_enabled'})
        if disallowed:
            raise _error(status.HTTP_400_BAD_REQUEST, 'FIELD_NOT_EDITABLE',
                         f'Only label and monitoring_enabled can be changed ({", ".join(disallowed)} cannot).')
        updated = service.update_target_fields(connection, watchlist_id=str(watchlist['id']),
                                               target_id=str(target['id']), fields=fields) or target
        if 'label' in fields:
            _audit(connection, request, admin=admin, action='target_updated', entity_type='external_watchlist_target',
                   entity_id=target['id'], metadata={'watchlist_id': str(watchlist['id']), 'fields': ['label']})
        if toggled is not None:
            _audit(connection, request, admin=admin,
                   action='monitoring_resumed' if toggled else 'monitoring_paused',
                   entity_type='external_watchlist_target', entity_id=target['id'],
                   metadata={'watchlist_id': str(watchlist['id']), 'scope': 'target', 'address': target.get('address')})
        connection.commit()
        return {'target': _target_payload(updated, None)}


def remove_target(watchlist_id: str, target_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        target = _load_target(connection, watchlist, target_id)
        service.remove_target(connection, watchlist_id=str(watchlist['id']), target_id=str(target['id']))
        _audit(connection, request, admin=admin, action='target_removed', entity_type='external_watchlist_target',
               entity_id=target['id'], metadata={'watchlist_id': str(watchlist['id']), 'address': target.get('address'),
                                                 'network': target.get('network')})
        connection.commit()
        return {'removed': True, 'target_id': str(target['id']),
                'retained': 'Observed events and findings for this target are retained for provenance.'}


def run_target_diagnostic(watchlist_id: str, target_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        target = _load_target(connection, watchlist, target_id)
        checks: list[dict[str, Any]] = []
        started = _now()
        result: dict[str, Any] = {'ok': False}
        try:
            probe = probe_target({
                'network': target['network'], 'chain_id': int(target['chain_id']),
                'address': target['address'], 'target_type': target['target_type'],
            })
            checks = [
                {'check': 'rpc_configured', 'ok': True},
                {'check': 'chain_id_matches', 'ok': True, 'expected': int(target['chain_id'])},
                {'check': 'chain_tip_readable', 'ok': True, 'block': probe['tip']},
                {'check': 'bytecode_present', 'ok': bool(probe['has_code']) or target['target_type'] == 'wallet',
                 'has_code': probe['has_code']},
            ]
            lag = (int(probe['tip']) - int(target['last_processed_block'])) if target.get('last_processed_block') is not None else None
            checks.append({'check': 'processing_lag_blocks', 'ok': lag is not None, 'value': lag})
            merged_state = dict(target.get('runtime_state') or {})
            for key, value in (probe.get('runtime_state') or {}).items():
                existing = merged_state.get(key) if isinstance(merged_state.get(key), dict) else {}
                merged_state[key] = {**value, **existing} if key in ('safe', 'proxy') else {**existing, **value}
            service.update_target_fields(connection, watchlist_id=str(watchlist['id']), target_id=str(target['id']),
                                         fields={'contract_type': probe.get('contract_type'), 'has_code': probe.get('has_code'),
                                                 'runtime_state': merged_state})
            result = {'ok': all(c['ok'] for c in checks[:4]), 'contract_type': probe.get('contract_type'),
                      'chain_tip': probe['tip'], 'rpc_host': probe.get('rpc_host'),
                      'resolved': probe.get('runtime_state') or {}}
        except HTTPException as exc:  # a failed check is a diagnostic RESULT, not a request error
            detail = exc.detail if isinstance(exc.detail, dict) else {'message': str(exc.detail)}
            checks.append({'check': str(detail.get('code') or 'diagnostic').lower(), 'ok': False,
                           'message': detail.get('message')})
            result = {'ok': False, 'error': detail}
        _audit(connection, request, admin=admin, action='diagnostic_run', entity_type='external_watchlist_target',
               entity_id=target['id'], metadata={'watchlist_id': str(watchlist['id']), 'ok': result.get('ok')})
        connection.commit()
        return {
            'target_id': str(target['id']),
            'network': _network_label(str(target['network'])),
            'checked_at': started.isoformat(),
            'checks': checks,
            'result': result,
            'read_only': True,
        }


# ── backfill ─────────────────────────────────────────────────────────────────
def trigger_backfill(watchlist_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        refuse_credentials(body)
        watchlist = _load_watchlist(connection, watchlist_id, for_update=True)
        days = _backfill_days(body.get('days', body.get('backfill_days', watchlist.get('backfill_days') or 30)),
                              allow_zero=False)
        targets = service.list_targets(connection, [str(watchlist['id'])])
        wanted = body.get('target_ids')
        if wanted is not None:
            if not isinstance(wanted, list) or not wanted:
                raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_TARGET_IDS', 'target_ids must be a non-empty list.')
            wanted_ids = {_uuid(item, 'Target') for item in wanted}
            unknown = wanted_ids - {str(t['id']) for t in targets}
            if unknown:
                raise _error(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, 'One or more targets were not found on this protocol.')
            targets = [t for t in targets if str(t['id']) in wanted_ids]
        if not targets:
            raise _error(status.HTTP_400_BAD_REQUEST, 'NO_TARGETS', 'Add a target before running a historical backfill.')
        created, skipped = [], []
        for target in targets:
            if service.open_backfill_for_target(connection, str(target['id'])) is not None:
                skipped.append({'target_id': str(target['id']), 'reason': 'backfill_already_open'})
                continue
            job = service.create_backfill(connection, watchlist_id=str(watchlist['id']), target=target,
                                          days=days, requested_by=str(admin['id']))
            created.append(job)
            _audit(connection, request, admin=admin, action='backfill_triggered',
                   entity_type='external_watchlist_backfill', entity_id=job['id'],
                   metadata={'watchlist_id': str(watchlist['id']), 'target_id': str(target['id']), 'requested_days': days})
        if not created:
            raise _error(status.HTTP_409_CONFLICT, 'BACKFILL_ALREADY_OPEN',
                         'A historical backfill is already pending or running for every selected target.')
        connection.commit()
        return {
            'created': [_backfill_payload(job) for job in created],
            'skipped': skipped,
            'requested_days': days,
            'message': 'Backfill queued. The external watchlist worker scans history in the background.',
        }


# ── events / findings ────────────────────────────────────────────────────────
def list_events(watchlist_id: str, request: Any, *, category: str | None = None, target_id: str | None = None,
                limit: Any = None, offset: Any = None) -> dict[str, Any]:
    pilot.require_live_mode()
    page_size, start = _page(limit, offset)
    wanted_category = str(category or '').strip().lower() or None
    categories = {spec.category for spec in abi.EVENT_SPECS}
    if wanted_category and wanted_category not in categories:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_CATEGORY', f'category must be one of {", ".join(sorted(categories))}.')
    with pilot.pg_connection() as connection:
        _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        target_filter = _uuid(target_id, 'Target') if target_id else None
        rows, total = service.list_events(connection, watchlist_id=str(watchlist['id']), limit=page_size, offset=start,
                                          category=wanted_category, target_id=target_filter)
        connection.commit()
        return {
            'events': [_event_payload(row) for row in rows],
            'pagination': _page_meta(total, page_size, start, len(rows)),
            'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        }


def list_findings(watchlist_id: str, request: Any, *, status_filter: str | None = None, severity: str | None = None,
                  limit: Any = None, offset: Any = None) -> dict[str, Any]:
    pilot.require_live_mode()
    page_size, start = _page(limit, offset)
    wanted_status = str(status_filter or '').strip().lower() or None
    if wanted_status and wanted_status not in ewc.FINDING_STATUSES:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_STATUS', f'status must be one of {", ".join(ewc.FINDING_STATUSES)}.')
    wanted_severity = str(severity or '').strip().lower() or None
    if wanted_severity and wanted_severity not in ewc.EXTERNAL_SEVERITIES:
        raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_SEVERITY', 'severity must be low, medium or high.')
    with pilot.pg_connection() as connection:
        _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        rows, total = service.list_findings(connection, watchlist_id=str(watchlist['id']), limit=page_size, offset=start,
                                            status=wanted_status, severity=wanted_severity)
        connection.commit()
        return {
            'findings': [_finding_payload(row) for row in rows],
            'pagination': _page_meta(total, page_size, start, len(rows)),
            'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        }


def _load_finding(connection: Any, watchlist: dict[str, Any], finding_id: str) -> dict[str, Any]:
    finding = service.get_finding(connection, watchlist_id=str(watchlist['id']), finding_id=_uuid(finding_id, 'Finding'))
    if finding is None:
        raise _error(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, 'Finding not found.')
    return finding


def _finding_events(connection: Any, watchlist: dict[str, Any], finding: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    if finding.get('tx_hash'):
        events = service.events_for_tx(connection, watchlist_id=str(watchlist['id']), tx_hash=str(finding['tx_hash']))
    if not events and finding.get('primary_event_id'):
        events = service.events_by_ids(connection, watchlist_id=str(watchlist['id']),
                                       event_ids=[str(finding['primary_event_id'])])
    return events


def get_finding(watchlist_id: str, finding_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        finding = _load_finding(connection, watchlist, finding_id)
        related = _finding_events(connection, watchlist, finding)
        packages, _total = service.list_evidence(connection, watchlist_id=str(watchlist['id']), limit=_MAX_PAGE, offset=0)
        own = [row for row in packages if str(row.get('finding_id') or '') == str(finding['id'])]
        latest_package = service.latest_evidence_package(connection, watchlist_id=str(watchlist['id']),
                                                         finding_id=str(finding['id']))
        integrity = evidence_builder.verify_package(latest_package) if latest_package else None
        connection.commit()
        return {
            'finding': _finding_payload(finding, detail=True),
            'protocol': {'id': str(watchlist['id']), 'name': watchlist.get('name'), 'website_url': watchlist.get('website_url')},
            'related_telemetry': [_event_payload(row) for row in related],
            'evidence': [_evidence_summary(row) for row in own],
            'evidence_integrity': integrity,
            'notices': _notices(),
        }


def update_finding(watchlist_id: str, finding_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        refuse_credentials(body)
        watchlist = _load_watchlist(connection, watchlist_id)
        finding = _load_finding(connection, watchlist, finding_id)
        new_status = str(body.get('status') or '').strip().lower()
        if new_status not in ewc.FINDING_STATUSES:
            raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_STATUS', f'status must be one of {", ".join(ewc.FINDING_STATUSES)}.')
        note = _clean_text(body.get('note'), field='note', limit=1000)
        _refuse_secret_text(note=note)
        updated = service.update_finding_status(connection, watchlist_id=str(watchlist['id']), finding_id=str(finding['id']),
                                                status=new_status, note=note, user_id=str(admin['id']))
        _audit(connection, request, admin=admin, action='finding_status_changed', entity_type='external_watchlist_finding',
               entity_id=finding['id'], metadata={'watchlist_id': str(watchlist['id']),
                                                  'previous_status': finding.get('status'), 'new_status': new_status,
                                                  'note_recorded': bool(note)})
        if new_status == 'outreach_candidate' and finding.get('status') != 'outreach_candidate':
            _audit(connection, request, admin=admin, action='outreach_candidate_marked',
                   entity_type='external_watchlist_finding', entity_id=finding['id'],
                   metadata={'watchlist_id': str(watchlist['id']), 'internal_only': True})
        connection.commit()
        merged = {**finding, **(updated or {})}
        return {'finding': _finding_payload(merged, detail=True)}


# ── evidence ─────────────────────────────────────────────────────────────────
def _generate_evidence(connection: Any, request: Any, *, admin: dict[str, Any], watchlist: dict[str, Any],
                       finding: dict[str, Any]) -> dict[str, Any]:
    target = service.get_target(connection, watchlist_id=str(watchlist['id']), target_id=str(finding['target_id'])) \
        if finding.get('target_id') else None
    events = _finding_events(connection, watchlist, finding)
    try:
        package = evidence_builder.build_evidence_package(
            watchlist=watchlist, target=target, finding=finding, events=events, generated_by=str(admin['id']),
        )
    except evidence_builder.EvidenceSealingUnavailable:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, evidence_builder.EvidenceSealingUnavailable.code,
                     'Evidence signing is not configured on this deployment, so no package was produced.') from None
    row = service.insert_evidence(connection, watchlist_id=str(watchlist['id']), finding_id=str(finding['id']),
                                  package=package, generated_by=str(admin['id']))
    _audit(connection, request, admin=admin, action='evidence_generated', entity_type='external_watchlist_evidence',
           entity_id=row['id'], metadata={'watchlist_id': str(watchlist['id']), 'finding_id': str(finding['id']),
                                          'evidence_sha256': row.get('evidence_sha256'),
                                          'manifest_sha256': row.get('manifest_sha256')})
    return row


def generate_evidence(watchlist_id: str, finding_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        finding = _load_finding(connection, watchlist, finding_id)
        row = _generate_evidence(connection, request, admin=admin, watchlist=watchlist, finding=finding)
        verification = evidence_builder.verify_package(row)
        connection.commit()
        return {'evidence': {**_evidence_summary(row, verification), 'files': row.get('files'),
                             'manifest': row.get('manifest')}}


def generate_prospect_report(watchlist_id: str, finding_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        finding = _load_finding(connection, watchlist, finding_id)
        package_row = service.latest_evidence_package(connection, watchlist_id=str(watchlist['id']),
                                                      finding_id=str(finding['id']))
        if package_row is None:
            package_row = _generate_evidence(connection, request, admin=admin, watchlist=watchlist, finding=finding)
        verification = evidence_builder.verify_package(package_row)
        try:
            report = evidence_builder.build_prospect_report(
                watchlist=watchlist, finding=finding, verification=verification, generated_by=None,
            )
        except evidence_builder.EvidenceSealingUnavailable:
            raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, evidence_builder.EvidenceSealingUnavailable.code,
                         'Evidence signing is not configured on this deployment, so no report was produced.') from None
        row = service.insert_evidence(connection, watchlist_id=str(watchlist['id']), finding_id=str(finding['id']),
                                      package=report, generated_by=str(admin['id']),
                                      source_evidence_id=str(package_row['id']))
        _audit(connection, request, admin=admin, action='prospect_report_generated',
               entity_type='external_watchlist_evidence', entity_id=row['id'],
               metadata={'watchlist_id': str(watchlist['id']), 'finding_id': str(finding['id']),
                         'source_evidence_id': str(package_row['id'])})
        connection.commit()
        return {
            'report': report['report'],
            'report_record': _evidence_summary(row, None),
            'source_evidence': _evidence_summary(package_row, verification),
        }


def list_evidence(watchlist_id: str, request: Any, *, limit: Any = None, offset: Any = None) -> dict[str, Any]:
    pilot.require_live_mode()
    page_size, start = _page(limit, offset)
    with pilot.pg_connection() as connection:
        _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        rows, total = service.list_evidence(connection, watchlist_id=str(watchlist['id']), limit=page_size, offset=start)
        connection.commit()
        return {
            'evidence': [_evidence_summary(row) for row in rows],
            'pagination': _page_meta(total, page_size, start, len(rows)),
            'disclaimer': ewc.EVIDENCE_DISCLAIMER,
            'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        }


def get_evidence(watchlist_id: str, evidence_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        _authorize(connection, request)
        watchlist = _load_watchlist(connection, watchlist_id)
        row = service.get_evidence(connection, watchlist_id=str(watchlist['id']), evidence_id=_uuid(evidence_id, 'Evidence'))
        if row is None:
            raise _error(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, 'Evidence not found.')
        verification = evidence_builder.verify_package(row)
        connection.commit()
        files = row.get('files') if isinstance(row.get('files'), dict) else {}
        return {
            'evidence': {**_evidence_summary(row, verification), 'files': files, 'manifest': row.get('manifest'),
                         'seal': {k: v for k, v in (row.get('seal') or {}).items() if k != 'signature'}},
            'report': files.get(evidence_builder.REPORT_FILE),
        }


# ── conversion ───────────────────────────────────────────────────────────────
def convert_to_pilot(watchlist_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        admin = _authorize(connection, request)
        refuse_credentials(body)
        if body.get('confirm') is not True:
            raise _error(status.HTTP_400_BAD_REQUEST, 'CONFIRMATION_REQUIRED',
                         'Converting creates a Pilot workspace. Send confirm: true to proceed.')
        watchlist = _load_watchlist(connection, watchlist_id, for_update=True)
        if watchlist.get('converted_workspace_id') or service.get_conversion(connection, str(watchlist['id'])):
            raise _error(status.HTTP_409_CONFLICT, 'ALREADY_CONVERTED',
                         'This protocol was already converted to a Pilot workspace.')
        targets = service.list_targets(connection, [str(watchlist['id'])])
        wanted = body.get('target_ids')
        if wanted is None:
            selected = targets
        else:
            if not isinstance(wanted, list):
                raise _error(status.HTTP_400_BAD_REQUEST, 'INVALID_TARGET_IDS', 'target_ids must be a list.')
            wanted_ids = {_uuid(item, 'Target') for item in wanted}
            unknown = wanted_ids - {str(t['id']) for t in targets}
            if unknown:
                raise _error(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, 'One or more targets were not found on this protocol.')
            selected = [t for t in targets if str(t['id']) in wanted_ids]
        workspace_name = _clean_text(body.get('workspace_name'), field='workspace_name', limit=120)
        _refuse_secret_text(workspace_name=workspace_name)
        result = conversion.convert(connection, watchlist=watchlist, targets=selected, founder=admin,
                                    request=request, workspace_name=workspace_name)
        _audit(connection, request, admin=admin, action='converted_to_pilot', entity_type='external_watchlist',
               entity_id=watchlist['id'], metadata={
                   'organization_id': result['organization']['id'], 'workspace_id': result['workspace']['id'],
                   'copied_target_count': len(result['copied_targets']),
                   'evaluation_days': result['evaluation_days'], 'customer_authorized': False,
                   'invitation_sent': False,
               })
        connection.commit()
        logger.info('external_watchlist_converted watchlist_id=%s workspace_id=%s', watchlist['id'], result['workspace']['id'])
        return {'conversion': result}


# ── execution boundary ───────────────────────────────────────────────────────
def refuse_execution(watchlist_id: str, capability: str, request: Any) -> dict[str, Any]:
    """The server-side answer to every execution-shaped request on an external
    target: 403, after authorization, with nothing read or written."""
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        org_service.require_internal_admin(connection, request)
    logger.warning('external_watchlist_execution_refused capability=%s', capability)
    raise _error(
        status.HTTP_403_FORBIDDEN, CODE_EXECUTION_FORBIDDEN,
        'External Watchlist targets have execution_authority = NONE. Decoda does not execute, sign, '
        'approve, pause, integrate with, or remediate anything on infrastructure it monitors publicly.',
        capability=capability,
    )
