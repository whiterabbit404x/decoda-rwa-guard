"""Narrative for an operational-integrity detection — EXPLANATION ONLY.

The trust boundary:

    matcher.py decides everything an operator can act on — the checks, the
    reason code, the detection type, the amounts, the severity, the confidence,
    the status. Those are already final by the time this module is called.

    This module turns that decided object into a sentence. It may summarize,
    explain, prioritize, describe business impact, and suggest an investigation
    step. It may not change a single deterministic field.

``merge_ai_narrative`` is the enforcement point: it takes the immutable
detection dict and an AI payload and returns a dict where the ONLY thing the AI
contributed is ``ai_summary``. Any attempt — accidental or adversarial — to set
severity, confidence, a check status, a reason code, or an approval is dropped
and counted, never applied.

The deterministic builder below is always available, so the screen works fully
with AI disabled, unreachable, or returning nonsense.
"""

from __future__ import annotations

import logging
from typing import Any

from services.api.app.domains.operational_integrity import schemas

logger = logging.getLogger(__name__)

NARRATIVE_SCHEMA_VERSION = 'operational-integrity-explanation-v1'

#: Shown next to the narrative so the operator knows what the text is and is not.
AI_AUTHORITY_LABEL = 'AI Analysis: Explanation only'

# The only key an AI layer may contribute.
_AI_WRITABLE_FIELDS = ('ai_summary',)

_REASON_PHRASES: dict[str, str] = {
    'NO_MATCHING_AUTHORIZED_ISSUANCE': 'no corresponding authorized issuance was found in the authoritative source',
    'NO_MATCHING_AUTHORIZED_REDEMPTION': 'no corresponding authorized redemption was found in the authoritative source',
    'AMOUNT_MISMATCH': 'the closest authorization record was for a different amount',
    'REFERENCE_MISMATCH': 'the business reference on the on-chain event does not match the authorization record',
    'SETTLEMENT_NOT_COMPLETE': 'the matching authorization has not reached a cleared settlement state',
    'OUTSIDE_AUTHORIZED_WINDOW': 'the matching authorization falls outside its permitted window',
    'SETTLEMENT_DEADLINE_EXCEEDED': 'the settlement deadline for an authorized operation passed without clearing',
    'MATCHED_AUTHORIZED_ISSUANCE': 'the issuance matches an authorized record held by the authoritative source',
    'MATCHED_AUTHORIZED_REDEMPTION': 'the redemption matches an authorized record held by the authoritative source',
    'AUTHORITATIVE_SOURCE_MISSING': 'no authoritative business state is recorded for this asset',
    'AUTHORITATIVE_SOURCE_UNAVAILABLE': 'the authoritative source could not be reached on its last attempt',
    'AUTHORITATIVE_SOURCE_STALE': 'the authoritative state is older than the configured freshness threshold',
    'OPERATION_NOT_DECODED': 'the observed on-chain event could not be decoded into a mint or burn',
}


def reason_phrase(reason_code: Any) -> str:
    code = str(reason_code or '').strip().upper()
    return _REASON_PHRASES.get(code, code.replace('_', ' ').lower() or 'no reason code was recorded')


def _format_units(value: Any) -> str:
    if value is None:
        return 'an unrecorded amount'
    try:
        return f'{int(value):+,}'
    except (TypeError, ValueError):
        return str(value)


def build_deterministic_narrative(event: dict[str, Any]) -> dict[str, Any]:
    """Template narrative grounded entirely in fields the matcher computed.

    Always available; the default. Never asserts anything the checks do not."""
    checks = event.get('operational_checks') or {}
    conclusion = str(event.get('conclusion') or schemas.CONCLUSION_INDETERMINATE)
    asset = str((event.get('provenance') or {}).get('asset_name') or 'the monitored asset')
    operation = str(event.get('operation') or 'operation')
    reason = reason_phrase(event.get('deterministic_reason_code'))

    passed = [schemas.CHECK_LABELS.get(k, k) for k, v in checks.items() if (v or {}).get('status') == schemas.PASS]
    failed = [schemas.CHECK_LABELS.get(k, k) for k, v in checks.items() if (v or {}).get('status') == schemas.FAIL]
    unknown = [schemas.CHECK_LABELS.get(k, k) for k, v in checks.items() if (v or {}).get('status') == schemas.UNKNOWN]

    if conclusion == schemas.CONCLUSION_INDETERMINATE:
        finding = (
            f'Operational authorization for this {operation} on {asset} could not be established.'
        )
        explanation = (
            f'The check could not be completed because {reason}. '
            'This is a data-availability state, not a finding that the operation was unauthorized.'
        )
        step = 'Restore the authoritative source, then re-evaluate before drawing a conclusion.'
    elif conclusion == schemas.CONCLUSION_OPERATIONALLY_AUTHORIZED:
        finding = f'The observed {operation} on {asset} reconciles with authorized business state.'
        explanation = f'Every operational check passed: {reason}.'
        step = 'No investigation is required for this event.'
    else:
        finding = (
            f'A cryptographically valid {operation} on {asset} is not supported by authorized business state.'
        )
        explanation = (
            f'The transaction was accepted on-chain, but {reason}. '
            f'Observed {_format_units(event.get("observed_amount"))} against an authorized '
            f'{_format_units(event.get("expected_amount"))}.'
        )
        step = (
            'Confirm the operation with the authoritative source of record, then open an '
            'investigation if it remains unexplained.'
        )

    return {
        'finding': finding,
        'explanation': explanation,
        'checks_passed': passed,
        'checks_failed': failed,
        'checks_unknown': unknown,
        'investigation_step': step,
        'authority': AI_AUTHORITY_LABEL,
        'source': 'deterministic',
        'schema_version': NARRATIVE_SCHEMA_VERSION,
    }


def ai_facts(event: dict[str, Any]) -> dict[str, Any]:
    """The immutable, already-decided object handed to an AI layer.

    Read-only by construction: the model receives the verdict as an input, so it
    has nothing left to decide."""
    return {
        'detection_type': event.get('detection_type'),
        'category': event.get('category'),
        'severity': event.get('severity'),
        'conclusion': event.get('conclusion'),
        'deterministic_reason_code': event.get('deterministic_reason_code'),
        'confidence': event.get('confidence'),
        'operation': event.get('operation'),
        'observed_amount': event.get('observed_amount'),
        'expected_amount': event.get('expected_amount'),
        'variance_amount': event.get('variance_amount'),
        'operational_checks': event.get('operational_checks'),
        'telemetry_source': event.get('telemetry_source'),
        'telemetry_stage': event.get('telemetry_stage'),
        'tx_hash': event.get('tx_hash'),
        'asset_name': (event.get('provenance') or {}).get('asset_name'),
        'instruction': (
            'Explain this already-decided detection for a security operator. You may '
            'summarize, explain, prioritize, describe business impact, and suggest an '
            'investigation step. You may NOT change severity, confidence, amounts, check '
            'results, the reason code, or the status, and you may not approve, deny, or '
            'execute anything.'
        ),
        'schema_version': NARRATIVE_SCHEMA_VERSION,
    }


def merge_ai_narrative(detection: dict[str, Any], ai_payload: Any) -> dict[str, Any]:
    """Merge an AI response into a detection, keeping every deterministic field.

    Returns a NEW dict. The AI can only ever land in ``ai_summary``; anything
    else it tried to set is dropped and reported in ``ai_rejected_fields`` so the
    attempt is visible rather than silent.
    """
    merged = dict(detection)
    merged.setdefault('ai_summary', None)
    merged['ai_summary_source'] = 'deterministic'
    merged['ai_authority'] = AI_AUTHORITY_LABEL

    if not isinstance(ai_payload, dict):
        return merged

    rejected = sorted(
        key for key in ai_payload
        if key in schemas.DETERMINISTIC_FIELDS or key not in _AI_WRITABLE_FIELDS
    )
    summary = ai_payload.get('ai_summary')
    if isinstance(summary, str) and summary.strip():
        merged['ai_summary'] = ' '.join(summary.split())[:1200]
        merged['ai_summary_source'] = 'ai'

    if rejected:
        merged['ai_rejected_fields'] = rejected
        logger.info(
            'event=operational_integrity_ai_fields_rejected count=%s fields=%s',
            len(rejected), ','.join(rejected),
        )
    return merged
