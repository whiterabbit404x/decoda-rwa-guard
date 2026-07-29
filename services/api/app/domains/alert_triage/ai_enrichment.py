"""Optional AI narrative enrichment for alert clusters (fail-closed).

Contract (mirrors the truthfulness rules for Screen 6):
  * The deterministic rules-based summary always exists first (built in
    clustering.py). AI enrichment may ONLY replace that summary text with a more
    readable narrative.
  * AI enrichment may NOT change the cluster's membership, severity, confidence,
    or recommendation category — those stay deterministic.
  * A cluster is only marked ``ai_assisted`` when a REAL (non-simulated) provider
    actually produced its summary. Any failure, disablement, or misconfiguration
    falls back to the rules-based summary and records the reason — it never
    fabricates AI reasoning and never blocks clustering.
  * The narrative is derived ONLY from the cluster's own already-grounded fields
    (title, family, severity, member count, reason labels, asset name). No new
    facts (actors, tx hashes, values, attack types) are ever introduced.
  * Provider output is treated as untrusted text and sanitized here.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Protocol

from services.api.app.domains.alert_triage import config as cfg

logger = logging.getLogger(__name__)

# Hard bound on any narrative we will persist / render.
MAX_SUMMARY_CHARS = 480

_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
_WS = re.compile(r'\s+')
_HTML = re.compile(r'<[^>]*>')


def sanitize_narrative(text: Any) -> str:
    """Collapse untrusted provider text into safe, bounded plain text.

    Strips HTML-looking tags and control characters so an AI summary can never
    inject markup or escape sequences into the UI, and bounds the length.
    """
    s = str(text or '')
    s = _HTML.sub(' ', s)
    s = _CONTROL_CHARS.sub(' ', s)
    s = _WS.sub(' ', s).strip()
    if len(s) > MAX_SUMMARY_CHARS:
        s = s[:MAX_SUMMARY_CHARS].rstrip() + '…'
    return s


class NarrativeProvider(Protocol):
    """A provider that turns grounded cluster facts into a short narrative.

    ``simulated`` distinguishes an offline/deterministic double (never counted as
    AI-assisted) from a real provider call.
    """

    name: str
    simulated: bool

    def narrate(self, context: dict[str, Any]) -> str: ...


class MockAlertNarrativeProvider:
    """Deterministic, offline provider. Used as a test double / offline default.

    It is ``simulated`` — a run that only used this provider stays rules-based and
    the panel shows 'Rules-based', never 'AI-assisted'.
    """

    name = 'mock'
    simulated = True

    def narrate(self, context: dict[str, Any]) -> str:
        reasons = context.get('reason_labels') or []
        lead = reasons[0].lower() if reasons else 'shared root-cause evidence'
        return (
            f"{context.get('member_count', 0)} related alerts on "
            f"{context.get('asset_name') or 'the monitored asset'} were grouped by {lead}."
        )


class FailingNarrativeProvider:
    """Always raises — used in tests to prove the fail-closed fallback path."""

    name = 'failing'
    simulated = False

    def narrate(self, context: dict[str, Any]) -> str:
        raise RuntimeError('narrative provider unavailable')


def _cluster_context(cluster: dict[str, Any]) -> dict[str, Any]:
    """The ONLY facts an AI provider is allowed to see — already grounded, no
    raw actors / tx hashes / values."""
    return {
        'title': cluster.get('title'),
        'detection_family': cfg.detection_family_label(cluster.get('detection_family')),
        'severity': cluster.get('derived_severity'),
        'member_count': cluster.get('member_count'),
        'asset_name': cluster.get('primary_asset_name'),
        'recommendation': cfg.recommendation_label(cluster.get('recommendation_category')),
        'reason_labels': [cfg.reason_label(c) for c in (cluster.get('reason_codes') or [])],
    }


def get_narrative_provider(config: dict[str, Any] | None = None) -> Optional[NarrativeProvider]:
    """Resolve a REAL narrative provider, or None when AI is unavailable.

    Returns None (rules-based) when no live provider is configured — the offline
    mock is never auto-selected in production, so a real deployment without AI
    stays truthfully rules-based.
    """
    if not cfg.ai_available(config):
        return None
    from services.api.app import ai_triage

    conf = config or ai_triage.triage_config()
    provider = str(conf.get('provider') or '').strip().lower()
    try:
        if provider == 'openai':
            return OpenAINarrativeProvider(conf)
        # Anthropic (and any future live provider) can be wired here; until then a
        # missing implementation falls back to rules-based rather than pretending.
        return None
    except Exception:
        logger.warning('event=alert_triage_narrative_provider_init_failed provider=%s', provider)
        return None


def enrich_clusters(
    clusters: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    provider_override: Optional[NarrativeProvider] = None,
) -> dict[str, Any]:
    """Attach a narrative + source to each cluster. Mutates each cluster dict.

    Returns aggregate {ai_used, ai_error, provider}. Deterministic fields are
    never touched. Fail-closed: any provider error leaves the rules-based summary
    intact and is reported once.
    """
    provider = provider_override if provider_override is not None else get_narrative_provider(config)
    ai_used = 0
    ai_error: Optional[str] = None

    for cluster in clusters:
        # The deterministic summary is the guaranteed floor.
        cluster.setdefault('recommendation_summary', cluster.get('recommendation_summary') or '')
        cluster['recommendation_source'] = 'rules_based'
        if provider is None:
            continue
        try:
            raw = provider.narrate(_cluster_context(cluster))
            narrative = sanitize_narrative(raw)
            if not narrative:
                continue
            cluster['recommendation_summary'] = narrative
            # Only a real (non-simulated) provider earns the 'ai_assisted' label.
            if getattr(provider, 'simulated', False):
                cluster['recommendation_source'] = 'rules_based'
            else:
                cluster['recommendation_source'] = 'ai_assisted'
                ai_used += 1
        except Exception as exc:  # noqa: BLE001 - fail-closed, keep rules-based summary
            ai_error = ai_error or f'{exc.__class__.__name__}'
            logger.warning('event=alert_triage_narrative_failed error_type=%s', exc.__class__.__name__)

    return {
        'ai_used': ai_used,
        'ai_error': ai_error,
        'provider': getattr(provider, 'name', None) if provider is not None else None,
    }


class OpenAINarrativeProvider:
    """Thin, lazily-constructed OpenAI narrative provider.

    Never imported or called by tests (they inject a provider override or run with
    AI disabled), so no OpenAI credits are consumed. The prompt carries ONLY the
    already-grounded cluster facts and forbids new facts, so the model cannot
    invent actors, hashes, values, or attack types.
    """

    name = 'openai'
    simulated = False

    def __init__(self, config: dict[str, Any]):
        self._model = str(config.get('model') or '').strip()
        self._timeout = float(config.get('request_timeout_seconds') or 30.0)
        self._max_tokens = int(config.get('max_output_tokens') or 512)
        import os

        self._api_key = (
            os.getenv('AI_API_KEY') or os.getenv('OPENAI_API_KEY') or ''
        ).strip()
        if not self._api_key or not self._model:
            raise RuntimeError('openai narrative provider is not fully configured')

    def narrate(self, context: dict[str, Any]) -> str:  # pragma: no cover - network path
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key, timeout=self._timeout)
        facts = (
            f"Cluster: {context.get('title')}\n"
            f"Category: {context.get('detection_family')}\n"
            f"Severity: {context.get('severity')}\n"
            f"Related alerts: {context.get('member_count')}\n"
            f"Asset: {context.get('asset_name') or 'unspecified'}\n"
            f"Recommendation: {context.get('recommendation')}\n"
            f"Evidence signals: {', '.join(context.get('reason_labels') or []) or 'none'}"
        )
        instructions = (
            'You are a security triage assistant. Write at most two sentences summarizing '
            'this alert cluster for an analyst. Use ONLY the facts provided. Do not invent '
            'actors, transaction hashes, amounts, asset values, or attack types. Do not use '
            'markup.'
        )
        response = client.responses.create(
            model=self._model,
            max_output_tokens=self._max_tokens,
            input=[
                {'role': 'system', 'content': instructions},
                {'role': 'user', 'content': facts},
            ],
        )
        return getattr(response, 'output_text', '') or ''
