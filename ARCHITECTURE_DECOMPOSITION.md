# Architecture Decomposition Plan

## Status: In Progress (P0-1 first pass)

## Problem

`services/api/app/pilot.py` was 15,169 lines and contained auth, billing, migrations, monitoring, workspace management, RBAC, webhooks, Slack, evidence, and export logic in a single file. This makes SOC 2/security review, incident response, ownership, and safe change management impossible at enterprise scale.

## Target Structure

```
services/api/app/
├── pilot.py                          # Compatibility/glue layer; shrinks each pass
├── domains/
│   ├── __init__.py                   # Module boundary docs
│   ├── rate_limit/
│   │   └── __init__.py               # EXTRACTED (pass 1) ✓
│   ├── evidence/
│   │   └── __init__.py               # Planned (pass 2)
│   ├── billing/
│   │   └── __init__.py               # Planned (pass 2)
│   ├── workspaces/
│   │   └── __init__.py               # Planned (pass 3)
│   ├── auth/
│   │   └── __init__.py               # Planned (pass 3)
│   ├── monitoring/
│   │   └── __init__.py               # Planned (pass 4)
│   ├── integrations/
│   │   └── __init__.py               # Planned (pass 4)
│   ├── onboarding/
│   │   └── __init__.py               # Planned (pass 4)
│   └── response_actions/
│       └── __init__.py               # Planned (pass 5)
├── evidence_signing.py               # Standalone (hardened P2-6) ✓
└── export_storage.py                 # Standalone (hardened P2-5) ✓
```

## Completed Extractions (Pass 1)

### `domains/rate_limit/` ✓

**Owns:**
- Redis/Upstash distributed rate limiter client (`_redis_rate_limiter`)
- In-memory fallback per-process rate limiter state
- `enforce_auth_rate_limit()` — public auth endpoint guard
- Fallback warning emission logic

**Must NOT import:**
- `services.api.app.main` (circular)
- `services.api.app.pilot` (circular — pilot imports this module)
- Other domain packages

**Backward compat:**  
`pilot.py` keeps thin wrapper functions (`def enforce_auth_rate_limit(...)`) that delegate to the domain module. Tests that monkeypatch rate limit state must target `services.api.app.domains.rate_limit` directly.

**Config env vars:**
- `REDIS_URL` — primary Redis backend
- `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` — Upstash HTTP Redis
- `ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION` — break-glass override (enterprise_ready=false)

## Module Boundary Rules

1. Domain modules must not import from `main.py` or from each other (prevents circular imports).
2. Shared utilities go in `pilot.py` until a dedicated `utils.py` is created.
3. Each domain module must have a docstring explaining what it owns and what it must not import.
4. Database connections are passed in from `pilot.py`; domain modules do not own DB connection lifecycle.
5. HTTP request/response types (FastAPI) are allowed in domain modules.
6. Secrets and env var access is allowed in domain modules (they own their own config).

## pilot.py Shrinkage Progress

| Pass | Lines Removed | Total Lines | Notes |
|------|---------------|-------------|-------|
| Before | — | 15,169 | Baseline |
| Pass 1 | ~90 | ~15,090 | Rate limiting extracted (wrappers kept) |
| Pass 2 (planned) | ~800 | ~14,290 | Evidence + billing helpers |
| Pass 3 (planned) | ~2,000 | ~12,290 | Workspace/RBAC + auth helpers |
| Pass 4 (planned) | ~3,000 | ~9,290 | Monitoring + integrations |
| Pass 5 (planned) | ~2,000 | ~7,290 | Onboarding + response actions |

**Target: <10,000 lines by end of pass 3.**

## Ownership Map

| Domain | Owner Team | Criticality |
|--------|-----------|-------------|
| auth/session/JWT/CSRF/MFA | Security | P0 |
| rate_limit | Security | P0 |
| evidence/signing/export | Compliance | P0 |
| workspaces/RBAC | Platform | P1 |
| billing/webhooks | Billing | P1 |
| monitoring/alerts/incidents | Core | P1 |
| integrations/Slack/SIEM | Integrations | P2 |
| onboarding | Product | P2 |
| response_actions | Core | P2 |

## Import Rules (Anti-Circular)

```
domains/rate_limit   → stdlib, fastapi only
domains/evidence     → stdlib, fastapi, evidence_signing, export_storage only
domains/billing      → stdlib, fastapi only
domains/workspaces   → stdlib, fastapi, domains/billing only
domains/auth         → stdlib, fastapi, domains/rate_limit only
domains/monitoring   → stdlib, fastapi only
pilot.py             → all domains (imports and re-exports)
main.py              → pilot.py, domains/* (via pilot re-exports)
```

## Next Steps

1. Extract `billing_runtime_status()` and related Stripe/Paddle helpers → `domains/billing/`
2. Extract evidence signing helpers + export job helpers → `domains/evidence/`
3. Add architecture import tests that verify no circular imports
4. Add CODEOWNERS file when team structure is defined
