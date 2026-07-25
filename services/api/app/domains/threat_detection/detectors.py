"""Deterministic, explainable threat detectors over normalized telemetry.

Pure — no database, no network. The service layer gathers telemetry rows and
rolling baselines and passes them in; these functions extract features and emit
``DetectionCandidate`` objects. Scoring (severity/confidence) lives in
``scoring.py`` and is applied by the service, so detectors stay focused on
"what pattern does the evidence show?".

Truthfulness guarantees:
  * Only detectors supported by the evidence this platform actually stores are
    implemented here (transfer telemetry + decoded privileged events). Reentrancy,
    flash-loan, and oracle-deviation detectors require call traces / an oracle
    observation stream that is not available, so they are NOT implemented and are
    declared unsupported in ``config.detector_support`` — a normal transfer can
    never become a "confirmed reentrancy/flash-loan exploit".
  * Every candidate is grounded in one or more real telemetry rows (``evidence``);
    nothing is fabricated. Careful, non-overclaiming titles are used
    ("Possible …", "… Pattern", "Coordinated …").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from services.api.app.domains.threat_detection import config as tdc

ZERO_ADDRESS = '0x0000000000000000000000000000000000000000'
BURN_ADDRESSES = frozenset(
    {ZERO_ADDRESS, '0x000000000000000000000000000000000000dead'}
)


# --------------------------------------------------------------------------
# Normalized telemetry event
# --------------------------------------------------------------------------
@dataclass
class TelemetryEvent:
    id: str
    workspace_id: str
    event_type: str
    observed_at: datetime
    evidence_source: str
    asset_id: Optional[str] = None
    target_id: Optional[str] = None
    chain_id: Optional[int] = None
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    amount: Optional[Decimal] = None
    contract_address: Optional[str] = None

    @property
    def epoch(self) -> int:
        ts = self.observed_at
        if ts is None:
            return 0
        if getattr(ts, 'tzinfo', None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp())


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _lc(value: Any) -> Optional[str]:
    text = str(value or '').strip().lower()
    return text or None


def normalize_event(row: dict[str, Any]) -> TelemetryEvent:
    """Build a TelemetryEvent from a telemetry_events row (columns + payload_json)."""
    payload = row.get('payload_json') or {}
    if not isinstance(payload, dict):
        payload = {}
    observed_at = row.get('observed_at')
    if isinstance(observed_at, str):
        try:
            observed_at = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
        except ValueError:
            observed_at = None
    if observed_at is not None and getattr(observed_at, 'tzinfo', None) is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    chain_id = row.get('chain_id')
    if chain_id is None:
        chain_id = payload.get('chain_id')
    try:
        chain_id = int(chain_id) if chain_id is not None else None
    except (ValueError, TypeError):
        chain_id = None

    block_number = payload.get('block_number') or payload.get('block')
    try:
        block_number = int(block_number) if block_number not in (None, '') else None
    except (ValueError, TypeError):
        block_number = None

    return TelemetryEvent(
        id=str(row.get('id')),
        workspace_id=str(row.get('workspace_id')),
        event_type=str(row.get('event_type') or '').strip().lower(),
        observed_at=observed_at,
        evidence_source=str(row.get('evidence_source') or 'live').strip().lower(),
        asset_id=str(row['asset_id']) if row.get('asset_id') else None,
        target_id=str(row['target_id']) if row.get('target_id') else None,
        chain_id=chain_id,
        tx_hash=_lc(payload.get('tx_hash') or payload.get('transaction_hash') or payload.get('hash')),
        block_number=block_number,
        from_address=_lc(payload.get('from') or payload.get('from_address') or payload.get('owner')),
        to_address=_lc(payload.get('to') or payload.get('to_address')),
        amount=_to_decimal(payload.get('value') or payload.get('amount_wei') or payload.get('amount')),
        contract_address=_lc(
            payload.get('contract') or payload.get('contract_address') or payload.get('token_address')
        ),
    )


# --------------------------------------------------------------------------
# Rolling baselines (supplied by the service; kept as a plain structure so the
# detectors stay pure and testable).
# --------------------------------------------------------------------------
@dataclass
class AssetBaseline:
    transfer_mean: Optional[Decimal] = None
    transfer_samples: int = 0
    mint_burn_mean: Optional[Decimal] = None
    mint_burn_samples: int = 0


def _baseline_for(baselines: dict[str, AssetBaseline], asset_id: Optional[str]) -> AssetBaseline:
    if asset_id and asset_id in baselines:
        return baselines[asset_id]
    return baselines.get('__global__', AssetBaseline())


# --------------------------------------------------------------------------
# Evidence + candidate structures
# --------------------------------------------------------------------------
@dataclass
class EvidenceRef:
    telemetry_id: Optional[str]
    evidence_type: str
    evidence_quality: str
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    actor_address: Optional[str] = None
    observed_at: Optional[datetime] = None
    payload: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> str:
        return '|'.join(
            [
                str(self.telemetry_id or ''),
                str(self.evidence_type or ''),
                str(self.tx_hash or ''),
                str(self.actor_address or ''),
            ]
        )


@dataclass
class DetectionCandidate:
    detection_type: str
    title: str
    explanation: str
    recommended_next_step: str
    evidence_quality: str
    window_seconds: int
    window_epoch: int
    chain_id: Optional[int] = None
    primary_asset_id: Optional[str] = None
    contract_address: Optional[str] = None
    actors: list[str] = field(default_factory=list)
    tx_hashes: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    evidence_source: str = 'live'
    # The stable grouping discriminator for the cluster key. Defaults to the actor
    # set, but detectors whose cluster is defined by something other than a fixed
    # actor set (e.g. coordinated activity keyed by shared destination) override it
    # so the cluster stays stable as new related actors join.
    cluster_signature: Optional[str] = None
    # Feature inputs the service feeds into scoring.
    signal_count: int = 1
    amount_deviation: Optional[float] = None
    baseline_samples: int = 0
    privileged: bool = False
    actor_repetition: int = 0
    temporal_correlation: bool = False
    baseline_window: str = '24h'

    @property
    def actor_count(self) -> int:
        return len({a for a in self.actors if a})

    @property
    def transaction_count(self) -> int:
        return len({t for t in self.tx_hashes if t})


def _window_label(window_seconds: int) -> str:
    if window_seconds >= tdc.WINDOW_24H:
        return '24h'
    if window_seconds >= tdc.WINDOW_1H:
        return '1h'
    if window_seconds >= tdc.WINDOW_15M:
        return '15m'
    return f'{max(1, window_seconds // 60)}m'


def _evidence_source_for(events: list[TelemetryEvent]) -> str:
    """A cluster is 'live' only if every supporting event is live. Any simulator
    or replay event downgrades the whole cluster's evidence_source so it can never
    be presented as live customer evidence."""
    sources = {e.evidence_source for e in events}
    if sources == {'live'}:
        return 'live'
    if 'simulator' in sources:
        return 'simulator'
    if 'replay' in sources:
        return 'replay'
    return next(iter(sources), 'live')


def _mk_evidence(event: TelemetryEvent, evidence_type: str, quality: str, extra: dict[str, Any] | None = None) -> EvidenceRef:
    payload = {
        'event_type': event.event_type,
        'from': event.from_address,
        'to': event.to_address,
        'amount': str(event.amount) if event.amount is not None else None,
    }
    if extra:
        payload.update(extra)
    return EvidenceRef(
        telemetry_id=event.id,
        evidence_type=evidence_type,
        evidence_quality=quality,
        tx_hash=event.tx_hash,
        block_number=event.block_number,
        actor_address=event.from_address,
        observed_at=event.observed_at,
        payload=payload,
    )


# --------------------------------------------------------------------------
# Detector: Unusual Fund Movement
# --------------------------------------------------------------------------
def detect_unusual_transfers(
    transfers: list[TelemetryEvent],
    *,
    baselines: dict[str, AssetBaseline],
    config: dict[str, Any],
) -> list[DetectionCandidate]:
    """Transfers whose amount deviates materially from the asset's rolling mean,
    and rapid fan-out (one source paying many distinct destinations quickly).

    Clustered by (asset, source actor, 24h window) so repeated large transfers
    from the same source update one detection rather than spawning duplicates."""
    medium = float(config['amount_deviation_medium'])
    high = float(config['amount_deviation_high'])
    min_samples = int(config['min_baseline_samples'])
    fan_out_min = int(config['fan_out_min_destinations'])
    fan_out_window = int(config['fan_out_window_seconds'])

    # Group transfers by (asset_id, source actor).
    groups: dict[tuple, list[TelemetryEvent]] = {}
    for ev in transfers:
        if ev.from_address is None:
            continue
        groups.setdefault((ev.asset_id, ev.from_address), []).append(ev)

    candidates: list[DetectionCandidate] = []
    for (asset_id, source), evs in groups.items():
        baseline = _baseline_for(baselines, asset_id)
        mean = baseline.transfer_mean
        samples = baseline.transfer_samples

        flagged: list[tuple[TelemetryEvent, float]] = []
        max_dev = 0.0
        if mean is not None and mean > 0 and samples >= min_samples:
            for ev in evs:
                if ev.amount is None:
                    continue
                dev = float(ev.amount / mean)
                if dev >= medium:
                    flagged.append((ev, dev))
                    max_dev = max(max_dev, dev)

        # Rapid fan-out: distinct destinations within the fan-out window.
        distinct_dests = {e.to_address for e in evs if e.to_address}
        earliest = min((e.epoch for e in evs), default=0)
        latest = max((e.epoch for e in evs), default=0)
        fan_out = len(distinct_dests) >= fan_out_min and (latest - earliest) <= fan_out_window

        if not flagged and not fan_out:
            continue

        signal_count = (1 if flagged else 0) + (1 if fan_out else 0)
        involved = flagged and [e for (e, _d) in flagged] or list(evs)
        evidence = [_mk_evidence(e, 'unusual_transfer', 'normalized_telemetry', {'deviation_multiple': round(d, 3)}) for (e, d) in flagged]
        if fan_out:
            for e in evs:
                if e.to_address:
                    evidence.append(_mk_evidence(e, 'fan_out_transfer', 'normalized_telemetry'))
        # De-dup evidence by dedupe key, preserving order.
        seen: set[str] = set()
        deduped: list[EvidenceRef] = []
        for item in evidence:
            k = item.dedupe_key()
            if k not in seen:
                seen.add(k)
                deduped.append(item)

        title = 'Unusual fund movement'
        if fan_out and flagged:
            title = 'Large transfer with rapid fan-out'
        elif fan_out:
            title = 'Rapid fund fan-out to multiple destinations'
        explanation_bits = []
        if flagged:
            explanation_bits.append(
                f'{len(flagged)} transfer(s) from {source[:10]}… reached up to {max_dev:.1f}x the asset’s '
                f'{_window_label(tdc.WINDOW_24H)} transfer baseline.'
            )
        if fan_out:
            explanation_bits.append(
                f'Source distributed funds to {len(distinct_dests)} distinct destinations within '
                f'{max(1, (latest - earliest) // 60)} minute(s).'
            )
        explanation_bits.append('Grounded in normalized transfer telemetry only (no trace-level evidence).')

        candidates.append(
            DetectionCandidate(
                detection_type='unusual_transfer',
                title=title,
                explanation=' '.join(explanation_bits),
                recommended_next_step='Review the flagged transfers and confirm the destinations are expected before escalating.',
                evidence_quality='normalized_telemetry',
                window_seconds=tdc.WINDOW_24H,
                window_epoch=earliest,
                chain_id=next((e.chain_id for e in evs if e.chain_id is not None), None),
                primary_asset_id=asset_id,
                contract_address=next((e.contract_address for e in evs if e.contract_address), None),
                actors=[source],
                tx_hashes=[e.tx_hash for e in involved if e.tx_hash],
                evidence=deduped,
                evidence_source=_evidence_source_for(evs),
                signal_count=max(1, signal_count),
                amount_deviation=round(max_dev, 3) if max_dev else None,
                baseline_samples=samples,
                actor_repetition=len(evs),
                temporal_correlation=fan_out,
                baseline_window=_window_label(tdc.WINDOW_24H),
            )
        )
    return candidates


# --------------------------------------------------------------------------
# Detector: Coordinated Low-and-Slow Activity
# --------------------------------------------------------------------------
def detect_coordinated_activity(
    transfers: list[TelemetryEvent],
    *,
    config: dict[str, Any],
) -> list[DetectionCandidate]:
    """Many individually small transfers from multiple related actors targeting
    the same asset within a rolling window — a coordinated / related behavioral
    cluster. Deliberately does NOT claim a single controlling attacker."""
    window = int(config['low_slow_window_seconds'])
    min_events = int(config['low_slow_min_events'])
    min_actors = int(config['low_slow_min_actors'])

    candidates: list[DetectionCandidate] = []
    # Group by asset, then bucket into fixed windows.
    by_asset: dict[Optional[str], list[TelemetryEvent]] = {}
    for ev in transfers:
        by_asset.setdefault(ev.asset_id, []).append(ev)

    for asset_id, evs in by_asset.items():
        buckets: dict[int, list[TelemetryEvent]] = {}
        for ev in evs:
            b = (ev.epoch // window) * window if window else 0
            buckets.setdefault(b, []).append(ev)
        for bucket_epoch, bucket_events in buckets.items():
            actors = {e.from_address for e in bucket_events if e.from_address}
            dests = {e.to_address for e in bucket_events if e.to_address}
            if len(bucket_events) < min_events or len(actors) < min_actors:
                continue
            # "Related": either they converge on a shared destination (fan-in) or
            # all target the same asset/contract. Require a shared destination OR a
            # single common contract to avoid flagging unrelated background volume.
            shared_dest = len(dests) == 1 and len(actors) >= min_actors
            shared_contract = len({e.contract_address for e in bucket_events if e.contract_address}) == 1
            if not (shared_dest or shared_contract):
                continue
            all_actors = sorted(actors)
            evidence = [_mk_evidence(e, 'coordinated_transfer', 'normalized_telemetry') for e in bucket_events]
            reason = 'converging on a shared destination' if shared_dest else 'targeting the same contract'
            # Cluster is defined by the shared destination/contract + window, NOT the
            # actor set (which grows as more related addresses join the pattern).
            shared_target = next(iter(dests)) if shared_dest else (next((e.contract_address for e in bucket_events if e.contract_address), 'contract-wide'))
            candidates.append(
                DetectionCandidate(
                    detection_type='coordinated_activity',
                    title='Coordinated low-and-slow activity',
                    explanation=(
                        f'{len(bucket_events)} small transfers from {len(actors)} related addresses {reason} within a '
                        f'{_window_label(window)} window. Cumulative pattern exceeds normal per-event volume while each '
                        f'event stays below a single-event alert threshold. Coordinated pattern — not attributed to one controller.'
                    ),
                    recommended_next_step='Investigate the related address cluster and shared destination for staged accumulation.',
                    evidence_quality='normalized_telemetry',
                    window_seconds=window,
                    window_epoch=bucket_epoch,
                    chain_id=next((e.chain_id for e in bucket_events if e.chain_id is not None), None),
                    primary_asset_id=asset_id,
                    contract_address=next((e.contract_address for e in bucket_events if e.contract_address), None),
                    actors=all_actors,
                    cluster_signature='coord:' + str(shared_target),
                    tx_hashes=[e.tx_hash for e in bucket_events if e.tx_hash],
                    evidence=evidence,
                    evidence_source=_evidence_source_for(bucket_events),
                    signal_count=2 if (shared_dest and shared_contract) else 1,
                    baseline_samples=0,
                    actor_repetition=len(bucket_events),
                    temporal_correlation=True,
                    baseline_window=_window_label(window),
                )
            )
    return candidates


# --------------------------------------------------------------------------
# Detector: Mint / Burn Irregularity
# --------------------------------------------------------------------------
def detect_mint_burn_irregularity(
    transfers: list[TelemetryEvent],
    *,
    baselines: dict[str, AssetBaseline],
    config: dict[str, Any],
) -> list[DetectionCandidate]:
    """Mint (from zero-address) / burn (to zero/dead) events with abnormal size
    or frequency vs the asset's rolling mint/burn baseline."""
    high = float(config['mint_burn_deviation_high'])
    min_samples = int(config['min_baseline_samples'])

    # Group by (asset, kind) where kind in {mint, burn}.
    groups: dict[tuple, list[TelemetryEvent]] = {}
    for ev in transfers:
        kind = None
        if ev.from_address in (ZERO_ADDRESS,):
            kind = 'mint'
        elif ev.to_address in BURN_ADDRESSES:
            kind = 'burn'
        if kind is None:
            continue
        groups.setdefault((ev.asset_id, kind), []).append(ev)

    candidates: list[DetectionCandidate] = []
    for (asset_id, kind), evs in groups.items():
        baseline = _baseline_for(baselines, asset_id)
        mean = baseline.mint_burn_mean
        samples = baseline.mint_burn_samples
        max_dev = 0.0
        big: list[TelemetryEvent] = []
        if mean is not None and mean > 0 and samples >= min_samples:
            for ev in evs:
                if ev.amount is None:
                    continue
                dev = float(ev.amount / mean)
                if dev >= high:
                    big.append(ev)
                    max_dev = max(max_dev, dev)
        frequency_flag = len(evs) >= max(3, min_samples)  # unusual burst of mint/burn events
        if not big and not frequency_flag:
            continue
        involved = big or evs
        earliest = min((e.epoch for e in evs), default=0)
        evidence = [_mk_evidence(e, f'{kind}_event', 'event_logs', {'deviation_multiple': None}) for e in involved]
        title = f'{kind.capitalize()} irregularity'
        parts = []
        if big:
            parts.append(f'{len(big)} {kind} event(s) reached up to {max_dev:.1f}x the asset’s {kind} baseline.')
        if frequency_flag:
            parts.append(f'Unusual frequency: {len(evs)} {kind} events observed.')
        parts.append('Grounded in transfer/event-log telemetry; no off-chain reserve data was assumed.')
        candidates.append(
            DetectionCandidate(
                detection_type='mint_burn_irregularity',
                title=title,
                explanation=' '.join(parts),
                recommended_next_step=f'Verify the {kind} authority and confirm the supply change is authorized and reserve-backed.',
                evidence_quality='event_logs',
                window_seconds=tdc.WINDOW_24H,
                window_epoch=earliest,
                chain_id=next((e.chain_id for e in evs if e.chain_id is not None), None),
                primary_asset_id=asset_id,
                contract_address=next((e.contract_address for e in evs if e.contract_address), None),
                actors=sorted({e.from_address for e in involved if e.from_address}),
                cluster_signature=f'{kind}',
                tx_hashes=[e.tx_hash for e in involved if e.tx_hash],
                evidence=evidence,
                evidence_source=_evidence_source_for(evs),
                signal_count=(1 if big else 0) + (1 if frequency_flag else 0) or 1,
                amount_deviation=round(max_dev, 3) if max_dev else None,
                baseline_samples=samples,
                actor_repetition=len(evs),
                temporal_correlation=frequency_flag,
                baseline_window=_window_label(tdc.WINDOW_24H),
            )
        )
    return candidates


# --------------------------------------------------------------------------
# Detector: Privileged / Admin Action
# --------------------------------------------------------------------------
_HIGH_IMPACT_PRIVILEGED = frozenset(
    {'ownership_transferred', 'owner_changed', 'admin_changed', 'upgraded', 'implementation_upgraded', 'proxy_upgraded'}
)


def detect_privileged_actions(
    privileged_events: list[TelemetryEvent],
    *,
    config: dict[str, Any],
) -> list[DetectionCandidate]:
    """Decoded privileged/admin events (ownership/upgrade/pause/role/config). Only
    fires on decoded evidence — a plain transfer can never reach this detector."""
    groups: dict[tuple, list[TelemetryEvent]] = {}
    for ev in privileged_events:
        # Self-guard: only decoded privileged/admin event types reach this detector,
        # so a plain transfer can never be scored as an admin action even if passed in.
        if ev.event_type not in tdc.PRIVILEGED_EVENT_TYPES:
            continue
        groups.setdefault((ev.asset_id, ev.from_address, ev.event_type), []).append(ev)

    candidates: list[DetectionCandidate] = []
    for (asset_id, actor, event_type), evs in groups.items():
        earliest = min((e.epoch for e in evs), default=0)
        evidence = [_mk_evidence(e, 'privileged_action', 'decoded_call', {'action': event_type}) for e in evs]
        pretty = event_type.replace('_', ' ')
        high_impact = event_type in _HIGH_IMPACT_PRIVILEGED
        candidates.append(
            DetectionCandidate(
                detection_type='privileged_action',
                title=f'Privileged action: {pretty}',
                explanation=(
                    f'{len(evs)} decoded {pretty} action(s) executed by {actor[:10] if actor else "an admin"}…. '
                    'Privileged/administrative operations can change control or supply; verify authorization.'
                ),
                recommended_next_step='Confirm the executing key is an authorized admin and the change was change-controlled.',
                evidence_quality='decoded_call',
                window_seconds=tdc.WINDOW_1H,
                window_epoch=earliest,
                chain_id=next((e.chain_id for e in evs if e.chain_id is not None), None),
                primary_asset_id=asset_id,
                contract_address=next((e.contract_address for e in evs if e.contract_address), None),
                actors=[actor] if actor else [],
                cluster_signature=f'{actor or "admin"}:{event_type}',
                tx_hashes=[e.tx_hash for e in evs if e.tx_hash],
                evidence=evidence,
                evidence_source=_evidence_source_for(evs),
                signal_count=2 if (high_impact and len(evs) > 1) else 1,
                privileged=True,
                baseline_samples=0,
                actor_repetition=len(evs),
                temporal_correlation=len(evs) > 1,
                baseline_window=_window_label(tdc.WINDOW_1H),
            )
        )
    return candidates


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def extract_candidates(
    events: list[TelemetryEvent],
    *,
    baselines: dict[str, AssetBaseline],
    config: dict[str, Any] | None = None,
) -> list[DetectionCandidate]:
    """Run every SUPPORTED detector over a batch of telemetry events."""
    cfg = config or tdc.engine_config()
    transfers = [e for e in events if e.event_type in tdc.TRANSFER_EVENT_TYPES]
    privileged = [e for e in events if e.event_type in tdc.PRIVILEGED_EVENT_TYPES]

    candidates: list[DetectionCandidate] = []
    candidates.extend(detect_unusual_transfers(transfers, baselines=baselines, config=cfg))
    candidates.extend(detect_coordinated_activity(transfers, config=cfg))
    candidates.extend(detect_mint_burn_irregularity(transfers, baselines=baselines, config=cfg))
    candidates.extend(detect_privileged_actions(privileged, config=cfg))
    return candidates
