"""Central ingestion-time privacy and secret-redaction boundary.

Why this module exists
----------------------
Decoda's telemetry today is overwhelmingly PUBLIC on-chain data, and the paths
that carry it (QuickNode Streams, stable RPC polling, the realtime WebSocket
family) build their payloads field-by-field from normalized chain facts rather
than storing whatever arrived. That is genuinely low risk, and this module does
not pretend otherwise.

The risk is at the edges where an *arbitrary* value reaches persistence:

  * ``/pilot/threat/analyze/*`` and ``/pilot/compliance/*`` accept a caller-shaped
    JSON body and persist it verbatim into ``analysis_runs.request_payload``
    (and, for the threat routes, into ``metadata.original_ui_request``).
  * Provider/transport exception text is persisted into
    ``monitored_systems.last_error_text`` and ``monitoring_polls.error_message``
    and is rendered to the customer. An RPC URL with an embedded API key is a
    normal thing for such a message to contain.
  * Anything that is about to leave Decoda for an external AI provider.

and where a future private/off-chain connector would land. Rather than adding a
fourth ad-hoc scrubber, every one of those callers now passes through
:func:`sanitize_ingested_payload` (or :func:`sanitize_for_ai` /
:func:`sanitize_error_text`) BEFORE the write or the forward — not on read, and
not only when rendering a log line.

The contract
------------
::

    SOURCE -> validation -> classification -> redaction -> safe payload -> persistence -> AI / alerting / evidence / logs

:func:`sanitize_ingested_payload` returns a :class:`SanitizationResult` carrying
the sanitized payload plus metadata describing WHAT was removed — rule ids and
field paths, never the original values. A caller that has the result never needs
the original sensitive value again, and nothing here ever logs, returns, or
stores it.

Evidentiary truthfulness
------------------------
"Redacted by privacy policy" and "absent in the source" are different facts and
stay different in the output:

  * a redacted field KEEPS its key with the :data:`REDACTED` sentinel as its
    value, and its path is listed in ``redacted_fields``;
  * a dropped field is REMOVED and its path is listed in ``dropped_fields``;
  * a field that was never present appears in neither list.

Public-chain forensic fields (:data:`PUBLIC_CHAIN_FIELDS`) are never touched by
value-shape detection: a 32-byte transaction hash and a private key are both
64 hex characters, and destroying tx hashes to catch a key Decoda does not ingest
would trade real forensic value for an imaginary gain.

Policy order
------------
Mandatory Decoda rules run first and cannot be disabled. A workspace policy may
then make redaction STRICTER; it can never make it weaker (see
:meth:`PrivacyPolicy.from_settings`).

This module is deliberately framework-independent: no FastAPI, no database, no
network. It is imported by request handlers, by workers, and by tests alike.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
from urllib import parse as _urlparse

# ---------------------------------------------------------------------------
# Sensitivity classes (Phase 2)
# ---------------------------------------------------------------------------
#: Public blockchain facts. Required for investigation; preserved verbatim.
PUBLIC_CHAIN_DATA = 'PUBLIC_CHAIN_DATA'
#: Customer-private operational metadata (internal service/host/env names).
CUSTOMER_OPERATIONAL_METADATA = 'CUSTOMER_OPERATIONAL_METADATA'
#: Personal data. A public wallet address is NOT PII here — see module docstring.
PII = 'PII'
#: Authentication/authorization material. Never persisted, never forwarded.
CREDENTIAL = 'CREDENTIAL'
#: Network identifiers (private IPs, loopback, link-local, internal hostnames).
NETWORK_IDENTIFIER = 'NETWORK_IDENTIFIER'
#: Arbitrary text: error messages, log lines, operator notes.
FREEFORM_TEXT = 'FREEFORM_TEXT'

SENSITIVITY_CLASSES: tuple[str, ...] = (
    PUBLIC_CHAIN_DATA,
    CUSTOMER_OPERATIONAL_METADATA,
    PII,
    CREDENTIAL,
    NETWORK_IDENTIFIER,
    FREEFORM_TEXT,
)

# ---------------------------------------------------------------------------
# Sentinels. A caller can tell these apart from a value that was simply absent.
# ---------------------------------------------------------------------------
REDACTED = '[REDACTED]'
REDACTED_DEPTH = '[REDACTED_DEPTH_LIMIT]'
TRUNCATED_SUFFIX = '…[TRUNCATED]'


# ---------------------------------------------------------------------------
# Bounds. A malformed or hostile payload must cost bounded time and memory.
# ---------------------------------------------------------------------------
MAX_DEPTH = 12
MAX_CONTAINER_ITEMS = 500
MAX_STRING_CHARS = 8192
MAX_TOTAL_FIELDS = 5000


# ---------------------------------------------------------------------------
# Source types and their documented failure behavior (Phase 16)
# ---------------------------------------------------------------------------
#: Public on-chain telemetry (QuickNode Streams, stable RPC polling, realtime WS).
SOURCE_PUBLIC_CHAIN = 'public_chain'
#: A caller-shaped request body persisted as a workspace record.
SOURCE_CUSTOMER_REQUEST = 'customer_request'
#: Provider/transport error text destined for persistence or the customer UI.
SOURCE_INTEGRATION_ERROR = 'integration_error'
#: Context assembled for an external AI provider.
SOURCE_AI_CONTEXT = 'ai_context'
#: Future private/off-chain connector telemetry. None ships today.
SOURCE_PRIVATE_OFFCHAIN = 'private_offchain'

#: Source types that REFUSE ingestion when the filter itself errors, rather than
#: persisting anything unfiltered. Public-chain sources are deliberately absent:
#: refusing them would take live monitoring down over a filter bug, so they
#: degrade to the public-chain allowlist instead (recorded, never silent).
FAIL_CLOSED_SOURCE_TYPES: frozenset[str] = frozenset({
    SOURCE_CUSTOMER_REQUEST,
    SOURCE_AI_CONTEXT,
    SOURCE_PRIVATE_OFFCHAIN,
})

PRIVACY_FILTER_FAILED_CODE = 'INGESTION_PRIVACY_FILTER_FAILED'


class PrivacyFilterError(Exception):
    """The sanitizer could not complete, so ingestion is refused.

    Carries only the stable code and the source type — never the payload, and
    never a fragment of whatever could not be processed.
    """

    def __init__(self, source_type: str, *, code: str = PRIVACY_FILTER_FAILED_CODE):
        super().__init__(code)
        self.code = code
        self.source_type = str(source_type or 'unknown')


# ---------------------------------------------------------------------------
# Phase 4 — mandatory sensitive-key ruleset
# ---------------------------------------------------------------------------
def normalize_key(key: Any) -> str:
    """Fold a field/header name to its comparison form.

    Lowercase, with every non-alphanumeric character removed, so ``API_KEY``,
    ``api-key``, ``apiKey`` and ``Api Key`` all collapse to ``apikey``. Matching
    is then EXACT against a closed set — never a substring scan, which is what
    would otherwise destroy innocent fields like ``secretary`` (→ ``secretary``)
    or ``password_policy_enabled`` (→ ``passwordpolicyenabled``).
    """
    return re.sub(r'[^a-z0-9]', '', str(key or '').lower())


#: Keys whose VALUE is credential material. Stripped regardless of workspace
#: policy: a customer cannot opt back into storing these (Phase 9 policy order).
#: Written in readable form; compared through :func:`normalize_key`, so each
#: entry also covers its dashed / camelCase / spaced spellings.
_MANDATORY_CREDENTIAL_KEYS: tuple[str, ...] = (
    'authorization',
    'proxy_authorization',
    'www_authenticate',
    'cookie',
    'set_cookie',
    'api_key',
    'apikey',
    'x_api_key',
    'x_auth_token',
    'access_token',
    'refresh_token',
    'id_token',
    'bearer',
    'bearer_token',
    'password',
    'passwd',
    'secret',
    'client_secret',
    'webhook_secret',
    'signing_secret',
    'shared_secret',
    'private_key',
    'privatekey',
    'secret_key',
    'seed_phrase',
    'mnemonic',
    'recovery_code',
    'recovery_phrase',
    'session',
    'session_id',
    'session_token',
    'csrf',
    'csrf_token',
    'xsrf_token',
    'auth_token',
    'credential',
    'credentials',
)

MANDATORY_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    normalize_key(name) for name in _MANDATORY_CREDENTIAL_KEYS
)

#: Keys carrying a URL. Their values are sanitized (userinfo, secret query
#: params, fragments) rather than dropped: the host and path shape are needed to
#: diagnose which provider failed.
_URL_KEYS: tuple[str, ...] = (
    'url', 'uri', 'endpoint', 'endpoint_url', 'rpc_url', 'rpc_endpoint',
    'webhook_url', 'callback_url', 'href', 'link', 'target_url', 'base_url',
)
URL_KEYS: frozenset[str] = frozenset(normalize_key(name) for name in _URL_KEYS)

#: Keys whose value is an email address, redacted when policy asks for it.
_EMAIL_KEYS: tuple[str, ...] = ('email', 'email_address', 'contact_email', 'user_email', 'owner_email')
EMAIL_KEYS: frozenset[str] = frozenset(normalize_key(name) for name in _EMAIL_KEYS)

#: Keys whose value is a host or IP, subject to the private-network policy.
_NETWORK_KEYS: tuple[str, ...] = (
    'ip', 'ip_address', 'client_ip', 'source_ip', 'remote_addr', 'remote_ip',
    'host', 'hostname', 'internal_host', 'internal_hostname', 'peer_address',
)
NETWORK_KEYS: frozenset[str] = frozenset(normalize_key(name) for name in _NETWORK_KEYS)


# ---------------------------------------------------------------------------
# Phase 10 — public-chain forensic fields, preserved verbatim
# ---------------------------------------------------------------------------
#: Fields that carry public blockchain facts. Value-shape detection is NEVER run
#: against them, so a 64-hex transaction hash survives intact. They are still
#: subject to explicit CUSTOMER exclusion rules (a workspace may legitimately
#: choose not to retain one), but never to the credential heuristics.
_PUBLIC_CHAIN_FIELDS: tuple[str, ...] = (
    'chain_id', 'chainid', 'network', 'chain_network',
    'block_number', 'block', 'block_hash', 'block_timestamp',
    'transaction_hash', 'tx_hash', 'txhash', 'transaction_index', 'tx_index',
    'contract_address', 'contract_identifier',
    'from_address', 'to_address', 'from', 'to', 'address', 'counterparty',
    'wallet_address', 'token_address', 'token_contract_address',
    'amount', 'value', 'value_wei', 'value_eth', 'amount_text',
    'event_signature', 'event_name', 'event_type', 'log_index',
    'observed_at', 'ingested_at', 'detected_by', 'evidence_source',
    'gas', 'gas_price', 'gas_used', 'nonce', 'status',
)
PUBLIC_CHAIN_FIELDS: frozenset[str] = frozenset(
    normalize_key(name) for name in _PUBLIC_CHAIN_FIELDS
)


# ---------------------------------------------------------------------------
# Phase 5 — secret VALUE shapes
# ---------------------------------------------------------------------------
# Deliberately NOT here: a bare 64-hex string. Every wallet-transfer telemetry
# row Decoda persists carries one as ``tx_hash``; redacting that shape blindly
# would erase the single most important forensic identifier the product has. A
# 0x-prefixed 32-byte value is only treated as key material when its KEY says so
# (``private_key`` and friends are already mandatory-stripped above).
_BEARER_RE = re.compile(r'\b(?:bearer|token)\s+[A-Za-z0-9\-._~+/]{12,}=*', re.IGNORECASE)
_BASIC_AUTH_RE = re.compile(r'\bbasic\s+[A-Za-z0-9+/]{12,}={0,2}', re.IGNORECASE)
_JWT_RE = re.compile(r'\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b')
#: Matches the WHOLE block, not just its header line. The end marker is an
#: alternation with end-of-string so a truncated block — a key pasted without its
#: trailing ``-----END`` line — is still consumed entirely rather than leaving the
#: base64 body behind.
_PEM_RE = re.compile(
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)'
)
#: ``scheme://user:password@host`` — credentials embedded in a URL.
_URL_USERINFO_RE = re.compile(r'\b([a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@')
_URL_RE = re.compile(r'\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s"\'<>\\]+')
_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')

#: A mnemonic is the WHOLE value: exactly a BIP-39 word count of bare lowercase
#: words. Mirrors ``organizations.looks_like_secret`` rather than re-deriving a
#: second, differently-wrong definition — "12+ short words anywhere in the text"
#: matches ordinary prose and would redact real operator notes.
_MNEMONIC_WORD_COUNTS = frozenset({12, 15, 18, 21, 24})
_MNEMONIC_RE = re.compile(r'^[a-z]{3,8}(?: [a-z]{3,8})+$')

#: Query-string keys whose VALUE is a credential. Same closed-set, normalized
#: matching as the field rules.
_SENSITIVE_QUERY_KEYS: tuple[str, ...] = (
    'token', 'key', 'api_key', 'apikey', 'secret', 'signature', 'sig', 'auth',
    'password', 'access_token', 'refresh_token', 'session', 'credential',
)
SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset(
    normalize_key(name) for name in _SENSITIVE_QUERY_KEYS
)


def looks_like_mnemonic(value: str) -> bool:
    """Whether the whole string is a BIP-39-shaped recovery phrase."""
    collapsed = ' '.join(str(value or '').strip().lower().split())
    if not collapsed:
        return False
    return bool(
        _MNEMONIC_RE.fullmatch(collapsed)
        and len(collapsed.split(' ')) in _MNEMONIC_WORD_COUNTS
    )


# ---------------------------------------------------------------------------
# Phase 7 — URL sanitization
# ---------------------------------------------------------------------------
def _looks_like_url_secret_segment(segment: str) -> bool:
    """Whether one URL path segment looks like an embedded key.

    Same shape test ``onboarding_discovery`` already applies to RPC URLs: long,
    dense, alphanumeric-with-digits. Short human segments (``v2``, ``rpc``) stay.
    """
    text = str(segment or '')
    if len(text) < 12:
        return False
    alnum = sum(1 for char in text if char.isalnum())
    return alnum / max(1, len(text)) > 0.8 and any(char.isdigit() for char in text)


def sanitize_url(value: Any) -> tuple[str, list[str]]:
    """A storage/log-safe URL, plus the rule ids that fired.

    Removes userinfo credentials, redacts secret-looking path segments, redacts
    the VALUES of sensitive query parameters (keeping their names, which are
    diagnostic, not secret), and drops the fragment. Everything needed to say
    *which provider and which route* failed survives::

        https://u:p@rpc.example/v1/AbC123KeyMaterial?token=xyz#f
        -> https://rpc.example/v1/[REDACTED]?token=[REDACTED]

    A value that cannot be parsed as a URL degrades to its host, or to
    :data:`REDACTED` when even that is unavailable — never to the raw input.
    """
    raw = str(value or '').strip()
    if not raw:
        return '', []
    rules: list[str] = []
    try:
        parsed = _urlparse.urlsplit(raw)
    except ValueError:
        return REDACTED, ['credential.url_unparseable']
    if not parsed.scheme or not parsed.netloc:
        return raw, []
    if parsed.username or parsed.password:
        rules.append('credential.url_userinfo')
    host = parsed.hostname or ''
    port = f':{parsed.port}' if parsed.port else ''

    segments: list[str] = []
    for segment in (parsed.path or '').split('/'):
        if not segment:
            continue
        if _looks_like_url_secret_segment(segment):
            segments.append(REDACTED)
            if 'credential.url_path_secret' not in rules:
                rules.append('credential.url_path_secret')
        else:
            segments.append(segment)
    path = ('/' + '/'.join(segments)) if segments else ''

    query = ''
    if parsed.query:
        pairs: list[str] = []
        for key, item in _urlparse.parse_qsl(parsed.query, keep_blank_values=True):
            if normalize_key(key) in SENSITIVE_QUERY_KEYS:
                pairs.append(f'{key}={REDACTED}')
                if 'credential.url_query_secret' not in rules:
                    rules.append('credential.url_query_secret')
            else:
                pairs.append(f'{key}={item}')
        query = '?' + '&'.join(pairs)
    if parsed.fragment:
        rules.append('credential.url_fragment_dropped')
    return f'{parsed.scheme}://{host}{port}{path}{query}', rules


# ---------------------------------------------------------------------------
# Phase 8 — private network identifiers
# ---------------------------------------------------------------------------
#: Hostname suffixes that only resolve inside a customer's own network.
_INTERNAL_HOST_SUFFIXES: tuple[str, ...] = (
    '.local', '.internal', '.intranet', '.lan', '.home', '.corp', '.private',
    '.localdomain', '.cluster.local', '.svc', '.svc.cluster.local',
)


def classify_network_identifier(value: Any) -> str | None:
    """The rule id for a private/loopback/link-local/internal host, else ``None``.

    A PUBLIC address or a public provider hostname returns ``None``: Decoda needs
    ``base-mainnet.g.alchemy.com`` to say which RPC provider degraded, and
    redacting it would buy no privacy at all.
    """
    text = str(value or '').strip()
    if not text:
        return None
    candidate = text.strip('[]')
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        lowered = text.lower().rstrip('.')
        if lowered in {'localhost', 'localhost.localdomain'}:
            return 'network.loopback'
        if any(lowered.endswith(suffix) for suffix in _INTERNAL_HOST_SUFFIXES):
            return 'network.internal_hostname'
        return None
    if address.is_loopback:
        return 'network.loopback'
    if address.is_link_local:
        return 'network.link_local'
    if address.is_private:
        return 'network.private_ip'
    return None


# ---------------------------------------------------------------------------
# Phase 15 — deterministic pseudonymization (opt-in)
# ---------------------------------------------------------------------------
#: Dedicated env key. Deliberately NOT any evidence-signing, auth, or secret
#: encryption key: correlating two telemetry rows must never be able to weaken,
#: or be weakened by, a key that authenticates or seals evidence.
PSEUDONYM_KEY_ENV = 'TELEMETRY_PRIVACY_PSEUDONYM_KEY'


def pseudonymize(value: Any, *, scope: str, label: str = 'internal-ip') -> str:
    """A stable, keyed pseudonym for one private identifier.

    Standard HMAC-SHA256 from the standard library — no bespoke construction.
    Same ``scope`` (the workspace) plus same input yields the same pseudonym, so
    an investigator can still correlate events; a different workspace derives a
    different pseudonym because the scope is mixed into the message.

    With no key configured this returns :data:`REDACTED`: an UNKEYED digest of a
    small space like RFC1918 is trivially reversible by enumeration, so emitting
    one would be pseudonymity in name only.
    """
    key = (os.getenv(PSEUDONYM_KEY_ENV) or '').strip()
    if not key:
        return REDACTED
    digest = hmac.new(
        key.encode('utf-8'),
        f'{scope}|{value}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f'{label}:{digest[:16]}'


# ---------------------------------------------------------------------------
# Phase 9 — workspace privacy policy
# ---------------------------------------------------------------------------
#: How a workspace asks for private network identifiers to be handled.
NETWORK_MODE_PRESERVE = 'preserve'
NETWORK_MODE_MASK = 'mask'
NETWORK_MODE_PSEUDONYMIZE = 'pseudonymize'
NETWORK_MODE_DROP = 'drop'
NETWORK_MODES: tuple[str, ...] = (
    NETWORK_MODE_PRESERVE, NETWORK_MODE_MASK, NETWORK_MODE_PSEUDONYMIZE, NETWORK_MODE_DROP,
)

#: The settings a workspace may set. Used by the API layer to validate input and
#: to describe the supported controls to the customer.
POLICY_FIELDS: tuple[str, ...] = (
    'excluded_fields',
    'redacted_fields',
    'allowed_metadata_fields',
    'redact_private_ips',
    'redact_emails',
    'private_network_mode',
)

MAX_POLICY_FIELD_NAMES = 100
MAX_POLICY_FIELD_NAME_CHARS = 200


@dataclass(frozen=True)
class PrivacyPolicy:
    """One workspace's privacy configuration, already validated.

    Every field here can only ADD redaction. There is deliberately no
    "allow_credentials" or "skip_rules" setting to build: the mandatory rules are
    not expressed in this object at all, so no customer value can reach them.
    """

    #: Field names dropped entirely (key removed, path recorded in ``dropped_fields``).
    excluded_fields: frozenset[str] = frozenset()
    #: Field names kept but with :data:`REDACTED` as the value.
    redacted_fields: frozenset[str] = frozenset()
    #: When non-empty, metadata keys outside this allowlist are dropped.
    allowed_metadata_fields: frozenset[str] | None = None
    redact_private_ips: bool = True
    redact_emails: bool = False
    private_network_mode: str = NETWORK_MODE_MASK
    #: Mixed into pseudonyms so one workspace's pseudonyms never match another's.
    scope: str = 'default'

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None, *, scope: str = 'default') -> 'PrivacyPolicy':
        """Build a policy from stored workspace settings, fail-safe.

        Unknown keys are ignored and malformed values fall back to the DEFAULT —
        never to "off". A corrupted settings row therefore degrades to Decoda's
        defaults rather than silently disabling redaction.
        """
        data = settings if isinstance(settings, Mapping) else {}
        mode = str(data.get('private_network_mode') or NETWORK_MODE_MASK).strip().lower()
        if mode not in NETWORK_MODES:
            mode = NETWORK_MODE_MASK
        allowed = data.get('allowed_metadata_fields')
        return cls(
            excluded_fields=_normalized_name_set(data.get('excluded_fields')),
            redacted_fields=_normalized_name_set(data.get('redacted_fields')),
            allowed_metadata_fields=(
                _normalized_name_set(allowed) if isinstance(allowed, (list, tuple, set, frozenset)) and allowed else None
            ),
            redact_private_ips=_as_bool(data.get('redact_private_ips'), default=True),
            redact_emails=_as_bool(data.get('redact_emails'), default=False),
            private_network_mode=mode,
            scope=str(scope or 'default'),
        )

    def stricter(self, **overrides: Any) -> 'PrivacyPolicy':
        """A copy with additional redaction applied. Never relaxes a setting."""
        merged: dict[str, Any] = {
            'excluded_fields': self.excluded_fields,
            'redacted_fields': self.redacted_fields,
            'allowed_metadata_fields': self.allowed_metadata_fields,
            'redact_private_ips': self.redact_private_ips,
            'redact_emails': self.redact_emails,
            'private_network_mode': self.private_network_mode,
            'scope': self.scope,
        }
        for key, value in overrides.items():
            if key in ('redact_private_ips', 'redact_emails'):
                merged[key] = bool(merged[key]) or bool(value)
            elif key in ('excluded_fields', 'redacted_fields'):
                merged[key] = frozenset(merged[key]) | _normalized_name_set(value)
            else:
                merged[key] = value
        return PrivacyPolicy(**merged)  # type: ignore[arg-type]


#: The policy used when a workspace has configured nothing.
DEFAULT_POLICY = PrivacyPolicy()

#: The AI boundary is stricter than storage by construction: email and private
#: network identifiers are always removed before anything leaves Decoda, whatever
#: the workspace chose for its own database rows.
AI_POLICY_OVERRIDES: dict[str, Any] = {'redact_emails': True, 'redact_private_ips': True}


def _as_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'1', 'true', 'yes', 'on'}:
            return True
        if text in {'0', 'false', 'no', 'off'}:
            return False
    return default


def _normalized_name_set(value: Any) -> frozenset[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        names = value
    elif isinstance(value, str):
        names = value.split(',')
    else:
        return frozenset()
    out: set[str] = set()
    for name in list(names)[:MAX_POLICY_FIELD_NAMES]:
        normalized = normalize_key(str(name)[:MAX_POLICY_FIELD_NAME_CHARS])
        if normalized:
            out.add(normalized)
    return frozenset(out)


# ---------------------------------------------------------------------------
# Phase 3 — result model
# ---------------------------------------------------------------------------
@dataclass
class SanitizationResult:
    """The sanitized payload plus a description of what was removed.

    ``redacted_fields`` / ``dropped_fields`` are dotted PATHS, never values;
    ``rule_ids`` are the stable rule identifiers that fired. Nothing in this
    object contains, encodes, or hashes the original sensitive value.
    """

    payload: Any
    redacted_fields: list[str] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)
    changed: bool = False
    source_type: str = ''
    #: True when the sanitizer failed and a fail-open source degraded to the
    #: public-chain allowlist. Never true on a fail-closed source (that raises).
    filter_failed: bool = False

    @property
    def redaction_metadata(self) -> dict[str, Any]:
        """Safe metadata for evidence, audit, and AI operational records.

        This is what Phase 14's ``privacy_processing`` block is built from: counts,
        rule ids, field paths and the source type — never the redacted content.
        """
        return {
            'applied': bool(self.changed),
            'source_type': self.source_type,
            'redacted_fields': list(self.redacted_fields),
            'dropped_fields': list(self.dropped_fields),
            'redaction_count': len(self.redacted_fields) + len(self.dropped_fields),
            'rules': list(self.rule_ids),
            'filter_failed': bool(self.filter_failed),
        }


class _Collector:
    """Accumulates rule hits during one sanitization pass."""

    def __init__(self) -> None:
        self.redacted: list[str] = []
        self.dropped: list[str] = []
        self.rules: list[str] = []
        self.fields_seen = 0

    def redact(self, path: str, rule: str) -> None:
        self.redacted.append(path)
        self.rule(rule)

    def drop(self, path: str, rule: str) -> None:
        self.dropped.append(path)
        self.rule(rule)

    def rule(self, rule: str) -> None:
        if rule and rule not in self.rules:
            self.rules.append(rule)


# ---------------------------------------------------------------------------
# Phase 13 — freeform text
# ---------------------------------------------------------------------------
def sanitize_text(
    value: Any,
    *,
    policy: PrivacyPolicy = DEFAULT_POLICY,
    collector: _Collector | None = None,
    path: str = '',
) -> str:
    """Strip credential shapes out of one freeform string.

    Order matters: PEM blocks and JWTs first (they are unambiguous), then URLs
    (so ``https://u:p@host/KEY?token=x`` is rewritten rather than left whole),
    then the ``Bearer``/``Basic`` header shapes, then policy-driven email and
    private-address handling. A value that is ENTIRELY a recovery phrase is
    replaced outright.

    The original never appears in the return value, in the collector, or in any
    log line this module emits.
    """
    sink = collector if collector is not None else _Collector()
    text = str(value if value is not None else '')
    if not text:
        return text

    if len(text) > MAX_STRING_CHARS:
        text = text[:MAX_STRING_CHARS] + TRUNCATED_SUFFIX
        sink.rule('bounds.value_truncated')

    if looks_like_mnemonic(text):
        sink.redact(path, 'credential.mnemonic')
        return REDACTED

    original = text
    if _PEM_RE.search(text):
        text = _PEM_RE.sub(REDACTED, text)
        sink.rule('credential.pem_private_key')
    if _JWT_RE.search(text):
        text = _JWT_RE.sub(REDACTED, text)
        sink.rule('credential.jwt')

    def _replace_url(match: 're.Match[str]') -> str:
        cleaned, rules = sanitize_url(match.group(0))
        for rule in rules:
            sink.rule(rule)
        return cleaned

    if _URL_RE.search(text):
        text = _URL_RE.sub(_replace_url, text)
    # A ``user:password@host`` that was not part of a parseable URL.
    if _URL_USERINFO_RE.search(text):
        text = _URL_USERINFO_RE.sub(lambda m: f'{m.group(1)}{REDACTED}@', text)
        sink.rule('credential.url_userinfo')

    if _BEARER_RE.search(text):
        text = _BEARER_RE.sub(REDACTED, text)
        sink.rule('credential.bearer_token')
    if _BASIC_AUTH_RE.search(text):
        text = _BASIC_AUTH_RE.sub(REDACTED, text)
        sink.rule('credential.basic_auth')

    if policy.redact_emails and _EMAIL_RE.search(text):
        text = _EMAIL_RE.sub(REDACTED, text)
        sink.rule('pii.email')

    if policy.redact_private_ips:
        text = _redact_private_addresses_in_text(text, policy=policy, sink=sink)

    if text != original and path:
        sink.redacted.append(path)
    return text


_IPV4_IN_TEXT_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
_IPV6_IN_TEXT_RE = re.compile(r'\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b')


def _redact_private_addresses_in_text(text: str, *, policy: PrivacyPolicy, sink: _Collector) -> str:
    """Apply the private-network policy to bare addresses inside free text."""
    if policy.private_network_mode == NETWORK_MODE_PRESERVE:
        return text

    def _replace(match: 're.Match[str]') -> str:
        rule = classify_network_identifier(match.group(0))
        if rule is None:
            return match.group(0)
        sink.rule(rule)
        return _apply_network_mode(match.group(0), policy=policy)

    return _IPV6_IN_TEXT_RE.sub(_replace, _IPV4_IN_TEXT_RE.sub(_replace, text))


def _apply_network_mode(value: Any, *, policy: PrivacyPolicy) -> str:
    """Render one private network identifier under the workspace's chosen mode."""
    if policy.private_network_mode == NETWORK_MODE_PSEUDONYMIZE:
        return pseudonymize(value, scope=policy.scope)
    return REDACTED


def sanitize_error_text(
    value: Any,
    *,
    policy: PrivacyPolicy = DEFAULT_POLICY,
    max_chars: int = 240,
    source_type: str = SOURCE_INTEGRATION_ERROR,
) -> str:
    """A persistence- and customer-safe rendering of provider/transport error text.

    Provider errors routinely quote the URL that failed, and an RPC URL commonly
    carries the API key in its path or query. This is the choke point every such
    message goes through before it reaches ``monitored_systems.last_error_text``,
    ``monitoring_polls.error_message``, or a log line — so the credential is
    removed BEFORE the write rather than at render time.

    Never raises: an error path that raised while sanitizing an error would lose
    the operational signal entirely, so the fallback is the exception CLASS name,
    which cannot contain customer data.
    """
    try:
        collector = _Collector()
        text = sanitize_text(value, policy=policy, collector=collector)
        if collector.rules:
            record_redaction_metrics(
                source_type=source_type,
                redacted_count=1,
                rule_ids=collector.rules,
            )
        return text[:max_chars]
    except Exception:  # pragma: no cover - defensive; must never break an error path
        record_privacy_failure(source_type)
        return f'{type(value).__name__ if isinstance(value, BaseException) else "error"}:{REDACTED}'


# ---------------------------------------------------------------------------
# Phase 6 — header handling
# ---------------------------------------------------------------------------
#: The only headers persisted by default. An ALLOWLIST, so a header nobody
#: thought about is dropped rather than stored. Authentication material is
#: verified at the edge and discarded — it is never part of telemetry.
DEFAULT_HEADER_ALLOWLIST: frozenset[str] = frozenset(
    normalize_key(name) for name in (
        'content-type', 'content-length', 'content-encoding',
        'user-agent', 'x-request-id', 'x-correlation-id', 'traceparent',
    )
)

#: Never persisted under any allowlist. Listed explicitly so the prohibition is
#: testable rather than implied by the allowlist's contents.
NEVER_PERSISTED_HEADERS: frozenset[str] = frozenset(
    normalize_key(name) for name in (
        'authorization', 'proxy-authorization', 'cookie', 'set-cookie',
        'x-api-key', 'x-auth-token',
    )
)


def sanitize_headers(
    headers: Any,
    *,
    allowlist: Iterable[str] | None = None,
    source_type: str = SOURCE_PUBLIC_CHAIN,
) -> SanitizationResult:
    """Keep only allowlisted headers; record the NAMES of everything dropped.

    Header names are not secret and are useful evidence ("an Authorization header
    was present and discarded"); header VALUES never survive this function.
    """
    allowed = (
        frozenset(normalize_key(name) for name in allowlist)
        if allowlist is not None
        else DEFAULT_HEADER_ALLOWLIST
    ) - NEVER_PERSISTED_HEADERS
    collector = _Collector()
    kept: dict[str, Any] = {}
    try:
        items = headers.items() if hasattr(headers, 'items') else list(headers or [])
    except Exception:
        items = []
    for name, value in items:
        normalized = normalize_key(name)
        if normalized in allowed:
            kept[str(name)] = str(value)
            continue
        rule = (
            'credential.authorization_header'
            if normalized in NEVER_PERSISTED_HEADERS
            else 'header.not_allowlisted'
        )
        collector.drop(str(name), rule)
    return _finish(kept, collector, source_type=source_type)


# ---------------------------------------------------------------------------
# Phase 3 — the canonical entry point
# ---------------------------------------------------------------------------
def sanitize_ingested_payload(
    payload: Any,
    *,
    source_type: str,
    policy: PrivacyPolicy = DEFAULT_POLICY,
) -> SanitizationResult:
    """Sanitize one externally-supplied payload before it is persisted or forwarded.

    Call this BEFORE the database write or the outbound request — never after.
    The returned :class:`SanitizationResult` is everything a caller needs; the
    original object is not modified and the original sensitive values are not
    reachable through the result.

    Failure behavior is per source type (Phase 16). A source in
    :data:`FAIL_CLOSED_SOURCE_TYPES` raises :class:`PrivacyFilterError` rather
    than allowing unfiltered data through. A public-chain source degrades to the
    public-chain field allowlist and sets ``filter_failed``, because taking live
    monitoring offline over a filter bug is the worse failure — and it is
    recorded, counted, and visible, not silent.
    """
    collector = _Collector()
    try:
        sanitized = _sanitize_value(payload, path='', depth=0, policy=policy, collector=collector)
    except Exception:
        record_privacy_failure(source_type)
        if source_type in FAIL_CLOSED_SOURCE_TYPES:
            raise PrivacyFilterError(source_type) from None
        return _public_chain_fallback(payload, source_type=source_type)
    return _finish(sanitized, collector, source_type=source_type)


def sanitize_for_ai(
    payload: Any,
    *,
    source_type: str = SOURCE_AI_CONTEXT,
    policy: PrivacyPolicy = DEFAULT_POLICY,
) -> SanitizationResult:
    """Sanitize context that is about to leave Decoda for an external AI provider.

    Stricter than the storage boundary by construction (:data:`AI_POLICY_OVERRIDES`):
    email addresses and private network identifiers are removed regardless of what
    the workspace chose for its own rows, because an external provider is a
    different trust boundary from the customer's own database.

    Fail-closed: if sanitization cannot complete, this raises rather than sending
    anything. Public blockchain identifiers pass through intact — an AI triage
    result that cannot name the transaction it is about is worthless.
    """
    return sanitize_ingested_payload(
        payload,
        source_type=source_type,
        policy=policy.stricter(**AI_POLICY_OVERRIDES),
    )


def _finish(payload: Any, collector: _Collector, *, source_type: str) -> SanitizationResult:
    changed = bool(collector.redacted or collector.dropped or collector.rules)
    if changed:
        record_redaction_metrics(
            source_type=source_type,
            redacted_count=len(collector.redacted) + len(collector.dropped),
            rule_ids=collector.rules,
        )
    return SanitizationResult(
        payload=payload,
        redacted_fields=collector.redacted,
        dropped_fields=collector.dropped,
        rule_ids=collector.rules,
        changed=changed,
        source_type=str(source_type or ''),
    )


def _public_chain_fallback(payload: Any, *, source_type: str) -> SanitizationResult:
    """Reduce a payload to its public-chain fields after a sanitizer failure.

    Only primitive values under :data:`PUBLIC_CHAIN_FIELDS` keys survive, so the
    result cannot carry a credential even though the filter did not complete.
    Monitoring keeps the facts it needs; ``filter_failed`` states plainly that the
    row is a degraded one.
    """
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in list(payload.items())[:MAX_CONTAINER_ITEMS]:
            if normalize_key(key) in PUBLIC_CHAIN_FIELDS and isinstance(value, (str, int, float, bool, type(None))):
                kept[str(key)] = value
            else:
                dropped.append(str(key))
    return SanitizationResult(
        payload=kept,
        redacted_fields=[],
        dropped_fields=dropped,
        rule_ids=['privacy.filter_failed_public_chain_fallback'],
        changed=True,
        source_type=str(source_type or ''),
        filter_failed=True,
    )


def _sanitize_value(
    value: Any,
    *,
    path: str,
    depth: int,
    policy: PrivacyPolicy,
    collector: _Collector,
    public_chain_context: bool = False,
) -> Any:
    """Recursive worker. Bounded in depth, breadth, and string length."""
    if depth > MAX_DEPTH:
        collector.rule('bounds.depth_exceeded')
        return REDACTED_DEPTH
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, path=path, depth=depth, policy=policy, collector=collector)
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        for index, item in enumerate(list(value)[:MAX_CONTAINER_ITEMS]):
            items.append(_sanitize_value(
                item, path=f'{path}[{index}]', depth=depth + 1, policy=policy,
                collector=collector, public_chain_context=public_chain_context,
            ))
        if len(value) > MAX_CONTAINER_ITEMS:
            collector.rule('bounds.too_many_items')
        return items
    if isinstance(value, str):
        # A public-chain field's value is a chain fact, not freeform text: a tx
        # hash must survive byte-for-byte. See PUBLIC_CHAIN_FIELDS.
        if public_chain_context:
            if len(value) > MAX_STRING_CHARS:
                collector.rule('bounds.value_truncated')
                return value[:MAX_STRING_CHARS] + TRUNCATED_SUFFIX
            return value
        return sanitize_text(value, policy=policy, collector=collector, path=path)
    return value


def _sanitize_mapping(
    value: Mapping[str, Any],
    *,
    path: str,
    depth: int,
    policy: PrivacyPolicy,
    collector: _Collector,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for index, (raw_key, raw_value) in enumerate(value.items()):
        if index >= MAX_CONTAINER_ITEMS:
            collector.rule('bounds.too_many_items')
            break
        collector.fields_seen += 1
        if collector.fields_seen > MAX_TOTAL_FIELDS:
            collector.rule('bounds.too_many_fields')
            break
        key = str(raw_key)
        child_path = f'{path}.{key}' if path else key
        normalized = normalize_key(key)

        # 1. Mandatory Decoda rules. No workspace policy reaches this branch.
        if normalized in MANDATORY_CREDENTIAL_KEYS:
            out[key] = REDACTED
            collector.redact(child_path, 'credential.key_match')
            continue

        # 2. Customer exclusion/redaction rules — stricter only, never weaker.
        if normalized in policy.excluded_fields:
            collector.drop(child_path, 'policy.customer_excluded')
            continue
        if normalized in policy.redacted_fields:
            out[key] = REDACTED
            collector.redact(child_path, 'policy.customer_redacted')
            continue

        # 3. The workspace's metadata allowlist, when it set one. Checked BEFORE
        # the typed branches below: an allowlist says "these are the only metadata
        # keys I want retained", and dropping a key is stricter than masking it,
        # so the customer's stricter intent has to win over the default handling.
        if policy.allowed_metadata_fields is not None and _is_metadata_path(child_path):
            if normalized not in policy.allowed_metadata_fields:
                collector.drop(child_path, 'policy.metadata_not_allowlisted')
                continue

        # 4. Public-chain forensic fields pass through untouched (Phase 10).
        if normalized in PUBLIC_CHAIN_FIELDS:
            out[key] = _sanitize_value(
                raw_value, path=child_path, depth=depth + 1, policy=policy,
                collector=collector, public_chain_context=True,
            )
            continue

        # 5. Typed handling for URL / email / network keys.
        if normalized in URL_KEYS and isinstance(raw_value, str):
            cleaned, rules = sanitize_url(raw_value)
            if rules:
                collector.redacted.append(child_path)
                for rule in rules:
                    collector.rule(rule)
            out[key] = cleaned
            continue
        if normalized in EMAIL_KEYS and isinstance(raw_value, str):
            if policy.redact_emails:
                out[key] = REDACTED
                collector.redact(child_path, 'pii.email')
            else:
                out[key] = raw_value
            continue
        if normalized in NETWORK_KEYS and isinstance(raw_value, str):
            out[key] = _sanitize_network_value(
                raw_value, path=child_path, policy=policy, collector=collector,
            )
            continue

        out[key] = _sanitize_value(
            raw_value, path=child_path, depth=depth + 1, policy=policy, collector=collector,
        )
    return out


def _is_metadata_path(path: str) -> bool:
    """Whether this path sits inside a ``metadata`` container.

    The allowlist applies to open-ended metadata bags, not to the canonical
    top-level fields the product's own workflow depends on.
    """
    parts = path.split('.')
    return len(parts) > 1 and normalize_key(parts[-2]) in {'metadata', 'meta', 'labels', 'tags', 'attributes'}


def _sanitize_network_value(
    value: str, *, path: str, policy: PrivacyPolicy, collector: _Collector,
) -> Any:
    """Apply the private-network policy to a value whose KEY says it is a host/IP."""
    rule = classify_network_identifier(value)
    if rule is None or not policy.redact_private_ips:
        # A public provider hostname stays: it says which provider degraded.
        return value
    if policy.private_network_mode == NETWORK_MODE_PRESERVE:
        return value
    if policy.private_network_mode == NETWORK_MODE_DROP:
        collector.drop(path, rule)
        return None
    collector.redact(path, rule)
    return _apply_network_mode(value, policy=policy)


# ---------------------------------------------------------------------------
# Phase 17 — safe operational metrics
# ---------------------------------------------------------------------------
#: Rule ids are a small, closed vocabulary, so they are safe as a metric label.
#: Payload values, emails, IPs, and token fragments never appear as labels — the
#: counters below take only the source type and the rule id.
METRIC_REDACTION_EVENTS = 'telemetry_redaction_events_total'
METRIC_REDACTED_FIELDS = 'telemetry_redacted_fields_total'
METRIC_PRIVACY_FAILURES = 'telemetry_ingestion_privacy_failures_total'


def record_redaction_metrics(*, source_type: str, redacted_count: int, rule_ids: Sequence[str]) -> None:
    """Count one redaction event. Never raises, never labels with payload data."""
    try:
        from services.api.app import observability

        observability.increment(METRIC_REDACTION_EVENTS, source_type=str(source_type or 'unknown'))
        if redacted_count:
            observability.increment(
                METRIC_REDACTED_FIELDS, redacted_count, source_type=str(source_type or 'unknown'),
            )
        for rule_id in list(rule_ids)[:20]:
            observability.increment(
                METRIC_REDACTION_EVENTS,
                source_type=str(source_type or 'unknown'),
                rule_id=str(rule_id),
            )
    except Exception:  # pragma: no cover - metrics must never break ingestion
        pass


def record_privacy_failure(source_type: str) -> None:
    """Count one sanitizer failure. Never raises."""
    try:
        from services.api.app import observability

        observability.increment(METRIC_PRIVACY_FAILURES, source_type=str(source_type or 'unknown'))
    except Exception:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Phase 14 — evidence transparency
# ---------------------------------------------------------------------------
def privacy_processing_metadata(result: SanitizationResult | None) -> dict[str, Any]:
    """The ``privacy_processing`` block recorded alongside sanitized records.

    States that sanitization happened and what it removed, so a package is never
    presented as containing an original it does not contain. Carries counts, rule
    ids, and field paths only — putting the redacted value into provenance would
    defeat the redaction.
    """
    if result is None:
        return {'applied': False, 'redacted_fields': 0, 'rules': []}
    return {
        'applied': bool(result.changed),
        'source_type': result.source_type,
        'redacted_fields': len(result.redacted_fields) + len(result.dropped_fields),
        'rules': list(result.rule_ids),
        'filter_failed': bool(result.filter_failed),
    }
