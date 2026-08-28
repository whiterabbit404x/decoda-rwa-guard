"""Request-level handlers for Screen 3's Integrity tab.

Contract:
  * GET  /assets/{id}/integrity          — asset + on-chain state + authoritative
                                           state + the persisted reconciliation
                                           result. STRICTLY side-effect free: it
                                           never evaluates, never writes a
                                           snapshot, and never creates a
                                           detection. A page refresh therefore
                                           cannot manufacture records.
  * GET  /assets/{id}/integrity/history  — persisted snapshots, newest first.
  * POST /assets/{id}/integrity/reconcile — explicit operator diagnostic. RBAC
                                           gated; appends one immutable snapshot.
  * POST /assets/{id}/integrity/investigate — opens (or returns) the existing
                                           investigation for the canonical event.
                                           Never executes a response action and
                                           never duplicates an incident.

Every query is workspace-scoped through the resolved workspace context, so a
user in workspace A can never read workspace B's reconciliation data.
"""

from __future__ import annotations

from typing import Any, Optional

from services.api.app import pilot
from services.api.app.domains.asset_integrity import ai_explanation
from services.api.app.domains.asset_integrity import config as aic
from services.api.app.domains.asset_integrity import reconciliation as engine
from services.api.app.domains.asset_integrity import service

try:  # fastapi is stubbed in the offline test runner
    from fastapi import HTTPException, status
except Exception:  # pragma: no cover
    HTTPException = pilot.HTTPException  # type: ignore
    status = pilot.status  # type: ignore


_ASSET_COLUMNS = (
    'id, name, asset_type, rwa_asset_type, chain_network, identifier, custodian, '
    'token_symbol, token_contract_address, token_decimals, value_usd, '
    'verification_status, created_by_user_id'
)


def _load_asset(connection: Any, *, workspace_id: str, asset_id: str) -> dict[str, Any]:
    row = connection.execute(
        f'SELECT {_ASSET_COLUMNS} FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
        (asset_id, workspace_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
    return dict(row)


def _asset_summary(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': str(asset.get('id')),
        'name': asset.get('name'),
        'asset_type': asset.get('asset_type'),
        'rwa_asset_type': asset.get('rwa_asset_type'),
        'chain_network': asset.get('chain_network'),
        'identifier': asset.get('identifier'),
        'custodian': asset.get('custodian'),
        'token_symbol': asset.get('token_symbol'),
        'token_contract_address': asset.get('token_contract_address'),
        'token_decimals': asset.get('token_decimals'),
        'value_usd': pilot._json_safe_value(asset.get('value_usd')),
        'verification_status': asset.get('verification_status'),
    }


def _onchain_state_payload(row: Optional[dict[str, Any]], *, now: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Blockchain OBSERVATION. ``available=false`` means exactly that — the UI
    must render an unavailable state, never a zero or a healthy value."""
    if row is None:
        return {
            'available': False,
            'unavailable_reason': 'no_observation',
            'total_supply': None, 'token_decimals': None, 'last_delta': None,
            'last_delta_operation': None, 'last_delta_at': None, 'observed_at': None,
            'block_number': None, 'tx_hash': None, 'chain_network': None,
            'contract_address': None, 'provider_type': None, 'evidence_source': None,
            'age_seconds': None, 'stale': None,
        }
    age = engine.age_seconds(row.get('observed_at'), now)
    return {
        'available': True,
        'unavailable_reason': None,
        'total_supply': pilot._json_safe_value(row.get('total_supply')),
        'token_decimals': row.get('token_decimals'),
        'last_delta': pilot._json_safe_value(row.get('last_delta')),
        'last_delta_operation': row.get('last_delta_operation'),
        'last_delta_at': service._iso(row.get('last_delta_at')),
        'observed_at': service._iso(row.get('observed_at')),
        'block_number': row.get('block_number'),
        'tx_hash': row.get('tx_hash'),
        'chain_network': row.get('chain_network'),
        'contract_address': row.get('contract_address'),
        # Provenance only. Reconciliation logic is vendor-independent.
        'provider_type': row.get('provider_type'),
        'evidence_source': row.get('evidence_source'),
        'age_seconds': age,
        'stale': bool(age is not None and age > int(config['onchain_stale_seconds'])),
    }


def _authoritative_state_payload(row: Optional[dict[str, Any]], *, now: Any, config: dict[str, Any]) -> dict[str, Any]:
    """AUTHORITATIVE business state — deliberately a separate object from the
    on-chain observation so the UI can never blur the two."""
    if row is None:
        return {
            'available': False,
            'source_status': 'missing',
            'expected_total_supply': None, 'settlement_state': None, 'source_name': None,
            'source_kind': None, 'source_error': None, 'external_reference': None,
            'observed_at': None, 'evidence_source': None, 'age_seconds': None, 'stale': None,
        }
    source_status = str(row.get('source_status') or 'reported').lower()
    age = engine.age_seconds(row.get('observed_at'), now)
    return {
        'available': source_status == 'reported' and row.get('expected_total_supply') is not None,
        'source_status': source_status,
        'expected_total_supply': pilot._json_safe_value(row.get('expected_total_supply')),
        'settlement_state': row.get('settlement_state'),
        'source_name': row.get('source_name'),
        'source_kind': row.get('source_kind'),
        'source_error': row.get('source_error'),
        'external_reference': row.get('external_reference'),
        'observed_at': service._iso(row.get('observed_at')),
        'evidence_source': row.get('evidence_source'),
        'age_seconds': age,
        'stale': bool(age is not None and age > int(config['authoritative_stale_seconds'])),
    }


def _reconciliation_payload(snapshot: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The persisted verdict, exactly as the deterministic engine wrote it."""
    if snapshot is None:
        return None
    return {
        'id': str(snapshot.get('id')),
        'status': snapshot.get('status'),
        'reason_code': snapshot.get('reason_code'),
        'severity': snapshot.get('severity'),
        'observed_supply': pilot._json_safe_value(snapshot.get('observed_supply')),
        'expected_supply': pilot._json_safe_value(snapshot.get('expected_supply')),
        'variance_units': pilot._json_safe_value(snapshot.get('variance_units')),
        'token_decimals': snapshot.get('token_decimals'),
        'rule_id': snapshot.get('rule_id'),
        'rule_version': snapshot.get('rule_version'),
        'rule_config': pilot._json_safe_value(snapshot.get('rule_config')),
        'evaluated_at': service._iso(snapshot.get('evaluated_at')),
        'onchain_observed_at': service._iso(snapshot.get('onchain_observed_at')),
        'authoritative_observed_at': service._iso(snapshot.get('authoritative_observed_at')),
        'onchain_source': snapshot.get('onchain_source'),
        'authoritative_source': snapshot.get('authoritative_source'),
        'evidence_source': snapshot.get('evidence_source'),
        'block_number': snapshot.get('block_number'),
        'tx_hash': snapshot.get('tx_hash'),
        'external_reference': snapshot.get('external_reference'),
        'evidence_count': int(snapshot.get('evidence_count') or 0),
        'evidence_refs': pilot._json_safe_value(snapshot.get('evidence_refs')) or [],
        'match_detail': pilot._json_safe_value(snapshot.get('match_detail')) or {},
        'canonical_event_id': str(snapshot['canonical_event_id']) if snapshot.get('canonical_event_id') else None,
        'trigger_source': snapshot.get('trigger_source'),
        'is_anomaly': str(snapshot.get('status') or '') in engine.ANOMALY_STATUSES,
        'is_indeterminate': str(snapshot.get('status') or '') in engine.INDETERMINATE_STATUSES,
    }


def _ai_assessment_payload(snapshot: Optional[dict[str, Any]], asset: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The narrative shown in the AI Asset Risk Assessor panel.

    The stored summary is used when present. When it is absent (AI disabled at
    evaluation time, or an older snapshot), the deterministic template is
    rebuilt from the SAME persisted facts — so the panel is never empty and
    never depends on AI availability.
    """
    if snapshot is None:
        return None
    stored = str(snapshot.get('ai_summary') or '').strip()
    source = str(snapshot.get('ai_summary_source') or 'deterministic')
    fallback = ai_explanation.build_deterministic_summary({
        'asset_name': asset.get('name'),
        'status': snapshot.get('status'),
        'reason_code': snapshot.get('reason_code'),
        'severity': snapshot.get('severity'),
        'variance_units': pilot._json_safe_value(snapshot.get('variance_units')),
        'authoritative_source': snapshot.get('authoritative_source'),
        'rule_id': snapshot.get('rule_id'),
        'rule_version': snapshot.get('rule_version'),
        'evidence_count': snapshot.get('evidence_count'),
    })
    if not stored:
        return fallback
    return {**fallback, 'explanation': stored, 'source': source}


def _investigation_payload(connection: Any, *, workspace_id: str, snapshot: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Where the Investigate Variance CTA should go, from canonical facts only.

    ``available=false`` means there is nothing to investigate (no anomaly, or the
    anomaly has no canonical event) — the UI disables the CTA rather than
    offering an action that would fail.
    """
    empty = {'available': False, 'reason': 'no_anomaly', 'canonical_event_id': None,
             'incident_id': None, 'alert_id': None, 'destination': None}
    if snapshot is None or str(snapshot.get('status') or '') not in engine.ANOMALY_STATUSES:
        return empty
    event_id = snapshot.get('canonical_event_id')
    if not event_id:
        return {**empty, 'reason': 'no_canonical_event'}
    row = None
    if service._table_exists(connection, 'threat_detections'):
        row = connection.execute(
            'SELECT id, status, linked_alert_id, linked_incident_id FROM threat_detections WHERE id = %s AND workspace_id = %s',
            (str(event_id), workspace_id),
        ).fetchone()
    if row is None:
        return {**empty, 'reason': 'no_canonical_event'}
    incident_id = str(row['linked_incident_id']) if row.get('linked_incident_id') else None
    alert_id = str(row['linked_alert_id']) if row.get('linked_alert_id') else None
    destination = None
    if incident_id:
        destination = f'/incidents/{incident_id}'
    elif alert_id:
        destination = f'/alerts?alertId={alert_id}'
    return {
        'available': True,
        'reason': None,
        'canonical_event_id': str(row['id']),
        'detection_status': row.get('status'),
        'incident_id': incident_id,
        'alert_id': alert_id,
        'destination': destination,
    }


# --------------------------------------------------------------------------
# GET /assets/{asset_id}/integrity  — side-effect free
# --------------------------------------------------------------------------
def integrity_state_endpoint(asset_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    cfg = aic.integrity_config()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        asset = _load_asset(connection, workspace_id=workspace_id, asset_id=asset_id)
        now = pilot.utc_now()

        onchain_row = service.load_onchain_observation(connection, workspace_id=workspace_id, asset_id=asset_id)
        authoritative_row = service.load_authoritative_state(connection, workspace_id=workspace_id, asset_id=asset_id)
        snapshot = service.load_latest_snapshot(connection, workspace_id=workspace_id, asset_id=asset_id)

        payload = {
            'asset': _asset_summary(asset),
            'onchain_state': _onchain_state_payload(onchain_row, now=now, config=cfg),
            'authoritative_state': _authoritative_state_payload(authoritative_row, now=now, config=cfg),
            'reconciliation': _reconciliation_payload(snapshot),
            'ai_assessment': _ai_assessment_payload(snapshot, asset),
            'investigation': _investigation_payload(connection, workspace_id=workspace_id, snapshot=snapshot),
            'canonical_event': None,
            'rule': {'rule_id': cfg['rule_id'], 'rule_version': cfg['rule_version']},
            'reconcile_enabled': bool(cfg['on_demand_enabled']),
            'workspace': workspace_context['workspace'],
        }
        if snapshot is not None and snapshot.get('canonical_event_id'):
            payload['canonical_event'] = service.canonical_event_payload(
                workspace_id=workspace_id, asset_id=asset_id,
                result=_result_from_snapshot(snapshot), onchain_row=onchain_row,
                authoritative_row=authoritative_row,
                evidence_refs=pilot._json_safe_value(snapshot.get('evidence_refs')) or [],
                detected_at=snapshot.get('evaluated_at'),
                event_id=str(snapshot['canonical_event_id']),
                incident_id=payload['investigation'].get('incident_id'),
            )
        # Truthful "not configured" state: the asset has no integrity evidence at all.
        if snapshot is None and onchain_row is None and authoritative_row is None:
            payload['state'] = 'not_configured'
        elif snapshot is None:
            payload['state'] = 'not_evaluated'
        else:
            payload['state'] = 'evaluated'
        return payload


def _result_from_snapshot(snapshot: dict[str, Any]) -> engine.ReconciliationResult:
    """Rebuild the engine result object from a persisted snapshot, WITHOUT
    re-evaluating. Historical evidence keeps the rule it was produced under."""
    return engine.ReconciliationResult(
        status=str(snapshot.get('status')),
        reason_code=str(snapshot.get('reason_code')),
        variance_units=engine.to_units(snapshot.get('variance_units')),
        observed_supply=engine.to_units(snapshot.get('observed_supply')),
        expected_supply=engine.to_units(snapshot.get('expected_supply')),
        severity=str(snapshot.get('severity') or 'low'),
        rule_id=str(snapshot.get('rule_id') or ''),
        rule_version=int(snapshot.get('rule_version') or 0),
        rule_config=pilot._json_safe_value(snapshot.get('rule_config')) or {},
        match=engine.MatchResult(outcome=engine.MATCH_INSUFFICIENT_DATA),
        matched_issuance_id=str(snapshot['matched_issuance_id']) if snapshot.get('matched_issuance_id') else None,
    )


# --------------------------------------------------------------------------
# GET /assets/{asset_id}/integrity/history  — side-effect free
# --------------------------------------------------------------------------
def integrity_history_endpoint(asset_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    cfg = aic.integrity_config()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        _load_asset(connection, workspace_id=workspace_id, asset_id=asset_id)
        rows = service.load_snapshot_history(
            connection, workspace_id=workspace_id, asset_id=asset_id, limit=int(cfg['history_limit']),
        )
        return {
            'asset_id': str(asset_id),
            'snapshots': [
                {
                    'id': str(r.get('id')),
                    'evaluated_at': service._iso(r.get('evaluated_at')),
                    'observed_supply': pilot._json_safe_value(r.get('observed_supply')),
                    'expected_supply': pilot._json_safe_value(r.get('expected_supply')),
                    'variance_units': pilot._json_safe_value(r.get('variance_units')),
                    'status': r.get('status'),
                    'reason_code': r.get('reason_code'),
                    'severity': r.get('severity'),
                    'rule_id': r.get('rule_id'),
                    'rule_version': r.get('rule_version'),
                    'onchain_source': r.get('onchain_source'),
                    'authoritative_source': r.get('authoritative_source'),
                    'evidence_source': r.get('evidence_source'),
                    'evidence_count': int(r.get('evidence_count') or 0),
                    'canonical_event_id': str(r['canonical_event_id']) if r.get('canonical_event_id') else None,
                    'trigger_source': r.get('trigger_source'),
                }
                for r in rows
            ],
            'total': len(rows),
            'workspace': workspace_context['workspace'],
        }


# --------------------------------------------------------------------------
# POST /assets/{asset_id}/integrity/reconcile  — explicit operator diagnostic
# --------------------------------------------------------------------------
def reconcile_endpoint(asset_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    cfg = aic.integrity_config()
    if not cfg['on_demand_enabled']:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                'code': 'reconciliation_on_demand_disabled',
                'message': 'On-demand reconciliation is disabled for this deployment. Reconciliation results are produced by the monitoring system.',
            },
        )
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        # Operational action -> monitoring.configure permission (RBAC enforced).
        user, workspace_context = pilot.require_ops_rbac_guard(connection, request)
        workspace_id = workspace_context['workspace_id']
        asset = _load_asset(connection, workspace_id=workspace_id, asset_id=asset_id)
        if not service._table_exists(connection, 'asset_reconciliation_snapshots'):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='Reconciliation storage is provisioning. Try again shortly.',
            )
        outcome = service.evaluate_and_persist(
            connection, workspace_id=workspace_id, asset_id=asset_id,
            asset_name=str(asset.get('name') or 'Asset'), trigger_source='manual', config=cfg,
        )
        connection.commit()
        result: engine.ReconciliationResult = outcome['result']
        service.log_integrity_event(
            'asset_reconciliation_requested', workspace_id=workspace_id, asset_id=asset_id,
            user_id=str(user['id']), reconciliation_id=outcome['snapshot_id'], status=result.status,
        )
        return {
            'status': 'evaluated',
            'reconciliation_id': outcome['snapshot_id'],
            'reconciliation': {
                'status': result.status,
                'reason_code': result.reason_code,
                'severity': result.severity,
                'variance_units': service._num(result.variance_units),
                'observed_supply': service._num(result.observed_supply),
                'expected_supply': service._num(result.expected_supply),
                'rule_id': result.rule_id,
                'rule_version': result.rule_version,
                'evaluated_at': service._iso(outcome['evaluated_at']),
                'evidence_count': len(outcome['evidence_refs']),
            },
            'canonical_event_id': outcome['canonical_event_id'],
        }


# --------------------------------------------------------------------------
# POST /assets/{asset_id}/integrity/investigate
# --------------------------------------------------------------------------
def investigate_endpoint(asset_id: str, request: Any) -> dict[str, Any]:
    """Open (or return) the investigation for this asset's canonical event.

    Delegates to the EXISTING finding -> investigation workflow, so repeated
    clicks return the same alert/incident instead of creating duplicates. It
    never executes a response action.
    """
    pilot.require_live_mode()
    from services.api.app.domains.threat_detection import service as threat_service

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot.require_ops_rbac_guard(connection, request)
        workspace_id = workspace_context['workspace_id']
        _load_asset(connection, workspace_id=workspace_id, asset_id=asset_id)
        snapshot = service.load_latest_snapshot(connection, workspace_id=workspace_id, asset_id=asset_id)
        if snapshot is None or str(snapshot.get('status') or '') not in engine.ANOMALY_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='There is no unexplained variance to investigate for this asset.',
            )
        event_id = snapshot.get('canonical_event_id')
        if not event_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='No canonical operational-integrity event exists for this reconciliation result.',
            )
        outcome = threat_service.investigate_detection(
            connection, workspace_id=workspace_id, user_id=user['id'],
            detection_id=str(event_id), now=pilot.utc_now(), commit=True,
        )
        service.log_integrity_event(
            'asset_integrity_investigation_opened', workspace_id=workspace_id, asset_id=asset_id,
            event_id=str(event_id), reconciliation_id=str(snapshot.get('id')),
            outcome=outcome.get('status'), created=outcome.get('created'),
        )
        return {**outcome, 'asset_id': str(asset_id), 'reconciliation_id': str(snapshot.get('id'))}
