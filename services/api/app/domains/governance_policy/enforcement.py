"""The ENFORCEMENT evaluation path: the same policy engine, run on real facts.

    evaluate_response_action(connection, ...) -> EnforcementOutcome

Screen 11's simulator answers "what WOULD this policy decide?" and stores its
answer stamped ``simulation = TRUE``. That row authorizes nothing, and Screen 8
excludes it. This module produces the OTHER kind of evaluation — the one Screen 8
actually consumes — by running the identical deterministic engine against facts
read from canonical backend rows rather than from a request body, and persisting
the result with ``simulation = FALSE``.

    operational anomaly (Screen 5 detection)
        -> alert -> incident -> response action (Screen 8)
        -> THIS MODULE: governing policy evaluated on the canonical facts
        -> governance_policy_evaluations row, simulation = FALSE
        -> Screen 8's execution gate reads it via latest_policy_evaluation

What this module deliberately does NOT do
-----------------------------------------
It does not copy a simulation result and relabel it. Every enforcement row is
produced by ``engine.evaluate_policy`` running here, on facts this module read.
It accepts no decision, no policy version, no operator authority, no amount and
no settlement state from a caller: a browser can name a response action and
nothing else, so there is no path by which a client can supply an ALLOW. It never
imports an AI provider, and no field it reads can carry model output — an AI
recommendation is not one of the canonical rows below.

Idempotent
----------
Re-evaluating an action whose observed facts and governing policy version have
not changed returns the decision that already exists rather than writing a second
one. Under a capped policy a duplicate ALLOW would consume the day's issuance
limit twice for an operation that happened once. Facts that genuinely CHANGED
produce a new digest, so a re-evaluation still yields the new decision.

Fail-closed
-----------
A fact that could not be READ is not a fact. Any failed query abandons the
evaluation and records nothing, which leaves Screen 8's gate at
POLICY_EVALUATION_MISSING and LOCKED. A fact that is genuinely ABSENT is passed
to the engine as absent, and the engine fails closed on any mandatory constraint
it cannot show to be satisfied. Neither path can produce an ALLOW.

Every read carries the workspace id. There is no cross-tenant query here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import engine, service
from services.api.app.domains.governance_policy.schemas import (
    EvaluationContext,
    PolicyDecision,
    PolicyDefinition,
)

logger = logging.getLogger(__name__)

#: Outcome statuses. Only ``recorded`` writes a row.
STATUS_RECORDED = 'recorded'
#: No ACTIVE policy governs this workspace/asset/operation. Screen 8 reports
#: NOT_APPLICABLE from its own scope probe; nothing is written here, because an
#: evaluation of a policy that does not exist is not an authorization.
STATUS_NO_POLICY = 'no_governing_policy'
#: A canonical fact could not be read. Nothing is written: the gate must stay at
#: POLICY_EVALUATION_MISSING rather than receive a verdict built on a guess.
STATUS_FACTS_UNAVAILABLE = 'facts_unavailable'
#: The policy tables are not provisioned yet.
STATUS_STORAGE_UNAVAILABLE = 'storage_unavailable'
#: An enforcement evaluation for THESE facts under THIS policy version already
#: exists. Nothing is written: see ``existing_evaluation``.
STATUS_ALREADY_EVALUATED = 'already_evaluated'

#: The business event that justifies each operation. Screen 3/5 vocabulary,
#: reused rather than redeclared, so the enforcement path and the reconciliation
#: path name the same off-chain record for the same on-chain operation.
BUSINESS_EVENT_FOR_OPERATION: dict[str, str] = {
    gpc.OPERATION_MINT: gpc.BUSINESS_EVENT_SUBSCRIPTION,
    gpc.OPERATION_BURN: gpc.BUSINESS_EVENT_REDEMPTION,
    gpc.OPERATION_TRANSFER: gpc.BUSINESS_EVENT_TRANSFER_INSTRUCTION,
}

#: Authorized-issuance settlement states, mapped onto the policy vocabulary.
#: Anything unrecognized stays UNKNOWN — never CLEARED — so an unfamiliar value
#: from an upstream system can never satisfy a settlement requirement.
_SETTLEMENT_CLEARED_STATES = frozenset({
    'settled', 'cleared', 'complete', 'completed', 'final', 'finalized',
})
_SETTLEMENT_PENDING_STATES = frozenset({'pending', 'in_progress', 'processing', 'submitted'})
_SETTLEMENT_FAILED_STATES = frozenset({'failed', 'rejected', 'cancelled', 'canceled', 'reversed'})


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _text(value: Any) -> Optional[str]:
    text = str(value or '').strip()
    return text or None


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def normalize_settlement_state(value: Any) -> Optional[str]:
    """An authorized issuance's settlement state, in the policy vocabulary.

    Returns None for a value that is absent OR unrecognized. Both reach the
    engine as "the settlement state could not be established", which is a DENY
    for any policy that imposes a settlement requirement — never a pass.
    """
    key = str(value or '').strip().lower()
    if not key:
        return None
    if key in _SETTLEMENT_CLEARED_STATES:
        return gpc.SETTLEMENT_CLEARED
    if key in _SETTLEMENT_PENDING_STATES:
        return gpc.SETTLEMENT_PENDING
    if key in _SETTLEMENT_FAILED_STATES:
        return gpc.SETTLEMENT_FAILED
    return None


@dataclass(frozen=True)
class EnforcementFacts:
    """The authoritative operation context, as this module read it.

    Every field is either a canonical row value or None. There is deliberately no
    field a request body writes and no field an AI layer could write.
    """

    operation: Optional[str] = None
    amount_usd: Optional[Decimal] = None
    business_event: Optional[str] = None
    settlement_status: Optional[str] = None
    compliance_approval: bool = False
    asset_id: Optional[str] = None
    incident_id: Optional[str] = None
    canonical_event_id: Optional[str] = None
    operator_id: Optional[str] = None
    #: Names of canonical facts a query FAILED to return. Any entry abandons the
    #: evaluation — "we could not look" is not an input the engine may reason on.
    unreadable: tuple[str, ...] = ()
    #: Which row each fact came from, stored in the evaluation's input snapshot.
    sources: dict[str, Any] = field(default_factory=dict)
    #: The action's proposer, recorded for TRACEABILITY only. Never passed to the
    #: engine as the operation's operator — see ``resolve_action_facts``.
    proposer_user_id: Optional[str] = None

    @property
    def readable(self) -> bool:
        return not self.unreadable

    def digest(self, *, policy_id: str, policy_version: Any, response_action_id: Optional[str]) -> str:
        """A stable fingerprint of WHAT WAS OBSERVED, under WHICH policy version.

        Deliberately excludes the clock and every derived running total, so two
        evaluations of the same action against the same policy version, with the
        same observed facts, share one digest. That is what lets the producer be
        idempotent — and idempotency is not cosmetic here: a second identical
        ALLOW row would consume the policy's daily issuance cap a second time for
        an operation that happened once.

        Facts that genuinely CHANGED (a settlement that cleared, a corrected
        amount) change the digest, so a re-evaluation produces the new decision
        rather than returning a stale one.
        """
        material = {
            'policy_id': str(policy_id or ''),
            'policy_version': str(policy_version or ''),
            'response_action_id': str(response_action_id or ''),
            'operation': self.operation,
            'amount_usd': str(self.amount_usd) if self.amount_usd is not None else None,
            'business_event': self.business_event,
            'settlement_status': self.settlement_status,
            'compliance_approval': bool(self.compliance_approval),
            'asset_id': self.asset_id,
            'incident_id': self.incident_id,
            'canonical_event_id': self.canonical_event_id,
            'operator_id': self.operator_id,
            'proposer_user_id': self.proposer_user_id,
            'sources': dict(self.sources or {}),
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()

    def as_payload(self) -> dict[str, Any]:
        """The shape ``service.build_context`` consumes.

        Note what is NOT here: no decision, no policy version, no daily total and
        no operator authority. Those are resolved by ``build_context`` from
        canonical state, exactly as they are for a simulation.
        """
        return {
            'operation': self.operation,
            'amount_usd': self.amount_usd,
            'operator_id': self.operator_id,
            'business_event': self.business_event,
            'settlement_status': self.settlement_status,
            'compliance_approval': bool(self.compliance_approval),
            'asset_id': self.asset_id,
            'incident_id': self.incident_id,
            'event_id': self.canonical_event_id,
        }


@dataclass(frozen=True)
class EnforcementOutcome:
    """What the enforcement pass did, and why."""

    status: str
    decision: Optional[PolicyDecision] = None
    policy: Optional[PolicyDefinition] = None
    facts: Optional[EnforcementFacts] = None
    recorded: bool = False
    #: The evaluation that already existed for these facts, when nothing new was
    #: written. Reported so the caller can name the decision that still governs.
    existing_evaluation_id: Optional[str] = None
    existing_decision: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'status': self.status,
            'recorded': bool(self.recorded),
            'evaluation': self.decision.as_dict() if self.decision else None,
            'existing_evaluation_id': self.existing_evaluation_id,
            'existing_decision': self.existing_decision,
            'policy_id': self.policy.policy_id if self.policy else None,
            'policy_key': self.policy.policy_key if self.policy else None,
            'policy_version': self.policy.version if self.policy else None,
            'unreadable_facts': list((self.facts.unreadable if self.facts else ()) or ()),
            # Stated by the backend on every enforcement result, like the gate.
            'decision_authority': engine.schemas.DECISION_AUTHORITY,
            'ai_authority': engine.schemas.AI_AUTHORITY,
        }


def _table_exists(connection: Any, name: str, unreadable: list[str]) -> bool:
    """Fail-closed table probe. A probe that RAISES is recorded as unreadable."""
    try:
        row = connection.execute(
            'SELECT to_regclass(%s) IS NOT NULL AS present', (f'public.{name}',),
        ).fetchone()
    except Exception:
        logger.exception('governance_enforcement_table_probe_failed table=%s', name)
        if f'table:{name}' not in unreadable:
            unreadable.append(f'table:{name}')
        return False
    return bool(_row_dict(row).get('present'))


# --------------------------------------------------------------------------
# Fact resolution — canonical rows only
# --------------------------------------------------------------------------
def resolve_action_facts(
    connection: Any, *, workspace_id: str, action: dict[str, Any],
) -> EnforcementFacts:
    """Read the operation context for ONE response action from canonical rows.

    The chain walked, all workspace-scoped:

        response action -> alert -> threat detection   (operation, amount, asset)
                        -> incident                    (lifecycle linkage)
        asset + operation -> asset_authorized_issuances (business event,
                                                         settlement state)

    A read that fails is recorded in ``unreadable`` and abandons the pass. A row
    that is genuinely absent leaves its facts None, and the engine fails closed on
    every constraint that needs them.
    """
    unreadable: list[str] = []
    sources: dict[str, Any] = {}

    metadata = action.get('execution_metadata') if isinstance(action.get('execution_metadata'), dict) else {}
    chain = metadata.get('chain_linked_ids') if isinstance(metadata.get('chain_linked_ids'), dict) else {}
    action_id = _text(action.get('id'))
    incident_id = _text(action.get('incident_id')) or _text(chain.get('incident_id'))
    alert_id = _text(action.get('alert_id')) or _text(chain.get('alert_id'))
    asset_id = _text(metadata.get('asset_id')) or _text(chain.get('asset_id'))
    canonical_event_id = _text(metadata.get('event_id')) or _text(chain.get('event_id'))
    detection_id = _text(chain.get('detection_id'))
    sources['response_action_id'] = action_id

    # -- alert -> detection ------------------------------------------------
    if alert_id and not detection_id and _table_exists(connection, 'alerts', unreadable):
        try:
            row = _row_dict(connection.execute(
                'SELECT detection_id, target_id FROM alerts WHERE id = %s::uuid AND workspace_id = %s',
                (alert_id, workspace_id),
            ).fetchone())
        except Exception:
            logger.exception('governance_enforcement_alert_read_failed workspace_id=%s', workspace_id)
            unreadable.append('alert')
            row = {}
        detection_id = detection_id or _text(row.get('detection_id'))
        target_id = _text(row.get('target_id'))
        if target_id:
            sources['target_id'] = target_id
            if not asset_id and _table_exists(connection, 'targets', unreadable):
                try:
                    target = _row_dict(connection.execute(
                        'SELECT asset_id FROM targets WHERE id = %s::uuid AND workspace_id = %s',
                        (target_id, workspace_id),
                    ).fetchone())
                except Exception:
                    logger.exception('governance_enforcement_target_read_failed workspace_id=%s', workspace_id)
                    unreadable.append('target')
                    target = {}
                asset_id = asset_id or _text(target.get('asset_id'))
    if alert_id:
        sources['alert_id'] = alert_id

    # -- detection: the canonical operational event ------------------------
    operation: Optional[str] = None
    amount: Optional[Decimal] = None
    external_reference: Optional[str] = None
    if detection_id and _table_exists(connection, 'threat_detections', unreadable):
        try:
            detection = _row_dict(connection.execute(
                '''SELECT id, operation, observed_amount, primary_asset_id, tx_hash, provenance
                   FROM threat_detections WHERE id = %s::uuid AND workspace_id = %s''',
                (detection_id, workspace_id),
            ).fetchone())
        except Exception:
            logger.exception('governance_enforcement_detection_read_failed workspace_id=%s', workspace_id)
            unreadable.append('threat_detection')
            detection = {}
        if detection:
            sources['detection_id'] = detection_id
            operation = gpc.normalize_operation(detection.get('operation'))
            amount = _decimal(detection.get('observed_amount'))
            asset_id = asset_id or _text(detection.get('primary_asset_id'))
            canonical_event_id = canonical_event_id or _text(detection.get('tx_hash'))
            provenance = detection.get('provenance') if isinstance(detection.get('provenance'), dict) else {}
            external_reference = _text(provenance.get('external_reference'))

    # -- authorized issuance: the off-chain record -------------------------
    # This is the AUTHORITATIVE business-event and settlement fact. Absent means
    # "no authorization backs this operation", which is exactly the anomaly the
    # policy is meant to deny — it is never treated as a satisfied requirement.
    business_event: Optional[str] = None
    settlement_status: Optional[str] = None
    if asset_id and _table_exists(connection, 'asset_authorized_issuances', unreadable):
        try:
            issuance = _row_dict(connection.execute(
                '''SELECT id, operation, amount, settlement_state, external_reference
                   FROM asset_authorized_issuances
                   WHERE workspace_id = %s AND asset_id = %s::uuid
                     AND (%s::text IS NULL OR external_reference = %s::text)
                   ORDER BY authorized_at DESC
                   LIMIT 1''',
                (workspace_id, asset_id, external_reference, external_reference),
            ).fetchone())
        except Exception:
            logger.exception('governance_enforcement_issuance_read_failed workspace_id=%s', workspace_id)
            unreadable.append('asset_authorized_issuance')
            issuance = {}
        if issuance:
            sources['authorized_issuance_id'] = _text(issuance.get('id'))
            issued_operation = gpc.normalize_operation(issuance.get('operation'))
            operation = operation or issued_operation
            settlement_status = normalize_settlement_state(issuance.get('settlement_state'))
            # The business event is named by the AUTHORIZED operation, not by the
            # observed one: an unauthorized mint must not be handed the
            # subscription its own on-chain event implies.
            if issued_operation:
                business_event = BUSINESS_EVENT_FOR_OPERATION.get(issued_operation)

    if incident_id:
        sources['incident_id'] = incident_id
    if asset_id:
        sources['asset_id'] = asset_id
    if canonical_event_id:
        sources['canonical_event_id'] = canonical_event_id

    return EnforcementFacts(
        operation=operation,
        amount_usd=amount,
        business_event=business_event,
        settlement_status=settlement_status,
        # A compliance approval is an APPROVAL ARTIFACT recorded by a human on
        # Screen 8, and the policy engine has no evidence source for one here. It
        # is therefore never asserted: a policy naming COMPLIANCE_APPROVER leaves
        # that role outstanding, and Screen 8 collects the real sign-off.
        compliance_approval=False,
        asset_id=asset_id,
        incident_id=incident_id,
        canonical_event_id=canonical_event_id,
        # NOT ESTABLISHED, deliberately. The operator a policy's TREASURY_OPERATOR
        # requirement is about is whoever performed the governed OPERATION, and
        # no canonical row maps an on-chain sender to a workspace member. The
        # response action's proposer is a different person with a different
        # question to answer, and substituting them would be worse than useless:
        # `response.propose` is exactly the permission that evidences
        # TREASURY_OPERATOR, so any proposer would silently satisfy the policy's
        # operator-authority check for an operation nobody authorized. Left
        # absent, the role stays outstanding and the engine denies — which is the
        # honest answer for an operation whose operator cannot be evidenced.
        operator_id=None,
        proposer_user_id=_text(action.get('created_by_user_id')),
        unreadable=tuple(unreadable),
        sources=sources,
    )


def governing_policy(
    connection: Any, *, workspace_id: str, operation: Optional[str], asset_id: Optional[str],
) -> Optional[PolicyDefinition]:
    """The ACTIVE policy that governs this operation in this workspace.

    An asset-scoped policy wins over a workspace-wide one for the same operation;
    among equals the most recently updated wins, which is the same ordering
    ``list_policies`` publishes. Returns None when nothing governs — never a
    DRAFT, DISABLED or ARCHIVED policy, because only an ACTIVE policy can
    authorize anything.
    """
    normalized = gpc.normalize_operation(operation)
    if not normalized:
        return None
    try:
        row = connection.execute(
            f'''SELECT {service._POLICY_COLUMNS} FROM {service.POLICIES_TABLE}
                WHERE workspace_id = %s AND status = %s AND operation = %s
                  AND (asset_id IS NULL OR (%s::uuid IS NOT NULL AND asset_id = %s::uuid))
                ORDER BY (asset_id IS NOT NULL) DESC, updated_at DESC
                LIMIT 1''',
            (workspace_id, gpc.STATUS_ACTIVE, normalized, asset_id, asset_id),
        ).fetchone()
    except Exception:
        logger.exception('governance_enforcement_policy_read_failed workspace_id=%s', workspace_id)
        raise
    return service.policy_from_row(row) if row else None


def existing_evaluation(
    connection: Any, *, workspace_id: str, policy_id: str, fact_digest: str,
) -> Optional[dict[str, Any]]:
    """An ENFORCEMENT evaluation already recorded for exactly these facts.

    Workspace-scoped and simulation-excluded, matching the same row Screen 8
    would read. Returns None when none exists OR when the lookup could not run —
    the caller then evaluates and writes, which is the safe direction: a missed
    match records a duplicate decision, while a false match would suppress a real
    re-evaluation.
    """
    if not fact_digest:
        return None
    try:
        row = connection.execute(
            f'''SELECT id, decision, policy_version, evaluated_at
                FROM {service.EVALUATIONS_TABLE}
                WHERE workspace_id = %s AND policy_id = %s::uuid AND simulation = FALSE
                  AND input_snapshot->>'fact_digest' = %s
                ORDER BY evaluated_at DESC
                LIMIT 1''',
            (workspace_id, policy_id, fact_digest),
        ).fetchone()
    except Exception:
        logger.warning(
            'governance_enforcement_idempotency_lookup_failed workspace_id=%s', workspace_id,
        )
        return None
    return _row_dict(row) or None


# --------------------------------------------------------------------------
# The producer
# --------------------------------------------------------------------------
def evaluate_response_action(
    connection: Any,
    *,
    workspace_id: str,
    action: dict[str, Any],
    now: datetime,
    user_id: Optional[str] = None,
) -> EnforcementOutcome:
    """Run the deterministic policy engine on ONE response action's real facts.

    Persists the verdict as an ENFORCEMENT evaluation (``simulation = FALSE``)
    that Screen 8's gate reads through the existing ``latest_policy_evaluation``
    semantics. The caller owns the transaction and commits.

    ``user_id`` is recorded as WHO TRIGGERED the evaluation. It is not an input to
    the decision, and it is never offered to the policy as the operation's
    operator: no caller's own permissions can satisfy a policy requirement here
    (see ``resolve_action_facts``).
    """
    if not service.storage_ready(connection):
        return EnforcementOutcome(status=STATUS_STORAGE_UNAVAILABLE)

    facts = resolve_action_facts(connection, workspace_id=workspace_id, action=action)
    if not facts.readable:
        # A fact we could not read is not a fact. Record nothing: Screen 8 then
        # reports POLICY_EVALUATION_MISSING and stays LOCKED, which is the honest
        # answer, rather than an ALLOW built on a fact nobody established.
        service.log_event(
            'governance_policy_enforcement_facts_unavailable', workspace_id=workspace_id,
            action_id=str(action.get('id') or ''), unreadable=','.join(facts.unreadable),
        )
        return EnforcementOutcome(status=STATUS_FACTS_UNAVAILABLE, facts=facts)

    try:
        policy = governing_policy(
            connection, workspace_id=workspace_id,
            operation=facts.operation, asset_id=facts.asset_id,
        )
    except Exception:
        return EnforcementOutcome(
            status=STATUS_FACTS_UNAVAILABLE,
            facts=EnforcementFacts(
                **{**facts.__dict__, 'unreadable': facts.unreadable + ('governing_policy',)},
            ),
        )
    if policy is None:
        # Nothing governs this operation. An evaluation of a policy that does not
        # exist would be a DENY/POLICY_NOT_FOUND row, and storing one would turn
        # "no policy applies" into a recorded refusal for an action the workspace
        # never chose to govern. Screen 8 reports NOT_APPLICABLE from its own
        # scope probe instead.
        service.log_event(
            'governance_policy_enforcement_no_policy', workspace_id=workspace_id,
            action_id=str(action.get('id') or ''), operation=facts.operation,
        )
        return EnforcementOutcome(status=STATUS_NO_POLICY, facts=facts)

    action_id = str(action.get('id') or '') or None
    fact_digest = facts.digest(
        policy_id=policy.policy_id, policy_version=policy.version, response_action_id=action_id,
    )
    # Idempotency. Re-evaluating an action whose facts and governing version have
    # not changed must NOT write a second decision: under a capped policy the
    # duplicate ALLOW would consume the day's issuance limit twice for one
    # operation, and the second write would eventually deny a legitimate one.
    already = existing_evaluation(
        connection, workspace_id=workspace_id, policy_id=policy.policy_id, fact_digest=fact_digest,
    )
    if already:
        return EnforcementOutcome(
            status=STATUS_ALREADY_EVALUATED, policy=policy, facts=facts, recorded=False,
            existing_evaluation_id=str(already.get('id') or '') or None,
            existing_decision=str(already.get('decision') or '') or None,
        )

    # Server-resolved context: the operator's authority and today's issuance
    # total come from canonical state here, exactly as they do for a simulation.
    base_context = service.build_context(
        connection, workspace_id=workspace_id, policy=policy,
        payload=facts.as_payload(), now=now, simulation=False,
    )
    context = EvaluationContext(
        **{
            **base_context.__dict__,
            'response_action_id': action_id,
            'fact_digest': fact_digest,
            'fact_sources': dict(facts.sources),
        }
    )

    # THE decision. The same pure function Screen 11 calls, on canonical facts.
    decision = engine.evaluate_policy(policy, context, now=now)
    recorded = service.record_evaluation(
        connection, workspace_id=workspace_id, decision=decision,
        context=context, user_id=user_id,
    )
    service.log_event(
        'governance_policy_enforcement_evaluated', workspace_id=workspace_id,
        action_id=str(action.get('id') or ''), policy_key=decision.policy_key,
        policy_version=decision.policy_version, decision=decision.decision,
        evaluation_id=decision.evaluation_id, recorded=recorded,
        reason_codes=','.join(decision.reason_codes) or None,
    )
    return EnforcementOutcome(
        status=STATUS_RECORDED if recorded else STATUS_STORAGE_UNAVAILABLE,
        decision=decision, policy=policy, facts=facts, recorded=recorded,
    )
