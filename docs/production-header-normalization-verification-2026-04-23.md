# Production header-normalization verification report — 2026-04-23

Environment: Codex sandbox (`/workspace/decoda-rwa-guard`, UTC date 2026-04-23).

## Request
1. Confirm production API SHA includes header-normalization patch.
2. Inspect one failing browser request (`/ops/monitoring/runtime-status`, `/alerts`, `/incidents`) and validate `X-Workspace-Id` is one UUID (not comma-separated).
3. Re-check Railway logs for zero occurrences of:
   - `invalid input syntax for type uuid`
   - duplicated workspace id value with comma.
4. If still present, rollback or hotfix immediately.

## What was verifiable in this environment

### Code-level patch presence (repository HEAD)
- `services/api/app/pilot.py` includes `normalize_workspace_header_value()`, which:
  - takes the first non-empty token from a comma-separated header,
  - validates UUID format,
  - raises a 400 for malformed values.
- `services/api/tests/test_workspace_header_normalization.py` includes tests for:
  - single UUID acceptance,
  - comma-separated header normalization to first UUID,
  - malformed header rejection.

### Runtime/deployment verification attempts (blocked)

#### 1) Production endpoint probe attempt
Command attempted:

```bash
API_URL="https://decoda-rwa-guard-api-production.up.railway.app"
curl -sS -o /tmp/resp_single.json -w "HTTP %{http_code}\n" \
  "$API_URL/ops/monitoring/runtime-status" \
  -H "X-Workspace-Id: 11111111-1111-1111-1111-111111111111" \
  -H "Authorization: Bearer invalid"
```

Observed result:
- `curl: (56) CONNECT tunnel failed, response 403`
- No external connectivity to the Railway URL from this sandbox.

#### 2) Railway CLI check
Command attempted:

```bash
railway --version
```

Observed result:
- `railway: command not found`.
- Cannot inspect deployment SHA or logs via CLI in this environment.

#### 3) Browser DevTools inspection
- Not possible in this environment (no browser_container tooling available in this run).

## Required operator runbook (execute in production-capable environment)

1. Confirm deployed API SHA:

```bash
curl -sS https://decoda-rwa-guard-api-production.up.railway.app/health | jq -r '.backend_git_commit'
```

2. Compare to commit introducing header normalization:

```bash
git log --oneline -- services/api/app/pilot.py services/api/tests/test_workspace_header_normalization.py
```

3. DevTools check (Network tab):
- Open one failing request to `/ops/monitoring/runtime-status`, `/alerts`, or `/incidents`.
- Verify request header `X-Workspace-Id` is exactly one UUID and not CSV.

4. Railway log scan:

```bash
railway logs --service decoda-rwa-guard-api | rg -n "invalid input syntax for type uuid|x-workspace-id|workspace.*,.+"
```

5. Decision:
- If any uuid syntax/comma duplication errors persist, deploy hotfix immediately (enforce single UUID header at edge/auth proxy) or rollback to last known good SHA.

## Status summary
- **Patch exists in repository HEAD:** yes.
- **Production SHA includes patch:** not verifiable from this sandbox.
- **DevTools header shape check:** not verifiable from this sandbox.
- **Railway log zero-error check:** not verifiable from this sandbox.
- **Rollback/hotfix execution:** blocked here (no deployment control in this environment).
