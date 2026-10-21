# Decoda Assets and Decoda Vault → Guard (testnet)

Decoda Assets and Decoda Vault send Guard signed events about what happens in them
(a supply mismatch, a blocked operation, an abnormal transfer). Guard analyses them in
the workspace an organization admin chose, raises alerts and incidents, and reports
back: alerts, incidents and monitoring coverage to Assets; risk on the addresses an alert
names, and coverage, to Vault. Vault applies that risk in its deterministic policy
engine (a critical signal blocks further operations on the address).

**These are testnet product events.** Every alert and incident created from one says so
(`Testnet product event`, plus `synthetic data` when the producer marks its data
synthetic, as Assets does). They are never written to telemetry, detection or
monitoring tables, never count towards live monitoring health or reporting systems, and
are not live chain evidence.

## Intake

`POST /api/integrations/decoda/events` (no session; authenticated by its signature).

| Header | Value |
|---|---|
| `X-Decoda-Producer` | `decoda-assets` or `decoda-vault`; selects the shared secret |
| `X-Decoda-Timestamp` | unix seconds; accepted within ±300 s |
| `X-Decoda-Signature` | `v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>` |
| `X-Decoda-Event-Id` | the envelope's `eventId`; a replay is acknowledged and never processed twice |

The signature is checked before anything touches the database. The envelope must name
`producer` equal to the header, an `eventType` with the producer's prefix (`assets.`,
`vault.`), `environment: testnet`, and `tenant.platformOrganizationId` — the Decoda
platform organization, matched to `organizations.platform_organization_id`. Bodies are
limited to 256 KiB, enforced while the body is read (a chunked body is cut off at the
limit, never buffered whole). An `occurredAt` that is not a real ISO 8601 time is
dropped (stored as unknown), not refused. The shared vectors in
[`docs/contracts/decoda-signature-v1.vectors.json`](contracts/decoda-signature-v1.vectors.json)
are identical in all three repositories, and each one's tests check its signer and
verifier against them.

| Answer | Meaning (what the sender does) |
|---|---|
| 202 `{accepted, duplicate, routed, detection}` | recorded (or already recorded) |
| 400 | malformed body, or event id differs from the envelope (dead) |
| 401 | missing, stale or invalid signature (dead) |
| 413 | body too large (dead) |
| 422 | unknown producer, unsupported event type or environment, unresolvable or unknown tenant (dead) |
| 503 | intake not configured for this producer, or database unavailable (retry) |

**Routing.** An owner or admin of the organization (who must also be an admin of the
workspace, with MFA as the workspace requires) routes each product to one workspace on
**Integrations → Decoda products**. That admin is recorded as the user of the alerts and
incidents Guard creates from the product's events. Events for an organization with no
route are kept as **unrouted**: recorded, not analysed, and counted on Integrations;
routing later does not re-analyse them.

## Detectors

| Event | Guard creates |
|---|---|
| `assets.ownership.mismatch`, critical | critical alert + incident (evidence: the event) |
| `assets.ownership.mismatch`, high | high alert |
| `assets.nav.deviation` | alert at the reported severity |
| `assets.operation.blocked`, `assets.settlement.failed` | medium alert |
| `assets.distribution.blocked` (blocked before Vault) | low alert |
| `assets.tokenization.completed` | monitoring coverage for the asset |
| `vault.transaction.abnormal_transfer`, `vault.approval.repeated_failures` | high alert |
| `vault.transaction.blocked` | medium alert |
| anything else | recorded only |

## Signals back

Guard derives what to report from the current state of its alerts and incidents, so any
status change (whichever screen or workflow made it) is reported; an unchanged state is
not sent again. Each new state is a new version with its own `X-Decoda-Event-Id`, signed
with the same v1 scheme (producer `decoda-rwa-guard`). The body carries the same id as
`event_id`, so the signature covers it: receivers refuse a body whose `event_id` differs
from the header, and a captured signal cannot be replayed under a fresh id. A failure of
an older version never affects a newer one written while it was in flight: the newer
version stays pending. 408/429/5xx and network errors
are retried with backoff (dead after 10 attempts); other 4xx are dead at once. Admins
can send failed signals again from Integrations.

| To | Signal | When |
|---|---|---|
| Assets | `alert`, `incident` (shape of `POST /v1/integrations/rwa-guard/signals`) | an alert or incident from an Assets event is created or changes |
| Assets | `coverage` (organization and asset) | after `assets.tokenization.completed` |
| Vault | `risk` (`critical` / `elevated` / `normal`) on the addresses an alert names | a high or critical alert opens, changes or closes — only for an organization that also routed Vault |
| Vault | `coverage` | for the workspace Vault is routed to; re-sent every 10 minutes (Vault shows older reports as stale) |

Coverage is only what Guard can stand behind: asset monitoring and incident detection
are `active` because events are routed and analysed; contract and wallet monitoring are
`active` only when an enabled Guard monitoring target exists for **every** address
concerned, and `not_configured` (Assets) or `inactive` / `not_connected` (Vault)
otherwise.

Delivery runs right after each accepted event, in the background job runner, and in
`python -m services.api.app.run_decoda_signal_worker` (one pass every 30 s; safe as
several replicas). Without that worker, a change no new event triggers (an alert resolved
in Guard) is reported at the next event or job run.

## Configuration

| Variable | Purpose |
|---|---|
| `DECODA_ASSETS_INBOUND_SECRET` | verifies events from Decoda Assets (`ASSETS_RWA_GUARD_SIGNING_SECRET` there) |
| `DECODA_VAULT_INBOUND_SECRET` | verifies events from Decoda Vault (`VAULT_RWA_GUARD_SIGNING_SECRET` there) |
| `DECODA_ASSETS_SIGNALS_URL`, `DECODA_ASSETS_SIGNALS_SECRET` | Assets' `…/v1/integrations/rwa-guard/signals` and `ASSETS_RWA_GUARD_INBOUND_SECRET` |
| `DECODA_VAULT_SIGNALS_URL`, `DECODA_VAULT_SIGNALS_SECRET` | Vault's `…/v1/integrations/rwa-guard/signals` and `VAULT_RWA_GUARD_INBOUND_SECRET` |

Signal URLs must be `https` (plain `http` only for localhost). Unset, the product shows
as not configured and nothing is accepted from, or sent to, it. Each secret is shared
by exactly one sender and one receiver; never reuse one across directions or products.

## Rollback

Migration `0159_decoda_product_intake.sql` only adds three tables; the previous release
never reads them. Redeploying it stops the intake (the route answers 404, which the
senders dead-letter: pause their delivery first) and the signals. To remove the tables:
`BEGIN; DROP TABLE decoda_product_signals, decoda_product_events, decoda_product_links; DELETE FROM schema_migrations WHERE version = '0159_decoda_product_intake.sql'; COMMIT;`
Alerts and incidents already created stay, labelled as testnet product events.
