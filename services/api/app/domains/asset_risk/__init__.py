"""Asset Risk Assessor domain.

The autonomous, deterministic risk engine behind Screen 3 (Protected Asset
Registry). AI is used only to summarize and explain results — never to compute
severity or invent reserve/price values.

Modules:
  scoring         — pure, Decimal-safe risk math (reserve coverage, market
                    deviation, monitoring coverage, contract exposure, the
                    canonical weighted 0-100 score + confidence). No I/O.
  config          — environment-driven thresholds / worker cadence.
  ai_explanation  — schema-validated AI narrative with a deterministic fallback.
  summary         — canonical risk-summary builder for the registry AI panel.
  service         — DB-backed assessment: gather evidence, persist snapshots,
                    dedup findings -> alerts, resolve cleared conditions.
  registry        — table-ready list enrichment (filter/sort/paginate) and
                    create-time registry field persistence.
  worker          — the run-once assessment cycle used by the worker entrypoint.

This package must not import from services.api.app.main. It may import
services.api.app.pilot for shared DB / auth utilities (matching the existing
domain convention).
"""
