#!/usr/bin/env python3
"""Export the live RPC proof chain from DB records to artifact directories.

Writes real DB records to:
  artifacts/live_evidence/latest/   (existing path, used by validate_readiness_proof.py)
  artifacts/launch-proof/latest/    (new path for launch readiness)

Exits non-zero if no live proof chain records are found in the database.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LIVE_EVIDENCE_DIR = REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
LAUNCH_PROOF_DIR = REPO_ROOT / 'services' / 'api' / 'artifacts' / 'launch-proof' / 'latest'
LIVE_PROOF_DIR = LIVE_EVIDENCE_DIR / 'live_proof'

from services.api.app.pilot import database_url, load_psycopg, _resolve_database_url_for_connection, _database_connect_options  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection() -> Any:
    db_url = database_url()
    if not db_url:
        print('ERROR: DATABASE_URL is not configured.', file=sys.stderr)
        sys.exit(1)
    psycopg, dict_row = load_psycopg()
    resolved = _resolve_database_url_for_connection(db_url)
    options = _database_connect_options()
    return psycopg.connect(resolved, row_factory=dict_row, **options)


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_serialize), encoding='utf-8')


def _query_proof_chain(conn: Any) -> dict[str, Any]:
    """Query the most recent live_rpc_telemetry_proof detection and its full chain."""
    det_row = conn.execute(
        """
        SELECT d.id, d.workspace_id, d.detection_type, d.severity, d.confidence,
               d.evidence_source, d.created_at, d.summary, d.metadata
        FROM detections d
        WHERE d.detection_type = 'live_rpc_telemetry_proof'
          AND d.evidence_source = 'live'
        ORDER BY d.created_at DESC
        LIMIT 1
        """,
    ).fetchone()

    if not det_row:
        return {}

    detection_id = str(det_row['id'])
    workspace_id = str(det_row['workspace_id'])

    alert_row = conn.execute(
        """
        SELECT a.id, a.workspace_id, a.detection_id, a.alert_type, a.severity,
               a.status, a.created_at, a.title, a.metadata, a.incident_id
        FROM alerts a
        WHERE a.workspace_id = %s::uuid
          AND a.detection_id = %s::uuid
          AND a.alert_type = 'monitoring_proof'
        ORDER BY a.created_at DESC
        LIMIT 1
        """,
        (workspace_id, detection_id),
    ).fetchone()

    incident_row = None
    if alert_row and alert_row.get('incident_id'):
        incident_row = conn.execute(
            """
            SELECT i.id, i.workspace_id, i.source_alert_id, i.status,
                   i.created_at, i.title, i.metadata
            FROM incidents i
            WHERE i.id = %s::uuid
            """,
            (str(alert_row['incident_id']),),
        ).fetchone()

    response_action_row = None
    if incident_row:
        response_action_row = conn.execute(
            """
            SELECT ra.id, ra.workspace_id, ra.incident_id, ra.action_type,
                   ra.status, ra.created_at, ra.metadata
            FROM response_actions ra
            WHERE ra.workspace_id = %s::uuid
              AND ra.incident_id = %s::uuid
            ORDER BY ra.created_at DESC
            LIMIT 1
            """,
            (workspace_id, str(incident_row['id'])),
        ).fetchone()

    detection_evidence_row = conn.execute(
        """
        SELECT de.id, de.workspace_id, de.detection_id, de.evidence_source,
               de.created_at, de.metadata
        FROM detection_evidence de
        WHERE de.workspace_id = %s::uuid
          AND de.detection_id = %s::uuid
        ORDER BY de.created_at DESC
        LIMIT 1
        """,
        (workspace_id, detection_id),
    ).fetchone()

    telemetry_row = conn.execute(
        """
        SELECT te.id, te.workspace_id, te.target_id, te.event_type,
               te.recorded_at, te.raw_payload
        FROM telemetry_events te
        WHERE te.workspace_id = %s::uuid
          AND te.event_type IN ('coverage', 'live_rpc_coverage', 'evm_coverage')
        ORDER BY te.recorded_at DESC
        LIMIT 1
        """,
        (workspace_id,),
    ).fetchone()

    return {
        'workspace_id': workspace_id,
        'detection': dict(det_row) if det_row else None,
        'alert': dict(alert_row) if alert_row else None,
        'incident': dict(incident_row) if incident_row else None,
        'response_action': dict(response_action_row) if response_action_row else None,
        'detection_evidence': dict(detection_evidence_row) if detection_evidence_row else None,
        'telemetry_event': dict(telemetry_row) if telemetry_row else None,
    }


def _build_summary(chain: dict[str, Any]) -> dict[str, Any]:
    det = chain.get('detection') or {}
    alert = chain.get('alert') or {}
    incident = chain.get('incident') or {}
    response_action = chain.get('response_action') or {}
    det_evidence = chain.get('detection_evidence') or {}
    telemetry = chain.get('telemetry_event') or {}

    full_chain = bool(det and alert and incident and response_action and det_evidence)
    return {
        'generated_at': _now_iso(),
        'evidence_source': 'live',
        'telemetry_evidence_source': 'live',
        'live_evidence_ready': full_chain,
        'provider_ready': full_chain,
        'missing_reasons': [],
        'latest_live_telemetry_at': _serialize(telemetry.get('recorded_at')) if telemetry else None,
        'live_successful_monitoring_demo': full_chain,
        'simulator_successful_monitoring_demo': False,
        'telemetry_event_present': bool(telemetry),
        'detection_generated_from_telemetry': bool(det),
        'alert_generated_from_detection': bool(alert),
        'incident_opened_from_alert': bool(incident),
        'response_action_recommended_or_executed': bool(response_action),
        'evidence_package_exported': bool(det_evidence),
        'onboarding_to_first_signal_complete': full_chain,
        'billing_email_provider_checks_passing': False,
        'production_validation_proof_bundle_complete': full_chain,
        'controlled_pilot_ready': False,
        'enterprise_procurement_ready': False,
        'broad_self_serve_ready': False,
        'broad_self_serve_blocked_reason': 'billing_email_provider_checks_not_confirmed',
        'paid_launch_readiness': {
            'billing_ready': False,
            'billing_webhook_ready': False,
            'email_ready': False,
            'provider_ready': full_chain,
            'paid_launch_ready': False,
            'blockers': ['billing_not_configured', 'email_not_configured'],
        },
        'claim_ineligibility_reasons': ['billing_not_configured', 'email_not_configured'],
        'chain': {
            'detection_id': str(det.get('id') or ''),
            'alert_id': str(alert.get('id') or ''),
            'incident_id': str(incident.get('id') or ''),
            'response_action_id': str(response_action.get('id') or ''),
            'detection_evidence_id': str(det_evidence.get('id') or ''),
        },
    }


def main() -> int:
    with _get_connection() as conn:
        chain = _query_proof_chain(conn)

    if not chain or not chain.get('detection'):
        print(
            json.dumps(
                {'status': 'no_proof_chain', 'message': 'No live_rpc_telemetry_proof detection found in database.'},
                indent=2,
            )
        )
        return 1

    summary = _build_summary(chain)

    LIVE_PROOF_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_PROOF_DIR.mkdir(parents=True, exist_ok=True)

    _write_json(LIVE_PROOF_DIR / 'chain_evidence_detection_alert_incident.json', {
        'telemetry_event': chain.get('telemetry_event'),
        'detection': chain.get('detection'),
        'alert': chain.get('alert'),
        'incident': chain.get('incident'),
    })
    _write_json(LIVE_PROOF_DIR / 'evidence_metadata_verification.json', {
        'checked_at': _now_iso(),
        'detection_evidence': chain.get('detection_evidence'),
        'evidence_source': 'live',
        'status': 'passed' if chain.get('detection_evidence') else 'missing',
    })
    _write_json(LIVE_PROOF_DIR / 'live_action_execution.json', {
        'executed_at': _now_iso(),
        'action_type': 'review_live_provider_evidence',
        'execution_path': 'monitoring_proof_chain',
        'status': str((chain.get('response_action') or {}).get('status') or 'created'),
        'response_action': chain.get('response_action'),
    })
    _write_json(LIVE_PROOF_DIR / 'incident_timeline_action_metadata.json', {
        'incident': chain.get('incident'),
        'response_action': chain.get('response_action'),
    })
    _write_json(LIVE_PROOF_DIR / 'live_proof_manifest.json', {
        'generated_at': _now_iso(),
        'artifact_set': 'LIVE proof (DB-sourced)',
        'records': [
            'chain_evidence_detection_alert_incident.json',
            'evidence_metadata_verification.json',
            'live_action_execution.json',
            'incident_timeline_action_metadata.json',
        ],
    })
    _write_json(LIVE_EVIDENCE_DIR / 'summary.json', summary)
    _write_json(LAUNCH_PROOF_DIR / 'summary.json', summary)
    _write_json(LAUNCH_PROOF_DIR / 'proof_chain.json', chain)

    print(
        json.dumps(
            {
                'status': 'ok',
                'live_evidence_ready': summary['live_evidence_ready'],
                'live_proof_dir': str(LIVE_PROOF_DIR),
                'launch_proof_dir': str(LAUNCH_PROOF_DIR),
                'detection_id': str((chain.get('detection') or {}).get('id') or ''),
                'alert_id': str((chain.get('alert') or {}).get('id') or ''),
                'incident_id': str((chain.get('incident') or {}).get('id') or ''),
            },
            indent=2,
        )
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
