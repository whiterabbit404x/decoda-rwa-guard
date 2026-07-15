# Onboarding Agent Worker — Deployment & Live Verification

Activates the dedicated Railway service that drains the durable
`onboarding_agent_runs` queue for the Autonomous Onboarding Agent (Screen 1), and
gives the exact, ordered steps to verify the LIVE `/onboarding` flow end-to-end.

## Root cause this addresses

Discovery is enqueued as a durable job on `POST /api/onboarding/sessions/{id}/discover`.
The API also runs the pipeline **inline** right after enqueue
(`ONBOARDING_INLINE_WORKER=true`, the default) so single-process deployments still
progress. There was **no dedicated Railway service** running
`run_onboarding_worker`, so if inline execution is ever disabled the queue would fill
without a consumer — jobs sit `queued`, and the session stays at "Ready 0/10 pending".

Two independent failure modes produced the reported LIVE symptom:

1. **No dedicated worker + inline disabled** → queued jobs never claimed. Fixed by the
   new service below (`railway-onboarding-worker.json`) and the safe migration order.
2. **A stale draft restored in the browser** → a session created before `/discover`
   ever ran (a pre-fix bundle, or a `/discover` that hard-failed) is re-hydrated from
   `localStorage` and shows "Ready", 0/10, every step pending, Run button still enabled.
   Fixed in the frontend by `isAbandonedDraftSession()` (discards the persisted id and
   returns the user to a fresh intake form instead of trapping them).

Backend + frontend code for the correct flow is already merged (PR #1304): the single
Run button does `POST …/sessions` → persist id → `POST …/sessions/{id}/discover` →
render the canonical snapshot. This document makes the **deployment** operational.

## Railway services

| Service | Start command | Config file | Key env |
|---------|---------------|-------------|---------|
| API (web) | `uvicorn services.api.app.main:app --host 0.0.0.0 --port $PORT` (Dockerfile default) | `railway.json` | `DATABASE_URL`, `LIVE_MODE_ENABLED=true`, `REDIS_URL`, Ethereum + Base RPC |
| Stable RPC monitoring worker | `python -m services.api.app.run_monitoring_worker` | `railway-worker.json` | `DATABASE_URL`, RPC |
| **Onboarding worker (new)** | `python -m services.api.app.run_onboarding_worker` | **`railway-onboarding-worker.json`** | `DATABASE_URL` (same as API), `LIVE_MODE_ENABLED=true`, `REDIS_URL`, Ethereum RPC (`EVM_RPC_URL_1`), Base RPC (`EVM_RPC_URL_8453`), commit SHA (`RAILWAY_GIT_COMMIT_SHA`) |

Create a **new Railway service** in the same project from this repo and point its config
at `railway-onboarding-worker.json` (or set `APP_START_COMMAND=python -m
services.api.app.run_onboarding_worker` on the service — the Dockerfile CMD honors
`APP_START_COMMAND`). `restartPolicyType: ON_FAILURE` makes a configuration-error exit
(code 1) visible and auto-restarting rather than idling on 503s.

**The onboarding worker must use the same `DATABASE_URL` as the API service** — it
claims the same `onboarding_agent_runs` rows the API enqueues. The claim is a
distributed-safe conditional `UPDATE ... WHERE status='queued'`, so inline + dedicated
execution can never double-process a run.

Do **not** start the worker with `python a.py & python b.py` inside another service —
background shell jobs hide crashes and defeat Railway's restart/lifecycle.

## Safe inline → dedicated migration order

`ONBOARDING_INLINE_WORKER` defaults to `true`. **Do not set it to `false` until the
dedicated worker is proven to be claiming jobs**, or discovery jobs queue forever.

1. Deploy the onboarding worker service (config above).
2. Confirm the startup log marker:
   `event=onboarding_worker_started onboarding_worker_registered=true … app_commit_sha=<sha>`
3. Confirm it can reach Postgres (no `event=onboarding_worker_configuration_error`; the
   process stays up instead of exiting 1).
4. Confirm it polls the queue (`event=onboarding_worker_cycle …` appears when a run is due).
5. Submit a test discovery from `/onboarding` (see retest cases below).
6. Confirm the worker claims and updates it (`processed=1 run_id=… session_id=…`).
7. **Only then** set `ONBOARDING_INLINE_WORKER=false` on the **API** service.

If the worker is ever unavailable, keep inline execution enabled (leave
`ONBOARDING_INLINE_WORKER` unset/`true`) rather than leaving jobs queued forever.

## RPC configuration (per chain, no secrets)

Discovery resolves RPC endpoints in this order (`onboarding_agent._default_rpc_urls`):

1. Session-attached custom RPC endpoints (encrypted at rest).
2. `ONBOARDING_RPC_URLS` and `ONBOARDING_RPC_URLS_<chain_id>` (comma-separated).
3. Fallback to the monitoring RPC config: `EVM_RPC_URLS`, `EVM_RPC_URL_<chain_id>`
   (`EVM_RPC_URL_1` for Ethereum Mainnet, `EVM_RPC_URL_8453` for Base Mainnet).

Set at least:

| Chain | Chain id | Variable(s) |
|-------|----------|-------------|
| Ethereum Mainnet | 1 | `EVM_RPC_URL_1` (or `ONBOARDING_RPC_URLS_1`) |
| Base Mainnet | 8453 | `EVM_RPC_URL_8453` (or `ONBOARDING_RPC_URLS_8453`) |

**Never print full RPC URLs — they may contain API keys.** When verifying, log only
`rpc_configured=true/false`, the chain id, and the redacted provider host.

If no RPC is configured for the selected chain, discovery fails **closed**: the
`connect_chain` step is marked `failed` with `error_code=no_rpc_endpoint` and the
session becomes `partial` (surfaced in the UI as `RPC_NOT_CONFIGURED` with an "Open
Integrations" action). It never remains at 0/10 pending.

## Deployment / build verification (which commit is live)

Both services expose their deployed commit without leaking secrets:

- **Web bundle:** `GET https://rwa.decodasecurity.com/api/build-info` →
  `commitSha`, `shortCommitSha`, `branch`, `buildTimestamp`, `vercelEnv`,
  `runtimeConfig`. Compare `commitSha` to the merged PR #1304 commit; if it differs, the
  deployed JS bundle is stale — trigger a rebuild/redeploy and confirm no CDN/browser/
  service-worker cache is serving the old bundle (the route sends `Cache-Control:
  no-store`).
- **API service:** `GET <api-host>/health` → `backend_git_commit`, `backend_build_id`,
  `service`, `live_mode_enabled`. `git rev-parse HEAD` for the intended commit and
  compare.
- **Onboarding worker:** the `event=onboarding_worker_started … app_commit_sha=<sha>`
  log line reports the worker's deployed commit.

A merged PR does **not** imply production is running that commit — verify all three
match before declaring the fix live.

## Verifying the durable run in Postgres (redacted)

For one manual test session, inspect the queue and report safely (short/redacted ids,
no RPC URLs). Exactly one run should exist per discovery:

```sql
-- Session state
SELECT left(id::text, 8) AS session, status, current_step, error_code, updated_at
FROM onboarding_sessions WHERE id = '<session-uuid>';

-- Durable run(s) for that session (expect exactly one)
SELECT left(id::text, 8) AS run, run_type, status, retry_count,
       worker_id, created_at, started_at, finished_at, left(error_message, 60) AS err
FROM onboarding_agent_runs WHERE session_id = '<session-uuid>'
ORDER BY created_at;
```

Possible run states: `queued`, `running`, `completed`, `partial` (session-level),
`failed`. No run should remain `queued` indefinitely — if it does, the worker is not
claiming (check step 2–4 above); the API's inline path is the fail-safe meanwhile.

## SSE vs polling

- SSE subscribes on the **new** session id (`/api/onboarding/sessions/{id}/events`); the
  worker/API publishes on that same id, and DB state is written **before** each event.
- Polling refetches the authoritative snapshot every 2.5 s while the session is active
  and SSE is not `live`, so a connected-but-silent stream still advances the UI.
- **"SSE connected" is not "discovery healthy."** Progress is derived only from the
  canonical session + step rows.

## Production retest cases (run after Start over → fresh session)

| Case | Input | Expected |
|------|-------|----------|
| A — EOA wallet | a wallet address on the selected chain | discovery request sent; first step runs; session → `partial`; `error_code=no_deployed_contract` → UI `NO_CONTRACT_BYTECODE` ("appears to be a wallet"); **not** pending |
| B — Ethereum Mainnet contract | a deployed contract, chain id 1 | chain id 1 verified; bytecode found; deterministic steps progress; findings / proposal appear |
| C — Base Mainnet contract | a deployed contract, chain id 8453 | chain id 8453 verified; run claimed; timeline progresses; proposal or actionable failure |
| D — Missing RPC | a chain with no configured RPC | session → `partial`; `error_code=no_rpc_endpoint` → UI `RPC_NOT_CONFIGURED` with Retry / Open Integrations; **not** pending |

After **Start over**, a new submit must create a **new** session id and immediately call
`/discover`; a refresh must not re-hydrate the old stale draft.
