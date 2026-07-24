"""Environment-driven configuration for the Asset Risk Assessor.

Follows the existing repository conventions (``env_flag`` + ``os.getenv`` with
fail-closed defaults, mirroring ai_triage.triage_config). All knobs are optional;
the deterministic defaults here are the single source of truth so the worker,
the on-demand assessment, and the tests all agree.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any


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
    value = str(os.getenv(name, 'true' if default else 'false')).strip().lower()
    return value in {'1', 'true', 'yes', 'on'}


def assessor_config() -> dict[str, Any]:
    """Resolve Asset Risk Assessor configuration from the environment."""
    interval_seconds = max(30, _env_int('ASSET_RISK_ASSESSOR_INTERVAL_SECONDS', 900))
    return {
        # Background worker: continuously enqueues + assesses due assets. Off by default.
        'enabled': _env_flag('ASSET_RISK_ASSESSOR_ENABLED', default=False),
        # On-demand assessment: a bounded, synchronous assessment runs inside the API
        # process when an operator clicks Run. It only reads stored evidence (no
        # unbounded provider scans) so it is safe even while the background worker is
        # disabled. On by default; set false to force a healthy background worker.
        'on_demand_enabled': _env_flag('ASSET_RISK_ASSESSOR_ON_DEMAND_ENABLED', default=True),
        'interval_seconds': interval_seconds,
        # A worker whose last heartbeat is older than this is not "healthy"; the
        # capability response reports worker_healthy=false. Defaults to two cycles so a
        # single missed beat is tolerated. Never below 120s.
        'worker_heartbeat_stale_seconds': max(120, _env_int('ASSET_RISK_WORKER_HEARTBEAT_STALE_SECONDS', interval_seconds * 2)),
        # A job left queued longer than this with no healthy worker to claim it is
        # reconciled to a terminal state (blocked) instead of pending forever.
        'queued_job_timeout_seconds': max(60, _env_int('ASSET_RISK_QUEUED_JOB_TIMEOUT_SECONDS', 900)),
        # How many assets to assess per worker cycle (bounded so one cycle cannot
        # run unbounded provider work).
        'batch_size': max(1, _env_int('ASSET_RISK_ASSESSOR_BATCH_SIZE', 25)),
        # An assessment older than this is considered stale and is prioritized.
        'assessment_stale_seconds': max(60, _env_int('ASSET_RISK_ASSESSMENT_STALE_SECONDS', 3600)),
        # Rolling baseline window for market-deviation detection.
        'baseline_days': max(1, _env_int('ASSET_RISK_BASELINE_DAYS', 30)),
        'min_baseline_samples': max(2, _env_int('ASSET_RISK_MIN_BASELINE_SAMPLES', 5)),
        # Reserve / price freshness ceilings (seconds).
        'reserve_stale_seconds': max(60, _env_int('ASSET_RESERVE_STALE_SECONDS', 86400)),
        'price_stale_seconds': max(60, _env_int('ASSET_PRICE_STALE_SECONDS', 3600)),
        # Deviation thresholds (percent) and z-score.
        'deviation_medium_percent': _env_decimal('ASSET_PRICE_DEVIATION_MEDIUM_PERCENT', '5'),
        'deviation_high_percent': _env_decimal('ASSET_PRICE_DEVIATION_HIGH_PERCENT', '15'),
        'zscore_high': _env_decimal('ASSET_PRICE_ZSCORE_HIGH', '3'),
        'oracle_disagreement_percent': _env_decimal('ASSET_ORACLE_DISAGREEMENT_PERCENT', '2'),
        # Default per-workspace minimum reserve coverage ratio when an asset does
        # not override it.
        'default_min_coverage_ratio': _env_decimal('ASSET_RESERVE_MIN_COVERAGE_RATIO', '1.0'),
        'over_collateralization_ratio': _env_decimal('ASSET_RESERVE_OVER_COLLATERALIZATION_RATIO', '2.0'),
        # Lease held while a single asset is being assessed (prevents duplicate
        # concurrent assessments across replicas).
        'job_lease_seconds': max(30, _env_int('ASSET_RISK_JOB_LEASE_SECONDS', 300)),
        'max_attempts': max(1, _env_int('ASSET_RISK_JOB_MAX_ATTEMPTS', 3)),
    }


# RWA product taxonomy shown as the registry "Asset Type" column. Reserve
# backing is required for asset types whose value is a claim on off-chain
# reserves; it is optional for asset types with no on-chain liability model.
RWA_ASSET_TYPES: dict[str, dict[str, Any]] = {
    'tokenized_treasury': {'label': 'Tokenized Treasury', 'reserve_required': True},
    'stablecoin': {'label': 'Stablecoin', 'reserve_required': True},
    'money_market_fund': {'label': 'Money Market Fund', 'reserve_required': True},
    'fund_share': {'label': 'Fund Share', 'reserve_required': True},
    'corporate_bond': {'label': 'Corporate Bond', 'reserve_required': True},
    'private_credit': {'label': 'Private Credit', 'reserve_required': True},
    'invoice_financing': {'label': 'Invoice Financing', 'reserve_required': True},
    'commodity': {'label': 'Commodity', 'reserve_required': True},
    'real_estate': {'label': 'Real Estate', 'reserve_required': False},
    'other': {'label': 'Other', 'reserve_required': False},
}

RESERVE_FEED_TYPES = {'none', 'manual', 'attestation', 'proof_of_reserve', 'api'}


def rwa_type_label(value: Any) -> str:
    key = str(value or '').strip().lower()
    entry = RWA_ASSET_TYPES.get(key)
    if entry:
        return str(entry['label'])
    return 'Unclassified' if not key else key.replace('_', ' ').title()


def reserve_required_for(rwa_asset_type: Any, reserve_feed_type: Any = None) -> bool:
    """Whether reserve backing is a *required* control for this asset.

    An explicitly configured reserve feed always makes reserve verification
    required regardless of taxonomy.
    """
    feed = str(reserve_feed_type or '').strip().lower()
    if feed and feed != 'none':
        return True
    key = str(rwa_asset_type or '').strip().lower()
    entry = RWA_ASSET_TYPES.get(key)
    return bool(entry['reserve_required']) if entry else False


def worker_heartbeat_is_fresh(last_heartbeat_at: Any, now: Any, config: dict[str, Any] | None = None) -> bool:
    """Whether a persisted worker heartbeat is fresh enough to call the background
    worker healthy. A missing heartbeat is never fresh (fail-closed)."""
    if last_heartbeat_at is None or now is None:
        return False
    cfg = config or assessor_config()
    try:
        from datetime import timezone
        hb = last_heartbeat_at
        if getattr(hb, 'tzinfo', None) is None:
            hb = hb.replace(tzinfo=timezone.utc)
        return (now - hb).total_seconds() <= int(cfg['worker_heartbeat_stale_seconds'])
    except Exception:
        return False


def execution_mode(config: dict[str, Any], *, worker_healthy: bool) -> str:
    """Canonical execution mode for the Asset Risk Assessor.

    * ``background``  — a healthy background worker is available (on-demand may also be on).
    * ``on_demand``   — no healthy worker, but bounded synchronous assessment is enabled.
    * ``unavailable`` — no execution path (worker unhealthy AND on-demand disabled).
    """
    if config.get('on_demand_enabled'):
        return 'background' if worker_healthy else 'on_demand'
    return 'background' if worker_healthy else 'unavailable'


def blocking_configuration_errors(config: dict[str, Any] | None = None) -> list[str]:
    """Worker startup validation. The assessor needs a database in live mode;
    it does NOT need an AI key (the summary falls back to deterministic text)."""
    from services.api.app import pilot

    cfg = config or assessor_config()
    errors: list[str] = []
    if not cfg['enabled']:
        return errors
    if not pilot.database_url():
        errors.append('DATABASE_URL is required for the Asset Risk Assessor worker.')
    return errors
