# Decoda Staff Access — Attribution, Audit, and Customer Visibility

> What is recorded when authorized Decoda personnel read or change information
> about a customer organization, what the customer can see of it, and what this
> document deliberately does **not** claim.

Implemented by `services/api/app/staff_access.py`.
Tested by `services/api/tests/test_staff_access_audit.py` and
`apps/web/tests/staff-access-audit-wording.spec.ts`.

---

## 1. The claim, stated exactly

> Customer-specific access by authorized Decoda personnel is recorded with the
> staff actor, organization/workspace context, operation and timestamp. Relevant
> staff-access events are visible in the customer's audit history.

> Decoda does not provide staff with a customer impersonation or login-as
> mechanism.

Both sentences are verified by the tests listed in §7. What is **not** claimed:

* Not *"every action Decoda staff ever takes is visible to customers."* Three
  staff reads are recorded internally and are **not** mirrored into any
  customer's history, for the reasons in §4: the cross-tenant customer directory,
  the unscoped feedback roadmap view, and the Pilot applicant queue. The
  pre-tenant Pilot application decisions are not mirrored either — there is no
  workspace yet to mirror them into.
* Not *"staff access requires a stated reason."* A reason vocabulary exists, is
  validated, and is recorded when a staff client sends one, but no current
  operation refuses to proceed without it (§6).
* Not *"staff can be prevented from reading customer data."* Internal staff hold
  the founder console by design. What changed is that the access is now on the
  record.

---

## 2. Who counts as Decoda staff

Authorization is server-side and has exactly two sources, neither reachable from
a request:

| Source | Where |
| --- | --- |
| `users.is_internal_admin` | database column, no API write path exists |
| exact-address allowlist | `DECODA_INTERNAL_ADMIN_EMAILS`, wildcards rejected |

`organizations.require_internal_admin()` authenticates first, then checks the
above, and raises `403 INTERNAL_ADMIN_REQUIRED` otherwise. Authentication runs
through `pilot.authenticate_with_connection`, which applies the mandatory Pilot
MFA gate — a staff account is not exempt from it. Being internal staff grants no
plan entitlement, no execution privilege, and **no workspace membership**: staff
still cannot read a tenant's telemetry, detections, alerts, incidents, or
evidence without ordinary membership in that workspace.

A refused attempt increments `decoda_internal_admin_denied_total` and writes a
structured log line. It deliberately does **not** write an audit row: no customer
data was reached, so recording it as an *access* would describe something that
did not happen, and a refusal costs the caller nothing to repeat.

---

## 3. The staff access matrix

| Route | Read/Write | Customer data touched | Audit event | Customer visible |
| --- | --- | --- | --- | --- |
| `GET /admin/customers` | read | every organization: plan, status, usage, primary contact address | `staff.customer_list_viewed` (one event, `result_count=N`) | no — §4 |
| `GET /admin/customers/{id}` | read | one organization: profile, plan, entitlements, usage, workspaces, members, data lifecycle, feedback | `staff.customer_detail_viewed` | **yes** |
| `GET /admin/feedback?organization_id=…` | read | one organization's evaluator feedback | `staff.feedback_viewed` | **yes** |
| `GET /admin/feedback` (unscoped) | read | feedback across tenants (roadmap view) | `staff.feedback_viewed` (one event, `result_count=N`) | no — §4 |
| `GET /admin/pilot-requests` | read | Pilot applications (pre-tenant) | `staff.pilot_request_queue_viewed` | no — §4 |
| `POST /admin/customers/{id}/plan` | write | organization plan | `organization.plan_changed` (internal) + `staff.plan_changed` (mirror) | **yes** |
| `POST /admin/customers/{id}/status` | write | organization status | `organization.status_changed` + `staff.status_changed` | **yes** |
| `POST /admin/customers/{id}/extend-evaluation` | write | Pilot end date | `organization.evaluation_extended` / `…_expiry_set` + `staff.pilot_deadline_changed` | **yes** |
| `POST /admin/pilot-requests/{id}/approve` | write | one application | `pilot_request.approved`, `…invitation_sent` / `…invitation_delivery_failed` | no — pre-tenant |
| `POST /admin/pilot-requests/{id}/reject` | write | one application | `pilot_request.rejected` | no — pre-tenant |
| `POST /admin/pilot-requests/{id}/resend-invitation` | write | one application | same as approve | no — pre-tenant |

`GET /admin/readiness` is **not** a staff route despite its path: it authorizes
`_require_workspace_admin` and reads only the caller's own workspace.

The event vocabulary is closed. `staff_access.record_staff_access()` refuses an
action outside `STAFF_ACTIONS`, so a typo cannot create an unclassified event,
and a test asserts every declared action is actually recorded somewhere — no
event is published for a capability this product does not have.

---

## 4. One chokepoint, two rows

`record_staff_access()` is the only place staff access is recorded. It is called
**after** authorization succeeds, and it writes:

**1. The internal record** — `workspace_id = NULL`, `user_id` = the staff
account, plus the staff source IP and request id. This is the Decoda-internal
answer to who/what/when/why. No customer ever reads it.

**2. The customer mirror** — one row per workspace of the accessed organization,
written with `user_id = NULL` and **no request object**, so no staff identity and
no staff IP enters the customer's chain. Its metadata is assembled inside the
module from named scalars; there is no parameter through which a caller could
pass arbitrary content into it.

Both rows go through `pilot.log_audit`, so both are SHA-256 hash-chained, sealed,
and append-only on the same infrastructure as every other audit row.

**Why a mirror rather than widening the customer's query.** A row belonging to
the `workspace_id IS NULL` chain, rendered inside a workspace view, would break
that workspace's offline chain verification (its `previous_row_hash` points into
a chain the customer cannot see), would escape workspace-scoped retention, and
would drag Decoda-internal metadata into a customer surface. The mirror is a real
member of the workspace's own chain instead, so it verifies, ages out, and reads
like any other event in that workspace.

**Why the cross-tenant reads are not mirrored.** A directory read touches every
tenant. Mirroring it would write one row into every workspace in the estate per
request — an audit-event explosion that buries the customer-specific events that
matter. It is recorded once, with the number of rows returned. Applicant-queue
reads happen before any organization or workspace exists, so there is no customer
history for them to belong to.

**Fail-closed.** If the record cannot be written, the request fails. Customer
information is not served off the record.

---

## 5. What the customer sees

`GET /events` returns, for each row, an `actor_type` derived server-side:

| `actor_type` | Rendered as |
| --- | --- |
| `workspace_member` | Workspace member |
| `decoda_staff` | Decoda staff |
| `automated_service` | Automated service |
| `system` | System |

Only a row written by `staff_access` carries `decoda_staff`; the label is never
inferred in the browser, and an unclassified row carries no badge at all rather
than being dressed up as one. Staff rows also carry `access_mode`
(`Read only` / `Change`) and a plain sentence, for example:

```
Decoda staff viewed organization support details
18 Sep 2026 10:42 UTC
Read only
```

```
Decoda staff changed the Pilot end date
18 Sep 2026 10:45 UTC
Previous: open_ended    New: 2026-09-30T00:00:00+00:00
```

The customer is **not** shown: the staff member's name, address, or user id; the
staff source IP; any internal note; any other tenant's identifiers. An opaque
`staff_access_record_id` and `correlation_id` are included so a customer question
can be tied to the internal record without disclosing anything in it.

The governance change log (`GET /workspace/governance/changes`) applies the same
labelling, so a staff row is never reported there as a platform (`system`) action.

---

## 6. Access reasons

A staff client may declare why:

* `x-decoda-access-reason` — one of `support_case`, `customer_request`,
  `security_investigation`, `billing_account`, `pilot_management`. A value
  outside the vocabulary is refused with `400 INVALID_STAFF_ACCESS_REASON`
  rather than stored as free text. Recorded on **both** rows, so the customer
  sees the stated reason.
* `x-decoda-access-note` — optional, ≤200 characters, refused if it is shaped
  like a credential. Recorded on the **internal row only**: a note usually names
  an internal ticket.

No current operation *requires* a reason. Nothing in the product grants
exceptional access to tenant data, so there is no operation for which a reason
is the control; making one mandatory would only break the console. The
enforcement point exists — `staff_access.REASON_REQUIRED_ACTIONS`, currently
empty — so that any future mechanism granting exceptional tenant access refuses
to be recorded without one. See §8.

---

## 7. No impersonation — how that is verified

There is no impersonation, support-login, break-glass session, or
session-elevation mechanism in this repository, and staff tooling mints no
customer session.

| Check | Where |
| --- | --- |
| Repository-wide search for mechanism shapes (`def impersonate…`, `login_as(`, `/support-login`, `impersonation_token`, `elevate_session`, …) across 1,000+ Python/TS/TSX files | `test_16_no_impersonation_mechanism_exists_anywhere_in_the_repository` |
| Broad keyword search whose only permitted hits are a named allowlist (the export-storage `break_glass` **storage** override, which concerns non-WORM local export storage and not identity, and one prose comment) | `test_16b_the_keyword_search_has_no_unexplained_hits` |
| Every staff endpoint executed with `pilot.create_access_token` patched to raise, and asserting no `auth_sessions` or `users` insert | `test_15_no_staff_endpoint_mints_a_customer_session_or_token` |
| `require_internal_admin` writes nothing — it is a check, not a grant | `test_15b_staff_access_grants_no_workspace_membership` |

---

## 8. Audit integrity

| Property | How it holds |
| --- | --- |
| Append-only | `log_audit` only INSERTs. No API updates or deletes an audit row; the sole `UPDATE audit_logs` in the codebase is the scheduled retention anonymizer in `data_retention.py`. |
| Actor not client-supplied | The actor is the account `require_internal_admin` resolved from the session. No staff handler takes a `user_id`/`actor` parameter, and headers naming another user change nothing. |
| Scope server-derived | Mirror workspaces are read from `workspaces.organization_id` for the one accessed organization. No workspace id is read from a request. |
| Server timestamp | `pilot.utc_now()` at insert, `sealed_at` set with it. |
| Sanitized metadata | Built from named scalars; a forbidden key or a non-scalar value raises rather than being recorded. |
| Customers cannot forge a staff event | No customer-facing endpoint accepts an audit `action` or `metadata`; `decoda_staff` is only ever written by this module. |

---

## 9. Known gaps

1. **No mandatory reason.** §6. Operations proceed without one; the field is
   recorded when sent.
2. **Cross-tenant reads are internal-only.** §4. A customer cannot see that their
   organization appeared in a directory listing, only that their organization was
   opened in detail.
3. **No staff-access review surface.** The internal records are queryable in
   `audit_logs` but no console screen presents them for internal review.
4. **Mirror cap.** An organization with more than 100 workspaces has its mirror
   truncated; the internal record logs a warning and states the count. No current
   plan permits that many workspaces.
5. **Pilot applicant reads are not visible to the applicant.** They pre-date any
   workspace, so there is nowhere to show them.
6. **Direct database access is outside this control.** The out-of-band operator
   scripts (`services/api/scripts/grant_internal_admin.py`,
   `check_user_auth_state.py`) and anything else run against the database by
   someone holding database credentials produce no HTTP session, so there is no
   actor for this chokepoint to attribute. Granting internal admin is
   deliberately one of those — there is no API write path to
   `users.is_internal_admin` at all — which means the grant itself is governed by
   database access control and database-side logging, not by this document.
