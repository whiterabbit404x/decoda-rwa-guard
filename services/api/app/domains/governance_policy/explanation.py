"""Narrative for a policy decision — EXPLANATION ONLY.

The trust boundary:

    engine.evaluate_policy decides everything an operator can act on — the
    decision, the reason codes, the checks, the policy version, the outstanding
    approvals. Those are already FINAL by the time this module is called.

    This module turns that decided object into a sentence. It may summarize,
    explain, prioritize, describe business impact, and suggest a next step. It
    may not change a single deterministic field.

``merge_ai_explanation`` is the enforcement point: it takes the immutable
decision dict plus an AI payload and returns a dict where the ONLY thing the AI
contributed is ``ai_explanation``. Any attempt — accidental or adversarial — to
set the decision, a reason code, a policy version, a check, or an approval is
dropped and counted, never applied.

The deterministic builder below is always available, so Screen 11 works fully
with AI disabled, unreachable, or returning nonsense. §8: "If AI is unavailable,
the policy decision must still work."
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import schemas

logger = logging.getLogger(__name__)

EXPLANATION_SCHEMA_VERSION = 'governance-policy-explanation-v1'

#: Shown next to the narrative so the operator knows what the text is and is not.
AI_AUTHORITY_LABEL = 'AI Analysis: Explanation only'

#: The only key an AI layer may contribute.
_AI_WRITABLE_FIELDS = ('ai_explanation',)

_REASON_PHRASES: dict[str, str] = {
    gpc.POLICY_SATISFIED: 'every requirement the policy imposes was met',
    gpc.POLICY_NOT_FOUND: 'no policy governs this operation, so nothing authorizes it',
    gpc.POLICY_DISABLED: 'the policy is disabled and cannot authorize an operation',
    gpc.POLICY_NOT_ACTIVE: 'the policy is not active, so it cannot authorize an operation',
    gpc.OPERATION_MISMATCH: 'the policy governs a different operation',
    gpc.BUSINESS_EVENT_MISSING: 'the required business event was not supplied',
    gpc.BUSINESS_EVENT_MISMATCH: 'the business event is not the type the policy requires',
    gpc.SETTLEMENT_NOT_CLEARED: 'settlement has not reached the state the policy requires',
    gpc.SETTLEMENT_STATE_UNKNOWN: 'the settlement state could not be established',
    gpc.OUTSIDE_ALLOWED_WINDOW: 'the request falls outside the policy’s allowed UTC window',
    gpc.EVALUATION_TIMESTAMP_MISSING: 'no evaluation timestamp was supplied, so the allowed window could not be checked',
    gpc.AMOUNT_LIMIT_EXCEEDED: 'the amount would exceed the policy’s daily issuance limit',
    gpc.AMOUNT_INVALID: 'the request amount is missing or not a valid value',
    gpc.DAILY_TOTAL_UNAVAILABLE: 'today’s issuance total under this policy could not be established',
    gpc.TREASURY_OPERATOR_MISSING: 'the operator does not hold the Treasury Operator authority the policy requires',
    gpc.COMPLIANCE_APPROVAL_MISSING: 'the policy requires a Compliance Approver before issuance and none is present',
    gpc.REQUIRED_ROLE_MISSING: 'a role the policy requires could not be evidenced',
}


def reason_phrase(reason_code: Any) -> str:
    code = str(reason_code or '').strip().upper()
    return _REASON_PHRASES.get(code, code.replace('_', ' ').lower() or 'no reason code was recorded')


def _join(phrases: list[str]) -> str:
    if not phrases:
        return ''
    if len(phrases) == 1:
        return phrases[0]
    return ', '.join(phrases[:-1]) + ', and ' + phrases[-1]


def build_deterministic_explanation(decision: dict[str, Any]) -> dict[str, Any]:
    """Template narrative grounded entirely in fields the engine computed.

    Always available; the default. Never asserts anything the checks do not.
    """
    verdict = str(decision.get('decision') or gpc.DECISION_DENY).upper()
    policy_key = str(decision.get('policy_key') or 'the governing policy')
    version = decision.get('policy_version')
    operation = str(decision.get('operation') or 'operation').lower()
    codes = [str(c) for c in (decision.get('reason_codes') or [])]
    outstanding = [str(r) for r in (decision.get('required_approvals') or [])]

    version_text = f' version {version}' if version is not None else ''

    if verdict == gpc.DECISION_ALLOW:
        summary = (
            f'This {operation} would be allowed: {policy_key}{version_text} evaluated every '
            'requirement it imposes and each one was met.'
        )
        next_step = 'No policy blocker remains. Execution still follows the response authorization gate.'
    else:
        phrases = [reason_phrase(code) for code in codes] or ['the policy was not satisfied']
        summary = (
            f'This {operation} would be denied because {policy_key}{version_text} was not satisfied: '
            f'{_join(phrases)}.'
        )
        if outstanding:
            labels = [gpc.GOVERNANCE_ROLE_LABELS.get(r, r.replace('_', ' ').title()) for r in outstanding]
            next_step = f'Obtain sign-off from: {_join(labels)}, then re-evaluate.'
        else:
            next_step = 'Correct the inputs the policy requires, then re-evaluate.'

    return {
        'summary': summary,
        'next_step': next_step,
        'reason_phrases': [reason_phrase(code) for code in codes],
        'authority': AI_AUTHORITY_LABEL,
        'source': 'deterministic',
        'schema_version': EXPLANATION_SCHEMA_VERSION,
    }


def ai_facts(decision: dict[str, Any]) -> dict[str, Any]:
    """The immutable, already-decided object handed to an AI layer.

    Read-only by construction: the model receives the verdict as an INPUT, so it
    has nothing left to decide.
    """
    return {
        'decision': decision.get('decision'),
        'reason_codes': decision.get('reason_codes'),
        'policy_key': decision.get('policy_key'),
        'policy_version': decision.get('policy_version'),
        'operation': decision.get('operation'),
        'amount_usd': decision.get('amount_usd'),
        'checks': decision.get('checks'),
        'required_approvals': decision.get('required_approvals'),
        'required_roles': decision.get('required_roles'),
        'violation_action': decision.get('violation_action'),
        'decision_authority': schemas.DECISION_AUTHORITY,
        'instruction': (
            'Explain this already-decided policy evaluation for a governance operator. You may '
            'summarize, explain, prioritize, describe business impact, and suggest a next step. '
            'You may NOT decide or change the ALLOW/DENY outcome, invent or drop a reason code, '
            'change a policy version or a check result, approve anything, or execute anything. '
            'The decision was produced by deterministic code and is given to you as a fact.'
        ),
        'schema_version': EXPLANATION_SCHEMA_VERSION,
    }


def explanation_config() -> dict[str, Any]:
    """Mirrors the repository's AI configuration convention (see
    domains/asset_integrity/ai_explanation.ai_summary_config)."""
    provider = (os.getenv('AI_PROVIDER', '') or '').strip().lower()
    enabled = str(os.getenv('GOVERNANCE_POLICY_AI_ENABLED', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}
    has_key = bool((os.getenv('AI_API_KEY') or os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or '').strip())
    return {
        'enabled': enabled,
        'provider': provider,
        'model': (os.getenv('AI_MODEL_GOVERNANCE_POLICY', '') or os.getenv('AI_MODEL', '') or '').strip(),
        'has_key': has_key,
        'timeout_seconds': float(os.getenv('AI_REQUEST_TIMEOUT_SECONDS', '30') or 30),
        'max_output_tokens': int(os.getenv('AI_MAX_OUTPUT_TOKENS', '2000') or 2000),
    }


def merge_ai_explanation(decision: dict[str, Any], ai_payload: Any) -> dict[str, Any]:
    """Merge an AI response into a decision, keeping every deterministic field.

    Returns a NEW dict. The AI can only ever land in ``ai_explanation``; anything
    else it tried to set is dropped and reported in ``ai_rejected_fields`` so the
    attempt is visible rather than silent.
    """
    merged = dict(decision)
    deterministic = build_deterministic_explanation(decision)
    merged['ai_explanation'] = deterministic['summary']
    merged['ai_next_step'] = deterministic['next_step']
    merged['ai_explanation_source'] = 'deterministic'
    # A LABEL for the narrative panel. Deliberately NOT 'ai_authority': that key
    # is part of the deterministic Screen 8 contract ('Recommend only') and this
    # module may not write a deterministic field, not even a matching one.
    merged['ai_explanation_authority'] = AI_AUTHORITY_LABEL
    merged['decision_authority'] = schemas.DECISION_AUTHORITY

    if not isinstance(ai_payload, dict):
        return merged

    rejected = sorted(
        key for key in ai_payload
        if key in schemas.DETERMINISTIC_FIELDS or key not in _AI_WRITABLE_FIELDS
    )
    text = ai_payload.get('ai_explanation')
    if isinstance(text, str) and text.strip():
        merged['ai_explanation'] = ' '.join(text.split())[:1200]
        merged['ai_explanation_source'] = 'ai'

    if rejected:
        merged['ai_rejected_fields'] = rejected
        logger.info(
            'event=governance_policy_ai_fields_rejected count=%s fields=%s',
            len(rejected), ','.join(rejected),
        )
    return merged


def _build_prompt(facts: dict[str, Any]) -> dict[str, Any]:
    system = (
        'You explain a completed, deterministic governance policy evaluation to an operator. '
        'The ALLOW/DENY decision, the reason codes, the policy version, and the check results '
        'have ALREADY been computed by application code and are given to you. Rules: never '
        'decide or restate a different outcome; never invent, drop, or reinterpret a reason '
        'code; never approve, authorize, or execute anything; use only the facts given. '
        'Respond with a single JSON object with the key: ai_explanation (string).'
    )
    return {
        'system': system,
        'user': json.dumps(facts, separators=(',', ':'), default=str),
        'evidence_obj': facts,
        'prompt_version': EXPLANATION_SCHEMA_VERSION,
    }


def explain(decision: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the decision with a narrative attached.

    Deterministic unless a live provider is enabled, configured, and returns a
    usable object. Every failure path falls back to the deterministic narrative;
    none of them can change the decision, because the decision is merged in from
    the caller's dict and every deterministic key is rejected on merge.
    """
    cfg = config or explanation_config()
    if not cfg.get('enabled') or cfg.get('provider') not in {'openai', 'anthropic'} or not cfg.get('has_key') or not cfg.get('model'):
        return merge_ai_explanation(decision, None)
    try:
        from services.api.app.ai_providers import get_triage_provider

        provider = get_triage_provider(cfg['provider'])
        raw = provider.analyze(
            prompt=_build_prompt(ai_facts(decision)),
            model=cfg['model'],
            timeout_seconds=float(cfg.get('timeout_seconds') or 30),
            max_output_tokens=int(cfg.get('max_output_tokens') or 2000),
        )
        merged = merge_ai_explanation(decision, json.loads(raw.raw_text))
        merged['ai_provider'] = getattr(raw, 'provider', cfg['provider'])
        merged['ai_model'] = getattr(raw, 'model', cfg['model'])
        return merged
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never blocks
        logger.info('event=governance_policy_ai_explanation_fallback reason=%s', type(exc).__name__)
        merged = merge_ai_explanation(decision, None)
        merged['ai_fallback_reason'] = type(exc).__name__
        return merged
