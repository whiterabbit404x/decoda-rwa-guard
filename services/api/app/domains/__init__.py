"""
Domain modules extracted from pilot.py for enterprise-scale ownership and SOC 2 review.

Each sub-package owns a distinct bounded context and must NOT import from
services.api.app.main or from other domain packages (to prevent circular imports).
Shared utilities (env_flag, utc_now, etc.) should live in services.api.app.pilot
until a dedicated utils module is created.

Domains:
  rate_limit  — auth rate limiting (Redis/Upstash/memory fallback)
  evidence    — evidence signing, export storage helpers
  billing     — billing provider runtime status
"""
