"""DB-backed Threat Detection Engineer.

Reads normalized telemetry incrementally, runs the deterministic detectors,
clusters/upserts detections (never duplicating on re-processing), attaches
evidence links grounded in real telemetry rows, and provides the idempotent
Start Investigation action. AI is only used for the narrative, never for
severity/confidence.

Truthfulness guarantees implemented here:
  * Counts (event/evidence/actor/transaction) are recomputed from the persisted
    evidence table after each upsert, so re-processing the same telemetry updates
    ONE cluster and never inflates counts.
  * A cluster whose evidence is not all live carries a non-live evidence_source
    and is never alert-eligible.
  * Start Investigation is idempotent: it reuses a deterministic alert id per
    cluster and returns an existing open alert/incident instead of duplicating.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import HTTPException, status

from services.api.app import pilot
from services.api.app.domains.threat_detection import ai_explanation
from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import detectors, scoring
from services.api.app.domains.threat_detection.detectors import AssetBaseline

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Canonical detection-lifecycle logging (never logs secrets)
# --------------------------------------------------------------------------
def log_event(event: str, **fields: Any) -> None:
    ordered = ' '.join(f'{k}={v}' for k, v in fields.items() if v is not None)
    logger.info('event=%s %s', event, ordered)


def _table_exists(connection: Any, name: str) -> bool:
    try:
        row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS ok', (f'public.{name}',)).fetchone()
        return bool((row or {}).get('ok'))
    except Exception:
        return False


def _deterministic_alert_id(workspace_id: str, cluster_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'threat-detect-alert:{workspace_id}:{cluster_key}'))


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _epoch(ts: Any) -> Optional[int]:
    if ts is None:
        return None
    try:
        if getattr(ts, 'tzinfo', None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp())
    except Exception:
        return None


# --------------------------------------------------------------------------
# Rolling baselines
# --------------------------------------------------------------------------
def gather_baselines(connection: Any, *, workspace_id: str, config: dict[str, Any]) -> dict[str, AssetBaseline]:
    """Per-asset transfer and mint/burn amount baselines from stored telemetry.

    Amounts are numeric-guarded (only well-formed integer/decimal strings count),
    so a malformed payload never corrupts a baseline. Mint = from zero-address;
    burn = to a zero/dead address."""
    if not _table_exists(connection, 'telemetry_events'):
        return {}
    zero = detectors.ZERO_ADDRESS
    dead = '0x000000000000000000000000000000000000dead'
    try:
        rows = connection.execute(
            '''
            SELECT asset_id,
                   AVG(amt) FILTER (WHERE amt IS NOT NULL AND NOT is_mint AND NOT is_burn) AS transfer_mean,
                   COUNT(*) FILTER (WHERE amt IS NOT NULL AND NOT is_mint AND NOT is_burn) AS transfer_samples,
                   AVG(amt) FILTER (WHERE amt IS NOT NULL AND (is_mint OR is_burn)) AS mint_burn_mean,
                   COUNT(*) FILTER (WHERE amt IS NOT NULL AND (is_mint OR is_burn)) AS mint_burn_samples
            FROM (
                SELECT asset_id,
                       CASE WHEN v ~ '^[0-9]+(\\.[0-9]+)?$' THEN v::numeric END AS amt,
                       (lower(COALESCE(f, '')) = %s) AS is_mint,
                       (lower(COALESCE(t, '')) IN (%s, %s)) AS is_burn
                FROM (
                    SELECT asset_id,
                           COALESCE(payload_json->>'value', payload_json->>'amount_wei', payload_json->>'amount') AS v,
                           COALESCE(payload_json->>'from', payload_json->>'from_address', payload_json->>'owner') AS f,
                           COALESCE(payload_json->>'to', payload_json->>'to_address') AS t
                    FROM telemetry_events
                    WHERE workspace_id = %s
                      AND observed_at >= NOW() - (%s || ' days')::interval
                      AND event_type = ANY(%s)
                ) raw
            ) classified
            GROUP BY asset_id
            ''',
            (
                zero,
                zero,
                dead,
                workspace_id,
                str(int(config['baseline_days'])),
                list(tdc.TRANSFER_EVENT_TYPES),
            ),
        ).fetchall()
    except Exception:
        logger.exception('event=threat_detection_baseline_query_failed')
        return {}

    baselines: dict[str, AssetBaseline] = {}
    for row in rows:
        key = str(row.get('asset_id')) if row.get('asset_id') else '__global__'
        baselines[key] = AssetBaseline(
            transfer_mean=Decimal(str(row['transfer_mean'])) if row.get('transfer_mean') is not None else None,
            transfer_samples=int(row.get('transfer_samples') or 0),
            mint_burn_mean=Decimal(str(row['mint_burn_mean'])) if row.get('mint_burn_mean') is not None else None,
            mint_burn_samples=int(row.get('mint_burn_samples') or 0),
        )
    return baselines


# --------------------------------------------------------------------------
# Checkpoint
# --------------------------------------------------------------------------
def _read_checkpoint(connection: Any, workspace_id: str) -> dict[str, Any]:
    if not _table_exists(connection, 'threat_detection_checkpoints'):
        return {'last_processed_at': None, 'last_processed_telemetry_id': None}
    row = connection.execute(
        'SELECT last_processed_at, last_processed_telemetry_id FROM threat_detection_checkpoints WHERE workspace_id = %s',
        (workspace_id,),
    ).fetchone()
    if not row:
        return {'last_processed_at': None, 'last_processed_telemetry_id': None}
    return {
        'last_processed_at': row.get('last_processed_at'),
        'last_processed_telemetry_id': str(row['last_processed_telemetry_id']) if row.get('last_processed_telemetry_id') else None,
    }


def _advance_checkpoint(connection: Any, *, workspace_id: str, last_at: Any, last_id: Any, processed: int, now: Any) -> None:
    if not _table_exists(connection, 'threat_detection_checkpoints'):
        return
    connection.execute(
        '''
        INSERT INTO threat_detection_checkpoints (workspace_id, last_processed_at, last_processed_telemetry_id, last_run_at, processed_total, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (workspace_id) DO UPDATE SET
            last_processed_at = COALESCE(EXCLUDED.last_processed_at, threat_detection_checkpoints.last_processed_at),
            last_processed_telemetry_id = COALESCE(EXCLUDED.last_processed_telemetry_id, threat_detection_checkpoints.last_processed_telemetry_id),
            last_run_at = EXCLUDED.last_run_at,
            processed_total = threat_detection_checkpoints.processed_total + EXCLUDED.processed_total,
            updated_at = EXCLUDED.updated_at
        ''',
        (workspace_id, last_at, last_id, now, int(processed), now),
    )


def _load_scan_window(connection: Any, *, workspace_id: str, config: dict[str, Any], now: Any, checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    """Load a bounded, recent window of telemetry for correlation.

    The window always extends far enough back to give the windowed detectors full
    context (>= 24h) but is bounded by batch_size so a cycle can never scan
    unbounded history. Re-scanning the trailing window is safe: cluster + evidence
    dedup make upserts idempotent."""
    correlation_seconds = max(tdc.WINDOW_24H, int(config['low_slow_window_seconds']))
    last_at = checkpoint.get('last_processed_at')
    # telemetry_events has no chain_id column; chain_id is read from payload_json by
    # normalize_event, so it is not selected here.
    # Runtime/ingestion rows (rpc_polling heartbeats, provider checks, cursor
    # updates, …) are excluded here at the source so they can never enter detector
    # evaluation or become anomalies/detections. extract_candidates also filters by
    # event type, so this is defence-in-depth plus a smaller, cheaper scan.
    rows = connection.execute(
        '''
        SELECT id, workspace_id, asset_id, target_id, event_type, observed_at, evidence_source, payload_json
        FROM telemetry_events
        WHERE workspace_id = %s
          AND observed_at >= COALESCE(%s::timestamptz, NOW()) - (%s || ' seconds')::interval
          AND lower(event_type) <> ALL(%s)
        ORDER BY observed_at ASC, id ASC
        LIMIT %s
        ''',
        (workspace_id, last_at, str(int(correlation_seconds)), tdc.runtime_event_types(), int(config['batch_size'])),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Cluster upsert + evidence
# --------------------------------------------------------------------------
def upsert_candidate(connection: Any, *, workspace_id: str, candidate: detectors.DetectionCandidate, config: dict[str, Any], now: Any) -> dict[str, Any]:
    """Cluster/upsert one candidate and attach its evidence. Returns an outcome dict."""
    # The cluster discriminator is the detector-supplied signature (e.g. shared
    # destination for coordinated activity) or, by default, the actor set.
    actor_sig = candidate.cluster_signature or scoring.actor_signature(candidate.actors)
    window_bucket = scoring.window_bucket(candidate.window_epoch or _epoch(now), candidate.window_seconds)
    key = scoring.cluster_key(
        workspace_id=workspace_id,
        detection_type=candidate.detection_type,
        chain_id=candidate.chain_id,
        asset_id=candidate.primary_asset_id,
        contract_address=candidate.contract_address,
        actor_signature_value=actor_sig,
        window_bucket_value=window_bucket,
        detector_version=tdc.DETECTOR_VERSION,
    )

    existing = connection.execute(
        'SELECT id, status, first_seen_at, linked_alert_id, linked_incident_id FROM threat_detections WHERE workspace_id = %s AND cluster_key = %s',
        (workspace_id, key),
    ).fetchone()

    created = existing is None
    detection_id = str(existing['id']) if existing else str(uuid.uuid4())
    prior_status = str(existing['status']) if existing else None

    if created:
        connection.execute(
            '''
            INSERT INTO threat_detections (
                id, workspace_id, cluster_key, detection_type, title, severity, confidence, status,
                chain_id, primary_asset_id, evidence_source, evidence_quality, baseline_window,
                event_count, actor_count, transaction_count, evidence_count, score_inputs,
                explanation, recommended_next_step, alert_eligible, detector_version,
                ai_summary, ai_summary_source, first_seen_at, last_seen_at, detected_at, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'low', 0, 'anomaly',
                %s, %s, %s, %s, %s,
                0, 0, 0, 0, '{}'::jsonb,
                %s, %s, FALSE, %s,
                NULL, 'deterministic', %s, %s, %s, %s, %s
            )
            ON CONFLICT (workspace_id, cluster_key) DO NOTHING
            ''',
            (
                detection_id, workspace_id, key, candidate.detection_type, candidate.title,
                candidate.chain_id, candidate.primary_asset_id, candidate.evidence_source, candidate.evidence_quality, candidate.baseline_window,
                candidate.explanation, candidate.recommended_next_step, tdc.DETECTOR_VERSION,
                now, now, now, now, now,
            ),
        )
        # Re-read in case of a concurrent insert winning the ON CONFLICT.
        row = connection.execute(
            'SELECT id, status, first_seen_at, linked_alert_id, linked_incident_id FROM threat_detections WHERE workspace_id = %s AND cluster_key = %s',
            (workspace_id, key),
        ).fetchone()
        detection_id = str(row['id'])
        prior_status = str(row['status'])
        first_seen_at = row.get('first_seen_at') or now
        linked_alert_id = row.get('linked_alert_id')
        linked_incident_id = row.get('linked_incident_id')
    else:
        first_seen_at = existing.get('first_seen_at') or now
        linked_alert_id = existing.get('linked_alert_id')
        linked_incident_id = existing.get('linked_incident_id')

    # Attach evidence (idempotent by dedupe_key).
    evidence_added = 0
    for ev in candidate.evidence:
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
                    str(uuid.uuid4()), workspace_id, detection_id, ev.telemetry_id, ev.tx_hash, ev.block_number,
                    ev.actor_address, ev.evidence_type, ev.evidence_quality, pilot._json_dumps(ev.payload),
                    ev.dedupe_key(), ev.observed_at, now,
                ),
            ).fetchone()
            if result is not None:
                evidence_added += 1
        except Exception:
            logger.exception('event=threat_detection_evidence_insert_failed detection_id=%s', detection_id)

    # Recompute aggregates from persisted evidence (idempotent counts).
    agg = connection.execute(
        '''
        SELECT COUNT(*) AS evidence_count,
               COUNT(DISTINCT actor_address) FILTER (WHERE actor_address IS NOT NULL) AS actor_count,
               COUNT(DISTINCT transaction_hash) FILTER (WHERE transaction_hash IS NOT NULL) AS tx_count,
               MIN(observed_at) AS min_at,
               MAX(observed_at) AS max_at
        FROM threat_detection_evidence
        WHERE detection_id = %s
        ''',
        (detection_id,),
    ).fetchone() or {}
    evidence_count = int(agg.get('evidence_count') or 0)
    actor_count = max(int(agg.get('actor_count') or 0), candidate.actor_count)
    tx_count = max(int(agg.get('tx_count') or 0), candidate.transaction_count)
    span_seconds = 0
    min_at, max_at = agg.get('min_at'), agg.get('max_at')
    if min_at is not None and max_at is not None:
        e0, e1 = _epoch(min_at), _epoch(max_at)
        if e0 is not None and e1 is not None:
            span_seconds = max(0, e1 - e0)
    persistence_windows = max(1, (span_seconds // max(1, candidate.window_seconds)) + 1)

    # Score (deterministic — the LLM never sets these).
    severity = scoring.compute_severity(
        detection_type=candidate.detection_type,
        amount_deviation=candidate.amount_deviation,
        privileged=candidate.privileged,
        affected_asset_count=1,
        actor_count=actor_count,
        persistence_windows=int(persistence_windows),
        asset_importance=0,
    )
    confidence = scoring.compute_confidence(
        evidence_quality=candidate.evidence_quality,
        evidence_count=evidence_count,
        signal_count=candidate.signal_count,
        baseline_deviation=candidate.amount_deviation,
        actor_repetition=candidate.actor_repetition,
        temporal_correlation=candidate.temporal_correlation,
        baseline_samples=candidate.baseline_samples,
        min_baseline_samples=int(config['min_baseline_samples']),
    )

    promote = (
        confidence['confidence'] >= float(config['promote_confidence'])
        and scoring.severity_rank(severity['level']) >= scoring.severity_rank('medium')
    )
    # Preserve an operator-driven investigation state; never downgrade it.
    if prior_status in ('investigating', 'resolved', 'dismissed'):
        new_status = prior_status
    else:
        new_status = 'open' if promote else 'anomaly'

    alert_eligible = bool(
        new_status in ('open', 'investigating')
        and candidate.evidence_source == 'live'
        and scoring.severity_rank(severity['level']) >= scoring.severity_rank('high')
        and confidence['confidence'] >= float(config['alert_min_confidence'])
    )

    score_inputs = {'severity': severity['inputs'], 'confidence': confidence['inputs'], 'promote': promote}

    facts = {
        'detection_type': candidate.detection_type,
        'severity': severity['level'],
        'confidence': confidence['confidence'],
        'evidence_source': candidate.evidence_source,
        'evidence_quality': candidate.evidence_quality,
        'asset_name': None,
        'event_count': evidence_count,
        'evidence_count': evidence_count,
        'actor_count': actor_count,
        'explanation': candidate.explanation,
        'recommended_next_step': candidate.recommended_next_step,
        'tx_hashes': [t for t in candidate.tx_hashes if t],
    }
    summary = ai_explanation.generate_summary(facts)

    connection.execute(
        '''
        UPDATE threat_detections SET
            title = %s,
            severity = %s,
            confidence = %s,
            status = %s,
            evidence_source = %s,
            evidence_quality = %s,
            baseline_window = %s,
            event_count = %s,
            actor_count = %s,
            transaction_count = %s,
            evidence_count = %s,
            score_inputs = %s::jsonb,
            explanation = %s,
            recommended_next_step = %s,
            alert_eligible = %s,
            detector_version = %s,
            ai_summary = %s,
            ai_summary_source = %s,
            first_seen_at = LEAST(first_seen_at, %s),
            last_seen_at = GREATEST(last_seen_at, %s),
            detected_at = CASE WHEN status = 'anomaly' AND %s <> 'anomaly' THEN %s ELSE detected_at END,
            updated_at = %s
        WHERE id = %s AND workspace_id = %s
        ''',
        (
            candidate.title, severity['level'], confidence['confidence'], new_status,
            candidate.evidence_source, candidate.evidence_quality, candidate.baseline_window,
            evidence_count, actor_count, tx_count, evidence_count,
            pilot._json_dumps(score_inputs), candidate.explanation, candidate.recommended_next_step,
            alert_eligible, tdc.DETECTOR_VERSION,
            summary.get('finding'), summary.get('source') or 'deterministic',
            min_at or now, max_at or now, new_status, now, now,
            detection_id, workspace_id,
        ),
    )

    if created:
        log_event('threat_detection_created', workspace_id=workspace_id, detection_id=detection_id, detection_type=candidate.detection_type, severity=severity['level'], status=new_status)
    else:
        log_event('threat_detection_updated', workspace_id=workspace_id, detection_id=detection_id, evidence_added=evidence_added, status=new_status)

    return {
        'detection_id': detection_id,
        'cluster_key': key,
        'created': created,
        'status': new_status,
        'severity': severity['level'],
        'confidence': confidence['confidence'],
        'alert_eligible': alert_eligible,
        'evidence_added': evidence_added,
        'promoted': promote,
    }


# --------------------------------------------------------------------------
# Eligible-alert emission (worker path — reused deterministic id)
# --------------------------------------------------------------------------
def _resolve_asset_user_id(connection: Any, *, workspace_id: str, asset_id: Optional[str]) -> Optional[str]:
    if not asset_id:
        return None
    try:
        row = connection.execute(
            'SELECT created_by_user_id FROM assets WHERE id = %s AND workspace_id = %s',
            (asset_id, workspace_id),
        ).fetchone()
    except Exception:
        return None
    uid = (row or {}).get('created_by_user_id')
    return str(uid) if uid else None


def ensure_alert_for_detection(connection: Any, *, workspace_id: str, user_id: str, detection: dict[str, Any], now: Any) -> str:
    """Create (or refresh) the canonical alert for a detection cluster. Idempotent:
    the alert id is deterministic per (workspace, cluster_key)."""
    alert_id = _deterministic_alert_id(workspace_id, str(detection['cluster_key']))
    payload = {
        'source': 'threat_detection_engineer',
        'detection_id': str(detection['id']),
        'detection_type': detection.get('detection_type'),
        'cluster_key': detection.get('cluster_key'),
        'evidence_source': detection.get('evidence_source'),
        'evidence_quality': detection.get('evidence_quality'),
        'confidence': _num(detection.get('confidence')),
        'dedupe_signature': str(detection['cluster_key']),
    }
    title = str(detection.get('title') or 'Threat detection')
    summary_text = str(detection.get('explanation') or title)
    severity = str(detection.get('severity') or 'medium')
    connection.execute(
        '''
        INSERT INTO alerts (
            id, workspace_id, user_id, analysis_run_id, alert_type, title, severity, status, source_service,
            summary, payload, created_at, module_key, source, dedupe_signature, occurrence_count, first_seen_at, last_seen_at, updated_at
        ) VALUES (
            %s, %s, %s, NULL, %s, %s, %s, 'open', 'threat-detection-engineer',
            %s, %s::jsonb, NOW(), 'threat_detection', 'threat_detection_engineer', %s, 1, %s, %s, NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            severity = EXCLUDED.severity,
            title = EXCLUDED.title,
            summary = EXCLUDED.summary,
            payload = EXCLUDED.payload,
            status = CASE WHEN alerts.status IN ('resolved', 'false_positive') THEN 'open' ELSE alerts.status END,
            dedupe_signature = EXCLUDED.dedupe_signature,
            last_seen_at = %s,
            occurrence_count = alerts.occurrence_count + 1,
            updated_at = NOW()
        ''',
        (
            alert_id, workspace_id, user_id, str(detection.get('detection_type') or 'threat_detection'), title, severity,
            summary_text, pilot._json_dumps(payload), str(detection['cluster_key']), now, now, now,
        ),
    )
    connection.execute(
        'UPDATE threat_detections SET linked_alert_id = %s, updated_at = %s WHERE id = %s AND workspace_id = %s AND linked_alert_id IS NULL',
        (alert_id, now, str(detection['id']), workspace_id),
    )
    return alert_id


# --------------------------------------------------------------------------
# Process one workspace
# --------------------------------------------------------------------------
def process_workspace(connection: Any, *, workspace_id: str, config: dict[str, Any] | None = None, now: Any = None) -> dict[str, Any]:
    cfg = config or tdc.engine_config()
    now = now or pilot.utc_now()
    checkpoint = _read_checkpoint(connection, workspace_id)
    rows = _load_scan_window(connection, workspace_id=workspace_id, config=cfg, now=now, checkpoint=checkpoint)

    stats = {'workspace_id': workspace_id, 'telemetry_scanned': len(rows), 'candidates': 0, 'detections_created': 0, 'detections_updated': 0, 'anomalies': 0, 'alerts_upserted': 0, 'skipped': False}
    if not rows:
        stats['skipped'] = True
        return stats

    # Determine strictly-new events vs the checkpoint (for processed accounting).
    last_at = checkpoint.get('last_processed_at')
    last_id = checkpoint.get('last_processed_telemetry_id')
    last_epoch = _epoch(last_at)
    new_rows = []
    for r in rows:
        e = _epoch(r.get('observed_at'))
        rid = str(r.get('id'))
        if last_epoch is None or (e is not None and e > last_epoch) or (e == last_epoch and last_id and rid != last_id):
            new_rows.append(r)
    if last_epoch is not None and not new_rows:
        stats['skipped'] = True
        return stats

    baselines = gather_baselines(connection, workspace_id=workspace_id, config=cfg)
    events = [detectors.normalize_event(r) for r in rows]
    candidates = detectors.extract_candidates(events, baselines=baselines, config=cfg)
    stats['candidates'] = len(candidates)

    for candidate in candidates:
        outcome = upsert_candidate(connection, workspace_id=workspace_id, candidate=candidate, config=cfg, now=now)
        if outcome['created']:
            stats['detections_created'] += 1
        else:
            stats['detections_updated'] += 1
        if outcome['status'] == 'anomaly':
            stats['anomalies'] += 1
        log_event('threat_detection_evidence_processed', workspace_id=workspace_id, detection_id=outcome['detection_id'], evidence_added=outcome['evidence_added'])
        # Worker may upsert an eligible canonical alert when a user is resolvable.
        if outcome['alert_eligible']:
            det = connection.execute(
                'SELECT id, cluster_key, detection_type, title, explanation, severity, confidence, evidence_source, evidence_quality, primary_asset_id FROM threat_detections WHERE id = %s AND workspace_id = %s',
                (outcome['detection_id'], workspace_id),
            ).fetchone()
            uid = _resolve_asset_user_id(connection, workspace_id=workspace_id, asset_id=(det or {}).get('primary_asset_id'))
            if det is not None and uid:
                ensure_alert_for_detection(connection, workspace_id=workspace_id, user_id=uid, detection=dict(det), now=now)
                stats['alerts_upserted'] += 1
                log_event('threat_detection_alert_upserted', workspace_id=workspace_id, detection_id=outcome['detection_id'])

    # Advance checkpoint to the max scanned position.
    max_row = max(rows, key=lambda r: (_epoch(r.get('observed_at')) or 0, str(r.get('id'))))
    _advance_checkpoint(
        connection, workspace_id=workspace_id, last_at=max_row.get('observed_at'),
        last_id=str(max_row.get('id')), processed=len(new_rows), now=now,
    )
    return stats


def run_detection_for_workspace(workspace_id: str, *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open a connection, process one workspace, and commit. Used by the worker
    and by tests/fixtures to drive the engine without a background loop."""
    cfg = config or tdc.engine_config()
    with pilot.pg_connection() as connection:
        now = pilot.utc_now()
        log_event('threat_detection_run_started', workspace_id=workspace_id, detector_version=tdc.DETECTOR_VERSION)
        stats = process_workspace(connection, workspace_id=workspace_id, config=cfg, now=now)
        connection.commit()
        log_event(
            'threat_detection_run_completed', workspace_id=workspace_id,
            scanned=stats['telemetry_scanned'], created=stats['detections_created'],
            updated=stats['detections_updated'], anomalies=stats['anomalies'], alerts=stats['alerts_upserted'],
        )
        return stats


# --------------------------------------------------------------------------
# Start Investigation (idempotent)
# --------------------------------------------------------------------------
def start_investigation(detection_id: str, request: Any) -> dict[str, Any]:
    """Open (or return the existing) investigation for a threat detection.

    Idempotent and truthful:
      * returns an existing open incident/alert instead of creating a duplicate;
      * refuses to open an investigation on a sub-threshold anomaly or a
        resolved/dismissed detection with an actionable error;
      * creates the canonical alert (deterministic id per cluster) and marks the
        detection ``investigating`` — it never executes a mitigation action.
    """
    pilot.require_live_mode()
    try:
        uuid.UUID(str(detection_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='detection_id must be a valid UUID.')

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        return investigate_detection(
            connection, workspace_id=workspace_context['workspace_id'], user_id=user['id'],
            detection_id=str(detection_id), now=pilot.utc_now(), commit=True,
        )


def investigate_detection(connection: Any, *, workspace_id: str, user_id: str, detection_id: str, now: Any, commit: bool = False) -> dict[str, Any]:
    """Connection-level core of Start Investigation (auth is done by the caller).

    Idempotent and truthful — see ``start_investigation``."""
    if not _table_exists(connection, 'threat_detections'):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Detection not found.')

    detection = connection.execute(
        '''
        SELECT id, workspace_id, cluster_key, detection_type, title, explanation, severity, confidence,
               status, evidence_source, evidence_quality, evidence_count, linked_alert_id, linked_incident_id, last_seen_at
        FROM threat_detections
        WHERE id = %s::uuid AND workspace_id = %s
        ''',
        (detection_id, workspace_id),
    ).fetchone()
    if detection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Detection not found.')
    detection = dict(detection)
    det_status = str(detection.get('status') or '')

    # Idempotency: an existing open incident wins.
    if detection.get('linked_incident_id'):
        inc = connection.execute(
            "SELECT id, status FROM incidents WHERE id = %s AND workspace_id = %s",
            (str(detection['linked_incident_id']), workspace_id),
        ).fetchone()
        if inc is not None and str(inc.get('status') or '') not in ('resolved', 'closed'):
            return {'status': 'existing_incident', 'detection_id': detection_id, 'incident_id': str(inc['id']), 'alert_id': str(detection.get('linked_alert_id') or '') or None, 'destination': f"/incidents/{inc['id']}", 'created': False}

    # Truthful refusals.
    if det_status in ('resolved', 'dismissed'):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='This detection is resolved; no investigation to open.')
    if det_status == 'anomaly':
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Insufficient evidence: this is a sub-threshold anomaly, not a promoted detection.')
    if int(detection.get('evidence_count') or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Insufficient evidence to open an investigation.')

    # Existing open alert -> return it (do not duplicate).
    if detection.get('linked_alert_id'):
        existing_alert = connection.execute(
            "SELECT id, status, incident_id FROM alerts WHERE id = %s AND workspace_id = %s",
            (str(detection['linked_alert_id']), workspace_id),
        ).fetchone()
        if existing_alert is not None:
            if existing_alert.get('incident_id'):
                return {'status': 'existing_incident', 'detection_id': detection_id, 'incident_id': str(existing_alert['incident_id']), 'alert_id': str(existing_alert['id']), 'destination': f"/incidents/{existing_alert['incident_id']}", 'created': False}
            return {'status': 'existing_alert', 'detection_id': detection_id, 'alert_id': str(existing_alert['id']), 'incident_id': None, 'destination': f"/alerts?alertId={existing_alert['id']}", 'created': False}

    # Create the canonical alert and mark the detection under investigation.
    alert_id = ensure_alert_for_detection(connection, workspace_id=workspace_id, user_id=user_id, detection=detection, now=now)
    connection.execute(
        "UPDATE threat_detections SET status = 'investigating', linked_alert_id = %s, updated_at = %s WHERE id = %s AND workspace_id = %s",
        (alert_id, now, detection_id, workspace_id),
    )
    if commit:
        connection.commit()
    log_event('threat_detection_investigation_opened', workspace_id=workspace_id, detection_id=detection_id, alert_id=alert_id)
    return {'status': 'created', 'detection_id': detection_id, 'alert_id': alert_id, 'incident_id': None, 'destination': f'/alerts?alertId={alert_id}', 'created': True}
