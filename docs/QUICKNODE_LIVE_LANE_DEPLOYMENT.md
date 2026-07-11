# QuickNode Live Chain-Tip Lane — Deployment

Real-time detection of monitored-wallet transfers at the **current Base chain tip**,
independent of the single QuickNode Stream's historical replay (which starts at an old
block, e.g. `stream_started_at_block=48391739`, and never reaches the tip in time).

## Root cause this addresses

The one production QuickNode Stream is configured provider-side to replay sequentially
from an old block and posts to `POST /api/integrations/quicknode/streams/base`. That
route advances `stream_key='base'` — a **delivery** high-water mark tens of thousands of
blocks behind the head — so a fresh transfer only appears after Stable RPC Polling.
There was also **no dedicated Railway service** running `run_quicknode_live_worker`, and
no webhook route a current-block stream could post to. Backend code alone cannot fix a
provider stream that keeps replaying from block 48391739 — the dashboard must send
current blocks to a **live** route.

## Two independent lanes

| Lane | Route | Checkpoint identity | detected_by | Controls live UI? |
|------|-------|---------------------|-------------|-------------------|
| Live (chain tip) | `POST /api/integrations/quicknode/streams/base-live` | `quicknode:base:live` | `quicknode_stream` | **Yes** |
| Historical backfill | `POST /api/integrations/quicknode/streams/base-backfill` (or the RPC worker) | `quicknode:base:backfill` | `quicknode_stream_backfill` | No |
| Legacy delivery (unchanged) | `POST /api/integrations/quicknode/streams/base` | `base` | `quicknode_stream` | No (never live health) |

The live lane also runs as an RPC poller in the dedicated worker
(`run_quicknode_live_worker`) so detection at the tip does not depend on the provider
stream being reconfigured first; Stable RPC Polling remains the always-on fallback.

## Railway services

| Service | Start command | Config file | Key env |
|---------|---------------|-------------|---------|
| API (web) | `uvicorn services.api.app.main:app --host 0.0.0.0 --port $PORT` (Dockerfile default) | `railway.json` | `QUICKNODE_STREAMS_SECRET`, `DATABASE_URL`, `REDIS_URL`, Base RPC |
| Stable RPC monitoring worker | `python -m services.api.app.run_monitoring_worker` | `railway-worker.json` | `DATABASE_URL`, Base RPC |
| **QuickNode live worker (new)** | `python -m services.api.app.run_quicknode_live_worker` | **`railway-quicknode-live-worker.json`** | `QUICKNODE_LIVE_ENABLED=true`, Base RPC (`EVM_RPC_URL_8453`/`BASE_EVM_RPC_URL`/`EVM_RPC_URL`), `DATABASE_URL`, `REDIS_URL` |

Create a **new Railway service** in the same project from this repo and set its config
to `railway-quicknode-live-worker.json` (or set `APP_START_COMMAND=python -m
services.api.app.run_quicknode_live_worker` on the service — the Dockerfile CMD honors
`APP_START_COMMAND`). `restartPolicyType: ON_FAILURE` makes a crash or a
configuration-error exit (code 2) visible and auto-restarting. Multi-replica safe: a
Postgres advisory lock (`quicknode:base:live`) ensures only one replica processes each
tip range, and the persist path dedupes by `tx_hash`, so API replicas and worker
replicas never double-persist.

Do **not** start the live worker with `python a.py & python b.py` inside another
service — background shell jobs hide crashes and defeat Railway's restart/lifecycle.

## Migration order

Migrations auto-run at API startup via `pilot.run_migrations()` (globs `*.sql` sorted,
tracked in `schema_migrations`, each idempotent):

- `0120_quicknode_stream_checkpoints.sql` — the checkpoint table.
- `0121_quicknode_live_backfill_checkpoints.sql` — documents the live/backfill identities.
- `0122_quicknode_base_checkpoint_to_backfill.sql` — copies the legacy `base` cursor into
  `quicknode:base:backfill` (`ON CONFLICT DO NOTHING`); never creates or seeds
  `quicknode:base:live` from the old block. The live lane seeds itself from the current
  chain head on first run. The worker also performs this copy at runtime
  (`seed_backfill_from_base_checkpoint`) for deployments that have not yet migrated.

## Expected startup logs (proof of readiness)

API service (every boot):
```
event=quicknode_live_lane_started deployment_commit_sha=<sha> stream_lane=live stream_key=base-live checkpoint_identity=quicknode:base:live chain_head=<n> checkpoint_block=<n> lag_blocks=<n> configuration_valid=true
```

QuickNode live worker service:
```
# enabled + Base RPC present:
quicknode_live_worker_started deployment_commit_sha=<sha> poll_interval_seconds=3 backfill_enabled=true
event=quicknode_live_lane_started ... lag_blocks=<n> configuration_valid=true
# disabled:
event=quicknode_live_lane_disabled deployment_commit_sha=<sha> enabled=false reason=QUICKNODE_LIVE_ENABLED_not_true ...
# enabled but misconfigured (exits 2, Railway restarts):
event=quicknode_live_lane_configuration_error severity=high deployment_commit_sha=<sha> enabled=true reason=base_rpc_not_configured ...
```

Per live batch and match:
```
event=quicknode_stream_batch deployment_commit_sha=<sha> stream_lane=live stream_key=base-live checkpoint_identity=quicknode:base:live first_block=<n> last_block=<n> checkpoint_block=<n> chain_head=<n> lag_blocks=<n> tx_count=<n> matched=<n> persisted=<n> duplicates=<n> degraded=false
event=quicknode_live_match tx_hash=0x... workspace_id=<uuid> target_id=<uuid> chain_id=8453 from=0x... to=0x... block_number=<n> chain_head=<n> lag_blocks=<n> persisted=true duplicate=false redis_publish_success=true
event=telemetry_persisted ... committed=true
event=telemetry_redis_publish ... success=true
```

## QuickNode dashboard actions (provider side — required)

Backend code cannot fix a stream still replaying from block 48391739. In the QuickNode
dashboard:

1. **Create a NEW Stream** (do not repoint the historical one onto the live route).
2. Network: **Base mainnet** (chain_id 8453).
3. Start position: **Latest / current block** (not a historical block).
4. Destination (webhook): `https://<api-host>/api/integrations/quicknode/streams/base-live`.
5. Signing: keep HMAC signing enabled; the security token must equal
   `QUICKNODE_STREAMS_SECRET` (same secret validates all three routes; the live route
   enforces the identical signature + timestamp replay protection).
6. Confirmations: match `QUICKNODE_LIVE_CONFIRMATION_BLOCKS` (default 2) so delivered
   blocks are safe against tip reorgs.
7. Historical traffic: either leave the existing `…/streams/base` stream as-is (legacy
   delivery + gap backfill) **or** create a separate replay stream pointing ONLY to
   `…/streams/base-backfill`. Never send the live and historical streams to the same
   route — the lane is decided by the route, and mixing them corrupts the lag signal.
