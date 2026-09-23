"""AI investigation for external findings: three statements, kept apart.

External monitoring sees a public chain and nothing else. So every analysis is
exactly three separately labelled statements, and they never blur together:

  observed_fact              what the chain recorded — event, contract, block,
                             transaction, decoded values. Nothing inferred.
  decoda_interpretation      what that change means structurally, in careful
                             language ("privileged configuration change",
                             "administrative change", "observed anomaly").
  operational_authorization  what public telemetry CANNOT establish: whether
                             the organization authorized the change.

The deterministic builder is authoritative and always available. An optional
live model (EXTERNAL_WATCHLIST_AI_ENABLED + the existing AI provider settings)
may rephrase, but its output is accepted only if it keeps all three sections,
cites no transaction or address absent from the facts, uses none of the banned
accusatory words, and still states that authorization is unknown. Anything else
falls back to the deterministic analysis.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from services.api.app import telemetry_privacy
from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.external_watchlist import detection

logger = logging.getLogger(__name__)

ANALYSIS_SCHEMA_VERSION = 'external-watchlist-analysis-v1'
_SECTIONS = ('observed_fact', 'decoda_interpretation', 'operational_authorization')
_HEX = re.compile(r'0x[0-9a-fA-F]{6,}')


def _clip(text: Any, limit: int = 900) -> str:
    value = ' '.join(str(text or '').split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + '…'


def _describe_decoded(decoded: dict[str, Any]) -> str:
    parts = []
    for key, value in decoded.items():
        if value is None or isinstance(value, (list, dict)):
            continue
        parts.append(f'{key.replace("_", " ")} = {value}')
    return '; '.join(parts[:8])


def build_facts(
    *, draft: detection.FindingDraft, watchlist: dict[str, Any], target: dict[str, Any],
) -> dict[str, Any]:
    """The only material an analysis may draw on."""
    network = str(target.get('network') or '')
    meta = ewc.SUPPORTED_NETWORKS.get(network, {})
    return {
        'protocol': watchlist.get('name'),
        'network': meta.get('label') or network,
        'target_type': target.get('target_type'),
        'monitored_address': target.get('address'),
        'contract_address': draft.contract_address,
        'event_name': draft.event.event_name if draft.event else None,
        'tx_hash': draft.tx_hash,
        'block_number': draft.block_number,
        'observed_at': draft.observed_at.isoformat() if draft.observed_at else None,
        'initiator': draft.initiator,
        'decoded': draft.decoded,
        'title': draft.title,
        'finding_class': ewc.FINDING_CLASS_LABELS.get(draft.finding_class, draft.finding_class),
        'explanation': draft.explanation,
        'interpretation': draft.interpretation,
        'previous_state': draft.previous_state,
        'new_state': draft.new_state,
    }


def build_deterministic_analysis(facts: dict[str, Any]) -> dict[str, Any]:
    event_name = facts.get('event_name')
    if event_name and facts.get('tx_hash'):
        location = (
            f'{event_name} was emitted by {facts.get("contract_address")} on {facts.get("network")} in block '
            f'{facts.get("block_number")} (transaction {facts.get("tx_hash")}).'
        )
        decoded = _describe_decoded(facts.get('decoded') or {})
        observed = (
            f'{location} Decoded parameters: {decoded}. ' if decoded else f'{location} '
        ) + 'The transaction was successfully confirmed on-chain.'
        if facts.get('initiator'):
            observed += f' It was submitted by {facts.get("initiator")}.'
    else:
        observed = f'{facts.get("explanation")} Observed on {facts.get("network")}.'
    return {
        'schema_version': ANALYSIS_SCHEMA_VERSION,
        'source': 'deterministic',
        'observed_fact': _clip(observed),
        'decoda_interpretation': _clip(facts.get('interpretation') or facts.get('explanation')),
        'operational_authorization': ewc.AUTHORIZATION_UNKNOWN_STATEMENT,
        'monitoring_scope': ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
    }


class AnalysisValidationError(ValueError):
    pass


def validate_analysis(obj: Any, *, facts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise AnalysisValidationError('analysis must be an object')
    out: dict[str, Any] = {}
    for key in _SECTIONS:
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            raise AnalysisValidationError(f'{key} must be a non-empty string')
        out[key] = _clip(value)
    try:
        detection.assert_careful_language(*out.values())
    except detection.LanguageViolation as exc:
        raise AnalysisValidationError(str(exc)) from exc
    authorization = out['operational_authorization'].lower()
    if 'authoriz' not in authorization or not any(word in authorization for word in ('not', 'unknown', 'cannot')):
        raise AnalysisValidationError('operational_authorization must state that authorization is not established')
    allowed = {
        str(value).lower()
        for value in _hex_values(facts)
    }
    for token in _HEX.findall(' '.join(out.values())):
        lowered = token.lower()
        # A cited hex value must be (part of) one the facts contain. A longer
        # token that merely CONTAINS a known value is refused: that is how an
        # invented transaction hash built around a real address would look.
        if not any(lowered in value for value in allowed):
            raise AnalysisValidationError('analysis cites a transaction or address not present in the facts')
    out['schema_version'] = ANALYSIS_SCHEMA_VERSION
    out['monitoring_scope'] = ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC
    return out


def _hex_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_hex_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_hex_values(item))
    elif isinstance(value, str):
        found.extend(match.lower() for match in _HEX.findall(value))
    return found


def _prompt(facts: dict[str, Any]) -> dict[str, str]:
    # AI PRIVACY BOUNDARY — same sanitizer every other AI surface uses. Public
    # chain identifiers pass through; anything credential-shaped does not.
    sanitized = telemetry_privacy.sanitize_for_ai(facts).payload
    system = (
        'You summarize an on-chain change observed by independent public monitoring of an '
        'organization that is NOT a customer and has not authorized the monitoring. Use only the '
        'supplied facts. Never call the change an attack, hack, exploit, compromise or malicious. '
        'Never invent addresses or transactions. Respond with one JSON object with exactly three '
        'string keys: observed_fact (only what the chain recorded), decoda_interpretation (careful '
        'structural meaning), operational_authorization (state that public telemetry does not '
        'establish whether the organization authorized the change).'
    )
    return {
        'system': system,
        'user': json.dumps(sanitized, separators=(',', ':'), default=str),
        'evidence_obj': sanitized,
        'prompt_version': ANALYSIS_SCHEMA_VERSION,
    }


def generate_analysis(facts: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    deterministic = build_deterministic_analysis(facts)
    cfg = config or ewc.ai_config()
    if not (cfg.get('enabled') and cfg.get('provider') in {'openai', 'anthropic'} and cfg.get('has_key') and cfg.get('model')):
        return deterministic
    try:
        from services.api.app.ai_providers import get_triage_provider

        raw = get_triage_provider(cfg['provider']).analyze(
            prompt=_prompt(facts), model=cfg['model'],
            timeout_seconds=float(cfg.get('timeout_seconds') or 30),
            max_output_tokens=int(cfg.get('max_output_tokens') or 1200),
        )
        validated = validate_analysis(json.loads(raw.raw_text), facts=facts)
        validated['source'] = 'ai'
        validated['provider'] = getattr(raw, 'provider', cfg['provider'])
        validated['model'] = getattr(raw, 'model', cfg['model'])
        return validated
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never blocks detection
        logger.info('event=external_watchlist_ai_analysis_fallback reason=%s', type(exc).__name__)
        deterministic['ai_fallback_reason'] = type(exc).__name__
        return deterministic
