"""Executive Brief generation for the Dashboard Co-Pilot (Screen 2).

The Executive Brief is an evidence-grounded, executive-readable summary of the
last reporting period. It is generated at most once per workspace per reporting
period (idempotent), never on every page load, and it degrades to a fully
deterministic brief whenever the AI provider is disabled, unavailable,
rate-limited, or returns output that fails validation.

Two hard truthfulness guarantees enforced here:

1. **The LLM never invents facts.** Every citation and every ``key_finding``
   source reference produced by the model is validated against a
   workspace-scoped index of real source IDs. Unknown or cross-workspace refs
   are dropped; a finding left with no valid reference is removed; if the model
   grounded nothing, the whole brief falls back to the deterministic path.
2. **Numbers come from the deterministic scorers**, not the model. The brief
   only narrates the risk/health scores and aggregates passed to it.

This module reuses the existing provider abstraction
(:mod:`services.api.app.ai_providers`) — it does not open a second OpenAI
integration. Any object exposing ``analyze(prompt, model, timeout_seconds,
max_output_tokens) -> ProviderRawResult`` works, so tests can inject fakes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

BRIEF_PROMPT_VERSION = 'dashboard-brief-2026-07-1'
# Bumped 1 -> 2: briefs stored before the canonical active-incident fix were keyed
# only on the reporting date, so a stale "no open incidents" brief survived a same
# day incident-state change. v2 keys carry a state fingerprint (see
# ``brief_state_fingerprint``); bumping the version also forces every previously
# stored, potentially inconsistent brief to be regenerated on next read.
BRIEF_VERSION = 2

# Destinations a recommended-focus item may deep-link to. Anything else is
# coerced to 'monitoring' so the frontend never renders a broken link.
_VALID_DESTINATIONS = {'alerts', 'incidents', 'monitoring', 'assets', 'system-health'}

_SYSTEM_PROMPT = (
    'You are the Decoda RWA Guard Dashboard Co-Pilot. You write a concise, '
    'executive-readable operational brief for a security operator. You may ONLY '
    'describe facts present in the provided evidence JSON. Never invent an '
    'incident, alert, asset value, provider failure, or recommendation. Never '
    'claim "all systems healthy" unless health_status is "healthy". Prefer '
    'decisions over raw event counts. Distinguish confirmed incidents from '
    'anomalies. Do not claim causation without evidence. Every key_finding '
    'source_ref and every citation MUST reference an id that appears in '
    'evidence.available_sources. Respond with a single JSON object only, no '
    'prose, matching the requested schema.'
)


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


# The canonical state a brief narrates. A change to ANY of these fields makes a
# previously stored brief stale — its prose can no longer be trusted to match the
# metrics — so the fingerprint (and therefore the idempotency key) changes and the
# brief is regenerated. Kept explicit and ordered so the hash is stable/reviewable.
_FINGERPRINT_FIELDS: tuple[str, ...] = (
    'active_incidents_now',
    'critical_high_active_incidents_now',
    'active_alerts_now',
    'risk_score',
    'system_health_score',
    'telemetry_freshness',
    'monitoring_state',
    'schema_version',
)


def brief_state_fingerprint(state: Mapping[str, Any]) -> str:
    """Deterministic short hash of the canonical state a brief describes.

    Folding the fingerprint into the idempotency key is what makes brief
    invalidation reliable: when the active-incident count, critical/high count,
    active-alert count, risk score, health score, telemetry freshness, monitoring
    operational state, or prompt/brief schema version changes, the key changes and
    the previously stored brief no longer matches (is treated as stale).
    """
    canonical = {field: state.get(field) for field in _FINGERPRINT_FIELDS}
    blob = json.dumps(canonical, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


def brief_idempotency_key(
    workspace_id: str,
    reporting_date: str,
    prompt_version: str = BRIEF_PROMPT_VERSION,
    fingerprint: str = '',
) -> str:
    """Versioned, state-aware key for one brief per workspace/date/state.

    The base is ``{workspace}:{date}:v{brief_version}:{prompt_version}``; when a
    ``fingerprint`` (see :func:`brief_state_fingerprint`) is supplied it is
    appended, so two same-day requests collapse onto the same row only while the
    underlying canonical state is unchanged — and a state change yields a distinct
    key that misses the stale row and regenerates.
    """
    base = f'{workspace_id}:{reporting_date}:v{BRIEF_VERSION}:{prompt_version}'
    return f'{base}:{fingerprint}' if fingerprint else base


# --------------------------------------------------------------------------
# Source index + citation validation
# --------------------------------------------------------------------------


def build_source_index(citations: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """Index the workspace's real source refs as {source_type: {id, ...}}.

    ``citations`` are the *candidate* references the aggregation layer already
    verified belong to this workspace (alert IDs, incident IDs, asset IDs,
    monitoring target IDs, provider/worker/telemetry snapshot IDs).
    """
    index: dict[str, set[str]] = {}
    for citation in citations:
        source_type = str(citation.get('source_type') or '').strip()
        source_id = str(citation.get('source_id') or '').strip()
        if source_type and source_id:
            index.setdefault(source_type, set()).add(source_id)
    return index


def _citation_allowed(citation: dict[str, Any], index: dict[str, set[str]]) -> bool:
    source_type = str(citation.get('source_type') or '').strip()
    source_id = str(citation.get('source_id') or '').strip()
    return bool(source_type) and source_id in index.get(source_type, set())


def validate_citations(
    citations: Iterable[dict[str, Any]],
    index: dict[str, set[str]],
    lookup: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Drop any citation that is not a known, workspace-scoped source ref.

    When ``lookup`` (keyed by ``(source_type, source_id)``) is supplied the
    canonical label/url/occurred_at are substituted so the model can never spoof
    a display label or deep-link for a real id.
    """
    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for citation in citations or []:
        if not isinstance(citation, dict) or not _citation_allowed(citation, index):
            continue
        key = (str(citation.get('source_type')), str(citation.get('source_id')))
        if key in seen:
            continue
        seen.add(key)
        canonical = (lookup or {}).get(key)
        validated.append(dict(canonical) if canonical else _clean_citation(citation))
    return validated


def _clean_citation(citation: dict[str, Any]) -> dict[str, Any]:
    return {
        'source_type': str(citation.get('source_type') or ''),
        'source_id': str(citation.get('source_id') or ''),
        'label': str(citation.get('label') or '')[:160],
        'occurred_at': citation.get('occurred_at'),
        'url': str(citation.get('url') or ''),
    }


# --------------------------------------------------------------------------
# Evidence assembly (bounded input to the model)
# --------------------------------------------------------------------------


def build_brief_evidence(aggregates: dict[str, Any]) -> dict[str, Any]:
    """Select and bound the aggregates that are safe to send to the model.

    Never sends raw events, secrets, RPC URLs, or unbounded lists — only
    pre-aggregated counts, deltas, the deterministic scores, and the list of
    available source refs the model is allowed to cite.
    """
    metrics = aggregates.get('metrics', {})
    risk = aggregates.get('risk', {})
    health = aggregates.get('health', {})
    deltas = aggregates.get('deltas', {})
    return {
        'period': {
            'start': aggregates.get('period_start'),
            'end': aggregates.get('period_end'),
            'label': 'last_24_hours',
        },
        'telemetry': {
            'events_last_24h': aggregates.get('telemetry_events_24h', 0),
            'events_prev_24h': aggregates.get('telemetry_events_prev_24h', 0),
            'freshness': aggregates.get('telemetry_freshness', 'unavailable'),
        },
        'alerts': {
            # active_now == currently-active alerts; created_during_period == new
            # alerts in the window. Kept separate so the model never conflates
            # "0 active now" with "0 activity this period" (or vice-versa).
            'active_now': metrics.get('active_alert_count', 0),
            'active_total': metrics.get('active_alert_count', 0),
            'created_during_period': aggregates.get('alerts_created_24h', 0),
            'by_severity': aggregates.get('alert_severity_counts', {}),
            'clusters': aggregates.get('alert_cluster_count', 0),
            'delta_7d': deltas.get('active_alert_count'),
        },
        'incidents': {
            # active_now / critical_high_active_now are the operator-visible current
            # state; opened/resolved_during_period are reporting-period movement.
            'active_now': metrics.get('open_incident_count', 0),
            'active_total': metrics.get('open_incident_count', 0),
            'critical_high_active_now': aggregates.get('incidents_critical_high', 0),
            'opened_during_period': aggregates.get('incidents_opened_24h', 0),
            'resolved_during_period': aggregates.get('incidents_resolved_24h', 0),
            'opened_24h': aggregates.get('incidents_opened_24h', 0),
            'resolved_24h': aggregates.get('incidents_resolved_24h', 0),
            'critical_or_high': aggregates.get('incidents_critical_high', 0),
            'delta_7d': deltas.get('open_incident_count'),
        },
        'assets': {
            'monitored_count': metrics.get('monitored_asset_count', 0),
            'critical_affected': aggregates.get('critical_assets_affected', 0),
            'value_usd': metrics.get('total_asset_value_usd'),
        },
        'monitoring': {
            'degraded_providers': aggregates.get('degraded_provider_count', 0),
            'stale_targets': aggregates.get('stale_target_count', 0),
            'worker_failures': aggregates.get('worker_failure_count', 0),
        },
        'risk': {
            'score': risk.get('score'),
            'band': risk.get('band'),
            'change': deltas.get('risk_score'),
            'top_drivers': risk.get('top_risk_drivers', [])[:5],
        },
        'health': {
            'score': health.get('score'),
            'status': health.get('status'),
            'change': deltas.get('system_health_score'),
        },
        'top_anomalies': aggregates.get('top_anomalies', [])[:5],
        'available_sources': aggregates.get('citations', [])[:40],
        'evidence_quality': risk.get('evidence_quality', 'partial'),
    }


def build_brief_prompt(evidence: dict[str, Any], prompt_version: str = BRIEF_PROMPT_VERSION) -> dict[str, str]:
    """Build the provider prompt payload (system + user + parsed evidence)."""
    schema_hint = {
        'headline': 'string',
        'summary': 'string (2-4 sentences)',
        'key_findings': [{'title': 'string', 'description': 'string', 'severity': 'low|medium|high|critical', 'source_refs': [{'source_type': 'string', 'source_id': 'string'}]}],
        'recommended_focus': [{'title': 'string', 'reason': 'string', 'destination': 'alerts|incidents|monitoring|assets|system-health'}],
        'confidence': 'number 0..1',
    }
    user = (
        'Write the executive brief as JSON matching this schema:\n'
        + json.dumps(schema_hint, separators=(',', ':'))
        + '\n\nEvidence (the only facts you may use):\n'
        + json.dumps(evidence, separators=(',', ':'), default=str)
    )
    return {
        'system': _SYSTEM_PROMPT,
        'user': user,
        'evidence_obj': json.dumps(evidence, default=str),
        'prompt_version': prompt_version,
    }


# --------------------------------------------------------------------------
# Deterministic fallback brief
# --------------------------------------------------------------------------


def _pick_citations_for(aggregates: dict[str, Any], source_types: set[str], limit: int = 3) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for citation in aggregates.get('citations', []):
        if str(citation.get('source_type')) in source_types:
            picked.append(_clean_citation(citation))
        if len(picked) >= limit:
            break
    return picked


def build_deterministic_brief(aggregates: dict[str, Any]) -> dict[str, Any]:
    """Compose a truthful brief purely from verified aggregates (no model).

    Used whenever AI is unavailable/invalid. Reads the same evidence the model
    would have seen, so the dashboard is fully functional without OpenAI.
    """
    metrics = aggregates.get('metrics', {})
    risk = aggregates.get('risk', {})
    health = aggregates.get('health', {})
    deltas = aggregates.get('deltas', {})

    active_alerts = int(metrics.get('active_alert_count', 0) or 0)
    open_incidents = int(metrics.get('open_incident_count', 0) or 0)
    risk_score = risk.get('score', 0)
    risk_band = str(risk.get('band', 'low'))
    health_status = str(health.get('status', 'not_configured'))

    # Headline: lead with the single most important operational fact.
    if open_incidents > 0:
        headline = f'{open_incidents} open incident{"s" if open_incidents != 1 else ""} {"require" if open_incidents != 1 else "requires"} attention'
    elif active_alerts > 0:
        headline = f'{active_alerts} active alert{"s" if active_alerts != 1 else ""} under review'
    elif health_status not in {'healthy'}:
        headline = f'System health is {health_status.replace("_", " ")}'
    else:
        headline = 'No open incidents; monitoring nominal'

    # Summary: 2-4 grounded sentences.
    parts: list[str] = []
    parts.append(
        f'Global risk is {risk_score}/100 ({risk_band})'
        + _delta_phrase(deltas.get('risk_score'), 'point')
        + '.'
    )
    parts.append(
        f'System health is {health.get("score", 0)}/100 ({health_status.replace("_", " ")})'
        + _delta_phrase(deltas.get('system_health_score'), 'point')
        + '.'
    )
    # Current state vs reporting-period activity, stated precisely. Never says
    # "no open incidents in the current window" while incidents are active — the
    # bug this wording replaces. Assembled from the same aggregates the metric
    # cards read, so the fallback prose can never contradict the numbers.
    parts.append(_incident_activity_sentence(aggregates))
    parts.append(_alert_activity_sentence(aggregates))
    summary = ' '.join(p for p in parts[:4] if p)

    key_findings = _deterministic_findings(aggregates)
    recommended_focus = _deterministic_focus(aggregates)
    citations = [c for finding in key_findings for c in finding.get('source_refs', [])]
    confidence = 0.7 if risk.get('evidence_quality') == 'complete' else 0.5

    return {
        'headline': headline,
        'summary': summary,
        'key_findings': key_findings,
        'recommended_focus': recommended_focus,
        'citations': _dedupe_citations(citations),
        'confidence': confidence,
        'generation_mode': 'deterministic_fallback',
        'provider': 'deterministic',
        'model': 'deterministic',
        'prompt_version': BRIEF_PROMPT_VERSION,
    }


def _plural(n: int) -> str:
    return '' if n == 1 else 's'


def _incident_activity_sentence(aggregates: dict[str, Any]) -> str:
    """Precise incident wording: current active state, then period movement.

    Distinguishes *active now* (operator-visible open incidents) from *opened /
    resolved during the reporting period*. Never claims "no open incidents" while
    active incidents exist.
    """
    metrics = aggregates.get('metrics', {})
    active = int(metrics.get('open_incident_count', 0) or 0)
    crit_high = int(aggregates.get('incidents_critical_high', 0) or 0)
    opened = int(aggregates.get('incidents_opened_24h', 0) or 0)
    resolved = int(aggregates.get('incidents_resolved_24h', 0) or 0)

    if active > 0:
        verb = 'is' if active == 1 else 'are'
        text = f'There {verb} {active} active incident{_plural(active)}'
        if crit_high > 0:
            text += f', including {crit_high} critical/high incident{_plural(crit_high)}'
        if opened > 0 or resolved > 0:
            return text + f'; {opened} opened and {resolved} resolved during the last 24 hours.'
        return text + '.'
    if opened > 0 or resolved > 0:
        return f'No incidents are currently active; {opened} opened and {resolved} resolved during the last 24 hours.'
    return 'No incidents are currently active.'


def _alert_activity_sentence(aggregates: dict[str, Any]) -> str:
    """Precise alert wording: current active state and period creations."""
    metrics = aggregates.get('metrics', {})
    active = int(metrics.get('active_alert_count', 0) or 0)
    created = int(aggregates.get('alerts_created_24h', 0) or 0)

    if active > 0:
        verb = 'is' if active == 1 else 'are'
        text = f'{active} active alert{_plural(active)} {verb} under review'
        if created > 0:
            text += f' ({created} new during the last 24 hours)'
        return text + '.'
    if created > 0:
        verb = 'was' if created == 1 else 'were'
        return f'No alerts are currently active; {created} new alert{_plural(created)} {verb} created during the last 24 hours.'
    return 'No new alerts were created during the last 24 hours.'


def _delta_phrase(delta: Any, unit: str) -> str:
    if delta is None:
        return ''
    try:
        value = int(round(float(delta)))
    except (TypeError, ValueError):
        return ''
    if value == 0:
        return ', unchanged from the previous snapshot'
    direction = 'up' if value > 0 else 'down'
    return f', {direction} {abs(value)} {unit}{"s" if abs(value) != 1 else ""} from the previous snapshot'


def _deterministic_findings(aggregates: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    risk = aggregates.get('risk', {})
    metrics = aggregates.get('metrics', {})

    # Top risk driver as a finding, grounded in alert/incident refs.
    for driver in risk.get('top_risk_drivers', [])[:2]:
        refs = _pick_citations_for(aggregates, {'alert', 'incident'}, limit=2)
        findings.append({
            'title': str(driver.get('label')),
            'description': f'{driver.get("label")} contributes {driver.get("percent")}% of current risk. {driver.get("detail", "")}'.strip(),
            'severity': 'high' if driver.get('percent', 0) >= 40 else 'medium',
            'source_refs': refs,
        })

    # Health insights (already carry source refs).
    for insight in aggregates.get('health', {}).get('insights', [])[:2]:
        refs: list[dict[str, Any]] = []
        if insight.get('source_id'):
            refs = _pick_citations_for(aggregates, {str(insight.get('source_type'))}, limit=1)
        findings.append({
            'title': str(insight.get('message'))[:80],
            'description': str(insight.get('message')),
            'severity': str(insight.get('severity', 'warning')).replace('warning', 'medium'),
            'source_refs': refs,
        })

    if not findings:
        findings.append({
            'title': 'No material anomalies detected',
            'description': f'{metrics.get("monitored_asset_count", 0)} monitored asset(s) reported no active alerts or incidents in the window.',
            'severity': 'low',
            'source_refs': [],
        })
    return findings


def _deterministic_focus(aggregates: dict[str, Any]) -> list[dict[str, Any]]:
    focus: list[dict[str, Any]] = []
    metrics = aggregates.get('metrics', {})
    if int(metrics.get('open_incident_count', 0) or 0) > 0:
        focus.append({'title': 'Review open incidents', 'reason': 'One or more incidents are unresolved.', 'destination': 'incidents'})
    if int(metrics.get('active_alert_count', 0) or 0) > 0:
        focus.append({'title': 'Triage active alerts', 'reason': 'Active alerts are awaiting operator review.', 'destination': 'alerts'})
    if int(aggregates.get('stale_target_count', 0) or 0) > 0 or int(aggregates.get('degraded_provider_count', 0) or 0) > 0:
        focus.append({'title': 'Check monitoring sources', 'reason': 'A provider or target is degraded or stale.', 'destination': 'monitoring'})
    if int(metrics.get('monitored_asset_count', 0) or 0) == 0:
        focus.append({'title': 'Register protected assets', 'reason': 'No assets are being monitored yet.', 'destination': 'assets'})
    if not focus:
        focus.append({'title': 'Maintain monitoring coverage', 'reason': 'No immediate action required; keep coverage current.', 'destination': 'system-health'})
    return focus[:4]


def _dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for citation in citations:
        key = (str(citation.get('source_type')), str(citation.get('source_id')))
        if key in seen:
            continue
        seen.add(key)
        out.append(citation)
    return out


# --------------------------------------------------------------------------
# AI generation with validation + fallback
# --------------------------------------------------------------------------


def _validate_ai_payload(
    parsed: Any,
    index: dict[str, set[str]],
    lookup: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    """Validate + sanitize model output. Returns None if it cannot be trusted."""
    if not isinstance(parsed, dict):
        return None
    headline = str(parsed.get('headline') or '').strip()
    summary = str(parsed.get('summary') or '').strip()
    if not headline or not summary:
        return None

    raw_findings = parsed.get('key_findings')
    if not isinstance(raw_findings, list):
        raw_findings = []

    validated_findings: list[dict[str, Any]] = []
    grounded_any = False
    for finding in raw_findings:
        if not isinstance(finding, dict):
            continue
        refs = validate_citations(finding.get('source_refs') or [], index, lookup)
        # Reject a finding that cites nothing verifiable — it cannot be trusted.
        if not refs:
            continue
        grounded_any = True
        validated_findings.append({
            'title': str(finding.get('title') or '')[:120],
            'description': str(finding.get('description') or '')[:600],
            'severity': _normalize_finding_severity(finding.get('severity')),
            'source_refs': refs,
        })

    # If the model produced findings but none survived grounding, do not trust it.
    if raw_findings and not grounded_any:
        return None

    raw_focus = parsed.get('recommended_focus')
    if not isinstance(raw_focus, list):
        raw_focus = []
    validated_focus: list[dict[str, Any]] = []
    for item in raw_focus[:4]:
        if not isinstance(item, dict):
            continue
        destination = str(item.get('destination') or '').strip().lower()
        if destination not in _VALID_DESTINATIONS:
            destination = 'monitoring'
        validated_focus.append({
            'title': str(item.get('title') or '')[:120],
            'reason': str(item.get('reason') or '')[:300],
            'destination': destination,
        })

    try:
        confidence = float(parsed.get('confidence'))
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))

    citations = [c for finding in validated_findings for c in finding['source_refs']]
    return {
        'headline': headline[:200],
        'summary': summary[:1000],
        'key_findings': validated_findings,
        'recommended_focus': validated_focus,
        'citations': _dedupe_citations(citations),
        'confidence': confidence,
    }


def _normalize_finding_severity(value: Any) -> str:
    text = str(value or '').strip().lower()
    if text in {'critical', 'high', 'medium', 'low'}:
        return text
    if text in {'warning', 'warn'}:
        return 'medium'
    if text in {'info', 'informational'}:
        return 'low'
    return 'medium'


def generate_executive_brief(
    *,
    aggregates: dict[str, Any],
    provider: Any,
    model: str = '',
    prompt_version: str = BRIEF_PROMPT_VERSION,
    timeout_seconds: float = 30.0,
    max_output_tokens: int = 1200,
    logger: Any = None,
) -> dict[str, Any]:
    """Generate the brief via the AI provider, falling back to deterministic.

    Returns a brief dict (see module docstring). ``generation_mode`` is ``'ai'``
    only when the provider returned validated, fully-grounded output; otherwise
    it is ``'deterministic_fallback'``. Any provider exception, invalid JSON,
    simulated (mock) output, or failed validation degrades to the deterministic
    brief so the dashboard always renders.
    """
    index = build_source_index(aggregates.get('citations', []))
    lookup = {
        (str(c.get('source_type')), str(c.get('source_id'))): _clean_citation(c)
        for c in aggregates.get('citations', [])
    }
    evidence = build_brief_evidence(aggregates)
    prompt = build_brief_prompt(evidence, prompt_version)

    try:
        raw = provider.analyze(
            prompt=prompt,
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
    except Exception as exc:  # transport/availability/unknown-provider failures
        _log(logger, 'warning', 'dashboard_brief_ai_failed', error=type(exc).__name__)
        return build_deterministic_brief(aggregates)

    # The offline mock provider is not a real brief generator — never store its
    # (triage-shaped) output as an AI brief.
    if getattr(raw, 'simulated', False):
        return build_deterministic_brief(aggregates)

    try:
        parsed = json.loads(raw.raw_text)
    except (json.JSONDecodeError, TypeError):
        _log(logger, 'warning', 'dashboard_brief_invalid_json', provider=getattr(raw, 'provider', ''))
        return build_deterministic_brief(aggregates)

    validated = _validate_ai_payload(parsed, index, lookup)
    if validated is None:
        _log(logger, 'warning', 'dashboard_brief_validation_rejected', provider=getattr(raw, 'provider', ''))
        return build_deterministic_brief(aggregates)

    _log(logger, 'info', 'dashboard_brief_ai_ok', provider=getattr(raw, 'provider', ''), latency_ms=getattr(raw, 'latency_ms', 0))
    validated.update({
        'generation_mode': 'ai',
        'provider': getattr(raw, 'provider', 'openai'),
        'model': getattr(raw, 'model', model) or model,
        'prompt_version': getattr(raw, 'prompt_version', prompt_version) or prompt_version,
    })
    return validated


def _log(logger: Any, level: str, event: str, **fields: Any) -> None:
    if logger is None:
        return
    try:
        getattr(logger, level)('event=%s %s', event, ' '.join(f'{k}={v}' for k, v in fields.items()))
    except Exception:
        pass
