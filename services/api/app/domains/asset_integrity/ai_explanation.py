"""AI explanation layer for reconciliation results — READ + EXPLAIN ONLY.

The trust boundary this module enforces:

    The deterministic engine (reconciliation.py) computes observed supply,
    expected supply, variance, authorization outcome, reason code, status and
    severity. This module receives those as already-computed structured facts
    and turns them into narrative.

    The AI never calculates supply, never calculates variance, never decides
    whether a transaction was authorized, never sets a status, reason code, or
    severity, and never executes an action. Its output is validated against a
    strict schema and is stored in a field (``ai_summary``) that no decision
    path reads.

The deterministic builder is always available and is the default, so Screen 3
works fully with AI disabled or unreachable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA_VERSION = 'asset-integrity-summary-v1'

_REASON_PHRASES = {
    'SUPPLY_MATCHES_AUTHORITATIVE_STATE': 'the observed on-chain supply matches the authoritative expected supply',
    'MATCHED_AUTHORIZED_ISSUANCE': 'the supply change matches an authorized issuance recorded by the authoritative source',
    'MATCHED_AUTHORIZED_REDEMPTION': 'the supply change matches an authorized redemption recorded by the authoritative source',
    'NO_MATCHING_AUTHORIZED_ISSUANCE': 'no matching authorized issuance was found in the configured authoritative source',
    'NO_MATCHING_AUTHORIZED_REDEMPTION': 'no matching authorized redemption was found in the configured authoritative source',
    'AMOUNT_MISMATCH': 'the closest authorization record was for a different amount',
    'SETTLEMENT_NOT_COMPLETE': 'the matching authorization has not reached a completed settlement state',
    'REFERENCE_MISMATCH': 'the business reference on the on-chain event does not match the authorization record',
    'OUTSIDE_AUTHORIZED_WINDOW': 'the matching authorization falls outside its allowed time window',
    'AUTHORITATIVE_SOURCE_STALE': 'the authoritative source data is older than the configured freshness threshold',
    'AUTHORITATIVE_SOURCE_MISSING': 'no authoritative state is recorded for this asset',
    'AUTHORITATIVE_SOURCE_UNAVAILABLE': 'the authoritative source could not be reached on its last attempt',
    'ONCHAIN_OBSERVATION_MISSING': 'no on-chain supply observation is stored for this asset',
    'ONCHAIN_OBSERVATION_STALE': 'the stored on-chain observation is older than the configured freshness threshold',
    'SUPPLY_RECONCILIATION_NOT_APPLICABLE': 'this asset has no token total supply to reconcile',
}

_RISK_IMPACT_BY_SEVERITY = {'critical': 'Critical', 'high': 'High', 'medium': 'Medium', 'low': 'Low'}


def reason_phrase(reason_code: Any) -> str:
    code = str(reason_code or '').strip().upper()
    return _REASON_PHRASES.get(code, code.replace('_', ' ').lower() or 'no reason code was recorded')


def _clip(text: str, limit: int = 600) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


def _format_units(value: Any) -> str:
    """Format a base-unit integer with thousands separators and an explicit sign."""
    if value is None:
        return 'an unknown number of'
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f'{as_int:+,}' if as_int else '0'


# --------------------------------------------------------------------------
# Deterministic (authoritative) builder — always available, always valid.
# --------------------------------------------------------------------------
def build_deterministic_summary(facts: dict[str, Any]) -> dict[str, Any]:
    """Template narrative grounded entirely in the facts the engine computed."""
    name = str(facts.get('asset_name') or 'This asset')
    status = str(facts.get('status') or 'INSUFFICIENT_EVIDENCE')
    reason_code = str(facts.get('reason_code') or '')
    severity = str(facts.get('severity') or 'low').lower()
    variance = facts.get('variance_units')
    source = str(facts.get('authoritative_source') or 'the authoritative source')
    rule = f"{facts.get('rule_id') or 'rule'} v{facts.get('rule_version') or ''}".strip()
    evidence_count = facts.get('evidence_count')

    if status == 'RECONCILED':
        explanation = (
            f'{name} reconciles: {reason_phrase(reason_code)}. No variance was detected under {rule}.'
        )
    elif status == 'AUTHORIZED_VARIANCE':
        explanation = (
            f'{name} shows a supply change of {_format_units(variance)} units, and {reason_phrase(reason_code)}. '
            f'The change is accounted for, so it is not an integrity anomaly.'
        )
    elif status == 'UNEXPLAINED_VARIANCE':
        explanation = (
            f'The observed on-chain supply differs from the authorized issuance recorded by {source} '
            f'by {_format_units(variance)} units, and {reason_phrase(reason_code)}. '
            f'A blockchain transaction can be cryptographically valid and still be operationally unauthorized.'
        )
    elif status == 'NOT_APPLICABLE':
        # Nothing is missing here, so the data-availability sentence below would
        # be false: it would promise a verdict once evidence arrives, for a
        # dimension that does not exist for this asset.
        explanation = (
            f'Supply reconciliation does not apply to {name}: {reason_phrase(reason_code)}. '
            f'No supply variance can be computed for it, and none is implied — this is not a clean bill of health '
            f'for the asset, only a statement that this particular check does not apply.'
        )
    else:
        explanation = (
            f'Reconciliation could not establish integrity for {name} because {reason_phrase(reason_code)}. '
            f'This is a data-availability state, not evidence of an anomaly — and it is not evidence that the asset is healthy.'
        )

    next_steps: list[str] = []
    if status == 'UNEXPLAINED_VARIANCE':
        next_steps = [
            f'Confirm with {source} whether an authorization exists for this supply change.',
            'Open an investigation so the variance is tracked with its evidence.',
        ]
    elif status in ('STALE_AUTHORITATIVE_DATA', 'SOURCE_UNAVAILABLE', 'MISSING_AUTHORITATIVE_DATA'):
        next_steps = [f'Restore or refresh {source}, then re-run reconciliation.']
    elif status == 'INSUFFICIENT_EVIDENCE':
        next_steps = ['Confirm the asset has a monitoring target collecting on-chain supply observations.']
    elif status == 'NOT_APPLICABLE':
        # Deliberately no "configure a source" step: no configuration gives a
        # wallet address a token total supply.
        next_steps = [
            'Register the token contract on this asset if supply reconciliation is expected to apply to it.',
        ]

    return {
        'explanation': _clip(explanation),
        'risk_impact': _RISK_IMPACT_BY_SEVERITY.get(severity, 'Low'),
        'next_steps': [_clip(s, 200) for s in next_steps][:4],
        'source': 'deterministic',
        'schema_version': SUMMARY_SCHEMA_VERSION,
        'evidence_count': evidence_count,
    }


# --------------------------------------------------------------------------
# Strict schema validation for any AI-produced object
# --------------------------------------------------------------------------
class SummaryValidationError(Exception):
    pass


def validate_summary_schema(obj: Any, *, deterministic: dict[str, Any]) -> dict[str, Any]:
    """Accept only narrative fields from the model.

    ``risk_impact`` is taken from the DETERMINISTIC severity, not from the
    model's output, even when the model supplies one — severity is never an AI
    decision. Same for evidence_count.
    """
    if not isinstance(obj, dict):
        raise SummaryValidationError('summary must be an object')
    explanation = obj.get('explanation')
    if not isinstance(explanation, str) or not explanation.strip():
        raise SummaryValidationError('explanation must be a non-empty string')
    steps = obj.get('next_steps')
    if steps is not None and not isinstance(steps, list):
        raise SummaryValidationError('next_steps must be a list')
    return {
        'explanation': _clip(explanation),
        'risk_impact': deterministic['risk_impact'],
        'next_steps': [_clip(str(s), 200) for s in (steps or []) if str(s).strip()][:4],
        'source': 'ai',
        'schema_version': SUMMARY_SCHEMA_VERSION,
        'evidence_count': deterministic.get('evidence_count'),
    }


# --------------------------------------------------------------------------
# Optional live-provider generation (always falls back)
# --------------------------------------------------------------------------
def ai_summary_config() -> dict[str, Any]:
    provider = (os.getenv('AI_PROVIDER', '') or '').strip().lower()
    enabled = str(os.getenv('ASSET_INTEGRITY_AI_ENABLED', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}
    has_key = bool((os.getenv('AI_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or '').strip())
    return {
        'enabled': enabled,
        'provider': provider,
        'model': (os.getenv('AI_MODEL_ASSET_INTEGRITY', '') or os.getenv('AI_MODEL', '') or '').strip(),
        'has_key': has_key,
        'timeout_seconds': float(os.getenv('AI_REQUEST_TIMEOUT_SECONDS', '30') or 30),
        'max_output_tokens': int(os.getenv('AI_MAX_OUTPUT_TOKENS', '2000') or 2000),
    }


def _build_prompt(facts: dict[str, Any]) -> dict[str, Any]:
    # The model receives ONLY already-computed facts and is explicitly forbidden
    # from producing any of the deterministic outputs.
    system = (
        'You explain a completed, deterministic reconciliation result to an operator. '
        'The status, reason code, variance, and severity have ALREADY been computed by '
        'application code and are given to you. Rules: never compute or restate a '
        'different supply, variance, or severity; never decide whether a transaction was '
        'authorized; never invent settlement records or references; use only the numbers '
        'given; if the status says data was missing or stale, say the result is unknown — '
        'never describe it as safe or as an anomaly. Respond with a single JSON object '
        'with keys: explanation (string), next_steps (string[]).'
    )
    return {
        'system': system,
        'user': json.dumps(facts, separators=(',', ':'), default=str),
        'evidence_obj': facts,
        'prompt_version': SUMMARY_SCHEMA_VERSION,
    }


def generate_summary(facts: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a validated summary. Deterministic unless a live provider is
    enabled, configured, and returns a schema-valid object."""
    deterministic = build_deterministic_summary(facts)
    cfg = config or ai_summary_config()
    if not cfg.get('enabled') or cfg.get('provider') not in {'openai', 'anthropic'} or not cfg.get('has_key') or not cfg.get('model'):
        return deterministic
    try:
        from services.api.app.ai_providers import get_triage_provider

        provider = get_triage_provider(cfg['provider'])
        raw = provider.analyze(
            prompt=_build_prompt(facts),
            model=cfg['model'],
            timeout_seconds=float(cfg.get('timeout_seconds') or 30),
            max_output_tokens=int(cfg.get('max_output_tokens') or 2000),
        )
        validated = validate_summary_schema(json.loads(raw.raw_text), deterministic=deterministic)
        validated['provider'] = getattr(raw, 'provider', cfg['provider'])
        validated['model'] = getattr(raw, 'model', cfg['model'])
        return validated
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never blocks
        logger.info('event=asset_integrity_ai_summary_fallback reason=%s', type(exc).__name__)
        deterministic['ai_fallback_reason'] = type(exc).__name__
        return deterministic
