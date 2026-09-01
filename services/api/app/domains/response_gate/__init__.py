"""Response Gate (Screen 8) — the deterministic execution gate.

The lane this package implements exists because of one rule:

    AI may recommend. Deterministic policy controls execution.

    Operational anomaly
        -> deterministic policy evaluation (Screen 11)
        -> AI recommends a playbook (narrative only)
        -> human quorum approval
        -> DETERMINISTIC EXECUTION GATE   <- this package
        -> authorized response execution

Modules:
  config  — canonical vocabulary: the two authority constants, the gate
            decisions, the machine-readable reason codes, and the governance
            role -> workspace-permission map reused from Screen 11.
  engine  — ``evaluate_gate(inputs) -> ExecutionGate``. A pure function over
            already-resolved facts. This is the ONLY code in the product that
            decides whether a response action may execute.
  service — workspace-scoped DB reads that resolve those facts: the canonical
            policy evaluation Screen 11 recorded, the role-scoped approval
            decisions, and the incident state.

Trust boundary
--------------
``engine.evaluate_gate`` imports ``config`` and the standard library. It has no
database handle, no HTTP client, no clock it did not receive, and no import path
that reaches ``ai_providers``. An LLM therefore cannot participate in an
execution decision even by accident: there is no seam through which one could be
called. AI output is not an input to this function — the engine has no parameter
that could carry it.

Fail-closed
-----------
Every path that cannot establish a fact leaves the gate LOCKED with an explicit
reason code. There is no branch that reaches ``can_execute = True`` from missing,
unreadable, or malformed input.

This package must not import from services.api.app.main. It may import
services.api.app.pilot for shared DB/auth utilities, matching the existing
domain convention.
"""

from services.api.app.domains.response_gate import config, engine  # noqa: F401
from services.api.app.domains.response_gate.config import (  # noqa: F401
    AI_AUTHORITY,
    EXECUTION_AUTHORITY,
)
from services.api.app.domains.response_gate.engine import (  # noqa: F401
    ExecutionGate,
    GateInputs,
    evaluate_gate,
)
