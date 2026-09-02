"""Governance & Policy (Screen 11) — deterministic operation policy evaluation.

The lane this package implements exists because of one rule:

    An operational event is judged ALLOW or DENY by deterministic code reading
    stored policy constraints. Nothing else may produce that judgement.

    Operational event
        -> deterministic policy evaluation
        -> ALLOW / DENY
        -> human-readable explanation (narrative only)
        -> Screen 8 response authorization gate

Modules:
  config      — canonical vocabulary: operations, policy statuses, business
                events, settlement states, reason codes, the governance-role ->
                workspace-permission map, and env-driven configuration.
  schemas     — the policy definition, the evaluation context, the structured
                checks and the PolicyDecision. Pure data: no DB, no clock, no
                network, no AI.
  engine      — evaluate_policy(policy, context) -> PolicyDecision. A pure
                function over the two inputs. This is the ONLY code in the
                product that decides ALLOW or DENY for an operation policy.
  explanation — narrative ONLY. Receives an already-decided PolicyDecision and
                turns it into a sentence. merge_ai_explanation() is the
                enforcement point: an AI payload can only ever land in
                ``ai_explanation``; a decision, a reason code, a version, or an
                approval it tried to set is dropped and counted.
  service     — workspace-scoped DB reads/writes: policies, immutable version
                history, the server-resolved evaluation context, and the
                evaluation record Screen 8 consumes.
  enforcement — the ENFORCEMENT producer. Runs the same ``engine`` against facts
                read from canonical rows (detection -> alert -> incident ->
                response action, plus the authorized issuance) and persists the
                verdict with ``simulation = FALSE``. This is the only writer of
                the rows Screen 8's execution gate consumes; it copies no
                simulation and accepts no decision from a caller.
  endpoints   — request handlers. RBAC and tenant isolation reuse the canonical
                pilot helpers; nothing here invents a permission model.

Trust boundary
--------------
``engine.evaluate_policy`` imports ``config`` and ``schemas`` and nothing else.
It has no I/O of any kind, so an LLM cannot participate in a decision even
accidentally: there is no seam through which one could be called. The AI layer
is handed the finished decision as an INPUT and has nothing left to decide.

Fail-closed
-----------
Every path that cannot establish a fact returns DENY with an explicit reason
code. A missing policy, a disabled policy, an unreadable settlement state, an
unresolvable daily total under a capped policy — all DENY. There is no code path
in which an error, an outage, or absent data produces ALLOW.

Simulation vs enforcement
-------------------------
A Screen 11 simulation is predictive and read-only. It queries policy and
configuration state, and it writes exactly one row: a
``governance_policy_evaluations`` record stamped ``simulation = TRUE``, which is
excluded from every production counter. It cannot execute an action, approve
anything, or mutate policy, incident, or settlement state.

An ENFORCEMENT evaluation (``enforcement.evaluate_response_action``) is the other
kind, and the only one that can authorize anything downstream. It runs the SAME
deterministic engine, but its inputs come from canonical backend rows rather than
from a request body, and its row is stamped ``simulation = FALSE``. A simulation
is never copied, relabelled, or promoted into one: the two are produced by
different call paths reading different inputs, and Screen 8 reads only the
second.

This package must not import from services.api.app.main. It may import
services.api.app.pilot for shared DB/auth utilities, matching the existing
domain convention.
"""
