# Feature 1 runtime contamination audit

Date: 2026-04-04

This audit captures the final fail-closed fixes so `monitoring_demo_scenario` and synthetic/demo outputs cannot influence LIVE/HYBRID runtime truth.

| File path | Old behavior | Why unsafe | New behavior | Disposition |
|---|---|---|---|---|
| `services/api/app/activity_providers.py` | `MONITORING_INGESTION_MODE=degraded` was coerced to `hybrid`; synthetic/demo generators still existed in the same runtime function and mode checks were partly lowercase-specific. | Coercion blurred runtime truth and risked accidental branch reuse; mixed-case mode comparisons made guard correctness fragile. | `monitoring_ingestion_mode()` now preserves the configured mode (`degraded` stays `degraded`), mode checks are normalized, and `degraded` returns explicit `DEGRADED_EVIDENCE` fail-closed results with `claim_safe=false` and no synthetic output. | Removed unsafe fallback semantics; DEMO path remains isolated. |
| `services/api/app/monitoring_runner.py` (`_normalize_event`) | Event metadata always included `monitoring_demo_scenario` key, even in non-demo runtime, and repeatedly re-resolved demo scenario fields. | Leaving demo-specific metadata in non-demo payload shapes can create audit ambiguity and accidental downstream coupling. | Demo metadata is now attached only when a demo scenario is actually active; LIVE/HYBRID payloads no longer carry demo scenario markers. | Isolated DEMO markers behind explicit demo boundary. |
| `services/api/app/monitoring_runner.py` (state mapping) | Evidence/truthfulness mapping logic was duplicated inline. | Duplicated mapping risks drift and inconsistent semantics across provider, validator, and runtime status. | Mapping now uses shared truth helpers (`ui_evidence_state`, `ui_truthfulness_state`) to preserve explicit no-evidence/degraded/failed semantics. | Hardened shared truth model usage. |

## Hard-boundary outcome

- `monitoring_demo_scenario` remains configurable for DEMO workflows only.
- LIVE/HYBRID providers return only real evidence states (`REAL_EVIDENCE`) or explicit uncertainty/failure states (`NO_EVIDENCE`, `DEGRADED_EVIDENCE`, `FAILED_EVIDENCE`).
- No LIVE/HYBRID branch emits synthetic/demo evidence or synthetic-success fallback output.
- No-evidence and degraded states remain explicit and persisted with `claim_safe=false`.
