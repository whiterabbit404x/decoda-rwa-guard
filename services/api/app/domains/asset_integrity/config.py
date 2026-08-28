"""Environment-driven configuration for asset integrity reconciliation.

Follows the repository convention (``_env_*`` helpers with fail-closed defaults,
mirroring domains/asset_risk/config.py). The defaults here are the single source
of truth shared by the endpoints, the evaluation path, and the tests.

The rule id/version is stamped onto every persisted snapshot. Bumping
RECONCILIATION_RULE_VERSION changes only how FUTURE snapshots are evaluated;
historical snapshots keep the rule they were produced under and are never
silently recalculated.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from services.api.app.domains.asset_integrity import reconciliation


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (ValueError, TypeError):
        return default


def _env_decimal(name: str, default: str) -> Decimal:
    try:
        return Decimal(str(os.getenv(name, default)).strip())
    except Exception:
        return Decimal(default)


def _env_flag(name: str, default: bool = False) -> bool:
    return str(os.getenv(name, 'true' if default else 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}


def integrity_config() -> dict[str, Any]:
    """Resolve reconciliation configuration from the environment."""
    return {
        # Rule identity stamped onto every snapshot (auditor reproducibility).
        'rule_id': (os.getenv('ASSET_RECONCILIATION_RULE_ID', 'RP-17') or 'RP-17').strip(),
        'rule_version': max(1, _env_int('ASSET_RECONCILIATION_RULE_VERSION', 4)),
        # Authoritative state older than this is STALE_AUTHORITATIVE_DATA — a
        # data-quality state, never an anomaly.
        'authoritative_stale_seconds': max(60, _env_int('ASSET_AUTHORITATIVE_STALE_SECONDS', 3600)),
        # On-chain observation older than this cannot support a verdict.
        'onchain_stale_seconds': max(60, _env_int('ASSET_ONCHAIN_OBSERVATION_STALE_SECONDS', 3600)),
        # Base-unit tolerance treated as no variance (rounding/dust). Default 0:
        # an RWA supply is exact.
        'variance_tolerance_units': _env_decimal('ASSET_RECONCILIATION_VARIANCE_TOLERANCE_UNITS', '0'),
        # How far an authorization may sit from the on-chain event it explains.
        'match_window_seconds': max(60, _env_int('ASSET_RECONCILIATION_MATCH_WINDOW_SECONDS', 86400)),
        # Manual re-reconcile from the Integrity tab. On by default: it only
        # reads stored observations (no unbounded provider scans).
        'on_demand_enabled': _env_flag('ASSET_RECONCILIATION_ON_DEMAND_ENABLED', default=True),
        # How many authorization records the matcher considers per evaluation.
        'authorization_lookback_limit': max(1, _env_int('ASSET_RECONCILIATION_AUTHORIZATION_LIMIT', 50)),
        # History page size ceiling for the Integrity > History tab.
        'history_limit': max(1, min(200, _env_int('ASSET_RECONCILIATION_HISTORY_LIMIT', 25))),
    }


def rules_from_config(config: dict[str, Any] | None = None) -> reconciliation.ReconciliationRules:
    """Build the pure engine's rule object from resolved configuration."""
    cfg = config or integrity_config()
    return reconciliation.ReconciliationRules(
        rule_id=str(cfg['rule_id']),
        rule_version=int(cfg['rule_version']),
        authoritative_stale_seconds=int(cfg['authoritative_stale_seconds']),
        onchain_stale_seconds=int(cfg['onchain_stale_seconds']),
        variance_tolerance_units=Decimal(str(cfg['variance_tolerance_units'])),
        match_window_seconds=int(cfg['match_window_seconds']),
    )
