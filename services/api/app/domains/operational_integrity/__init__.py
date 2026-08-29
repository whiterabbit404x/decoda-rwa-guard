"""Operational Integrity detections (Screen 5 — Threat Monitoring).

The lane this package implements exists because of one fact about tokenized
real-world assets:

    A transaction can be cryptographically valid and still be operationally
    unauthorized.

A mint may carry a valid signature, be included on-chain, and pass every
consensus rule — while no subscription exists, settlement has not cleared, the
transfer agent authorizes nothing, and no policy permits it. Screen 5's existing
cyber lane looks for behavioral/exploit patterns; this lane reconciles the
on-chain event against the AUTHORITATIVE off-chain business state.

Modules:
  config          — canonical category/detection-type/reason-code vocabulary,
                    detector support map, and env-driven thresholds.
  schemas         — the canonical operational event object and the structured
                    PASS / FAIL / UNKNOWN check records. Pure data, no I/O.
  normalization   — provider-agnostic telemetry normalization, including the
                    PreconfirmationTelemetryProvider interface and the TRUTHFUL
                    resolution of telemetry source/stage. A stage is only ever
                    reported as PRECONFIRMATION when the ingestion path actually
                    delivered a preconfirmation.
  matcher         — the deterministic operational matcher. Given a normalized
                    on-chain event plus authoritative business records it emits
                    the checks, the reason code, the detection type, and the
                    severity. No LLM, no float arithmetic.
  service         — DB-backed, workspace-scoped evaluation and the idempotent
                    upsert into the EXISTING threat_detections table.
  explanation     — narrative ONLY. The AI layer receives an immutable,
                    already-decided detection object and may never change a
                    deterministic field.

Trust boundary: everything a customer can act on (amounts, checks, reason code,
severity, confidence, status) is computed here deterministically. AI output is
confined to ``ai_summary``.

This package must not import from services.api.app.main. It may import
services.api.app.pilot for shared DB/auth utilities, matching the existing
domain convention.
"""
