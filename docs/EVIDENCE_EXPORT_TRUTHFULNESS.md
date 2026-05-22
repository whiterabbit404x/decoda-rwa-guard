# Evidence Export Truthfulness

**Scope:** Proof bundle exports, evidence packages, and audit logs for Decoda RWA Guard.

**Last updated:** Session 12 — Customer-Facing Evidence Export Quality

---

## Evidence Source Types

| Type | Meaning | Customer-Facing Label |
|------|---------|----------------------|
| `live` | Backed by real on-chain telemetry from a configured provider | `live_provider` (green) |
| `simulator` | Produced by guided simulator or demo workflow | `simulator` (blue) |
| `fixture` | Static test fixture evidence (test/dev only) | `fixture_only` (gray) |
| `unavailable` | Source expected but unreachable or returned fallback data | `unavailable` (yellow) |
| `missing` | No source records found for this chain | `missing` (gray) |
| `unknown` | Source field present but unrecognized value | `unknown` (gray) |

**Rules:**
- `fallback` API responses must be labeled `unavailable`, never `simulator`.
- `simulator` label is only correct when evidence was explicitly produced by simulator/demo/guided-simulator sources.
- `live` is only valid when `evidence_source = 'live'` or `'live_provider'` is set on linked alert/detection records.
- `unknown` source must never be treated as `live_provider`.

---

## Package Status Values

| Status | Meaning |
|--------|---------|
| `complete` | All required sections present AND source truthfulness is known (not missing/unknown) |
| `partial` | Some core sections missing but at least one is present |
| `blocked` | No usable evidence — all core sections absent |

Rules:
- `package_status=complete` only when `export_status=complete` AND `evidence_source_type` is not `missing` or `unknown`.
- `package_status=partial` when `export_status=partial` (some sections present, some absent).
- `package_status=blocked` when `export_status=incomplete` (no core chain sections found).
- A `partial` or `blocked` proof bundle must never be presented as a complete verification artifact.

---

## Source Truthfulness Status

| Value | Meaning |
|-------|---------|
| `verified_live` | Evidence confirmed from live provider source |
| `verified_simulator` | Evidence confirmed from simulator/demo source |
| `fixture_only` | Evidence is from a static test fixture |
| `unavailable` | Provider was unreachable; truthfulness unverifiable |
| `unknown` | Source type is unrecognized |

---

## Required Package Metadata (Schema 1.1)

Every proof bundle `summary.json` must include:

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | string | Currently `"1.1"` |
| `export_id` | string | The export job ID |
| `generated_at` | ISO timestamp | UTC generation time |
| `generated_by` | string | `"Decoda RWA Guard"` |
| `workspace_id` | string | Workspace owning this export |
| `incident_id` | string | Incident this bundle covers |
| `asset_id` | string or null | Asset linked to incident (if available) |
| `target_id` | string or null | Target linked to first alert (if available) |
| `export_status` | string | `complete`, `partial`, `incomplete` |
| `package_status` | string | `complete`, `partial`, `blocked` |
| `export_format_version` | string | `"1.0"` |
| `evidence_source_type` | string | Evidence source classification |
| `source_truthfulness_status` | string | Verified truthfulness label |
| `source_truthfulness_reason` | string | Human-readable explanation |
| `available_sections` | string[] | Sections present in the package |
| `unavailable_sections` | string[] | Sections missing from the package |
| `section_statuses` | object[] | Per-section availability details |
| `missing_sections` | string[] | Legacy: sections absent from core chain |
| `warnings` | string[] | Human-readable warnings |
| `redactions_applied` | bool | True if sensitive fields were removed |
| `chain_complete` | bool | Whether the full chain is present |
| `customer_summary` | object | Customer-facing summary (see below) |

---

## Section Availability Rules

Tracked sections:

| Section | Description |
|---------|-------------|
| `telemetry` | Detection metrics / telemetry events |
| `detection` | Detection records linked to alerts |
| `alert` | Alerts linked via detection_metrics |
| `incident` | The incident record itself |
| `response_action` | Response actions for the incident |
| `asset_context` | Asset linked to the incident |
| `target_context` | Target linked to first alert |
| `provider_context` | Whether provider source is known |
| `export_metadata` | Export job metadata |
| `audit_log` | Audit log entries for the incident |

Each section in `section_statuses` has:
- `section_name`: string
- `status`: `"available"` | `"unavailable"` | `"redacted"`
- `reason`: string explaining why unavailable/redacted (empty if available)

Missing sections must appear in both `unavailable_sections` (in `summary.json`) and `section_statuses`.

---

## Redaction Policy

The export system applies `_redact_secret_fields()` to all bundle data before writing.

Redacted field name patterns (case-insensitive):
- `api_key`, `api-key`
- `secret_key`, `secret-key`
- `webhook_secret`
- `auth_token`
- `bearer`
- `private_key`, `private-key`
- `smtp_password`
- `database_url`
- `authorization`

Rules:
- Sensitive field values are replaced with `"[REDACTED]"`.
- `redactions_applied` is set to `true` in `summary.json` when any redaction occurs.
- Non-sensitive identifiers (`workspace_id`, `asset_id`, `alert_id`, `incident_id`) are **never** redacted.
- Tests must prove that known secret-pattern values do not appear in export output.

---

## Customer Summary Rules

`customer_summary` in `summary.json` must contain:

```json
{
  "headline": "...",
  "what_happened": "...",
  "why_it_matters": "...",
  "source_note": "...",
  "limitations": [...]
}
```

Rules:
- `source_note` must say "simulator" when evidence is from a simulator.
- `source_note` must say "live-provider" when evidence is from live data.
- `limitations` must mention missing live-provider evidence when evidence is unavailable or missing.
- `limitations` must mention each missing section.
- Do NOT claim regulatory compliance in `customer_summary`.
- Do NOT claim audit certification in `customer_summary`.
- Do NOT claim broad production readiness in `customer_summary`.

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
| `summary.json` | Bundle identity, status, evidence_source_type, section statuses, customer_summary |
| `incidents.json` | Linked incident record |
| `alerts.json` | Alerts linked via detection_metrics |
| `detections.json` | Detections linked via alert IDs |
| `response_actions.json` | Response actions for the incident |
| `audit_log.json` | Audit entries for the incident and export |
| `evidence.json` | Raw evidence fields from detection_metrics |
| `detection_metrics.json` | Full or filtered detection metric records |

---

## What Exports Can and Cannot Prove

**Can prove:**
- That a specific incident was recorded in the workspace
- Which alerts and detections were linked
- What response actions were taken
- The evidence chain integrity from telemetry to action
- Whether evidence came from a live provider or simulator

**Cannot prove:**
- Regulatory compliance (e.g., SEC, CFTC, MiCA)
- Audit certification
- Broad production readiness
- External system state (e.g., on-chain state at time of detection)
- Completeness of the underlying data source

---

## Simulator vs Live Evidence Wording

| Condition | Correct Label | Customer Note |
|-----------|--------------|---------------|
| `alerts.source = 'live_provider'` | `live` | "Live-provider evidence" |
| `alerts.source = 'simulator'` or `'guided_simulator'` | `simulator` | "Simulator evidence — not live-provider proof" |
| `alerts.source = 'fallback'` | `unavailable` | "Provider unavailable at collection time" |
| `detections.evidence_source = 'live'` | contributes to `live` | |
| `detections.evidence_source = 'simulator'` | contributes to `simulator` | |
| No alerts or detections | `missing` | "No evidence found" |

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

```bash
cd /home/user/decoda-rwa-guard

# Session 12 evidence export truthfulness tests
uv run pytest services/api/tests/test_evidence_export_truthfulness.py -v

# Existing proof bundle tests
uv run pytest services/api/tests/test_proof_bundle_export.py -v

# Full export suite
uv run pytest \
  services/api/tests/test_evidence_export_truthfulness.py \
  services/api/tests/test_proof_bundle_export.py \
  services/api/tests/test_assets_and_exports_foundations.py \
  -q
```

Key test scenarios in `test_evidence_export_truthfulness.py`:
- Simulator evidence labeled `simulator`, not `live_provider` (Test A)
- Unknown source not treated as `live_provider` (Test B)
- Missing telemetry in `unavailable_sections` (Test C)
- Missing response action in `unavailable_sections` (Test D)
- Partial package → `package_status=partial` (Test E)
- No evidence → `package_status=blocked` (Test F)
- Complete chain → `package_status=complete` (Test G)
- Simulator customer summary limitation (Test H)
- Missing live-provider customer summary limitation (Test I)
- No secrets in export JSON (Test J)
- `redactions_applied=true` when secrets removed (Test K)
- Cross-workspace rejected with 404 (Test L)
- All required section names present (Test M)
- All required metadata fields present (Test N)

---

## Canonical Evidence Source Field (Session 12 Follow-Up)

The proof bundle `summary.json` now includes **two evidence source fields** to support both new and legacy clients:

| Field | Purpose | Example value |
|-------|---------|---------------|
| `evidence_source` | **Canonical customer-facing field** (new) | `"live_provider"` |
| `evidence_source_type` | Legacy field retained for backward compatibility | `"live"` |

### Canonical enum

`evidence_source` is always one of:

| Value | Meaning |
|-------|---------|
| `live_provider` | Evidence confirmed from live provider API |
| `simulator` | Evidence from simulator/demo environment |
| `fixture` | Static test fixture evidence |
| `unavailable` | Provider was unreachable; fallback data may be present |
| `unknown` | Source type is unrecognized or absent — fail-closed |

### Normalization rules (via `normalize_evidence_source()`)

| Raw input | Canonical output |
|-----------|----------------|
| `live`, `live_provider` | `live_provider` |
| `simulator`, `simulation`, `guided_simulator` | `simulator` |
| `fixture`, `test_fixture` | `fixture` |
| `unavailable` | `unavailable` |
| `unknown` | `unknown` |
| `None`, `""`, unrecognized | `unknown` |

Rules:
- **Fail-closed**: Unknown or unrecognized values always become `unknown`, never `live_provider`.
- `simulator` always remains `simulator` — it is never promoted to `live_provider`.
- Only explicit `live` or `live_provider` inputs may produce `live_provider`.

### Backward compatibility

The legacy `evidence_source_type` field is preserved unchanged. It may contain values like `"live"` (not `"live_provider"`) which were used before the canonical enum was introduced.

- **New clients** should read `evidence_source` (canonical enum).
- **Legacy clients** that already parse `evidence_source_type` continue to work — the field is not removed.
- The two fields do not contradict each other: `evidence_source_type: "live"` always corresponds to `evidence_source: "live_provider"`.

### Consistency with source_truthfulness_status

`source_truthfulness_status` is always consistent with `evidence_source`:

| `evidence_source` | `source_truthfulness_status` |
|-------------------|------------------------------|
| `live_provider` | `verified_live` |
| `simulator` | `verified_simulator` |
| `fixture` | `fixture_only` |
| `unavailable` | `unavailable` |
| `unknown` | `unknown` |

---

## Remaining Gaps

- **Telemetry event IDs** are not yet linked from detection_metrics to telemetry_events directly in the proof bundle. The `evidence.json` file contains the JSONB evidence field from detection_metrics which may include telemetry context.
- **Full evidence lineage**: The chain from telemetry_event → detection → alert → incident → action → export is followed, but telemetry_events table is not directly queried.
- **Proof bundle for incidents without detection_metrics linkage**: If an incident was created without going through the detection_metrics path, some sections will appear as missing/partial.
- **Environment field**: Not yet included in summary.json. Could be added to distinguish staging vs production exports.
