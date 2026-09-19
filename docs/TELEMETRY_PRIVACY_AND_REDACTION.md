# Telemetry privacy and redaction

This document describes what Decoda RWA Guard ingests, what it strips before
storing or forwarding it, and — just as importantly — what it does **not**
currently ingest. It is written so a security reviewer can check every claim
against the code and the tests rather than taking it on trust.

The implementation is `services/api/app/telemetry_privacy.py` (the sanitizer),
`services/api/app/telemetry_privacy_policy.py` (the per-workspace policy), and
migration `0155_telemetry_privacy_policy.sql`. The contract is pinned by
`services/api/tests/test_telemetry_privacy_redaction.py`.

---

## 1. What Decoda currently ingests

Decoda's supported telemetry today is **public blockchain data**. Every
production ingestion path is listed here.

| Source | Raw input accepted? | Persisted? | Sent to AI? | Sanitization |
| --- | --- | --- | --- | --- |
| QuickNode Streams webhook (`/api/integrations/quicknode/streams/base`, `-live`, `-backfill`) | Signed JSON batch; only `tx_hash`, `from`, `to`, `value`, `block_number`, `chain_id` are read | Yes — `telemetry_events.payload_json`, rebuilt field-by-field | Only via the incident snapshot | `normalize_base_stream_tx` reconstructs the payload from named chain fields; the request body is never stored, and no request header is persisted |
| Stable RPC polling / realtime WebSocket (`monitoring_runner`, `base_realtime_ingestor`) | JSON-RPC responses | Yes — `telemetry_events`, rebuilt field-by-field | Only via the incident snapshot | Same field-by-field construction. Provider **error text** is sanitized by `_safe_error_message` before it reaches `monitoring_polls.error_message` / `monitored_systems.last_error_text` |
| `/pilot/threat/analyze/{contract,transaction,market}` | **Yes** — a caller-shaped JSON body | Yes — `analysis_runs.request_payload`, and a copy in `metadata.original_ui_request` | No | `persist_analysis_run` → `sanitize_ingested_payload`; the `original_ui_request` copy is sanitized in `threat_payloads._safe_metadata` |
| `/pilot/compliance/*`, `/pilot/resilience/*` | **Yes** — a caller-shaped JSON body | Yes — `analysis_runs.request_payload`, plus `governance_actions.payload` / `incidents.payload` | No | `persist_analysis_run` and `sanitize_workspace_payload` at each record-creation choke point |
| Analysis response → alert + outbound webhook delivery | Response from the internal threat/compliance/reconciliation service | Yes — `alerts.payload`, and forwarded to workspace webhooks | No | `sanitize_workspace_payload` in `_persist_live_analysis`, before both the alert write and the forward |
| AI incident triage (`ai_triage.build_prompt`) | Server-built evidence snapshot only | The snapshot is stored as the job's evidence | **Yes** | `sanitize_for_ai` — stricter than the storage boundary |
| Onboarding discovery (RPC endpoints) | Customer-supplied RPC URLs | Encrypted at rest (`secret_crypto`); a redacted form is displayed | No | `onboarding_discovery.redact_rpc_url`, `onboarding_agent.redact_text` / `redact_json`, plus SSRF validation |
| Pilot feedback (`organizations.record_feedback`) | Customer free text | Yes | No | `looks_like_secret` **refuses** the submission rather than storing and redacting it |
| Billing webhooks (Stripe / Paddle) | Signed provider body | Billing state only | No | Signature verified; the body is not stored as telemetry |
| Structured logs | Log records | stdout | No | `structured_logging._scrub` masks secret-looking keys **and** secret-shaped values (defense in depth, not the primary control) |

### What Decoda does NOT currently ingest

- No private or off-chain customer telemetry connector ships today.
- No ingestion path accepts or persists arbitrary HTTP request headers.
- No ingestion path stores a raw request body verbatim; the analysis routes
  store a **sanitized** copy of the submitted body.
- No agent, log shipper, or host metrics collector is installed on customer
  infrastructure.

The sanitizer's private-network and PII handling exists so that a future
off-chain connector lands behind a boundary that already works, not because such
a connector exists now.

---

## 2. The boundary

```
SOURCE
  ↓ schema validation
  ↓ privacy classification
  ↓ redaction / exclusion
NORMALIZED SAFE PAYLOAD
  ↓
persistence  →  AI / alerting / evidence / logs
```

Sanitization happens **before** the database write and **before** any outbound
forward — never on read, and never only when rendering a log line. The single
entry point is:

```python
sanitize_ingested_payload(payload, *, source_type, policy) -> SanitizationResult
```

`SanitizationResult` carries `payload`, `redacted_fields`, `dropped_fields`,
`rule_ids`, `changed`, and `filter_failed`. It never contains, encodes, or
hashes the original sensitive value, so a caller holding the result cannot
recover what was removed.

### Sensitivity classes

`PUBLIC_CHAIN_DATA`, `CUSTOMER_OPERATIONAL_METADATA`, `PII`, `CREDENTIAL`,
`NETWORK_IDENTIFIER`, `FREEFORM_TEXT`.

A public wallet address is classified as `PUBLIC_CHAIN_DATA`, **not** PII. It is
public by construction and it is the identifier an investigation is about.

---

## 3. Mandatory credential stripping

These apply to every workspace and cannot be disabled by any policy setting.

**By field name** (matched case-insensitively, ignoring `-`, `_`, and spaces, so
`API_KEY`, `api-key`, `apiKey` and `Api Key` all match):

```
authorization        proxy_authorization   www_authenticate
cookie               set_cookie
api_key              apikey                x_api_key        x_auth_token
access_token         refresh_token         id_token
bearer               bearer_token          auth_token
password             passwd
secret               client_secret         webhook_secret   signing_secret
shared_secret        secret_key
private_key          seed_phrase           mnemonic
recovery_code        recovery_phrase
session              session_id            session_token
csrf                 csrf_token            xsrf_token
credential           credentials
```

Matching is against a **closed set of normalized names**, not a substring scan.
`secretary`, `password_policy_enabled`, `keyboard_layout`, `authority` and
`tokenomics` are all preserved.

**By value shape**, in freeform strings:

- `Bearer <token>` and `Basic <credentials>` header shapes
- JWTs (`eyJ….….…`)
- PEM private key blocks, including blocks truncated before their `-----END` line
- URL userinfo (`scheme://user:password@host`)
- Sensitive URL query parameters
- BIP-39-shaped recovery phrases, where the phrase is the *entire* value

**Deliberately not a rule:** a bare 64-character hex string. Every wallet-transfer
row Decoda persists carries one as `tx_hash`. Redacting that shape blindly would
erase the single most important forensic identifier the product has. Key material
is caught by its field name instead.

### Headers

No ingestion path persists HTTP headers today. The sanitizer nonetheless provides
`sanitize_headers`, which is **allowlist-based**: only explicitly listed headers
survive, and `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`,
`X-Api-Key` and `X-Auth-Token` are refused even if an allowlist names them.
Authentication material is consumed for verification and discarded. The *name* of
a dropped header is recorded — that is useful evidence — but never its value.

### URLs

A stored or logged URL has its userinfo removed, secret-looking path segments
replaced, sensitive query parameter **values** replaced, and its fragment dropped:

```
https://user:pass@base-mainnet.g.alchemy.com/v2/Ab3xKeyMaterial99?token=xyz#f
→ https://base-mainnet.g.alchemy.com/v2/[REDACTED]?token=[REDACTED]
```

The host and route survive, because "which provider failed, on which endpoint" is
the whole diagnostic value. Public provider hostnames are **not** redacted.

---

## 4. Customer controls

Per workspace, stored in `workspace_telemetry_privacy_policies`, read and written
at `GET` / `PUT /workspace/telemetry-privacy` (requires `security.manage`):

| Setting | Effect | Default |
| --- | --- | --- |
| `excluded_fields` | Field names dropped entirely (key removed) | empty |
| `redacted_fields` | Field names kept with `[REDACTED]` as the value | empty |
| `allowed_metadata_fields` | When non-empty, the only metadata keys retained | empty (no allowlist) |
| `redact_private_ips` | Apply the private-network rules | `true` |
| `redact_emails` | Redact email addresses | `false` |
| `private_network_mode` | `preserve` / `mask` / `pseudonymize` / `drop` | `mask` |

Policy order:

```
mandatory Decoda security rules
        ↓
customer exclusion / redaction rules
        ↓
normalized payload
```

A customer policy may only make redaction **stricter**. The mandatory rules are
compiled into the sanitizer and are not read from this table, so no stored value
can reach them. Naming a mandatory credential field in `allowed_metadata_fields`
is **rejected** at the API with `MANDATORY_REDACTION_CANNOT_BE_DISABLED`, rather
than accepted as a rule that would silently never apply.

Every change is audited as `workspace.telemetry_privacy_policy_changed`,
recording the actor, the timestamp, and which settings changed. It records field
*names* (which are the configuration) and never the contents of the fields being
excluded.

### Private network identifiers

RFC1918 IPv4, IPv6 ULA, loopback, link-local, and internal hostname suffixes
(`.local`, `.internal`, `.lan`, `.corp`, `.svc.cluster.local`, …) are recognised.
Public addresses and public RPC provider hostnames are **not** redacted — they are
operationally necessary and not private.

`pseudonymize` uses HMAC-SHA256 from the standard library, keyed by
`TELEMETRY_PRIVACY_PSEUDONYM_KEY` and scoped by workspace id, so the same address
correlates across events within a workspace and does **not** correlate across
workspaces. This key is deliberately separate from evidence-signing,
authentication, and secret-encryption keys. With no key configured,
pseudonymization degrades to redaction: an unkeyed digest of a space as small as
RFC1918 is reversible by enumeration and would be pseudonymity in name only.

---

## 5. AI boundary

Before any evidence leaves Decoda for an external model provider,
`ai_triage.build_prompt` runs the snapshot through `sanitize_for_ai`.

**Never sent:** API keys, bearer/access tokens, authorization headers, cookies,
private keys, seed phrases, passwords, client secrets, session identifiers. In
addition, the AI boundary is stricter than storage by construction: email
addresses and private network identifiers are removed **regardless** of the
workspace's own setting, because a model provider is a different trust boundary
from the customer's own database.

**Always sent, intact:** the public blockchain identifiers the model must cite —
transaction hashes, addresses, amounts, block numbers, chain ids, timestamps and
record ids. A triage result that could not name the transaction it is about would
be worthless, and the grounding validator requires those references.

Only safe metadata is recorded about the redaction (`redaction_count`,
`rule_ids`, `source_type`). The redacted content is never recorded, logged, or
forwarded.

The AI path is **fail-closed**: if sanitization cannot complete, it raises and no
provider call is made.

---

## 6. Evidence behaviour

Where sanitization occurred, the record carries a `privacy_processing` block:

```json
{
  "privacy_processing": {
    "applied": true,
    "source_type": "customer_request",
    "redacted_fields": 3,
    "rules": ["credential.key_match", "network.private_ip"],
    "filter_failed": false
  }
}
```

It states counts, rule ids and field paths. It never contains a redacted value —
putting the secret into provenance metadata would defeat the redaction.

Two facts are kept distinct throughout:

- **redacted by privacy policy** — the key is still present, with `[REDACTED]` as
  its value, and its path is listed in `redacted_fields`;
- **absent in the source** — no key at all, and it appears in no list.

`applied` is `false` when nothing was removed, so a package is never described as
sanitized when it was not — and never described as containing an original it does
not contain.

Public on-chain evidence is unaffected: a normal QuickNode or RPC-polling
telemetry row is field-equivalent before and after sanitization, which is
asserted directly in the test suite.

---

## 7. Logging behaviour

`structured_logging._scrub` masks secret-looking **keys** (pre-existing) and now
also strips credential **shapes from values**. That closed a real gap:
`error_message` matches none of the key hints, and a transport exception routinely
quotes the URL it failed on — an RPC URL that carries the provider API key.

Private network identifiers are deliberately *not* redacted in Decoda's own
operational logs: those addresses are Decoda's infrastructure, and masking them
would cost diagnostic value for no privacy gain. The private-network rules apply
to ingested customer telemetry, where the address belongs to the customer.

Log scrubbing is **defense in depth, not the primary control**. The primary
control is that the value was already removed at ingestion.

---

## 8. Failure behaviour

Per source type:

| Source type | On sanitizer failure |
| --- | --- |
| `private_offchain` | **Refuse ingestion.** Raises `PrivacyFilterError` with code `INGESTION_PRIVACY_FILTER_FAILED`. No raw payload is persisted, and the error carries no fragment of it |
| `customer_request` | Refuse, as above |
| `ai_context` | Refuse, as above — nothing is forwarded |
| `public_chain` | **Degrade, do not fail.** The payload is reduced to its public-chain field allowlist (primitives only), `filter_failed` is set, and `telemetry_ingestion_privacy_failures_total` is incremented |

The public-chain exception is deliberate: taking live monitoring offline over a
filter bug is the worse failure. The degradation is recorded, counted and visible
on the row — never silent — and the reduced payload still cannot carry a
credential, because only allowlisted public-chain keys survive it.

`sanitize_error_text` never raises: an error path that raised while sanitizing an
error would lose the operational signal entirely, so its fallback is the exception
class name.

---

## 9. Metrics

```
telemetry_redaction_events_total{source_type, rule_id}
telemetry_redacted_fields_total{source_type}
telemetry_ingestion_privacy_failures_total{source_type}
```

Labels are the source type and a closed rule-id vocabulary. Payload values, email
addresses, IP addresses, token fragments and wallet identifiers are never used as
labels, and cardinality is bounded by construction.

---

## 10. What this does and does not claim

Supported:

> Decoda applies mandatory credential filtering before supported private telemetry
> is persisted or forwarded. Public blockchain identifiers required for
> investigation remain intact.

> Decoda strips supported credential fields — authorization tokens, cookies, API
> keys, passwords, private keys, and seed phrases — before private telemetry is
> stored or forwarded. Workspace privacy policies can apply additional exclusions
> or redaction. Public blockchain identifiers required for security investigation
> are preserved.

Not supported, and not to be claimed:

- ~~"No sensitive data ever reaches Decoda."~~ Decoda necessarily receives the
  inbound request before it can sanitize it. The guarantee is about
  **persistence and forwarding**, not about receipt.
- ~~"All telemetry is anonymized."~~ It is not. Public blockchain identifiers are
  retained deliberately, because they are what makes the evidence usable.
- Redaction is rule-based. It covers the documented key names and value shapes.
  A credential in a field name nobody anticipated, with a shape no rule matches,
  can still be stored — which is why `excluded_fields` exists as a customer-side
  control and why the analysis routes remain the narrowest surface.
