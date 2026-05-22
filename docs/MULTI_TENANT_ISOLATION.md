# Multi-Tenant Isolation

> This document describes the tenant isolation model, object-level authorization
> rules, and the test suite for Decoda RWA Guard.
>
> **Important:** Improving multi-tenant isolation increases production readiness
> for a controlled pilot.  It does **not** by itself make the product
> broad paid SaaS ready.  Broad paid SaaS readiness also requires billing,
> email, live provider configuration, and live evidence — all of which are
> separately gated by `build_paid_launch_readiness()`.

---

## 1. Tenant Isolation Model

The **workspace** is the isolation boundary.  Every customer operates within
one or more workspaces.  All data objects carry a `workspace_id` foreign key
that ties them to exactly one workspace.

Users access workspace data only after:
1. Presenting a valid Bearer token (JWT).
2. The server resolving the workspace from the `X-Workspace-Id` header or the
   user's `current_workspace_id`.
3. The server confirming the user has an active `workspace_members` row for
   that workspace (`_ensure_membership`).

---

## 2. Core Objects and Their Isolation

Every one of the following objects is scoped to a workspace and must never
be accessible to a user from a different workspace:

| Object | Table | Isolation column |
|---|---|---|
| Workspace | `workspaces` | `id` (is the scope) |
| User membership / role | `workspace_members` | `workspace_id` |
| Protected asset | `assets` | `workspace_id` |
| Monitored target | `targets` | `workspace_id` |
| Monitored system | `monitored_systems` | `workspace_id` |
| Monitoring config | `monitored_systems` | `workspace_id` |
| Telemetry event | `telemetry_events` | `workspace_id` |
| Detection | `detections` | `workspace_id` |
| Alert | `alerts` | `workspace_id` |
| Incident | `incidents` | `workspace_id` |
| Response action | `response_actions` | `workspace_id` |
| Evidence | `evidence` | `workspace_id` |
| Export job | `export_jobs` | `workspace_id` |
| Audit log | `audit_logs` | `workspace_id` |
| Action history | `action_history` | `workspace_id` |
| API key | `api_keys` | `workspace_id` |
| Auth session | `auth_sessions` | `workspace_id` |

---

## 3. Object-Level Authorization Rules

### Rule 1 — Every ID lookup must include workspace scope

```sql
-- Correct
SELECT * FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL

-- Never do this
SELECT * FROM assets WHERE id = %s
```

Use `require_object_in_workspace()` from `services/api/app/tenant_isolation.py`
when building new endpoints.

### Rule 2 — Fetch before mutate, with workspace scope

Every mutation endpoint must verify workspace ownership **before** issuing any
`UPDATE`, `DELETE`, or `INSERT` that depends on the object.  The read and
mutate steps must use the same workspace_id from the authenticated session.

### Rule 3 — Export and response-action isolation

Proof bundle export and response-action creation must verify that every
referenced object (incident, alert, asset) belongs to the same workspace as
the authenticated session.  Cross-workspace reference in a proof bundle payload
or action payload must be rejected with HTTP 404.

### Rule 4 — List endpoints are workspace-scoped

All `LIST` queries (`SELECT … FROM table WHERE workspace_id = %s`) use
the session-derived workspace_id.  Query-parameter `workspace_id` fields are
not used to override the authenticated context.

### Rule 5 — Runtime and readiness are workspace-scoped

`build_workspace_monitoring_summary_fallback()` and related helpers take
explicit per-workspace counters.  They must never aggregate data across
workspaces.

---

## 4. Safe 404 vs 403 Behavior

| Situation | HTTP status | Reason |
|---|---|---|
| Object ID belongs to another workspace | **404** | Avoids disclosing object existence in another workspace |
| Object exists but role is insufficient | **403** | Object is visible; the role blocks the action |
| Request body workspace_id differs from session | **403** | Explicit override attempt detected |
| Workspace membership missing | **403** | User does not belong to that workspace |

The safe 404 policy means a cross-workspace ID guess is indistinguishable from
a non-existent object.  No workspace ID, object name, or metadata from another
workspace is disclosed in error responses.

---

## 5. Body / Query / Header Workspace Override Rule

The authorized `workspace_id` is **always** derived from the authenticated
session:

```
Bearer token → user_id → workspace_members row → workspace_id
              optionally filtered by X-Workspace-Id header
```

Request body `workspace_id` fields and query-string `workspace_id` parameters
**must not** override the session-derived workspace context.

- **Headers**: `X-Workspace-Id` is accepted but must still be validated against
  the user's workspace membership.  A user cannot claim a workspace they are
  not a member of by supplying a different header value.
- **Body**: Use `reject_body_workspace_override(body_workspace_id, authorized_workspace_id)`
  at mutation endpoints that accept a `workspace_id` field.
- **Query params**: List endpoints do not accept a `workspace_id` query
  parameter to override the session scope.

---

## 6. Export and Response-Action Isolation

### Proof bundle export

`_generate_export_artifact(connection, workspace_id, export_id)` looks up the
incident referenced in the export job using `WHERE workspace_id = %s AND id = %s`.
If the incident doesn't exist in the requesting workspace, the export fails with
an exception (which the caller records as a failed export job status).

### Response-action creation

`create_enforcement_action()` looks up `incident_id` with
`WHERE id = %s AND workspace_id = %s`.  If the incident belongs to another
workspace, HTTP 404 is returned before any INSERT is attempted.

### Action execution

`execute_enforcement_action()` looks up `response_actions` with
`WHERE id = %s AND workspace_id = %s`.  Cross-workspace action IDs are never
found and return HTTP 404.

---

## 7. Canonical Helpers (`services/api/app/tenant_isolation.py`)

```python
from services.api.app.tenant_isolation import (
    require_object_in_workspace,
    assert_same_workspace,
    reject_body_workspace_override,
    safe_not_found,
)

# Fetch by ID with workspace scope — raises 404 if not found
row = require_object_in_workspace(
    connection,
    table='alerts',
    object_id=alert_id,
    workspace_id=workspace_context['workspace_id'],
)

# Assert an already-fetched row belongs to the right workspace — raises 404
assert_same_workspace(row['workspace_id'], workspace_context['workspace_id'])

# Reject body-level workspace override — raises 403
reject_body_workspace_override(payload.get('workspace_id'), workspace_context['workspace_id'])

# Get a safe 404 to raise
raise safe_not_found('Alert not found.')
```

---

## 8. Endpoint Families Covered

The following endpoint families enforce workspace-scoped object-level
authorization at both the read and write level:

- **Assets**: `get_asset`, `list_assets`, `create_asset`, `update_asset`, `delete_asset`
- **Targets / monitoring**: `get_target`, `list_targets`, `update_target`, `delete_target`, `set_target_enabled`
- **Monitored systems**: `create_monitored_system`, `patch_monitored_system`, `delete_monitored_system`
- **Detections**: `get_detection`, `get_detection_evidence`, `list_detections`
- **Alerts**: `get_alert`, `list_alerts`, `patch_alert`
- **Incidents**: `list_incidents`, `patch_incident`
- **Response actions**: `create_enforcement_action`, `execute_enforcement_action`
- **Export / proof bundle**: `get_export`, `get_export_artifact_content`, `list_exports`, `_generate_export_artifact`
- **Members / invitations**: `list_workspace_members`, `create_workspace_invitation`
- **Audit log**: `log_audit` always records the session workspace_id

---

## 9. Testing Commands

```bash
cd /home/user/decoda-rwa-guard

# Session 14 isolation tests (32 tests, cases A–X)
python -m pytest services/api/tests/test_multi_tenant_isolation.py -q

# Ensure prior sessions still pass
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

---

## 10. Known Non-Goals

- **Formal penetration testing**: This document describes the isolation model
  and automated test coverage.  It is not a substitute for a penetration test
  or a formal OWASP ASVS audit.
- **Rate limiting and DDoS protection**: Out of scope for this module.
- **Row-level encryption**: Workspace data is logically isolated by `workspace_id`
  but is not encrypted at row level.  Secret values (API keys, tokens) use
  `secret_crypto.py` for at-rest encryption.
- **Cross-workspace admin queries**: Ops-internal admin routes may aggregate
  across workspaces for platform health monitoring.  These are not customer-
  facing and must be protected by an ops-role guard (`require_ops_rbac_guard`).

---

## 11. Broad Paid SaaS Readiness Disclaimer

Improving multi-tenant isolation from 75% to ~88% demonstrates that the
product enforces workspace-level authorization across all core SaaS objects and
includes cross-workspace negative tests.

This improvement does **not** make the product broad paid SaaS ready.
Broad paid SaaS readiness additionally requires:

- Billing provider configured (`STRIPE_SECRET_KEY` / Paddle equivalent)
- Email provider configured (`SENDGRID_API_KEY` / `RESEND_API_KEY` / SMTP)
- Live EVM provider configured (non-placeholder `EVM_RPC_URL`)
- Live evidence present (not simulator-only)
- All four gates in `build_paid_launch_readiness()` passing
- CI/release proof artifacts generated and validated

Run `build_paid_launch_readiness()` to see current blockers.
