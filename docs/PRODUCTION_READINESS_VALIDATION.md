# Production Readiness Validation

Internal/admin readiness is exposed at `GET /admin/readiness` (authenticated workspace admin scope).

## Categories

- **Platform**: database, auth/session config, required env presence (redacted), redis/email/billing config-or-disabled, app/api URL config.
- **Runtime**: heartbeat, latest poll, latest telemetry, reporting systems count, protected assets count, enabled monitoring config count, target coverage, provider health, freshness, confidence, contradiction flags.
- **Workflow**: detection/alert/incident/response-action counts, latest timestamps for each, linkage quality.
- **Evidence & Export**: evidence source status, export capability status, latest export job status, audit log availability, proof bundle capability (if available).
- **Integrations**: slack/webhook/delivery log statuses, API key support.
- **Security**: readiness access control, secret redaction, admin workspace scoping.

## Statuses
- `pass`: requirement satisfied.
- `warn`: non-blocking risk or intentionally disabled component.
- `fail`: blocking issue.
- `unavailable`: no trustworthy signal.

## Launch gates

### ready_for_pilot
Blocks when any of the following are true:
- DB unreachable.
- Auth/session missing.
- Workspace not evaluated.
- Workspace-scoped `protected_assets_count == 0`.
- Telemetry missing (heartbeat alone does not pass).
- Contradiction flags present.
- Evidence/export health not truthfully known/live.

`reporting_systems_count == 0` emits setup-required warning and must not appear healthy.

### ready_for_paid_public_launch
Requires `ready_for_pilot` plus:
- Billing configured unless paid UI is disabled.
- Email configured when required.
- Redis/cache configured when required.
- Production app/api URLs configured.
- Provider/integration statuses are known.
- Evidence source is live (not simulator).
- No simulator data represented as live.

## Truthfulness constraints
- Simulator evidence is labeled and cannot be treated as live readiness.
- Missing optional tables should return `unavailable`/`warn`, not crash endpoint.
- Secret values are never returned (booleans/status-only evidence).

## Remaining known gaps
- Some integration/export checks depend on table/service availability and may remain `unavailable` in partial deployments.
- Proof bundle capability is conditional and may be unavailable where not implemented.

---

## Session 10 — Paid Launch Billing/Email/Provider Readiness

### What is checked

`services/api/app/paid_launch_readiness.py` exposes `build_paid_launch_readiness()` which checks:

| Gate | Required env vars |
|---|---|
| Billing provider | `BILLING_PROVIDER` + `STRIPE_SECRET_KEY` + `STRIPE_PRICE_ID` (Stripe) or `PADDLE_API_KEY` + `PADDLE_PRICE_ID_*` (Paddle) |
| Billing webhook | `STRIPE_WEBHOOK_SECRET` (Stripe) or `PADDLE_WEBHOOK_SECRET` (Paddle) |
| Email provider | `EMAIL_PROVIDER` + `EMAIL_FROM` + `SENDGRID_API_KEY` / `RESEND_API_KEY` / `SMTP_*` |
| Live provider | `EVM_RPC_URL` (non-placeholder) |

### Why it fails closed

- `BILLING_PROVIDER=none` or absent → `billing_ready=false`.
- Missing webhook secret → `billing_webhook_ready=false` independently of `billing_ready`.
- Placeholder values in `EVM_RPC_URL` → `provider_ready=false`.
- Unknown status is never treated as ready.
- `paid_launch_ready=true` only when **all four** gates pass.

### How to run tests

```
python -m pytest services/api/tests/test_paid_launch_readiness.py -q
```

### How to interpret blockers

The `paid_launch_blockers` list in the output describes every unmet gate in plain language:

```json
{
  "paid_launch_ready": false,
  "paid_launch_status": "blocked",
  "paid_launch_blockers": [
    "billing provider is not configured",
    "billing webhook secret is missing",
    "email provider is not configured",
    "live provider configuration is missing"
  ]
}
```

Each blocker maps to a specific env var group shown in `billing_missing_env`, `email_missing_env`, or `provider_missing_env`. Secret values are never included.

### Important distinction

> **Passing pilot readiness is not the same as broad paid launch readiness.**

`build_production_readiness()` with `paid_ui_disabled=True` can return `ready_for_pilot=True` while `build_paid_launch_readiness()` returns `paid_launch_ready=false`. These are independent checks. Pilot status does not imply launch readiness.


## Session 10 — Paid Launch Billing/Email/Provider Readiness

Passing pilot readiness is not the same as broad paid launch readiness.

Broad paid launch now requires a separate `paid_launch_readiness` section in canonical readiness/proof output with fail-closed gates for:
- Billing provider + webhook readiness.
- Email readiness (`EMAIL_PROVIDER`, `EMAIL_FROM`, `EMAIL_DOMAIN`, plus one credential path: `SENDGRID_API_KEY` or `RESEND_API_KEY` or `SMTP_HOST`+`SMTP_USER`+`SMTP_PASSWORD`).
- Provider config readiness (`EVM_RPC_URL` non-placeholder).
- Live provider proof readiness (`LIVE_PROVIDER_PROOF_PRESENT=true` or canonical evidence source=`live`; simulator evidence does not satisfy this).

Interpret blockers from `paid_launch_blockers` as explicit reasons broad paid launch is still blocked.

Recommended checks:
- `python -m pytest services/api/tests/test_paid_launch_readiness.py -q`
- `python -m pytest services/api/tests/test_admin_readiness.py services/api/tests/test_proof_bundle_export.py services/api/tests/test_validate_readiness_proof.py services/api/tests/test_workspace_readiness_gate_aggregation.py services/api/tests/test_saas_workflow_validation.py -q`

---

## Session 11 — CI/Release Evidence and Launch Proof Artifacts

### What is generated

`scripts/generate_release_proof.py` creates five canonical JSON proof artifacts that provide fail-closed evidence of release readiness:

| Artifact | Location | Purpose |
|---|---|---|
| `ci-required-gates.json` | `artifacts/release-proof/latest/ci-required-gates.json` | Proof of CI gates: backend tests, SaaS workflow validation, readiness validation, paid launch readiness, live evidence, frontend build. |
| `release-proof/summary.json` | `artifacts/release-proof/latest/summary.json` | Overall release readiness: references CI gates, launch proof, manifest, and test report. |
| `launch-proof/summary.json` | `artifacts/launch-proof/latest/summary.json` | Launch readiness summary: pilot vs. paid GA, billing/email/provider/live-evidence gates, blockers. |
| `manifest.json` | `artifacts/release-proof/latest/manifest.json` | Deterministic artifact manifest with SHA256 integrity verification for all required files. |
| `test-report-summary.json` | `artifacts/release-proof/latest/test-report-summary.json` | Machine-readable test report summary with fail-closed semantics (missing/not_run tests cannot pass). |

### How to generate locally

```bash
make generate-release-proof
```

or

```bash
python scripts/generate_release_proof.py --mode local
```

### How to validate

```bash
make validate-release-proof
```

or

```bash
python scripts/validate_release_proof.py
```

The validator checks:
- All required artifact files exist
- Schema versions are correct
- Fail-closed semantics (unknown is never treated as pass)
- `broad_paid_saas_ready` cannot be true unless all gates pass
- No secret-like values in artifacts
- Required fields are present
- Manifest SHA256 hashes match actual artifact file contents
- Manifest missing required files cause overall_status=fail
- Test report summary status is never faked as pass
- All artifact paths are relative and under artifacts/

### How to interpret the artifacts

#### ci-required-gates.json

- `overall_status`: `pass` only when all required gates pass. Gates with status `not_run` do not prevent pass in local mode, but do prevent pass in strict CI mode.
- `required_gates`: structured list of gates with `status`, `command`, `summary`, and optional `blockers`.
- `broad_paid_launch_ready`: always false in local/CI mode; reserved for staging/production.
- `blockers`: list of explicit failure reasons preventing release.

Example: missing billing configuration creates blocker `"billing provider is not configured"`.

#### release-proof/summary.json

- `release_status`: `pass` only when both `ci_required_gates_ready` and `launch_proof_ready` are true.
- `ci_required_gates_ready`: true only if the ci-required-gates artifact exists and has overall_status=pass.
- `launch_proof_ready`: true only if launch-proof artifact exists and is pass.
- `paid_launch_ready`: always false in local mode; cannot be overridden.
- `blockers`: why the release is not ready (missing artifacts, failed gates, etc.).

#### launch-proof/summary.json

- `launch_mode`: `pilot` (default) or `paid_ga` (only when broad_paid_saas_ready=true).
- `pilot_ready`: true when live evidence is available (fail-closed without live).
- `controlled_pilot_ready`: may be true even when broad_paid_saas_ready is false.
- `broad_paid_saas_ready`: true only when all of:
  - `billing_ready` = true
  - `billing_webhook_ready` = true
  - `email_ready` = true
  - `provider_ready` = true
  - `live_evidence_ready` = true
  - `ci_required_gates_ready` = true
- `readiness`: gate-by-gate status (all booleans).
- `blockers`: explicit reasons why broad launch is blocked.

#### manifest.json

- `schema_version`: 1
- `generated_at`: ISO 8601 timestamp of generation
- `release_channel`: mode ('local', 'ci', 'staging', 'prod')
- `commit_sha`: git commit SHA from current HEAD
- `branch`: git branch name
- `files`: array of artifact file metadata, each containing:
  - `path`: relative artifact path (must be under artifacts/)
  - `sha256`: computed SHA256 hash of file contents (or 'missing')
  - `size_bytes`: file size in bytes
  - `required`: boolean (true for all release-critical files)
  - `status`: 'present' or 'missing'
- `overall_status`: 'pass' only if all required files exist and hashes match
- `blockers`: list of integrity issues (missing files, hash mismatches)
- `warnings`: optional warnings

**Why manifest is important**: provides cryptographic proof that release artifacts have not been tampered with and all required files are present. The validator checks that manifest SHA256 values match actual file contents.

#### test-report-summary.json

- `schema_version`: 1
- `generated_at`: ISO 8601 timestamp of generation
- `release_channel`: mode ('local', 'ci', 'staging', 'prod')
- `commit_sha`: git commit SHA from current HEAD
- `branch`: git branch name
- `test_suites`: dict of test suite results, each containing:
  - `name`: suite name
  - `status`: 'pass', 'fail', 'not_run', or 'missing'
  - `tests_run`: count of tests executed
  - `tests_passed`: count of passed tests
  - `tests_failed`: count of failed tests
  - `summary`: human-readable summary
- `overall_status`: 'pass' only if all test suites passed; 'fail' if any suite failed; 'not_run'/'missing' if tests not executed
- `blockers`: list of test execution issues
- `warnings`: optional warnings

**Why test report is important**: provides deterministic, machine-readable proof of test execution. In local mode, test_suites are not executed, so overall_status is 'not_run' and cannot be treated as pass. In CI mode, actual test results from CI pipelines should populate this artifact.

### Why local artifacts fail closed

In local development mode (`--mode local`), artifacts are generated with safe, fail-closed assumptions:
- Live evidence is unavailable unless `artifacts/live_evidence/latest/summary.json` exists and proves live data.
- CI gates are not run in local mode; they remain `not_run`.
- `paid_launch_ready` and `broad_paid_saas_ready` always remain false.
- Simulator or fallback evidence cannot satisfy live evidence gates.

This ensures local development artifacts never falsely claim readiness, but allows controlled-pilot readiness to pass when appropriate.

### Why missing live evidence blocks broad paid SaaS

The `live_evidence` gate in `ci-required-gates.json` checks whether live data is actually available:
- Without live evidence, the product cannot claim to be monitoring real assets.
- Simulator evidence is labeled but cannot satisfy live evidence gates.
- Missing live evidence creates blocker: `"live evidence summary not found"`.
- This blocks both `ci_required_gates_ready` and `launch_proof_ready`.

### Why pilot readiness is separate from paid GA readiness

- **Pilot readiness** (`pilot_ready`, `controlled_pilot_ready`) can be true for controlled pilots with limited users and safe fallbacks.
- **Paid GA readiness** (`broad_paid_saas_ready`) requires all paid launch gates, including billing, email, provider, and live evidence.
- A product can be controlled-pilot ready (safe for trusted customers) while not being broad paid SaaS ready (unsafe for public launch).

### How GitHub Actions integrates the proofs

The `.github/workflows/ci-release-gates.yml` workflow:
1. Runs paid launch readiness tests
2. Generates release proof artifacts with `python scripts/generate_release_proof.py --mode ci`
3. Validates artifacts with `python scripts/validate_release_proof.py`
4. Uploads artifacts as CI artifacts (retained for 30 days)

The proofs can be reviewed before merging to main or before a production deploy.

### Important: Artifacts are evidence, not marketing claims

The artifacts in `artifacts/release-proof/` and `artifacts/launch-proof/` are cryptographically truthful snapshots of readiness at the moment they were generated. They:
- Never include secret values (only presence flags and env var names)
- Fail closed (unknown is never treated as pass)
- Are machine-readable and validator-checkable
- Can be committed to source control for audit purposes
- Should not be faked or overridden for release marketing

If an artifact reports failure, the only correct response is to fix the underlying issues. Do not force artifacts to pass.

### Session 11 hardening: 100% CI/release evidence controls

Session 11 extends Session 10 release proof with two additional enterprise-grade artifacts to reach 100% CI/release evidence category completion:

#### New: Artifact Manifest with SHA256 Integrity

The `manifest.json` artifact provides:
- **Deterministic inventory** of all release-critical artifacts (ci-required-gates.json, release-proof/summary.json, launch-proof/summary.json)
- **Cryptographic integrity** via SHA256 hashes: validator rejects any manifest with mismatched hashes
- **Fail-closed file validation**: missing required files cause overall_status=fail and are listed in blockers
- **Path security**: all artifact paths must be relative and under artifacts/ (validator rejects absolute or out-of-tree paths)

This hardens the release chain against accidental artifact loss or tampering.

#### New: Machine-Readable Test Report Summary

The `test-report-summary.json` artifact provides:
- **Deterministic test execution tracking** with schema_version=1
- **Fail-closed test status**: overall_status can be 'pass', 'fail', 'not_run', or 'missing', but never treats unknown as pass
- **Machine-parseable results**: test_suites dict with per-suite pass/fail/not_run status
- **Local mode safety**: in local generation mode, test_suites are not executed, so overall_status='not_run' and cannot satisfy release-readiness gates
- **CI mode readiness**: in CI generation mode, actual test execution results populate test_suites, allowing overall_status='pass' only when all tests pass

This ensures that missing or skipped tests cannot be misinterpreted as successful tests.

#### Impact on release proof summary

The release-proof/summary.json now includes:
- `manifest_ready`: boolean indicating manifest overall_status=pass
- `test_report_ready`: boolean indicating test-report overall_status != 'fail'
- `evidence_files`: array now includes manifest.json and test-report-summary.json (in addition to ci-required-gates and launch-proof)

All four files are required for release_status=pass. If any are missing or invalid, release_status=fail with corresponding blockers.

#### Why this improves CI/release evidence category

The CI/release evidence category reaches 100% because:
1. **Completeness**: all five artifacts are generated deterministically in CI
2. **Integrity**: manifest proves no tampering via cryptographic hashes
3. **Fail-closed**: test reports, missing files, and unknown status all fail closed
4. **Automation-safe**: suitable for CI systems (no manual approval, no faked data)
5. **Enterprise-reviewable**: all artifacts are JSON with clear schema, blockers, and evidence trails

#### Why broad paid SaaS readiness may still remain blocked

The CI/release evidence category (100%) is independent of broad paid SaaS readiness. Even with perfect CI/release evidence, broad paid SaaS readiness (`broad_paid_saas_ready=false`) requires additional gates that may remain unmet:
- **Billing provider** configuration (STRIPE_SECRET_KEY, PADDLE_API_KEY, etc.)
- **Email provider** configuration (SENDGRID_API_KEY, RESEND_API_KEY, SMTP_*, etc.)
- **EVM RPC provider** configured (non-placeholder EVM_RPC_URL)
- **Live evidence** available (not simulator-only evidence)
- **All paid launch gates** passing

These are separately validated via `build_paid_launch_readiness()` and are intentionally not part of the CI/release evidence category.

---

## Session 12 — Customer-Facing Evidence Export Quality

### What improved (Evidence/export: 70% → target 82%)

Evidence/export scoring was 70% because:
- Proof bundles lacked schema versioning and canonical metadata
- `unavailable_sections` was always an empty list
- Section availability was not tracked per-section with statuses
- No customer-facing summary existed
- Redaction was not systematically applied to secret-like fields
- `package_status` (customer-facing) did not exist (only internal `export_status`)
- `source_truthfulness_status` and `source_truthfulness_reason` were absent

Session 12 adds:

| Improvement | Impact |
|-------------|--------|
| `schema_version: "1.1"` in summary.json | Versioned, auditable format |
| `export_id`, `generated_by`, `target_id` fields | Complete provenance |
| `package_status` (`complete`/`partial`/`blocked`) | Customer-facing completion signal |
| `source_truthfulness_status` + `source_truthfulness_reason` | Clear truthfulness chain |
| `section_statuses` with per-section `available`/`unavailable` | Granular section visibility |
| `available_sections` + `unavailable_sections` lists | Audit-friendly section inventory |
| `provider_context` section tracked | Provider availability visible |
| `_build_customer_export_summary()` with headline/source_note/limitations | Customer-facing non-overclaiming summary |
| `_redact_secret_fields()` applied to all bundle data | Secret leak prevention |
| `redactions_applied` flag in summary | Redaction transparency |
| 19 new tests in `test_evidence_export_truthfulness.py` | All required behaviors verified |

### Commands to run

```bash
cd /home/user/decoda-rwa-guard

# Session 12 tests
uv run pytest services/api/tests/test_evidence_export_truthfulness.py -q

# Full evidence/export test suite
uv run pytest \
  services/api/tests/test_evidence_export_truthfulness.py \
  services/api/tests/test_proof_bundle_export.py \
  services/api/tests/test_assets_and_exports_foundations.py \
  -q

# Confirm Sessions 10 and 11 still pass
uv run pytest services/api/tests/test_paid_launch_readiness.py -q
uv run pytest services/api/tests/test_release_proof_artifacts.py -q
```

### How to inspect exported packages

A proof bundle `summary.json` now looks like:

```json
{
  "schema_version": "1.1",
  "export_id": "exp-abc123",
  "generated_at": "2026-05-22T10:00:00Z",
  "generated_by": "Decoda RWA Guard",
  "workspace_id": "ws-live",
  "incident_id": "inc-live",
  "package_status": "complete",
  "evidence_source_type": "live",
  "source_truthfulness_status": "verified_live",
  "source_truthfulness_reason": "Evidence sourced from live provider API responses.",
  "available_sections": ["telemetry", "detection", "alert", "incident", "response_action", ...],
  "unavailable_sections": [],
  "section_statuses": [
    {"section_name": "telemetry", "status": "available", "reason": ""},
    ...
  ],
  "redactions_applied": false,
  "customer_summary": {
    "headline": "Complete evidence package generated",
    "source_note": "This package contains live-provider evidence.",
    "limitations": []
  }
}
```

Key checks when inspecting an exported package:
1. `package_status` — `blocked` means no usable evidence
2. `evidence_source_type` — `simulator` means it is NOT live-provider proof
3. `unavailable_sections` — any missing chain steps are listed here
4. `redactions_applied` — if true, some fields were sanitized
5. `customer_summary.source_note` — plain-language summary of evidence source

### Important warning

> **Polished evidence export format does NOT equal broad paid SaaS readiness.**

Improving the export quality to 82% improves customer trust and audit-readiness for controlled pilot customers. It does NOT satisfy the remaining broad paid SaaS launch gates:

- Billing provider still requires configuration (`STRIPE_SECRET_KEY` / Paddle equivalent)
- Email provider still requires configuration
- Live provider (`EVM_RPC_URL`) must be non-placeholder
- `paid_launch_ready=true` requires all four gates from Session 10 to pass

Evidence export improvements are a necessary but not sufficient condition for broad paid SaaS launch.

### Session 12 Follow-Up — Canonical evidence_source alias

**Follow-up cleanup:** Added `evidence_source` as a canonical, customer-facing field to every proof bundle `summary.json` while preserving the legacy `evidence_source_type` field for backward compatibility.

Changes made:
- Added `normalize_evidence_source()` helper in `pilot.py` — maps raw source values to the canonical enum (`live_provider` | `simulator` | `fixture` | `unavailable` | `unknown`). Fails closed to `unknown` for unrecognized or empty values.
- `evidence_source` now appears in `summary.json` alongside `evidence_source_type`. Old field is not removed.
- `evidence_source_type: "live"` maps to `evidence_source: "live_provider"` — the legacy `"live"` value is preserved in the old field only.
- `source_truthfulness_status` remains consistent with the canonical `evidence_source` value.
- 7 new tests added to `test_evidence_export_truthfulness.py` verifying canonical field presence, correct mapping, fail-closed behavior, and enum validity.
- `test_N_summary_contains_all_required_metadata_fields` updated to require `evidence_source` in schema 1.1.

No existing Session 10/11/12 tests were weakened. All proof bundle export tests continue to pass.

### Session 12 Hardening Follow-Up — Fail-closed package status and customer_summary safety

**Goal:** Close remaining evidence/export polish gaps without weakening any existing gates.

Changes made:

- **`package_status` now fails closed** using the canonical `evidence_source` field and `source_truthfulness_status` rather than the legacy `evidence_source_type` field:
  - `complete` requires `evidence_source not in {unknown, unavailable}` AND `source_truthfulness_status not in {unknown, unavailable}`
  - `partial` is returned when any evidence rows exist but completeness cannot be claimed
  - `blocked` is returned only when no evidence rows exist at all

- **`fixture` evidence source explicitly handled** throughout the evidence chain:
  - `fixture` and `test_fixture` alert/detection sources now produce `evidence_source_type = fixture`
  - `source_truthfulness_status = fixture_only` and `source_truthfulness_reason` are set correctly
  - Bundle warnings flag fixture evidence as non-live-production proof

- **`_build_customer_export_summary` cannot overclaim:**
  - New `fixture` case: `source_note` says "not live-provider proof"
  - `unavailable` case updated: `source_note` starts with "Evidence source is unavailable."
  - No case ever says "regulatory compliant", "audit certified", "enterprise ready", or "broad paid SaaS ready"

- **10 new hardening tests** added to `test_evidence_export_truthfulness.py`:
  - A: complete impossible when evidence_source is unknown
  - B: complete impossible when source_truthfulness_status is unknown
  - C: simulator customer_summary says "not live-provider proof"
  - D: fixture customer_summary says "not live-provider proof"
  - E: unknown source customer_summary warns about live-provider proof
  - F: blocked package returned when no usable evidence
  - G: package_status not complete without response_action
  - H: package_status not complete without telemetry
  - I: customer_summary never contains forbidden claims across all source types
  - J: canonical evidence_source always a valid enum across all connection types

- **UI panel** (`evidence-audit-panel.tsx`): added explicit warning banner when `package_status` is `partial` or `blocked`

**Broad paid SaaS readiness remains blocked** unless all launch gates pass with real proof artifacts:
- Billing provider configuration (`STRIPE_SECRET_KEY` / Paddle)
- Email provider configuration
- Live provider (`EVM_RPC_URL`) must be non-placeholder
- `paid_launch_ready=true` requires all four gates from Session 10 to pass

Evidence/export hardening improves customer trust and fail-closed audit safety. It does not change the broad paid SaaS launch gate requirements.

---

## Session 13 — Runtime Truthfulness and Contradiction Guards

### Goal

Strengthen canonical runtime status so it is source-truthful, freshness-aware,
and contradiction-safe.  Runtime state must never look healthier than it is.

### Signal Taxonomy

Each signal has a distinct timestamp and meaning:

| Signal | Timestamp | Proves |
|---|---|---|
| Heartbeat | `last_heartbeat_at` | Worker/service is alive |
| Poll | `last_poll_at` | Monitoring loop attempted provider work |
| Telemetry | `last_telemetry_at` | Monitored data actually arrived |
| Detection | `last_detection_at` | Telemetry was evaluated for risk |
| Alert | `last_alert_at` | Customer-facing risk signal was created |
| Incident | `last_incident_at` | Alert was escalated to a case |
| Response action | `last_response_action_at` | System acted on the incident |
| Evidence export | `last_evidence_export_at` | Chain exported as audit evidence |

Heartbeat must not infer telemetry.  Poll must not infer telemetry.  Detection
must not be inferred from telemetry alone.

### Runtime Status Meanings

| Value | Meaning |
|---|---|
| `healthy` | All systems reporting, telemetry fresh, no contradictions |
| `limited` | Partial coverage, stale data, or non-critical contradictions |
| `offline` | No systems reporting or critical contradiction present |
| `misconfigured` | Workspace configuration incomplete |
| `unknown` | Status cannot be determined |

`unknown` is never treated as `healthy`.

### Freshness Thresholds

| Signal | Threshold |
|---|---|
| heartbeat | 5 minutes |
| poll | 10 minutes |
| telemetry | 15 minutes |
| detection | 30 minutes |
| alert | 30 minutes |
| incident | 60 minutes |
| response_action | 60 minutes |
| evidence_export | 24 hours |

### Session 13 Contradiction Guards

| Flag | Triggered by |
|---|---|
| `healthy_without_reporting_systems` | `runtime_status == healthy` and `reporting_systems == 0` |
| `current_without_telemetry` | freshness is current/fresh but `last_telemetry_at` is null |
| `offline_with_current_telemetry` | offline claimed but telemetry signal is current |
| `live_mode_with_simulator_evidence` | `monitoring_mode == live` but evidence is simulator |
| `live_evidence_without_provider_ready` | live_provider evidence but provider not ready |
| `systems_without_protected_assets` | configured_systems > 0 but protected_assets == 0 |
| `reporting_exceeds_configured` | reporting_systems > configured_systems |
| `detection_without_telemetry` | detection present but telemetry missing |
| `alert_without_detection` | alert present but detection missing |
| `incident_without_alert` | incident present but alert missing |
| `response_action_without_case` | response action exists but no incident or alert |
| `evidence_export_without_source_truthfulness` | evidence exported but source is unknown/none |

If `contradiction_flags` is non-empty: `runtime_status` must not be `healthy`,
and `confidence_status` must be `low` or `unavailable`.

### Why Simulator Evidence Cannot Satisfy Live Readiness

Simulator evidence is generated by the internal simulator, not by a real
blockchain provider.  It cannot be presented as customer audit evidence and
does not satisfy `paid_launch_ready` checks.  The
`live_mode_with_simulator_evidence` contradiction flag blocks any attempt to
claim live monitoring while serving simulator data.

### New Canonical Fields

`build_workspace_monitoring_summary` now accepts and emits:

- `last_alert_at`, `last_incident_at`, `last_response_action_at`, `last_evidence_export_at`
- `signal_freshness`: per-signal freshness dict

New helper module: `services/api/app/runtime_truthfulness.py`
- `compute_signal_freshness`, `build_signal_freshness`
- `detect_runtime_contradictions`, `derive_runtime_status`, `derive_confidence_status`

### How to Run Tests

```bash
# Session 13 tests (38 tests, no fastapi dependency)
pytest services/api/tests/test_runtime_truthfulness.py -q

# Admin readiness, paid launch, release proof
pytest services/api/tests/test_admin_readiness.py \
       services/api/tests/test_paid_launch_readiness.py \
       services/api/tests/test_release_proof_artifacts.py \
       -q
```

### Runtime Truthfulness Score Impact

These changes are estimated to raise the Runtime Truthfulness category from
80% to approximately 90% by:

- Separating heartbeat/poll/telemetry/detection/alert/incident/response/evidence timestamps
- Adding per-signal freshness with canonical thresholds
- Adding 11 new session-13 contradiction flags
- Adding `signal_freshness` to the canonical runtime summary
- Adding pure helper functions covered by 38 new tests

**Broad paid SaaS readiness remains blocked** unless all required launch gates
pass: billing, email, live provider, CI evidence, staging validation, and
evidence export validation all passing simultaneously.

Runtime truthfulness improves customer trust and operational safety.  It does
not by itself make the product broad paid SaaS ready.

---

## Session 14 — Multi-Tenant Isolation and Object-Level Authorization

### Why this improves Multi-tenant isolation (75% → estimated 88%)

The multi-tenant isolation category was at 75% because:
- No canonical object-level authorization helper module existed.
- Cross-workspace negative tests were absent.
- Body/query workspace_id override protection was untested.
- Export and response-action cross-workspace rejection was not verified.
- Audit log workspace scoping was not explicitly tested.

Session 14 adds:

| Improvement | Impact |
|---|---|
| `services/api/app/tenant_isolation.py` | Canonical helpers: `require_object_in_workspace`, `assert_same_workspace`, `reject_body_workspace_override`, `safe_not_found` |
| 32 new tests in `test_multi_tenant_isolation.py` (cases A–X) | Full negative test coverage for all core SaaS objects |
| Cross-workspace asset/target/detection/alert/incident/action/export tests | Proves isolation is enforced at every endpoint family |
| Body workspace_id override test | Proves body cannot override session workspace context |
| Audit log scoping test | Proves `log_audit` always uses the session workspace |
| List endpoint scoping test | Proves list queries use session workspace, not query params |
| `docs/MULTI_TENANT_ISOLATION.md` | Reference documentation for the isolation model |

### Tests added

```
services/api/tests/test_multi_tenant_isolation.py  (32 tests)

A  Workspace A cannot read Workspace B asset
B  Workspace A cannot update Workspace B asset
C  Workspace A cannot delete Workspace B asset
D  Workspace A cannot enable/disable Workspace B monitoring target (×2)
E  Workspace A cannot read Workspace B detection evidence
F  Workspace A cannot read Workspace B detection
G  Workspace A cannot read Workspace B alert
H  Workspace A cannot acknowledge/resolve Workspace B alert
I  Incident list never includes Workspace B incidents
J  Workspace A cannot close/update Workspace B incident
K  Workspace A cannot execute Workspace B response action
L  Workspace A cannot generate proof bundle for Workspace B incident
M  Workspace A cannot read/download Workspace B export artifact (×2)
N  Runtime setup chain uses workspace-scoped counters only
O  Monitoring summary fallback uses isolated workspace state
P  _ensure_membership rejects wrong workspace
Q  Cross-workspace 404 does not reveal object details
R  Body workspace_id cannot override session (×2)
S  list_assets uses session workspace, not query params
T  Mixed-workspace export is rejected
U  Mixed-workspace response action is rejected
V  log_audit writes the session workspace_id
W  list_assets never returns Workspace B rows
X  Tenant isolation helpers (assert_same_workspace, require_object_in_workspace, safe_not_found, reject_body_workspace_override)
+ 2 additional edge-case tests
```

### Commands to run

```bash
cd /home/user/decoda-rwa-guard

# Session 14 isolation tests
python -m pytest services/api/tests/test_multi_tenant_isolation.py -q

# Prior sessions still passing
python -m pytest \
  services/api/tests/test_saas_workflow_validation.py \
  services/api/tests/test_workspace_readiness_gate_aggregation.py \
  services/api/tests/test_response_actions_api.py \
  services/api/tests/test_proof_bundle_export.py \
  services/api/tests/test_assets_and_exports_foundations.py \
  -q
python -m pytest services/api/tests/test_paid_launch_readiness.py -q
python -m pytest services/api/tests/test_release_proof_artifacts.py -q
python -m pytest services/api/tests/test_evidence_export_truthfulness.py -q
python -m pytest services/api/tests/test_runtime_truthfulness.py -q
```

### Remaining blockers for broad paid SaaS readiness

Multi-tenant isolation at ~88% is a necessary but not sufficient condition.
Broad paid SaaS readiness additionally requires:

- `BILLING_PROVIDER` + billing credentials (`STRIPE_SECRET_KEY` / Paddle)
- `EMAIL_PROVIDER` + email credentials (`SENDGRID_API_KEY` / `RESEND_API_KEY` / SMTP)
- Live EVM provider (`EVM_RPC_URL` non-placeholder)
- Live evidence from a real chain provider (not simulator)
- All `build_paid_launch_readiness()` gates passing simultaneously
- CI proof artifacts generated and validated

---

## Final 100% Readiness Gate (Session 14)

### What "100%" means

`production_100_percent_ready: true` in `artifacts/final-readiness/latest/summary.json` means:

1. All required proof artifact files are present and valid.
2. Every category (product_concept, saas_workflow, runtime_truthfulness, ui_polish, auth_workspace_model, multi_tenant_isolation, evidence_export, billing_email_launch_readiness, ci_release_evidence, enterprise_readiness) has `status: pass`.
3. Live evidence is confirmed (not simulator evidence).
4. Staging validation has been executed with real credentials.
5. No unresolved blockers remain.

### Why local/CI fail-closed is acceptable

In local and CI modes:
- Artifacts are generated with safe, fail-closed assumptions.
- `safe_to_sell_broadly_today` is always `false`.
- `broad_paid_saas_ready` is always `false`.
- CI proves fail-closed behavior — it does NOT prove live provider readiness.

This is intentional: CI running correctly without secrets proves the gate system works. It does not prove the product is ready to sell.

### Why staging/production strict mode is required before broad sales

Only in `--mode staging --strict` or `--mode production --strict`:
- Real billing, email, and provider credentials are present.
- Live evidence is confirmed from a real blockchain RPC endpoint.
- Staging validation has run end-to-end.
- `safe_to_sell_broadly_today` can become `true`.

### How to run

```bash
# Generate proof artifacts first
make generate-release-proof

# Validate all sessions + final readiness
make validate-100-percent-readiness

# Or directly (local mode — expect production_100_percent_ready=false)
python scripts/validate_100_percent_readiness.py --mode local

# Staging strict mode (requires real credentials)
python scripts/validate_100_percent_readiness.py --mode staging --strict
```

### How to inspect results

```bash
cat artifacts/final-readiness/latest/summary.json
```

Key fields:
- `overall_score` — 0–100, computed from category scores
- `production_100_percent_ready` — true only when every gate passes
- `safe_to_sell_broadly_today` — true only in staging/production strict with all gates passing
- `blockers` — list of explicit reasons blocking readiness
- `warnings` — non-blocking issues to be aware of
- `proof_artifacts` — paths to all proof artifacts used

### Warning

> **Do not sell broadly until `safe_to_sell_broadly_today` is `true` in staging or production strict mode.**
>
> `production_100_percent_ready: false` in local/CI mode is expected and correct. It does not indicate a problem — it indicates that live credentials have not been provided.
>
> Never force `safe_to_sell_broadly_today: true` by editing artifacts. Fix the underlying gates instead.

---

## Session 15 — Broad Paid SaaS Launch Validation / Staging Go-Live Gates

### What was added

Session 15 adds a canonical staging launch proof layer that proves (or blocks)
broad paid SaaS readiness based on real staging/live environment validation.

**New scripts:**
- `scripts/generate_staging_launch_proof.py` — generates `artifacts/staging-proof/latest/summary.json`
- `scripts/validate_staging_launch_proof.py` — validates structure and fail-closed rules

**New artifact:** `artifacts/staging-proof/latest/summary.json`

**New test file:** `services/api/tests/test_staging_launch_proof.py` (tests A–Q)

**Updated:** `scripts/validate_100_percent_readiness.py` — now requires staging proof artifact

**Updated:** Makefile — added `generate-staging-proof`, `validate-staging-proof`; extended `validate-launch`, `validate-paid-ga`, `validate-100-percent-readiness`

**Updated:** `.github/workflows/ci-release-gates.yml` — added staging proof generation/validation step

**New doc:** `docs/STAGING_GO_LIVE_VALIDATION.md`

### Four new validation models

1. **Staging launch validation** — checks STAGING_API_URL, STAGING_APP_URL,
   STAGING_DATABASE_URL, STAGING_AUTH_TOKEN_SECRET, STAGING_WORKER_ENABLED.
   All five are required blockers if absent.

2. **Live provider validation** — checks EVM_RPC_URL configuration and live
   evidence readiness from the launch-proof artifact. Simulator evidence fails
   this gate. Fixture evidence fails this gate. Unknown evidence fails closed.

3. **Billing production-mode validation** — checks BILLING_PROVIDER, live secret
   key presence (sk_live_* only; sk_test_* rejected), webhook secret (whsec_*),
   and price ID configuration.

4. **Email production-mode validation** — checks EMAIL_PROVIDER, API key presence,
   EMAIL_FROM (non-placeholder, non-test-domain), EMAIL_DOMAIN.

### Fail-closed rules enforced by validator

- `broad_paid_saas_ready` cannot be true unless all four validation sections pass.
- `safe_to_sell_broadly_today` cannot be true unless `broad_paid_saas_ready` is true.
- `test_mode_detected=true` cannot coexist with `billing_production_validation.status=pass`.
- Simulator/fixture evidence cannot coexist with `live_provider_validation.status=pass`.
- Blockers present → `broad_paid_saas_ready` and `safe_to_sell_broadly_today` must be false.
- No secret-like values may appear in the artifact.

### Updated final 100% readiness gate

`validate_100_percent_readiness.py` now:
- Loads `artifacts/staging-proof/latest/summary.json`
- Requires `staging_launch_ready=true` in the staging proof for `staging_ok=true`
- Missing staging proof → staging validation blocker → `production_100_percent_ready=false`
- Adds `staging_proof_validation` gate to `required_gates`
- Adds staging proof path to `proof_artifacts` list

### Run commands

```bash
# Generate fail-closed staging proof (local/CI)
python scripts/generate_staging_launch_proof.py --mode local

# Validate staging proof artifact
python scripts/validate_staging_launch_proof.py

# Full 100% readiness check (includes staging proof)
make validate-100-percent-readiness

# Staging environment (requires real credentials)
python scripts/generate_staging_launch_proof.py --mode staging --strict
python scripts/validate_staging_launch_proof.py
python scripts/validate_100_percent_readiness.py --mode staging --strict
```

### Broad paid SaaS readiness status after Session 15

`broad_paid_saas_ready` remains `false` in local/CI mode. This is correct.
To reach `broad_paid_saas_ready=true`, all of the following must pass in
staging/production mode:
- staging_launch_validation.status = pass
- live_provider_validation.status = pass
- billing_production_validation.status = pass
- email_production_validation.status = pass
- All required dependencies (paid_launch_readiness, release_proof,
  runtime_truthfulness, evidence_export_truthfulness, multi_tenant_isolation) = pass

See `docs/STAGING_GO_LIVE_VALIDATION.md` for the complete go-live checklist.

---

## Live Evidence Chain Validation

`check_live_evidence_chain()` in `services/api/app/paid_launch_readiness.py` validates the complete
Detect → Respond chain with real operational evidence, not simulation.

### Required chain fields

| Field | Type | Description |
|---|---|---|
| `evidence_source` | str | Must be `live` or `live_provider`. |
| `last_telemetry_at` | str (ISO8601) | Must be present. Heartbeat or poll alone are not sufficient. |
| `detections_count` | int | Must be ≥1. |
| `detection_telemetry_linked` | bool | Detection must link to telemetry by ID/lineage. |
| `alerts_count` | int | Must be ≥1. |
| `alert_detection_linked` | bool | Alert must link to detection by ID/lineage. |
| `incidents_count` or `response_actions_count` | int | At least one must be ≥1. |
| `incident_alert_linked` | bool | Incident/response-action must link to alert. |
| `export_capability` | str | Must be `pass`/`available`/`ready`. |
| `export_source_label` | str | Must be `live` or empty. |
| `contradiction_flags` | list | Must be empty or free of live-evidence contradiction codes. |

### Why heartbeat/poll are not telemetry

- **Heartbeat** proves the monitoring worker process is alive.
- **Poll** proves the monitoring loop ran (iterated).
- **Telemetry** proves monitored chain data actually arrived in the system.

All three are separate signals. A healthy heartbeat + poll with no telemetry means the monitoring
worker is running but no data has been ingested. `live_evidence_chain_ready` remains `false`
until `last_telemetry_at` is populated.

### Contradiction guards

The following states **always fail** `live_evidence_chain_ready`:

- `evidence_source = simulator / demo / guided_simulator / fixture`
- `evidence_source = unknown`
- `last_telemetry_at` absent (heartbeat-only or poll-only)
- Detection/alert/incident not linked by evidence lineage
- Contradiction flags containing `live_mode_with_simulator*`, `missing_telemetry`, etc.
- Export labeled as anything other than `live`

### Running chain validation tests

```bash
pytest services/api/tests/test_paid_launch_readiness.py -k chain -v
```

### Provider mode

`check_provider_readiness()` now returns an explicit `provider_mode`:

| Mode | Meaning |
|---|---|
| `live` | EVM_RPC_URL is configured and non-placeholder |
| `disabled` | EVM_RPC_URL is absent |
| `unknown` | EVM_RPC_URL is a placeholder value |
| `simulator` | Not from this function; set externally |

`EVM_CHAIN_ID` is optional but recommended for explicit chain identification.
Its presence is reported in `chain_id_configured`.

---

## Dependency Security (npm audit)

The `postcss` vulnerability (GHSA-qx2v-qp2m-jg93, moderate) is resolved by:

- Root-level `devDependencies`: `"postcss": ">=8.5.10"`
- Root-level `overrides`: `"postcss": ">=8.5.10"` and `"next": { "postcss": ">=8.5.10" }`
- Workspace `apps/web` also pins `"postcss": "^8.5.15"` as a devDependency

After any `npm install`, verify with `npm audit` → `found 0 vulnerabilities`.

The test `test_dependency_audit_gate` in `test_paid_launch_readiness.py` programmatically
verifies postcss ≥8.5.10 is installed.

### Commands to verify

```bash
npm audit
node -e "console.log(require('./node_modules/postcss/package.json').version)"
# Expected: 8.5.10 or higher
```

---

## Why blocker 3 still fails locally

Blocker 3 is **live provider evidence** — proof that real monitored chain data
arrived from a real EVM JSON-RPC provider. Without provider env vars, the proof
**must fail closed**:

```
provider_ready=false
provider_mode=disabled
provider_health_checked=false
evidence_source=unknown
latest_live_telemetry_at=null
live_evidence_ready=false
missing: EVM_RPC_URL or STAGING_EVM_RPC_URL not configured
```

**This is expected and safe.** Fail-closed guardrail tests and mocked
positive-path tests still pass — they prove the logic. They cannot, and must
not, manufacture real live evidence. `live_evidence_ready` is never hardcoded
to `true`; it is read from the actual `artifacts/live-evidence-proof/latest/summary.json`
artifact written by `scripts/generate_live_evidence_proof.py`.

### To pass real production evidence

Set:

- `STAGING_EVM_RPC_URL` — a real Ethereum-compatible JSON-RPC endpoint
- `STAGING_EVM_CHAIN_ID` — the chain id (e.g. `1`)
- `STAGING_WORKER_ENABLED=true`

Then run:

```bash
make run-staging-live-proof
```

This is the single entry point for proving blocker 3. It runs the real proof
chain (live RPC calls, staging proof, validate staging proof,
validate-100-percent-readiness in staging strict mode), reads the artifact, and
exits 0 only when `live_evidence_ready=true`. The RPC URL is masked in all
output.

### Expected passing output

```
provider_ready=true
provider_mode=live
provider_health_checked=true
evidence_source=live
latest_live_telemetry_at=<timestamp>
live_evidence_ready=true
telemetry_event_id=<present>
detection_id=<present>
alert_id=<present>
incident_id or response_action_id=<present>
evidence_package_id=<present>
```

### CI

`.github/workflows/staging-live-evidence-proof.yml` provides two-tier coverage:

- **`pull_request` / push**: structural fail-closed validation. Runs the
  blocker 3 guardrail tests and asserts the proof fail-closes without secrets.
  Needs no secrets and never red-Xes a PR for a missing secret.
- **`workflow_dispatch` / push to main**: real proof job. Reads the
  `STAGING_EVM_RPC_URL`, `STAGING_EVM_CHAIN_ID`, `STAGING_WORKER_ENABLED`
  repository secrets and runs `make run-staging-live-proof`. When the secret
  is absent the job clearly skips with a notice instead of failing red. The
  full RPC URL is never printed (the runner masks it; GitHub Actions also
  masks secret values in logs).

