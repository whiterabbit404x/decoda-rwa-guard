"""Ingestion: provider logs → normalized events → rules → findings.

One path for both lanes, so a historical backfill and a live poll can never
disagree about what an event is or what it means:

    RPC logs → abi.decode_log (normalizer) → enrich (block time, initiator)
             → service.insert_event (deduplicated) → detection.evaluate_event
             → analysis.generate_analysis → service.insert_finding

Rules run ONLY for a log that was newly stored. Re-reading a range (a retried
chunk, an overlapping scan) therefore never double counts a transfer in a
baseline, never re-fires a finding, and never duplicates telemetry.

Every row written carries ``monitoring_scope = external_public``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from services.api.app.domains.external_watchlist import abi
from services.api.app.domains.external_watchlist import analysis
from services.api.app.domains.external_watchlist import detection
from services.api.app.domains.external_watchlist import rpc
from services.api.app.domains.external_watchlist import service

logger = logging.getLogger(__name__)

INGEST_BACKFILL = 'historical_backfill'
INGEST_LIVE = 'live_poll'

_INITIATOR_CATEGORIES = frozenset({'access_control', 'upgradeability', 'emergency_control', 'multisig'})


@dataclass
class ChunkContext:
    watchlist: dict[str, Any]
    target: dict[str, Any]
    rule_ctx: detection.RuleContext
    rule_state: detection.RuleState
    client: rpc.ReadOnlyRpcClient
    ingest_source: str
    backfill_id: str | None = None
    #: Known (block, unix time) points used to date a block without a lookup.
    anchors: list[tuple[int, int]] = field(default_factory=list)
    avg_block_seconds: float = 2.0
    enrichment_limit: int = 40
    timestamp_cache: dict[int, int] = field(default_factory=dict)
    initiator_cache: dict[str, str | None] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: Set for a historical scan: the plan range being replayed. Rule state then
    #: belongs to the backfill job (see backfill.run_job), not to the target.
    history_range_index: int | None = None


def load_rule_state(
    connection: Any, target: dict[str, Any], *, runtime_state: dict[str, Any] | None = None,
) -> detection.RuleState:
    """Baselines are shared by both lanes (order-independent statistics). The
    runtime state — current owner, implementation, threshold, role holders,
    last oracle answer — is the target's live state unless a historical scan
    passes its own, because "previous state" must come from EARLIER blocks."""
    baselines: dict[tuple[str, str], detection.Baseline] = {}
    for row in service.load_baselines(connection, str(target['id'])):
        baselines[(str(row['token_address']), str(row['stat_kind']))] = detection.Baseline(
            sample_count=int(row.get('sample_count') or 0),
            mean=Decimal(str(row.get('mean') or 0)),
            max_value=Decimal(str(row['max_value'])) if row.get('max_value') is not None else None,
            updated_block=row.get('updated_block'),
        )
    if runtime_state is None:
        runtime_state = target.get('runtime_state') if isinstance(target.get('runtime_state'), dict) else {}
    return detection.RuleState(baselines=baselines, runtime_state=dict(runtime_state))


def persist_rule_state(
    connection: Any, target_id: str, state: detection.RuleState, *,
    backfill_id: str | None = None, history_range_index: int | None = None,
) -> None:
    for (token, kind), baseline in state.baselines.items():
        if not baseline.dirty:
            continue
        service.save_baseline(
            connection, target_id=target_id, token=token, kind=kind, sample_count=baseline.sample_count,
            mean=baseline.mean, max_value=baseline.max_value, updated_block=baseline.updated_block,
        )
        baseline.dirty = False
    if state.runtime_state_dirty:
        if backfill_id is not None:
            service.save_backfill_rule_state(
                connection, backfill_id, {'range_index': history_range_index, 'state': state.runtime_state},
            )
        else:
            service.save_runtime_state(connection, target_id, state.runtime_state)
        state.runtime_state_dirty = False


def estimate_timestamp(block: int, anchors: list[tuple[int, int]], avg_block_seconds: float) -> int | None:
    """Date a block from known anchors: interpolate between the two nearest,
    extrapolate from the nearest one with the network's block time otherwise."""
    points = sorted({(int(b), int(t)) for b, t in anchors if b is not None and t is not None})
    if not points:
        return None
    below = [p for p in points if p[0] <= block]
    above = [p for p in points if p[0] >= block]
    if below and above and below[-1][0] != above[0][0]:
        (b0, t0), (b1, t1) = below[-1], above[0]
        return int(t0 + (block - b0) * (t1 - t0) / (b1 - b0))
    nearest = min(points, key=lambda p: abs(p[0] - block))
    return int(nearest[1] + (block - nearest[0]) * avg_block_seconds)


def _enrich(ctx: ChunkContext, events: list[abi.DecodedLog]) -> None:
    """Block times and initiators, within a per-chunk lookup budget.

    A lookup that cannot be made (budget, provider error) never fails the
    chunk: the time is estimated from anchors and labelled as estimated, and an
    unknown initiator stays unknown rather than guessed.
    """
    remaining = max(0, int(ctx.enrichment_limit))
    for number in sorted({event.block_number for event in events if event.block_timestamp is None}):
        if number in ctx.timestamp_cache:
            continue
        if remaining <= 0:
            break
        remaining -= 1
        try:
            timestamp = rpc.block_timestamp(ctx.client, number, ctx.timestamp_cache)
        except (rpc.ExternalExecutionForbidden,):
            raise
        except Exception:  # noqa: BLE001 - estimated instead
            timestamp = None
        if timestamp:
            ctx.anchors.append((number, timestamp))
    for event in events:
        # Only administrative events get an upfront sender lookup: they are
        # rare and every one becomes a finding. High-volume transfers and oracle
        # rounds are looked up only if they produce a finding.
        if event.spec.category not in _INITIATOR_CATEGORIES or event.initiator:
            continue
        if event.tx_hash in ctx.initiator_cache:
            event.initiator = ctx.initiator_cache[event.tx_hash]
            continue
        if remaining <= 0:
            continue
        remaining -= 1
        try:
            event.initiator = rpc.transaction_sender(ctx.client, event.tx_hash)
        except (rpc.ExternalExecutionForbidden,):
            raise
        except Exception:  # noqa: BLE001 - left unknown
            event.initiator = None
        ctx.initiator_cache[event.tx_hash] = event.initiator
    ctx.enrichment_limit = remaining


def _observed_time(ctx: ChunkContext, event: abi.DecodedLog) -> tuple[datetime, str, datetime | None]:
    if event.block_timestamp is not None:
        return event.block_timestamp, 'block_timestamp', event.block_timestamp
    cached = ctx.timestamp_cache.get(event.block_number)
    if cached:
        moment = datetime.fromtimestamp(cached, tz=timezone.utc)
        return moment, 'block_timestamp', moment
    estimated = estimate_timestamp(event.block_number, ctx.anchors, ctx.avg_block_seconds)
    if estimated:
        return datetime.fromtimestamp(estimated, tz=timezone.utc), 'estimated_from_block', None
    return ctx.now, 'ingested_at', None


def _finding_initiator(ctx: ChunkContext, draft: detection.FindingDraft) -> str | None:
    if draft.initiator or not draft.tx_hash:
        return draft.initiator
    if draft.tx_hash in ctx.initiator_cache:
        return ctx.initiator_cache[draft.tx_hash]
    if ctx.enrichment_limit <= 0:
        return None
    ctx.enrichment_limit -= 1
    try:
        sender = rpc.transaction_sender(ctx.client, draft.tx_hash)
    except rpc.ExternalExecutionForbidden:
        raise
    except Exception:  # noqa: BLE001
        sender = None
    ctx.initiator_cache[draft.tx_hash] = sender
    return sender


def store_findings(
    connection: Any, ctx: ChunkContext, drafts: list[detection.FindingDraft], *, primary_event_id: str | None,
) -> int:
    created = 0
    for draft in drafts:
        draft.initiator = _finding_initiator(ctx, draft)
        facts = analysis.build_facts(draft=draft, watchlist=ctx.watchlist, target=ctx.target)
        finding_id = service.insert_finding(connection, {
            'watchlist_id': str(ctx.watchlist['id']),
            'target_id': str(ctx.target['id']),
            'rule_key': draft.rule_key,
            'detection_profile': draft.detection_profile,
            'finding_type': draft.finding_type,
            'finding_class': draft.finding_class,
            'title': draft.title,
            'severity': draft.severity,
            'confidence': draft.confidence,
            'network': ctx.target['network'],
            'chain_id': ctx.target['chain_id'],
            'contract_address': draft.contract_address,
            'tx_hash': draft.tx_hash,
            'block_number': draft.block_number,
            'log_index': draft.log_index,
            'observed_at': draft.observed_at,
            'initiator': draft.initiator,
            'primary_event_id': primary_event_id,
            'related_event_ids': [primary_event_id] if primary_event_id else [],
            'decoded': draft.decoded,
            'previous_state': draft.previous_state,
            'new_state': draft.new_state,
            'explanation': draft.explanation,
            'ai_analysis': analysis.generate_analysis(facts),
            'score_inputs': draft.score_inputs,
            'dedupe_key': draft.dedupe_key,
        })
        if finding_id:
            created += 1
    return created


def process_logs(connection: Any, ctx: ChunkContext, logs: list[dict[str, Any]]) -> dict[str, Any]:
    """Store and evaluate one chunk of logs. The caller owns the transaction."""
    decoded = sorted(
        (event for event in (abi.decode_log(log) for log in logs) if event is not None),
        key=lambda event: event.sort_key,
    )
    stats = {'logs': len(logs), 'decoded': len(decoded), 'events_inserted': 0, 'findings_created': 0}
    if not decoded:
        return stats
    _enrich(ctx, decoded)
    latest_event_at: datetime | None = None
    for event in decoded:
        observed_at, observed_source, block_time = _observed_time(ctx, event)
        event_id = service.insert_event(connection, {
            'watchlist_id': str(ctx.watchlist['id']),
            'target_id': str(ctx.target['id']),
            'network': ctx.target['network'],
            'chain_id': ctx.target['chain_id'],
            'contract_address': event.contract_address,
            'block_number': event.block_number,
            'block_hash': event.block_hash,
            'tx_hash': event.tx_hash,
            'log_index': event.log_index,
            'event_name': event.event_name,
            'event_type': event.spec.event_type,
            'event_category': event.spec.category,
            'topic0': event.topic0,
            'decoded': event.decoded,
            'decode_status': event.decode_status,
            'initiator': event.initiator,
            'raw_log': event.raw_log,
            'payload_sha256': event.payload_sha256,
            'ingest_source': ctx.ingest_source,
            'backfill_id': ctx.backfill_id,
            'block_timestamp': block_time,
            'observed_at': observed_at,
            'observed_at_source': observed_source,
        })
        if event_id is None:
            continue
        stats['events_inserted'] += 1
        latest_event_at = observed_at if latest_event_at is None or observed_at > latest_event_at else latest_event_at
        drafts = detection.evaluate_event(ctx.rule_ctx, event, ctx.rule_state, observed_at=observed_at)
        if drafts:
            stats['findings_created'] += store_findings(connection, ctx, drafts, primary_event_id=event_id)
    persist_rule_state(
        connection, str(ctx.target['id']), ctx.rule_state,
        backfill_id=ctx.backfill_id if ctx.history_range_index is not None else None,
        history_range_index=ctx.history_range_index,
    )
    if latest_event_at is not None:
        connection.execute(
            """UPDATE external_watchlist_targets
               SET last_event_at = GREATEST(COALESCE(last_event_at, %s), %s), updated_at = NOW()
               WHERE id = %s""",
            (latest_event_at, latest_event_at, str(ctx.target['id'])),
        )
    return stats
