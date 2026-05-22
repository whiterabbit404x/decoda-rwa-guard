# Runtime Truthfulness — Canonical Signal Semantics

_Session 13 — added 2026-05-22_

## Purpose

The runtime status system must never look healthier than it really is.  This
document defines the canonical signal taxonomy, freshness rules, contradiction
guards, and safe status derivation logic used by Decoda RWA Guard.

---

## Canonical Signal Timestamps

Each timestamp is independent.  A downstream signal must **never** be inferred
from its upstream counterpart.

| Field | Proves | Must NOT be inferred from |
|---|---|---|
| `last_heartbeat_at` | The worker/service process is alive | nothing |
| `last_poll_at` | The monitoring loop attempted provider work | heartbeat |
| `last_telemetry_at` | Monitored data actually arrived | heartbeat, poll |
| `last_detection_at` | Telemetry was evaluated for risk | telemetry alone |
| `last_alert_at` | Detection created a customer-facing risk signal | detection alone |
| `last_incident_at` | Alert was escalated into a case/workflow | alert alone |
| `last_response_action_at` | System recommended or executed an action | incident alone |
| `last_evidence_export_at` | The chain can be exported as evidence | response alone |

---

## Freshness Thresholds (defaults)

| Signal | Threshold | Meaning |
|---|---|---|
| heartbeat | 300 s (5 min) | Worker has checked in recently |
| poll | 600 s (10 min) | Loop ran recently |
| telemetry | 900 s (15 min) | Data arrived recently |
| detection | 1800 s (30 min) | Evaluation ran recently |
| alert | 1800 s (30 min) | Alert was created recently |
| incident | 3600 s (60 min) | Incident is active recently |
| response_action | 3600 s (60 min) | Action was taken recently |
| evidence_export | 86400 s (24 h) | Export is recent |

Thresholds are defined in `services/api/app/runtime_truthfulness.py`:
`FRESHNESS_THRESHOLDS_SECONDS`.

### Freshness Values

| Value | Meaning |
|---|---|
| `current` | Timestamp exists and age ≤ threshold |
| `stale` | Timestamp exists but age > threshold |
| `unavailable` | No timestamp (signal never observed) |
| `unknown` | Timestamp present but invalid/unparseable |

`unavailable` and `unknown` must never be treated as `current` or `stale`.

---

## Signal Freshness Object (`signal_freshness`)

The canonical runtime summary includes a `signal_freshness` dict with one entry
per signal:

```json
{
  "signal_freshness": {
    "heartbeat": "current",
    "poll": "current",
    "telemetry": "unavailable",
    "detection": "unavailable",
    "alert": "unavailable",
    "incident": "unavailable",
    "response_action": "unavailable",
    "evidence_export": "unavailable"
  }
}
```

---

## Contradiction Guards

The following flags are raised when impossible or misleading runtime states
are detected.  If **any** flag is set, `runtime_status` must not be `healthy`.

| Flag | Condition |
|---|---|
| `healthy_without_reporting_systems` | `runtime_status == 'healthy'` and `reporting_systems == 0` |
| `current_without_telemetry` | `freshness_status` is current/fresh but `last_telemetry_at` is null |
| `offline_with_current_telemetry` | `runtime_status == 'offline'` but telemetry signal is current |
| `live_mode_with_simulator_evidence` | `monitoring_mode == 'live'` but `evidence_source == 'simulator'` |
| `live_evidence_without_provider_ready` | `evidence_source == 'live_provider'` but `provider_ready == False` |
| `systems_without_protected_assets` | `configured_systems > 0` but `protected_assets == 0` |
| `reporting_exceeds_configured` | `reporting_systems > configured_systems > 0` |
| `detection_without_telemetry` | `last_detection_at` present but `last_telemetry_at` is null |
| `alert_without_detection` | `last_alert_at` present but `last_detection_at` is null |
| `incident_without_alert` | `last_incident_at` present but `last_alert_at` is null |
| `response_action_without_case` | `last_response_action_at` present but no incident or alert |
| `evidence_export_without_source_truthfulness` | `last_evidence_export_at` present but `evidence_source` is unknown/none |

Additional guards inherited from the canonical summary builder:
`offline_with_current_telemetry`, `telemetry_unavailable_with_high_confidence`,
`live_monitoring_without_reporting_systems`, `alert_exists_without_detection`,
`incident_exists_without_alert`, `response_action_exists_without_incident`,
`evidence_package_without_detection_alert_incident_chain`, and others.

### Rules

- If `contradiction_flags` is non-empty → `runtime_status` must not be `'healthy'`
- If `contradiction_flags` is non-empty → `confidence_status` must be `'low'` or `'unavailable'`
- If telemetry is unavailable → `freshness_status` must not be `'current'`
- If `evidence_source == 'simulator'` → paid-launch readiness must not be `ready`
- `unknown` must never be treated as healthy

---

## Status Reason Examples

Safe `status_reason` values that are meaningful without overclaiming:

| Scenario | Example `status_reason` |
|---|---|
| Guard fired | `guard:healthy_without_reporting_systems` |
| DB unavailable | `Monitoring persistence unavailable` |
| Contradiction | `runtime_contradiction_healthy_without_reporting_systems` |
| Partial failure | `runtime_status_degraded:partial_query_failure` |
| No config | `workspace_not_configured` |

Forbidden `status_reason` patterns:
- Anything claiming "healthy" when `reporting_systems == 0`
- Anything claiming "live" when `last_telemetry_at` is null
- Anything claiming "high" confidence when contradictions exist

---

## Blocked Misleading Statuses

| Misleading claim | Why it is blocked |
|---|---|
| `runtime_status: healthy` with 0 reporting systems | No systems are actually delivering data |
| `freshness_status: current` with no telemetry timestamp | Freshness cannot be claimed without evidence |
| `evidence_source: live_provider` with simulator data | Mislabels simulated data as live proof |
| `confidence: high` with contradictions present | Contradictions invalidate confidence |
| `monitoring_status: live` with no telemetry | Live monitoring requires actual telemetry |
| Heartbeat inferred as telemetry | Heartbeat only proves the process is alive |
| Poll inferred as telemetry | Poll only proves the loop attempted work |

---

## Helper Functions

Defined in `services/api/app/runtime_truthfulness.py`:

| Function | Purpose |
|---|---|
| `compute_signal_freshness(ts, now, threshold_s)` | Returns `current/stale/unavailable/unknown` for one signal |
| `build_signal_freshness(...)` | Returns per-signal freshness dict for all 8 signals |
| `detect_runtime_contradictions(...)` | Returns sorted list of session-13 contradiction flags |
| `derive_runtime_status(...)` | Returns safe `runtime_status` that never overclaims healthy |
| `derive_confidence_status(...)` | Returns safe `confidence_status` |

---

## Running Tests

```bash
# Session 13 runtime truthfulness tests
pytest services/api/tests/test_runtime_truthfulness.py -q

# Existing admin, paid launch, and release proof tests
pytest services/api/tests/test_admin_readiness.py \
       services/api/tests/test_paid_launch_readiness.py \
       services/api/tests/test_release_proof_artifacts.py \
       -q
```

---

## Important Limitations

Runtime truthfulness improvements (Session 13) increase customer trust and
operational safety.  They do **not** by themselves make the product broad paid
SaaS ready.

Broad paid SaaS readiness additionally requires:

- Billing provider (Stripe/Paddle) configured and verified
- Email provider configured
- Live EVM provider (`EVM_RPC_URL`) pointing to a real non-placeholder endpoint
- `paid_launch_ready=true` from all four Session 10 launch gates
- CI/release evidence from Session 11
- Customer-facing evidence export validation from Session 12
- Staging launch validation
