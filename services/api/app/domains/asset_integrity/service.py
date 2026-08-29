"""DB-backed asset integrity reconciliation.

Responsibilities:
  * read the latest stored on-chain observation and authoritative state for one
    workspace-scoped asset (never cross-tenant),
  * run the PURE deterministic engine over them,
  * persist an immutable reconciliation snapshot,
  * emit the canonical operational-integrity event into the EXISTING
    threat_detections table (Screen 5) so the downstream
    detection -> policy -> response -> incident -> evidence flow is fed from one
    object rather than a parallel architecture,
  * emit structured logs for every evaluation.

Read paths in this module are strictly side-effect free. Only ``evaluate_and_persist``
(reached from an explicit POST or the monitoring worker) writes.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any, Optional

from services.api.app import pilot
from services.api.app.domains.asset_integrity import ai_explanation
from services.api.app.domains.asset_integrity import config as aic
from services.api.app.domains.asset_integrity import reconciliation as engine
from services.api.app.domains.asset_risk import config as arc
from services.api.app.domains.operational_integrity import matcher as op_matcher
from services.api.app.domains.operational_integrity import schemas as op_schemas

logger = logging.getLogger(__name__)

# The canonical Screen 5 detection type an unexplained supply variance maps to.
# Reuses the existing vocabulary rather than inventing a parallel taxonomy.
CANONICAL_DETECTION_TYPE = 'mint_burn_irregularity'
CANONICAL_EVENT_TYPE = 'STATE_DRIFT_DETECTED'
CANONICAL_EVENT_CATEGORY = 'OPERATIONAL_INTEGRITY'


def log_integrity_event(event: str, **fields: Any) -> None:
    ordered = ' '.join(f'{k}={v}' for k, v in fields.items() if v is not None)
    logger.info('event=%s %s', event, ordered)


def _table_exists(connection: Any, name: str) -> bool:
    try:
        row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS ok', (f'public.{name}',)).fetchone()
    except Exception:
        return False
    return bool((row or {}).get('ok'))


def _num(value: Any) -> Any:
    """JSON-safe numeric: base-unit supply values are exact integers."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _amount_str(value: Any) -> Optional[str]:
    """Base-unit supply values bind to NUMERIC(78, 0) as exact strings — a uint256
    amount must never pass through a float on its way into the database."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(int(value)) if value == value.to_integral_value() else str(value)
    return str(value)


# --------------------------------------------------------------------------
# Evidence loading (workspace-scoped)
# --------------------------------------------------------------------------
def load_onchain_observation(connection: Any, *, workspace_id: str, asset_id: str) -> Optional[dict[str, Any]]:
    if not _table_exists(connection, 'asset_onchain_supply_observations'):
        return None
    row = connection.execute(
        '''
        SELECT id, total_supply, token_decimals, chain_network, contract_address, block_number, tx_hash,
               last_delta, last_delta_operation, last_delta_at, provider_type, evidence_source,
               telemetry_event_id, observed_at
        FROM asset_onchain_supply_observations
        WHERE workspace_id = %s AND asset_id = %s
        ORDER BY observed_at DESC, created_at DESC
        LIMIT 1
        ''',
        (workspace_id, asset_id),
    ).fetchone()
    return dict(row) if row else None


def load_authoritative_state(connection: Any, *, workspace_id: str, asset_id: str) -> Optional[dict[str, Any]]:
    if not _table_exists(connection, 'asset_authoritative_state'):
        return None
    row = connection.execute(
        '''
        SELECT id, expected_total_supply, token_decimals, settlement_state, source_name, source_kind,
               source_status, source_error, external_reference, evidence_source, observed_at
        FROM asset_authoritative_state
        WHERE workspace_id = %s AND asset_id = %s
        ORDER BY observed_at DESC, created_at DESC
        LIMIT 1
        ''',
        (workspace_id, asset_id),
    ).fetchone()
    return dict(row) if row else None


def load_authorizations(connection: Any, *, workspace_id: str, asset_id: str, limit: int = 50) -> list[dict[str, Any]]:
    if not _table_exists(connection, 'asset_authorized_issuances'):
        return []
    rows = connection.execute(
        '''
        SELECT id, operation, amount, token_decimals, settlement_state, external_reference,
               source_name, evidence_source, authorized_at, effective_from, effective_until, consumed_by_tx_hash
        FROM asset_authorized_issuances
        WHERE workspace_id = %s AND asset_id = %s
        ORDER BY authorized_at DESC
        LIMIT %s
        ''',
        (workspace_id, asset_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in (rows or [])]


# --------------------------------------------------------------------------
# Mapping stored rows -> pure engine inputs
# --------------------------------------------------------------------------
def _to_observation(row: Optional[dict[str, Any]]) -> Optional[engine.OnChainObservation]:
    if row is None:
        return None
    return engine.OnChainObservation(
        total_supply=engine.to_units(row.get('total_supply')),
        observed_at=row.get('observed_at'),
        block_number=row.get('block_number'),
        tx_hash=row.get('tx_hash'),
        last_delta=engine.to_units(row.get('last_delta')),
        last_delta_operation=row.get('last_delta_operation'),
        last_delta_at=row.get('last_delta_at'),
        external_reference=None,
        provider_type=str(row.get('provider_type') or 'unknown'),
        evidence_source=str(row.get('evidence_source') or 'live'),
        available=True,
    )


def _to_authoritative(row: Optional[dict[str, Any]]) -> Optional[engine.AuthoritativeState]:
    if row is None:
        return None
    return engine.AuthoritativeState(
        expected_total_supply=engine.to_units(row.get('expected_total_supply')),
        observed_at=row.get('observed_at'),
        settlement_state=row.get('settlement_state'),
        source_name=row.get('source_name'),
        external_reference=row.get('external_reference'),
        evidence_source=str(row.get('evidence_source') or 'live'),
        source_status=str(row.get('source_status') or 'reported'),
    )


def _to_authorizations(rows: list[dict[str, Any]]) -> list[engine.AuthorizedIssuance]:
    return [
        engine.AuthorizedIssuance(
            id=str(r['id']) if r.get('id') else None,
            operation=str(r.get('operation') or 'mint'),
            amount=engine.to_units(r.get('amount')),
            settlement_state=str(r.get('settlement_state') or 'pending'),
            external_reference=r.get('external_reference'),
            authorized_at=r.get('authorized_at'),
            effective_from=r.get('effective_from'),
            effective_until=r.get('effective_until'),
            source_name=r.get('source_name'),
            evidence_source=str(r.get('evidence_source') or 'live'),
        )
        for r in rows
    ]


# --------------------------------------------------------------------------
# Evidence references — real stored rows, counted, never a hardcoded number
# --------------------------------------------------------------------------
def build_evidence_refs(
    *,
    onchain_row: Optional[dict[str, Any]],
    authoritative_row: Optional[dict[str, Any]],
    authorization_rows: list[dict[str, Any]],
    result: engine.ReconciliationResult,
) -> list[dict[str, Any]]:
    """Every artifact the verdict actually rests on. The count shown in the UI is
    ``len()`` of this list — it is never a constant."""
    refs: list[dict[str, Any]] = []
    if onchain_row is not None:
        refs.append({
            'kind': 'onchain_supply_observation',
            'table': 'asset_onchain_supply_observations',
            'id': str(onchain_row.get('id')),
            'observed_at': _iso(onchain_row.get('observed_at')),
            'source': str(onchain_row.get('provider_type') or 'unknown'),
            'evidence_source': str(onchain_row.get('evidence_source') or 'live'),
        })
        if onchain_row.get('telemetry_event_id'):
            refs.append({
                'kind': 'telemetry_event',
                'table': 'telemetry_events',
                'id': str(onchain_row.get('telemetry_event_id')),
                'observed_at': _iso(onchain_row.get('observed_at')),
            })
        if onchain_row.get('tx_hash'):
            refs.append({
                'kind': 'onchain_transaction',
                'tx_hash': str(onchain_row.get('tx_hash')),
                'block_number': onchain_row.get('block_number'),
                'observed_at': _iso(onchain_row.get('last_delta_at') or onchain_row.get('observed_at')),
            })
    if authoritative_row is not None:
        refs.append({
            'kind': 'authoritative_state',
            'table': 'asset_authoritative_state',
            'id': str(authoritative_row.get('id')),
            'observed_at': _iso(authoritative_row.get('observed_at')),
            'source': str(authoritative_row.get('source_name') or 'unknown'),
            'external_reference': authoritative_row.get('external_reference'),
            'evidence_source': str(authoritative_row.get('evidence_source') or 'live'),
        })
    # Authorization records the matcher actually examined (same operation only).
    operation = None
    if result.match.matched is not None:
        operation = str(result.match.matched.operation or '').lower()
    elif result.variance_units is not None:
        operation = 'mint' if result.variance_units > 0 else 'burn'
    for row in authorization_rows:
        if operation and str(row.get('operation') or '').lower() != operation:
            continue
        refs.append({
            'kind': 'authorized_issuance_candidate',
            'table': 'asset_authorized_issuances',
            'id': str(row.get('id')),
            'operation': row.get('operation'),
            'external_reference': row.get('external_reference'),
            'settlement_state': row.get('settlement_state'),
            'matched': bool(result.match.matched is not None and str(result.match.matched.id) == str(row.get('id'))),
        })
    # The rule the verdict was produced under is itself an audit artifact.
    refs.append({
        'kind': 'reconciliation_rule',
        'rule_id': result.rule_id,
        'rule_version': result.rule_version,
        'rule_config': result.rule_config,
    })
    return refs


# --------------------------------------------------------------------------
# Canonical operational-integrity event (feeds Screens 5 / 11 / 8 / 7 / 9)
# --------------------------------------------------------------------------
def _cluster_key(*, workspace_id: str, asset_id: str, result: engine.ReconciliationResult) -> str:
    """Stable idempotency key. The SAME unexplained variance re-evaluated maps to
    ONE canonical event, so repeated reconciliations and repeated Investigate
    clicks never create duplicate detections or incidents."""
    raw = '|'.join([
        'asset_integrity',
        workspace_id,
        asset_id,
        result.rule_id,
        str(result.rule_version),
        result.status,
        result.reason_code,
        str(result.variance_units if result.variance_units is not None else ''),
    ])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def canonical_event_payload(
    *,
    workspace_id: str,
    asset_id: str,
    result: engine.ReconciliationResult,
    onchain_row: Optional[dict[str, Any]],
    authoritative_row: Optional[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    detected_at: Any,
    event_id: Optional[str] = None,
    incident_id: Optional[str] = None,
) -> dict[str, Any]:
    """Decoda's canonical event representation for this reconciliation result."""
    return {
        'event_id': event_id,
        'event_type': CANONICAL_EVENT_TYPE,
        'asset_id': asset_id,
        'workspace_id': workspace_id,
        'category': CANONICAL_EVENT_CATEGORY,
        'status': result.status,
        'reason_code': result.reason_code,
        'severity': result.severity,
        'observed_value': _num(result.observed_supply),
        'expected_value': _num(result.expected_supply),
        'variance_units': _num(result.variance_units),
        'rule_id': result.rule_id,
        'rule_version': result.rule_version,
        'source': {
            'onchain': str((onchain_row or {}).get('provider_type') or 'unknown'),
            'authoritative': str((authoritative_row or {}).get('source_name') or 'unknown'),
        },
        'evidence_refs': evidence_refs,
        'detected_at': _iso(detected_at),
        'incident_id': incident_id,
    }


def emit_canonical_event(
    connection: Any,
    *,
    workspace_id: str,
    asset_id: str,
    asset_name: str,
    result: engine.ReconciliationResult,
    onchain_row: Optional[dict[str, Any]],
    authoritative_row: Optional[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    explanation: str,
    now: Any,
) -> Optional[str]:
    """Upsert the operational-integrity detection for an anomalous result.

    Only a real anomaly emits an event: an indeterminate result (stale / missing /
    unavailable source) must never appear downstream as a detected threat.
    Returns the canonical event id, or None when nothing was emitted.
    """
    if not result.is_anomaly:
        return None
    if not _table_exists(connection, 'threat_detections'):
        log_integrity_event(
            'asset_integrity_canonical_event_skipped', workspace_id=workspace_id,
            asset_id=asset_id, reason='threat_detections_absent',
        )
        return None

    key = _cluster_key(workspace_id=workspace_id, asset_id=asset_id, result=result)
    evidence_source = str((onchain_row or {}).get('evidence_source') or 'live')
    title = f'Unexplained supply variance on {asset_name}'
    recommended = 'Confirm the authorization with the authoritative source, then open an investigation.'
    score_inputs = {
        'engine': 'asset_integrity_reconciliation',
        'status': result.status,
        'reason_code': result.reason_code,
        'variance_units': _num(result.variance_units),
        'observed_supply': _num(result.observed_supply),
        'expected_supply': _num(result.expected_supply),
        'rule_id': result.rule_id,
        'rule_version': result.rule_version,
        'match': result.match.as_dict(),
    }

    existing = connection.execute(
        'SELECT id, status FROM threat_detections WHERE workspace_id = %s AND cluster_key = %s',
        (workspace_id, key),
    ).fetchone()

    # Canonical operational-integrity facts carried on the SAME row Screen 5
    # reads, so the Screen 3 verdict and the Screen 5 detection are one object
    # rather than two that can disagree.
    checks = op_matcher.checks_from_reconciliation(
        result, onchain_row=onchain_row,
        authoritative_source=str((authoritative_row or {}).get('source_name') or '') or None,
    )
    checks_json = op_schemas.checks_as_dict(checks)

    if existing is None:
        event_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO threat_detections (
                id, workspace_id, cluster_key, category, detection_type, title, severity, confidence, status,
                primary_asset_id, evidence_source, evidence_quality, event_count, actor_count,
                transaction_count, evidence_count, score_inputs, explanation, recommended_next_step,
                alert_eligible, detector_version, deterministic_reason_code, operational_checks,
                observed_amount, expected_amount, variance_amount, tx_hash, ai_summary, ai_summary_source,
                first_seen_at, last_seen_at, detected_at, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, 'open',
                %s, %s, 'normalized_telemetry', 1, 0,
                %s, %s, %s::jsonb, %s, %s,
                %s, %s, %s, %s::jsonb,
                %s, %s, %s, %s, NULL, 'deterministic',
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (workspace_id, cluster_key) DO NOTHING
            ''',
            (
                event_id, workspace_id, key, CANONICAL_EVENT_CATEGORY, CANONICAL_DETECTION_TYPE,
                title, result.severity, 1.0,
                asset_id, evidence_source,
                1 if (onchain_row or {}).get('tx_hash') else 0, len(evidence_refs),
                pilot._json_dumps(score_inputs), explanation, recommended,
                evidence_source == 'live', f'{result.rule_id}-v{result.rule_version}',
                result.reason_code, pilot._json_dumps(checks_json),
                _amount_str(result.observed_supply), _amount_str(result.expected_supply),
                _amount_str(result.variance_units), (onchain_row or {}).get('tx_hash'),
                now, now, now, now, now,
            ),
        )
        row = connection.execute(
            'SELECT id FROM threat_detections WHERE workspace_id = %s AND cluster_key = %s',
            (workspace_id, key),
        ).fetchone()
        event_id = str(row['id']) if row else event_id
        log_integrity_event(
            'asset_integrity_canonical_event_created', workspace_id=workspace_id, asset_id=asset_id,
            event_id=event_id, status=result.status, reason_code=result.reason_code, severity=result.severity,
        )
    else:
        event_id = str(existing['id'])
        # Never downgrade an operator-driven state; refresh the evidence facts.
        connection.execute(
            '''
            UPDATE threat_detections SET
                category = %s,
                severity = %s,
                evidence_count = %s,
                score_inputs = %s::jsonb,
                explanation = %s,
                deterministic_reason_code = %s,
                operational_checks = %s::jsonb,
                observed_amount = %s,
                expected_amount = %s,
                variance_amount = %s,
                last_seen_at = GREATEST(last_seen_at, %s),
                updated_at = %s
            WHERE id = %s AND workspace_id = %s
            ''',
            (
                CANONICAL_EVENT_CATEGORY, result.severity, len(evidence_refs),
                pilot._json_dumps(score_inputs), explanation,
                result.reason_code, pilot._json_dumps(checks_json),
                _amount_str(result.observed_supply), _amount_str(result.expected_supply),
                _amount_str(result.variance_units),
                now, now, event_id, workspace_id,
            ),
        )
        log_integrity_event(
            'asset_integrity_canonical_event_updated', workspace_id=workspace_id, asset_id=asset_id,
            event_id=event_id, status=result.status, reason_code=result.reason_code,
        )
    return event_id


# --------------------------------------------------------------------------
# Does a token TOTAL SUPPLY exist as a concept for this asset?
# --------------------------------------------------------------------------
APPLICABILITY_APPLICABLE = 'APPLICABLE'
APPLICABILITY_NOT_APPLICABLE = 'NOT_APPLICABLE'


def token_supply_applicability(asset: Optional[dict[str, Any]], onchain_row: Optional[dict[str, Any]]) -> str:
    """Whether a token TOTAL SUPPLY is a meaningful field for this asset.

    A wallet is not a token contract: it has no total supply to observe, and
    reporting one as "Unavailable" would imply a value we failed to collect.
    Applicability is asserted from real facts only — an actual observed supply,
    a registered token contract, or a reserve-backed RWA type.

    Canonical for BOTH the read path (the On-Chain card's Total Supply row and
    the projected verdict) and the persist path, so a rendered "Not applicable"
    and a stored reconciliation status can never disagree.
    """
    if onchain_row is not None and onchain_row.get('total_supply') is not None:
        return APPLICABILITY_APPLICABLE
    asset = asset or {}
    if str(asset.get('token_contract_address') or '').strip():
        return APPLICABILITY_APPLICABLE
    if arc.reserve_required_for(asset.get('rwa_asset_type'), asset.get('reserve_feed_type')):
        return APPLICABILITY_APPLICABLE
    return APPLICABILITY_NOT_APPLICABLE


# --------------------------------------------------------------------------
# Evaluate + persist (WRITE path — never reached from a GET)
# --------------------------------------------------------------------------


def evaluate_asset(
    connection: Any,
    *,
    workspace_id: str,
    asset_id: str,
    config: dict[str, Any] | None = None,
    now: Any = None,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the deterministic engine over stored evidence. Reads only.

    ``asset`` supplies the applicability fact. Omitted (a caller that has not
    loaded the row), supply is assumed to apply — the pre-existing behaviour.
    """
    cfg = config or aic.integrity_config()
    now = now or pilot.utc_now()
    onchain_row = load_onchain_observation(connection, workspace_id=workspace_id, asset_id=asset_id)
    authoritative_row = load_authoritative_state(connection, workspace_id=workspace_id, asset_id=asset_id)
    authorization_rows = load_authorizations(
        connection, workspace_id=workspace_id, asset_id=asset_id,
        limit=int(cfg['authorization_lookback_limit']),
    )
    result = engine.evaluate(
        onchain=_to_observation(onchain_row),
        authoritative=_to_authoritative(authoritative_row),
        authorizations=_to_authorizations(authorization_rows),
        rules=aic.rules_from_config(cfg),
        now=now,
        supply_applicable=(
            asset is None
            or token_supply_applicability(asset, onchain_row) == APPLICABILITY_APPLICABLE
        ),
    )
    evidence_refs = build_evidence_refs(
        onchain_row=onchain_row, authoritative_row=authoritative_row,
        authorization_rows=authorization_rows, result=result,
    )
    return {
        'result': result,
        'onchain_row': onchain_row,
        'authoritative_row': authoritative_row,
        'authorization_rows': authorization_rows,
        'evidence_refs': evidence_refs,
        'config': cfg,
        'now': now,
    }


def evaluate_and_persist(
    connection: Any,
    *,
    workspace_id: str,
    asset_id: str,
    asset_name: str = 'Asset',
    trigger_source: str = 'worker',
    config: dict[str, Any] | None = None,
    now: Any = None,
    asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate, persist an immutable snapshot, and emit the canonical event.

    Historical snapshots are never modified — each evaluation appends a new row
    carrying the rule id/version it was produced under.
    """
    evaluated = evaluate_asset(
        connection, workspace_id=workspace_id, asset_id=asset_id, config=config, now=now, asset=asset,
    )
    result: engine.ReconciliationResult = evaluated['result']
    onchain_row = evaluated['onchain_row']
    authoritative_row = evaluated['authoritative_row']
    evidence_refs = evaluated['evidence_refs']
    now = evaluated['now']

    summary = ai_explanation.generate_summary({
        'asset_name': asset_name,
        'status': result.status,
        'reason_code': result.reason_code,
        'severity': result.severity,
        'variance_units': _num(result.variance_units),
        'observed_supply': _num(result.observed_supply),
        'expected_supply': _num(result.expected_supply),
        'authoritative_source': (authoritative_row or {}).get('source_name'),
        'onchain_source': (onchain_row or {}).get('provider_type'),
        'rule_id': result.rule_id,
        'rule_version': result.rule_version,
        'evidence_count': len(evidence_refs),
        'data_gaps': result.data_gaps,
    })

    canonical_event_id = emit_canonical_event(
        connection, workspace_id=workspace_id, asset_id=asset_id, asset_name=asset_name,
        result=result, onchain_row=onchain_row, authoritative_row=authoritative_row,
        evidence_refs=evidence_refs, explanation=str(summary.get('explanation') or ''), now=now,
    )

    snapshot_id = str(uuid.uuid4())
    if _table_exists(connection, 'asset_reconciliation_snapshots'):
        connection.execute(
            '''
            INSERT INTO asset_reconciliation_snapshots (
                id, workspace_id, asset_id, observed_supply, expected_supply, variance_units, token_decimals,
                status, reason_code, severity, rule_id, rule_version, rule_config,
                onchain_observed_at, authoritative_observed_at, evaluated_at,
                onchain_source, authoritative_source, evidence_source, block_number, tx_hash,
                external_reference, matched_issuance_id, evidence_count, evidence_refs, match_detail,
                canonical_event_id, ai_summary, ai_summary_source, trigger_source, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb, %s::jsonb,
                %s, %s, %s, %s, %s
            )
            ''',
            (
                snapshot_id, workspace_id, asset_id,
                result.observed_supply, result.expected_supply, result.variance_units,
                (onchain_row or {}).get('token_decimals') or (authoritative_row or {}).get('token_decimals'),
                result.status, result.reason_code, result.severity,
                result.rule_id, result.rule_version, pilot._json_dumps(result.rule_config),
                (onchain_row or {}).get('observed_at'), (authoritative_row or {}).get('observed_at'), now,
                (onchain_row or {}).get('provider_type'), (authoritative_row or {}).get('source_name'),
                str((onchain_row or {}).get('evidence_source') or 'live'),
                (onchain_row or {}).get('block_number'), (onchain_row or {}).get('tx_hash'),
                (authoritative_row or {}).get('external_reference'), result.matched_issuance_id,
                len(evidence_refs), pilot._json_dumps(evidence_refs), pilot._json_dumps(result.match.as_dict()),
                canonical_event_id, summary.get('explanation'), summary.get('source') or 'deterministic',
                trigger_source, now,
            ),
        )

    log_integrity_event(
        'asset_reconciliation_evaluated',
        workspace_id=workspace_id, asset_id=asset_id, reconciliation_id=snapshot_id,
        event_id=canonical_event_id, status=result.status, reason_code=result.reason_code,
        severity=result.severity, rule_id=result.rule_id, rule_version=result.rule_version,
        observed_supply=_num(result.observed_supply), expected_supply=_num(result.expected_supply),
        variance_units=_num(result.variance_units),
        onchain_observed_at=_iso((onchain_row or {}).get('observed_at')),
        authoritative_observed_at=_iso((authoritative_row or {}).get('observed_at')),
        evaluated_at=_iso(now), evidence_count=len(evidence_refs), trigger_source=trigger_source,
    )

    return {
        'snapshot_id': snapshot_id,
        'result': result,
        'summary': summary,
        'evidence_refs': evidence_refs,
        'canonical_event_id': canonical_event_id,
        'onchain_row': onchain_row,
        'authoritative_row': authoritative_row,
        'evaluated_at': now,
    }


# --------------------------------------------------------------------------
# Read path (side-effect free)
# --------------------------------------------------------------------------
def load_latest_snapshot(connection: Any, *, workspace_id: str, asset_id: str) -> Optional[dict[str, Any]]:
    if not _table_exists(connection, 'asset_reconciliation_snapshots'):
        return None
    row = connection.execute(
        '''
        SELECT * FROM asset_reconciliation_snapshots
        WHERE workspace_id = %s AND asset_id = %s
        ORDER BY evaluated_at DESC, created_at DESC
        LIMIT 1
        ''',
        (workspace_id, asset_id),
    ).fetchone()
    return dict(row) if row else None


def load_snapshot_history(connection: Any, *, workspace_id: str, asset_id: str, limit: int = 25) -> list[dict[str, Any]]:
    if not _table_exists(connection, 'asset_reconciliation_snapshots'):
        return []
    rows = connection.execute(
        '''
        SELECT id, observed_supply, expected_supply, variance_units, status, reason_code, severity,
               rule_id, rule_version, evaluated_at, onchain_observed_at, authoritative_observed_at,
               onchain_source, authoritative_source, evidence_source, evidence_count,
               canonical_event_id, trigger_source
        FROM asset_reconciliation_snapshots
        WHERE workspace_id = %s AND asset_id = %s
        ORDER BY evaluated_at DESC, created_at DESC
        LIMIT %s
        ''',
        (workspace_id, asset_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in (rows or [])]
