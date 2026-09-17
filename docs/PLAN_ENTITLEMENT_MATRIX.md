<!-- GENERATED FILE — do not edit by hand.
     Source: services/api/app/entitlements.py :: capability_matrix()
     Regenerate: python -m scripts.render_plan_matrix --write -->

# Plan entitlement matrix

The single authoritative statement of what each plan can do. Every cell below is
COMPUTED from `services/api/app/entitlements.py` — the same table the API,
the workers, and `GET /account/plan` read — so this document cannot promise a
capability the engine withholds, or withhold one the engine grants.

Two facts decide every row: the **plan** (Pilot / Scale / Enterprise) and the
**lifecycle state** (is the evaluation still running, is the tenant suspended).
That is why `ACTIVE PILOT` and `EXPIRED PILOT` are separate columns rather than
one "Pilot" column: an active evaluation is meant to exercise the production
security workflows, and an expired one keeps all of its data while losing the
ability to start new expensive work.

| Capability                     | ACTIVE PILOT | EXPIRED PILOT | SCALE     | ENTERPRISE | Decided by                                 |
| ------------------------------ | ------------ | ------------- | --------- | ---------- | ------------------------------------------ |
| Core dashboard                 | YES          | YES           | YES       | YES        | always available, expired tenants included |
| Monitoring                     | YES          | NO            | YES       | YES        | lifecycle (not plan-gated)                 |
| Threat detection               | YES          | NO            | YES       | YES        | feature entitlement                        |
| Alerts                         | YES          | YES           | YES       | YES        | always available, expired tenants included |
| Incidents                      | YES          | YES           | YES       | YES        | always available, expired tenants included |
| AI investigation               | YES          | NO            | YES       | YES        | feature entitlement                        |
| Incident playbooks             | YES          | NO            | YES       | YES        | feature entitlement                        |
| Response recommendations       | YES          | NO            | YES       | YES        | feature entitlement                        |
| Evidence workflows             | YES          | NO            | YES       | YES        | lifecycle (not plan-gated)                 |
| Audit-ready exports            | YES          | NO            | YES       | YES        | feature entitlement                        |
| Workspaces                     | 1            | 1             | 3         | CUSTOM     | plan limit                                 |
| Contracts                      | 5            | 5             | 25        | CUSTOM     | plan limit                                 |
| Evidence packages              | 10           | 10            | UNLIMITED | CUSTOM     | plan limit                                 |
| Automatic production execution | NO           | NO            | NO        | NO         | feature entitlement                        |
| Multi-network                  | NO           | NO            | NO        | YES        | feature entitlement — NOT ENFORCED         |
| Custom evidence templates      | NO           | NO            | NO        | YES        | feature entitlement — NOT ENFORCED         |
| Custom integrations            | NO           | NO            | NO        | YES        | feature entitlement — NOT ENFORCED         |
| Priority alert routing         | NO           | NO            | YES       | YES        | feature entitlement — NOT ENFORCED         |
| Custom SLA                     | NO           | NO            | NO        | YES        | commercial agreement, no software control  |

## How to read the columns

* **ACTIVE PILOT** — a complimentary, approval-only evaluation. Bounded by
  1 workspace, 5 monitored contracts, 10 evidence packages, and recommend-only
  execution. Not bounded by a fixed duration: see "Pilot duration" below.
* **EXPIRED PILOT** — `evaluation_expires_at` has passed. Nothing is deleted and
  every read path still works; new monitoring, investigations, evidence packages,
  integrations and executions are refused with `PLAN_EVALUATION_EXPIRED`.
* **SCALE** — the ongoing paid production plan.
* **ENTERPRISE** — custom. Limits and features are widened per agreement through
  audited `entitlement_overrides` rows, never by being Enterprise alone.

A suspended organization behaves like an expired Pilot for every row decided by
lifecycle, regardless of plan.

## Automatic production execution

`NO` on every plan by default, including Enterprise. The product principle is
that AI recommends, a deterministic policy engine decides, and a human
authorizes. An Enterprise tenant that has signed off on autonomous execution
receives it through an explicit, audited `entitlement_overrides` entry.

## Declared but NOT enforced

These feature keys exist in the plan table and are reported by
`GET /account/plan`, but no code path reads them yet. They describe a commercial
intention, not a control, and must not be presented to a customer as a capability
their plan grants or withholds:

* `custom_evidence_templates`
* `custom_integrations`
* `multi_network`
* `priority_routing`

## Pilot duration

`evaluation_expires_at` is OPTIONAL, and a Pilot is one of two shapes:

* **`NULL` — open-ended.** The default for a newly approved Pilot, and what the
  grandfathered pre-invitation Pilots already were. The evaluation stays active
  until an authorized founder/admin ends it (`status = 'expired'`) or suspends
  the organization. A missing deadline is not evidence of one, and inventing it
  would revoke access the tenant was never told about. This is not "free
  forever": Pilot access is complimentary and approval-only, and Decoda ends it.
* **A timestamp — dated.** A founder set or extended a deadline by hand, or the
  deployment set `PILOT_EVALUATION_DAYS` so every new Pilot is stamped with one.
  `PLAN_EVALUATION_EXPIRED` applies once it passes, exactly as before.

Both shapes are enforced by the same `evaluation_expired` / `lifecycle_state`
path, so nothing downstream carries its own reading of a null deadline.
