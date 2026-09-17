# Mandatory Pilot MFA boundary

**The invariant**

> Every HUMAN user of a Pilot workspace must have MFA enrolled AND must have
> completed an MFA challenge on the CURRENT authentication session before any
> Pilot business or data API answers them.

This document states what enforces that, where, what stays reachable before it
is satisfied, and what it does *not* cover.

---

## 1. Architecture before this change

Authentication resolved a caller in one of two functions
(`pilot.authenticate_with_connection`, `pilot.authenticate_request`), and MFA was
checked somewhere else entirely — inside `_require_workspace_permission`, which
only routes that need a *named permission* call.

That left four ways for a human to reach Pilot customer data without MFA:

1. **Most data routes never used the permission helper.** `list_assets`,
   `list_alerts`, `list_incidents`, `list_audit_events`, `list_exports`,
   `get_export_artifact_content`, `list_workspace_api_keys`,
   `list_slack_integrations` and many others call `authenticate_with_connection`
   directly and then `resolve_workspace`. No MFA check ran on any of them.
2. **The policy defaulted to off.** `workspace_auth_policies.mfa_enforcement`
   defaults to `optional` (migration 0092), and nothing raised it for a Pilot
   tenant. Even on the routes that *did* check, the answer was "not required".
3. **Enrollment was confused with authentication.** The check that existed
   passed as soon as `users.mfa_enabled_at` was set. A session created *before*
   the account enrolled — or a second browser that never completed a challenge —
   satisfied it.
4. **Invitation acceptance minted a full session.** `signup_invited_user` →
   `create_invited_account` issues a bearer session, and
   `accept_pilot_invitation` then provisions the Pilot workspace. Between those
   and enrollment, the session could read everything.

All four were verified against `main` before the change.

---

## 2. Where the decision is made now

One module — **`services/api/app/mfa_authorization.py`** — and one chokepoint.

```python
# the policy (framework-free, no FastAPI, no `pilot` import)
mfa_authorization.resolve_plan_floor(connection, workspace_id)   -> the plan's floor
mfa_authorization.decide_access(enforcement=..., role=...,       -> allowed / which refusal
                                mfa_enrolled=..., session_mfa_completed=...)

# the enforcement (services/api/app/pilot.py)
require_pilot_mfa(connection, request, user, session=..., purpose=...)
    -> the evaluated state
    raises 403 MFA_ENROLLMENT_REQUIRED   # no second factor on the account
    raises 403 MFA_CHALLENGE_REQUIRED    # enrolled, but not on THIS session
```

`require_pilot_mfa` is called from **both** authentication entry points:

```
    every authenticated customer route
        └── pilot.authenticate_with_connection(connection, request)   ← boundary
        └── pilot.authenticate_request(request)                       ← boundary
                _validate_session()        session row, incl. its MFA record
                build_user_response()      enrollment flag + memberships
                require_pilot_mfa()        refuse, or return the state
```

Nothing else in the service authenticates a bearer session. A test sweeps the
whole `services/api/app` tree for a second `decode_access_token` caller and
fails if one appears (`test_9b_no_other_module_authenticates_a_bearer_session_of_its_own`).

### Default-deny by request path

The boundary is **default-deny**: any path that is not on
`pilot.PILOT_MFA_BOOTSTRAP_PATHS` is protected. A route added tomorrow is
protected the moment it authenticates, with no further change. A request whose
path cannot be read at all is treated as protected.

### The effective policy

`pilot.workspace_effective_mfa_enforcement` is the ONE function that answers
"does this workspace require MFA, and why":

```
effective enforcement = strongest(configured workspace policy, plan floor)
```

| Tenant state                       | Floor         | Result                              |
| ---------------------------------- | ------------- | ----------------------------------- |
| Plan = Pilot                       | `all_members` | mandatory, whatever the policy says |
| Plan = Scale / Enterprise          | none          | the configured policy, unchanged    |
| Workspace has no organization link | `all_members` | fail closed (it heals into a Pilot) |
| Tenant row unreadable              | `all_members` | fail closed                         |
| Tenancy schema not migrated (0150) | none          | the configured policy, unchanged    |

For a Pilot the policy row is not even read — nothing a customer can write to
`workspace_auth_policies` is stronger than `all_members`, so there is no
configuration that turns it off.

---

## 3. Two controls, kept separate

| | Login MFA (this boundary) | Step-up MFA (unchanged) |
|---|---|---|
| Question | Did this session complete a second factor at all? | Is that factor recent AND held right now? |
| Fact | `auth_sessions.mfa_verified_at IS NOT NULL` | `mfa_verified_at` + `totp`/`recovery_code` in `authentication_methods` + `reauthenticated_at` inside the workspace window |
| Satisfied by | sign-in challenge, enrollment confirmation, recovery-code sign-in, OIDC `amr`, session step-up | session step-up (`POST /auth/session/step-up`) or a fresh reauthentication |
| Lifetime | the session | the workspace's `reauthentication_minutes` |
| Guards | every Pilot business/data API | response-action approval, rejection, execution |

A fresh TOTP code is **never** demanded per request: once a session carries
`mfa_verified_at`, the login boundary is satisfied for its lifetime. The
response-action gates (`_require_session_mfa`,
`_require_action_approval_session_mfa`, `_session_approval_stepup_satisfied`)
are untouched by this change.

---

## 4. Bootstrap endpoints — what stays reachable

32 of 416 declared routes. Every one is unauthenticated by nature, completes
MFA, ends the session, or returns nothing but the caller's own identity.

| Endpoint | Why it is exempt |
| --- | --- |
| `GET /health`, `/health/*`, `/metrics`, `/auth/health` | Liveness. No session is resolved. |
| `GET /auth/csrf-token` | Anti-CSRF bootstrap. Touches no database. |
| `POST /auth/signin`, `/auth/signup`, `/auth/oidc/start`, `/auth/oidc/callback` | Establishing a session. There is no session yet to gate. |
| `POST /auth/signout`, `/auth/signout-all` | Ending a session must never require MFA. |
| `POST /auth/verify-email`, `/auth/resend-verification`, `/auth/forgot-password`, `/auth/reset-password`, `/auth/reset-password/validate` | Proving control of the address and password recovery. Gating these would strand a locked-out user. |
| `POST /auth/mfa/enroll`, `/auth/mfa/confirm` | **How the requirement is satisfied.** Gating these makes the boundary a lockout. |
| `POST /auth/mfa/complete-signin` | The sign-in challenge itself; carries a challenge token, not a session. |
| `POST /auth/mfa/recovery-codes/regenerate` | Requires a valid TOTP code of its own. |
| `POST /auth/session/step-up` | How an enrolled account satisfies MFA on an existing session. |
| `POST /auth/reauthenticate` | Password + TOTP; returns no workspace data. |
| `GET /auth/me` | The caller's own identity — and the MFA state the app renders the gate from. |
| `GET /account/pilot-access` | The caller's own access state, read from their session only. |
| `POST /auth/select-workspace` | Selecting a workspace reads nothing; every data route re-resolves and re-applies the boundary. |
| `POST /pilot-requests`, `GET /pilot-invitations`, `POST /pilot-invitations/signup`, `POST /pilot-invitations/accept` | Approval-only onboarding, all of it before the person can enroll. None returns tenant data. |
| `POST /workspace/invitations/accept` | Joining a workspace grants membership, not data. The next data request is still refused. |

`POST /auth/mfa/disable`, `GET /auth/sessions`, `POST /auth/sessions/revoke` and
`DELETE /auth/delete-account` are deliberately **not** exempt.

---

## 5. What is refused

The other 384 declared routes, including every surface the security brief names:

```
/assets  /targets  /monitoring/*  /detections  /alerts  /incidents
/incidents/{id}/timeline  /history/actions  /exports  /exports/{id}/download
/exports/{id}/archive  /events (audit)  /workspace/api-keys  /api-keys
/integrations/slack  /integrations/webhooks  /integrations/routing
/workspace/settings  /workspace/security-settings  /workspace/access-control
/response/actions  /api/v1/*  /threat-monitoring/*  /dashboard
```

The refusal is stable and machine-readable:

```json
{
  "code": "MFA_ENROLLMENT_REQUIRED",
  "message": "Multi-factor authentication is required for Pilot access. Set up an authenticator before accessing this workspace.",
  "reason": "not_enrolled",
  "plan": "pilot",
  "enforcement": "all_members",
  "mfa_enrollment_required": true,
  "mfa_challenge_required": false
}
```

HTTP **403**, not 401: the caller is authenticated and the request is well
formed. A 401 would make every client drop the session and bounce to sign-in,
which is the wrong remedy for "enrolled, challenge pending".

---

## 6. Behaviour by identity

| Identity | Behaviour |
| --- | --- |
| Pilot **Owner** | Denied without MFA. `all_members` covers every role. |
| Pilot **Admin** | Denied without MFA. |
| Pilot **Analyst** | Denied without MFA. |
| Pilot **Viewer** | Denied without MFA. |
| **Founder / Decoda internal admin** inside a customer workspace | Denied without MFA. Elevated privilege is not a second factor; no role or staff flag is an input to the decision. |
| **Internal admin at organization level** (`/admin/*`) | Unchanged in mechanism — `require_internal_admin` calls `authenticate_with_connection`, so the boundary applies whenever that staff account is itself a member of a Pilot workspace. A staff account with no workspace membership has no tenant to protect and is unaffected. |
| **SCIM directory-sync token** (`/scim/v2/*`) | Never reaches the boundary. `_authenticate_scim` resolves a workspace token, not a user session, so an interactive TOTP challenge is never demanded of a machine. It can create and suspend users; it cannot read monitoring, evidence or audit data. |
| **Workspace API key** (`X-API-Key` on `/api/v1/*`) | Not a machine login today. The middleware adds the key check *on top of* a human bearer session, and the handler still calls `authenticate_with_connection`, so `/api/v1/*` is inside the boundary. No MFA bypass was created for machines. |

---

## 7. Invitation flow, step by step

```
POST /pilot-invitations/signup     account + session created, NO workspace
    ↓  nothing to protect, and nothing readable: every data route needs a workspace
POST /pilot-invitations/accept     Pilot organization + workspace + membership
    ↓  the session now points at a Pilot workspace
GET  /pilot/assets                 403 MFA_ENROLLMENT_REQUIRED
POST /auth/mfa/enroll              allowed (bootstrap)
POST /auth/mfa/confirm             allowed; stamps mfa_verified_at on THIS session
GET  /pilot/assets                 allowed, subject to normal RBAC
```

`mfa_confirm_enrollment` marks the current session MFA-verified, so a person who
just enrolled is not immediately refused for not having challenged.

---

## 8. Existing sessions

A session issued before the requirement existed is refused on its next Pilot
request. Enforcement reads **current** tenant state and the **current** session
row; nothing about MFA is carried in the access token (`create_access_token`
signs `sub`, `exp`, `iat`, `jti`, `sv`, `kv` — no MFA claim exists to replay).

* Password-only session, account not enrolled → `MFA_ENROLLMENT_REQUIRED`.
* Password-only session, account enrolled elsewhere → `MFA_CHALLENGE_REQUIRED`;
  `POST /auth/session/step-up` resolves it without a re-login.

---

## 9. MFA lifecycle

`POST /auth/mfa/disable` is refused **409 `MFA_REQUIRED_BY_WORKSPACE`** while any
workspace the account belongs to requires MFA — a Pilot workspace always does.
Checking every membership (not just the selected workspace) stops a dual-plan
member from switching to the permissive workspace, disabling there, and keeping
the Pilot membership.

Belt and braces: if MFA were cleared anyway, `mfa_disable` bumps
`users.session_version` and revokes every session, and the boundary then answers
`MFA_ENROLLMENT_REQUIRED` on the next request. There is no
`disable MFA → keep using Pilot APIs` path.

### Recovery codes

Verified, not redesigned — no vulnerability was found:

* single-use — selected `WHERE consumed_at IS NULL`, stamped on use;
* hashed with the managed AUTH key (`_auth_token_hash`), never stored in clear;
* returned once at generation and never readable again;
* regenerating deletes every prior code first;
* rate limited (`mfa_complete_signin`, `mfa_recovery_codes`);
* a successful recovery sign-in produces a properly MFA-authenticated session
  (`mfa_verified_at` + `authentication_methods = ["password","recovery_code"]`);
* recovery never disables MFA.

Added by this change: `auth.mfa_recovery_used` (with the remaining-code count),
`auth.mfa_challenge_success` and `auth.mfa_challenge_failed`. The failure event
is committed before the refusal is raised, so brute-force evidence survives the
rejected request. No audit record carries a secret, an OTP or a recovery code.

### Audit vocabulary

```
auth.mfa_enrollment_started        auth.mfa_challenge_success
auth.mfa_enabled                   auth.mfa_challenge_failed
auth.mfa_recovery_used             auth.mfa_recovery_codes_regenerated
auth.mfa_disabled                  auth.session_step_up_verified
```

---

## 10. Frontend

`apps/web/app/mfa-required-gate.tsx`, rendered by `AuthenticatedRoute`. It reads
`user.mfa` from `/auth/me` — a backend fact — and shows:

> **Multi-factor authentication is required for Pilot access.**
> Set up an authenticator before accessing this workspace.

with a link to `/settings/security?return_to=<the page they asked for>`. An
enrolled operator on an unverified session is sent to *verify* instead of
*enroll*, because the remedies differ.

The gate is **not** the control. Every Pilot API refuses such a session whether
or not the browser ever renders it; the screen exists so the operator gets a
route instead of a wall of 403s. `/settings/security` is never gated — it is
where the requirement is satisfied — mirroring the server's bootstrap allowlist.
The return to the requested page is **offered**, not automatic: MFA confirmation
also returns the one-time recovery codes, and navigating away on the operator's
behalf would destroy the only copy they will ever see.

---

## 11. What this boundary does NOT claim

* **It does not cover a route that never authenticates.** The boundary runs when
  a human identity is resolved. An unauthenticated route that returned tenant
  data would be a tenancy bug, not an MFA bug, and is covered by
  [MULTI_TENANT_ISOLATION.md](MULTI_TENANT_ISOLATION.md).
* **It does not apply before migration 0150.** A deployment whose tenancy schema
  is not migrated has no organization plan to read, so the plan floor does not
  participate and the configurable workspace policy decides, exactly as before.
  Production is migrated; a fresh environment must run migrations before the
  Pilot floor takes effect.
* **It does not weaken or replace step-up MFA.** Response-action approval and
  execution still require a recent challenge carried by `totp`/`recovery_code`.
* **It does not touch the Pilot execution boundary** (PR #1472,
  [PILOT_EXECUTION_BOUNDARY.md](PILOT_EXECUTION_BOUNDARY.md)). Recommend-only
  remains a separate, independently enforced control.
* **It is not a session-fixation or token-theft control.** A stolen bearer token
  from a session that already completed MFA is still a valid session; that is
  what session revocation, `session_version`, and the step-up gates address.
* **A dual-plan member is governed by the strictest workspace they belong to.**
  If an account is a member of both a Pilot and a Scale workspace, the session
  must satisfy MFA for either. This is intentional — the SESSION is what can
  reach Pilot data, so the SESSION is what must be MFA-verified — and it is the
  one documented change to non-Pilot behaviour.
* **A non-Pilot workspace that configured `all_members` is now enforced on every
  route**, not only on the ones using `_require_workspace_permission`. That is a
  strengthening of existing, customer-chosen behaviour, applied by the same
  chokepoint.

---

## 12. Route audit

416 routes declared in `services/api/app/main.py`: **32 bootstrap, 384
protected**. A representative slice, in the required form:

| Route | Authentication path | MFA policy path | Workspace scoped | Pre-MFA result |
| --- | --- | --- | --- | --- |
| `GET /assets` | `authenticate_with_connection` | chokepoint | yes | 403 `MFA_ENROLLMENT_REQUIRED` |
| `GET /alerts` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /incidents` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /incidents/{id}/timeline` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /history/actions` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /exports` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /exports/{id}/download` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /exports/{id}/archive` | `_require_workspace_permission` | chokepoint + helper | yes | 403 |
| `GET /events` (audit) | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET`/`POST /workspace/api-keys` | `authenticate_with_connection` / `_require_workspace_admin` | chokepoint (+ helper) | yes | 403 |
| `GET /integrations/slack` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `PUT /integrations/routing/{type}` | `_require_workspace_permission` | chokepoint + helper | yes | 403 |
| `GET /workspace/settings` | `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /api/v1/alerts` | API-key middleware **and** `authenticate_with_connection` | chokepoint | yes | 403 |
| `GET /stream/alerts` (SSE) | `authenticate_request` | chokepoint | yes | 403 JSON, code preserved |
| `POST /response/actions/{id}/approve` | `_require_workspace_permission` + step-up | chokepoint + step-up | yes | 403 |
| `GET /admin/customers` | `require_internal_admin` → `authenticate_with_connection` | chokepoint | no (org level) | 403 if staff member of a Pilot workspace, else allowed |
| `GET /scim/v2/Users` | `_authenticate_scim` (machine token) | not applicable | yes | unaffected — no human session |
| `GET /auth/me` | `authenticate_request` | bootstrap | no | 200, reporting `mfa.satisfied = false` |
| `POST /auth/mfa/enroll` | `authenticate_with_connection` | bootstrap | no | 200 |
| `POST /pilot-invitations/accept` | `authenticate_with_connection` | bootstrap | no | 200 |
| `GET /health` | none | bootstrap | no | 200 |

A test regenerates this classification from `main.py` on every run and fails if a
route escapes it (`test_9_the_route_audit_finds_no_unprotected_pilot_data_route`).

---

## 13. Tests

`services/api/tests/test_pilot_mandatory_mfa.py` (115 cases) and
`apps/web/tests/mandatory-pilot-mfa-gate.spec.ts` (15 cases).

```
python -m pytest services/api/tests/test_pilot_mandatory_mfa.py -q
npx playwright test apps/web/tests/mandatory-pilot-mfa-gate.spec.ts
```
