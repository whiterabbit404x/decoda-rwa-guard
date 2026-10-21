"""Decoda product events (Decoda Assets, Decoda Vault) and the signals Guard sends back.

Intake: ``POST /api/integrations/decoda/events``

    X-Decoda-Producer:   decoda-assets | decoda-vault (selects the shared secret)
    X-Decoda-Timestamp:  unix seconds, within ±300 s
    X-Decoda-Signature:  v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
    X-Decoda-Event-Id:   the envelope's eventId; a replay is acknowledged, never re-processed

The answer drives the sender's retries: 202 accepted (or ``duplicate``), 400 malformed,
401 bad or stale signature, 413 too large, 422 unsupported or for an unknown tenant, 503
intake not configured or database unavailable (the sender retries).

Routing. The envelope's ``tenant.platformOrganizationId`` names the Guard organization
(``organizations.platform_organization_id``). An organization admin links each producer
to ONE workspace of the organization; events for an organization without a link are
kept as ``unrouted``: recorded, not detected, counted on Integrations.

Truthfulness. These are TESTNET product events, labelled as such on every alert and
incident they create (``evidence_source = 'product_event'``, plus ``synthetic`` when the
producer marks its data synthetic). They are never written to telemetry, detection or
monitoring tables and never count towards live monitoring health or reporting systems.

Signals back. Guard reports the state of what it created: alerts, incidents and
monitoring coverage to Decoda Assets; risk on the addresses an alert names, and
coverage, to Decoda Vault. ``refresh_signals`` derives the desired state from the
current alerts and incidents, whichever path changed them, and records a new version
of a signal only when its body changed; ``deliver_due`` sends pending versions, signed
with the same v1 scheme (producer ``decoda-rwa-guard``), with retries. Coverage is
reported only as Guard can stand behind it: contract or wallet monitoring is ``active``
only when an enabled Guard monitoring target exists for that address.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 256 * 1024
SIGNAL_PRODUCER = 'decoda-rwa-guard'
SIGNAL_TIMEOUT_SECONDS = 5.0
SIGNAL_LEASE_SECONDS = 60
SIGNAL_MAX_ATTEMPTS = 10
VAULT_COVERAGE_REFRESH_MINUTES = 10  # Vault shows coverage older than 30 minutes as stale
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 409, 410, 413, 415, 422})
SEVERITIES = ('info', 'low', 'medium', 'high', 'critical')
CLOSED_ALERT_STATUSES = frozenset({'resolved', 'false_positive', 'closed', 'dismissed'})
PRODUCT_EVENT_SOURCE = 'decoda_product_event'
PRODUCT_EVENT_LABEL = 'Testnet product event'

_EVENT_ID = re.compile(r'^[A-Za-z0-9._:-]{8,128}$')
_EVENT_TYPE = re.compile(r'^[a-z][a-z0-9_.]{2,119}$')
_ADDRESS = re.compile(r'^0x[0-9a-fA-F]{40}$')
_UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_TENANT_REF = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')


@dataclass(frozen=True)
class Producer:
    key: str
    label: str
    event_prefix: str
    tenant_key: str
    inbound_secret_env: str
    signals_url_env: str
    signals_secret_env: str


PRODUCERS: dict[str, Producer] = {
    'decoda-assets': Producer(
        key='decoda-assets',
        label='Decoda Assets',
        event_prefix='assets.',
        tenant_key='assetsOrgId',
        inbound_secret_env='DECODA_ASSETS_INBOUND_SECRET',
        signals_url_env='DECODA_ASSETS_SIGNALS_URL',
        signals_secret_env='DECODA_ASSETS_SIGNALS_SECRET',
    ),
    'decoda-vault': Producer(
        key='decoda-vault',
        label='Decoda Vault',
        event_prefix='vault.',
        tenant_key='vaultOrgId',
        inbound_secret_env='DECODA_VAULT_INBOUND_SECRET',
        signals_url_env='DECODA_VAULT_SIGNALS_URL',
        signals_secret_env='DECODA_VAULT_SIGNALS_SECRET',
    ),
}

# Decoda Assets names networks by its own keys; Vault (and its risk signals) by Vault's.
ASSETS_TO_VAULT_NETWORK = {'base_sepolia': 'base-sepolia', 'ethereum_sepolia': 'sepolia', 'polygon_amoy': 'polygon-amoy'}
VAULT_NETWORKS = frozenset(ASSETS_TO_VAULT_NETWORK.values())


class IntakeError(Exception):
    """A refusal with the HTTP status the sender acts on."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self) -> dict[str, Any]:
        return {'accepted': False, 'code': self.code, 'detail': self.message}


# ── Signing (the shared Decoda v1 scheme) ────────────────────────────────────


def signature(secret: str, body: bytes, timestamp: int) -> str:
    mac = hmac.new(secret.encode('utf-8'), f'{timestamp}.'.encode('ascii') + body, hashlib.sha256).hexdigest()
    return f'v1={mac}'


def signed_headers(secret: str, body: bytes, *, event_id: str, now: float | None = None) -> dict[str, str]:
    timestamp = int(now if now is not None else time.time())
    return {
        'Content-Type': 'application/json',
        'X-Decoda-Timestamp': str(timestamp),
        'X-Decoda-Signature': signature(secret, body, timestamp),
        'X-Decoda-Event-Id': event_id,
        'X-Decoda-Producer': SIGNAL_PRODUCER,
    }


def verify_signature(secret: str, raw: bytes, headers: dict[str, str], *, now: float | None = None) -> str:
    """Verify a v1-signed request and return its event id. Raises ``IntakeError``."""
    if not secret:
        raise IntakeError(503, 'INTAKE_NOT_CONFIGURED', 'This producer is not configured on this Guard deployment.')
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    raw_ts = lowered.get('x-decoda-timestamp', '')
    provided = lowered.get('x-decoda-signature', '')
    event_id = lowered.get('x-decoda-event-id', '')
    if not raw_ts.isdigit() or not provided or not _EVENT_ID.match(event_id):
        raise IntakeError(401, 'SIGNATURE_MISSING', 'X-Decoda-Timestamp, X-Decoda-Signature and X-Decoda-Event-Id are required.')
    current = int(now if now is not None else time.time())
    if abs(current - int(raw_ts)) > TOLERANCE_SECONDS:
        raise IntakeError(401, 'SIGNATURE_STALE', 'The request timestamp is outside the accepted window.')
    if not hmac.compare_digest(signature(secret, raw, int(raw_ts)), provided):
        raise IntakeError(401, 'SIGNATURE_INVALID', 'The request signature is invalid.')
    return event_id


# ── Envelope and detectors (pure) ────────────────────────────────────────────


@dataclass(frozen=True)
class Envelope:
    producer: Producer
    event_id: str
    event_type: str
    platform_organization_id: str
    product_tenant_ref: str | None
    severity_hint: str | None
    synthetic: bool
    occurred_at: str | None
    payload: dict[str, Any]
    raw: dict[str, Any]


def parse_envelope(raw: bytes, producer: Producer, event_id: str) -> Envelope:
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise IntakeError(400, 'MALFORMED_EVENT', 'The body is not valid JSON.') from None
    if not isinstance(body, dict):
        raise IntakeError(400, 'MALFORMED_EVENT', 'The body must be a JSON object.')
    if body.get('eventId') != event_id:
        raise IntakeError(400, 'EVENT_ID_MISMATCH', 'X-Decoda-Event-Id does not match the envelope eventId.')
    if body.get('producer') != producer.key:
        raise IntakeError(422, 'PRODUCER_MISMATCH', 'The envelope producer does not match X-Decoda-Producer.')
    event_type = body.get('eventType')
    if not isinstance(event_type, str) or not _EVENT_TYPE.match(event_type) or not event_type.startswith(producer.event_prefix):
        raise IntakeError(422, 'UNSUPPORTED_EVENT_TYPE', f'{producer.label} event types start with "{producer.event_prefix}".')
    if body.get('environment') != 'testnet':
        raise IntakeError(422, 'ENVIRONMENT_NOT_SUPPORTED', 'Guard accepts Decoda product events from testnet only.')
    tenant = body.get('tenant') if isinstance(body.get('tenant'), dict) else {}
    platform_org = tenant.get('platformOrganizationId')
    if not isinstance(platform_org, str) or not _UUID.match(platform_org):
        raise IntakeError(
            422,
            'TENANT_UNRESOLVED',
            'The envelope does not name a Decoda platform organization (tenant.platformOrganizationId).',
        )
    tenant_ref = tenant.get(producer.tenant_key)
    tenant_ref = str(tenant_ref) if tenant_ref is not None and _TENANT_REF.match(str(tenant_ref)) else None
    payload = body.get('payload') if isinstance(body.get('payload'), dict) else {}
    hint = payload.get('severity_hint')
    provenance = body.get('provenance') if isinstance(body.get('provenance'), dict) else {}
    occurred_at = body.get('occurredAt') if isinstance(body.get('occurredAt'), str) else None
    return Envelope(
        producer=producer,
        event_id=event_id,
        event_type=event_type,
        platform_organization_id=platform_org.lower(),
        product_tenant_ref=tenant_ref,
        severity_hint=hint if hint in SEVERITIES else None,
        synthetic=provenance.get('synthetic') is True,
        occurred_at=occurred_at,
        payload=payload,
        raw=body,
    )


@dataclass(frozen=True)
class Detection:
    """What one product event means to Guard."""

    kind: str  # 'alert' | 'coverage' | 'recorded'
    alert_type: str = ''
    severity: str = 'medium'
    title: str = ''
    summary: str = ''
    incident: bool = False
    subjects: tuple[tuple[str, str], ...] = ()  # (address, Vault network key) the alert is about
    recommendations: tuple[str, ...] = field(default_factory=tuple)


RECORDED = Detection(kind='recorded')


def _subject(address: Any, network: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(address, str) or not _ADDRESS.match(address):
        return ()
    vault_network = ASSETS_TO_VAULT_NETWORK.get(str(network), str(network))
    return ((address.lower(), vault_network),) if vault_network in VAULT_NETWORKS else ()


def _severity(value: Any, default: str) -> str:
    return value if value in SEVERITIES else default


def detect(envelope: Envelope) -> Detection:
    """The detector table (mirrors Decoda Assets' development stand-in for Guard)."""
    payload = envelope.payload
    kind = envelope.event_type
    if envelope.producer.key == 'decoda-assets':
        metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
        if kind == 'assets.ownership.mismatch':
            severity = _severity(envelope.severity_hint, 'high')
            title = str(metadata.get('headline') or 'Ownership reconciliation mismatch')
            if metadata.get('supply_difference'):
                title = f'{title}: on-chain supply differs by {metadata["supply_difference"]} units'
            return Detection(
                kind='alert',
                alert_type='supply_mismatch' if metadata.get('scope') == 'supply' else 'ownership_mismatch',
                severity=severity,
                title=title,
                summary=(
                    'Decoda Assets reconciled the issuer register against chain state and found a difference '
                    f'(expected supply {metadata.get("expected_supply")}, on-chain {metadata.get("onchain_supply")}).'
                ),
                incident=severity == 'critical',
                subjects=_subject(metadata.get('token_contract'), metadata.get('network')),
                recommendations=(
                    'Verify on-chain total supply against the issuer register and Vault-executed mints.',
                    'Identify the minting transaction and the address holding the unexplained units.',
                    'Review Decoda Vault mint authority usage and approvals for the token contract.',
                ),
            )
        if kind == 'assets.nav.deviation':
            return Detection(
                kind='alert',
                alert_type='nav_deviation',
                severity=_severity(envelope.severity_hint, 'medium'),
                title=f'NAV deviation {metadata.get("deviation_pct", "?")}%',
                summary=f'Implied NAV {metadata.get("implied_nav")} vs recorded {metadata.get("recorded_nav")}.',
            )
        if kind == 'assets.operation.blocked':
            decision = metadata.get('decision') if isinstance(metadata.get('decision'), dict) else {}
            return Detection(
                kind='alert',
                alert_type='blocked_operation',
                severity='medium',
                title=f'Blocked operation attempt: {decision.get("summary") or "Vault policy BLOCK"}',
                summary='Decoda Vault policy blocked an operation Decoda Assets requested.',
            )
        if kind == 'assets.settlement.failed':
            return Detection(
                kind='alert',
                alert_type='settlement_failed',
                severity='medium',
                title=f'Settlement failed: {metadata.get("reason") or "token leg did not complete"}',
                summary='A settlement recorded in Decoda Assets did not complete.',
            )
        if kind == 'assets.distribution.blocked' and metadata.get('blocked_before_vault'):
            return Detection(
                kind='alert',
                alert_type='eligibility_block',
                severity='low',
                title='Allocation to an ineligible investor was blocked before Vault',
                summary='Decoda Assets refused an allocation to an investor who is not eligible.',
            )
        if kind == 'assets.tokenization.completed':
            return Detection(kind='coverage')
        return RECORDED
    network = payload.get('network')
    if kind == 'vault.transaction.abnormal_transfer':
        return Detection(
            kind='alert',
            alert_type='abnormal_transfer',
            severity='high',
            title=f'Abnormal transfer from {payload.get("wallet_name") or "a Vault wallet"}',
            summary=str(payload.get('detail') or "A transfer deviates from the wallet's normal pattern."),
            subjects=_subject(payload.get('destination'), network),
        )
    if kind == 'vault.approval.repeated_failures':
        return Detection(
            kind='alert',
            alert_type='repeated_approval_failures',
            severity='high',
            title=f'Repeated approval rejections for {payload.get("wallet_name") or "a Vault wallet"}',
            summary=f'{payload.get("rejections_24h", "Several")} approvals were rejected in 24 hours.',
            subjects=_subject(payload.get('wallet'), network),
        )
    if kind == 'vault.transaction.blocked':
        reasons = payload.get('reasons') if isinstance(payload.get('reasons'), list) else []
        first = reasons[0] if reasons and isinstance(reasons[0], dict) else {}
        return Detection(
            kind='alert',
            alert_type='policy_block',
            severity='medium',
            title=f'Vault policy blocked a transaction: {first.get("message") or first.get("code") or "BLOCK"}',
            summary='The Decoda Vault policy engine blocked a transaction.',
        )
    return RECORDED


# ── Configuration ────────────────────────────────────────────────────────────


def inbound_secret(producer: Producer) -> str:
    return os.getenv(producer.inbound_secret_env, '').strip()


def _local_host(host: str | None) -> bool:
    return (host or '').lower() in {'localhost', '127.0.0.1', '::1'}


def signals_endpoint(producer: Producer) -> tuple[str, str] | None:
    """(url, secret) to report to ``producer``, or None when not configured (or not https)."""
    url = os.getenv(producer.signals_url_env, '').strip()
    secret = os.getenv(producer.signals_secret_env, '').strip()
    if not url or not secret:
        return None
    parsed = urlparse(url)
    if parsed.scheme == 'https' or (parsed.scheme == 'http' and _local_host(parsed.hostname)):
        return url, secret
    return None


# ── Intake ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntakeResult:
    status: int
    body: dict[str, Any]
    workspace_id: str | None = None  # where to refresh signals after the response


def _alert_id(workspace_id: str, envelope: Envelope) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'decoda-product-alert:{workspace_id}:{envelope.producer.key}:{envelope.event_id}'))


def _evidence_payload(envelope: Envelope) -> dict[str, Any]:
    return {
        'source': PRODUCT_EVENT_SOURCE,
        'evidence_source': 'product_event',
        'evidence_label': PRODUCT_EVENT_LABEL,
        'environment': 'testnet',
        'synthetic': envelope.synthetic,
        'producer': envelope.producer.key,
        'event_id': envelope.event_id,
        'event_type': envelope.event_type,
        'product_tenant_ref': envelope.product_tenant_ref,
        'asset_id': envelope.payload.get('asset_id') if envelope.producer.key == 'decoda-assets' else None,
    }


def _record_alert(connection: Any, *, workspace_id: str, user_id: str, envelope: Envelope, detection: Detection) -> str:
    from services.api.app import pilot

    alert_id = _alert_id(workspace_id, envelope)
    payload = {
        **_evidence_payload(envelope),
        'subjects': [{'address': address, 'network': network} for address, network in detection.subjects],
        'dedupe_signature': f'{envelope.producer.key}:{envelope.event_id}',
    }
    title = f'{envelope.producer.label} ({PRODUCT_EVENT_LABEL.lower()}): {detection.title}'[:300]
    summary = detection.summary or detection.title
    if envelope.synthetic:
        summary = f'{summary} The producer marks this data as synthetic.'
    connection.execute(
        """
        INSERT INTO alerts (
            id, workspace_id, user_id, analysis_run_id, alert_type, title, severity, status, source_service,
            summary, payload, created_at, module_key, source, dedupe_signature, occurrence_count, first_seen_at, last_seen_at, updated_at
        ) VALUES (
            %s, %s, %s, NULL, %s, %s, %s, 'open', 'decoda-product-events',
            %s, %s::jsonb, NOW(), 'decoda_products', %s, %s, 1, NOW(), NOW(), NOW()
        )
        ON CONFLICT (id) DO NOTHING
        """,
        (
            alert_id,
            workspace_id,
            user_id,
            detection.alert_type,
            title,
            detection.severity,
            summary[:2000],
            pilot._json_dumps(payload),
            PRODUCT_EVENT_SOURCE,
            payload['dedupe_signature'],
        ),
    )
    return alert_id


def _record_incident(
    connection: Any, *, workspace_id: str, user_id: str, envelope: Envelope, detection: Detection, alert_id: str
) -> str | None:
    from services.api.app import pilot

    dedup_key = f'decoda|{envelope.producer.key}|{envelope.event_id}'
    payload = {
        **_evidence_payload(envelope),
        'alert_id': alert_id,
        'evidence': [
            {
                'type': PRODUCT_EVENT_SOURCE,
                'label': PRODUCT_EVENT_LABEL,
                'synthetic': envelope.synthetic,
                'producer': envelope.producer.key,
                'event_id': envelope.event_id,
                'event_type': envelope.event_type,
                'occurred_at': envelope.occurred_at,
                'payload': envelope.payload,
            }
        ],
        'recommendations': list(detection.recommendations),
    }
    title = f'Investigation: {envelope.producer.label} ({PRODUCT_EVENT_LABEL.lower()}) — {detection.title}'[:300]
    inserted = connection.execute(
        """
        INSERT INTO incidents (
            id, workspace_id, user_id, analysis_run_id, event_type, title, severity, status, workflow_status,
            summary, target_id, source_alert_id, linked_alert_ids, dedup_key, payload, created_at, updated_at
        ) VALUES (
            %s, %s, %s, NULL, %s, %s, %s, 'open', 'open',
            %s, NULL, %s::uuid, %s::jsonb, %s, %s::jsonb, NOW(), NOW()
        )
        ON CONFLICT (workspace_id, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
        RETURNING id
        """,
        (
            str(uuid.uuid4()),
            workspace_id,
            user_id,
            detection.alert_type or 'decoda_product_event',
            title,
            detection.severity,
            (detection.summary or detection.title)[:2000],
            alert_id,
            pilot._json_dumps([alert_id]),
            dedup_key,
            pilot._json_dumps(payload),
        ),
    ).fetchone()
    if inserted is None:
        existing = connection.execute(
            'SELECT id FROM incidents WHERE workspace_id = %s AND dedup_key = %s', (workspace_id, dedup_key)
        ).fetchone()
        return str(existing['id']) if existing else None
    incident_id = str(inserted['id'])
    connection.execute(
        'UPDATE alerts SET incident_id = %s::uuid, updated_at = NOW() WHERE id = %s::uuid AND workspace_id = %s AND incident_id IS NULL',
        (incident_id, alert_id, workspace_id),
    )
    try:
        from services.api.app import forensic_investigation

        forensic_investigation.assign_incident_reference(connection, workspace_id=workspace_id, incident_id=incident_id, now=None)
    except Exception:  # pragma: no cover - a reference must never block the incident
        logger.warning('event=decoda_product_incident_reference_failed incident_id=%s', incident_id)
    pilot.append_incident_timeline_event(
        connection,
        workspace_id=workspace_id,
        incident_id=incident_id,
        event_type='incident.auto_created',
        message=f'Incident opened automatically from a critical {envelope.producer.label} testnet product event ({envelope.event_type}).',
        actor_user_id=None,
        metadata={'alert_id': alert_id, 'producer': envelope.producer.key, 'event_id': envelope.event_id},
    )
    return incident_id


def _organization_for(connection: Any, platform_organization_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        'SELECT id, platform_organization_id FROM organizations WHERE platform_organization_id = %s::uuid',
        (platform_organization_id,),
    ).fetchone()
    return dict(row) if row else None


def _link_for(connection: Any, organization_id: str, producer: str) -> dict[str, Any] | None:
    """The workspace this producer is routed to — only a workspace that still belongs to the organization."""
    row = connection.execute(
        """
        SELECT l.workspace_id, l.created_by_user_id
        FROM decoda_product_links l
        JOIN workspaces w ON w.id = l.workspace_id AND w.organization_id = l.organization_id
        WHERE l.organization_id = %s AND l.producer = %s
        """,
        (organization_id, producer),
    ).fetchone()
    return dict(row) if row else None


def process_event(raw: bytes, headers: dict[str, str], *, connect: Callable[[], Any] | None = None) -> IntakeResult:
    """Verify, record, route and detect one product event. Never raises: refusals are results."""
    try:
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
        producer = PRODUCERS.get(lowered.get('x-decoda-producer', ''))
        if producer is None:
            raise IntakeError(422, 'UNKNOWN_PRODUCER', 'X-Decoda-Producer must be decoda-assets or decoda-vault.')
        if len(raw) > MAX_BODY_BYTES:
            raise IntakeError(413, 'EVENT_TOO_LARGE', f'Events are limited to {MAX_BODY_BYTES} bytes.')
        event_id = verify_signature(inbound_secret(producer), raw, headers)
        envelope = parse_envelope(raw, producer, event_id)
    except IntakeError as exc:
        if exc.status in (401, 503):
            logger.warning('event=decoda_product_intake_refused code=%s', exc.code)
        return IntakeResult(exc.status, exc.body())
    try:
        return _record(envelope, connect=connect)
    except IntakeError as exc:
        return IntakeResult(exc.status, exc.body())
    except Exception as exc:  # database unavailable or schema missing: the sender retries
        logger.warning('event=decoda_product_intake_unavailable error=%s', type(exc).__name__)
        return IntakeResult(
            503,
            {'accepted': False, 'code': 'INTAKE_UNAVAILABLE', 'detail': 'Guard could not record the event; retry later.'},
        )


def _record(envelope: Envelope, *, connect: Callable[[], Any] | None) -> IntakeResult:
    from services.api.app import pilot

    with (connect or pilot.pg_connection)() as connection:
        organization = _organization_for(connection, envelope.platform_organization_id)
        if organization is None:
            raise IntakeError(422, 'TENANT_NOT_FOUND', 'No Guard organization is linked to this Decoda platform organization.')
        organization_id = str(organization['id'])
        link = _link_for(connection, organization_id, envelope.producer.key)
        workspace_id = str(link['workspace_id']) if link else None
        row_id = str(uuid.uuid4())
        inserted = connection.execute(
            """
            INSERT INTO decoda_product_events (
                id, producer, event_id, event_type, organization_id, workspace_id, routing_status, product_tenant_ref,
                severity_hint, synthetic, envelope, detection, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::timestamptz)
            ON CONFLICT (producer, event_id) DO NOTHING
            RETURNING id
            """,
            (
                row_id,
                envelope.producer.key,
                envelope.event_id,
                envelope.event_type,
                organization_id,
                workspace_id,
                'routed' if workspace_id else 'unrouted',
                envelope.product_tenant_ref,
                envelope.severity_hint,
                envelope.synthetic,
                pilot._json_dumps(envelope.raw),
                'none',
                _timestamp_or_none(envelope.occurred_at),
            ),
        ).fetchone()
        if inserted is None:
            return IntakeResult(202, {'accepted': True, 'duplicate': True})
        if workspace_id is None:
            connection.commit()
            return IntakeResult(202, {'accepted': True, 'duplicate': False, 'routed': False, 'detection': 'none'})
        detection = detect(envelope)
        alert_id = incident_id = None
        if detection.kind == 'alert':
            user_id = str(link['created_by_user_id'])
            alert_id = _record_alert(connection, workspace_id=workspace_id, user_id=user_id, envelope=envelope, detection=detection)
            if detection.incident:
                incident_id = _record_incident(
                    connection, workspace_id=workspace_id, user_id=user_id, envelope=envelope, detection=detection, alert_id=alert_id
                )
        outcome = (
            'alert_and_incident' if incident_id else 'alert' if alert_id else 'coverage' if detection.kind == 'coverage' else 'recorded'
        )
        connection.execute(
            'UPDATE decoda_product_events SET detection = %s, alert_id = %s, incident_id = %s WHERE id = %s',
            (outcome, alert_id, incident_id, row_id),
        )
        connection.commit()
    return IntakeResult(202, {'accepted': True, 'duplicate': False, 'routed': True, 'detection': outcome}, workspace_id=workspace_id)


def _timestamp_or_none(value: str | None) -> str | None:
    if not value or not re.match(r'^\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:?\d{2})?$', value):
        return None
    return value


# ── Signals back to the products ─────────────────────────────────────────────


def _external_status(status: str) -> str:
    value = (status or 'open').strip().lower()
    if value == 'false_positive':
        return 'dismissed'
    return value if re.match(r'^[a-z_]{1,32}$', value) else 'open'


def assets_alert_signal(alert: dict[str, Any]) -> dict[str, Any]:
    payload = alert['payload'] or {}
    incident_id = str(alert['incident_id']) if alert.get('incident_id') else None
    return {
        'kind': 'alert',
        'organization_id': payload.get('product_tenant_ref'),
        'asset_id': payload.get('asset_id'),
        'external_id': f'guard-alert-{alert["id"]}',
        'severity': _severity(alert['severity'], 'medium'),
        'status': _external_status(alert['status']),
        'title': str(alert['title'])[:200],
        'detail': {
            'alert_type': alert.get('alert_type'),
            'evidence_source': 'product_event',
            'synthetic': bool(payload.get('synthetic')),
            'incident_id': f'guard-incident-{incident_id}' if incident_id else None,
        },
        'path': f'/incidents/{incident_id}' if incident_id else '/alerts',
        'related_event_id': payload.get('event_id'),
    }


def assets_incident_signal(incident: dict[str, Any], alert: dict[str, Any]) -> dict[str, Any]:
    payload = alert['payload'] or {}
    status = incident.get('workflow_status') or incident.get('status') or 'open'
    return {
        'kind': 'incident',
        'organization_id': payload.get('product_tenant_ref'),
        'asset_id': payload.get('asset_id'),
        'external_id': f'guard-incident-{incident["id"]}',
        'severity': _severity(incident['severity'], 'high'),
        'status': _external_status(status),
        'title': str(incident.get('title') or 'Investigation')[:200],
        'detail': {'alert_id': f'guard-alert-{alert["id"]}', 'reference': incident.get('reference'), 'evidence_source': 'product_event'},
        'path': f'/incidents/{incident["id"]}',
        'related_event_id': payload.get('event_id'),
    }


def _all_monitored(addresses: list[str], monitored: set[str]) -> str:
    wanted = {a.lower() for a in addresses if isinstance(a, str) and _ADDRESS.match(a)}
    return 'active' if wanted and wanted <= monitored else 'not_configured'


def assets_coverage_signals(
    organization_ref: str, completed: list[dict[str, Any]], *, monitored_contracts: set[str], monitored_wallets: set[str]
) -> list[dict[str, Any]]:
    """Coverage for each tokenized asset, and for the organization: only what Guard actually monitors is active."""
    signals: list[dict[str, Any]] = []
    per_asset: list[dict[str, str]] = []
    for event in completed:
        payload = event.get('payload') or {}
        metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
        asset_id = payload.get('asset_id')
        if not asset_id:
            continue
        statuses = {
            'asset_monitoring': 'active',
            'contract_monitoring': _all_monitored([metadata.get('token_contract')], monitored_contracts),
            'wallet_monitoring': _all_monitored(
                [metadata.get('mint_authority'), metadata.get('issuer_treasury'), metadata.get('burn_authority')], monitored_wallets
            ),
            'incident_detection': 'active',
            'evidence': 'active',
        }
        per_asset.append(statuses)
        signals.append(
            {
                'kind': 'coverage',
                'organization_id': organization_ref,
                'asset_id': asset_id,
                'external_id': f'guard-asset-coverage-{asset_id}',
                'severity': None,
                'status': 'monitoring',
                'title': 'Decoda RWA Guard monitoring coverage',
                'detail': {**statuses, 'evidence_source': 'product_event'},
                'path': '/integrations',
                'related_event_id': event.get('event_id'),
            }
        )
    organization = {
        'asset_monitoring': 'active',
        'incident_detection': 'active',
        'evidence': 'active',
        'contract_monitoring': 'active'
        if per_asset and all(s['contract_monitoring'] == 'active' for s in per_asset)
        else 'not_configured',
        'wallet_monitoring': 'active' if per_asset and all(s['wallet_monitoring'] == 'active' for s in per_asset) else 'not_configured',
    }
    signals.append(
        {
            'kind': 'coverage',
            'organization_id': organization_ref,
            'asset_id': None,
            'external_id': 'guard-org-coverage',
            'severity': None,
            'status': 'monitoring',
            'title': 'Decoda RWA Guard monitoring coverage',
            'detail': {**organization, 'evidence_source': 'product_event'},
            'path': '/integrations',
            'related_event_id': None,
        }
    )
    return signals


def vault_risk_signals(alert: dict[str, Any], platform_organization_id: str) -> list[dict[str, Any]]:
    """An open critical alert makes its subjects critical in Vault (Vault blocks); high, elevated; closed, normal."""
    payload = alert['payload'] or {}
    severity = alert['severity']
    if severity not in ('critical', 'high'):
        return []
    open_ = str(alert['status'] or 'open').lower() not in CLOSED_ALERT_STATUSES
    risk = ('critical' if severity == 'critical' else 'elevated') if open_ else 'normal'
    signals = []
    for subject in payload.get('subjects') or []:
        address, network = subject.get('address'), subject.get('network')
        if not isinstance(address, str) or not _ADDRESS.match(address) or network not in VAULT_NETWORKS:
            continue
        signals.append(
            {
                'kind': 'risk',
                'platform_organization_id': platform_organization_id,
                'network': network,
                'subject_address': address.lower(),
                'risk_state': risk,
                'reason': str(alert['title'])[:300] if open_ else f'Resolved in Decoda RWA Guard: {str(alert["title"])[:260]}',
                'external_id': f'guard-alert-{alert["id"]}-{network}-{address.lower()}'[:128],
                'related_event_id': payload.get('event_id'),
            }
        )
    return signals


def vault_coverage_signal(
    platform_organization_id: str, *, wallets: set[str], contracts: set[str], monitored_wallets: set[str], monitored_contracts: set[str]
) -> dict[str, Any]:
    def status(seen: set[str], monitored: set[str]) -> str:
        if not seen:
            return 'not_connected'
        return 'active' if seen <= monitored else 'inactive'

    return {
        'kind': 'coverage',
        'platform_organization_id': platform_organization_id,
        'external_id': 'guard-org-coverage',
        'contract_monitoring': status(contracts, monitored_contracts),
        'wallet_monitoring': status(wallets, monitored_wallets),
        'policy_monitoring': 'active',
        'incident_detection': 'active',
    }


def _body_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def _upsert_signal(connection: Any, workspace_id: str, destination: str, body: dict[str, Any]) -> None:
    from services.api.app import pilot

    connection.execute(
        """
        INSERT INTO decoda_product_signals (id, workspace_id, destination, kind, external_id, body, body_hash, status, related_event_id)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, 'pending', %s)
        ON CONFLICT (workspace_id, destination, kind, external_id) DO UPDATE SET
            version = decoda_product_signals.version + 1, body = EXCLUDED.body, body_hash = EXCLUDED.body_hash,
            status = 'pending', attempts = 0, next_attempt_at = NOW(), last_error = NULL, last_http_status = NULL,
            related_event_id = COALESCE(EXCLUDED.related_event_id, decoda_product_signals.related_event_id), updated_at = NOW()
        WHERE decoda_product_signals.body_hash <> EXCLUDED.body_hash
        """,
        (
            str(uuid.uuid4()),
            workspace_id,
            destination,
            body['kind'],
            body['external_id'],
            pilot._json_dumps(body),
            _body_hash(body),
            body.get('related_event_id'),
        ),
    )


def _monitored(connection: Any, workspace_id: str) -> tuple[set[str], set[str]]:
    """Addresses this workspace has an enabled Guard monitoring target for: (contracts, wallets)."""
    rows = connection.execute(
        """
        SELECT lower(contract_identifier) AS contract, lower(wallet_address) AS wallet FROM targets
        WHERE workspace_id = %s AND enabled AND deleted_at IS NULL
        """,
        (workspace_id,),
    ).fetchall()
    return {r['contract'] for r in rows if r['contract']}, {r['wallet'] for r in rows if r['wallet']}


def refresh_signals(connection: Any, workspace_id: str) -> None:
    """Record a new signal version for every reported object whose state changed (in the caller's transaction)."""
    organization = _organization_of_workspace(connection, workspace_id)
    if organization is None:
        return
    platform_org = str(organization['platform_organization_id']) if organization['platform_organization_id'] else None
    # Which products this ORGANIZATION connected (its consent to receive Guard's signals), and
    # which of them are routed to THIS workspace (coverage is reported from that workspace).
    links = connection.execute(
        """
        SELECT l.producer, l.workspace_id FROM decoda_product_links l
        JOIN workspaces w ON w.id = l.workspace_id AND w.organization_id = l.organization_id
        WHERE l.organization_id = %s
        """,
        (organization['id'],),
    ).fetchall()
    connected = {r['producer'] for r in links}
    routed_here = {r['producer'] for r in links if str(r['workspace_id']) == workspace_id}
    if not connected:
        return
    monitored_contracts, monitored_wallets = _monitored(connection, workspace_id)
    alerts = connection.execute(
        """
        SELECT id, alert_type, title, severity, status, incident_id, payload FROM alerts
        WHERE workspace_id = %s AND source = %s
          AND (status NOT IN ('resolved', 'false_positive', 'closed', 'dismissed') OR updated_at > NOW() - INTERVAL '30 days')
        ORDER BY created_at DESC LIMIT 500
        """,
        (workspace_id, PRODUCT_EVENT_SOURCE),
    ).fetchall()
    incident_ids = [str(a['incident_id']) for a in alerts if a['incident_id']]
    incidents = {}
    if incident_ids:
        incidents = {
            str(r['id']): dict(r)
            for r in connection.execute(
                'SELECT id, title, severity, status, workflow_status, reference FROM incidents WHERE workspace_id = %s AND id = ANY(%s::uuid[])',
                (workspace_id, incident_ids),
            ).fetchall()
        }
    for row in alerts:
        alert = dict(row)
        payload = alert['payload'] or {}
        if payload.get('producer') == 'decoda-assets' and 'decoda-assets' in connected and payload.get('product_tenant_ref'):
            _upsert_signal(connection, workspace_id, 'decoda-assets', assets_alert_signal(alert))
            incident = incidents.get(str(alert['incident_id'])) if alert['incident_id'] else None
            if incident:
                _upsert_signal(connection, workspace_id, 'decoda-assets', assets_incident_signal(incident, alert))
        # Vault applies Guard's risk only for an organization that connected Vault to Guard.
        if 'decoda-vault' in connected and platform_org:
            for body in vault_risk_signals(alert, platform_org):
                _upsert_signal(connection, workspace_id, 'decoda-vault', body)
    if 'decoda-assets' in routed_here:
        completed = connection.execute(
            """
            SELECT event_id, product_tenant_ref, envelope->'payload' AS payload FROM decoda_product_events
            WHERE workspace_id = %s AND producer = 'decoda-assets' AND event_type = 'assets.tokenization.completed'
              AND product_tenant_ref IS NOT NULL
            ORDER BY received_at DESC LIMIT 200
            """,
            (workspace_id,),
        ).fetchall()
        by_org: dict[str, list[dict[str, Any]]] = {}
        for event in completed:
            by_org.setdefault(str(event['product_tenant_ref']), []).append(dict(event))
        for organization_ref, events in by_org.items():
            for body in assets_coverage_signals(
                organization_ref, events, monitored_contracts=monitored_contracts, monitored_wallets=monitored_wallets
            ):
                _upsert_signal(connection, workspace_id, 'decoda-assets', body)
    if 'decoda-vault' in routed_here and platform_org:
        seen = connection.execute(
            """
            SELECT DISTINCT lower(envelope->'payload'->>'wallet') AS wallet,
                   CASE WHEN envelope->'payload'->>'tx_type' <> 'transfer' THEN lower(envelope->'payload'->>'destination') END AS contract
            FROM decoda_product_events
            WHERE workspace_id = %s AND producer = 'decoda-vault' AND received_at > NOW() - INTERVAL '30 days'
            """,
            (workspace_id,),
        ).fetchall()
        wallets = {r['wallet'] for r in seen if r['wallet'] and _ADDRESS.match(r['wallet'])}
        contracts = {r['contract'] for r in seen if r['contract'] and _ADDRESS.match(r['contract'])}
        _upsert_signal(
            connection,
            workspace_id,
            'decoda-vault',
            vault_coverage_signal(
                platform_org,
                wallets=wallets,
                contracts=contracts,
                monitored_wallets=monitored_wallets,
                monitored_contracts=monitored_contracts,
            ),
        )
        # Vault shows a report older than 30 minutes as stale: report coverage again before then.
        connection.execute(
            """
            UPDATE decoda_product_signals SET version = version + 1, status = 'pending', attempts = 0, next_attempt_at = NOW(),
                updated_at = NOW()
            WHERE workspace_id = %s AND destination = 'decoda-vault' AND kind = 'coverage' AND status = 'delivered'
              AND delivered_at < NOW() - make_interval(mins => %s)
            """,
            (workspace_id, VAULT_COVERAGE_REFRESH_MINUTES),
        )


def signal_event_id(signal_id: str, version: int) -> str:
    return 'grd_' + hashlib.sha256(f'{signal_id}:{version}'.encode()).hexdigest()[:40]


def _owner() -> str:
    return f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}'[:120]


def _claim(connection: Any, limit: int, owner: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH due AS (
            SELECT id FROM decoda_product_signals
            WHERE status IN ('pending', 'failed', 'not_configured') AND next_attempt_at <= NOW()
              AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
            ORDER BY next_attempt_at LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE decoda_product_signals s
        SET lease_owner = %s, lease_expires_at = NOW() + make_interval(secs => %s)
        FROM due WHERE s.id = due.id
        RETURNING s.id, s.destination, s.version, s.body, s.body_hash, s.attempts
        """,
        (limit, owner, SIGNAL_LEASE_SECONDS),
    ).fetchall()
    connection.commit()
    return [dict(r) for r in rows]


def _settle(connection: Any, row: dict[str, Any], owner: str, **fields: Any) -> None:
    assignments = ', '.join(f'{key} = %({key})s' for key in fields)
    connection.execute(
        f"""
        UPDATE decoda_product_signals SET {assignments}, lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
        WHERE id = %(id)s AND lease_owner = %(owner)s
        """,
        {**fields, 'id': row['id'], 'owner': owner},
    )
    connection.commit()


def _backoff_seconds(attempts: int) -> int:
    return min(3600, 30 * (2 ** max(0, attempts - 1)))


def deliver_due(
    *, limit: int = 50, connect: Callable[[], Any] | None = None, client: Any = None, owner: str | None = None
) -> dict[str, int]:
    """Send the pending signal versions. Each row is claimed with a short lease, so replicas never send one twice at once."""
    from services.api.app import pilot

    owner = owner or _owner()
    stats = {'delivered': 0, 'failed': 0, 'dead': 0, 'not_configured': 0}
    own_client = client is None
    if own_client:
        import httpx

        client = httpx.Client(timeout=SIGNAL_TIMEOUT_SECONDS)
    try:
        with (connect or pilot.pg_connection)() as connection:
            for row in _claim(connection, limit, owner):
                producer = PRODUCERS[row['destination']]
                endpoint = signals_endpoint(producer)
                if endpoint is None:
                    _settle(
                        connection,
                        row,
                        owner,
                        status='not_configured',
                        next_attempt_at=_in_seconds(connection, 300),
                        last_error=f'{producer.signals_url_env} / {producer.signals_secret_env} not configured (https required).',
                    )
                    stats['not_configured'] += 1
                    continue
                url, secret = endpoint
                body = json.dumps(row['body'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
                headers = signed_headers(secret, body, event_id=signal_event_id(str(row['id']), int(row['version'])))
                attempts = int(row['attempts']) + 1
                try:
                    response = client.post(url, content=body, headers=headers)
                    code = int(response.status_code)
                    error = None if 200 <= code < 300 else f'HTTP {code}'
                except Exception as exc:  # transport failure: retried
                    code, error = None, f'transport: {type(exc).__name__}'
                if code is not None and 200 <= code < 300:
                    # Delivered this version. If the state changed meanwhile, the new version stays pending.
                    connection.execute(
                        """
                        UPDATE decoda_product_signals SET delivered_hash = %s, delivered_at = NOW(), attempts = 0,
                            last_error = NULL, last_http_status = %s,
                            status = CASE WHEN version = %s THEN 'delivered' ELSE 'pending' END,
                            lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
                        WHERE id = %s AND lease_owner = %s
                        """,
                        (row['body_hash'], code, row['version'], row['id'], owner),
                    )
                    connection.commit()
                    stats['delivered'] += 1
                elif code in PERMANENT_STATUSES or attempts >= SIGNAL_MAX_ATTEMPTS:
                    _settle(connection, row, owner, status='dead', attempts=attempts, last_error=error, last_http_status=code)
                    stats['dead'] += 1
                else:
                    _settle(
                        connection,
                        row,
                        owner,
                        status='failed',
                        attempts=attempts,
                        last_error=error,
                        last_http_status=code,
                        next_attempt_at=_in_seconds(connection, _backoff_seconds(attempts)),
                    )
                    stats['failed'] += 1
    finally:
        if own_client:
            client.close()
    return stats


def _in_seconds(connection: Any, seconds: int) -> Any:
    return connection.execute('SELECT NOW() + make_interval(secs => %s) AS at', (seconds,)).fetchone()['at']


def drain(workspace_id: str | None = None, *, connect: Callable[[], Any] | None = None, client: Any = None) -> dict[str, Any]:
    """Refresh signals (one workspace, or every linked one) and deliver what is due."""
    from services.api.app import pilot

    with (connect or pilot.pg_connection)() as connection:
        if workspace_id:
            workspaces = [workspace_id]
        else:
            workspaces = [str(r['workspace_id']) for r in connection.execute('SELECT DISTINCT workspace_id FROM decoda_product_links').fetchall()]
        for ws in workspaces:
            refresh_signals(connection, ws)
        connection.commit()
    return {'workspaces': len(workspaces), **deliver_due(connect=connect, client=client)}


def drain_safely(workspace_id: str | None = None) -> None:
    """For background tasks and job runners: never raises."""
    try:
        drain(workspace_id)
    except Exception as exc:
        logger.warning('event=decoda_product_signal_drain_failed error=%s', type(exc).__name__)


# ── Integrations page: status and links (workspace-scoped) ───────────────────


def _org_admin(connection: Any, organization_id: str, user_id: str) -> bool:
    from services.api.app import organizations

    return organizations.membership_role(connection, organization_id=organization_id, user_id=user_id) in ('owner', 'admin')


def _organization_of_workspace(connection: Any, workspace_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT o.id, o.name, o.platform_organization_id FROM workspaces w JOIN organizations o ON o.id = w.organization_id
        WHERE w.id = %s
        """,
        (workspace_id,),
    ).fetchone()
    return dict(row) if row else None


def status_view(connection: Any, *, workspace_id: str, user_id: str, workspace_admin: bool) -> dict[str, Any]:
    """What this workspace's members see on Integrations. Unrouted counts are the workspace's organization's."""
    organization = _organization_of_workspace(connection, workspace_id)
    can_manage = bool(organization) and workspace_admin and _org_admin(connection, str(organization['id']), user_id)
    products = []
    for producer in PRODUCERS.values():
        link = None
        if organization:
            row = connection.execute(
                """
                SELECT l.workspace_id, l.updated_at, w.name AS workspace_name FROM decoda_product_links l
                JOIN workspaces w ON w.id = l.workspace_id AND w.organization_id = l.organization_id
                WHERE l.organization_id = %s AND l.producer = %s
                """,
                (organization['id'], producer.key),
            ).fetchone()
            link = dict(row) if row else None
        here = bool(link) and str(link['workspace_id']) == workspace_id
        events = connection.execute(
            """
            SELECT count(*) AS received, max(received_at) AS last_received_at,
                   count(*) FILTER (WHERE detection IN ('alert', 'alert_and_incident')) AS alerts
            FROM decoda_product_events WHERE workspace_id = %s AND producer = %s
            """,
            (workspace_id, producer.key),
        ).fetchone()
        unrouted = 0
        if organization:
            unrouted = int(
                connection.execute(
                    "SELECT count(*) AS n FROM decoda_product_events WHERE organization_id = %s AND producer = %s AND routing_status = 'unrouted'",
                    (organization['id'], producer.key),
                ).fetchone()['n']
            )
        signals = {
            r['status']: int(r['n'])
            for r in connection.execute(
                'SELECT status, count(*) AS n FROM decoda_product_signals WHERE workspace_id = %s AND destination = %s GROUP BY status',
                (workspace_id, producer.key),
            ).fetchall()
        }
        if not inbound_secret(producer):
            state = 'not_configured'
        elif not organization or not organization.get('platform_organization_id'):
            state = 'no_platform_organization'
        elif not link:
            state = 'not_linked'
        elif not here:
            state = 'linked_elsewhere'
        elif not int(events['received']):
            state = 'awaiting_first_event'
        else:
            state = 'receiving'
        products.append(
            {
                'producer': producer.key,
                'label': producer.label,
                'state': state,
                'evidence_label': PRODUCT_EVENT_LABEL,
                'linked_here': here,
                'linked_workspace': (link['workspace_name'] if link and not here else None),
                'linked_at': link['updated_at'].isoformat() if link and here and link.get('updated_at') else None,
                'events_received': int(events['received']) if here else 0,
                'alerts_created': int(events['alerts']) if here else 0,
                'last_event_at': events['last_received_at'].isoformat() if here and events['last_received_at'] else None,
                'unrouted_events': unrouted,
                'signals': {
                    'configured': signals_endpoint(producer) is not None,
                    'delivered': signals.get('delivered', 0),
                    'pending': signals.get('pending', 0),
                    'failed': signals.get('failed', 0),
                    'dead': signals.get('dead', 0),
                    'not_configured': signals.get('not_configured', 0),
                },
            }
        )
    return {
        'organization': {'linked': bool(organization), 'has_platform_organization': bool(organization and organization.get('platform_organization_id'))},
        'can_manage': can_manage,
        'products': products,
    }


def link_producer(connection: Any, *, workspace_id: str, user_id: str, producer: str) -> dict[str, Any]:
    if producer not in PRODUCERS:
        raise IntakeError(422, 'UNKNOWN_PRODUCER', 'Choose decoda-assets or decoda-vault.')
    organization = _organization_of_workspace(connection, workspace_id)
    if organization is None:
        raise IntakeError(409, 'WORKSPACE_WITHOUT_ORGANIZATION', 'This workspace does not belong to an organization.')
    if not _org_admin(connection, str(organization['id']), user_id):
        raise IntakeError(403, 'ORGANIZATION_ADMIN_REQUIRED', 'Only an owner or admin of the organization can route its product events.')
    connection.execute(
        """
        INSERT INTO decoda_product_links (id, organization_id, workspace_id, producer, created_by_user_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (organization_id, producer) DO UPDATE SET
            workspace_id = EXCLUDED.workspace_id, created_by_user_id = EXCLUDED.created_by_user_id, updated_at = NOW()
        """,
        (str(uuid.uuid4()), organization['id'], workspace_id, producer, user_id),
    )
    return {'linked': True, 'producer': producer}


def unlink_producer(connection: Any, *, workspace_id: str, user_id: str, producer: str) -> dict[str, Any]:
    if producer not in PRODUCERS:
        raise IntakeError(422, 'UNKNOWN_PRODUCER', 'Choose decoda-assets or decoda-vault.')
    organization = _organization_of_workspace(connection, workspace_id)
    if organization is None or not _org_admin(connection, str(organization['id']), user_id):
        raise IntakeError(403, 'ORGANIZATION_ADMIN_REQUIRED', 'Only an owner or admin of the organization can change this routing.')
    removed = connection.execute(
        'DELETE FROM decoda_product_links WHERE organization_id = %s AND producer = %s AND workspace_id = %s RETURNING id',
        (organization['id'], producer, workspace_id),
    ).fetchone()
    return {'linked': False, 'producer': producer, 'removed': removed is not None}


def retry_signals(connection: Any, *, workspace_id: str) -> int:
    cursor = connection.execute(
        """
        UPDATE decoda_product_signals SET status = 'pending', attempts = 0, next_attempt_at = NOW(), updated_at = NOW()
        WHERE workspace_id = %s AND status IN ('dead', 'failed')
        """,
        (workspace_id,),
    )
    return int(cursor.rowcount or 0)


# ── HTTP handlers (thin; the routes live in main.py) ─────────────────────────


def _http_error(exc: IntakeError) -> Exception:
    from fastapi import HTTPException

    return HTTPException(status_code=exc.status, detail=exc.message, headers={'X-Decoda-Error-Code': exc.code})


def http_status(request: Any) -> dict[str, Any]:
    from services.api.app import pilot

    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, context, _ = pilot.resolve_workspace_context_for_request(connection, request)
        workspace_id = str(context['workspace_id'])
        role = pilot._normalize_workspace_role(str(context.get('role') or ''))
        admin = pilot._workspace_permission_granted(connection, workspace_id, role, 'members.manage')
        return status_view(connection, workspace_id=workspace_id, user_id=str(user['id']), workspace_admin=admin)


def http_set_link(request: Any, producer: str, *, link: bool) -> dict[str, Any]:
    """Route (or stop routing) a producer to the current workspace: workspace AND organization admins only."""
    from services.api.app import pilot

    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, context = pilot._require_workspace_admin(connection, request)
        workspace_id = str(context['workspace_id'])
        try:
            change = link_producer if link else unlink_producer
            result = change(connection, workspace_id=workspace_id, user_id=str(user['id']), producer=producer)
        except IntakeError as exc:
            raise _http_error(exc) from None
        pilot.log_audit(
            connection,
            action='decoda_product.linked' if link else 'decoda_product.unlinked',
            entity_type='decoda_product_link',
            entity_id=producer,
            request=request,
            user_id=str(user['id']),
            workspace_id=workspace_id,
            metadata={'producer': producer},
        )
        connection.commit()
        return {**result, 'status': status_view(connection, workspace_id=workspace_id, user_id=str(user['id']), workspace_admin=True)}


def http_retry_signals(request: Any) -> tuple[dict[str, Any], str]:
    from services.api.app import pilot

    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, context = pilot._require_workspace_admin(connection, request)
        workspace_id = str(context['workspace_id'])
        retried = retry_signals(connection, workspace_id=workspace_id)
        pilot.log_audit(
            connection,
            action='decoda_product.signals_retried',
            entity_type='decoda_product_signal',
            entity_id=workspace_id,
            request=request,
            user_id=str(user['id']),
            workspace_id=workspace_id,
            metadata={'retried': retried},
        )
        connection.commit()
    return {'retried': retried}, workspace_id
