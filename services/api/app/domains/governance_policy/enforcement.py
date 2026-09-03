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
A fact that could not be READ is not a fact, and no failed read can ever widen an
authorization. What DIFFERS is whether the failure leaves anything worth
recording, and conflating the two is what made a producer-only outage invisible:

  MATERIAL fact unreadable (workspace/action identity, the ASSET that decides
  which policies are in scope, policy storage) — the evaluation is abandoned and
  NOTHING is recorded. There is no scope to reason inside, so there is no verdict
  to state. Screen 8 reports POLICY_EVALUATION_MISSING and stays LOCKED.

  SUPPLEMENTARY fact unreadable (the detection's operation/amount/provenance, the
  authorized issuance, the settlement state) — the action is still known to be in
  governed scope, so a deterministic fail-closed DENY IS recorded, naming the
  facts that could not be established (AUTHORITATIVE_FACTS_UNAVAILABLE plus
  DETECTION_FACTS_UNAVAILABLE / ISSUANCE_FACTS_UNAVAILABLE). No policy is matched
  from an incomplete context, so this can never reach an ALLOW.

The second case is the one that mattered in production. This producer reads MORE
facts than Screen 8's gate does — the gate never touches threat_detections or
asset_authorized_issuances — so a read failure here left the gate seeing a
perfectly healthy chain, finding no evaluation, and reporting
POLICY_EVALUATION_MISSING forever. The only thing that could clear that state was
the row this module had declined to write, and no operator action produced it.

A fact that is genuinely ABSENT is passed to the engine as absent, and the engine
fails closed on any mandatory constraint it cannot show to be satisfied. No path
here can produce an ALLOW from a fact nobody established.

Absent facts are RECORDED, not skipped
--------------------------------------
Being unable to resolve the governing policy is itself a verdict, and it is
written down. When the operation behind a response action cannot be established
— no threat detection stands behind the incident, or the one that does names no
operation, which is the ordinary shape of an incident opened from an operational
alert — no policy can be matched to it, and the producer used to return without
writing anything at all. Screen 8's gate meanwhile asks a WIDER question ("does
any ACTIVE policy cover this workspace/asset?"), answers yes, and therefore
reports POLICY_EVALUATION_MISSING / LOCKED. The action was then unauthorizable
forever: the only thing that could clear that state was the row the producer had
declined to write, and no operator action could produce it.

So whenever this workspace/asset IS inside an ACTIVE policy's scope
(``policy_scope_governed`` — the same predicate the gate uses, called from here
so the two can never disagree), a deterministic DENY is recorded with the reason
codes naming what could not be established. When NOTHING governs the
workspace/asset, nothing is written and the gate reports NOT_APPLICABLE from that
same probe, which blocks nothing and needs no row.

Every read carries the workspace id. There is no cross-tenant query here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
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

#: Outcome statuses. ``recorded`` and ``recorded_fail_closed`` write a row.
STATUS_RECORDED = 'recorded'
#: A DETERMINISTIC FAIL-CLOSED DENY was recorded because the governing policy for
#: this operation could not be resolved WHILE the workspace/asset is inside an
#: ACTIVE policy's scope. See ``evaluate_response_action``: this is the row that
#: stops such an action from sitting at POLICY_EVALUATION_MISSING forever.
STATUS_RECORDED_FAIL_CLOSED = 'recorded_fail_closed'
#: NOTHING governs this workspace/asset at all — not this operation, not any
#: other. Screen 8 reports NOT_APPLICABLE from the same scope probe used here,
#: and nothing is written, because an evaluation of a policy that does not exist
#: is not an authorization and the gate is not blocked on one.
STATUS_NO_POLICY = 'no_governing_policy'
#: A canonical fact could not be read. Nothing is written: the gate must stay at
#: POLICY_EVALUATION_MISSING rather than receive a verdict built on a guess.
STATUS_FACTS_UNAVAILABLE = 'facts_unavailable'
#: The policy tables are not provisioned yet.
STATUS_STORAGE_UNAVAILABLE = 'storage_unavailable'
#: An enforcement evaluation for THESE facts under THIS policy version already
#: exists. Nothing is written: see ``existing_evaluation``.
STATUS_ALREADY_EVALUATED = 'already_evaluated'
#: A verdict was reached but the INSERT did not land (storage vanished between the
#: readiness probe and the write).
STATUS_WRITE_FAILED = 'write_failed'
#: The producer raised. Recorded by the caller, never by this module.
STATUS_EXCEPTION = 'exception'

#: Every terminal status this module can return. Enumerated so a caller can
#: report one without hard-coding the set, and so the diagnostic payload the
#: recommend endpoint returns is drawn from a closed vocabulary.
STATUSES = (
    STATUS_RECORDED,
    STATUS_RECORDED_FAIL_CLOSED,
    STATUS_NO_POLICY,
    STATUS_FACTS_UNAVAILABLE,
    STATUS_STORAGE_UNAVAILABLE,
    STATUS_ALREADY_EVALUATED,
    STATUS_WRITE_FAILED,
    STATUS_EXCEPTION,
)

# --------------------------------------------------------------------------
# MATERIAL vs SUPPLEMENTARY facts
# --------------------------------------------------------------------------
#: Not every unreadable fact is equally fatal, and treating them as one class is
#: what made a producer-only read failure invisible.
#:
#: A MATERIAL fact is one without which no trustworthy decision exists at all:
#: the workspace and action identity, the ASSET identity (it decides which
#: policies are in scope), and the policy storage itself. When one of those
#: cannot be read, this module records NOTHING — there is no verdict to record,
#: and inventing one would state a conclusion about a scope nobody established.
#:
#: A SUPPLEMENTARY fact is a business fact ABOUT an action already known to be in
#: governed scope: the detection's operation, amount and provenance, the
#: authorized issuance behind it, the settlement state. Losing one of those does
#: not stop this module from knowing that a policy governs this action — so
#: silence is the wrong answer. Silence leaves Screen 8 at
#: POLICY_EVALUATION_MISSING, a state no operator can clear, while every fact the
#: GATE reads is healthy. A deterministic fail-closed DENY is recorded instead,
#: naming exactly which authoritative fact could not be established.
#:
#: The classification is fail-safe: anything not listed here is treated as
#: MATERIAL, so a fact added later cannot silently become a recordable gap.
SUPPLEMENTARY_FACTS = frozenset({
    'threat_detection',
    'table:threat_detections',
    'asset_authorized_issuance',
    'table:asset_authorized_issuances',
})

#: The reason code that names each supplementary gap in the recorded verdict.
_FACT_REASON_CODES: dict[str, str] = {
    'threat_detection': gpc.DETECTION_FACTS_UNAVAILABLE,
    'table:threat_detections': gpc.DETECTION_FACTS_UNAVAILABLE,
    'asset_authorized_issuance': gpc.ISSUANCE_FACTS_UNAVAILABLE,
    'table:asset_authorized_issuances': gpc.ISSUANCE_FACTS_UNAVAILABLE,
}


def is_supplementary_fact(fact: Any) -> bool:
    """Whether an unreadable-fact key is SUPPLEMENTARY. Unknown keys are MATERIAL."""
    return str(fact or '').strip() in SUPPLEMENTARY_FACTS


def facts_unavailable_reason_codes(facts: Any) -> tuple[str, ...]:
    """Reason codes naming the supplementary facts that could not be read.

    Always led by the generic ``AUTHORITATIVE_FACTS_UNAVAILABLE`` so a consumer
    can recognize the class without enumerating its members, followed by the
    specific code for each gap, in a stable order.
    """
    specific: list[str] = []
    for fact in facts or ():
        code = _FACT_REASON_CODES.get(str(fact or '').strip())
        if code and code not in specific:
            specific.append(code)
    if not specific:
        return ()
    return (gpc.AUTHORITATIVE_FACTS_UNAVAILABLE, *specific)

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


#: Re-exported so this module's readers find the predicate beside the scopes that
#: use it. Defined in ``service`` because that is the lower-level module of the
#: two, and ``enforcement`` already depends on it.
transaction_aborted = service.transaction_aborted


@contextmanager
def read_scope(connection: Any):
    """A SAVEPOINT around ONE canonical read that is allowed to fail and continue.

    Catching a database exception in Python does not undo what it did to the
    TRANSACTION. On PostgreSQL a failed statement aborts the whole transaction,
    and every statement after it fails with InFailedSqlTransaction until someone
    rolls back — so a producer that caught a failed detection read and carried on
    was, from that point, running a sequence of guaranteed failures. That is what
    turned ONE unreadable supplementary fact into "no evaluation was written at
    all, and the next action in the plan could not be evaluated either".

    Scoping each such read to its own savepoint means the rollback is exactly as
    wide as the statement that failed: the connection is immediately usable
    again, this module can still record the fail-closed verdict the failure
    implies, and the caller's surrounding transaction is untouched.

    Test fakes that do not implement ``transaction()`` fall through to a
    passthrough — they carry no real transaction semantics, and every caller
    below still handles the exception itself. Mirrors the existing
    ``pilot._reconcile_target_savepoint`` convention.
    """
    tx = getattr(connection, 'transaction', None)
    if callable(tx) and not transaction_aborted(connection):
        with tx():
            yield
    else:
        # Either no real transaction semantics (a fake), or the transaction is
        # ALREADY aborted, in which case a savepoint cannot be opened and trying
        # would corrupt psycopg's nesting for the rest of the request (see
        # ``transaction_aborted``). The statement below will fail with
        # InFailedSqlTransaction and the caller records it as unreadable, which is
        # the correct answer: nothing here can be read until someone rolls back.
        yield


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

    @property
    def material_unreadable(self) -> tuple[str, ...]:
        """Unreadable facts without which NO trustworthy decision exists.

        Any entry abandons the pass and records nothing: see
        ``SUPPLEMENTARY_FACTS`` for why the two classes are not the same answer.
        """
        return tuple(f for f in self.unreadable if not is_supplementary_fact(f))

    @property
    def supplementary_unreadable(self) -> tuple[str, ...]:
        """Unreadable BUSINESS facts about an action already in governed scope.

        Any entry forces a recorded fail-closed DENY rather than silence, and can
        never be reasoned past into an ALLOW.
        """
        return tuple(f for f in self.unreadable if is_supplementary_fact(f))

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
            # Which facts could not be READ is itself material. Without it, the
            # row recorded for "no detection stands behind this incident" and the
            # row for "the detection could not be read" share a digest, and the
            # idempotency check below would return the first as the answer to the
            # second — two different verdicts, with different reason codes,
            # collapsed into one. It also means a retry AFTER the outage clears
            # produces a new digest, so the real decision is reached rather than
            # a stale refusal being served forever.
            'unreadable': sorted(self.unreadable or ()),
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
            # Which class of fact was lost, so a caller can tell "nothing could be
            # established" from "an action in governed scope was denied for want
            # of a business fact" without re-deriving the classification.
            'material_facts_unavailable': list(
                (self.facts.material_unreadable if self.facts else ()) or ()),
            'supplementary_facts_unavailable': list(
                (self.facts.supplementary_unreadable if self.facts else ()) or ()),
            # Stated by the backend on every enforcement result, like the gate.
            'decision_authority': engine.schemas.DECISION_AUTHORITY,
            'ai_authority': engine.schemas.AI_AUTHORITY,
        }


def _table_exists(connection: Any, name: str, unreadable: list[str]) -> bool:
    """Fail-closed table probe. A probe that RAISES is recorded as unreadable."""
    try:
        with read_scope(connection):
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
#: The columns the policy engine needs from the Screen 5 detection. They exist
#: only on ``threat_detections`` (migration 0146); the legacy ``detections``
#: table has no operation, amount, tx_hash or provenance at all.
_DETECTION_COLUMNS = 'id, operation, observed_amount, primary_asset_id, tx_hash, provenance'


def resolve_threat_detection(
    connection: Any,
    *,
    workspace_id: str,
    detection_id: Optional[str],
    alert_id: Optional[str],
    incident_id: Optional[str],
    unreadable: list[str],
) -> dict[str, Any]:
    """The Screen 5 threat detection behind this response action, or ``{}``.

    Three lookups, most specific first, all workspace-scoped:

      1. an explicit ``chain_linked_ids.detection_id`` on the action;
      2. ``threat_detections.linked_alert_id`` — the column
         ``threat_detection.service.ensure_alert_for_detection`` stamps when it
         raises the canonical alert for a detection cluster;
      3. ``threat_detections.linked_incident_id`` — the same link at incident
         level, for an action attached to the incident rather than to the alert.

    ``alerts.detection_id`` is deliberately NOT one of them. That column
    references ``detections(id)`` (migration 0042) — a DIFFERENT table, with a
    different id space and none of the columns above — and the Screen 5 writer
    never populates it, because the link it owns runs the other way
    (``threat_detections.linked_alert_id``). Reading it as a threat-detection id
    could only ever miss, and a miss left ``operation`` unresolved, which made
    ``governing_policy`` return nothing, wrote no enforcement row, and parked
    every response action at POLICY_EVALUATION_MISSING / LOCKED.

    Fail-closed: a read that RAISES is recorded in ``unreadable`` and abandons
    the pass, so an unreadable detection can never be mistaken for an absent one.
    """
    if not (detection_id or alert_id or incident_id):
        return {}
    if not _table_exists(connection, 'threat_detections', unreadable):
        return {}

    attempts: list[tuple[str, tuple[Any, ...]]] = []
    if detection_id:
        attempts.append((
            f"""SELECT {_DETECTION_COLUMNS} FROM threat_detections
                WHERE id = %s::uuid AND workspace_id = %s""",
            (detection_id, workspace_id),
        ))
    if alert_id:
        attempts.append((
            f"""SELECT {_DETECTION_COLUMNS} FROM threat_detections
                WHERE linked_alert_id = %s::uuid AND workspace_id = %s
                ORDER BY detected_at DESC, id ASC LIMIT 1""",
            (alert_id, workspace_id),
        ))
    if incident_id:
        attempts.append((
            f"""SELECT {_DETECTION_COLUMNS} FROM threat_detections
                WHERE linked_incident_id = %s::uuid AND workspace_id = %s
                ORDER BY detected_at DESC, id ASC LIMIT 1""",
            (incident_id, workspace_id),
        ))

    for statement, params in attempts:
        try:
            with read_scope(connection):
                row = _row_dict(connection.execute(statement, params).fetchone())
        except Exception:
            logger.exception('governance_enforcement_detection_read_failed workspace_id=%s', workspace_id)
            if 'threat_detection' not in unreadable:
                unreadable.append('threat_detection')
            return {}
        if row:
            return row
    return {}


def resolve_action_facts(
    connection: Any, *, workspace_id: str, action: dict[str, Any],
) -> EnforcementFacts:
    """Read the operation context for ONE response action from canonical rows.

    The chain walked, all workspace-scoped:

        response action -> alert -> target             (asset)
                        -> threat detection            (operation, amount, asset)
                           via threat_detections' OWN linkage columns
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

    # -- alert -> target -> asset ------------------------------------------
    if alert_id and not asset_id and _table_exists(connection, 'alerts', unreadable):
        try:
            with read_scope(connection):
                row = _row_dict(connection.execute(
                    'SELECT target_id FROM alerts WHERE id = %s::uuid AND workspace_id = %s',
                    (alert_id, workspace_id),
                ).fetchone())
        except Exception:
            logger.exception('governance_enforcement_alert_read_failed workspace_id=%s', workspace_id)
            unreadable.append('alert')
            row = {}
        target_id = _text(row.get('target_id'))
        if target_id:
            sources['target_id'] = target_id
            if _table_exists(connection, 'targets', unreadable):
                try:
                    with read_scope(connection):
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
    authorization_id: Optional[str] = None
    detection = resolve_threat_detection(
        connection, workspace_id=workspace_id, detection_id=detection_id,
        alert_id=alert_id, incident_id=incident_id, unreadable=unreadable,
    )
    if detection:
        detection_id = _text(detection.get('id')) or detection_id
        sources['detection_id'] = detection_id
        operation = gpc.normalize_operation(detection.get('operation'))
        amount = _decimal(detection.get('observed_amount'))
        asset_id = asset_id or _text(detection.get('primary_asset_id'))
        canonical_event_id = canonical_event_id or _text(detection.get('tx_hash'))
        provenance = detection.get('provenance') if isinstance(detection.get('provenance'), dict) else {}
        # The authorization THIS detection is about, named by the detection itself.
        # Either identifier is an explicit link; neither is inferred.
        external_reference = _text(provenance.get('external_reference'))
        authorization_id = _text(provenance.get('authorization_id'))

    # -- authorized issuance: the off-chain record -------------------------
    # This is the AUTHORITATIVE business-event and settlement fact. Absent means
    # "no authorization backs this operation", which is exactly the anomaly the
    # policy is meant to deny — it is never treated as a satisfied requirement.
    #
    # Read ONLY through an identifier the detection itself carries. The previous
    # predicate degraded to "the most recently authorized issuance for this asset"
    # whenever the detection named none — which is every unmatched-issuance and
    # every supply-variance event, because a detection is STORED only when no
    # authorization matched it. An unauthorized mint was therefore handed an
    # unrelated cleared subscription, and the engine, shown a satisfied business
    # event and a CLEARED settlement, recorded a `simulation = FALSE` ALLOW for an
    # operation nobody authorized. An authorization that cannot be named is not
    # this operation's authorization, so none is read and both facts stay absent.
    business_event: Optional[str] = None
    settlement_status: Optional[str] = None
    issuance_lookup: Optional[tuple[str, tuple[Any, ...]]] = None
    if asset_id and authorization_id:
        issuance_lookup = (
            '''SELECT id, operation, amount, settlement_state, external_reference
               FROM asset_authorized_issuances
               WHERE workspace_id = %s AND asset_id = %s::uuid AND id = %s::uuid''',
            (workspace_id, asset_id, authorization_id),
        )
    elif asset_id and external_reference:
        issuance_lookup = (
            '''SELECT id, operation, amount, settlement_state, external_reference
               FROM asset_authorized_issuances
               WHERE workspace_id = %s AND asset_id = %s::uuid AND external_reference = %s::text
               ORDER BY authorized_at DESC
               LIMIT 1''',
            (workspace_id, asset_id, external_reference),
        )
    if issuance_lookup and _table_exists(connection, 'asset_authorized_issuances', unreadable):
        try:
            with read_scope(connection):
                issuance = _row_dict(connection.execute(*issuance_lookup).fetchone())
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
        with read_scope(connection):
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


def policy_scope_governed(
    connection: Any, *, workspace_id: str, asset_id: Optional[str],
) -> Optional[bool]:
    """Does ANY ACTIVE policy govern this workspace/asset, whatever the operation?

    THE single definition of that predicate. Screen 8's gate calls this same
    function (``response_gate.service._policy_governs``) rather than running its
    own copy of the query, because the producer and the gate disagreeing about it
    is exactly the defect this function exists to prevent:

        governing_policy() asks "which ACTIVE policy governs THIS OPERATION?"
        this asks           "is this workspace/asset inside ANY ACTIVE policy?"

    When the operation cannot be established the first returns None while the
    second still returns True. The producer used to write nothing in that case
    and the gate reported POLICY_EVALUATION_MISSING — a state no operator could
    ever clear, because the only thing that could clear it was the row the
    producer had declined to write. ``evaluate_response_action`` now records a
    deterministic fail-closed DENY there instead.

    Returns None when the probe could not be READ. "We could not look" is not
    "there is none", and neither is an authorization.
    """
    asset = str(asset_id or '').strip() or None
    try:
        with read_scope(connection):
            row = connection.execute(
                f"""SELECT 1 AS present FROM {service.POLICIES_TABLE}
                    WHERE workspace_id = %s AND status = %s
                      AND (asset_id IS NULL OR (%s::uuid IS NOT NULL AND asset_id = %s::uuid))
                    LIMIT 1""",
                (workspace_id, gpc.STATUS_ACTIVE, asset, asset),
            ).fetchone()
    except Exception:
        logger.exception('governance_enforcement_policy_scope_read_failed workspace_id=%s', workspace_id)
        return None
    return bool(_row_dict(row).get('present'))


def scope_policy_refs(
    connection: Any, *, workspace_id: str, asset_id: Optional[str], limit: int = 20,
) -> tuple[dict[str, Any], ...]:
    """The ACTIVE policies covering this workspace/asset, as audit references.

    Stored in a fail-closed row's input snapshot so the record answers the
    operator's next question — "which policies were in force, and why did none
    of them decide this?" — from the row itself rather than from a later query
    against state that may have changed since.

    These are REFERENCES, not an attribution. None of them governs the operation
    (that is why the row is fail-closed), so none is written to the evaluation's
    ``policy_id``: naming one there would report a verdict a policy did not
    reach. Best-effort — a failed read yields no references and never blocks the
    fail-closed DENY, which is the fact that actually matters.
    """
    asset = str(asset_id or '').strip() or None
    try:
        with read_scope(connection):
            rows = connection.execute(
                f"""SELECT id, policy_key, version, operation, asset_id
                    FROM {service.POLICIES_TABLE}
                    WHERE workspace_id = %s AND status = %s
                      AND (asset_id IS NULL OR (%s::uuid IS NOT NULL AND asset_id = %s::uuid))
                    ORDER BY (asset_id IS NOT NULL) DESC, updated_at DESC
                    LIMIT %s""",
                (workspace_id, gpc.STATUS_ACTIVE, asset, asset, int(limit)),
            ).fetchall()
    except Exception:
        logger.warning(
            'governance_enforcement_policy_scope_refs_failed workspace_id=%s', workspace_id,
        )
        return ()
    refs: list[dict[str, Any]] = []
    for row in rows or ():
        data = _row_dict(row)
        if not data:
            continue
        refs.append({
            'policy_id': _text(data.get('id')),
            'policy_key': _text(data.get('policy_key')),
            'policy_version': data.get('version'),
            'operation': _text(data.get('operation')),
            'asset_scoped': bool(data.get('asset_id')),
        })
    return tuple(refs)


def existing_evaluation(
    connection: Any, *, workspace_id: str, policy_id: Optional[str], fact_digest: str,
) -> Optional[dict[str, Any]]:
    """An ENFORCEMENT evaluation already recorded for exactly these facts.

    Workspace-scoped and simulation-excluded, matching the same row Screen 8
    would read. Returns None when none exists OR when the lookup could not run —
    the caller then evaluates and writes, which is the safe direction: a missed
    match records a duplicate decision, while a false match would suppress a real
    re-evaluation.

    ``policy_id`` is NULL for a fail-closed row (no policy could be matched to the
    operation), so the comparison is IS NOT DISTINCT FROM rather than ``=``:
    ``policy_id = NULL`` is never true, and a plain ``=`` would make every
    re-evaluation of such an action append another identical DENY.
    """
    if not fact_digest:
        return None
    try:
        with read_scope(connection):
            row = connection.execute(
                f'''SELECT id, decision, policy_version, evaluated_at
                    FROM {service.EVALUATIONS_TABLE}
                    WHERE workspace_id = %s AND policy_id IS NOT DISTINCT FROM %s::uuid
                      AND simulation = FALSE
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

    A verdict is recorded whenever one can be reached deterministically — which
    includes the case where the governing policy could NOT be resolved but an
    ACTIVE policy covers this workspace/asset. That records a fail-closed DENY
    (``STATUS_RECORDED_FAIL_CLOSED``) rather than nothing, because "nothing" is
    what the gate reports as POLICY_EVALUATION_MISSING and no operator can clear.

    ``user_id`` is recorded as WHO TRIGGERED the evaluation. It is not an input to
    the decision, and it is never offered to the policy as the operation's
    operator: no caller's own permissions can satisfy a policy requirement here
    (see ``resolve_action_facts``).
    """
    if not service.storage_ready(connection):
        return EnforcementOutcome(status=STATUS_STORAGE_UNAVAILABLE)

    facts = resolve_action_facts(connection, workspace_id=workspace_id, action=action)
    if facts.material_unreadable:
        # A MATERIAL fact we could not read is not a fact, and without it there is
        # no scope to reason inside. Record nothing: Screen 8 then reports
        # POLICY_EVALUATION_MISSING and stays LOCKED, which is the honest answer,
        # rather than an ALLOW — or a DENY — built on a scope nobody established.
        service.log_event(
            'governance_policy_enforcement_facts_unavailable', workspace_id=workspace_id,
            action_id=str(action.get('id') or ''),
            unreadable=','.join(facts.material_unreadable),
            fact_class='material',
        )
        return EnforcementOutcome(status=STATUS_FACTS_UNAVAILABLE, facts=facts)

    # A SUPPLEMENTARY fact that could not be read leaves the operation CONTEXT
    # incomplete while the action's scope is still known. Two consequences, and
    # both matter:
    #
    #   1. No policy is matched from that context. The observed operation, amount,
    #      business event and settlement state are precisely what a policy would
    #      be judged against, and half of them are missing — so this takes the
    #      same branch as "the operation could not be established", which records
    #      a fail-closed DENY rather than a verdict under rules the facts could
    #      not be checked against. A missing fact can therefore never become an
    #      ALLOW, which is the whole point.
    #
    #   2. Something IS still written. Declining to write is what left a
    #      producer-only read failure invisible: the GATE reads fewer facts than
    #      this producer does, so it saw a healthy chain, found no evaluation, and
    #      reported POLICY_EVALUATION_MISSING — a state no operator could clear,
    #      because the only thing that could clear it was the row this module had
    #      declined to write.
    supplementary_gap = facts.supplementary_unreadable

    policy: Optional[PolicyDefinition] = None
    if not supplementary_gap:
        try:
            policy = governing_policy(
                connection, workspace_id=workspace_id,
                operation=facts.operation, asset_id=facts.asset_id,
            )
        except Exception:
            # The policy read is MATERIAL: without it neither branch below can be
            # told apart, so nothing is recorded.
            return EnforcementOutcome(
                status=STATUS_FACTS_UNAVAILABLE,
                facts=replace(facts, unreadable=facts.unreadable + ('governing_policy',)),
            )

    # No policy could be matched to this OPERATION. That is two different
    # situations, and conflating them is what parked every recommended action at
    # POLICY_EVALUATION_MISSING:
    #
    #   nothing governs this workspace/asset at all
    #       -> write nothing. Screen 8's gate reports NOT_APPLICABLE from this
    #          same probe and is not blocked on an evaluation, so there is no
    #          state to clear and a recorded refusal would invent one for an
    #          action the workspace never chose to govern.
    #
    #   an ACTIVE policy DOES cover this workspace/asset, but the operation it
    #   would be judged against could not be established (no threat detection
    #   behind the incident, or one that names no operation — the ordinary case
    #   for an incident opened from an operational alert), or no ACTIVE policy
    #   governs the operation that WAS established
    #       -> record a deterministic FAIL-CLOSED DENY. The gate's scope probe
    #          says a policy governs, so it reports POLICY_EVALUATION_MISSING and
    #          LOCKED until an enforcement row exists; declining to write one left
    #          the action permanently unauthorizable, with no operator action that
    #          could change it. The row states exactly what could not be
    #          established and denies on it. It is produced by the same engine,
    #          on the same absent facts — never a relabelled simulation, and never
    #          an ALLOW.
    fail_closed = False
    if policy is None:
        governed = policy_scope_governed(
            connection, workspace_id=workspace_id, asset_id=facts.asset_id,
        )
        if governed is None:
            # The probe itself could not be read, so which of the two situations
            # holds is unknown. Record nothing rather than deny on a fact nobody
            # established; the gate stays closed either way.
            return EnforcementOutcome(
                status=STATUS_FACTS_UNAVAILABLE,
                facts=EnforcementFacts(
                    **{**facts.__dict__, 'unreadable': facts.unreadable + ('policy_scope',)},
                ),
            )
        if not governed:
            # NOTHING governs this workspace/asset — not this operation, not any
            # other. True whether or not a supplementary fact was readable: the
            # scope probe reads none of them. Screen 8 reports NOT_APPLICABLE from
            # this same probe and is not blocked on an evaluation, so no row is
            # owed and a recorded refusal would invent one.
            service.log_event(
                'governance_policy_enforcement_no_policy', workspace_id=workspace_id,
                action_id=str(action.get('id') or ''), operation=facts.operation,
                unreadable=','.join(supplementary_gap) or None,
            )
            return EnforcementOutcome(status=STATUS_NO_POLICY, facts=facts)
        fail_closed = True
        # Recorded in the snapshot, NOT in policy_id: see scope_policy_refs.
        facts = replace(facts, sources={
            **dict(facts.sources or {}),
            'scope_policies': list(scope_policy_refs(
                connection, workspace_id=workspace_id, asset_id=facts.asset_id,
            )),
        })

    action_id = str(action.get('id') or '') or None
    fact_digest = facts.digest(
        policy_id=policy.policy_id if policy else None,
        policy_version=policy.version if policy else None,
        response_action_id=action_id,
    )
    # Idempotency. Re-evaluating an action whose facts and governing version have
    # not changed must NOT write a second decision: under a capped policy the
    # duplicate ALLOW would consume the day's issuance limit twice for one
    # operation, and the second write would eventually deny a legitimate one.
    already = existing_evaluation(
        connection, workspace_id=workspace_id,
        policy_id=policy.policy_id if policy else None, fact_digest=fact_digest,
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
    # With no policy it takes its own step-1 terminal branch and returns
    # DENY / POLICY_NOT_FOUND — the fail-closed row is that verdict, not a
    # separately invented one, and not a simulation copied across.
    decision = engine.evaluate_policy(policy, context, now=now)
    if supplementary_gap:
        # Name what could not be READ, distinctly from what was read and found
        # wanting. Appended rather than substituted, so the engine's own
        # POLICY_NOT_FOUND is still the first code an auditor sees, and the row
        # states plainly that this is a refusal for want of facts.
        decision = replace(
            decision,
            reason_codes=decision.reason_codes + tuple(
                code for code in facts_unavailable_reason_codes(supplementary_gap)
                if code not in decision.reason_codes
            ),
        )
    if fail_closed and facts.operation is None and not supplementary_gap:
        # Name the missing link. POLICY_NOT_FOUND alone reads as "the workspace
        # authored no policy for this", which is not what happened: policies
        # exist, and the operation to match one against is what could not be
        # established. Appended, never substituted, so the engine's own code is
        # still the first one an auditor sees.
        decision = replace(
            decision,
            reason_codes=decision.reason_codes + (gpc.OPERATION_NOT_ESTABLISHED,),
        )
    recorded = service.record_evaluation(
        connection, workspace_id=workspace_id, decision=decision,
        context=context, user_id=user_id,
    )
    service.log_event(
        'governance_policy_enforcement_evaluated', workspace_id=workspace_id,
        action_id=str(action.get('id') or ''), policy_key=decision.policy_key,
        policy_version=decision.policy_version, decision=decision.decision,
        evaluation_id=decision.evaluation_id, recorded=recorded,
        fail_closed=fail_closed or None,
        reason_codes=','.join(decision.reason_codes) or None,
    )
    if not recorded:
        return EnforcementOutcome(
            status=STATUS_WRITE_FAILED, decision=decision, policy=policy,
            facts=facts, recorded=False,
        )
    return EnforcementOutcome(
        status=STATUS_RECORDED_FAIL_CLOSED if fail_closed else STATUS_RECORDED,
        decision=decision, policy=policy, facts=facts, recorded=True,
    )
