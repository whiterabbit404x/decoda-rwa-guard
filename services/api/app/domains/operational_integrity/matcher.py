"""The deterministic Operational Matcher (pure — no DB, no network, no AI).

This is the module that answers the question Screen 5 exists to answer:

    The chain accepted this transaction. Did the BUSINESS authorize it?

Pipeline (all deterministic, all integer/Decimal arithmetic):

    normalized on-chain event
        -> find candidate authorizations for the asset + operation
        -> compare amount, business reference, settlement state, time window
        -> classify the outcome into structured PASS / FAIL / UNKNOWN checks
        -> map to a detection type + reason code + severity

The authorization search itself reuses the Screen 3 matcher
(``domains.asset_integrity.reconciliation.match_authorization``) rather than
re-implementing it, so a mint judged unauthorized on Screen 3 is judged
unauthorized here, under the same rules, with the same reason code.

Fail-closed rules encoded here:

  * A missing / unavailable / stale authoritative source yields UNKNOWN checks
    and an INDETERMINATE conclusion. It NEVER yields "no authorized issuance".
  * An operation that could not be decoded is INDETERMINATE, never an anomaly.
  * Severity comes from configuration (config.severity_for), never from the
    evidence text and never from a model.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any, Optional, Sequence

from services.api.app.domains.asset_integrity import reconciliation as recon
from services.api.app.domains.operational_integrity import config as oic
from services.api.app.domains.operational_integrity import normalization, schemas

# Authoritative-source states that cannot support a verdict.
_UNUSABLE_SOURCE_STATES = frozenset({'unavailable', 'error', 'missing'})


def _check(key: str, status: str, reason: str, source: Optional[str] = None) -> schemas.OperationalCheck:
    return schemas.OperationalCheck(key=key, status=status, reason=reason, source=source)


def _authoritative_source_state(authoritative: Optional[dict[str, Any]], *, now: Any, config: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Classify the authoritative source: ('ok'|'missing'|'unavailable'|'stale', name)."""
    if not authoritative:
        return 'missing', None
    name = str(authoritative.get('source_name') or '') or None
    status = str(authoritative.get('source_status') or 'reported').strip().lower()
    if status in _UNUSABLE_SOURCE_STATES:
        return ('missing' if status == 'missing' else 'unavailable'), name
    age = recon.age_seconds(authoritative.get('observed_at'), now)
    if age is not None and age > int(config['authoritative_stale_seconds']):
        return 'stale', name
    return 'ok', name


def _settlement_check(match: recon.MatchResult, candidates: Sequence[recon.AuthorizedIssuance], source_name: Optional[str]) -> schemas.OperationalCheck:
    """Settlement is its OWN fact, evaluated independently of the amount match.

    A settled-but-wrong-amount authorization still proves settlement ran; an
    amount-matching authorization stuck in 'pending' fails settlement even though
    the transfer agent knows about the operation."""
    if match.outcome == recon.MATCH and match.matched is not None:
        return _check(
            schemas.CHECK_SETTLEMENT_MATCH, schemas.PASS,
            f'Settlement {str(match.matched.settlement_state or "cleared").lower()}',
            source_name,
        )
    settled = [c for c in candidates if recon.is_settled(c.settlement_state)]
    if not candidates:
        return _check(schemas.CHECK_SETTLEMENT_MATCH, schemas.FAIL, 'No matching settlement', source_name)
    if not settled:
        return _check(schemas.CHECK_SETTLEMENT_MATCH, schemas.FAIL, 'Settlement not cleared', source_name)
    return _check(schemas.CHECK_SETTLEMENT_MATCH, schemas.FAIL, 'No matching settlement', source_name)


def _detection_type_for(reason_code: str) -> str:
    """Map a deterministic reason code to its canonical detection type.

    'No authorization exists at all' and 'an authorization exists but does not
    reconcile' are materially different findings for an operator, so they are
    different detection types rather than one bucket."""
    if reason_code in (oic.NO_MATCHING_AUTHORIZED_ISSUANCE, oic.NO_MATCHING_AUTHORIZED_REDEMPTION):
        return oic.UNMATCHED_ISSUANCE
    if reason_code in (oic.AMOUNT_MISMATCH, oic.REFERENCE_MISMATCH, oic.OUTSIDE_AUTHORIZED_WINDOW):
        return oic.TRANSFER_AGENT_MISMATCH
    if reason_code in (oic.SETTLEMENT_NOT_COMPLETE, oic.SETTLEMENT_DEADLINE_EXCEEDED):
        return oic.SETTLEMENT_TIMEOUT
    return oic.UNMATCHED_ISSUANCE


def to_authorized_issuance(row: dict[str, Any]) -> recon.AuthorizedIssuance:
    """Adapt a stored ``asset_authorized_issuances`` row to the shared matcher."""
    return recon.AuthorizedIssuance(
        id=str(row['id']) if row.get('id') else None,
        operation=str(row.get('operation') or 'mint'),
        amount=recon.to_units(row.get('amount')),
        settlement_state=str(row.get('settlement_state') or 'pending'),
        external_reference=row.get('external_reference'),
        authorized_at=row.get('authorized_at'),
        effective_from=row.get('effective_from'),
        effective_until=row.get('effective_until'),
        source_name=row.get('source_name'),
        evidence_source=str(row.get('evidence_source') or 'live'),
    )


def evaluate_issuance(
    *,
    workspace_id: str,
    event: normalization.NormalizedChainEvent,
    authoritative: Optional[dict[str, Any]],
    authorizations: Sequence[dict[str, Any]],
    now: Any,
    config: dict[str, Any] | None = None,
    asset_name: Optional[str] = None,
) -> schemas.OperationalIntegrityEvent:
    """Reconcile ONE observed issuance/redemption against authoritative records.

    Returns the canonical event regardless of outcome — an authorized mint
    produces an OPERATIONALLY_AUTHORIZED event that is simply never persisted as
    a detection. The caller decides what to store; this function decides what is
    true.
    """
    # All arithmetic below (magnitudes, variances, and the shared authorization
    # matcher's amount comparison) runs at uint256 precision, so a 78-digit token
    # amount is never silently rounded into a wrong reconciliation value.
    with localcontext(normalization.exact_context()):
        return _evaluate_issuance(
            workspace_id=workspace_id, event=event, authoritative=authoritative,
            authorizations=authorizations, now=now, config=config, asset_name=asset_name,
        )


def _evaluate_issuance(
    *,
    workspace_id: str,
    event: normalization.NormalizedChainEvent,
    authoritative: Optional[dict[str, Any]],
    authorizations: Sequence[dict[str, Any]],
    now: Any,
    config: dict[str, Any] | None = None,
    asset_name: Optional[str] = None,
) -> schemas.OperationalIntegrityEvent:
    cfg = config or oic.engine_config()
    checks: dict[str, schemas.OperationalCheck] = {}

    # --- 1. On-chain event -------------------------------------------------
    operation = str(event.decoded_operation or '').strip().lower() or None
    if operation in ('mint', 'burn') and event.amount is not None:
        checks[schemas.CHECK_ON_CHAIN_EVENT] = _check(
            schemas.CHECK_ON_CHAIN_EVENT, schemas.PASS,
            f'{operation.capitalize()} observed',
            normalization.source_label(event.source),
        )
    else:
        checks[schemas.CHECK_ON_CHAIN_EVENT] = _check(
            schemas.CHECK_ON_CHAIN_EVENT, schemas.UNKNOWN,
            'Operation could not be decoded from the observed event',
            normalization.source_label(event.source),
        )

    # --- 2. Signer validity ------------------------------------------------
    # The chain accepted and included the transaction. Recording this as PASS is
    # the whole argument of the screen: cryptographic validity is not authority.
    checks[schemas.CHECK_SIGNER_VALIDITY] = _check(
        schemas.CHECK_SIGNER_VALIDITY,
        schemas.PASS if event.signature_valid else schemas.UNKNOWN,
        'Cryptographically valid' if event.signature_valid else 'No transaction reference to verify',
        f'chain {event.chain_id}' if event.chain_id else 'chain',
    )

    magnitude = None if event.amount is None else abs(event.amount)

    def build(
        *, reason_code: str, detection_type: str, status: str,
        expected: Optional[Decimal], matched_reference: Optional[str] = None,
    ) -> schemas.OperationalIntegrityEvent:
        severity = oic.severity_for(detection_type, cfg)
        conclusion = schemas.conclusion_from(checks, severity)
        # An indeterminate conclusion is a data-quality state, never a critical
        # integrity claim.
        if conclusion == schemas.CONCLUSION_INDETERMINATE:
            severity = 'medium'
        variance = None
        if magnitude is not None and expected is not None:
            variance = magnitude - expected
        return schemas.OperationalIntegrityEvent(
            workspace_id=workspace_id,
            asset_id=event.asset_id,
            category=oic.CATEGORY_OPERATIONAL_INTEGRITY,
            detection_type=detection_type,
            severity=severity,
            status=status,
            deterministic_reason_code=reason_code,
            confidence=Decimal(str(cfg['deterministic_confidence'])),
            checks=dict(checks),
            conclusion=conclusion,
            event_type='MINT' if operation == 'mint' else ('BURN' if operation == 'burn' else None),
            chain_id=event.chain_id,
            operation=operation,
            observed_amount=magnitude,
            expected_amount=expected,
            variance_amount=variance,
            amount_decimals=event.token_decimals,
            amount_unit=event.token_symbol,
            tx_hash=event.tx_hash,
            block_number=event.block_number,
            telemetry_source=event.source,
            telemetry_stage=event.stage,
            telemetry_observed_at=event.observed_at,
            preconfirmation_received_at=(
                event.preconfirmation_received_at
                if event.stage == schemas.STAGE_PRECONFIRMATION else None
            ),
            external_reference=event.external_reference,
            matched_authorization_reference=matched_reference,
            matcher_version=str(cfg['matcher_version']),
            evidence_source=event.evidence_source,
            first_seen_at=event.observed_at,
            provenance={
                'telemetry_id': event.telemetry_id,
                'telemetry_source': event.source,
                'telemetry_stage': event.stage,
                'chain_id': event.chain_id,
                'tx_hash': event.tx_hash,
                'block_number': event.block_number,
                'observed_at': schemas._iso(event.observed_at),
                'decoder': 'operational-integrity-normalizer-v1',
                'matcher': str(cfg['matcher_version']),
                'authoritative_source': str((authoritative or {}).get('source_name') or '') or None,
                'asset_name': asset_name,
                'evidence_source': event.evidence_source,
            },
        )

    # --- 3. Can the operation even be reconciled? --------------------------
    if operation not in ('mint', 'burn') or magnitude is None:
        checks[schemas.CHECK_TRANSFER_AGENT_MATCH] = _check(
            schemas.CHECK_TRANSFER_AGENT_MATCH, schemas.UNKNOWN,
            'Not evaluated — the on-chain operation was not decoded',
        )
        checks[schemas.CHECK_SETTLEMENT_MATCH] = _check(
            schemas.CHECK_SETTLEMENT_MATCH, schemas.UNKNOWN,
            'Not evaluated — the on-chain operation was not decoded',
        )
        return build(
            reason_code=oic.OPERATION_NOT_DECODED, detection_type=oic.UNMATCHED_ISSUANCE,
            status='indeterminate', expected=None,
        )

    # --- 4. Authoritative source usable? -----------------------------------
    source_state, source_name = _authoritative_source_state(authoritative, now=now, config=cfg)
    if source_state != 'ok':
        reason = {
            'missing': (oic.AUTHORITATIVE_SOURCE_MISSING, 'No authoritative source is recorded for this asset'),
            'unavailable': (oic.AUTHORITATIVE_SOURCE_UNAVAILABLE, 'The authoritative source did not return a usable state'),
            'stale': (oic.AUTHORITATIVE_SOURCE_STALE, 'The authoritative state is older than the configured freshness threshold'),
        }[source_state]
        checks[schemas.CHECK_TRANSFER_AGENT_MATCH] = _check(
            schemas.CHECK_TRANSFER_AGENT_MATCH, schemas.UNKNOWN, reason[1], source_name,
        )
        checks[schemas.CHECK_SETTLEMENT_MATCH] = _check(
            schemas.CHECK_SETTLEMENT_MATCH, schemas.UNKNOWN, reason[1], source_name,
        )
        return build(
            reason_code=reason[0], detection_type=oic.UNMATCHED_ISSUANCE,
            status='indeterminate', expected=None,
        )

    # --- 5. The authorization search ---------------------------------------
    candidates = [to_authorized_issuance(row) for row in authorizations]
    same_operation = [c for c in candidates if str(c.operation or '').lower() == operation]
    rules = recon.ReconciliationRules(
        rule_id=str(cfg['matcher_version']),
        rule_version=1,
        variance_tolerance_units=Decimal(str(cfg['amount_tolerance_units'])),
        match_window_seconds=int(cfg['match_window_seconds']),
        authoritative_stale_seconds=int(cfg['authoritative_stale_seconds']),
    )
    match = recon.match_authorization(
        operation=operation,
        amount=magnitude,
        event_reference=event.external_reference,
        event_at=event.observed_at,
        candidates=candidates,
        rules=rules,
    )

    if match.outcome == recon.MATCH and match.matched is not None:
        authorized_amount = recon.to_units(match.matched.amount)
        checks[schemas.CHECK_TRANSFER_AGENT_MATCH] = _check(
            schemas.CHECK_TRANSFER_AGENT_MATCH, schemas.PASS,
            'Authorized issuance on record' if operation == 'mint' else 'Authorized redemption on record',
            match.matched.source_name or source_name,
        )
        checks[schemas.CHECK_SETTLEMENT_MATCH] = _settlement_check(match, same_operation, source_name)
        return build(
            reason_code=(
                oic.MATCHED_AUTHORIZED_ISSUANCE if operation == 'mint' else oic.MATCHED_AUTHORIZED_REDEMPTION
            ),
            detection_type=oic.UNMATCHED_ISSUANCE,
            status='authorized',
            expected=authorized_amount,
            matched_reference=match.matched.external_reference,
        )

    # No match. The reason code names the CLOSEST candidate's failure, so
    # "the amount was wrong" is never flattened into "nothing was authorized".
    reason_code = str(match.reason_code or oic.NO_MATCHING_AUTHORIZED_ISSUANCE)
    detection_type = _detection_type_for(reason_code)
    if reason_code in (oic.NO_MATCHING_AUTHORIZED_ISSUANCE, oic.NO_MATCHING_AUTHORIZED_REDEMPTION):
        agent_reason = 'No authorized issuance' if operation == 'mint' else 'No authorized redemption'
    elif reason_code == oic.AMOUNT_MISMATCH:
        agent_reason = 'Authorized amount does not match the observed amount'
    elif reason_code == oic.REFERENCE_MISMATCH:
        agent_reason = 'Business reference does not match the authorization record'
    elif reason_code == oic.OUTSIDE_AUTHORIZED_WINDOW:
        agent_reason = 'Authorization falls outside its permitted window'
    elif reason_code == oic.SETTLEMENT_NOT_COMPLETE:
        agent_reason = 'Authorization on record, settlement not complete'
    else:
        agent_reason = 'No authorized issuance'

    checks[schemas.CHECK_TRANSFER_AGENT_MATCH] = _check(
        schemas.CHECK_TRANSFER_AGENT_MATCH,
        # A settlement-only failure means the transfer agent DID authorize the
        # operation — reporting that check as FAIL would misstate the evidence.
        schemas.PASS if reason_code == oic.SETTLEMENT_NOT_COMPLETE else schemas.FAIL,
        agent_reason,
        source_name,
    )
    checks[schemas.CHECK_SETTLEMENT_MATCH] = _settlement_check(match, same_operation, source_name)

    # Expected amount is what the authoritative source authorized for this
    # operation: zero when it authorized nothing at all.
    if reason_code in (oic.NO_MATCHING_AUTHORIZED_ISSUANCE, oic.NO_MATCHING_AUTHORIZED_REDEMPTION) and not same_operation:
        expected = Decimal('0')
    else:
        amounts = [recon.to_units(c.amount) for c in same_operation]
        usable = [a for a in amounts if a is not None]
        expected = max(usable, key=lambda a: abs(a)) if usable else Decimal('0')

    return build(
        reason_code=reason_code, detection_type=detection_type,
        status='anomaly', expected=expected,
    )


def evaluate_settlement_deadline(
    *,
    workspace_id: str,
    authorization: dict[str, Any],
    asset_id: Optional[str],
    asset_name: Optional[str],
    now: Any,
    config: dict[str, Any] | None = None,
    deadline_seconds: Optional[int] = None,
) -> Optional[schemas.OperationalIntegrityEvent]:
    """SETTLEMENT_TIMEOUT: an authorized operation whose settlement window has
    passed without clearing.

    Returns None when the authorization is settled or the deadline has not
    passed. The deadline is taken from the authorization's own
    ``effective_until`` when it has one, then from the caller-supplied
    asset/workspace policy value, and only then from the global default — a
    global constant is the last resort, never the rule.
    """
    cfg = config or oic.engine_config()
    if recon.is_settled(authorization.get('settlement_state')):
        return None

    authorized_at = authorization.get('authorized_at')
    effective_until = authorization.get('effective_until')
    now_epoch = recon._epoch(now)
    if now_epoch is None:
        return None

    deadline_epoch = recon._epoch(effective_until)
    deadline_source = 'authorization.effective_until'
    if deadline_epoch is None:
        window = int(deadline_seconds if deadline_seconds is not None else cfg['settlement_deadline_seconds'])
        deadline_source = 'policy' if deadline_seconds is not None else 'default'
        authorized_epoch = recon._epoch(authorized_at)
        if authorized_epoch is None:
            return None
        deadline_epoch = authorized_epoch + window
    if now_epoch <= deadline_epoch:
        return None

    amount = recon.to_units(authorization.get('amount'))
    source_name = str(authorization.get('source_name') or '') or None
    checks = {
        schemas.CHECK_ON_CHAIN_EVENT: _check(
            schemas.CHECK_ON_CHAIN_EVENT, schemas.PASS,
            'Authorized operation on record', source_name,
        ),
        schemas.CHECK_TRANSFER_AGENT_MATCH: _check(
            schemas.CHECK_TRANSFER_AGENT_MATCH, schemas.PASS,
            'Authorized by the transfer agent', source_name,
        ),
        schemas.CHECK_SETTLEMENT_MATCH: _check(
            schemas.CHECK_SETTLEMENT_MATCH, schemas.FAIL,
            f'Settlement deadline passed with state '
            f'"{str(authorization.get("settlement_state") or "unknown").lower()}"',
            source_name,
        ),
        schemas.CHECK_SIGNER_VALIDITY: _check(
            schemas.CHECK_SIGNER_VALIDITY, schemas.UNKNOWN,
            'Not applicable — no on-chain event is being reconciled',
        ),
    }
    severity = oic.severity_for(oic.SETTLEMENT_TIMEOUT, cfg)
    return schemas.OperationalIntegrityEvent(
        workspace_id=workspace_id,
        asset_id=asset_id,
        category=oic.CATEGORY_OPERATIONAL_INTEGRITY,
        detection_type=oic.SETTLEMENT_TIMEOUT,
        severity=severity,
        status='anomaly',
        deterministic_reason_code=oic.SETTLEMENT_DEADLINE_EXCEEDED,
        confidence=Decimal(str(cfg['deterministic_confidence'])),
        checks=checks,
        conclusion=schemas.conclusion_from(checks, severity),
        event_type=str(authorization.get('operation') or '').upper() or None,
        operation=str(authorization.get('operation') or '') or None,
        observed_amount=amount,
        expected_amount=amount,
        variance_amount=Decimal('0') if amount is not None else None,
        amount_decimals=authorization.get('token_decimals'),
        external_reference=authorization.get('external_reference'),
        matched_authorization_reference=authorization.get('external_reference'),
        matcher_version=str(cfg['matcher_version']),
        evidence_source=str(authorization.get('evidence_source') or 'live'),
        telemetry_source=None,
        telemetry_stage=schemas.STAGE_UNKNOWN,
        first_seen_at=authorized_at,
        provenance={
            'authorization_id': str(authorization.get('id') or '') or None,
            'authoritative_source': source_name,
            'settlement_state': authorization.get('settlement_state'),
            'deadline_source': deadline_source,
            'matcher': str(cfg['matcher_version']),
            'asset_name': asset_name,
            'evidence_source': str(authorization.get('evidence_source') or 'live'),
        },
    )


def checks_from_reconciliation(result: Any, *, onchain_row: Optional[dict[str, Any]] = None, authoritative_source: Optional[str] = None) -> dict[str, schemas.OperationalCheck]:
    """Canonical checks for a Screen 3 supply-reconciliation verdict.

    Screen 3 reconciles an asset's TOTAL SUPPLY; Screen 5 reconciles individual
    on-chain events. Both produce the same four-check shape so one detection
    detail panel renders either, and so a supply-level finding is never shown
    with an empty analysis (which would read as "nothing was checked").

    Called from the Screen 3 emitter so the mapping lives in exactly one place.
    """
    row = onchain_row or {}
    status = str(getattr(result, 'status', '') or '')
    reason_code = str(getattr(result, 'reason_code', '') or '')
    operation = str(getattr(result, 'match', None) and getattr(result.match, 'matched', None) and result.match.matched.operation or '') or None
    if not operation:
        operation = str(row.get('last_delta_operation') or '') or None

    checks: dict[str, schemas.OperationalCheck] = {}
    observed_label = f'{operation.capitalize()} observed' if operation else 'Supply change observed'
    checks[schemas.CHECK_ON_CHAIN_EVENT] = _check(
        schemas.CHECK_ON_CHAIN_EVENT, schemas.PASS, observed_label,
        str(row.get('provider_type') or '') or None,
    )

    # Screen 3 reconciles an aggregate supply reading. It can only assert signer
    # validity when the observation carries the transaction that moved supply.
    if row.get('tx_hash'):
        checks[schemas.CHECK_SIGNER_VALIDITY] = _check(
            schemas.CHECK_SIGNER_VALIDITY, schemas.PASS, 'Cryptographically valid',
            str(row.get('chain_network') or 'chain'),
        )
    else:
        checks[schemas.CHECK_SIGNER_VALIDITY] = _check(
            schemas.CHECK_SIGNER_VALIDITY, schemas.UNKNOWN,
            'Not evaluated — the supply observation carries no transaction reference',
        )

    if reason_code in (recon.MATCHED_AUTHORIZED_ISSUANCE, recon.MATCHED_AUTHORIZED_REDEMPTION):
        checks[schemas.CHECK_TRANSFER_AGENT_MATCH] = _check(
            schemas.CHECK_TRANSFER_AGENT_MATCH, schemas.PASS, 'Authorized record on file', authoritative_source,
        )
        checks[schemas.CHECK_SETTLEMENT_MATCH] = _check(
            schemas.CHECK_SETTLEMENT_MATCH, schemas.PASS, 'Settlement cleared', authoritative_source,
        )
    elif status in recon.INDETERMINATE_STATUSES or reason_code in (
        recon.AUTHORITATIVE_SOURCE_MISSING, recon.AUTHORITATIVE_SOURCE_STALE, recon.AUTHORITATIVE_SOURCE_UNAVAILABLE,
    ):
        gap = 'Authoritative state could not be established'
        checks[schemas.CHECK_TRANSFER_AGENT_MATCH] = _check(
            schemas.CHECK_TRANSFER_AGENT_MATCH, schemas.UNKNOWN, gap, authoritative_source,
        )
        checks[schemas.CHECK_SETTLEMENT_MATCH] = _check(
            schemas.CHECK_SETTLEMENT_MATCH, schemas.UNKNOWN, gap, authoritative_source,
        )
    else:
        agent_reason = {
            recon.NO_MATCHING_AUTHORIZED_ISSUANCE: 'No authorized issuance',
            recon.NO_MATCHING_AUTHORIZED_REDEMPTION: 'No authorized redemption',
            recon.AMOUNT_MISMATCH: 'Authorized amount does not match the observed change',
            recon.REFERENCE_MISMATCH: 'Business reference does not match the authorization record',
            recon.OUTSIDE_AUTHORIZED_WINDOW: 'Authorization falls outside its permitted window',
            recon.SETTLEMENT_NOT_COMPLETE: 'Authorization on record, settlement not complete',
        }.get(reason_code, 'No matching authorization')
        checks[schemas.CHECK_TRANSFER_AGENT_MATCH] = _check(
            schemas.CHECK_TRANSFER_AGENT_MATCH,
            schemas.PASS if reason_code == recon.SETTLEMENT_NOT_COMPLETE else schemas.FAIL,
            agent_reason, authoritative_source,
        )
        checks[schemas.CHECK_SETTLEMENT_MATCH] = _check(
            schemas.CHECK_SETTLEMENT_MATCH, schemas.FAIL,
            'Settlement not cleared' if reason_code == recon.SETTLEMENT_NOT_COMPLETE else 'No matching settlement',
            authoritative_source,
        )
    return checks
