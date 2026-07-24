"""AI explanation layer for asset risk assessments.

The deterministic severity math (scoring.py) is authoritative. This layer only
turns already-computed structured facts into human narrative. It never invents
reserve or price values, never claims a feed is verified when it is not, and
never recommends fund-moving actions. Every number in the narrative comes from
the structured ``facts`` the service passes in.

The deterministic builder is always available and is the default. An optional
live-provider path reuses the existing AI provider abstraction; any transport or
schema-validation failure falls back to the deterministic summary — the product
never blocks on the model, and a disabled/unavailable provider is truthful about
being deterministic.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA_VERSION = 'asset-risk-summary-v1'

_REQUIRED_KEYS = (
    'executive_summary',
    'risk_drivers',
    'investigation_steps',
    'data_gaps',
    'confidence_explanation',
)

_FINDING_TYPE_LABELS = {
    'asset_reserve_shortfall': 'reserve shortfall',
    'asset_reserve_feed_stale': 'stale reserve feed',
    'asset_reserve_feed_missing': 'missing reserve feed',
    'asset_ledger_mismatch': 'ledger mismatch',
    'asset_price_deviation': 'price deviation from baseline',
    'asset_oracle_disagreement': 'oracle disagreement',
    'asset_monitoring_gap': 'monitoring gap',
    'asset_supply_anomaly': 'supply anomaly',
    'asset_governance_change': 'governance change',
    'asset_over_collateralization': 'unexpected over-collateralization',
    'asset_contract_exposure': 'contract/administrative exposure',
}


def finding_label(finding_type: str) -> str:
    return _FINDING_TYPE_LABELS.get(str(finding_type or '').strip().lower(), str(finding_type or 'finding').replace('_', ' '))


def _clip(text: str, limit: int = 600) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


# --------------------------------------------------------------------------
# Deterministic (authoritative) builder
# --------------------------------------------------------------------------
def build_deterministic_summary(facts: dict[str, Any]) -> dict[str, Any]:
    """Grounded narrative built purely from structured facts. Always valid."""
    name = str(facts.get('asset_name') or 'This asset')
    level = str(facts.get('risk_level') or 'low')
    score = facts.get('risk_score')
    findings = [f for f in (facts.get('findings') or []) if isinstance(f, dict)]
    reserve = facts.get('reserve') or {}
    market = facts.get('market') or {}
    monitoring = facts.get('monitoring') or {}
    data_gaps = [str(g) for g in (facts.get('data_gaps') or []) if str(g).strip()]
    status = str(facts.get('assessment_status') or 'completed')

    # Executive summary.
    if findings:
        active = sorted(findings, key=lambda f: _severity_rank(f.get('severity')), reverse=True)
        top = active[0]
        exec_summary = (
            f'{name} scores {score}/100 ({level} risk). '
            f'{len(active)} active finding(s); the most severe is a '
            f'{str(top.get("severity") or "medium")}-severity {finding_label(top.get("finding_type"))}.'
        )
    else:
        exec_summary = f'{name} scores {score}/100 ({level} risk) with no active findings.'
    if status != 'completed':
        exec_summary += f' Assessment status: {status} — some evidence was unavailable.'

    # Risk drivers — one grounded line per material dimension/finding.
    drivers: list[str] = []
    reserve_status = str(reserve.get('status') or '')
    if reserve_status == 'critical':
        cov = reserve.get('coverage_percent')
        drivers.append(f'Reserve shortfall: verified coverage is {cov}%' if cov is not None else 'Material reserve shortfall detected.')
    elif reserve_status == 'warning':
        drivers.append('Reserve coverage is slightly below the configured minimum.')
    elif reserve_status == 'insufficient_evidence':
        drivers.append('Reserve backing cannot be proven — the feed is missing, unverified, or stale.')
    elif reserve_status == 'over_collateralized':
        drivers.append('Coverage significantly exceeds the expected range (unexpected over-collateralization).')
    market_status = str(market.get('status') or '')
    if market_status in ('high', 'critical', 'medium'):
        dev = market.get('deviation_30d_percent')
        drivers.append(
            f'Market value deviates {dev}% from its 30-day baseline.' if dev is not None
            else 'Market value deviates from its historical baseline.'
        )
    elif market_status == 'no_price_source':
        drivers.append('No price/oracle source is configured for valuation.')
    if str(monitoring.get('health')) in ('critical', 'warning', 'not_configured'):
        missing = monitoring.get('missing_controls') or []
        if missing:
            drivers.append('Monitoring gap: ' + ', '.join(str(m) for m in missing[:4]) + '.')
        else:
            drivers.append('Monitoring coverage is incomplete.')
    for f in findings:
        line = f'{finding_label(f.get("finding_type"))} ({str(f.get("severity") or "medium")}).'
        if line not in drivers:
            drivers.append(line)
    if not drivers:
        drivers.append('No material risk drivers; reserve, market, and monitoring signals are within expected ranges.')

    # Investigation steps — non-destructive, evidence-first.
    steps: list[str] = []
    if reserve_status in ('critical', 'warning', 'insufficient_evidence'):
        steps.append('Re-verify the reserve feed and confirm the latest attested reserve value and timestamp.')
    if market_status in ('high', 'critical', 'medium'):
        steps.append('Compare the primary oracle price against a secondary reference and inspect recent valuation snapshots.')
    if str(monitoring.get('health')) in ('critical', 'warning', 'not_configured'):
        steps.append('Link or repair the monitoring target/provider so live telemetry resumes.')
    if not steps:
        steps.append('No action required; continue scheduled monitoring.')

    # Data gaps.
    if not data_gaps:
        data_gaps = ['None — all required evidence categories were available.']

    confidence = facts.get('confidence')
    confidence_explanation = (
        f'Confidence {confidence} reflects the completeness and freshness of the evidence used. '
        'Lower confidence indicates missing or stale inputs, not lower risk.'
    )

    return {
        'schema_version': SUMMARY_SCHEMA_VERSION,
        'source': 'deterministic',
        'executive_summary': _clip(exec_summary),
        'risk_drivers': [_clip(d, 200) for d in drivers[:8]],
        'investigation_steps': [_clip(s, 200) for s in steps[:6]],
        'data_gaps': [_clip(g, 200) for g in data_gaps[:6]],
        'confidence_explanation': _clip(confidence_explanation, 300),
    }


def _severity_rank(severity: Any) -> int:
    return {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}.get(str(severity or '').strip().lower(), 0)


# --------------------------------------------------------------------------
# Strict schema validation for any AI-produced object
# --------------------------------------------------------------------------
class SummaryValidationError(Exception):
    pass


def validate_summary_schema(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise SummaryValidationError('summary must be an object')
    out: dict[str, Any] = {}
    es = obj.get('executive_summary')
    if not isinstance(es, str) or not es.strip():
        raise SummaryValidationError('executive_summary must be a non-empty string')
    out['executive_summary'] = _clip(es)
    for key in ('risk_drivers', 'investigation_steps', 'data_gaps'):
        value = obj.get(key)
        if not isinstance(value, list):
            raise SummaryValidationError(f'{key} must be a list')
        items = [_clip(str(v), 200) for v in value if str(v).strip()]
        out[key] = items[:8]
    ce = obj.get('confidence_explanation')
    if not isinstance(ce, str) or not ce.strip():
        raise SummaryValidationError('confidence_explanation must be a non-empty string')
    out['confidence_explanation'] = _clip(ce, 300)
    out['schema_version'] = SUMMARY_SCHEMA_VERSION
    return out


# --------------------------------------------------------------------------
# Optional live-provider generation (reuses AI env config; always falls back)
# --------------------------------------------------------------------------
def ai_summary_config() -> dict[str, Any]:
    provider = (os.getenv('AI_PROVIDER', '') or '').strip().lower()
    enabled = str(os.getenv('ASSET_RISK_AI_ENABLED', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}
    has_key = bool((os.getenv('AI_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or '').strip())
    return {
        'enabled': enabled,
        'provider': provider,
        'model': (os.getenv('AI_MODEL_ASSET_RISK', '') or os.getenv('AI_MODEL', '') or '').strip(),
        'has_key': has_key,
        'timeout_seconds': float(os.getenv('AI_REQUEST_TIMEOUT_SECONDS', '30') or 30),
        'max_output_tokens': int(os.getenv('AI_MAX_OUTPUT_TOKENS', '2000') or 2000),
    }


def _build_prompt(facts: dict[str, Any]) -> dict[str, str]:
    # The model receives ONLY structured, already-computed facts and is told not
    # to invent numbers. All figures the narrative may cite are present verbatim.
    system = (
        'You are a risk analyst. Summarize the provided structured asset risk '
        'assessment for an operator. Rules: use ONLY the numbers and statuses '
        'given; never invent reserve or price values; never claim a feed is '
        'verified unless the facts say so; never recommend moving funds; be '
        'concise. Respond with a single JSON object with keys: executive_summary '
        '(string), risk_drivers (string[]), investigation_steps (string[]), '
        'data_gaps (string[]), confidence_explanation (string).'
    )
    user = json.dumps(facts, separators=(',', ':'), default=str)
    return {'system': system, 'user': user, 'evidence_obj': facts, 'prompt_version': SUMMARY_SCHEMA_VERSION}


def generate_summary(facts: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a validated summary dict. Deterministic unless a live provider is
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
        parsed = json.loads(raw.raw_text)
        validated = validate_summary_schema(parsed)
        validated['source'] = 'ai'
        validated['provider'] = getattr(raw, 'provider', cfg['provider'])
        validated['model'] = getattr(raw, 'model', cfg['model'])
        return validated
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never blocks
        logger.info('event=asset_risk_ai_summary_fallback reason=%s', type(exc).__name__)
        deterministic['ai_fallback_reason'] = type(exc).__name__
        return deterministic
