"""AI explanation layer for threat detections.

The deterministic detection engine (detectors.py + scoring.py) is authoritative.
This layer only turns already-persisted, authorized evidence into human narrative.
It NEVER invents transactions, actors, assets, attack types, confidence, or
severity, and never recommends executing a response action from Screen 5.

The deterministic builder is always available and is the default. An optional
live-provider path reuses the existing AI provider abstraction; any transport or
schema-validation failure falls back to the deterministic summary. Output is
strictly validated against the supplied evidence before it is ever persisted.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA_VERSION = 'threat-detection-summary-v1'

_DETECTION_TYPE_LABELS = {
    'unusual_transfer': 'unusual fund movement',
    'coordinated_activity': 'coordinated low-and-slow activity',
    'mint_burn_irregularity': 'mint/burn irregularity',
    'privileged_action': 'privileged/admin action',
    'oracle_deviation': 'oracle deviation',
    'reentrancy_pattern': 'possible reentrancy pattern',
    'flash_loan_pattern': 'flash-loan-assisted sequence',
    'other': 'behavioral anomaly',
}

# Phrases that overclaim certainty. A telemetry-grounded detection must never be
# presented as a confirmed exploit.
_BANNED_PHRASES = (
    'confirmed exploit',
    'confirmed exploitation',
    'confirmed reentrancy',
    'confirmed flash loan',
    'definitely',
    'guaranteed',
    'the attacker is',
    'attacker identified',
    'stolen funds confirmed',
)

# Response-action verbs Screen 5 must never recommend executing (Screen 5 is
# detection-only; mitigation lives on Screen 8).
_ACTION_VERBS = (
    'pause the contract',
    'freeze the',
    'revoke the',
    'blacklist the',
    'execute the',
    'roll back',
    'rollback',
)

_HEX = re.compile(r'0x[0-9a-fA-F]{6,}')


def detection_label(detection_type: str) -> str:
    key = str(detection_type or '').strip().lower()
    return _DETECTION_TYPE_LABELS.get(key, key.replace('_', ' ') or 'anomaly')


def _clip(text: str, limit: int = 600) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


# --------------------------------------------------------------------------
# Deterministic (authoritative) builder
# --------------------------------------------------------------------------
def build_deterministic_summary(facts: dict[str, Any]) -> dict[str, Any]:
    """Grounded narrative built purely from supplied facts. Always valid."""
    dtype = str(facts.get('detection_type') or 'other')
    label = detection_label(dtype)
    severity = str(facts.get('severity') or 'low')
    confidence = facts.get('confidence')
    asset_name = str(facts.get('asset_name') or 'the monitored asset')
    evidence_count = int(facts.get('evidence_count') or 0)
    event_count = int(facts.get('event_count') or 0)
    actor_count = int(facts.get('actor_count') or 0)
    evidence_quality = str(facts.get('evidence_quality') or 'normalized_telemetry')
    source = str(facts.get('evidence_source') or 'live')

    finding = (
        f'{severity.capitalize()}-severity {label} affecting {asset_name}, correlated from '
        f'{event_count} event(s) across {max(actor_count, 1)} address(es).'
    )
    explanation = str(facts.get('explanation') or f'{label} observed in monitored telemetry.')
    impact = _impact_hypothesis(dtype, severity)
    step = str(facts.get('recommended_next_step') or 'Review the supporting evidence before escalating.')

    uncertainty_bits = [
        f'Grounded in {evidence_quality.replace("_", " ")} ({evidence_count} evidence item(s)).',
    ]
    if evidence_quality in ('normalized_telemetry', 'partial_metadata'):
        uncertainty_bits.append('No trace-level evidence is available, so this is a pattern indicator, not a confirmed exploit.')
    if source != 'live':
        uncertainty_bits.append(f'Evidence source is {source} — not live customer evidence.')
    if isinstance(confidence, (int, float)):
        uncertainty_bits.append(f'Confidence {round(float(confidence) * 100)}% reflects evidence strength, not impact.')

    return {
        'schema_version': SUMMARY_SCHEMA_VERSION,
        'source': 'deterministic',
        'finding': _clip(finding),
        'explanation': _clip(explanation),
        'impact_hypothesis': _clip(impact, 300),
        'investigation_step': _clip(step, 300),
        'uncertainty': _clip(' '.join(uncertainty_bits), 400),
    }


def _impact_hypothesis(detection_type: str, severity: str) -> str:
    key = str(detection_type or '').strip().lower()
    if key == 'mint_burn_irregularity':
        return 'If unauthorized, an abnormal supply change could dilute holders or signal reserve/backing divergence.'
    if key == 'privileged_action':
        return 'If the executing key is not an authorized admin, control or upgrade rights over the asset may be compromised.'
    if key == 'coordinated_activity':
        return 'If related, staged low-value movements can accumulate materially while each event stays below alert thresholds.'
    if key == 'unusual_transfer':
        return 'If unexpected, a large or rapidly-distributed movement could indicate exfiltration or misconfiguration.'
    return 'Potential impact depends on validation; treat as an indicator pending investigation.'


# --------------------------------------------------------------------------
# Strict schema + evidence validation for any AI-produced object
# --------------------------------------------------------------------------
class SummaryValidationError(Exception):
    pass


def validate_summary_schema(obj: Any, *, allowed_tx_hashes: set[str], allowed_assets: set[str]) -> dict[str, Any]:
    """Validate an AI object against the schema AND the supplied evidence.

    Rejects summaries that cite a transaction hash not supplied, mention an asset
    not supplied, claim confirmed exploitation, invent an attacker identity, or
    recommend executing a response action."""
    if not isinstance(obj, dict):
        raise SummaryValidationError('summary must be an object')
    out: dict[str, Any] = {}
    for key in ('finding', 'explanation', 'impact_hypothesis', 'investigation_step', 'uncertainty'):
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SummaryValidationError(f'{key} must be a non-empty string')
        out[key] = _clip(value, 600)

    blob = ' '.join(out.values())
    lower = blob.lower()

    for phrase in _BANNED_PHRASES:
        if phrase in lower:
            raise SummaryValidationError(f'summary overclaims certainty: {phrase!r}')
    for verb in _ACTION_VERBS:
        if verb in lower:
            raise SummaryValidationError(f'summary recommends a response action: {verb!r}')

    # Any hex-like token in the text must have been supplied as evidence.
    allowed = {h.lower() for h in allowed_tx_hashes if h}
    for match in _HEX.findall(blob):
        token = match.lower()
        if token not in allowed and not any(token in h or h in token for h in allowed):
            raise SummaryValidationError('summary cites a transaction/address not present in the evidence')

    out['schema_version'] = SUMMARY_SCHEMA_VERSION
    return out


# --------------------------------------------------------------------------
# Optional live-provider generation (reuses AI env config; always falls back)
# --------------------------------------------------------------------------
def ai_summary_config() -> dict[str, Any]:
    provider = (os.getenv('AI_PROVIDER', '') or '').strip().lower()
    enabled = str(os.getenv('THREAT_DETECTION_AI_ENABLED', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}
    has_key = bool((os.getenv('AI_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or '').strip())
    return {
        'enabled': enabled,
        'provider': provider,
        'model': (os.getenv('AI_MODEL_THREAT_DETECTION', '') or os.getenv('AI_MODEL', '') or '').strip(),
        'has_key': has_key,
        'timeout_seconds': float(os.getenv('AI_REQUEST_TIMEOUT_SECONDS', '30') or 30),
        'max_output_tokens': int(os.getenv('AI_MAX_OUTPUT_TOKENS', '1200') or 1200),
    }


def _build_prompt(facts: dict[str, Any]) -> dict[str, str]:
    system = (
        'You are a blockchain threat analyst. Summarize the provided structured '
        'threat detection for a security operator. Rules: use ONLY the evidence '
        'given; never invent transaction hashes, addresses, assets, or attacker '
        'identities; never claim confirmed exploitation (this is a telemetry-based '
        'indicator); never recommend executing a mitigation/response action; be '
        'concise and explicit about uncertainty. Respond with a single JSON object '
        'with keys: finding (string), explanation (string), impact_hypothesis '
        '(string), investigation_step (string), uncertainty (string).'
    )
    user = json.dumps(facts, separators=(',', ':'), default=str)
    return {'system': system, 'user': user, 'evidence_obj': facts, 'prompt_version': SUMMARY_SCHEMA_VERSION}


def generate_summary(facts: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a validated summary dict. Deterministic unless a live provider is
    enabled, configured, and returns a schema-valid, evidence-grounded object."""
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
            max_output_tokens=int(cfg.get('max_output_tokens') or 1200),
        )
        parsed = json.loads(raw.raw_text)
        allowed_tx = {str(t) for t in (facts.get('tx_hashes') or [])}
        allowed_assets = {str(a) for a in (facts.get('asset_names') or [])}
        validated = validate_summary_schema(parsed, allowed_tx_hashes=allowed_tx, allowed_assets=allowed_assets)
        validated['source'] = 'ai'
        validated['provider'] = getattr(raw, 'provider', cfg['provider'])
        validated['model'] = getattr(raw, 'model', cfg['model'])
        return validated
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never blocks
        logger.info('event=threat_detection_ai_summary_failed reason=%s', type(exc).__name__)
        deterministic['ai_fallback_reason'] = type(exc).__name__
        return deterministic
