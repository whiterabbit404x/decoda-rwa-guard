"""Integration Gateway Agent (Screen 10) — pure derivation layer.

This module contains ONLY pure, database-free functions. It turns canonical
backend facts (already fetched by the pilot request handlers) into the Screen-10
control-plane view — provider / API / webhook / connection rows — and runs the
deterministic Integration Gateway detectors that produce structured, evidence-
backed recommendations plus a derived connection-risk verdict.

Design rules (from the Screen-10 spec):

  * DETERMINISTIC FIRST, AI SECOND (§13). Every recommendation and risk verdict
    here is machine-verifiable from measured records. There is NO LLM call in this
    module; an optional AI layer may summarise on top, but core functionality never
    depends on it.
  * CANONICAL HEALTH, NOT A SECOND DEFINITION (§5/§26). Provider health is consumed
    from ``provider_health`` (built by pilot._build_monitoring_sources_enrichment —
    the same source Screen 4 and Screen 12 read). This module never re-derives a
    provider's health from configuration existence.
  * TRUTHFUL UNKNOWNS (§5/§28). A provider with no measured observation is
    ``unknown`` — never inflated to healthy/connected because an env var exists.
  * NO FABRICATED SIGNALS (§5). Metrics that are not measured (e.g. external
    custody credential age) are omitted, not invented.
  * NO SECRETS (§9). Only safe metadata is ever produced here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Canonical vocabularies (kept in one place; the frontend mirrors these).
# ---------------------------------------------------------------------------

# Provider integration status (Screen-10 truth table §28). Mapped 1:1 from the
# canonical provider_health_records status vocabulary so Screen 4/10/12 agree.
PROVIDER_STATUS_HEALTHY = 'healthy'
PROVIDER_STATUS_DEGRADED = 'degraded'
PROVIDER_STATUS_DISCONNECTED = 'disconnected'
PROVIDER_STATUS_UNKNOWN = 'unknown'

# Canonical provider_health_records.status -> Screen-10 provider status.
_CANONICAL_PROVIDER_STATUS = {
    'healthy': PROVIDER_STATUS_HEALTHY,
    'degraded': PROVIDER_STATUS_DEGRADED,
    'unavailable': PROVIDER_STATUS_DISCONNECTED,
    'error': PROVIDER_STATUS_DISCONNECTED,
}

# Connection-risk levels (§14).
RISK_LOW = 'low'
RISK_MEDIUM = 'medium'
RISK_HIGH = 'high'
RISK_CRITICAL = 'critical'
RISK_UNKNOWN = 'unknown'
_RISK_SEVERITY = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2, RISK_CRITICAL: 3, RISK_UNKNOWN: -1}

# Recommendation severity ordering (worst first when sorting).
_SEVERITY_ORDER = {'critical': 3, 'high': 2, 'medium': 1, 'low': 0}

# Known-provider type hints. Only used to LABEL a provider's type for display; a
# provider whose type cannot be inferred is labelled from the monitored source's
# own declared type, else 'Blockchain RPC' (provider_health_records is RPC-centric)
# or 'Other'. Never used to invent health.
_PROVIDER_TYPE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (('chainlink', 'pyth', 'oracle', 'redstone', 'api3'), 'Oracle'),
    (('fireblocks', 'copper', 'anchorage', 'bitgo', 'custod'), 'Custody'),
    (('circle', 'usdc', 'paxos', 'tether', 'stablecoin', 'issuer'), 'Stablecoin / Issuer'),
    (('cloudflare', 'fastly', 'akamai', 'dns'), 'Security / DNS'),
    (('alchemy', 'quicknode', 'infura', 'ankr', 'chainstack', 'blast', 'rpc', 'g.alchemy'), 'Blockchain RPC'),
]

# Webhook delivery health thresholds — bounded, deterministic (§37/§49).
_WEBHOOK_HEALTHY_SUCCESS_RATE = 0.95   # >= 95% recent success = healthy
_WEBHOOK_DEGRADED_SUCCESS_RATE = 0.80  # 80–95% = degraded

# Evidence signals for an observation whose latency was NOT meaningfully measured —
# a skipped probe, an active provider backoff, a rate-limit, or a hard
# provider-unavailable/error condition. Legacy failed rows persisted latency_ms = 0
# as a placeholder under these conditions; a stored 0 here means "no measurement",
# never a real 0 ms round trip, so it must render as "—" not "0 ms" (§5/§7/§28).
_UNMEASURED_LATENCY_EVIDENCE: tuple[str, ...] = (
    'provider_backoff_active',
    'backoff',
    'rate_limited',
    'rate_limit',
    'probe_skipped',
    'skipped',
    'provider_unavailable',
    'unavailable',
    'cache_hit',
)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_aware(value)
    try:
        return _as_aware(datetime.fromisoformat(str(value).replace('Z', '+00:00')))
    except (ValueError, TypeError):
        return None


def _norm(value: Any) -> str:
    return str(value or '').strip().lower()


def _provider_type_for(host: str, *, is_oracle: bool, declared_type: str | None) -> str:
    """Label a provider's type for display (never used to derive health)."""
    if is_oracle:
        return 'Oracle'
    key = _norm(host)
    for needles, label in _PROVIDER_TYPE_HINTS:
        if any(needle in key for needle in needles):
            return label
    declared = _norm(declared_type)
    if 'oracle' in declared:
        return 'Oracle'
    if declared and declared not in {'rpc', 'wallet', 'contract', 'evm'}:
        return declared_type or 'Other'
    return 'Blockchain RPC'


def _title_case_host(host: str) -> str:
    raw = str(host or '').strip()
    if not raw:
        return 'Unknown provider'
    # Redact any accidental URL to a hostname label; provider_type is already a host.
    label = raw.split('//')[-1].split('/')[0]
    # Take the recognisable brand token when the host is a full domain.
    parts = [p for p in label.replace('_', '.').split('.') if p]
    for part in parts:
        for needles, _ in _PROVIDER_TYPE_HINTS:
            if part.lower() in needles:
                return part.capitalize()
    token = parts[0] if parts else label
    return token[:1].upper() + token[1:] if token else label


def _effective_latency_ms(
    *, latency_ms: Any, status: str, backoff: bool,
    degraded_reason: Any, evidence_source: Any,
) -> Any:
    """Evidence-aware latency normalization for the Screen-10 read model (§5/§7/§28).

    Rules:
      * A missing latency stays ``None`` (the frontend renders "—").
      * A non-zero measured latency is passed through UNCHANGED — never discarded.
      * A stored ``latency_ms == 0`` is only meaningful for a HEALTHY / measured
        success. For an observation that was skipped or never meaningfully measured
        (provider backoff, rate limit, skipped probe, provider unavailable / error →
        Screen-10 disconnected/degraded/unknown), a persisted ``0`` is a placeholder
        for "no measurement" and is normalized to ``None`` so the operator sees "—",
        not a misleading "0 ms".

    This is deliberately NOT a blanket "every 0 → None": a legitimate ``0`` on a
    healthy observation is preserved. Only evidence-indicated unmeasured zeros —
    legacy persisted rows and non-success observations — become ``None``.
    """
    if latency_ms is None:
        return None
    try:
        value = float(latency_ms)
    except (TypeError, ValueError):
        return None
    if value != 0:
        return latency_ms
    # value == 0: keep only for a healthy/measured success; otherwise it is an
    # unmeasured placeholder and must render as unavailable.
    reason_text = ' '.join(
        str(part or '') for part in (status, degraded_reason, evidence_source)
    ).lower()
    unmeasured = (
        bool(backoff)
        or status in {PROVIDER_STATUS_DISCONNECTED, PROVIDER_STATUS_DEGRADED, PROVIDER_STATUS_UNKNOWN}
        or any(signal in reason_text for signal in _UNMEASURED_LATENCY_EVIDENCE)
    )
    return None if unmeasured else latency_ms


# ---------------------------------------------------------------------------
# Provider rows (Providers tab) — provider-centric aggregation of the canonical
# per-target provider health that Screen 4 already computed.
# ---------------------------------------------------------------------------
def build_provider_rows(
    *,
    sources: list[dict[str, Any]],
    provider_health: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Aggregate canonical per-source provider evidence into one row per provider.

    ``provider_health`` is pilot's canonical aggregate ({providers:[{host,status,
    latency_ms,checked_at,evidence_source,target_count}], ...}). We use its measured
    status as the headline and enrich each provider with the routing role, chains
    served, fallback availability and degraded reason drawn from ``sources``.
    """
    ph_providers: dict[str, dict[str, Any]] = {}
    for entry in ((provider_health or {}).get('providers') or []):
        host = _norm(entry.get('host'))
        if host:
            ph_providers[host] = entry

    # Fold the per-source facts by provider host.
    folded: dict[str, dict[str, Any]] = {}

    def _bucket(host: str) -> dict[str, Any]:
        return folded.setdefault(host, {
            'host': host, 'roles': set(), 'chains': set(), 'networks': set(),
            'target_count': 0, 'is_oracle': False, 'declared_type': None,
            'last_checked_at': None, 'latency_ms': None, 'degraded_reason': None,
            'evidence_source': None, 'backoff': False,
        })

    healthy_hosts: set[str] = set()
    for source in sources or []:
        host = _norm(source.get('provider') or source.get('operational_primary_provider')
                     or source.get('primary_provider') or source.get('configured_primary_provider'))
        if not host:
            continue
        bucket = _bucket(host)
        bucket['target_count'] += 1
        if source.get('is_oracle'):
            bucket['is_oracle'] = True
        if not bucket['declared_type']:
            bucket['declared_type'] = source.get('source_type')
        if source.get('network'):
            bucket['networks'].add(str(source.get('network')))
        if source.get('chain_id') is not None:
            bucket['chains'].add(str(source.get('chain_id')))
        # Routing role.
        if _norm(source.get('operational_primary_provider')) == host or _norm(source.get('primary_provider')) == host:
            bucket['roles'].add('primary')
        if _norm(source.get('fallback_provider')) == host:
            bucket['roles'].add('fallback')
        # Newest provider check timestamp + latency (successful only).
        checked = _coerce_dt(source.get('provider_checked_at'))
        prev = _coerce_dt(bucket['last_checked_at'])
        if checked is not None and (prev is None or checked > prev):
            bucket['last_checked_at'] = source.get('provider_checked_at')
            bucket['latency_ms'] = source.get('successful_latency_ms')
            bucket['evidence_source'] = source.get('evidence_source')
        if source.get('provider_backoff_active'):
            bucket['backoff'] = True
        if _norm(source.get('status')) in {'provider_unavailable', 'degraded', 'failed', 'critical'}:
            reason = source.get('status_reason')
            if reason and not bucket['degraded_reason']:
                bucket['degraded_reason'] = str(reason)

    rows: list[dict[str, Any]] = []
    for host, bucket in folded.items():
        canonical = ph_providers.get(host, {})
        canonical_status = _norm(canonical.get('status'))
        status = _CANONICAL_PROVIDER_STATUS.get(canonical_status, PROVIDER_STATUS_UNKNOWN)
        # Provider backoff (process-wide 429) means the newest poll was skipped —
        # never healthy. Fail-closed to degraded when canonical status is not worse.
        if bucket['backoff'] and status in {PROVIDER_STATUS_HEALTHY, PROVIDER_STATUS_UNKNOWN}:
            status = PROVIDER_STATUS_DEGRADED
            bucket['degraded_reason'] = bucket['degraded_reason'] or 'provider_backoff_active'
        if status in {PROVIDER_STATUS_HEALTHY}:
            healthy_hosts.add(host)
        role = 'primary' if 'primary' in bucket['roles'] else ('fallback' if 'fallback' in bucket['roles'] else 'observed')
        rows.append({
            'integration_key': f'provider:{host}',
            'provider': _title_case_host(host),
            'host': host,
            'type': _provider_type_for(host, is_oracle=bucket['is_oracle'], declared_type=bucket['declared_type']),
            'status': status,
            'role': role,
            'networks': sorted(bucket['networks']),
            'chains': sorted(bucket['chains']),
            'target_count': bucket['target_count'],
            # Prefer the canonical aggregate's timestamp/latency; fall back to the
            # freshest per-source values folded above.
            'last_check_at': canonical.get('checked_at') or bucket['last_checked_at'],
            # Evidence-aware: a persisted latency_ms == 0 from a skipped/backoff/
            # unavailable observation was never measured and renders as "—", never
            # "0 ms". A real measured latency is always passed through unchanged.
            'latency_ms': _effective_latency_ms(
                latency_ms=(canonical.get('latency_ms') if canonical.get('latency_ms') is not None else bucket['latency_ms']),
                status=status,
                backoff=bucket['backoff'],
                degraded_reason=bucket['degraded_reason'],
                evidence_source=canonical.get('evidence_source') or bucket['evidence_source'],
            ),
            'evidence_source': canonical.get('evidence_source') or bucket['evidence_source'],
            'degraded_reason': bucket['degraded_reason'],
            # Credential age is NOT measured for RPC providers in canonical storage;
            # surfaced as None so the UI shows "Not available", never a fabricated age.
            'credential_age_days': None,
            'credential_configured': None,
        })

    rows.sort(key=lambda r: (
        {PROVIDER_STATUS_DISCONNECTED: 0, PROVIDER_STATUS_DEGRADED: 1,
         PROVIDER_STATUS_UNKNOWN: 2, PROVIDER_STATUS_HEALTHY: 3}.get(r['status'], 4),
        r['provider'].lower(),
    ))
    return rows


# ---------------------------------------------------------------------------
# API rows (APIs tab) — SaaS-infra integrations from the canonical env snapshot.
# Only safe metadata (configured / provider name / status); never a credential.
# ---------------------------------------------------------------------------
def build_api_rows(infra: dict[str, Any] | None) -> list[dict[str, Any]]:
    infra = infra or {}
    rows: list[dict[str, Any]] = []

    def _status_from_snapshot(node: dict[str, Any]) -> tuple[str, bool]:
        s = _norm(node.get('status'))
        if s in {'healthy', 'ok'}:
            return 'Healthy', True
        if s in {'degraded', 'error'}:
            return 'Degraded', True
        # 'warning' = configured-but-incomplete or optional-not-configured.
        return 'Configured', False

    def _configured(node: dict[str, Any], keys: Iterable[str]) -> bool:
        checks = node.get('checks') or {}
        return any(bool(checks.get(k)) for k in keys)

    specs = [
        ('email', 'Email delivery', 'API key', 'notifications', ('provider_key_present', 'from_address_present')),
        ('stripe', 'Billing (Stripe)', 'Secret key', 'billing', ('secret_key_present', 'webhook_secret_present')),
        ('auth_rate_limiter', 'Auth rate limiter (Redis)', 'Connection URL', 'security', ('redis_url_present',)),
        ('slack', 'Slack notifications', 'OAuth / webhook', 'notifications', ('oauth_configured',)),
    ]
    for key, label, auth, scope, cfg_keys in specs:
        node = infra.get(key) if isinstance(infra.get(key), dict) else {}
        status, is_up = _status_from_snapshot(node)
        configured = _configured(node, cfg_keys) or is_up
        rows.append({
            'integration_key': f'api:{key}',
            'name': label,
            'authentication': auth,
            'scope': scope,
            # Safe status vocabulary (§B): Configured / Missing / Healthy / Degraded.
            'status': status if configured else 'Missing',
            'provider': node.get('provider') or None,
            'credential_configured': bool(configured),
            'message': node.get('message') or None,
            # Never expose secrets — these are always null here (§9).
            'credential_hint': None,
            'error_rate': None,   # not measured per-provider; honest null (§5).
            'latency_ms': None,
            'last_request_at': None,
        })
    return rows


# ---------------------------------------------------------------------------
# Webhook rows (Webhooks tab) — real delivery health, never faked (§37/§49).
# ``webhooks`` rows carry aggregated delivery counts computed by the caller.
# ---------------------------------------------------------------------------
def build_webhook_rows(webhooks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wh in webhooks or []:
        enabled = wh.get('enabled') is not False
        total = int(wh.get('delivery_total') or 0)
        succeeded = int(wh.get('delivery_succeeded') or 0)
        failed = int(wh.get('delivery_failed') or 0)
        pending = int(wh.get('delivery_pending') or 0)
        success_rate = (succeeded / total) if total > 0 else None

        if not enabled:
            status, retry_state = 'Disabled', 'disabled'
        elif total == 0:
            status, retry_state = 'Pending', 'idle'
        elif success_rate is not None and success_rate >= _WEBHOOK_HEALTHY_SUCCESS_RATE:
            status, retry_state = 'Healthy', ('retrying' if pending > 0 else 'idle')
        elif success_rate is not None and success_rate >= _WEBHOOK_DEGRADED_SUCCESS_RATE:
            status, retry_state = 'Degraded', ('retrying' if pending > 0 else 'idle')
        else:
            status, retry_state = 'Failed', ('retrying' if pending > 0 else 'idle')

        rows.append({
            'integration_key': f"webhook:{wh.get('id')}",
            'id': str(wh.get('id') or ''),
            'endpoint': _mask_url(wh.get('target_url')),
            'description': wh.get('description') or None,
            'event_types': wh.get('event_types') if isinstance(wh.get('event_types'), list) else [],
            'status': status,
            'enabled': enabled,
            'last_delivery_at': wh.get('last_delivery_at'),
            'success_rate': round(success_rate * 100, 1) if success_rate is not None else None,
            'delivery_total': total,
            'failures': failed,
            'pending': pending,
            'retry_state': retry_state,
            # Signing secret is configured/not — never the value (§9/§37).
            'signing_secret_configured': bool(wh.get('secret_configured')),
        })
    rows.sort(key=lambda r: ({'Failed': 0, 'Degraded': 1, 'Pending': 2, 'Healthy': 3, 'Disabled': 4}.get(r['status'], 5)))
    return rows


def _mask_url(value: Any) -> str:
    raw = str(value or '').strip()
    if not raw:
        return '—'
    try:
        # Show scheme + host + a redacted path so the destination is identifiable
        # without leaking a secret-bearing path/query.
        without_scheme = raw.split('://', 1)
        scheme = without_scheme[0] + '://' if len(without_scheme) == 2 else ''
        rest = without_scheme[-1]
        host = rest.split('/', 1)[0]
        return f'{scheme}{host}/…'
    except Exception:
        return raw[:40] + '…' if len(raw) > 40 else raw


# ---------------------------------------------------------------------------
# Connection rows (Connections tab) — normalized dependency view (§3.D).
# ---------------------------------------------------------------------------
def build_connection_rows(
    *,
    provider_rows: list[dict[str, Any]],
    webhook_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    connections: list[dict[str, Any]] = []
    for p in provider_rows:
        chain_label = ', '.join(p['networks']) or (', '.join(f'chain {c}' for c in p['chains']) or 'Configured chain')
        state = {
            PROVIDER_STATUS_HEALTHY: 'healthy',
            PROVIDER_STATUS_DEGRADED: 'degraded',
            PROVIDER_STATUS_DISCONNECTED: 'offline',
            PROVIDER_STATUS_UNKNOWN: 'unknown',
        }.get(p['status'], 'unknown')
        connections.append({
            'integration_key': p['integration_key'],
            'source': 'Decoda monitoring',
            'via': p['provider'],
            'destination': chain_label,
            'purpose': p['type'],
            'state': state,
            'role': p['role'],
            'failover': 'configured' if p['role'] == 'fallback' else ('primary' if p['role'] == 'primary' else 'none'),
            'last_verified_at': p['last_check_at'],
        })
    for w in webhook_rows:
        state = {'Healthy': 'healthy', 'Degraded': 'degraded', 'Failed': 'offline',
                 'Disabled': 'disabled', 'Pending': 'unknown'}.get(w['status'], 'unknown')
        connections.append({
            'integration_key': w['integration_key'],
            'source': 'Decoda incident/alert engine',
            'via': 'Outbound webhook',
            'destination': w['endpoint'],
            'purpose': 'Notification delivery',
            'state': state,
            'role': 'delivery',
            'failover': 'none',
            'last_verified_at': w['last_delivery_at'],
        })
    return connections


# ---------------------------------------------------------------------------
# Deterministic recommendation detectors (§10/§11/§13). Every recommendation is
# grounded in the canonical evidence above. No LLM. Returns normalized dicts the
# caller persists idempotently.
# ---------------------------------------------------------------------------
def _rec(
    *, integration_key: str, integration_kind: str, recommendation_type: str, severity: str,
    title: str, description: str, reason: str, recommended_action: str, execution_mode: str,
    risk_if_ignored: str, evidence: dict[str, Any], confidence: str = 'high',
) -> dict[str, Any]:
    return {
        'integration_key': integration_key,
        'integration_kind': integration_kind,
        'recommendation_type': recommendation_type,
        'severity': severity,
        'title': title,
        'description': description,
        'reason': reason,
        'recommended_action': recommended_action,
        'execution_mode': execution_mode,
        'risk_if_ignored': risk_if_ignored,
        'evidence': evidence,
        'confidence': confidence,
        'dedupe_key': f'{recommendation_type}:{integration_key}',
    }


def evaluate_recommendations(
    *,
    sources: list[dict[str, Any]],
    provider_rows: list[dict[str, Any]],
    webhook_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic Integration Gateway detectors. Grounded, machine-verifiable."""
    recs: list[dict[str, Any]] = []
    healthy_hosts = {p['host'] for p in provider_rows if p['status'] == PROVIDER_STATUS_HEALTHY}

    # Map declared fallbacks per chain (network) from canonical source config.
    chain_providers: dict[str, set[str]] = {}
    chain_has_fallback: dict[str, bool] = {}
    chain_label: dict[str, str] = {}
    for s in sources or []:
        network = str(s.get('network') or (f"chain {s.get('chain_id')}" if s.get('chain_id') is not None else '')).strip()
        if not network:
            continue
        chain_label[network] = network
        primary = _norm(s.get('operational_primary_provider') or s.get('primary_provider') or s.get('configured_primary_provider') or s.get('provider'))
        fallback = _norm(s.get('fallback_provider'))
        if primary:
            chain_providers.setdefault(network, set()).add(primary)
        if fallback:
            chain_providers.setdefault(network, set()).add(fallback)
            chain_has_fallback[network] = True

    # --- Detector 1: dependency concentration / no fallback (§40) -------------
    # A monitored chain served by exactly ONE provider with no configured fallback
    # and no second healthy provider available is a single point of failure.
    for network, providers in chain_providers.items():
        if chain_has_fallback.get(network):
            continue  # a fallback is configured — do NOT recommend adding one.
        distinct = {p for p in providers if p}
        if len(distinct) != 1:
            continue  # 0 or 2+ providers — not a single-provider concentration.
        only_host = next(iter(distinct))
        # If another healthy provider already exists in the workspace, redundancy
        # is one config change away; still flag but note the available candidate.
        candidate = next((h for h in sorted(healthy_hosts) if h != only_host), None)
        recs.append(_rec(
            integration_key=f'dependency:{network}',
            integration_kind='dependency',
            recommendation_type='add_fallback_provider',
            severity='high',
            title=f'Configure a secondary RPC provider for {network}',
            description=(
                f'{_title_case_host(only_host)} currently handles 100% of {network} monitoring '
                f'reads. No healthy fallback provider is configured.'),
            reason=f'Single RPC provider ({_title_case_host(only_host)}) with no configured fallback for {network}.',
            recommended_action=(
                f'Add a secondary provider for {network} and enable failover routing on Monitoring Sources.'
                + (f' {_title_case_host(candidate)} is already healthy and could serve as the fallback.' if candidate else '')),
            execution_mode='manual',
            risk_if_ignored='A single-provider outage would halt monitoring reads for this chain.',
            evidence={'network': network, 'provider': only_host, 'provider_count': 1,
                      'fallback_configured': False, 'healthy_alternate': candidate},
        ))

    # --- Detector 2/3: degraded or disconnected provider ----------------------
    for p in provider_rows:
        if p['status'] not in {PROVIDER_STATUS_DEGRADED, PROVIDER_STATUS_DISCONNECTED}:
            continue
        host = p['host']
        alt = next((h for h in sorted(healthy_hosts) if h != host), None)
        is_down = p['status'] == PROVIDER_STATUS_DISCONNECTED
        reason_txt = p.get('degraded_reason') or ('provider unavailable' if is_down else 'provider degraded')
        if alt:
            # A healthy alternate exists -> a failover is possible. Switching a live
            # primary route is a Level-2 action requiring approval (§18/§19); the
            # actual switch uses the existing Screen-4 routing layer (§16).
            recs.append(_rec(
                integration_key=p['integration_key'],
                integration_kind='provider',
                recommendation_type='enable_failover',
                severity='high',
                title=f'Route monitoring reads away from {p["provider"]}',
                description=(
                    f'{p["provider"]} is {p["status"]} ({reason_txt.replace("_", " ")}). '
                    f'{_title_case_host(alt)} is healthy and can take over.'),
                reason=f'{p["provider"]} {p["status"]}; healthy alternate {_title_case_host(alt)} available.',
                recommended_action=f'Approve failover to {_title_case_host(alt)} via Monitoring Sources auto-routing.',
                execution_mode='approval_required',
                risk_if_ignored='Continued degraded provider reads reduce monitoring reliability.',
                evidence={'provider': host, 'status': p['status'], 'reason': reason_txt,
                          'healthy_alternate': alt, 'target_count': p['target_count']},
            ))
        else:
            recs.append(_rec(
                integration_key=p['integration_key'],
                integration_kind='provider',
                recommendation_type='investigate_provider_degradation',
                severity='critical' if is_down else 'high',
                title=f'{p["provider"]} is {p["status"]} with no healthy fallback',
                description=(
                    f'{p["provider"]} is {p["status"]} ({reason_txt.replace("_", " ")}) and no other '
                    f'healthy provider is configured for its chains.'),
                reason=f'{p["provider"]} {p["status"]}; no healthy alternate provider available.',
                recommended_action='Investigate the provider outage and add a redundant provider.',
                execution_mode='advisory',
                risk_if_ignored='Loss of monitored-chain access with no automatic failover path.',
                evidence={'provider': host, 'status': p['status'], 'reason': reason_txt,
                          'healthy_alternate': None, 'target_count': p['target_count']},
            ))

    # --- Detector 4: unstable webhook (§37/§49) -------------------------------
    for w in webhook_rows:
        if w['status'] not in {'Degraded', 'Failed'}:
            continue
        severity = 'medium' if w['status'] == 'Degraded' else 'high'
        recs.append(_rec(
            integration_key=w['integration_key'],
            integration_kind='webhook',
            recommendation_type='review_webhook',
            severity=severity,
            title=f'Review failing webhook delivery to {w["endpoint"]}',
            description=(
                f'Webhook to {w["endpoint"]} is {w["status"].lower()}: '
                f'{w["failures"]} failed of {w["delivery_total"]} recent deliveries '
                f'({w["success_rate"]}% success).'),
            reason=f'Webhook success rate {w["success_rate"]}% with {w["failures"]} recent failures.',
            recommended_action='Review destination availability and the webhook retry policy.',
            execution_mode='advisory',
            risk_if_ignored='Downstream systems (e.g. customer SIEM) may miss alert/incident notifications.',
            evidence={'endpoint': w['endpoint'], 'success_rate': w['success_rate'],
                      'failures': w['failures'], 'total': w['delivery_total']},
            confidence='high',
        ))

    recs.sort(key=lambda r: _SEVERITY_ORDER.get(r['severity'], 0), reverse=True)
    return recs


# ---------------------------------------------------------------------------
# Connection risk (§14) — deterministic verdict derived from findings only.
# ---------------------------------------------------------------------------
def compute_connection_risk(
    *,
    provider_rows: list[dict[str, Any]],
    webhook_rows: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    has_scan: bool,
    has_any_integration: bool,
) -> dict[str, Any]:
    """Return {'level', 'reason'} derived from measured findings. Never hard-coded."""
    if not has_any_integration:
        return {'level': RISK_UNKNOWN, 'reason': 'No integrations are configured yet.'}
    if not has_scan and all(p['status'] == PROVIDER_STATUS_UNKNOWN for p in provider_rows):
        return {'level': RISK_UNKNOWN, 'reason': 'No health scan has completed yet; provider evidence is unavailable.'}

    disconnected = [p for p in provider_rows if p['status'] == PROVIDER_STATUS_DISCONNECTED]
    degraded = [p for p in provider_rows if p['status'] == PROVIDER_STATUS_DEGRADED]
    healthy_hosts = {p['host'] for p in provider_rows if p['status'] == PROVIDER_STATUS_HEALTHY}
    rec_severities = {r['severity'] for r in recommendations}

    # Critical: a provider is disconnected AND there is no healthy alternate at all
    # -> complete loss of monitored-chain access (§14).
    for p in disconnected:
        if not (healthy_hosts - {p['host']}):
            return {'level': RISK_CRITICAL,
                    'reason': f'{p["provider"]} is disconnected with no healthy alternate provider available.'}
    if 'critical' in rec_severities:
        return {'level': RISK_CRITICAL, 'reason': 'A critical integration finding is unresolved.'}

    # High: a degraded/disconnected primary with a possible-but-not-yet-applied
    # failover, or any high-severity open finding.
    if disconnected:
        return {'level': RISK_HIGH,
                'reason': f'{len(disconnected)} provider(s) disconnected; failover to a healthy provider is required.'}
    if 'high' in rec_severities:
        return {'level': RISK_HIGH, 'reason': 'A high-severity integration finding is unresolved.'}

    # Medium: degraded secondary / recurring webhook retries / medium findings.
    failing_webhooks = [w for w in webhook_rows if w['status'] in {'Degraded', 'Failed'}]
    if degraded or failing_webhooks or 'medium' in rec_severities:
        bits = []
        if degraded:
            bits.append(f'{len(degraded)} provider(s) degraded')
        if failing_webhooks:
            bits.append(f'{len(failing_webhooks)} webhook(s) unstable')
        return {'level': RISK_MEDIUM, 'reason': '; '.join(bits) or 'One or more medium findings are open.'}

    return {'level': RISK_LOW, 'reason': 'All configured integrations are healthy with no material findings.'}


def summarize(
    *,
    provider_rows: list[dict[str, Any]],
    webhook_rows: list[dict[str, Any]],
    api_rows: list[dict[str, Any]],
    open_recommendation_count: int,
) -> dict[str, Any]:
    total = len(provider_rows)
    return {
        'total_integrations': total + len(webhook_rows) + len(api_rows),
        'total_providers': total,
        'healthy': sum(1 for p in provider_rows if p['status'] == PROVIDER_STATUS_HEALTHY),
        'degraded': sum(1 for p in provider_rows if p['status'] == PROVIDER_STATUS_DEGRADED),
        'disconnected': sum(1 for p in provider_rows if p['status'] == PROVIDER_STATUS_DISCONNECTED),
        'unknown': sum(1 for p in provider_rows if p['status'] == PROVIDER_STATUS_UNKNOWN),
        'webhooks': len(webhook_rows),
        'apis': len(api_rows),
        'recommendations': open_recommendation_count,
    }
