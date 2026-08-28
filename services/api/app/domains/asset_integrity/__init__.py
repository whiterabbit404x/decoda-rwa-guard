"""Asset Integrity / Reconciliation domain (Screen 3 — Integrity tab).

The deterministic RWA operational-integrity layer. It answers one question that
a blockchain alone cannot: *was this cryptographically valid state change also
operationally authorized?*

    ON-CHAIN OBSERVATION  +  AUTHORITATIVE OFF-CHAIN STATE
              -> DETERMINISTIC MATCHER
              -> RECONCILIATION RESULT (status + reason code)
              -> AI MAY EXPLAIN THE RESULT

Modules:
  reconciliation  — pure, Decimal-safe matcher + variance math. No I/O, no clock
                    reads beyond what the caller passes in, no AI. This module is
                    the single source of truth for status, reason code, variance
                    and deterministic severity.
  config          — environment-driven freshness thresholds and the rule
                    id/version stamped onto every snapshot.
  ai_explanation  — schema-validated narrative with a deterministic fallback. It
                    receives already-computed facts and never produces numbers,
                    statuses, reason codes or severity.
  service         — DB-backed evaluation: read the latest observation and
                    authoritative state, run the pure engine, persist an
                    immutable snapshot, and emit the canonical
                    operational-integrity event into threat_detections.
  endpoints       — request-level handlers. GET is strictly side-effect free.

This package must not import from services.api.app.main. It may import
services.api.app.pilot for shared DB / auth utilities (matching the existing
domain convention).
"""
