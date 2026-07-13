"""Narrow model-provider abstraction for AI incident triage.

The domain layer (``ai_triage``) stays provider-neutral: it builds a trusted,
server-selected evidence snapshot, hands it to a provider implementing
``IncidentTriageProvider.analyze``, and validates whatever raw text comes back.
No provider is called directly from a route handler.

Providers that ship here:

* ``MockTriageProvider`` — deterministic, offline, grounded. Used by every unit
  and integration test (and as the default when ``AI_PROVIDER`` is empty), so
  tests never depend on live model output or a network call.
* ``OpenAITriageProvider`` — the initial PRODUCTION provider. Calls the official
  OpenAI Python SDK's Responses API with a strict JSON Schema structured output,
  an explicit per-request timeout, and bounded exponential-backoff retries. The
  ``openai`` SDK is intentionally imported lazily inside the call, so importing
  this module never requires the SDK to be installed (keeping the test harness
  import-safe) and no automated test ever touches the real OpenAI API.
* ``AnthropicTriageProvider`` — retained, optional. Uses the stdlib ``urllib`` so
  importing this module never pulls a network client.

Unknown provider names fail closed (``_UnknownTriageProvider`` raises a
non-retryable configuration error) — a misconfigured ``AI_PROVIDER`` never
silently produces a completed result.

Security invariants enforced regardless of provider:
  * The provider only ever receives the server-built evidence snapshot plus the
    agent policy — never a user-controlled system prompt, DB handle, or tool. No
    tools, web browsing, shell, SQL, wallet signing, contract calls, or arbitrary
    URL fetching are ever enabled on the model call.
  * Evidence content is embedded as clearly-fenced UNTRUSTED DATA, never as
    system-level instructions (see ``ai_triage.build_prompt``).
  * API keys are read from the environment inside the provider and never logged.
  * Only the validated structured result text and operational metadata are
    returned — never hidden reasoning / chain-of-thought (``store=False`` and
    reasoning items are never persisted or surfaced).
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
    # True when the output is deterministic/offline (the mock provider), not a real
    # model call. The domain layer uses this to keep cost at exactly 0 and to never
    # apply live pricing or surface a live model name for a synthetic result.
    simulated: bool = False
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
    this indifference).

    Truthful synthetic metadata (task requirement): a mock run is NOT a real model
    call, so it always reports ``provider='mock'`` and ``model='mock'`` (never the
    configured live ``AI_MODEL_TRIAGE`` value such as an OpenAI model name), zero
    token usage, and ``simulated=True`` so the domain layer keeps cost at exactly 0
    and never applies live pricing. Latency stays deterministic/observable.
    """

    name = 'mock'
    # Canonical model label for a synthetic run. Never the live AI_MODEL_TRIAGE.
    MODEL_LABEL = 'mock'

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
        # A mock run consumes no real tokens: report zero and mark the result
        # simulated. The domain layer forces estimated_cost_usd to 0 for a simulated
        # result and applies no OpenAI pricing. The configured ``model`` argument is
        # intentionally ignored so a mock run is never mislabeled with a live model.
        return ProviderRawResult(
            raw_text=raw_text,
            provider=self.name,
            model=self.MODEL_LABEL,
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            prompt_version=prompt.get('prompt_version', ''),
            simulated=True,
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
    for missing in snapshot.get('missing_information') or []:
        detail = missing.get('detail') if isinstance(missing, dict) else missing
        if detail:
            missing_information.append(str(detail))

    # Truthful summary: without telemetry there is no factual transfer to conclude
    # from, so the deterministic result states insufficient evidence (and emits no
    # cited factual findings) rather than asserting a transfer occurred.
    if telemetry:
        summary = (
            'A monitored wallet transfer produced a deterministic alert and this incident. '
            'Findings below are grounded only in the supplied evidence snapshot.'
        )
    else:
        summary = (
            'Insufficient evidence: no telemetry event could be resolved for this incident, '
            'so no factual transfer conclusion is asserted. See missing information.'
        )

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
        'summary': summary,
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


# ---------------------------------------------------------------------------
# OpenAI provider (initial production implementation, Responses API)
# ---------------------------------------------------------------------------
_OPENAI_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _classify_openai_error(exc: Exception) -> tuple[str, bool]:
    """Map an OpenAI SDK exception to a stable (error_code, retryable) pair.

    Duck-typed on ``status_code`` and the exception class name so this module
    never needs to import the ``openai`` package (kept import-safe / test-safe).
    The raw exception message/body is deliberately NOT propagated to callers.
    """
    status_code = getattr(exc, 'status_code', None)
    if status_code is None:
        status_code = getattr(exc, 'status', None)
    name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or 'timeout' in name:
        return 'provider_timeout', True
    if status_code == 429 or 'ratelimit' in name:
        return 'provider_rate_limited', True
    if isinstance(status_code, int) and status_code >= 500:
        return 'provider_unavailable', True
    if 'connection' in name:  # APIConnectionError / connection reset
        return 'provider_unavailable', True
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return 'provider_bad_request', False
    # Unknown failure: fail closed, do not retry blindly.
    return 'provider_error', False


def _openai_output_text(response: Any) -> str:
    """Extract ONLY the final structured text from a Responses API result.

    Never returns reasoning / chain-of-thought items — only ``output_text`` /
    ``output_text`` content blocks. Works with both the real SDK object and a
    plain dict/namespace (used by the mocked-client tests).
    """
    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    direct = _get(response, 'output_text')
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in (_get(response, 'output') or []):
        for part in (_get(item, 'content') or []):
            if _get(part, 'type') in ('output_text', 'text'):
                text = _get(part, 'text')
                if isinstance(text, str):
                    chunks.append(text)
    return ''.join(chunks).strip()


def _openai_usage(response: Any) -> tuple[int, int]:
    usage = response.get('usage') if isinstance(response, dict) else getattr(response, 'usage', None)
    if usage is None:
        return 0, 0

    def _u(key: str) -> int:
        val = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0

    return _u('input_tokens'), _u('output_tokens')


class OpenAITriageProvider:
    """Calls the OpenAI Responses API and returns the raw structured JSON text.

    * Model comes from ``AI_MODEL_TRIAGE`` (passed in as ``model``).
    * API key is read from ``AI_API_KEY`` (canonical) or ``OPENAI_API_KEY`` at
      call time and never logged.
    * Structured output is enforced with a strict JSON Schema (the
      ``IncidentTriageResult`` contract) supplied on the prompt.
    * The call is stateless (``store=False``), tool-free, and reasoning is never
      requested or surfaced — only the final structured text is returned.
    * Retryable transport errors (timeout / 429 / 5xx / connection) are retried
      with bounded exponential backoff; on exhaustion a stable
      ``TriageProviderError`` is raised (no provider body/secret in the message).

    ``client`` (a pre-built OpenAI client) and ``sleep`` are injectable so unit
    tests exercise every path with a mock — no automated test calls the real API.
    """

    name = 'openai'

    def __init__(self, *, client: Any = None, max_attempts: int = 3,
                 backoff_base_seconds: float = 0.5, sleep: Any = None):
        self._client = client
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base = float(backoff_base_seconds)
        self._sleep = sleep or time.sleep

    def _resolve_client(self, timeout_seconds: float) -> Any:
        if self._client is not None:
            return self._client
        import os
        api_key = (os.getenv('AI_API_KEY') or os.getenv('OPENAI_API_KEY') or '').strip()
        if not api_key:
            raise TriageProviderError(
                'AI_API_KEY (or OPENAI_API_KEY) is not configured for the OpenAI provider.',
                error_code='missing_api_key', retryable=False,
            )
        try:
            from openai import OpenAI  # lazy: importing this module must not need the SDK
        except Exception:
            raise TriageProviderError(
                'The openai SDK is not installed; AI_PROVIDER=openai cannot run.',
                error_code='provider_sdk_missing', retryable=False,
            ) from None
        # max_retries=0: we own retry/backoff so it is bounded and observable.
        return OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)

    def analyze(
        self,
        *,
        prompt: dict[str, str],
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> ProviderRawResult:
        model_name = (model or '').strip()
        if not model_name:
            raise TriageProviderError(
                'AI_MODEL_TRIAGE is not configured for the OpenAI provider.',
                error_code='missing_model', retryable=False,
            )
        client = self._resolve_client(timeout_seconds)

        schema = prompt.get('json_schema')
        if schema:
            text_format = {'format': {
                'type': 'json_schema',
                'name': prompt.get('json_schema_name') or 'incident_triage_result',
                'schema': schema,
                'strict': True,
            }}
        else:  # defensive: still force a JSON object even without the schema
            text_format = {'format': {'type': 'json_object'}}

        request_kwargs = {
            'model': model_name,
            'input': [
                {'role': 'system', 'content': prompt.get('system', '')},
                {'role': 'user', 'content': prompt.get('user', '')},
            ],
            'text': text_format,
            'max_output_tokens': int(max_output_tokens),
            # No tools / web browsing / code interpreter / file search are enabled.
            'store': False,  # stateless: never persist prompt or hidden reasoning.
            'timeout': timeout_seconds,
        }

        started = time.monotonic()
        for attempt in range(self._max_attempts):
            try:
                response = client.responses.create(**request_kwargs)
            except TriageProviderError:
                raise
            except Exception as exc:  # noqa: BLE001 - classify + collapse, never leak body
                error_code, retryable = _classify_openai_error(exc)
                if retryable and attempt < self._max_attempts - 1:
                    self._sleep(self._backoff_base * (2 ** attempt))
                    continue
                raise TriageProviderError(
                    f'OpenAI Responses API request failed ({error_code}).',
                    error_code=error_code, retryable=retryable,
                ) from None

            latency_ms = int((time.monotonic() - started) * 1000)
            raw_text = _openai_output_text(response)
            input_tokens, output_tokens = _openai_usage(response)
            resolved_model = response.get('model') if isinstance(response, dict) else getattr(response, 'model', None)
            return ProviderRawResult(
                raw_text=raw_text,
                provider=self.name,
                model=str(resolved_model or model_name),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                prompt_version=prompt.get('prompt_version', ''),
            )
        # Unreachable: the loop either returns or raises.
        raise TriageProviderError('OpenAI Responses API request failed.', error_code='provider_error', retryable=False)


class _UnknownTriageProvider:
    """Fail-closed provider for an unrecognized ``AI_PROVIDER`` value.

    Returned by ``get_triage_provider`` for any non-empty, unknown name so the
    triage job lands in a safe ``failed`` state (error_code ``unknown_provider``)
    instead of crashing the worker or silently reaching a live API.
    """

    def __init__(self, name: str):
        self.name = name

    def analyze(self, **_kwargs: Any) -> ProviderRawResult:
        raise TriageProviderError(
            f'Configured AI_PROVIDER "{self.name}" is not a recognized provider.',
            error_code='unknown_provider', retryable=False,
        )


_PROVIDERS: dict[str, Any] = {
    'mock': MockTriageProvider,
    'openai': OpenAITriageProvider,
    'anthropic': AnthropicTriageProvider,
}


def get_triage_provider(name: str | None) -> IncidentTriageProvider:
    """Return a provider instance by name.

    * empty/unset -> deterministic offline mock (safe default; no network).
    * ``mock`` / ``openai`` / ``anthropic`` -> the concrete provider.
    * anything else -> a fail-closed provider whose ``analyze`` raises a
      non-retryable ``unknown_provider`` error (never silently uses the mock,
      never reaches a live API).
    """
    key = (name or '').strip().lower()
    if not key:
        return MockTriageProvider()
    factory = _PROVIDERS.get(key)
    if factory is None:
        return _UnknownTriageProvider(key)
    return factory()
