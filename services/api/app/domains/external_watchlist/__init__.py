"""External Watchlist domain — founder-only, read-only monitoring of PUBLIC
blockchain infrastructure belonging to RWA protocols that are not (yet) Decoda
customers.

This is NOT a customer workspace. External data lives in its own
``monitoring_scope = 'external_public'`` tables (migration 0157) and every
external target has ``execution_authority = 'NONE'``.

Pipeline (each stage reuses existing Decoda infrastructure):

    External target
      → RPC            rpc.py        read-only gateway over evm_activity_provider's
                                     failover client (per-host backoff, 413 handling)
      → Normalizer     abi.py        event catalog + log decoding
      → Detection      detection.py  threat_detection detectors + scoring
      → Finding        service.py    external_watchlist_findings
      → AI analysis    analysis.py   ai_providers + telemetry_privacy, deterministic fallback
      → Evidence       evidence.py   evidence_signing manifest / HMAC / Ed25519

Modules:
  config      feature flag, scope/authority constants, networks, profiles, copy
  abi         event catalog and log decoding (pure)
  rpc         read-only RPC gateway, chunked adaptive eth_getLogs, block estimation
  detection   rules over decoded events (pure)
  analysis    three-part AI investigation (observed / interpretation / authorization)
  evidence    sealed evidence packages and sanitized prospect reports
  status      monitoring status derived from heartbeat / poll / telemetry facts (pure)
  service     SQL for the external_watchlist* tables only
  ingest      logs → events → rules → findings (shared by backfill and live polling)
  backfill    historical backfill planning and execution (worker only)
  worker      one bounded worker cycle
  conversion  explicit founder "Convert to Pilot Workspace"
  endpoints   request handlers (internal admin + feature flag first)

This package must not import from services.api.app.main.
"""
