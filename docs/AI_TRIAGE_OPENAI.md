# AI Evidence & Triage Agent — OpenAI provider (initial production provider)

This document corrects and completes the initial AI provider work from PR #1295.
The agent architecture is unchanged: it is a **constrained, evidence-grounded
incident-investigation layer**, never an autonomous responder. It only
summarizes incidents, builds evidence-linked timelines, recommends predefined
runbooks, and drafts reports. It never signs transactions, moves funds,
pauses/upgrades contracts, freezes wallets, or executes any on-chain action.
Recommendations require human approval (`response.approve`), which records a
decision and executes nothing.

Production remains **disabled** (`AI_TRIAGE_ENABLED=false`).

## 1. Provider mismatch — root cause

The user selected **OpenAI**, but PR #1295 shipped **`AnthropicTriageProvider`**
as the only concrete network provider and documented an Anthropic rollout:

- `services/api/app/ai_providers.py` registered `_PROVIDERS = {'mock', 'anthropic'}`
  — there was **no OpenAI provider**.
- `ai_triage.configuration_warnings()` and `.env.example` only described
  `anthropic` / `mock`; `AI_PROVIDER` defaulted to empty.
- The completion report described an "Anthropic provider (initial concrete
  implementation)".

The provider-neutral abstraction (`IncidentTriageProvider`) and the offline
`MockTriageProvider` were correct and are **kept**. The fix adds OpenAI as the
initial production provider, makes provider selection fail closed, and documents
OpenAI as the initial production path. The Anthropic provider is **retained but
optional**.

## 2. OpenAI Responses API implementation

`OpenAITriageProvider` (`services/api/app/ai_providers.py`):

- **provider name:** `openai`.
- **model:** from `AI_MODEL_TRIAGE` (passed in as `model`); missing model →
  `missing_model` (fail closed, no call).
- **API key:** `AI_API_KEY` (canonical) or `OPENAI_API_KEY`; missing →
  `missing_api_key` (fail closed, no call). Read at call time, **never logged**.
- **SDK:** the official `openai` Python SDK, imported **lazily** inside the call
  so importing the module never requires the SDK, and **no automated test ever
  imports or calls the real API** (tests inject a mock client).
- **Responses API call** uses `client.responses.create(...)` with:
  - `text.format = {type: json_schema, name, schema, strict: true}` — strict
    structured output (see §4).
  - `store=False` — stateless; the prompt and any hidden reasoning are never
    stored or surfaced. Only the final structured text is returned.
  - **no tools** — no web browsing, code interpreter, file search, shell, SQL,
    wallet signing, contract calls, or arbitrary URL fetching are enabled.
  - explicit per-request `timeout` (`AI_REQUEST_TIMEOUT_SECONDS`); the SDK client
    is built with `max_retries=0` because retries are owned here.
- **Bounded retries with exponential backoff:** retryable transport errors
  (timeout / HTTP 429 / HTTP 5xx / connection) are retried up to `max_attempts`
  (default 3) with `backoff_base * 2**attempt` delay. On exhaustion a stable
  `TriageProviderError(error_code, retryable)` is raised. 4xx (except 429) is
  **not** retried. The domain layer (`process_triage_job`) additionally requeues
  retryable failures across worker cycles up to `AI_MAX_RETRY_COUNT`.
- **No secrets / bodies in logs:** the provider never logs; error bodies are
  collapsed to a stable `error_code` (never propagated). The domain layer logs
  only `error_code`, provider, and model.
- **Returns only** `ProviderRawResult` (raw structured text + provider/model +
  input/output tokens + latency) — no chain-of-thought.

Error-code mapping: `provider_timeout`, `provider_rate_limited`,
`provider_unavailable` (5xx/connection), `provider_bad_request` (4xx),
`provider_error` (unknown), `provider_sdk_missing`, `missing_api_key`,
`missing_model`.

## 3. Backend validation retained (schema-valid ≠ trusted)

A schema-conforming response is **never** trusted merely because it parsed. Every
result still passes `ai_triage.validate_triage_output()`, which re-validates and
grounds against the server-built evidence snapshot:

- `incident_id` must match the snapshot (mismatch → `incident_mismatch`; the
  server value is authoritative).
- evidence references / citations must resolve to snapshot records
  (`invalid_evidence_reference`).
- telemetry IDs, transaction hashes, wallet addresses must exist in the snapshot
  (`invented_transaction`, `invented_wallet`, `invalid_evidence_reference` for an
  invented telemetry id).
- timestamps mismatching evidence are a soft warning.
- rule IDs / runbook IDs must be known (`unsupported_runbook`).
- allowed action types only; prohibited actions rejected (`prohibited_action`,
  `unsupported_action_type`).
- every factual risk finding **requires** a citation (`missing_citation`).
- confidence values are clamped to `[0, 1]`.
- `requires_human_approval` is forced `true`.

Any unsupported or invented value produces `validation_failed` (a safe terminal
state), never a `completed` result.

## 4. Structured output schema

`ai_triage.INCIDENT_TRIAGE_RESULT_SCHEMA` is the strict JSON Schema for the
`IncidentTriageResult` contract, built from the same allowed enums the validator
uses (severities, affected-entity types, risk levels, allowed action types). It
is OpenAI strict-mode compatible: every property is `required` and every object
sets `additionalProperties: false`; optional values are nullable rather than
omitted. It is passed to the provider on the prompt (`prompt['json_schema']`);
mock/anthropic ignore it. It is defense-in-depth only — the backend validation in
§3 is authoritative.

## 5. Environment variables (only genuinely-used vars)

Documented in `.env.example`:

```
AI_TRIAGE_ENABLED=false
AI_PROVIDER=openai
AI_MODEL_TRIAGE=<configured OpenAI model>
AI_API_KEY=<Railway secret>          # OPENAI_API_KEY also honored
AI_REQUEST_TIMEOUT_SECONDS=30
AI_MAX_INPUT_TOKENS=12000
AI_MAX_OUTPUT_TOKENS=2000
AI_MAX_INCIDENT_COST_USD=0.50
AI_DAILY_BUDGET_USD=25
AI_GLOBAL_DAILY_BUDGET_USD=100
AI_FAIL_CLOSED=true
# also used: AI_PRICE_INPUT_PER_MTOK, AI_PRICE_OUTPUT_PER_MTOK (cost estimation),
#            AI_MAX_RETRY_COUNT (job-level retry), AI_TRIAGE_INLINE (demo only)
```

The API key is a **Railway secret**. Never put a real key in source, GitHub,
tests, screenshots, or `.env.example`.

Provider selection:
- `AI_PROVIDER=mock` → offline deterministic mock (all tests, no network).
- `AI_PROVIDER=openai` → OpenAI Responses API.
- empty → mock (safe default).
- unknown → **fail closed**: the job lands in `failed` with `unknown_provider`;
  the mock is never silently used and no live API is reached.
- missing OpenAI key/model → clear configuration error (`missing_api_key` /
  `missing_model`), and the worker refuses to start (see §7).

## 6. Railway worker service

`services/api/app/run_ai_triage_worker.py` runs the async triage worker. Triage
is fully decoupled: **telemetry, alerts, and incident creation continue even if
the AI worker is stopped**.

**A Procfile entry does not create a running Railway service.** The `Procfile`
`ai-triage-worker:` line documents the process but Railway only runs a service
that is explicitly created from a service config. Deploy the dedicated service
from **`railway-ai-triage-worker.json`**:

```json
{ "deploy": { "startCommand": "python -m services.api.app.run_ai_triage_worker",
              "restartPolicyType": "ON_FAILURE", "restartPolicyMaxRetries": 10 } }
```

Guarantees:
- **crash is visible and restarts:** `restartPolicyType=ON_FAILURE`; a
  configuration error exits non-zero and Railway restarts it.
- **not in every API replica:** only this single dedicated service runs the
  worker; the API `web` process never claims jobs.
- **no duplicate analysis:** jobs are claimed with a conditional
  `UPDATE ai_triage_jobs SET status='running' WHERE status='queued'` plus a
  partial unique active-job index, so multiple replicas are safe.
- **disabled startup state is clear:** `AI_TRIAGE_ENABLED=false` →
  `event=ai_triage_worker_started state=disabled` + periodic
  `event=ai_triage_worker_disabled` heartbeat; the worker idles, does not exit.
- **enabled-but-invalid fails clearly:** e.g. `openai` without key/model →
  `event=ai_triage_worker_configuration_error` per problem +
  `event=ai_triage_worker_exiting reason=configuration_error` and **exit 1**.

## 7. Migration 0123 verification

`services/api/migrations/0123_ai_triage_agent.sql`:

- **idempotent:** all DDL is `CREATE TABLE/INDEX IF NOT EXISTS` and additive
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`; safe to re-apply.
- **loaded by the startup runner:** `pilot._run_migrations_once()` applies every
  `migrations/*.sql` in sorted order (gated by `RUN_MIGRATIONS_ON_STARTUP`), so
  `0123` is picked up automatically.
- **existing incidents/telemetry unaffected:** every new table is new and
  workspace-scoped; the only existing-table change is an additive, NULL-tolerant
  `incidents.dedup_key` column with a **partial** unique index that constrains
  only rows that populate it — existing rows (NULL) are untouched.
- **one-active-job-per-incident:** `uq_ai_triage_jobs_active_per_incident` is a
  partial unique index on `(incident_id) WHERE status IN ('queued','running')`;
  regeneration is allowed because terminal-state prior jobs leave the index.
- **workspace indexes exist:** snapshots, jobs, results, citations,
  recommendations, and usage events all have `(workspace_id, ...)` indexes.
- **evidence snapshots and AI results are separate tables**
  (`incident_evidence_snapshots` vs `ai_triage_results`), so raw evidence stays
  distinct from AI-generated text.
- **no existing evidence export broken:** the migration adds only new tables and
  one nullable column; no export query or existing schema is modified.

## 8. Testing

`services/api/tests/test_openai_triage_provider.py` (mocked OpenAI client) covers:
provider selection, valid structured response, strict-schema/stateless/no-tools
request shape, output-block walking (ignores reasoning), malformed response,
timeout, rate limit, provider-unavailable (5xx), retry-then-succeed, retry-limit,
4xx-not-retried, missing key, missing model, invalid citation, invented
transaction, invented telemetry id, unsupported runbook, prohibited action,
budget blocking before the provider call, unknown-provider fail-closed through
`process_triage_job`, and **no secrets/provider-body in logs**. The mock provider
remains the default in tests.

`services/api/tests/test_ai_triage_worker_states.py` covers disabled /
configuration-error / enabled startup states and worker exit codes.

Existing `test_ai_triage_agent.py` (44) and `test_ai_triage_eval_fixtures.py`
continue to pass unchanged. No automated test calls the real OpenAI API.

## 9. Offline staging verification

### A. Mock test (fully offline)

```
AI_TRIAGE_ENABLED=true
AI_PROVIDER=mock
```

1. Open an existing incident.
2. Start AI investigation (`POST /incidents/{id}/ai-triage`).
3. Verify an immutable evidence snapshot was created (hash recorded).
4. Verify the job transitions `queued → running → completed`.
5. Verify each citation opens the correct stored evidence.
6. Approve a recommendation — confirm it records a decision and **executes
   nothing** (`executed: false`).

Offline equivalent already runnable without a network:
`AI_TRIAGE_ENABLED=true AI_PROVIDER=mock python -m services.api.scripts.ai_triage_eval`.

### B. OpenAI staging test (one non-production test incident)

```
AI_TRIAGE_ENABLED=true
AI_PROVIDER=openai
AI_MODEL_TRIAGE=<configured OpenAI model>
AI_API_KEY=<Railway secret>
```

1. Use one existing **non-production** test incident.
2. Verify the structured output validates and grounds (all citations resolve).
3. Verify evidence grounding: no invented tx/wallet/telemetry survives.
4. Verify usage and estimated cost are recorded (`ai_usage_events`).
5. Verify audit events are written (snapshot created, queued, completed,
   recommendation reviewed).
6. Verify a provider failure leaves the incident unaffected (safe terminal
   state, deterministic severity unchanged).
7. Verify no autonomous action occurs — approval executes nothing.

`scripts/ai_triage_eval.py` can run the same fixtures against the live OpenAI
provider as a manual pre-enablement gate (non-zero exit on any validation
failure).

## 10. Production stays disabled

Keep `AI_TRIAGE_ENABLED=false` until **all** of the following hold:

- [ ] migration 0123 deployed
- [ ] authorization tests pass
- [ ] mock staging test passes
- [ ] OpenAI staging test passes
- [ ] budget controls work (per-incident, workspace-daily, global-daily)
- [ ] the dedicated worker service is healthy
- [ ] evidence citations are correct
- [ ] prohibited-action tests pass

The agent is **not** ready merely because the provider returns valid JSON. It is
ready only when every factual result is grounded in stored evidence, all
citations validate, budgets are enforced, and no high-impact action can execute
automatically.

## 11. Separate production issue — QuickNode base-live lag (not fixed here)

Production logs separately show the QuickNode `base-live` lane staying ~62–67
blocks behind the Base head and reporting `degraded=true`. This is a **separate
production issue** and is intentionally **out of scope** for this AI-provider
correction — no QuickNode transaction matching, telemetry persistence, or live
detection code was changed. It should be triaged on its own.
