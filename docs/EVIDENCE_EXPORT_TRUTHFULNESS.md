# Evidence Export Truthfulness

**Scope:** Proof bundle exports, evidence packages, and audit logs for Decoda RWA Guard.

---

## Evidence Source Types

| Type | Meaning | Customer-Facing Label |
|------|---------|----------------------|
| `live` | Backed by real on-chain telemetry from a configured provider | `live_provider` (green) |
| `simulator` | Produced by guided simulator or demo workflow | `simulator` (blue) |
| `unavailable` | Source expected but unreachable or returned fallback data | `unavailable` (yellow) |
| `missing` | No source records found for this chain | `missing` (gray) |
| `unknown` | Source field present but unrecognized value | `unknown` (gray) |

**Rules:**
- `fallback` API responses must be labeled `unavailable`, never `simulator`.
- `simulator` label is only correct when evidence was explicitly produced by simulator/demo/guided-simulator sources.
- `live` is only valid when `evidence_source = 'live'` or `'live_provider'` is set on linked alert/detection records.

---

## Export Status Values

| Status | Meaning |
|--------|---------|
| `complete` | All four core sections present: alerts, detections, response_actions, telemetry_evidence |
| `partial` | Some core sections missing but at least one is present |
| `incomplete` | No core chain sections found (alerts, detections, evidence all absent) |

A `partial` or `incomplete` proof bundle must never be presented as a complete verification artifact.

---

## Proof Bundle Chain

The proof bundle follows the canonical evidence chain:

```
Telemetry Event
  → Detection
    → Alert
      → Incident
        → Response Action
          → Audit Log
            → Export (summary + artifacts)
```

Each section has a corresponding file in the proof bundle JSON:

| File | Contents |
|------|----------|
| `summary.json` | Bundle identity, status, evidence_source_type, missing_sections, warnings |
| `incidents.json` | Linked incident record |
| `alerts.json` | Alerts linked via detection_metrics |
| `detections.json` | Detections linked via alert IDs |
| `response_actions.json` | Response actions for the incident |
| `audit_log.json` | Audit entries for the incident and export |
| `evidence.json` | Raw evidence fields from detection_metrics |
| `detection_metrics.json` | Full or filtered detection metric records |

---

## Missing Sections

If any chain section is absent, `missing_sections` lists the absent parts and `export_status` is set to `partial` or `incomplete`. The `summary.json` `warnings` field will include a human-readable description of what is missing.

Missing sections that appear in current deployments:
- `audit_log` — audit_logs table may not be populated for all incidents (non-blocking)
- `response_actions` — if no action has been created for the incident yet
- `detections` — if detections are not linked via `linked_alert_id` (older schema)

---

## What Blocks Production-Grade Proof

A proof bundle cannot serve as live production evidence when:

1. `evidence_source_type` is `simulator`, `unavailable`, `missing`, or `unknown`
2. `export_status` is `partial` or `incomplete`
3. `chain_complete` is `false` in `summary.json`
4. Any `warnings` are present about missing or simulator data

---

## Simulator vs Live Evidence

| Condition | Correct Label |
|-----------|--------------|
| `alerts.source = 'live_provider'` | `live` |
| `alerts.source = 'simulator'` or `'guided_simulator'` | `simulator` |
| `alerts.source = 'fallback'` | `unavailable` |
| `detections.evidence_source = 'live'` | contributes to `live` |
| `detections.evidence_source = 'simulator'` | contributes to `simulator` |
| No alerts or detections | `missing` |

If both live and simulator sources are present, `live` takes precedence.

---

## Workspace Scoping

All proof bundle queries are workspace-scoped:
- `export_jobs.workspace_id` must match the requesting workspace
- `incidents.workspace_id` must match — if the incident ID exists in another workspace, a 404 is returned
- `alerts`, `detections`, `response_actions`, `audit_logs` are all filtered by `workspace_id`

Cross-workspace export attempts are rejected at the incident lookup step.

---

## How to Test Export Behavior

Run the proof bundle export tests:

```bash
cd /home/user/decoda-rwa-guard
python -m pytest services/api/tests/test_proof_bundle_export.py -v
python -m pytest services/api/tests/test_assets_and_exports_foundations.py -v
```

Key test scenarios covered:
- Complete chain with live evidence → `export_status=complete`
- Complete chain with simulator evidence → labeled `simulator`
- Missing response actions → `export_status=partial`
- No alerts → `export_status=incomplete`, `evidence_source_type=missing`
- Cross-workspace incident ID → 404
- No raw secrets in export content

---

## Remaining Gaps

- **Telemetry event IDs** are not yet linked from detection_metrics to telemetry_events directly in the proof bundle. The `evidence.json` file contains the JSONB evidence field from detection_metrics which may include telemetry context.
- **Full evidence lineage**: Supported as `partial`. The chain from telemetry_event → detection → alert → incident → action → export is followed, but telemetry_events table is not directly queried in the proof bundle (it's represented through detection_metrics).
- **Proof bundle for incidents without detection_metrics linkage**: If an incident was created without going through the detection_metrics path, some sections will appear as missing/partial.
