"""Narrow model-provider abstraction for AI incident triage.

The domain layer (``ai_triage``) stays provider-neutral: it builds a trusted,
server-selected evidence snapshot, hands it to a provider implementing
``IncidentTriageProvider.analyze``, and validates whatever raw text comes back.
No provider is called directly from a route handler.

Two providers ship here:

* ``MockTriageProvider`` — deterministic, offline, grounded. Used by every unit
  and integration test (and as the default when ``AI_PROVIDER`` is unset), so
  tests never depend on live model output or a network call.
* ``AnthropicTriageProvider`` — the initial concrete provider. Calls the
  Anthropic Messages API with strict-JSON instructions and hard timeouts. The
  ``anthropic``/``httpx`` clients are intentionally NOT imported at module load;
  the call uses the stdlib ``urllib`` so importing this module never requires a
  network client to be installed (keeping the test harness import-safe).

Security invariants enforced regardless of provider:
  * The provider only ever receives the server-built evidence snapshot plus the
    agent policy — never a user-controlled system prompt, DB handle, or tool.
  * Evidence content is embedded as clearly-fenced UNTRUSTED DATA, never as
    system-level instructions (see ``ai_triage.build_prompt``).
  * API keys are read from the environment inside the provider and never logged.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


class TriageProviderError(Exception):
    """Raised for provider transport/availability failures (timeout, 5xx, network).

    Carries a stable ``error_code`` the job lifecycle maps onto a safe failed
    state. Never contains a raw provider response body or secret.
    """

    def __init__(self, message: str, *, error_code: str = 'provider_error', retryable: bool = True):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@dataclass
class ProviderRawResult:
    """Raw, still-unvalidated provider output plus operational metadata.

    ``raw_text`` is the model's verbatim response (expected to be a single JSON
    object). The domain layer parses + schema-validates it; this object never
    contains hidden reasoning or chain-of-thought.
    """

    raw_text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    prompt_version: str = ''
    extra: dict[str, Any] = field(default_factory=dict)


class IncidentTriageProvider(Protocol):
    name: str

    def analyze(
        self,
        *,
        prompt: dict[str, str],
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> ProviderRawResult:
        ...


# ---------------------------------------------------------------------------
# Deterministic mock provider
# ---------------------------------------------------------------------------
class MockTriageProvider:
    """Offline, deterministic provider that returns a grounded structured result.

    It reads ONLY the evidence embedded in the user prompt payload (passed as a
    parsed dict on ``prompt['evidence']``) and echoes grounded references, so the
    validator's grounding checks pass. It deliberately ignores any instruction
    text embedded in evidence fields (the prompt-injection defense is proven by
    this indifference). Token counts are derived from payload size so budget and
    usage-accounting tests have stable, non-zero numbers.
    """

    name = 'mock'

    def analyze(
        self,
        *,
        prompt: dict[str, str],
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> ProviderRawResult:
        started = time.monotonic()
        snapshot = prompt.get('evidence_obj') or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = {}
        result = _deterministic_result_from_snapshot(snapshot)
        raw_text = json.dumps(result, separators=(',', ':'))
        latency_ms = int((time.monotonic() - started) * 1000)
        # Stable, size-derived token estimates (never a real tokenizer here).
        input_tokens = max(1, len(prompt.get('user', '')) // 4)
        output_tokens = max(1, len(raw_text) // 4)
        return ProviderRawResult(
            raw_text=raw_text,
            provider=self.name,
            model=model or 'mock-deterministic',
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            prompt_version=prompt.get('prompt_version', ''),
        )


def _deterministic_result_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a valid, fully-grounded triage result object from a snapshot dict."""
    incident_id = str(snapshot.get('incident_id') or '')
    alert = snapshot.get('alert') or {}
    rule = snapshot.get('rule') or {}
    telemetry = snapshot.get('telemetry') or []
    rule_id = str(rule.get('rule_id') or '')
    severity = str(alert.get('severity') or 'medium').lower()

    telemetry_refs: list[str] = []
    affected: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    seen_wallets: set[str] = set()
    for row in telemetry:
        tid = str(row.get('telemetry_id') or '')
        if not tid:
            continue
        ref = f'telemetry:{tid}'
        telemetry_refs.append(ref)
        observed_at = row.get('observed_at')
        timeline.append({
            'timestamp': observed_at,
            'event': f"{row.get('event_type') or 'telemetry_event'} observed via {row.get('detected_by') or 'unknown'}",
            'evidence_refs': [ref],
        })
        for role in ('from', 'to'):
            wallet = row.get(role)
            if wallet and str(wallet).lower() not in seen_wallets:
                seen_wallets.add(str(wallet).lower())
                affected.append({'type': 'wallet', 'value': str(wallet), 'evidence_refs': [ref]})

    rule_ref = f'rule:{rule_id}' if rule_id else None
    finding_refs = ([rule_ref] if rule_ref else []) + telemetry_refs[:1]
    risk_findings: list[dict[str, Any]] = []
    if finding_refs:
        risk_findings.append({
            'title': f"Deterministic rule '{rule.get('name') or rule_id or 'rule'}' triggered",
            'description': (
                'A monitored wallet transfer matched the deterministic detection rule. '
                'This is a rule result and observed fact; no exploit is inferred from the transfer alone.'
            ),
            'confidence': 0.6,
            'evidence_refs': finding_refs,
        })

    citations: list[dict[str, Any]] = []
    if rule_ref:
        citations.append({'ref': rule_ref, 'description': 'Deterministic rule that triggered the alert.'})
    for ref in telemetry_refs:
        citations.append({'ref': ref, 'description': 'Telemetry event observed on-chain.'})

    missing_information: list[str] = []
    if not telemetry:
        missing_information.append('No telemetry events were present in the evidence snapshot.')

    recommended_actions: list[dict[str, Any]] = [{
        'action_type': 'notify_security_team',
        'reason': 'Route the confirmed detection to the security team for human review.',
        'risk_level': 'low',
        'requires_human_approval': True,
        'evidence_refs': (telemetry_refs[:1] or ([rule_ref] if rule_ref else [])),
    }]

    return {
        'schema_version': '1.0',
        'incident_id': incident_id,
        'summary': (
            'A monitored wallet transfer produced a deterministic alert and this incident. '
            'Findings below are grounded only in the supplied evidence snapshot.'
        ),
        'reason_triggered': str(rule.get('description') or rule.get('name') or 'Deterministic monitoring rule matched the telemetry event.'),
        'severity_assessment': {
            'recommended_severity': severity if severity in {'low', 'medium', 'high', 'critical'} else 'medium',
            'confidence': 0.6,
            'reason': 'Mirrors the deterministic alert severity; not an autonomous override.',
        },
        'affected_entities': affected,
        'timeline': timeline,
        'risk_findings': risk_findings,
        'missing_information': missing_information,
        'recommended_runbook_id': 'notify_security_team_v1',
        'recommended_actions': recommended_actions,
        'citations': citations,
    }


# ---------------------------------------------------------------------------
# Failing provider (test aid for provider-unavailable / timeout paths)
# ---------------------------------------------------------------------------
class FailingTriageProvider:
    """Always raises ``TriageProviderError`` — models a provider outage/timeout."""

    name = 'failing'

    def __init__(self, *, error_code: str = 'provider_timeout', retryable: bool = True):
        self._error_code = error_code
        self._retryable = retryable

    def analyze(self, **_kwargs: Any) -> ProviderRawResult:
        raise TriageProviderError('Simulated provider failure.', error_code=self._error_code, retryable=self._retryable)


# ---------------------------------------------------------------------------
# Anthropic provider (initial concrete implementation)
# ---------------------------------------------------------------------------
class AnthropicTriageProvider:
    """Calls the Anthropic Messages API and returns the raw JSON text.

    Uses the stdlib ``urllib`` so importing this module never pulls a network
    client. The API key is read from ``AI_API_KEY`` (or ``ANTHROPIC_API_KEY``) at
    call time and never logged. Provider/HTTP error bodies are collapsed to a
    stable ``TriageProviderError`` code; the raw body is not surfaced to callers.
    """

    name = 'anthropic'
    _API_URL = 'https://api.anthropic.com/v1/messages'
    _API_VERSION = '2023-06-01'

    def analyze(
        self,
        *,
        prompt: dict[str, str],
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> ProviderRawResult:
        import os
        import urllib.error
        import urllib.request

        api_key = (os.getenv('AI_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or '').strip()
        if not api_key:
            raise TriageProviderError('AI_API_KEY is not configured.', error_code='missing_api_key', retryable=False)

        body = json.dumps({
            'model': model or 'claude-opus-4-8',
            'max_tokens': int(max_output_tokens),
            'system': prompt.get('system', ''),
            'messages': [{'role': 'user', 'content': prompt.get('user', '')}],
        }).encode('utf-8')
        req = urllib.request.Request(self._API_URL, data=body, method='POST')
        req.add_header('content-type', 'application/json')
        req.add_header('x-api-key', api_key)
        req.add_header('anthropic-version', self._API_VERSION)

        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            code = 'provider_rate_limited' if exc.code == 429 else (
                'provider_unavailable' if exc.code >= 500 else 'provider_bad_request'
            )
            # Do NOT include exc body — it may echo provider internals.
            raise TriageProviderError(
                f'Anthropic API returned HTTP {exc.code}.',
                error_code=code,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TriageProviderError('Anthropic API request failed.', error_code='provider_timeout', retryable=True) from None
        latency_ms = int((time.monotonic() - started) * 1000)

        text_parts = [
            block.get('text', '')
            for block in (payload.get('content') or [])
            if isinstance(block, dict) and block.get('type') == 'text'
        ]
        raw_text = ''.join(text_parts).strip()
        usage = payload.get('usage') or {}
        return ProviderRawResult(
            raw_text=raw_text,
            provider=self.name,
            model=str(payload.get('model') or model),
            input_tokens=int(usage.get('input_tokens') or 0),
            output_tokens=int(usage.get('output_tokens') or 0),
            latency_ms=latency_ms,
            prompt_version=prompt.get('prompt_version', ''),
        )


_PROVIDERS: dict[str, Any] = {
    'mock': MockTriageProvider,
    'anthropic': AnthropicTriageProvider,
}


def get_triage_provider(name: str | None) -> IncidentTriageProvider:
    """Return a provider instance by name. Unknown/empty -> deterministic mock.

    Keeping the fallback on the mock means a misconfigured ``AI_PROVIDER`` never
    silently reaches a live API; the configuration warning surfaces separately.
    """
    key = (name or '').strip().lower()
    factory = _PROVIDERS.get(key, MockTriageProvider)
    return factory()
