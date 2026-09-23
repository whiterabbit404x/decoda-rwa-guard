"""Configuration and fixed vocabulary for the External Watchlist.

Single source of truth for the feature flag, the scope/authority constants, the
networks external monitoring may use, the detection profiles, the customer-safe
disclaimers, and the worker's bounded budgets. Kept free of any database or web
framework import so the worker, the API and the tests all read the same values.
"""

from __future__ import annotations

import os
from typing import Any

from services.api.app import evm_activity_provider as evm

# ── feature flag ─────────────────────────────────────────────────────────────
FEATURE_FLAG_ENV = 'EXTERNAL_WATCHLIST_ENABLED'

_TRUTHY = {'1', 'true', 'yes', 'on'}


def feature_enabled() -> bool:
    """Off unless explicitly enabled. Production can keep it off until verified."""
    return str(os.getenv(FEATURE_FLAG_ENV, '') or '').strip().lower() in _TRUTHY


# ── scope and authority ──────────────────────────────────────────────────────
#: Every row, event, finding and evidence package produced here carries this
#: scope. Customer monitoring is the other scope: its rows are keyed by a
#: workspace and never share a table with these.
MONITORING_SCOPE_EXTERNAL_PUBLIC = 'external_public'
MONITORING_SCOPE_WORKSPACE = 'workspace'

#: The only execution authority an external target can ever hold. Enforced by
#: CHECK constraints in migration 0157 and by the read-only RPC gateway.
EXECUTION_AUTHORITY_NONE = 'NONE'

DETECTOR_VERSION = 'external-watch-v1'

# ── customer-safe copy ───────────────────────────────────────────────────────
DISCLAIMER_VERSION = 'external-public-v1'

WATCHLIST_NOTICE = (
    'External Watchlist uses publicly available blockchain data. Monitored organizations '
    'have not necessarily authorized, endorsed, or partnered with Decoda Security.'
)
DETAIL_NOTICE = (
    'Independent public monitoring. This organization has not necessarily authorized or '
    'endorsed Decoda monitoring.'
)
EVIDENCE_DISCLAIMER = (
    'This report was generated from publicly available blockchain telemetry through '
    'independent monitoring by Decoda Security. It does not imply a commercial '
    'relationship, authorization, or endorsement by the monitored organization.'
)
AUTHORIZATION_UNKNOWN_STATEMENT = (
    'Public blockchain telemetry does not establish whether the change was operationally '
    'authorized by the organization.'
)

# ── vocabularies ─────────────────────────────────────────────────────────────
WATCHLIST_STATUSES = ('live', 'backfilling', 'paused', 'degraded', 'error')
TARGET_TYPES = ('contract', 'wallet', 'multisig', 'oracle')
CONTRACT_LIKE_TARGET_TYPES = ('contract', 'oracle')
WALLET_LIKE_TARGET_TYPES = ('wallet', 'multisig')
#: Target types whose address must hold deployed bytecode on the chosen network.
CODE_REQUIRED_TARGET_TYPES = ('contract', 'multisig', 'oracle')

BACKFILL_DAY_OPTIONS = (0, 7, 14, 30)
DEFAULT_BACKFILL_DAYS = 30
BACKFILL_STATUSES = ('pending', 'running', 'completed', 'partial', 'failed')
OPEN_BACKFILL_STATUSES = ('pending', 'running')

FINDING_STATUSES = ('new', 'reviewed', 'expected', 'interesting', 'outreach_candidate', 'dismissed')
FINDING_STATUS_LABELS = {
    'new': 'New',
    'reviewed': 'Reviewed',
    'expected': 'Expected',
    'interesting': 'Interesting',
    'outreach_candidate': 'Outreach Candidate',
    'dismissed': 'Dismissed',
}
FINDING_CLASSES = (
    'review',
    'administrative_change',
    'observed_anomaly',
    'unusual_activity',
    'privileged_configuration_change',
)
FINDING_CLASS_LABELS = {
    'review': 'Review',
    'administrative_change': 'Administrative change',
    'observed_anomaly': 'Observed anomaly',
    'unusual_activity': 'Unusual activity',
    'privileged_configuration_change': 'Privileged configuration change',
}
#: External findings never exceed 'high'. Public telemetry cannot see the
#: organization's change control, so it cannot justify a 'critical' claim.
EXTERNAL_SEVERITIES = ('low', 'medium', 'high')

#: Words an external finding, title, analysis or report must never contain.
#: Public telemetry shows WHAT changed on-chain; it cannot establish intent.
BANNED_LANGUAGE_PATTERN = (
    r'\b(attack\w*|hack\w*|exploit\w*|compromis\w*|malicious\w*|stolen|theft|breach\w*|drain\w*)\b'
)

DETECTION_PROFILES: tuple[dict[str, str], ...] = (
    {'key': 'privileged_role_changes', 'label': 'Privileged role changes'},
    {'key': 'ownership_changes', 'label': 'Ownership changes'},
    {'key': 'contract_upgrades', 'label': 'Contract upgrades'},
    {'key': 'proxy_admin_changes', 'label': 'Proxy admin changes'},
    {'key': 'pause_unpause', 'label': 'Pause / unpause'},
    {'key': 'multisig_configuration', 'label': 'Multisig configuration changes'},
    {'key': 'mint_burn', 'label': 'Mint / burn'},
    {'key': 'large_transfers', 'label': 'Large transfers'},
    {'key': 'oracle_updates', 'label': 'Oracle updates/deviations'},
)
DETECTION_PROFILE_KEYS = tuple(profile['key'] for profile in DETECTION_PROFILES)

# ── networks ─────────────────────────────────────────────────────────────────
#: Networks external monitoring may use: exactly the EVM networks the existing
#: monitoring worker routes (evm_activity_provider.CHAIN_MAP). Adding a network
#: here without a CHAIN_MAP entry is refused at import time below.
SUPPORTED_NETWORKS: dict[str, dict[str, Any]] = {
    'base-mainnet': {
        'label': 'Base Mainnet',
        'short_label': 'Base',
        'chain_id': 8453,
        'explorer_url': 'https://basescan.org',
        'avg_block_seconds': 2.0,
        'confirmations': 10,
    },
    'ethereum-mainnet': {
        'label': 'Ethereum Mainnet',
        'short_label': 'Ethereum',
        'chain_id': 1,
        'explorer_url': 'https://etherscan.io',
        'avg_block_seconds': 12.0,
        'confirmations': 3,
    },
    'arbitrum-one': {
        'label': 'Arbitrum One',
        'short_label': 'Arbitrum',
        'chain_id': 42161,
        'explorer_url': 'https://arbiscan.io',
        'avg_block_seconds': 0.25,
        'confirmations': 40,
    },
}

for _key, _meta in SUPPORTED_NETWORKS.items():
    _routed = (evm.CHAIN_MAP.get(_key) or {}).get('chain_id')
    if _routed != _meta['chain_id']:  # pragma: no cover - guards a future edit
        raise RuntimeError(f'external watchlist network {_key} is not routed by the monitoring worker')


def canonical_network(value: Any) -> str | None:
    """Map any alias the monitoring worker accepts to one canonical key.

    Returns None for a network the worker does not route, so callers refuse it.
    """
    text = str(value or '').strip().lower()
    if not text:
        return None
    if text in SUPPORTED_NETWORKS:
        return text
    chain_id = (evm.CHAIN_MAP.get(text) or {}).get('chain_id')
    if chain_id is None and text.isdigit():
        chain_id = int(text)
    for key, meta in SUPPORTED_NETWORKS.items():
        if meta['chain_id'] == chain_id:
            return key
    return None


def network_meta(network: str) -> dict[str, Any]:
    return dict(SUPPORTED_NETWORKS[network])


def explorer_address_url(network: str, address: str) -> str | None:
    meta = SUPPORTED_NETWORKS.get(network)
    return f"{meta['explorer_url']}/address/{address}" if meta else None


def explorer_tx_url(network: str, tx_hash: str) -> str | None:
    meta = SUPPORTED_NETWORKS.get(network)
    return f"{meta['explorer_url']}/tx/{tx_hash}" if meta else None


def rpc_urls_for_network(network: str) -> dict[str, Any]:
    """Endpoints for READING a public chain. Never returned to a browser.

    A dedicated ``EXTERNAL_WATCHLIST_RPC_URL_<chain_id>`` (comma-separated for
    failover) is preferred so external backfills never draw on the provider
    quota customer monitoring depends on. Without one, the monitoring worker's
    own per-chain routing is reused, which also brings its chain-id fail-closed
    behavior.
    """
    meta = SUPPORTED_NETWORKS.get(network)
    if not meta:
        return {'rpc_urls': [], 'source': None}
    env_name = f"EXTERNAL_WATCHLIST_RPC_URL_{meta['chain_id']}"
    dedicated = [part.strip() for part in str(os.getenv(env_name) or '').split(',') if part.strip()]
    if dedicated:
        return {'rpc_urls': list(dict.fromkeys(dedicated)), 'source': env_name}
    resolved = evm.resolve_chain_rpc(network)
    return {'rpc_urls': list(resolved.get('rpc_urls') or []), 'source': resolved.get('rpc_url_env')}


def rpc_configured(network: str) -> bool:
    return bool(rpc_urls_for_network(network)['rpc_urls'])


# ── detection defaults (per-protocol overridable) ────────────────────────────
#: Relative thresholds. The monitored protocol's own history is the baseline, so
#: "large" means large FOR THIS TOKEN, not above an arbitrary number.
DEFAULT_DETECTION_CONFIG: dict[str, Any] = {
    # A transfer this many times the token's rolling mean transfer is unusual.
    'large_transfer_multiple': 10.0,
    # A mint/burn this many times the token's rolling mean mint/burn is large.
    'mint_burn_multiple': 10.0,
    # No relative finding until the baseline has this many samples.
    'min_baseline_samples': 20,
    # Optional absolute floor in raw token units, only when a founder sets one.
    'large_transfer_min_amount': None,
    # Oracle: a single update that moves the answer by at least this percentage.
    'oracle_deviation_pct': 5.0,
    # Oracle: expected update interval; learned from observed history when unset.
    'oracle_heartbeat_seconds': None,
    # Oracle: an update gap this many times the expected interval is a finding.
    'oracle_stale_multiple': 3.0,
}

_DETECTION_CONFIG_BOUNDS: dict[str, tuple[float, float]] = {
    'large_transfer_multiple': (1.5, 10_000.0),
    'mint_burn_multiple': (1.5, 10_000.0),
    'min_baseline_samples': (3, 100_000),
    'oracle_deviation_pct': (0.01, 100.0),
    'oracle_heartbeat_seconds': (1, 30 * 24 * 3600),
    'oracle_stale_multiple': (1.1, 100.0),
}


class DetectionConfigError(ValueError):
    pass


def normalize_detection_config(raw: Any) -> dict[str, Any]:
    """Validate founder overrides. Unknown keys and out-of-range values refuse."""
    if raw in (None, ''):
        return {}
    if not isinstance(raw, dict):
        raise DetectionConfigError('detection_config must be an object.')
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in DEFAULT_DETECTION_CONFIG:
            raise DetectionConfigError(f'Unknown detection setting: {key}.')
        if value is None:
            cleaned[key] = None
            continue
        if key == 'large_transfer_min_amount':
            text = str(value).strip()
            if not text.isdigit():
                raise DetectionConfigError('large_transfer_min_amount must be a whole number of raw token units.')
            cleaned[key] = text
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise DetectionConfigError(f'{key} must be a number.') from None
        low, high = _DETECTION_CONFIG_BOUNDS[key]
        if not (low <= number <= high):
            raise DetectionConfigError(f'{key} must be between {low} and {high}.')
        cleaned[key] = int(number) if key in ('min_baseline_samples', 'oracle_heartbeat_seconds') else number
    return cleaned


def effective_detection_config(overrides: Any) -> dict[str, Any]:
    merged = dict(DEFAULT_DETECTION_CONFIG)
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in merged and value is not None:
                merged[key] = value
    return merged


# ── worker budgets ───────────────────────────────────────────────────────────
def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def worker_config() -> dict[str, Any]:
    interval = _env_int('EXTERNAL_WATCHLIST_WORKER_INTERVAL_SECONDS', 120, minimum=30, maximum=3600)
    return {
        'enabled': feature_enabled(),
        'interval_seconds': interval,
        # A poll older than this is not "live" (heartbeat/poll are separate facts).
        'poll_stale_seconds': _env_int(
            'EXTERNAL_WATCHLIST_POLL_STALE_SECONDS', max(300, interval * 3), minimum=60, maximum=86_400,
        ),
        'worker_heartbeat_stale_seconds': max(120, interval * 3),
        # Consecutive failed polls before a target is reported as an error.
        'error_failure_threshold': _env_int('EXTERNAL_WATCHLIST_ERROR_FAILURE_THRESHOLD', 5, minimum=1, maximum=100),
        # Provider courtesy: a hard cap on RPC calls per worker cycle, shared by
        # backfill and live polling, plus a pause between eth_getLogs calls.
        'max_rpc_calls_per_cycle': _env_int('EXTERNAL_WATCHLIST_MAX_RPC_CALLS_PER_CYCLE', 600, minimum=10, maximum=100_000),
        'rpc_pacing_seconds': _env_float('EXTERNAL_WATCHLIST_RPC_PACING_MS', 100.0, minimum=0.0, maximum=10_000.0) / 1000.0,
        # Initial eth_getLogs span; halved automatically when a provider refuses it.
        'backfill_chunk_blocks': _env_int('EXTERNAL_WATCHLIST_BACKFILL_CHUNK_BLOCKS', 2000, minimum=10, maximum=100_000),
        'min_chunk_blocks': _env_int('EXTERNAL_WATCHLIST_MIN_CHUNK_BLOCKS', 1, minimum=1, maximum=10_000),
        'chunk_max_retries': _env_int('EXTERNAL_WATCHLIST_CHUNK_MAX_RETRIES', 3, minimum=0, maximum=10),
        'retry_backoff_seconds': _env_float('EXTERNAL_WATCHLIST_RETRY_BACKOFF_SECONDS', 2.0, minimum=0.0, maximum=60.0),
        # Live tail catch-up per target per cycle.
        'max_blocks_per_poll': _env_int('EXTERNAL_WATCHLIST_MAX_BLOCKS_PER_POLL', 5000, minimum=10, maximum=1_000_000),
        'max_targets_per_cycle': _env_int('EXTERNAL_WATCHLIST_MAX_TARGETS_PER_CYCLE', 50, minimum=1, maximum=10_000),
        'max_backfills_per_cycle': _env_int('EXTERNAL_WATCHLIST_MAX_BACKFILLS_PER_CYCLE', 3, minimum=1, maximum=100),
        # Block-timestamp and initiator lookups per chunk (the rest are estimated).
        'max_enrichments_per_chunk': _env_int('EXTERNAL_WATCHLIST_MAX_ENRICHMENTS_PER_CHUNK', 40, minimum=0, maximum=10_000),
        'lease_seconds': _env_int('EXTERNAL_WATCHLIST_LEASE_SECONDS', 600, minimum=60, maximum=86_400),
        'confirmations_override': (
            _env_int('EXTERNAL_WATCHLIST_CONFIRMATIONS', 0, minimum=0, maximum=10_000)
            if str(os.getenv('EXTERNAL_WATCHLIST_CONFIRMATIONS', '') or '').strip() else None
        ),
    }


def confirmations_for(network: str, config: dict[str, Any] | None = None) -> int:
    cfg = config or worker_config()
    override = cfg.get('confirmations_override')
    if override is not None:
        return int(override)
    return int(SUPPORTED_NETWORKS.get(network, {}).get('confirmations', 5))


def validation_rpc_timeout_seconds() -> float:
    """Per-call bound for the founder's create/diagnostic requests (not the worker)."""
    return _env_float('EXTERNAL_WATCHLIST_VALIDATION_RPC_TIMEOUT_SECONDS', 4.0, minimum=0.5, maximum=10.0)


def ai_config() -> dict[str, Any]:
    """Optional live AI provider for the investigation narrative. Off by default;
    the deterministic analysis is always produced and always the fallback."""
    return {
        'enabled': str(os.getenv('EXTERNAL_WATCHLIST_AI_ENABLED', '') or '').strip().lower() in _TRUTHY,
        'provider': (os.getenv('AI_PROVIDER', '') or '').strip().lower(),
        'model': (os.getenv('AI_MODEL_EXTERNAL_WATCHLIST', '') or os.getenv('AI_MODEL', '') or '').strip(),
        'has_key': bool(
            (os.getenv('AI_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or '').strip()
        ),
        'timeout_seconds': _env_float('AI_REQUEST_TIMEOUT_SECONDS', 30.0, minimum=1.0, maximum=120.0),
        'max_output_tokens': _env_int('AI_MAX_OUTPUT_TOKENS', 1200, minimum=100, maximum=8000),
    }
