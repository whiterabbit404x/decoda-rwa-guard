"""Automatic incident creation for genuinely critical exploit detections.

Screen 7 requires the Digital Forensics Investigator to open an incident on its own
when a critical exploit threshold is genuinely breached — driven from the canonical
detection/alert pipeline, never from a frontend page effect.

This module contributes ONLY the automatic path. The manual "Open Incident" flow
(``escalate_alert_to_incident``) is preserved unchanged and interoperates with this
one: auto-creation sets ``alerts.incident_id`` and ``incidents.source_alert_id``, so a
later manual promotion of the same alert idempotently returns the same incident.

Idempotency guarantees (a repeated webhook / poll / retry / manual click must not
create a duplicate):
  * A deterministic ``dedup_key`` (``auto|<workspace>|<cluster_key>``) combined with
    the partial unique index ``uq_incidents_dedup_key`` (migration 0123). The INSERT
    uses ``ON CONFLICT ... DO NOTHING`` and then reads the surviving row.
  * ``threat_detections.linked_incident_id`` is set once (guarded ``WHERE ... IS NULL``)
    so a detection maps to exactly one incident.

Numbering is concurrency-safe (``forensic_investigation.assign_incident_reference``
uses an atomic per-workspace-year counter, never COUNT(*)+1).
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from services.api.app import pilot

logger = logging.getLogger(__name__)

# Severity ranking for the min-severity policy gate.
_SEVERITY_RANK = {'info': 0, 'low': 1, 'medium': 2, 'high': 3, 'critical': 4}


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except (ValueError, TypeError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    return str(os.getenv(name, 'true' if default else 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}


def auto_create_policy() -> dict[str, Any]:
    """Resolve the versioned automatic-incident-creation policy.

    Default policy: a detection auto-opens an incident only when it is CRITICAL, has
    sufficient canonical confidence, is alert-eligible, and is associated with a
    monitored asset in the current workspace. Non-critical detections never auto-open
    under the default policy (they remain manually promotable).
    """
    return {
        'policy_version': '1.0',
        'enabled': _env_flag('INCIDENT_AUTO_CREATE_ENABLED', True),
        'min_severity': (os.getenv('INCIDENT_AUTO_CREATE_MIN_SEVERITY', 'critical').strip().lower() or 'critical'),
        'min_confidence': max(0.0, min(1.0, _env_float('INCIDENT_AUTO_CREATE_MIN_CONFIDENCE', 0.7))),
        'require_monitored_asset': _env_flag('INCIDENT_AUTO_CREATE_REQUIRE_ASSET', True),
    }


def evaluate_policy(detection: dict[str, Any], *, policy: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Pure policy predicate. Returns ``(eligible, reason)``.

    ``reason`` is a stable machine code describing the decision (used in the audit
    metadata and unit tests).
    """
    pol = policy or auto_create_policy()
    if not pol.get('enabled', True):
        return False, 'policy_disabled'

    severity = str(detection.get('severity') or '').strip().lower()
    min_rank = _SEVERITY_RANK.get(str(pol.get('min_severity') or 'critical'), 4)
    if _SEVERITY_RANK.get(severity, -1) < min_rank:
        return False, 'below_min_severity'

    try:
        confidence = float(detection.get('confidence') or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < float(pol.get('min_confidence', 0.7)):
        return False, 'below_min_confidence'

    if not bool(detection.get('alert_eligible', True)):
        return False, 'not_alert_eligible'

    if pol.get('require_monitored_asset', True) and not detection.get('primary_asset_id'):
        return False, 'no_monitored_asset'

    return True, 'eligible'


def compute_dedup_key(workspace_id: str, detection: dict[str, Any]) -> str:
    """Deterministic canonical incident identity for de-duplication.

    ``cluster_key`` is the canonical detection cluster identity (``threat_detections``
    is UNIQUE on ``(workspace_id, cluster_key)``), so this key is stable across repeated
    processing of the same detection.
    """
    cluster_key = str(detection.get('cluster_key') or detection.get('id') or '')
    return f'auto|{workspace_id}|{cluster_key}'


def maybe_auto_create_incident(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    detection: dict[str, Any],
    alert_id: str,
    now: Any,
    policy: dict[str, Any] | None = None,
    request: Any = None,
) -> dict[str, Any] | None:
    """Transactionally, idempotently open a critical incident for a detection.

    Returns ``{'incident_id', 'created', 'dedup_key'}`` when an incident exists (new or
    pre-existing), or ``None`` when the policy does not permit auto-creation. Never
    executes any response action. The caller owns the surrounding transaction/commit.
    """
    pol = policy or auto_create_policy()
    eligible, reason = evaluate_policy(detection, policy=pol)
    if not eligible:
        return None

    detection_id = str(detection.get('id'))

    # Idempotency A: the detection is already linked to an incident.
    if detection.get('linked_incident_id'):
        return {'incident_id': str(detection['linked_incident_id']), 'created': False,
                'dedup_key': compute_dedup_key(workspace_id, detection), 'reason': 'existing_link'}

    dedup_key = compute_dedup_key(workspace_id, detection)
    incident_id = str(uuid.uuid4())
    severity = str(detection.get('severity') or 'critical').strip().lower()
    title = str(detection.get('title') or 'Critical threat detection')
    summary = str(detection.get('explanation') or title)
    event_type = str(detection.get('detection_type') or 'threat_detection')
    payload = {
        'source': 'incident_auto_creation',
        'policy_version': pol.get('policy_version'),
        'detection_id': detection_id,
        'cluster_key': detection.get('cluster_key'),
        'primary_asset_id': str(detection['primary_asset_id']) if detection.get('primary_asset_id') else None,
        'evidence_source': detection.get('evidence_source'),
        'confidence': float(detection.get('confidence') or 0),
    }

    # Idempotency B: the partial unique index on (workspace_id, dedup_key) collapses a
    # concurrent/duplicate creation to a single row. DO NOTHING then read the survivor.
    inserted = connection.execute(
        '''
        INSERT INTO incidents (
            id, workspace_id, user_id, analysis_run_id, event_type, title, severity, status, workflow_status,
            summary, target_id, source_alert_id, linked_alert_ids, dedup_key, payload, created_at, updated_at
        ) VALUES (
            %s, %s, %s, NULL, %s, %s, %s, 'open', 'open',
            %s, NULL, %s::uuid, %s::jsonb, %s, %s::jsonb, NOW(), NOW()
        )
        ON CONFLICT (workspace_id, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING
        RETURNING id
        ''',
        (
            incident_id, workspace_id, user_id, event_type, title, severity,
            summary, alert_id, pilot._json_dumps([alert_id]), dedup_key, pilot._json_dumps(payload),
        ),
    ).fetchone()

    if inserted is None:
        # A row with this dedup_key already exists — return it and ensure linkage.
        existing = connection.execute(
            'SELECT id FROM incidents WHERE workspace_id = %s AND dedup_key = %s LIMIT 1',
            (workspace_id, dedup_key),
        ).fetchone()
        existing_id = str(existing['id']) if existing and existing.get('id') else None
        if existing_id:
            connection.execute(
                'UPDATE threat_detections SET linked_incident_id = %s, updated_at = %s WHERE id = %s AND workspace_id = %s AND linked_incident_id IS NULL',
                (existing_id, now, detection_id, workspace_id),
            )
        return {'incident_id': existing_id, 'created': False, 'dedup_key': dedup_key, 'reason': 'dedup_hit'}

    created_id = str(inserted['id'])

    # Link the detection and the source alert (idempotent, NULL-guarded).
    connection.execute(
        'UPDATE threat_detections SET linked_incident_id = %s, status = CASE WHEN status = %s THEN %s ELSE status END, updated_at = %s WHERE id = %s AND workspace_id = %s AND linked_incident_id IS NULL',
        (created_id, 'open', 'investigating', now, detection_id, workspace_id),
    )
    connection.execute(
        'UPDATE alerts SET incident_id = %s::uuid, updated_at = NOW() WHERE id = %s::uuid AND workspace_id = %s AND incident_id IS NULL',
        (created_id, alert_id, workspace_id),
    )

    # Assign the human INC-YYYY-NNN reference (concurrency-safe counter).
    try:
        from services.api.app import forensic_investigation
        forensic_investigation.assign_incident_reference(
            connection, workspace_id=workspace_id, incident_id=created_id, now=now
        )
    except Exception:  # pragma: no cover - reference assignment must not block creation
        logger.warning('event=auto_incident_reference_failed incident_id=%s', created_id)

    # Initial workflow timeline event.
    pilot.append_incident_timeline_event(
        connection, workspace_id=workspace_id, incident_id=created_id,
        event_type='incident.auto_created',
        message='Incident automatically opened from a critical threat detection.',
        actor_user_id=None,
        metadata={'detection_id': detection_id, 'alert_id': alert_id, 'policy_version': pol.get('policy_version'),
                  'severity': severity, 'dedup_key': dedup_key},
    )

    # Permanent audit event for the automatic creation.
    try:
        pilot.log_audit(
            connection, action='incident.auto_created', entity_type='incident', entity_id=created_id,
            request=request, user_id=None, workspace_id=workspace_id,
            metadata={'detection_id': detection_id, 'alert_id': alert_id, 'policy_version': pol.get('policy_version'),
                      'severity': severity, 'confidence': float(detection.get('confidence') or 0)},
        )
    except Exception:  # pragma: no cover - audit must never break creation
        logger.warning('event=auto_incident_audit_failed incident_id=%s', created_id)

    logger.info('event=incident_auto_created incident_id=%s detection_id=%s alert_id=%s severity=%s',
                created_id, detection_id, alert_id, severity)
    return {'incident_id': created_id, 'created': True, 'dedup_key': dedup_key, 'reason': 'created'}
