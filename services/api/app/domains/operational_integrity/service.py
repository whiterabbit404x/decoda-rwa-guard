"""DB-backed Operational Integrity evaluation (workspace-scoped, idempotent).

Responsibilities:
  * read the workspace's own issuance/redemption telemetry and the authoritative
    business records it must reconcile against — never cross-tenant,
  * run the PURE matcher over them,
  * upsert ONE canonical detection per on-chain event into the EXISTING
    threat_detections table, with its provenance and evidence links,
  * report a TRUTHFUL telemetry-coverage state for the screen.

Idempotency: the cluster key is derived from the transaction (or, for a
settlement timeout, the authorization), NOT from the verdict. Repeated telemetry
for the same transaction therefore updates one row forever, and a verdict that
changes as business records arrive corrects that row instead of accumulating
contradictory duplicates. The upsert relies on the EXISTING
``UNIQUE (workspace_id, cluster_key)`` constraint on threat_detections.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any, Optional

from services.api.app import pilot
from services.api.app.domains.asset_integrity import service as integrity_service
from services.api.app.domains.operational_integrity import config as oic
from services.api.app.domains.operational_integrity import explanation, matcher, normalization, schemas

logger = logging.getLogger(__name__)

# Telemetry event types that can carry an issuance/redemption. Bound once so the
# scan window is narrow and index-friendly.
ISSUANCE_EVENT_TYPES = (
    'erc20_transfer', 'token_transfer', 'transfer', 'mint', 'burn',
    'tokens_minted', 'tokens_burned', 'issuance', 'redemption',
)


def log_event(event: str, **fields: Any) -> None:
    ordered = ' '.join(f'{k}={v}' for k, v in fields.items() if v is not None)
    logger.info('event=%s %s', event, ordered)


def _table_exists(connection: Any, name: str) -> bool:
    return integrity_service._table_exists(connection, name)


def _decimal_str(value: Any) -> Optional[str]:
    """Base-unit amounts are bound to NUMERIC(78,0) as strings — never floats."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(int(value)) if value == value.to_integral_value() else str(value)
    return str(value)


# --------------------------------------------------------------------------
# Workspace-scoped reads
# --------------------------------------------------------------------------
def load_issuance_telemetry(
    connection: Any, *, workspace_id: str, config: dict[str, Any], now: Any, asset_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Recent telemetry that could carry an issuance/redemption, newest first."""
    if not _table_exists(connection, 'telemetry_events'):
        return []
    params: list[Any] = [workspace_id, list(ISSUANCE_EVENT_TYPES), str(int(config['lookback_seconds']))]
    asset_clause = ''
    if asset_id:
        asset_clause = ' AND te.asset_id = %s::uuid'
        params.append(str(asset_id))
    params.append(int(config['batch_size']))
    rows = connection.execute(
        f'''
        SELECT te.id, te.asset_id, te.event_type, te.provider_type, te.evidence_source,
               te.observed_at, te.payload_json, a.name AS asset_name
        FROM telemetry_events te
        LEFT JOIN assets a ON a.id = te.asset_id AND a.workspace_id = te.workspace_id
        WHERE te.workspace_id = %s
          AND lower(te.event_type) = ANY(%s)
          AND te.observed_at >= NOW() - (%s || ' seconds')::interval{asset_clause}
        ORDER BY te.observed_at DESC
        LIMIT %s
        ''',
        tuple(params),
    ).fetchall()
    return [dict(r) for r in rows]


def load_open_authorizations(
    connection: Any, *, workspace_id: str, config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Authorizations that have not reached a cleared settlement state."""
    if not _table_exists(connection, 'asset_authorized_issuances'):
        return []
    rows = connection.execute(
        '''
        SELECT ai.id, ai.asset_id, ai.operation, ai.amount, ai.token_decimals, ai.settlement_state,
               ai.external_reference, ai.source_name, ai.evidence_source, ai.authorized_at,
               ai.effective_from, ai.effective_until, a.name AS asset_name
        FROM asset_authorized_issuances ai
        LEFT JOIN assets a ON a.id = ai.asset_id AND a.workspace_id = ai.workspace_id
        WHERE ai.workspace_id = %s
          AND lower(COALESCE(ai.settlement_state, '')) NOT IN ('settled','cleared','complete','completed','final','finalized')
        ORDER BY ai.authorized_at DESC
        LIMIT %s
        ''',
        (workspace_id, int(config['batch_size'])),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------
def cluster_key(*, workspace_id: str, detection_type: str, discriminator: str, matcher_version: str) -> str:
    """Stable idempotency key for one operational-integrity finding.

    Derived from the OBSERVED THING (a transaction, or an authorization), never
    from the verdict — so re-processing the same telemetry can only ever update
    the one row that already represents it."""
    raw = '|'.join(['operational_integrity', workspace_id, detection_type, discriminator, matcher_version])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


#: The two things an operational-integrity finding can be ABOUT. The lane is
#: chosen from the observed subject, never from the verdict: an on-chain mint
#: whose verdict shifts between "no authorization", "amount mismatch" and
#: "settlement not complete" is still the same transaction, and must keep
#: resolving to the same row.
LANE_ONCHAIN_EVENT = 'onchain_event'
LANE_AUTHORIZATION = 'authorization'


def event_lane(event: schemas.OperationalIntegrityEvent) -> tuple[str, str]:
    """(lane, discriminator) identifying what this finding is about.

    A finding raised from a transaction is keyed by that transaction; one raised
    from an authorization record with no on-chain event (a settlement deadline
    passing) is keyed by that authorization."""
    provenance = event.provenance or {}
    onchain_id = str(event.tx_hash or provenance.get('telemetry_id') or '').lower()
    if onchain_id:
        return LANE_ONCHAIN_EVENT, onchain_id
    authorization_id = str(provenance.get('authorization_id') or '')
    if authorization_id:
        return LANE_AUTHORIZATION, authorization_id
    # Neither subject was captured: fall back to the business reference so the
    # row is still stable rather than duplicating on every cycle.
    return LANE_ONCHAIN_EVENT, str(event.external_reference or '')


def event_cluster_key(event: schemas.OperationalIntegrityEvent) -> str:
    """Stable key for one finding, derived from its subject."""
    lane, discriminator = event_lane(event)
    return cluster_key(
        workspace_id=event.workspace_id,
        detection_type=lane,
        discriminator=discriminator,
        matcher_version=str(event.matcher_version or oic.MATCHER_VERSION),
    )


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
def _title_for(event: schemas.OperationalIntegrityEvent, asset_name: Optional[str]) -> str:
    label = oic.DETECTION_TYPE_LABELS.get(event.detection_type, event.detection_type.replace('_', ' ').title())
    return f'{label} on {asset_name}' if asset_name else label


def upsert_detection(
    connection: Any, *, event: schemas.OperationalIntegrityEvent, now: Any, config: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    """Persist ONE operational-integrity detection. Returns the outcome, or None
    when nothing should be stored.

    Only an evidenced anomaly is stored. An authorized event and an indeterminate
    event are both non-findings: storing either would put "we could not check" or
    "this was fine" into the operator's detection queue.
    """
    cfg = config or oic.engine_config()
    if not event.is_anomaly:
        return None
    if not _table_exists(connection, 'threat_detections'):
        log_event('operational_integrity_skipped', workspace_id=event.workspace_id, reason='threat_detections_absent')
        return None

    key = event_cluster_key(event)
    event.cluster_key = key
    matcher_version = str(event.matcher_version or oic.MATCHER_VERSION)
    asset_name = str((event.provenance or {}).get('asset_name') or '') or None
    narrative = explanation.build_deterministic_narrative(event.as_dict())
    explanation_text = f"{narrative['finding']} {narrative['explanation']}"
    recommended = narrative['investigation_step']
    score_inputs = {
        'engine': 'operational_integrity_matcher',
        'matcher_version': event.matcher_version,
        'detection_type': event.detection_type,
        'reason_code': event.deterministic_reason_code,
        'conclusion': event.conclusion,
        'observed_amount': schemas._num(event.observed_amount),
        'expected_amount': schemas._num(event.expected_amount),
        'variance_amount': schemas._num(event.variance_amount),
        'operational_checks': schemas.checks_as_dict(event.checks),
    }

    existing = connection.execute(
        'SELECT id, status, first_seen_at FROM threat_detections WHERE workspace_id = %s AND cluster_key = %s',
        (event.workspace_id, key),
    ).fetchone()

    created = existing is None
    detection_id = str(existing['id']) if existing else str(uuid.uuid4())
    # Simulator/replay evidence must never become an alert-eligible finding.
    alert_eligible = event.evidence_source == 'live'

    if created:
        # Every value is BOUND — no literals mixed into the VALUES list — so the
        # column order and the parameter order are the same list, which is what
        # makes this statement reviewable (and assertable) at a glance.
        columns = (
            'id', 'workspace_id', 'cluster_key', 'category', 'detection_type', 'title',
            'severity', 'confidence', 'status', 'chain_id', 'primary_asset_id',
            'evidence_source', 'evidence_quality', 'event_count', 'actor_count',
            'transaction_count', 'evidence_count', 'score_inputs', 'explanation',
            'recommended_next_step', 'alert_eligible', 'detector_version', 'matcher_version',
            'deterministic_reason_code', 'operational_checks', 'observed_amount',
            'expected_amount', 'variance_amount', 'amount_decimals', 'amount_unit',
            'operation', 'tx_hash', 'block_number', 'telemetry_source', 'telemetry_stage',
            'telemetry_observed_at', 'preconfirmation_received_at', 'provenance',
            'ai_summary', 'ai_summary_source', 'first_seen_at', 'last_seen_at',
            'detected_at', 'created_at', 'updated_at',
        )
        values = (
            detection_id, event.workspace_id, key, event.category, event.detection_type,
            _title_for(event, asset_name),
            event.severity, float(event.confidence), 'open', event.chain_id, event.asset_id,
            event.evidence_source, 'event_logs' if event.tx_hash else 'normalized_telemetry', 1, 0,
            1 if event.tx_hash else 0, 0, pilot._json_dumps(score_inputs), explanation_text,
            recommended, alert_eligible, matcher_version, matcher_version,
            event.deterministic_reason_code, pilot._json_dumps(schemas.checks_as_dict(event.checks)),
            _decimal_str(event.observed_amount),
            _decimal_str(event.expected_amount), _decimal_str(event.variance_amount),
            event.amount_decimals, event.amount_unit,
            event.operation, event.tx_hash, event.block_number, event.telemetry_source,
            event.telemetry_stage,
            event.telemetry_observed_at, event.preconfirmation_received_at,
            pilot._json_dumps(event.provenance or {}),
            None, 'deterministic', event.first_seen_at or now, now,
            now, now, now,
        )
        # JSONB columns need an explicit cast on their placeholder.
        jsonb = {'score_inputs', 'operational_checks', 'provenance'}
        placeholders = ', '.join('%s::jsonb' if c in jsonb else '%s' for c in columns)
        connection.execute(
            f'''
            INSERT INTO threat_detections ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT (workspace_id, cluster_key) DO NOTHING
            ''',
            values,
        )
        # Re-read in case a concurrent cycle won the ON CONFLICT: the row that
        # actually exists owns the id from here on.
        row = connection.execute(
            'SELECT id, status FROM threat_detections WHERE workspace_id = %s AND cluster_key = %s',
            (event.workspace_id, key),
        ).fetchone()
        if row is not None:
            detection_id = str(row['id'])
        log_event(
            'operational_integrity_detection_created', workspace_id=event.workspace_id,
            detection_id=detection_id, detection_type=event.detection_type,
            reason_code=event.deterministic_reason_code, severity=event.severity,
        )
    else:
        # Refresh the deterministic facts; never downgrade an operator-driven
        # status (investigating / resolved / dismissed stays where the human put it).
        connection.execute(
            '''
            UPDATE threat_detections SET
                category = %s,
                detection_type = %s,
                severity = %s,
                confidence = %s,
                deterministic_reason_code = %s,
                operational_checks = %s::jsonb,
                observed_amount = %s,
                expected_amount = %s,
                variance_amount = %s,
                telemetry_source = %s,
                telemetry_stage = %s,
                telemetry_observed_at = %s,
                preconfirmation_received_at = %s,
                provenance = %s::jsonb,
                score_inputs = %s::jsonb,
                explanation = %s,
                recommended_next_step = %s,
                matcher_version = %s,
                last_seen_at = GREATEST(last_seen_at, %s),
                updated_at = %s
            WHERE id = %s AND workspace_id = %s
            ''',
            (
                event.category, event.detection_type, event.severity, float(event.confidence),
                event.deterministic_reason_code, pilot._json_dumps(schemas.checks_as_dict(event.checks)),
                _decimal_str(event.observed_amount), _decimal_str(event.expected_amount),
                _decimal_str(event.variance_amount), event.telemetry_source, event.telemetry_stage,
                event.telemetry_observed_at, event.preconfirmation_received_at,
                pilot._json_dumps(event.provenance or {}), pilot._json_dumps(score_inputs),
                explanation_text, recommended, str(event.matcher_version or oic.MATCHER_VERSION),
                now, now, detection_id, event.workspace_id,
            ),
        )
        log_event(
            'operational_integrity_detection_updated', workspace_id=event.workspace_id,
            detection_id=detection_id, reason_code=event.deterministic_reason_code,
        )

    event.event_id = detection_id
    evidence_added = _attach_evidence(connection, event=event, detection_id=detection_id, now=now)
    if evidence_added:
        connection.execute(
            '''
            UPDATE threat_detections
               SET evidence_count = (
                       SELECT COUNT(*) FROM threat_detection_evidence
                        WHERE detection_id = %s AND workspace_id = %s
                   ),
                   updated_at = %s
             WHERE id = %s AND workspace_id = %s
            ''',
            (detection_id, event.workspace_id, now, detection_id, event.workspace_id),
        )
    return {'detection_id': detection_id, 'created': created, 'cluster_key': key, 'evidence_added': evidence_added}


def _attach_evidence(connection: Any, *, event: schemas.OperationalIntegrityEvent, detection_id: str, now: Any) -> int:
    """Link the detection to the stored telemetry that grounds it (idempotent).

    The evidence payload holds derived facts and provenance only — never an
    AI-generated sentence. Prose is not evidence.
    """
    if not _table_exists(connection, 'threat_detection_evidence'):
        return 0
    provenance = event.provenance or {}
    dedupe = f'operational_integrity:{event.deterministic_reason_code}:{event.tx_hash or provenance.get("telemetry_id") or provenance.get("authorization_id") or detection_id}'
    payload = {
        'detection_type': event.detection_type,
        'reason_code': event.deterministic_reason_code,
        'conclusion': event.conclusion,
        'operational_checks': schemas.checks_as_dict(event.checks),
        'observed_amount': schemas._num(event.observed_amount),
        'expected_amount': schemas._num(event.expected_amount),
        'variance_amount': schemas._num(event.variance_amount),
        'provenance': provenance,
    }
    try:
        result = connection.execute(
            '''
            INSERT INTO threat_detection_evidence (
                id, workspace_id, detection_id, telemetry_id, transaction_hash, block_number,
                actor_address, evidence_type, evidence_quality, evidence_payload, dedupe_key, observed_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (detection_id, dedupe_key) DO NOTHING
            RETURNING id
            ''',
            (
                str(uuid.uuid4()), event.workspace_id, detection_id,
                provenance.get('telemetry_id'), event.tx_hash, event.block_number,
                None, 'operational_integrity_reconciliation',
                'event_logs' if event.tx_hash else 'normalized_telemetry',
                pilot._json_dumps(payload), dedupe, event.telemetry_observed_at, now,
            ),
        ).fetchone()
        return 1 if result is not None else 0
    except Exception:
        logger.exception('event=operational_integrity_evidence_insert_failed detection_id=%s', detection_id)
        return 0


# --------------------------------------------------------------------------
# The cycle
# --------------------------------------------------------------------------
def evaluate_workspace(
    connection: Any, *, workspace_id: str, config: dict[str, Any] | None = None, now: Any = None,
) -> dict[str, Any]:
    """Reconcile one workspace's recent issuance telemetry + open authorizations."""
    cfg = config or oic.engine_config()
    now = now or pilot.utc_now()
    stats = {
        'events_evaluated': 0, 'anomalies': 0, 'created': 0, 'updated': 0,
        'authorized': 0, 'indeterminate': 0, 'settlement_timeouts': 0,
    }

    telemetry_rows = load_issuance_telemetry(connection, workspace_id=workspace_id, config=cfg, now=now)
    authoritative_cache: dict[str, Optional[dict[str, Any]]] = {}
    authorizations_cache: dict[str, list[dict[str, Any]]] = {}

    for row in telemetry_rows:
        event = normalization.normalize_telemetry_row(row)
        if not (event.is_issuance or event.is_redemption):
            continue
        stats['events_evaluated'] += 1
        asset_id = event.asset_id
        if asset_id and asset_id not in authoritative_cache:
            authoritative_cache[asset_id] = integrity_service.load_authoritative_state(
                connection, workspace_id=workspace_id, asset_id=asset_id,
            )
            authorizations_cache[asset_id] = integrity_service.load_authorizations(
                connection, workspace_id=workspace_id, asset_id=asset_id,
                limit=int(cfg['authorization_limit']),
            )
        result = matcher.evaluate_issuance(
            workspace_id=workspace_id,
            event=event,
            authoritative=authoritative_cache.get(asset_id or ''),
            authorizations=authorizations_cache.get(asset_id or '', []),
            now=now,
            config=cfg,
            asset_name=row.get('asset_name'),
        )
        if result.is_indeterminate:
            stats['indeterminate'] += 1
            continue
        if not result.is_anomaly:
            stats['authorized'] += 1
            continue
        stats['anomalies'] += 1
        outcome = upsert_detection(connection, event=result, now=now, config=cfg)
        if outcome:
            stats['created' if outcome['created'] else 'updated'] += 1

    for authorization in load_open_authorizations(connection, workspace_id=workspace_id, config=cfg):
        timeout = matcher.evaluate_settlement_deadline(
            workspace_id=workspace_id,
            authorization=authorization,
            asset_id=str(authorization['asset_id']) if authorization.get('asset_id') else None,
            asset_name=authorization.get('asset_name'),
            now=now,
            config=cfg,
        )
        if timeout is None:
            continue
        stats['settlement_timeouts'] += 1
        outcome = upsert_detection(connection, event=timeout, now=now, config=cfg)
        if outcome:
            stats['created' if outcome['created'] else 'updated'] += 1

    return stats


def run_for_workspace(workspace_id: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open a connection, evaluate one workspace, commit."""
    cfg = config or oic.engine_config()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        stats = evaluate_workspace(connection, workspace_id=workspace_id, config=cfg)
        connection.commit()
    log_event('operational_integrity_cycle', workspace_id=workspace_id, **{k: v for k, v in stats.items() if v})
    return stats


# --------------------------------------------------------------------------
# Truthful coverage state for the screen
# --------------------------------------------------------------------------
COVERAGE_LIVE = 'LIVE'
COVERAGE_DEGRADED = 'DEGRADED'
COVERAGE_UNAVAILABLE = 'UNAVAILABLE'


def telemetry_coverage(
    connection: Any, *, workspace_id: str, now: Any, config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What this workspace can HONESTLY claim about operational-integrity coverage.

    Three independent facts, never collapsed into one optimistic verdict:
      * is issuance telemetry arriving at all,
      * from which source and at which stage,
      * does an authoritative business record exist to reconcile against.

    A provider outage is DEGRADED, not "no anomalies found". A workspace with no
    authoritative source is UNAVAILABLE, not healthy.
    """
    cfg = config or oic.engine_config()
    result: dict[str, Any] = {
        'state': COVERAGE_UNAVAILABLE,
        'telemetry_source': None,
        'telemetry_stage': schemas.STAGE_UNKNOWN,
        'last_issuance_telemetry_at': None,
        'authoritative_sources': 0,
        'authorized_records': 0,
        'preconfirmation_available': normalization.preconfirmation_available(),
        'reasons': [],
    }
    reasons: list[str] = result['reasons']

    if _table_exists(connection, 'telemetry_events'):
        row = connection.execute(
            '''
            SELECT te.observed_at, te.provider_type, te.payload_json
            FROM telemetry_events te
            WHERE te.workspace_id = %s AND lower(te.event_type) = ANY(%s)
            ORDER BY te.observed_at DESC
            LIMIT 1
            ''',
            (workspace_id, list(ISSUANCE_EVENT_TYPES)),
        ).fetchone()
        if row:
            payload = row.get('payload_json') if isinstance(row.get('payload_json'), dict) else {}
            source = normalization.resolve_source(row.get('provider_type'), payload or {})
            result['telemetry_source'] = source
            result['telemetry_stage'] = normalization.resolve_stage(source, payload or {})
            result['last_issuance_telemetry_at'] = schemas._iso(row.get('observed_at'))
        else:
            reasons.append('no_issuance_telemetry')
    else:
        reasons.append('telemetry_unavailable')

    if _table_exists(connection, 'asset_authoritative_state'):
        counts = connection.execute(
            '''
            SELECT COUNT(DISTINCT asset_id) AS n
            FROM asset_authoritative_state
            WHERE workspace_id = %s AND source_status = 'reported'
            ''',
            (workspace_id,),
        ).fetchone() or {}
        result['authoritative_sources'] = int(counts.get('n') or 0)
    if _table_exists(connection, 'asset_authorized_issuances'):
        counts = connection.execute(
            'SELECT COUNT(*) AS n FROM asset_authorized_issuances WHERE workspace_id = %s',
            (workspace_id,),
        ).fetchone() or {}
        result['authorized_records'] = int(counts.get('n') or 0)

    if result['authoritative_sources'] == 0:
        reasons.append('no_authoritative_source')

    has_telemetry = result['last_issuance_telemetry_at'] is not None
    has_authority = result['authoritative_sources'] > 0
    if has_telemetry and has_authority:
        result['state'] = COVERAGE_LIVE
    elif has_telemetry or has_authority:
        result['state'] = COVERAGE_DEGRADED
    else:
        result['state'] = COVERAGE_UNAVAILABLE

    # Stale issuance telemetry under a configured authoritative source is
    # partial coverage, not full coverage.
    if result['state'] == COVERAGE_LIVE:
        try:
            from services.api.app.domains.asset_integrity import reconciliation as recon

            age = recon.age_seconds(
                _parse_iso(result['last_issuance_telemetry_at']), now,
            )
            if age is not None and age > int(cfg['lookback_seconds']):
                result['state'] = COVERAGE_DEGRADED
                reasons.append('issuance_telemetry_stale')
        except Exception:  # noqa: BLE001 - a parse failure must not fake coverage
            pass
    return result


def _parse_iso(value: Any) -> Any:
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
