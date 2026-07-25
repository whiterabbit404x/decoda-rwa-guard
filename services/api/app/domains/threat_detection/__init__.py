"""Threat Detection Engineer domain.

The autonomous, deterministic threat-detection engine behind Screen 5 (Threat
Monitoring). It correlates normalized on-chain telemetry into evidence-grounded
detections and sub-threshold anomalies. AI is used only to summarize already-
computed evidence — never to invent transactions, actors, findings, attack
types, confidence, or severity.

Modules:
  scoring         — pure severity/confidence math, evidence-quality ranking, and
                    the stable cluster_key. No I/O; fully unit-testable.
  detectors       — pure detector rules over extracted telemetry features
                    (unusual fund movement, coordinated low-and-slow, mint/burn
                    irregularity, privileged action). Detectors that require call
                    traces / oracle observations the platform does not have are
                    declared UNSUPPORTED and never false-positived.
  config          — environment-driven thresholds / worker cadence / detector
                    support map (single source of truth for engine + tests).
  summary         — canonical ThreatMonitoringSummary builder (read-only). The
                    frontend never recomputes any count.
  service         — DB-backed engine: incremental telemetry read, detector
                    evaluation, cluster upsert + evidence attach, and the
                    idempotent Start Investigation action.
  ai_explanation  — schema-validated AI narrative with a deterministic fallback;
                    rejects any summary that cites unsupplied evidence.
  worker          — the run-once detection cycle (checkpoint + heartbeat) used by
                    the worker entrypoint.
  endpoints       — request-level wrappers (auth + workspace scoping) for the
                    summary / detections / anomalies / investigate routes.

This package must not import from services.api.app.main. It may import
services.api.app.pilot for shared DB / auth utilities (matching the existing
domain convention).
"""
