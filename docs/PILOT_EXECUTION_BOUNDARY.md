# Pilot execution boundary

**The invariant**

> Pilot workspaces are observe, investigate, simulate, recommend, and
> evidence-only. They cannot execute production blockchain state changes.

This document states what enforces that, where, and what it does *not* cover.

---

## 1. What Pilot may and may not do

A workspace whose organization is on the **Pilot** plan (and any tenant without
the `automatic_execution` entitlement — see
[PLAN_ENTITLEMENT_MATRIX.md](PLAN_ENTITLEMENT_MATRIX.md)) keeps the whole
production security workflow and loses exactly one capability.

| Allowed                                        | Refused                                              |
| ---------------------------------------------- | ---------------------------------------------------- |
| Observe blockchain activity, read telemetry    | Sign or broadcast a transaction                      |
| Monitor contracts, detect threats              | Execute a state-changing contract call               |
| Create alerts, findings, incidents             | Pause / unpause a contract                           |
| Run investigations (deterministic and AI)      | Freeze / unfreeze a wallet or asset                  |
| Generate AI recommendations                    | Move funds, mint or burn                             |
| Simulate policies and playbooks                | Change ownership or admin roles                      |
| Approve / reject recommendations               | Execute automated remediation                        |
| Export evidence, view audit logs               | Invoke a production write-capable integration        |
|                                                | Use a customer wallet private key or signer          |

The narrowness is the point: an evaluation that cannot detect, investigate,
recommend, simulate and evidence is not an evaluation. The capability matrix is
computed from `entitlements.py`, and a test asserts that **Automatic production
execution** is the only enforced feature row an ACTIVE Pilot is refused
(`test_pilot_execution_boundary.py::test_10b_...`).

---

## 2. Where the decision is made

One module: **`services/api/app/execution_authorization.py`**.

```python
assert_execution_allowed(connection, workspace_id=..., action=..., source=...)
    -> ExecutionAuthorization      # a grant naming THIS workspace and action
    raises ExecutionForbidden      # 403 PILOT_EXECUTION_DISABLED
```

It reads the workspace's organization row through `organizations.py`, asks
`entitlements.effective_entitlements` for `automatic_execution`, and returns a
grant or refuses. It is **pure policy plus one tenant read**: it imports no
FastAPI and no `pilot`, so a worker can call it without pulling in the request
stack — a policy a worker cannot cheaply import is a policy a worker will skip.

**No caller identity is an input.** A Founder, an internal admin and an analyst
receive the same answer, because the restriction belongs to the tenant rather
than to the seat. A structural test asserts the policy functions take no `user`,
`role`, or `request` parameter.

`pilot.plan_execution_lock` — what Screen 8 renders — now delegates to this
module, so the screen and the enforcement path cannot disagree.

---

## 3. Where it is enforced (defense in depth)

```
request / API  →  service layer  →  provider boundary  →  signer material
     ①                  ②                   ③                 (never reached)
```

| # | Enforcement point | Code |
| - | ----------------- | ---- |
| ① | The execute command, as its **first** decision — before approver checks, before step-up MFA, before the deterministic gate | `pilot.execute_enforcement_action` |
| ① | The deterministic execution gate, for every other caller of it | `pilot._enforce_execution_gate` |
| ① | The compliance governance gateway routes (`POST /compliance/governance/actions`, `POST /pilot/compliance/governance/actions`) — these submit a freeze/pause **without** creating a response action, so the gate never sees them | `pilot.require_governance_action_execution_allowed` |
| ② | Re-established at the service layer immediately before each live branch; the grant it returns is what ③ verifies | `pilot.execute_enforcement_action` |
| ③ | Inside both write-capable provider calls, **unconditionally** — reaching them means production is about to be touched | `pilot._propose_safe_transaction`, `pilot._submit_freeze_wallet_governance_action` |

③ is what makes the invariant structural rather than procedural. Both provider
functions require an `ExecutionAuthorization` issued for that exact workspace and
action; a missing, mismatched or borrowed grant is refused. A future worker,
queue consumer, agent or script therefore **cannot** reach a provider by
forgetting a check upstream — there is no argument it can pass that skips the
tenant read.

### Workers and queues

The product has **no** background worker or queue consumer that executes a
response action today; the workers run monitoring, detection, asset risk, AI
triage, onboarding, retention and proof-chain work. A regression test asserts
that no `run_*`/`*_worker` module references a provider helper
(`test_17d_...`). Should one ever need to execute, ③ refuses it until it calls
`assert_execution_allowed` and carries the grant.

---

## 4. The error contract

```http
HTTP/1.1 403 Forbidden
Content-Type: application/json
```
```json
{
  "code": "PILOT_EXECUTION_DISABLED",
  "message": "Production execution is disabled for Pilot workspaces. Pilot mode supports monitoring, investigation, simulation, recommendations, and evidence only.",
  "reason": "plan_recommend_only",
  "plan": "pilot",
  "lifecycle_state": null,
  "action_id": "…",
  "action_type": "…",
  "source": "api"
}
```

`code` is the stable contract. `reason` is a separate field so the *sentence* can
differ without the code moving — a recommend-only plan and an ended evaluation
both stop a live run, but only one of them is fixed by upgrading:

| `reason`                     | Meaning                                                    |
| ---------------------------- | ---------------------------------------------------------- |
| `plan_recommend_only`        | The plan does not include production execution              |
| `evaluation_expired`         | The Pilot window closed; data is preserved                  |
| `organization_suspended`     | The organization is suspended                               |
| `organization_not_linked`    | No organization owns this workspace — fail closed           |
| `entitlement_unavailable`    | The entitlement could not be READ — fail closed             |
| `tenancy_schema_not_migrated`| **Not** a lock; there is no plan to consult (see §7)        |

403 rather than 409: the caller is authenticated and the request is well formed;
nothing they can do — collect approvals, complete a step-up, retry — changes the
answer. `409 EXECUTION_GATE_LOCKED` remains what an *unauthorized-yet* run gets,
and `PLAN_EXECUTION_NOT_ENTITLED` remains on the gate DTO so Screen 8 renders the
state. Both still block; they say different things because they mean different
things.

---

## 5. Signing credentials

Decoda **does not hold customer wallet private keys or seed phrases.** There is
no workspace-scoped write path that could store one:

* The only signer material in the product is `SAFE_SIGNER_KEY` — a
  **deployment** environment variable read through `secret_crypto.read_encrypted_env`,
  used to sign a Safe *proposal* (which the customer's own multisig signers must
  then approve). It is not tenant state and no API writes it.
* The product never calls `eth_sendTransaction`, `eth_sendRawTransaction`,
  `eth_sign` or `personal_sign`. Its entire JSON-RPC surface is read-only, and a
  regression test sweeps for both facts (`test_17_…`, `test_17b_…`).
* Pilot feedback and pilot-request submissions are **refused** when the text is
  shaped like a key or a BIP-39 mnemonic (`organizations.looks_like_secret`), so
  such material does not reach the database to be leaked later.

Because enforcement point ③ runs before the Safe payload is assembled, a refused
run never causes `_safe_signer_key()` to be called at all — a test asserts both
the behaviour and the ordering. When a deployment signer *is* configured and an
attempt is refused, the audit event records
`deployment_signer_configured: true` and a `pilot_execution_blocked_with_signer_configured`
warning is logged. **The key itself is never named, read or logged.**

---

## 6. The audit event

Every refusal — from the API, the service layer or the provider boundary —
writes `pilot_execution_blocked` to `action_history`, the incident timeline (when
the action has an incident) and the hash-chained `audit_logs`, then commits. A
refusal that left no trace would be indistinguishable from an attempt that never
happened.

Metadata is machine facts only (`execution_authorization.blocked_audit_metadata`):
`workspace_id`, `actor_type`, `actor_id`, `action_id`, `action_type`, `source`
(`api` / `service` / `provider` / `worker` / `queue` / `agent` / `script`),
`reason`, `plan`, `lifecycle_state`, `request_id`, `occurred_at`, `code`. **No
private keys, seed phrases, signing secrets, bearer tokens, or request bodies.**

Audit writing never masks the block: a failure there is logged and swallowed, and
the caller is still refused.

---

## 7. What this does **not** claim

This is a server-side authorization boundary. It is not a cryptographic
guarantee, and the following are real, known limits:

1. **Direct database access is outside it.** Anyone who can write to
   `organizations.entitlement_overrides` can grant execution. That is the
   intended unlock path for an Enterprise tenant, and it is audited — but it is
   an operator control, not a cryptographic one.
2. **A deployment that has not run migration 0150 is exempt**, because there is
   no organization plan to consult. Every pre-existing workspace-level gate
   (approval quorum, RBAC, step-up MFA, adapter configuration, the deterministic
   policy engine) still applies there. Run the tenancy migration for the boundary
   to participate.
3. **The compliance service is an internal service.** The guard sits on Decoda's
   API routes that reach it. A network path that talks to
   `COMPLIANCE_SERVICE_URL` directly is outside this boundary and must be
   protected by deployment network policy.
4. **Notification integrations stay enabled for Pilot** — Slack, webhooks, email,
   on-call alerts. Alerting is core evaluation functionality, and none of these
   changes blockchain or asset state. "Write-capable integration" here means one
   that changes production asset/contract/policy state.
5. **A compromised deployment secret is outside it.** The boundary answers "may
   this tenant execute", not "is this deployment trustworthy".

---

## 8. Tests

`services/api/tests/test_pilot_execution_boundary.py` — API refusal, privileged
callers, service layer, worker/queue sources, AI recommend-vs-execute, an
AUTHORIZED gate still refused, simulate/incidents/recommendations/evidence still
working, an entitled tenant unaffected, fail-closed on unknown/unlinked/unreadable
tenants, the provider boundary, signer non-loading, the audit event, the
compliance gateway, and the Phase-8 regression sweep.

`services/api/tests/test_pilot_execution_lock.py` — the Screen 8 gate overlay.
`apps/web/tests/response-action-plan-lock.spec.ts` — the operator-facing copy,
including the note that names the backend rather than the disabled button as the
control.
