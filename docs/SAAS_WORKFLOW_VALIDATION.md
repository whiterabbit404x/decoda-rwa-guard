# SaaS Workflow Validation

**Added:** Session 9 (2026-05-20)
**Applies to:** `services/api/tests/test_saas_workflow_validation.py`

---

## Purpose

This document explains what the final SaaS workflow validation proves, how to run it, what it does not prove, and how to interpret results.

The test file `test_saas_workflow_validation.py` is the authoritative single-file proof that Decoda RWA Guard has a real end-to-end SaaS workflow, not isolated screens. It is intentionally kept separate from the session-specific tests added in Sessions 2–8 so a reviewer can run exactly one file to check the full workflow chain.

---

## Canonical Workflow Under Validation

```
User / Workspace
  → Protected Asset
  → Monitoring Target / System
  → Enabled Monitoring Config
  → Telemetry
  → Detection
  → Alert
  → Incident
  → Response Action
  → Evidence / Audit
  → Export / Proof Bundle
```

This is the same workflow defined in `RUNTIME_SETUP_STEP_ORDER` inside `workspace_monitoring_summary.py` and surfaced in the product's runtime setup chain UI.

---

## What the Validation Proves

| Coverage Area | Assertions |
|---|---|
| **Workflow step order** | The canonical 12-step SaaS workflow is in correct order in `build_runtime_setup_chain` |
| **Workflow step completion** | All 12 steps reach `complete` when all required counters and timestamps are present |
| **Workflow step blocking** | Steps after telemetry/asset creation are `blocked` or `pending` when prerequisites are missing |
| **Evidence source canonicalization** | Simulator-family sources canonicalize to `simulator`; live-family to `live_provider`; null/empty never becomes `live` |
| **Evidence labels** | `detection_evidence_origin_label` returns SIMULATED for simulator, LIVE for live; simulator never produces LIVE EVIDENCE label |
| **Chain linkage** | `_response_action_payload` propagates all IDs when present; missing IDs are `null`, not invented |
| **Proof bundle completeness** | Complete chain → `export_status=complete`; empty chain → `export_status=incomplete`; simulator evidence → `evidence_source_type=simulator` with warning |
| **Proof bundle warnings** | Simulator evidence bundle always includes a warning that it is not live production proof |
| **Missing sections listed** | `missing_sections` is non-empty and explicit when chain sections are absent |
| **Export workspace isolation** | Export for incident from another workspace raises HTTP 404 |
| **Readiness gate: pilot** | `ready_for_pilot=False` when DB or auth is missing |
| **Readiness gate: paid launch** | `ready_for_paid_public_launch=False` when evidence is simulator or billing not configured |
| **Readiness fields** | All required top-level fields present; blocking reasons are explicit |
| **Alert workspace isolation** | Alert from wrong workspace returns HTTP 404/403 |
| **Enforcement query isolation** | `list_enforcement_actions` always includes `workspace_id = %s` filter with correct workspace |
| **Demo chain steps** | `build_realistic_demo_chain` produces all 11 required workflow stage steps |
| **Demo chain workspace** | All demo chain IDs reference the same bootstrap context (no cross-tenant mixing) |
| **Demo chain eligibility** | Simulator chain has `production_claim_eligible=False` |
| **Demo chain label** | Simulator chain label never claims "live" |
| **Demo chain summary** | Chain summary string covers the full path from `protected_asset` to `governance_action` |
| **Offline summary truth** | Fallback summary does not claim live/healthy status; reports zero systems |

---

## What the Validation Does NOT Prove

- **Live database integration.** Tests use fake in-memory connections; they do not connect to a real Postgres instance.
- **Live provider integration.** No real blockchain RPC, indexer, or oracle calls are made.
- **Auth token flow.** The auth stack is monkeypatched; full JWT issuance/verification is not tested here (covered in `test_auth_security_foundation.py`).
- **Frontend rendering.** The validation is backend-only; UI accuracy is not tested here.
- **Full HTTP route wiring.** The tests call Python functions directly, not the FastAPI HTTP layer (that is covered in session-specific tests using `TestClient`).
- **export_format_version in summary.json.** This field is not yet emitted by `_generate_export_artifact` (pre-existing gap; tracked in `test_proof_bundle_export.py::test_proof_bundle_summary_includes_all_required_fields` which currently fails).

---

## How to Run

### Quick (Windows PowerShell)

```powershell
cd F:\Blockchain_Security3\decoda-rwa-guard
python -m pytest services/api/tests/test_saas_workflow_validation.py -v
```

### Quick (Linux / macOS / WSL)

```bash
cd /path/to/decoda-rwa-guard
python3 -m pytest services/api/tests/test_saas_workflow_validation.py -v
```

### With coverage (optional)

```bash
python3 -m pytest services/api/tests/test_saas_workflow_validation.py -v --tb=short
```

### Run all related backend tests

```bash
python3 -m pytest services/api/tests/ -q --tb=no
```

---

## Expected Pass Output

```
======================== test session starts =========================
collected 33 items

test_saas_workflow_validation.py::test_saas_workflow_step_order_matches_canonical_path PASSED
test_saas_workflow_validation.py::test_saas_workflow_all_steps_complete_when_full_counters_and_timestamps PASSED
test_saas_workflow_validation.py::test_saas_workflow_steps_blocked_when_no_telemetry PASSED
test_saas_workflow_validation.py::test_saas_workflow_steps_blocked_when_no_assets PASSED
test_saas_workflow_validation.py::test_saas_workflow_current_step_is_first_incomplete PASSED
test_saas_workflow_validation.py::test_canonicalize_evidence_source_simulator_family PASSED
test_saas_workflow_validation.py::test_canonicalize_evidence_source_live_family PASSED
test_saas_workflow_validation.py::test_canonicalize_evidence_source_none_is_not_live PASSED
test_saas_workflow_validation.py::test_simulator_evidence_origin_label_is_simulated_not_live PASSED
test_saas_workflow_validation.py::test_simulator_evidence_never_becomes_live_label PASSED
test_saas_workflow_validation.py::test_response_action_full_chain_all_ids_present PASSED
test_saas_workflow_validation.py::test_response_action_chain_ids_null_not_invented_when_absent PASSED
test_saas_workflow_validation.py::test_chain_detection_id_null_not_invented_from_empty_metadata PASSED
test_saas_workflow_validation.py::test_proof_bundle_simulator_evidence_labeled_simulator_not_live PASSED
test_saas_workflow_validation.py::test_proof_bundle_simulator_evidence_generates_simulator_warning PASSED
test_saas_workflow_validation.py::test_proof_bundle_complete_chain_has_complete_status PASSED
test_saas_workflow_validation.py::test_proof_bundle_empty_chain_has_incomplete_status PASSED
test_saas_workflow_validation.py::test_proof_bundle_missing_sections_are_listed_not_hidden PASSED
test_saas_workflow_validation.py::test_proof_bundle_cross_workspace_incident_rejected PASSED
test_saas_workflow_validation.py::test_readiness_ready_for_pilot_requires_db_auth_telemetry_assets PASSED
test_saas_workflow_validation.py::test_readiness_paid_public_launch_false_when_simulator_evidence PASSED
test_saas_workflow_validation.py::test_readiness_paid_public_launch_false_without_billing PASSED
test_saas_workflow_validation.py::test_readiness_result_includes_required_top_level_fields PASSED
test_saas_workflow_validation.py::test_readiness_blocking_reasons_are_explicit_not_hidden PASSED
test_saas_workflow_validation.py::test_get_alert_from_wrong_workspace_returns_404 PASSED
test_saas_workflow_validation.py::test_list_enforcement_actions_query_scoped_to_requesting_workspace PASSED
test_saas_workflow_validation.py::test_realistic_demo_chain_all_workflow_steps_present PASSED
test_saas_workflow_validation.py::test_realistic_demo_chain_all_steps_reference_same_workspace PASSED
test_saas_workflow_validation.py::test_realistic_demo_chain_simulator_source_not_production_eligible PASSED
test_saas_workflow_validation.py::test_realistic_demo_chain_simulator_label_is_not_live PASSED
test_saas_workflow_validation.py::test_realistic_demo_chain_summary_string_covers_full_path PASSED
test_saas_workflow_validation.py::test_monitoring_summary_fallback_is_not_healthy PASSED
test_saas_workflow_validation.py::test_monitoring_summary_fallback_has_zero_reporting_systems PASSED
========================== 33 passed in 0.32s ==========================
```

---

## Expected Fail Examples

### If a workflow step is added without updating RUNTIME_SETUP_STEP_ORDER

```
FAILED test_saas_workflow_step_order_matches_canonical_path
AssertionError: Step order mismatch.
Expected: ['workspace_created', ..., 'evidence_export_ready']
Actual:   ['workspace_created', ..., 'evidence_export_ready', 'new_step']
```

Fix: Update `EXPECTED_STEP_ORDER` in the test to match, or remove the spurious step.

### If simulator evidence is accidentally labeled live in _generate_export_artifact

```
FAILED test_proof_bundle_simulator_evidence_labeled_simulator_not_live
AssertionError: assert 'live' == 'simulator'
```

Fix: Trace the `all_sources` accumulation in `pilot._generate_export_artifact` and ensure `source='simulator'` does not enter the `{'live', 'live_provider'}` branch.

### If workspace_id filter is removed from a query

```
FAILED test_list_enforcement_actions_query_scoped_to_requesting_workspace
AssertionError: Query missing workspace_id filter: SELECT id ... FROM response_actions ORDER BY created_at DESC
```

Fix: Restore `WHERE workspace_id = %s` to the affected query.

### If blockers are silently suppressed

```
FAILED test_readiness_blocking_reasons_are_explicit_not_hidden
AssertionError: assert 0 > 0
```

Fix: Ensure `build_production_readiness` appends the failing check's key to `blockers`.

---

## Live vs Simulator/Test Evidence Rules

| Source | Canonical value | Label | Production eligible |
|---|---|---|---|
| `live`, `live_provider`, `rpc`, `indexer`, `compliance_feed` | `live_provider` | LIVE EVIDENCE | Yes (with other requirements) |
| `simulator`, `guided_simulator` | `simulator` | SIMULATED EVIDENCE | No |
| `demo`, `synthetic`, `fallback`, `lab`, `replay` | Not live | Not live | No |
| `null` / empty | `simulator` (default) | No label | No |

**Simulator evidence rules:**
- Simulator evidence in a proof bundle triggers a warning that it is not live production proof.
- `ready_for_paid_public_launch` is `False` when evidence source is simulator.
- `production_claim_eligible` is `False` in demo chains.
- `ui_evidence_origin_label` must not claim "live" for simulator chains.

---

## What Blocks Pilot Launch

From `build_production_readiness`:
- Database unreachable
- Auth/session not configured
- Required env vars missing
- Contradiction flags present

`reporting_systems_count == 0` emits a setup-required warning but does not by itself block pilot if DB/auth/telemetry are present.

## What Blocks Paid Public Launch

Everything that blocks pilot, plus:
- `billing_required=True` but `billing_configured=False`
- `email_required=True` but `email_configured=False`
- `redis_required=True` but `redis_configured=False`
- Production app/API URLs not configured
- Evidence source is not live (simulator blocks paid launch)

---

## Pre-existing Known Failures (not introduced by this session)

These tests were failing before Session 9 and are unrelated to the workflow validation:

- `test_proof_bundle_export.py::test_proof_bundle_summary_includes_all_required_fields` — asserts `export_format_version` in `summary.json` but `_generate_export_artifact` does not emit this field yet
- `test_secret_crypto.py::test_secret_roundtrip`, `test_secret_decrypt_wrong_key_fails` — pyo3 dependency issue
- `test_web_detector_label_source.py::test_web_detector_label_map_includes_canonical_codes` — detector label map mismatch
- Various `test_monitoring_*` and `test_runtime_summary_*` tests — pre-existing schema/contract mismatches

Run `python3 -m pytest services/api/tests/ -q --tb=no` and compare the failure count against the 39 pre-existing failures to confirm no regressions.

---

## Troubleshooting Common Failures

| Symptom | Likely cause |
|---|---|
| `ModuleNotFoundError: No module named pytest` | Run `pip install pytest fastapi httpx` |
| `ImportError: No module named services.api.app` | Run from the repo root, not from `services/api/` |
| `AssertionError: Unexpected query in _FullChainConn` | `_generate_export_artifact` SQL changed; update the fake connection query patterns |
| `assert 39 != 39` (regression count check) | A pre-existing failing test was fixed — update the baseline count |
| Chain step count mismatch | A step was added to `RUNTIME_SETUP_STEP_ORDER`; update `EXPECTED_STEP_ORDER` in the test |
