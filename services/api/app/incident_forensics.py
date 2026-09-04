"""Incident forensic case record (Screen 7) — evidence domains + lifecycle timeline.

Screen 7 is the forensic CASE view for the canonical event lifecycle:

    state drift (Screen 3) -> operational anomaly (Screen 5) -> policy evaluated
    (Screen 11) -> response gated (Screen 8) -> incident (Screen 7) -> evidence
    package (Screen 9)

The business principle it has to make auditable is that a blockchain transaction
may be cryptographically valid while still being operationally unauthorized. To
show that, an incident's evidence has to be readable in four separate provenance
domains — what the CHAIN said, what the BUSINESS systems said, what the POLICY
engine decided, and what PEOPLE did — because those four are exactly the axes on
which "valid" and "authorized" come apart.

What this module is
-------------------
A READ/DERIVATION layer over rows that already exist. It introduces no table, no
migration and no second copy of any fact:

    ON_CHAIN      incident_evidence_snapshots (telemetry), evidence,
                  threat_detections on-chain provenance
    OPERATIONAL   asset_reconciliation_snapshots, asset_authoritative_state,
                  asset_authorized_issuances, threat_detections operational checks
    POLICY        governance_policy_evaluations (Screen 11, deterministic engine)
    HUMAN_ACTION  response_action_approvals, incident_timeline human-actor rows

Truthfulness rules honored here (see CLAUDE.md)
-----------------------------------------------
  * Classification is DETERMINISTIC — an explicit source/type map, never an LLM
    and never a heuristic over free text. AI may explain evidence elsewhere; it
    can never decide an artifact's provenance domain.
  * ``content_sha256`` is a real sha256 over the canonicalized bytes of the
    record that is actually stored (the same ``canonical_json`` serializer the
    evidence-package manifest uses). Nothing is hashed to produce a decorative
    hex string, and an artifact whose payload cannot be canonicalized reports
    ``integrity_status = 'unverified'`` with no hash at all.
  * ``immutable`` is TRUE only for an artifact carried inside an evidence
    snapshot whose persisted ``snapshot_hash`` RE-COMPUTES to the same value.
    Living in PostgreSQL is not immutability, and a live table read never claims
    it.
  * ``snapshot_status`` reaches ``sealed`` only when Screen 9's evidence-package
    layer confirms a package for this incident. Screen 7 never seals anything
    itself and never re-implements packaging.
  * A missing fact is reported as missing. Absent evidence is never rendered as
    "clean", and a read that FAILS is reported in ``unreadable`` rather than as
    an empty domain.

Every query is workspace-scoped; a cross-workspace incident id raises 404 before
any artifact is read.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable

from fastapi import HTTPException, status

from services.api.app import pilot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Evidence domains
# ---------------------------------------------------------------------------
ON_CHAIN = 'ON_CHAIN'
OPERATIONAL = 'OPERATIONAL'
POLICY = 'POLICY'
HUMAN_ACTION = 'HUMAN_ACTION'

#: Canonical order — the order the four summary cards are rendered in, and the
#: order the lifecycle itself runs in (chain observation, business state, policy
#: verdict, human response).
EVIDENCE_DOMAINS: tuple[str, ...] = (ON_CHAIN, OPERATIONAL, POLICY, HUMAN_ACTION)

DOMAIN_COUNT_KEYS: dict[str, str] = {
    ON_CHAIN: 'on_chain',
    OPERATIONAL: 'operational',
    POLICY: 'policy',
    HUMAN_ACTION: 'human_actions',
}

# ---------------------------------------------------------------------------
# Deterministic domain classification
# ---------------------------------------------------------------------------
# An artifact's domain is decided by the ARTIFACT TYPE the collector stamped on
# it, which is itself derived from the canonical table the row came from. This
# map is the single source of truth; it is exhaustive over the types this module
# emits, and an unknown type is never guessed into a domain (see
# ``classify_domain``). No natural-language input reaches it.
ARTIFACT_TYPE_DOMAINS: dict[str, str] = {
    # -- ON_CHAIN: what the chain itself recorded -----------------------------
    'transaction_receipt': ON_CHAIN,
    'transaction_payload': ON_CHAIN,
    'block_metadata': ON_CHAIN,
    'contract_event': ON_CHAIN,
    'chain_log': ON_CHAIN,
    'preconfirmation_record': ON_CHAIN,
    'mint_event': ON_CHAIN,
    'burn_event': ON_CHAIN,
    'transfer_event': ON_CHAIN,
    'signer_information': ON_CHAIN,
    'onchain_supply_observation': ON_CHAIN,
    'telemetry_event': ON_CHAIN,
    # -- OPERATIONAL: what the off-chain systems of record said ---------------
    'subscription_record': OPERATIONAL,
    'settlement_record': OPERATIONAL,
    'custody_state': OPERATIONAL,
    'transfer_agent_record': OPERATIONAL,
    'authoritative_state': OPERATIONAL,
    'authorized_issuance': OPERATIONAL,
    'nav_snapshot': OPERATIONAL,
    'reserve_data': OPERATIONAL,
    'reconciliation_output': OPERATIONAL,
    'ledger_delta': OPERATIONAL,
    'operational_check': OPERATIONAL,
    'detection_record': OPERATIONAL,
    # -- POLICY: what the deterministic policy engine decided -----------------
    'policy_snapshot': POLICY,
    'policy_evaluation_input': POLICY,
    'policy_decision': POLICY,
    'policy_reason_codes': POLICY,
    'policy_authorization_requirement': POLICY,
    'policy_simulation_result': POLICY,
    # -- HUMAN_ACTION: what a person did --------------------------------------
    'incident_acknowledgement': HUMAN_ACTION,
    'analyst_note': HUMAN_ACTION,
    'approval_decision': HUMAN_ACTION,
    'rejection_decision': HUMAN_ACTION,
    'escalation_record': HUMAN_ACTION,
    'compliance_decision': HUMAN_ACTION,
    'response_action_request': HUMAN_ACTION,
    'manual_status_transition': HUMAN_ACTION,
}

#: Operator-facing labels. One backend mapping so no React component
#: re-implements it and no SCREAMING_SNAKE key ever reaches an operator.
DOMAIN_LABELS: dict[str, str] = {
    ON_CHAIN: 'On-Chain',
    OPERATIONAL: 'Operational',
    POLICY: 'Policy',
    HUMAN_ACTION: 'Human Actions',
}


def classify_domain(artifact_type: Any) -> str | None:
    """The evidence domain for a persisted artifact type, or ``None``.

    Deterministic table lookup. ``None`` means "this collector emitted a type the
    domain map does not cover" — the artifact is still returned and counted in
    the total, but it is never assigned to a domain it was not mapped into, and
    it is never silently dropped either.
    """
    key = str(artifact_type or '').strip().lower()
    return ARTIFACT_TYPE_DOMAINS.get(key)


# ---------------------------------------------------------------------------
# Integrity states for a single artifact
# ---------------------------------------------------------------------------
#: Carried inside an evidence snapshot whose persisted hash re-computes. The
#: snapshot copy is tamper-evident: altering it changes ``snapshot_hash``.
INTEGRITY_SNAPSHOT_SEALED = 'snapshot_sealed'
#: A real sha256 over the canonicalized stored record, computed on read. It
#: proves what the row says NOW; it is not evidence that the row is unchanged.
INTEGRITY_CONTENT_HASHED = 'content_hashed'
#: The payload could not be canonicalized, so no digest is claimed.
INTEGRITY_UNVERIFIED = 'unverified'

INTEGRITY_LABELS: dict[str, str] = {
    INTEGRITY_SNAPSHOT_SEALED: 'Sealed in snapshot',
    INTEGRITY_CONTENT_HASHED: 'Content hashed',
    INTEGRITY_UNVERIFIED: 'Unverified',
}

# ---------------------------------------------------------------------------
# Forensic snapshot lifecycle (Phase 7). ``sealed`` is owned by Screen 9.
# ---------------------------------------------------------------------------
SNAPSHOT_COLLECTING = 'collecting'
SNAPSHOT_READY = 'ready'
SNAPSHOT_SEALED = 'sealed'
SNAPSHOT_FAILED = 'failed'

#: Screen 9 integrity states that mean the package really was built and its
#: integrity confirmed. Only these promote the incident snapshot to ``sealed``.
SEALING_INTEGRITY_STATES = frozenset({'verified', 'hash_generated'})

#: Bounded artifact page. The incident's full evidence inventory stays reachable
#: through Screen 9; this keeps one forensic directory response from growing
#: without limit.
ARTIFACT_CAP = 500


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _text(value: Any) -> str | None:
    text = str(value or '').strip()
    return text or None


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def content_digest(payload: Any) -> str | None:
    """sha256 over the canonical JSON bytes of a stored record, or ``None``.

    Uses the repository's single authoritative evidence-hashing serializer, so a
    digest computed here is byte-identical to the one an evidence-package
    manifest would compute for the same payload. A payload that cannot be
    canonicalized yields ``None`` and the caller reports ``unverified`` — a
    decorative hash is never substituted.
    """
    try:
        from services.api.app.evidence_signing import canonical_json
        return 'sha256:' + hashlib.sha256(canonical_json(payload)).hexdigest()
    except Exception:  # pragma: no cover - defensive: never fabricate a digest
        logger.warning('incident_forensics_digest_failed', exc_info=True)
        return None


def artifact_integrity(*, sealed_in_snapshot: bool, digest: str | None) -> tuple[str, bool]:
    """(integrity_status, immutable) for one artifact.

    ``immutable`` is TRUE only for a snapshot-sealed artifact: the evidence
    snapshot is a hashed point-in-time copy, so a change to the underlying row
    cannot alter it undetected. A live-table read is content-hashed at most —
    the digest describes the row as read, which is a weaker and DIFFERENT claim,
    and it is labelled as such.
    """
    if digest is None:
        return INTEGRITY_UNVERIFIED, False
    if sealed_in_snapshot:
        return INTEGRITY_SNAPSHOT_SEALED, True
    return INTEGRITY_CONTENT_HASHED, False


def count_domains(artifacts: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Per-domain artifact counts plus the total, from the artifacts themselves.

    The counts a client renders are derived from the SAME list it renders, so a
    summary card can never disagree with the directory beneath it. Unclassified
    artifacts contribute to ``total`` only — they are never folded into a domain
    to make the four numbers add up.
    """
    counts = {key: 0 for key in DOMAIN_COUNT_KEYS.values()}
    total = 0
    for artifact in artifacts:
        total += 1
        key = DOMAIN_COUNT_KEYS.get(str(artifact.get('domain') or ''))
        if key:
            counts[key] += 1
    counts['total'] = total
    return counts


def sort_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonical order: by server ``collected_at`` ascending, id as tiebreaker.

    Forensic ordering has to be the order events were RECORDED, not the order
    queries happened to return, so the directory and the timeline agree. An
    artifact with no timestamp sorts last rather than being dropped or being
    given a fabricated one.
    """
    def key(item: dict[str, Any]) -> tuple[int, str, str]:
        collected = _text(item.get('collected_at'))
        return (1, '', str(item.get('id') or '')) if collected is None else (0, collected, str(item.get('id') or ''))
    return sorted(artifacts, key=key)


def sort_timeline_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lifecycle order: canonical server ``occurred_at`` ascending.

    Millisecond precision is preserved wherever the source timestamp carries it
    (ISO-8601 strings sort lexicographically at equal precision). Frontend
    insertion order never participates.
    """
    def key(item: dict[str, Any]) -> tuple[int, str, str]:
        occurred = _text(item.get('occurred_at'))
        return (1, '', str(item.get('id') or '')) if occurred is None else (0, occurred, str(item.get('id') or ''))
    return sorted(events, key=key)


# ---------------------------------------------------------------------------
# Fail-closed table probe (mirrors the response_gate / governance conventions)
# ---------------------------------------------------------------------------
def _table_exists(connection: Any, name: str, unreadable: list[str]) -> bool:
    try:
        row = connection.execute(
            'SELECT to_regclass(%s) IS NOT NULL AS present', (f'public.{name}',),
        ).fetchone()
    except Exception:  # pragma: no cover - a probe failure is UNREADABLE
        logger.warning('incident_forensics_table_probe_failed table=%s', name, exc_info=True)
        unreadable.append(f'table:{name}')
        return False
    return bool(_row_dict(row).get('present'))


def _read(connection: Any, statement: str, params: tuple[Any, ...], *,
          fact: str, unreadable: list[str]) -> list[dict[str, Any]]:
    """One canonical read that is allowed to fail without poisoning the request.

    A read that RAISES is recorded in ``unreadable`` and yields no rows — which
    the response reports as a PARTIAL evidence view, never as an empty domain.
    "We could not look" and "there is none" are different answers and are never
    collapsed into one.
    """
    from services.api.app.domains.governance_policy.enforcement import read_scope
    try:
        with read_scope(connection):
            rows = connection.execute(statement, params).fetchall()
    except Exception:
        logger.warning('incident_forensics_read_failed fact=%s', fact, exc_info=True)
        unreadable.append(fact)
        return []
    return [_row_dict(row) for row in (rows or [])]


# ---------------------------------------------------------------------------
# Incident header + canonical correlation identifiers
# ---------------------------------------------------------------------------
def _require_incident(connection: Any, *, workspace_id: str, incident_id: str) -> dict[str, Any]:
    """The workspace's incident, or 404. The tenancy boundary for everything below.

    Every later read is filtered by ``workspace_id`` as well; this is the first
    gate, not the only one, so a foreign incident id can never reach an artifact
    read at all.
    """
    row = connection.execute(
        '''
        SELECT id, reference, title, severity, status, workflow_status, summary,
               target_id, source_alert_id, created_at, updated_at
        FROM incidents
        WHERE id = %s AND workspace_id = %s
        ''',
        (incident_id, workspace_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
    return _row_dict(row)


def resolve_correlation(connection: Any, *, workspace_id: str, incident: dict[str, Any],
                        unreadable: list[str]) -> dict[str, Any]:
    """The canonical lifecycle identifiers this incident already carries.

    Screen 7 does NOT mint an event id. It resolves the identifiers the rest of
    the workflow already writes — the Screen 5 threat detection is the canonical
    operational event, and its ``tx_hash`` is the correlation key Screen 11's
    evaluations and Screen 3's reconciliation snapshots are stamped with. A
    missing link is reported as ``None``, never invented.
    """
    incident_id = str(incident.get('id'))
    alert_id = _text(incident.get('source_alert_id'))
    detection: dict[str, Any] = {}
    if _table_exists(connection, 'threat_detections', unreadable):
        rows = _read(
            connection,
            '''SELECT id, detection_type, category, title, severity, tx_hash, block_number,
                      primary_asset_id, operation, deterministic_reason_code, detected_at,
                      telemetry_stage, telemetry_source, evidence_source, operational_checks,
                      observed_amount, expected_amount, variance_amount, amount_decimals,
                      amount_unit, provenance, preconfirmation_received_at, telemetry_observed_at
               FROM threat_detections
               WHERE workspace_id = %s
                 AND (linked_incident_id = %s::uuid
                      OR (%s::uuid IS NOT NULL AND linked_alert_id = %s::uuid))
               ORDER BY detected_at DESC, id ASC
               LIMIT 1''',
            (workspace_id, incident_id, alert_id, alert_id),
            fact='threat_detection', unreadable=unreadable,
        )
        detection = rows[0] if rows else {}

    # The canonical correlation key, in descending order of authority: the
    # detection's own transaction hash (what Screen 11 and Screen 3 stamp), then
    # the detection id, then the incident id. Never a newly minted value.
    event_id = _text(detection.get('tx_hash')) or _text(detection.get('id')) or incident_id
    return {
        'event_id': event_id,
        'incident_id': incident_id,
        'alert_id': alert_id,
        'detection_id': _text(detection.get('id')),
        'asset_id': _text(detection.get('primary_asset_id')),
        'detection': detection,
    }


def incident_header(connection: Any, *, workspace_id: str, incident: dict[str, Any],
                    correlation: dict[str, Any], unreadable: list[str]) -> dict[str, Any]:
    """Header facts for the Screen 7 case header: asset, category, detection, times.

    ``asset_label`` is the asset's real registered name, resolved through the
    canonical link the detection or the target already carries. When no asset row
    can be resolved the label is ``None`` and the UI says so — a raw UUID is not
    an asset name and a placeholder is not a fact.
    """
    detection = correlation.get('detection') or {}
    asset_id = _text(detection.get('primary_asset_id'))
    asset_label: str | None = None

    target_id = _text(incident.get('target_id'))
    if not asset_id and target_id and _table_exists(connection, 'targets', unreadable):
        rows = _read(
            connection,
            'SELECT asset_id FROM targets WHERE id = %s::uuid AND workspace_id = %s',
            (target_id, workspace_id), fact='target', unreadable=unreadable,
        )
        asset_id = _text(rows[0].get('asset_id')) if rows else None

    if asset_id and _table_exists(connection, 'assets', unreadable):
        rows = _read(
            connection,
            'SELECT name, asset_type, identifier FROM assets WHERE id = %s::uuid AND workspace_id = %s',
            (asset_id, workspace_id), fact='asset', unreadable=unreadable,
        )
        asset_label = _text(rows[0].get('name')) if rows else None

    return {
        'incident_id': str(incident.get('id')),
        'reference': _text(incident.get('reference')) or f"INC-{str(incident.get('id'))[:8]}",
        'title': _text(incident.get('title')),
        'severity': _text(incident.get('severity')),
        'status': _text(incident.get('workflow_status')) or _text(incident.get('status')),
        'asset_id': asset_id,
        'asset_label': asset_label,
        'target_id': target_id,
        'detection_category': _text(detection.get('category')),
        'detection_type': _text(detection.get('detection_type')),
        'detection_title': _text(detection.get('title')),
        'opened_at': _iso(incident.get('created_at')),
        'updated_at': _iso(incident.get('updated_at')),
    }


# ---------------------------------------------------------------------------
# Evidence snapshot (the sealed forensic copy)
# ---------------------------------------------------------------------------
def _latest_snapshot(connection: Any, *, workspace_id: str, incident_id: str,
                     unreadable: list[str]) -> dict[str, Any]:
    if not _table_exists(connection, 'incident_evidence_snapshots', unreadable):
        return {}
    rows = _read(
        connection,
        '''SELECT id, schema_version, snapshot_hash, snapshot_json, evidence_count,
                  is_complete, incomplete_reasons, created_at
           FROM incident_evidence_snapshots
           WHERE workspace_id = %s AND incident_id = %s
           ORDER BY created_at DESC, id DESC
           LIMIT 1''',
        (workspace_id, incident_id), fact='evidence_snapshot', unreadable=unreadable,
    )
    return rows[0] if rows else {}


def verify_snapshot_hash(snapshot_row: dict[str, Any]) -> bool | None:
    """Whether the persisted ``snapshot_hash`` re-computes over ``snapshot_json``.

    ``None`` when there is nothing to verify (no snapshot, or no stored payload).
    This is a genuine integrity check — it recomputes the digest with the same
    serializer that produced it — and it is what licenses the word "sealed" on an
    artifact carried inside the snapshot.
    """
    payload = snapshot_row.get('snapshot_json')
    recorded = _text(snapshot_row.get('snapshot_hash'))
    if not recorded or not isinstance(payload, dict):
        return None
    from services.api.app import ai_triage
    try:
        return ai_triage.compute_snapshot_hash(payload) == recorded
    except Exception:  # pragma: no cover - defensive
        logger.warning('incident_forensics_snapshot_verify_failed', exc_info=True)
        return None


def snapshot_state(*, snapshot_row: dict[str, Any], hash_verified: bool | None,
                   package: dict[str, Any]) -> str:
    """The incident's forensic snapshot lifecycle state.

    ``sealed`` is NOT something Screen 7 can reach on its own: it requires Screen
    9 to have produced a package whose integrity state confirms sealing. Screen 7
    tops out at ``ready`` — "an evidence snapshot exists and its hash verifies" —
    which is a true and much weaker statement than "sealed".
    """
    if not snapshot_row:
        return SNAPSHOT_COLLECTING
    if hash_verified is False:
        return SNAPSHOT_FAILED
    if package.get('available') and str(package.get('integrity_status') or '') in SEALING_INTEGRITY_STATES:
        return SNAPSHOT_SEALED
    return SNAPSHOT_READY


# ---------------------------------------------------------------------------
# Screen 9 evidence package linkage
# ---------------------------------------------------------------------------
def _evidence_package(connection: Any, *, workspace_id: str, incident_id: str,
                      unreadable: list[str]) -> dict[str, Any]:
    """The latest Screen 9 evidence package for this incident, or an absent state.

    Reuses Screen 9's OWN storage and its canonical display-state resolver, so
    the integrity wording Screen 7 shows is the wording Screen 9 shows. Screen 7
    builds no ZIP, computes no manifest and seals nothing; when no package
    exists it says exactly that.
    """
    absent = {'available': False, 'reason': 'not_generated'}
    if not _table_exists(connection, 'export_jobs', unreadable):
        return {'available': False, 'reason': 'unavailable'}
    rows = _read(
        connection,
        '''SELECT id, export_type, status, filters, package_number, size_bytes,
                  created_at, updated_at
           FROM export_jobs
           WHERE workspace_id = %s
             AND export_type IN ('proof_bundle', 'incident_report')
             AND filters->>'incident_id' = %s
           ORDER BY created_at DESC, id DESC
           LIMIT 1''',
        (workspace_id, incident_id), fact='evidence_package', unreadable=unreadable,
    )
    if not rows:
        return absent
    row = rows[0]
    package_id = str(row.get('id'))
    filters = row.get('filters') if isinstance(row.get('filters'), dict) else {}
    # Shape the row the way Screen 9's list projection does before handing it to
    # the shared resolver, so both screens read the same canonical state.
    projected: dict[str, Any] = {
        'id': package_id,
        'export_type': row.get('export_type'),
        'status': row.get('status'),
        'verification': filters.get('verification') if isinstance(filters.get('verification'), dict) else None,
        'superseded': False,
    }
    for key in ('export_status', 'completeness_score', 'manifest_sha256', 'manifest_file_count',
                'manifest_generated_at', 'integrity_hash', 'files_hashed', 'verified_at',
                'chain_complete', 'missing_sections', 'unavailable_sections'):
        if key in filters:
            projected[key] = filters[key]
    try:
        from services.api.app.evidence_completeness import (
            get_evidence_package_display_state as _display_state,
            integrity_status_label as _integrity_label,
        )
        display = _display_state(projected)
        integrity_status = str(display.get('integrity_status') or '')
        integrity_label = _integrity_label(integrity_status)
    except Exception:
        logger.warning('incident_forensics_package_state_failed', exc_info=True)
        unreadable.append('evidence_package_state')
        integrity_status, integrity_label = '', 'Unknown'
    return {
        'available': True,
        'package_id': package_id,
        'package_number': _text(row.get('package_number')) or f'EV-{package_id[:8].upper()}',
        'export_type': _text(row.get('export_type')),
        'status': _text(row.get('status')),
        'integrity_status': integrity_status,
        'integrity_label': integrity_label,
        'sealed_at': _iso(row.get('updated_at')) if integrity_status in SEALING_INTEGRITY_STATES else None,
        'created_at': _iso(row.get('created_at')),
        'route': f'/evidence?package_id={package_id}',
    }


# ---------------------------------------------------------------------------
# Artifact collectors — one per canonical source table
# ---------------------------------------------------------------------------
def _artifact(*, artifact_id: str, event_id: str | None, incident_id: str,
              artifact_type: str, file_name: str, source: str,
              collected_at: Any, payload: Any, sealed: bool,
              metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one directory row. The domain comes from the deterministic map only."""
    digest = content_digest(payload)
    integrity_status, immutable = artifact_integrity(sealed_in_snapshot=sealed, digest=digest)
    return pilot._json_safe_value({
        'id': artifact_id,
        'incident_id': incident_id,
        'event_id': event_id,
        'domain': classify_domain(artifact_type),
        'artifact_type': artifact_type,
        'file_name': file_name,
        'source': source,
        'collected_at': _iso(collected_at),
        'content_sha256': digest,
        'integrity_status': integrity_status,
        'integrity_label': INTEGRITY_LABELS.get(integrity_status, 'Unverified'),
        'immutable': immutable,
        'metadata': metadata or {},
    })


def _onchain_artifacts(*, incident_id: str, event_id: str | None, snapshot_row: dict[str, Any],
                       snapshot_sealed: bool) -> list[dict[str, Any]]:
    """Chain-side artifacts, taken from the evidence snapshot's telemetry rows.

    These are the records that carry a transaction identity. They are the half of
    the case that can be cryptographically valid while the operational half says
    nobody authorized it — which is precisely why they are kept in their own
    domain rather than merged with business records.
    """
    payload = snapshot_row.get('snapshot_json')
    if not isinstance(payload, dict):
        return []
    artifacts: list[dict[str, Any]] = []
    telemetry = payload.get('telemetry')
    for index, event in enumerate(telemetry if isinstance(telemetry, list) else []):
        if not isinstance(event, dict):
            continue
        telemetry_id = _text(event.get('telemetry_id')) or f'{incident_id}:telemetry:{index}'
        tx_hash = _text(event.get('tx_hash'))
        artifacts.append(_artifact(
            artifact_id=f'telemetry:{telemetry_id}',
            event_id=event_id, incident_id=incident_id,
            artifact_type='telemetry_event',
            file_name=f"telemetry_{telemetry_id}.json",
            source=_text(event.get('detected_by')) or _text(event.get('evidence_source')) or 'telemetry',
            collected_at=event.get('observed_at') or event.get('ingested_at'),
            payload=event, sealed=snapshot_sealed,
            metadata={
                'tx_hash': tx_hash,
                'block_number': event.get('block_number'),
                'chain_id': event.get('chain_id'),
                'event_type': _text(event.get('event_type')),
                'evidence_source': _text(event.get('evidence_source')),
                'link_scope': LINK_SCOPE_INCIDENT,
            },
        ))
    # Provider observations record WHICH provider saw the same transaction, and
    # are the raw chain-side receipt behind a collapsed canonical telemetry row.
    observations = payload.get('provider_observations')
    for index, observation in enumerate(observations if isinstance(observations, list) else []):
        if not isinstance(observation, dict):
            continue
        artifacts.append(_artifact(
            artifact_id=f'provider_observation:{incident_id}:{index}',
            event_id=event_id, incident_id=incident_id,
            artifact_type='transaction_receipt',
            file_name=f"provider_observation_{index + 1}.json",
            source=_text(observation.get('detected_by')) or _text(observation.get('provider')) or 'provider',
            collected_at=observation.get('observed_at'),
            payload=observation, sealed=snapshot_sealed,
            metadata={'tx_hash': _text(observation.get('tx_hash')),
                      'link_scope': LINK_SCOPE_INCIDENT},
        ))
    return artifacts


#: How an artifact was linked to this incident. EVENT means the record names this
#: incident's canonical event; ASSET means it concerns the same asset but was never
#: linked to this event. Presenting the second as the first would turn a coincidence
#: into evidence, so the distinction is carried to the UI rather than flattened.
LINK_SCOPE_EVENT = 'EVENT'
LINK_SCOPE_ASSET = 'ASSET'
LINK_SCOPE_INCIDENT = 'INCIDENT'


def _reconciliation_rows(connection: Any, *, workspace_id: str, asset_id: str,
                         detection_id: str | None,
                         unreadable: list[str]) -> list[dict[str, Any]]:
    """Reconciliation snapshots for this incident, event-linked ones preferred.

    ``asset_reconciliation_snapshots.canonical_event_id`` references the Screen 5
    detection a snapshot emitted, so when this incident HAS a detection the
    snapshots that name it are the ones that are actually about this event. Only
    when no such link exists do recent asset-scoped snapshots stand in, and those
    are stamped ``ASSET`` so the UI can say that they concern the asset rather
    than this event.
    """
    columns = """SELECT id, status, reason_code, severity, observed_supply, expected_supply,
                        variance_units, token_decimals, rule_id, rule_version, onchain_source,
                        authoritative_source, evidence_source, tx_hash, block_number,
                        external_reference, matched_issuance_id, evaluated_at,
                        onchain_observed_at, authoritative_observed_at, canonical_event_id
                 FROM asset_reconciliation_snapshots"""
    if detection_id:
        linked = _read(
            connection,
            f"""{columns}
                WHERE workspace_id = %s AND asset_id = %s::uuid
                  AND canonical_event_id = %s::uuid
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 5""",
            (workspace_id, asset_id, detection_id),
            fact='reconciliation_snapshot', unreadable=unreadable,
        )
        if linked:
            return [{**row, 'link_scope': LINK_SCOPE_EVENT} for row in linked]
    rows = _read(
        connection,
        f"""{columns}
            WHERE workspace_id = %s AND asset_id = %s::uuid
            ORDER BY evaluated_at DESC, id DESC
            LIMIT 5""",
        (workspace_id, asset_id), fact='reconciliation_snapshot', unreadable=unreadable,
    )
    return [
        {**row, 'link_scope': (
            LINK_SCOPE_EVENT
            if detection_id and str(row.get('canonical_event_id') or '') == detection_id
            else LINK_SCOPE_ASSET
        )}
        for row in rows
    ]


def _operational_artifacts(connection: Any, *, workspace_id: str, incident_id: str,
                           event_id: str | None, correlation: dict[str, Any],
                           unreadable: list[str]) -> list[dict[str, Any]]:
    """Business-state artifacts: reconciliation, authoritative state, authorizations.

    This is the half of the case that answers "was it authorized?" — the transfer
    agent's expected supply, the authorized issuance the matcher searched for, and
    the deterministic reconciliation verdict. Read live (not from the snapshot),
    so each is content-hashed rather than sealed.
    """
    artifacts: list[dict[str, Any]] = []
    asset_id = correlation.get('asset_id')
    detection = correlation.get('detection') or {}

    if detection:
        artifacts.append(_artifact(
            artifact_id=f"detection:{detection.get('id')}",
            event_id=event_id, incident_id=incident_id,
            artifact_type='detection_record',
            file_name=f"detection_{str(detection.get('id'))[:8]}.json",
            source=_text(detection.get('telemetry_source')) or 'Decoda detection engine',
            collected_at=detection.get('detected_at'), payload=detection, sealed=False,
            metadata={
                'detection_type': _text(detection.get('detection_type')),
                'category': _text(detection.get('category')),
                'reason_code': _text(detection.get('deterministic_reason_code')),
                'operation': _text(detection.get('operation')),
                'observed_amount': detection.get('observed_amount'),
                'expected_amount': detection.get('expected_amount'),
                'variance_amount': detection.get('variance_amount'),
                'amount_decimals': detection.get('amount_decimals'),
                'amount_unit': _text(detection.get('amount_unit')),
                'evidence_source': _text(detection.get('evidence_source')),
                'link_scope': LINK_SCOPE_EVENT,
            },
        ))
        checks = detection.get('operational_checks')
        if isinstance(checks, dict) and checks:
            artifacts.append(_artifact(
                artifact_id=f"operational_checks:{detection.get('id')}",
                event_id=event_id, incident_id=incident_id,
                artifact_type='operational_check',
                file_name='operational_checks.json',
                source='Decoda operational integrity matcher',
                collected_at=detection.get('detected_at'), payload=checks, sealed=False,
                metadata={'matcher_reason_code': _text(detection.get('deterministic_reason_code')),
                          'link_scope': LINK_SCOPE_EVENT},
            ))

    if not asset_id:
        return artifacts

    if _table_exists(connection, 'asset_reconciliation_snapshots', unreadable):
        for row in _reconciliation_rows(
            connection, workspace_id=workspace_id, asset_id=asset_id,
            detection_id=_text(detection.get('id')), unreadable=unreadable,
        ):
            artifacts.append(_artifact(
                artifact_id=f"reconciliation:{row.get('id')}",
                event_id=event_id, incident_id=incident_id,
                artifact_type='reconciliation_output',
                file_name=f"reconciliation_{str(row.get('id'))[:8]}.json",
                source=_text(row.get('authoritative_source')) or 'Reconciliation engine',
                collected_at=row.get('evaluated_at'), payload=row, sealed=False,
                metadata={
                    'status': _text(row.get('status')),
                    'reason_code': _text(row.get('reason_code')),
                    'variance_units': row.get('variance_units'),
                    'rule_id': _text(row.get('rule_id')),
                    'rule_version': row.get('rule_version'),
                    'evidence_source': _text(row.get('evidence_source')),
                    'link_scope': row.get('link_scope'),
                },
            ))

    if _table_exists(connection, 'asset_authoritative_state', unreadable):
        for row in _read(
            connection,
            '''SELECT id, expected_total_supply, token_decimals, settlement_state, source_name,
                      source_kind, source_status, source_error, external_reference,
                      evidence_source, observed_at
               FROM asset_authoritative_state
               WHERE workspace_id = %s AND asset_id = %s::uuid
               ORDER BY observed_at DESC, id DESC
               LIMIT 3''',
            (workspace_id, asset_id), fact='authoritative_state', unreadable=unreadable,
        ):
            artifacts.append(_artifact(
                artifact_id=f"authoritative_state:{row.get('id')}",
                event_id=event_id, incident_id=incident_id,
                artifact_type='authoritative_state',
                file_name=f"authoritative_state_{str(row.get('id'))[:8]}.json",
                source=_text(row.get('source_name')) or _text(row.get('source_kind')) or 'System of record',
                collected_at=row.get('observed_at'), payload=row, sealed=False,
                metadata={
                    'settlement_state': _text(row.get('settlement_state')),
                    # A source that could not report is NOT a variance and is never
                    # rendered as one; the state is carried through verbatim.
                    'source_status': _text(row.get('source_status')),
                    'evidence_source': _text(row.get('evidence_source')),
                    # No event column exists on this table, so the link is the
                    # asset — stated rather than implied to be event-specific.
                    'link_scope': LINK_SCOPE_ASSET,
                },
            ))

    if _table_exists(connection, 'asset_authorized_issuances', unreadable):
        for row in _read(
            connection,
            '''SELECT id, operation, amount, settlement_state, external_reference,
                      authorized_at, evidence_source
               FROM asset_authorized_issuances
               WHERE workspace_id = %s AND asset_id = %s::uuid
               ORDER BY authorized_at DESC, id DESC
               LIMIT 5''',
            (workspace_id, asset_id), fact='authorized_issuance', unreadable=unreadable,
        ):
            artifacts.append(_artifact(
                artifact_id=f"authorized_issuance:{row.get('id')}",
                event_id=event_id, incident_id=incident_id,
                artifact_type='authorized_issuance',
                file_name=f"authorized_issuance_{str(row.get('id'))[:8]}.json",
                source='Transfer agent record',
                collected_at=row.get('authorized_at'), payload=row, sealed=False,
                metadata={
                    'operation': _text(row.get('operation')),
                    'settlement_state': _text(row.get('settlement_state')),
                    'external_reference': _text(row.get('external_reference')),
                    'link_scope': LINK_SCOPE_ASSET,
                },
            ))
    return artifacts


def _policy_evaluations(connection: Any, *, workspace_id: str, incident_id: str,
                        event_id: str | None, correlation: dict[str, Any],
                        unreadable: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The deterministic policy verdicts attached to this incident, plus their raw rows.

    The authoritative decision is whatever the deterministic engine PERSISTED. No
    explanation, AI or otherwise, is promoted into the decision field, and a
    simulation is labelled a simulation so a what-if can never read as an
    enforcement verdict.

    Returns ``(evaluations, rows)`` — the structured forensics view and the raw
    rows, so the artifact collector can hash the stored record itself while the
    timeline path takes only the structured view and pays for no digests.
    """
    evaluations: list[dict[str, Any]] = []
    if not _table_exists(connection, 'governance_policy_evaluations', unreadable):
        return evaluations, []

    asset_id = correlation.get('asset_id')
    rows = _read(
        connection,
        '''SELECT id, policy_id, policy_key, policy_version, decision, reason_codes,
                  required_approvals, checks, operation, amount_usd, simulation,
                  engine_version, canonical_event_id, asset_id, incident_id, evaluated_at
           FROM governance_policy_evaluations
           WHERE workspace_id = %s
             AND (incident_id = %s::uuid
                  OR (%s::text IS NOT NULL AND canonical_event_id = %s::text)
                  OR (%s::uuid IS NOT NULL AND asset_id = %s::uuid))
           ORDER BY evaluated_at DESC, id DESC
           LIMIT 20''',
        (workspace_id, incident_id, event_id, event_id, asset_id, asset_id),
        fact='policy_evaluation', unreadable=unreadable,
    )
    # HOW each row matched, resolved by Screen 8's OWN provenance function, so the
    # two screens never describe one evaluation's linkage differently. An
    # ASSET_SHARED row concerns the same asset but was NOT reached for this
    # incident, and saying so is the difference between evidence and coincidence.
    from services.api.app.domains.response_gate.service import evaluation_match_provenance
    for row in rows:
        evaluation_id = str(row.get('id'))
        reason_codes = row.get('reason_codes') if isinstance(row.get('reason_codes'), list) else []
        required_approvals = row.get('required_approvals') if isinstance(row.get('required_approvals'), list) else []
        simulation = bool(row.get('simulation'))
        match_provenance = evaluation_match_provenance(
            row, response_action_id=None, canonical_event_id=event_id,
            incident_id=incident_id, asset_id=asset_id,
        )
        evaluations.append({
            'evaluation_id': evaluation_id,
            'policy_id': _text(row.get('policy_id')),
            'policy_key': _text(row.get('policy_key')),
            'policy_version': row.get('policy_version'),
            'decision': _text(row.get('decision')),
            'reason_codes': [str(code) for code in reason_codes],
            'required_approvals': [str(role) for role in required_approvals],
            'operation': _text(row.get('operation')),
            'amount_usd': row.get('amount_usd'),
            # A Screen 11 what-if predicts; it never authorizes. The flag is
            # carried through so the UI can never present one as the verdict that
            # gated the response.
            'simulation': simulation,
            'engine_version': _text(row.get('engine_version')),
            'evaluated_at': _iso(row.get('evaluated_at')),
            'canonical_event_id': _text(row.get('canonical_event_id')),
            'authority': 'deterministic_policy_engine',
            'match_provenance': match_provenance,
        })
        evaluations[-1] = pilot._json_safe_value(evaluations[-1])
    return evaluations, rows


def _policy_artifacts(*, incident_id: str, event_id: str | None,
                      evaluations: list[dict[str, Any]],
                      rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Directory rows for the policy domain, hashed over the stored evaluation.

    Takes the rows ``_policy_evaluations`` already read, so the policy tables are
    queried once per request and the timeline path — which needs only the
    structured verdict — pays for no artifact digests.
    """
    artifacts: list[dict[str, Any]] = []
    by_id = {str(row.get('id')): row for row in rows}
    for evaluation in evaluations:
        evaluation_id = str(evaluation['evaluation_id'])
        row = by_id.get(evaluation_id)
        if row is None:
            continue
        simulation = bool(evaluation.get('simulation'))
        artifacts.append(_artifact(
            artifact_id=f'policy_evaluation:{evaluation_id}',
            event_id=event_id, incident_id=incident_id,
            artifact_type='policy_simulation_result' if simulation else 'policy_decision',
            file_name=f"policy_evaluation_{evaluation_id[:8]}.json",
            source='Deterministic policy engine',
            collected_at=row.get('evaluated_at'), payload=row, sealed=False,
            metadata={
                'policy_key': evaluation.get('policy_key'),
                'policy_version': evaluation.get('policy_version'),
                'decision': evaluation.get('decision'),
                'reason_codes': evaluation.get('reason_codes'),
                'required_approvals': evaluation.get('required_approvals'),
                'simulation': simulation,
                # Screen 8's own provenance verdict: EVENT_SHARED / INCIDENT_SHARED
                # name this incident's event; ASSET_SHARED does not.
                'match_provenance': evaluation.get('match_provenance'),
            },
        ))
        checks = row.get('checks')
        if isinstance(checks, list) and checks:
            artifacts.append(_artifact(
                artifact_id=f'policy_checks:{evaluation_id}',
                event_id=event_id, incident_id=incident_id,
                artifact_type='policy_evaluation_input',
                file_name=f"policy_checks_{evaluation_id[:8]}.json",
                source='Deterministic policy engine',
                collected_at=row.get('evaluated_at'), payload=checks, sealed=False,
                metadata={'check_count': len(checks),
                          'match_provenance': evaluation.get('match_provenance')},
            ))
    return artifacts


def _human_action_artifacts(connection: Any, *, workspace_id: str, incident_id: str,
                            event_id: str | None, unreadable: list[str]) -> list[dict[str, Any]]:
    """Human artifacts: approvals, rejections, and operator timeline entries.

    An AI recommendation is NEVER recorded here as a human action or as an
    executed one. Only a decision a person actually recorded, or a timeline entry
    an authenticated user caused, becomes a human-action artifact.
    """
    artifacts: list[dict[str, Any]] = []

    if _table_exists(connection, 'response_action_approvals', unreadable):
        for row in _read(
            connection,
            '''SELECT a.id, a.subject_domain, a.subject_id, a.action_version, a.approver_user_id,
                      a.approver_role, a.decision, a.note, a.required_quorum, a.policy, a.created_at
               FROM response_action_approvals a
               JOIN response_actions ra
                 ON ra.id = a.subject_id AND ra.workspace_id = a.workspace_id
               WHERE a.workspace_id = %s
                 AND a.subject_domain = 'response_action'
                 AND ra.incident_id = %s::uuid
               ORDER BY a.created_at ASC, a.id ASC
               LIMIT 100''',
            (workspace_id, incident_id), fact='response_action_approval', unreadable=unreadable,
        ):
            decision = str(row.get('decision') or '').lower()
            artifacts.append(_artifact(
                artifact_id=f"approval:{row.get('id')}",
                event_id=event_id, incident_id=incident_id,
                artifact_type='rejection_decision' if decision == 'rejected' else 'approval_decision',
                file_name=f"approval_{str(row.get('id'))[:8]}.json",
                source=_text(row.get('approver_role')) or 'Workspace approver',
                collected_at=row.get('created_at'), payload=row, sealed=False,
                metadata={
                    'decision': decision or None,
                    'approver_role': _text(row.get('approver_role')),
                    'response_action_id': _text(row.get('subject_id')),
                    'action_version': row.get('action_version'),
                    'required_quorum': row.get('required_quorum'),
                    'link_scope': LINK_SCOPE_INCIDENT,
                },
            ))

    if _table_exists(connection, 'incident_timeline', unreadable):
        for row in _read(
            connection,
            '''SELECT id, event_type, message, actor_user_id, metadata, created_at
               FROM incident_timeline
               WHERE workspace_id = %s AND incident_id = %s AND actor_user_id IS NOT NULL
               ORDER BY created_at ASC, id ASC
               LIMIT 200''',
            (workspace_id, incident_id), fact='incident_timeline_human', unreadable=unreadable,
        ):
            event_type = str(row.get('event_type') or '')
            artifacts.append(_artifact(
                artifact_id=f"timeline:{row.get('id')}",
                event_id=event_id, incident_id=incident_id,
                artifact_type=_human_artifact_type(event_type),
                file_name=f"human_action_{str(row.get('id'))[:8]}.json",
                source='Workspace operator',
                collected_at=row.get('created_at'), payload=row, sealed=False,
                metadata={
                    'event_type': event_type or None,
                    'actor_user_id': _text(row.get('actor_user_id')),
                    'message': _text(row.get('message')),
                    'link_scope': LINK_SCOPE_INCIDENT,
                },
            ))
    return artifacts


#: Persisted timeline event_type -> human artifact type. Explicit mapping, so a
#: new event type is classified only when it is deliberately added here.
_HUMAN_EVENT_ARTIFACT_TYPES: dict[str, str] = {
    'incident.created': 'incident_acknowledgement',
    'incident.status_changed': 'manual_status_transition',
    'incident.closed': 'manual_status_transition',
    'incident.recommendation': 'analyst_note',
    'incident.timeline_note_added': 'analyst_note',
    'alert.escalated': 'escalation_record',
    'response_action.created': 'response_action_request',
    'response_action.approved': 'approval_decision',
    'response_action.approval_recorded': 'approval_decision',
    'response_action.rejected': 'rejection_decision',
    'response_action.approval_attempt_blocked': 'compliance_decision',
}


def _human_artifact_type(event_type: str) -> str:
    return _HUMAN_EVENT_ARTIFACT_TYPES.get(event_type.strip().lower(), 'analyst_note')


# ---------------------------------------------------------------------------
# Forensic lifecycle timeline
# ---------------------------------------------------------------------------
#: Persisted event_type -> (lifecycle stage, domain, actor type). The stage names
#: mirror the canonical event flow the product already implements. An event with
#: no mapping still appears on the timeline with a ``None`` stage — a real record
#: is never hidden because it has not been categorised.
_TIMELINE_EVENT_STAGES: dict[str, tuple[str, str, str]] = {
    'alert.created': ('detection_raised', ON_CHAIN, 'system'),
    'alert.escalated': ('incident_created', HUMAN_ACTION, 'user'),
    'incident.created': ('incident_created', HUMAN_ACTION, 'user'),
    'incident.auto_created': ('incident_created', OPERATIONAL, 'system'),
    'evidence.linked': ('evidence_linked', ON_CHAIN, 'system'),
    'incident.status_changed': ('status_changed', HUMAN_ACTION, 'user'),
    'incident.closed': ('status_changed', HUMAN_ACTION, 'user'),
    'incident.recommendation': ('recommendation_recorded', HUMAN_ACTION, 'user'),
    'incident.forensic_investigation.rerun': ('analysis_rerun', OPERATIONAL, 'user'),
    'incident.forensic_report.generated': ('report_generated', OPERATIONAL, 'user'),
    'response_action.created': ('response_gated', HUMAN_ACTION, 'user'),
    'response_action.policy_enforcement_evaluated': ('policy_evaluated', POLICY, 'system'),
    'response_action.approval_recorded': ('approval_recorded', HUMAN_ACTION, 'user'),
    'response_action.approved': ('approval_granted', HUMAN_ACTION, 'user'),
    'response_action.rejected': ('approval_rejected', HUMAN_ACTION, 'user'),
    'response_action.approval_attempt_blocked': ('approval_blocked', HUMAN_ACTION, 'user'),
    'response_action.execution_gate_locked': ('execution_blocked', POLICY, 'system'),
    'response_action.execution_gate_authorized': ('execution_authorized', POLICY, 'system'),
    'response_action.manual_required': ('execution_manual_required', HUMAN_ACTION, 'system'),
    'response_action.unsupported': ('execution_unsupported', HUMAN_ACTION, 'system'),
    'response_action.rollback_created': ('rollback_created', HUMAN_ACTION, 'user'),
    'response_action.rollback_completed': ('rollback_completed', HUMAN_ACTION, 'user'),
    'response_action.rolled_back': ('rollback_completed', HUMAN_ACTION, 'user'),
}

#: Operator-facing stage titles. One backend mapping; React renders these.
TIMELINE_STAGE_LABELS: dict[str, str] = {
    'state_drift_detected': 'State drift detected',
    'operational_anomaly': 'Operational anomaly determined',
    'detection_raised': 'Detection raised',
    'policy_evaluated': 'Policy evaluated',
    'policy_decision': 'Policy decision returned',
    'response_gated': 'Response gated',
    'incident_created': 'Incident created',
    'evidence_linked': 'Evidence linked',
    'evidence_snapshot_created': 'Evidence snapshot created',
    'evidence_package_sealed': 'Evidence package sealed',
    'status_changed': 'Incident status changed',
    'recommendation_recorded': 'Recommendation recorded',
    'analysis_rerun': 'Forensic analysis re-run',
    'report_generated': 'Forensic report generated',
    'approval_recorded': 'Approval recorded',
    'approval_granted': 'Approval granted',
    'approval_rejected': 'Approval rejected',
    'approval_blocked': 'Approval attempt blocked',
    'execution_blocked': 'Execution blocked by gate',
    'execution_authorized': 'Execution authorized by gate',
    'execution_manual_required': 'Manual execution required',
    'execution_unsupported': 'Live execution unsupported',
    'rollback_created': 'Rollback created',
    'rollback_completed': 'Rollback completed',
}


def _timeline_event(*, event_id: str, incident_id: str, correlation_id: str | None,
                    occurred_at: Any, event_type: str, stage: str | None, domain: str | None,
                    source: str, title: str, description: str | None,
                    actor_type: str, actor_id: str | None,
                    related_entity_type: str | None = None, related_entity_id: str | None = None,
                    metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return pilot._json_safe_value({
        'id': event_id,
        'incident_id': incident_id,
        'event_id': correlation_id,
        'occurred_at': _iso(occurred_at),
        'event_type': event_type,
        'stage': stage,
        'stage_label': TIMELINE_STAGE_LABELS.get(stage or '', None),
        'domain': domain,
        'source': source,
        'title': title,
        'description': description,
        'actor_type': actor_type,
        'actor_id': actor_id,
        'related_entity_type': related_entity_type,
        'related_entity_id': related_entity_id,
        'metadata': metadata or {},
    })


def build_forensic_timeline(*, incident_id: str, correlation: dict[str, Any],
                            timeline_rows: list[dict[str, Any]],
                            reconciliations: list[dict[str, Any]],
                            evaluations: list[dict[str, Any]],
                            snapshot_row: dict[str, Any],
                            package: dict[str, Any]) -> list[dict[str, Any]]:
    """The deterministic lifecycle, assembled only from records that EXIST.

    A stage the reference design shows but this incident has no record for is
    simply absent. Nothing is back-filled to make the flow look complete, and no
    timestamp is estimated: every entry carries the canonical server timestamp
    the source row was written with.
    """
    correlation_id = correlation.get('event_id')
    detection = correlation.get('detection') or {}
    events: list[dict[str, Any]] = []

    # 1-2. Chain observation, then the operational anomaly the matcher determined.
    if detection:
        detection_id = str(detection.get('id'))
        preconfirmed = detection.get('preconfirmation_received_at')
        if preconfirmed:
            events.append(_timeline_event(
                event_id=f'detection_preconfirmation:{detection_id}', incident_id=incident_id,
                correlation_id=correlation_id, occurred_at=preconfirmed,
                event_type='chain.preconfirmation_received', stage='state_drift_detected',
                domain=ON_CHAIN,
                source=_text(detection.get('telemetry_source')) or 'Chain telemetry',
                title='Preconfirmation received',
                description=_text(detection.get('telemetry_stage')),
                actor_type='system', actor_id=None,
                related_entity_type='threat_detection', related_entity_id=detection_id,
                metadata={'telemetry_stage': _text(detection.get('telemetry_stage'))},
            ))
        observed = detection.get('telemetry_observed_at')
        if observed:
            events.append(_timeline_event(
                event_id=f'detection_observed:{detection_id}', incident_id=incident_id,
                correlation_id=correlation_id, occurred_at=observed,
                event_type='chain.event_observed', stage='state_drift_detected', domain=ON_CHAIN,
                source=_text(detection.get('telemetry_source')) or 'Chain telemetry',
                title='On-chain event observed',
                description=_text(detection.get('title')),
                actor_type='system', actor_id=None,
                related_entity_type='threat_detection', related_entity_id=detection_id,
                metadata={'tx_hash': _text(detection.get('tx_hash')),
                          'block_number': detection.get('block_number')},
            ))
        events.append(_timeline_event(
            event_id=f'detection:{detection_id}', incident_id=incident_id,
            correlation_id=correlation_id, occurred_at=detection.get('detected_at'),
            event_type='detection.recorded', stage='operational_anomaly',
            domain=OPERATIONAL if str(detection.get('category') or '') == 'OPERATIONAL_INTEGRITY' else ON_CHAIN,
            source='Decoda detection engine',
            title=_text(detection.get('title')) or 'Detection recorded',
            description=_text(detection.get('deterministic_reason_code')),
            actor_type='system', actor_id=None,
            related_entity_type='threat_detection', related_entity_id=detection_id,
            metadata={'detection_type': _text(detection.get('detection_type')),
                      'category': _text(detection.get('category')),
                      'reason_code': _text(detection.get('deterministic_reason_code'))},
        ))

    # 3. Reconciliation: the authoritative lookup and its deterministic verdict.
    for row in reconciliations:
        reconciliation_id = str(row.get('id'))
        events.append(_timeline_event(
            event_id=f'reconciliation:{reconciliation_id}', incident_id=incident_id,
            correlation_id=correlation_id, occurred_at=row.get('evaluated_at'),
            event_type='reconciliation.evaluated', stage='operational_anomaly', domain=OPERATIONAL,
            source=_text(row.get('authoritative_source')) or 'Reconciliation engine',
            title='Reconciliation evaluated',
            description=_text(row.get('reason_code')),
            actor_type='system', actor_id=None,
            related_entity_type='asset_reconciliation_snapshot', related_entity_id=reconciliation_id,
            metadata={'status': _text(row.get('status')), 'reason_code': _text(row.get('reason_code')),
                      'rule_id': _text(row.get('rule_id')), 'rule_version': row.get('rule_version')},
        ))

    # 4. Policy evaluation and the decision it returned.
    for evaluation in evaluations:
        events.append(_timeline_event(
            event_id=f"policy_evaluation:{evaluation['evaluation_id']}", incident_id=incident_id,
            correlation_id=correlation_id, occurred_at=evaluation.get('evaluated_at'),
            event_type='policy.evaluated',
            # A simulation is a prediction, not the verdict that gated anything.
            stage='policy_evaluated' if evaluation.get('simulation') else 'policy_decision',
            domain=POLICY, source='Deterministic policy engine',
            title=(
                f"Policy {evaluation.get('policy_key') or 'evaluation'} "
                f"{'simulated' if evaluation.get('simulation') else 'evaluated'}"
            ),
            description=f"Decision: {evaluation.get('decision')}" if evaluation.get('decision') else None,
            actor_type='system', actor_id=None,
            related_entity_type='governance_policy_evaluation',
            related_entity_id=evaluation['evaluation_id'],
            metadata={'decision': evaluation.get('decision'),
                      'reason_codes': evaluation.get('reason_codes'),
                      'policy_version': evaluation.get('policy_version'),
                      'simulation': evaluation.get('simulation')},
        ))

    # 5. The persisted incident timeline (append-only): creation, gating, human decisions.
    for row in timeline_rows:
        row_id = str(row.get('id'))
        event_type = str(row.get('event_type') or '')
        stage, domain, actor_type = _TIMELINE_EVENT_STAGES.get(
            event_type.strip().lower(), (None, None, 'system'),
        )
        actor_id = _text(row.get('actor_user_id'))
        metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
        events.append(_timeline_event(
            event_id=f'incident_timeline:{row_id}', incident_id=incident_id,
            correlation_id=correlation_id, occurred_at=row.get('created_at'),
            event_type=event_type or 'incident.event', stage=stage, domain=domain,
            source='Decoda platform',
            title=TIMELINE_STAGE_LABELS.get(stage or '', None) or _text(row.get('message')) or 'Incident event',
            description=_text(row.get('message')),
            # An actor_user_id is what makes an event a HUMAN action. Without one
            # it stays a system event, whatever its type suggests.
            actor_type=actor_type if actor_id else 'system',
            actor_id=actor_id,
            related_entity_type='incident_timeline', related_entity_id=row_id,
            metadata=metadata,
        ))

    # 6. Evidence snapshot creation — real only when a snapshot row exists.
    if snapshot_row:
        events.append(_timeline_event(
            event_id=f"evidence_snapshot:{snapshot_row.get('id')}", incident_id=incident_id,
            correlation_id=correlation_id, occurred_at=snapshot_row.get('created_at'),
            event_type='evidence.snapshot_created', stage='evidence_snapshot_created',
            domain=OPERATIONAL, source='Decoda evidence service',
            title='Evidence snapshot created',
            description=_text(snapshot_row.get('snapshot_hash')),
            actor_type='system', actor_id=None,
            related_entity_type='incident_evidence_snapshot',
            related_entity_id=str(snapshot_row.get('id')),
            metadata={'snapshot_hash': _text(snapshot_row.get('snapshot_hash')),
                      'evidence_count': snapshot_row.get('evidence_count')},
        ))

    # 7. Sealing — recorded only when Screen 9 confirms a sealed package.
    if package.get('available') and package.get('sealed_at'):
        events.append(_timeline_event(
            event_id=f"evidence_package:{package.get('package_id')}", incident_id=incident_id,
            correlation_id=correlation_id, occurred_at=package.get('sealed_at'),
            event_type='evidence.package_sealed', stage='evidence_package_sealed',
            domain=OPERATIONAL, source='Decoda evidence packaging',
            title='Evidence package sealed',
            description=_text(package.get('package_number')),
            actor_type='system', actor_id=None,
            related_entity_type='evidence_package', related_entity_id=_text(package.get('package_id')),
            metadata={'package_number': package.get('package_number'),
                      'integrity_status': package.get('integrity_status')},
        ))

    return sort_timeline_events(events)


# ---------------------------------------------------------------------------
# Route implementations
# ---------------------------------------------------------------------------
def get_incident_evidence(incident_id: str, request: Any) -> dict[str, Any]:
    """Forensic evidence directory for one incident: four domains + integrity state.

    Workspace-scoped and read-only. Counts are derived from the artifact list that
    is returned, so they can never disagree with it, and a domain with no records
    reports zero rather than borrowing from another.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(
            connection, user['id'], request.headers.get('x-workspace-id'),
        )
        workspace_id = workspace_context['workspace_id']
        incident = _require_incident(connection, workspace_id=workspace_id, incident_id=incident_id)

        unreadable: list[str] = []
        correlation = resolve_correlation(
            connection, workspace_id=workspace_id, incident=incident, unreadable=unreadable,
        )
        event_id = correlation['event_id']
        header = incident_header(
            connection, workspace_id=workspace_id, incident=incident,
            correlation=correlation, unreadable=unreadable,
        )

        snapshot_row = _latest_snapshot(
            connection, workspace_id=workspace_id, incident_id=incident_id, unreadable=unreadable,
        )
        hash_verified = verify_snapshot_hash(snapshot_row)
        # Only a snapshot whose hash RE-COMPUTES licenses "sealed" on its rows.
        snapshot_sealed = hash_verified is True
        package = _evidence_package(
            connection, workspace_id=workspace_id, incident_id=incident_id, unreadable=unreadable,
        )

        artifacts: list[dict[str, Any]] = []
        artifacts.extend(_onchain_artifacts(
            incident_id=incident_id, event_id=event_id,
            snapshot_row=snapshot_row, snapshot_sealed=snapshot_sealed,
        ))
        artifacts.extend(_operational_artifacts(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            event_id=event_id, correlation=correlation, unreadable=unreadable,
        ))
        evaluations, evaluation_rows = _policy_evaluations(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            event_id=event_id, correlation=correlation, unreadable=unreadable,
        )
        artifacts.extend(_policy_artifacts(
            incident_id=incident_id, event_id=event_id,
            evaluations=evaluations, rows=evaluation_rows,
        ))
        artifacts.extend(_human_action_artifacts(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            event_id=event_id, unreadable=unreadable,
        ))

        ordered = sort_artifacts(artifacts)
        truncated = len(ordered) > ARTIFACT_CAP
        page = ordered[:ARTIFACT_CAP]
        return {
            'incident_id': incident_id,
            'event_id': event_id,
            'incident': header,
            'counts': count_domains(page),
            'domains': [
                {'domain': domain, 'label': DOMAIN_LABELS[domain], 'count_key': DOMAIN_COUNT_KEYS[domain]}
                for domain in EVIDENCE_DOMAINS
            ],
            'snapshot': {
                'status': snapshot_state(
                    snapshot_row=snapshot_row, hash_verified=hash_verified, package=package,
                ),
                'snapshot_id': _text(snapshot_row.get('id')) if snapshot_row else None,
                'snapshot_hash': _text(snapshot_row.get('snapshot_hash')) if snapshot_row else None,
                'hash_verified': hash_verified,
                'schema_version': _text(snapshot_row.get('schema_version')) if snapshot_row else None,
                'evidence_count': snapshot_row.get('evidence_count') if snapshot_row else None,
                'is_complete': snapshot_row.get('is_complete') if snapshot_row else None,
                'created_at': _iso(snapshot_row.get('created_at')) if snapshot_row else None,
            },
            'evidence_package': pilot._json_safe_value(package),
            'policy_evaluations': evaluations,
            'artifacts': page,
            'truncated': truncated,
            # A read that FAILED is named here. The client renders a partial-evidence
            # warning rather than presenting an incomplete directory as complete.
            'unreadable': unreadable,
            'partial': bool(unreadable),
            'label': 'Deterministic forensic evidence directory — collected records only.',
        }


def get_forensic_timeline(incident_id: str, request: Any) -> dict[str, Any]:
    """The incident's deterministic lifecycle events, oldest-first.

    Ordering uses canonical server timestamps at their persisted precision. Only
    stages with a real record appear.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(
            connection, user['id'], request.headers.get('x-workspace-id'),
        )
        workspace_id = workspace_context['workspace_id']
        incident = _require_incident(connection, workspace_id=workspace_id, incident_id=incident_id)
        return _timeline_payload(
            connection, workspace_id=workspace_id, incident_id=incident_id, incident=incident,
        )


def _timeline_payload(connection: Any, *, workspace_id: str, incident_id: str,
                      incident: dict[str, Any],
                      timeline_rows: list[dict[str, Any]] | None = None,
                      unreadable: list[str] | None = None) -> dict[str, Any]:
    """Assemble the forensic timeline payload on an already-authorized connection.

    ``timeline_rows`` may be supplied by a caller that has already read them
    (oldest-first), so the combined timeline endpoint reads ``incident_timeline``
    once rather than twice.
    """
    unreadable = [] if unreadable is None else unreadable
    correlation = resolve_correlation(
        connection, workspace_id=workspace_id, incident=incident, unreadable=unreadable,
    )
    if timeline_rows is None:
        timeline_rows = _read(
            connection,
            '''SELECT id, event_type, message, actor_user_id, metadata, created_at
               FROM incident_timeline
               WHERE workspace_id = %s AND incident_id = %s
               ORDER BY created_at ASC, id ASC
               LIMIT 500''',
            (workspace_id, incident_id), fact='incident_timeline', unreadable=unreadable,
        ) if _table_exists(connection, 'incident_timeline', unreadable) else []

    reconciliations: list[dict[str, Any]] = []
    asset_id = correlation.get('asset_id')
    if asset_id and _table_exists(connection, 'asset_reconciliation_snapshots', unreadable):
        reconciliations = _read(
            connection,
            '''SELECT id, status, reason_code, rule_id, rule_version, authoritative_source,
                      evaluated_at
               FROM asset_reconciliation_snapshots
               WHERE workspace_id = %s AND asset_id = %s::uuid
               ORDER BY evaluated_at DESC, id DESC
               LIMIT 5''',
            (workspace_id, asset_id), fact='reconciliation_snapshot', unreadable=unreadable,
        )

    evaluations, _ = _policy_evaluations(
        connection, workspace_id=workspace_id, incident_id=incident_id,
        event_id=correlation['event_id'], correlation=correlation, unreadable=unreadable,
    )
    snapshot_row = _latest_snapshot(
        connection, workspace_id=workspace_id, incident_id=incident_id, unreadable=unreadable,
    )
    package = _evidence_package(
        connection, workspace_id=workspace_id, incident_id=incident_id, unreadable=unreadable,
    )
    events = build_forensic_timeline(
        incident_id=incident_id, correlation=correlation, timeline_rows=timeline_rows,
        reconciliations=reconciliations, evaluations=evaluations,
        snapshot_row=snapshot_row, package=package,
    )
    return {
        'incident_id': incident_id,
        'event_id': correlation['event_id'],
        'events': events,
        'unreadable': unreadable,
        'partial': bool(unreadable),
    }


def get_incident_timeline(incident_id: str, request: Any) -> dict[str, Any]:
    """The timeline endpoint's payload: the legacy projection PLUS the forensic view.

    ADDITIVE. ``timeline`` keeps the exact shape and newest-first order the drawer
    and the case-file tabs already read, so every existing consumer is unaffected;
    ``events`` adds the Screen 7 lifecycle beside it. Both are built from ONE
    authenticated, workspace-scoped connection and ONE read of
    ``incident_timeline`` — Screen 7 gains the lifecycle without a second round
    trip or a second authentication.

    If the forensic assembly fails, the legacy timeline is still returned and the
    failure is named in ``unreadable`` — a degraded forensic view never takes the
    timeline endpoint down with it.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(
            connection, user['id'], request.headers.get('x-workspace-id'),
        )
        workspace_id = workspace_context['workspace_id']
        incident = _require_incident(connection, workspace_id=workspace_id, incident_id=incident_id)

        unreadable: list[str] = []
        rows = _read(
            connection,
            '''SELECT id, incident_id, event_type, message, actor_user_id, metadata, created_at
               FROM incident_timeline
               WHERE workspace_id = %s AND incident_id = %s
               ORDER BY created_at ASC, id ASC
               LIMIT 500''',
            (workspace_id, incident_id), fact='incident_timeline', unreadable=unreadable,
        ) if _table_exists(connection, 'incident_timeline', unreadable) else []
        # The legacy projection is newest-first, exactly as before.
        legacy = [pilot._json_safe_value(dict(row)) for row in reversed(rows)]

        try:
            forensic = _timeline_payload(
                connection, workspace_id=workspace_id, incident_id=incident_id,
                incident=incident, timeline_rows=rows, unreadable=unreadable,
            )
        except HTTPException:
            raise
        except Exception:
            logger.warning('incident_forensics_timeline_failed incident_id=%s',
                           incident_id, exc_info=True)
            unreadable.append('forensic_timeline')
            forensic = {'event_id': None, 'events': []}

        return {
            'incident_id': incident_id,
            'timeline': legacy,
            'event_id': forensic.get('event_id'),
            'events': forensic.get('events') or [],
            'unreadable': unreadable,
            'partial': bool(unreadable),
        }
