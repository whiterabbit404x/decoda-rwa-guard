"""Provider-agnostic telemetry normalization + TRUTHFUL provenance resolution.

Two jobs:

1. Turn a stored ``telemetry_events`` row (or a live push from a low-latency
   provider) into one ``NormalizedChainEvent`` the matcher can reconcile,
   regardless of which provider produced it.

2. Decide — from runtime facts only — what the platform may CLAIM about that
   telemetry. This is the module that stops Screen 5 from saying "Preconfirmed"
   or "Flashblocks" when the ingestion path delivered neither.

On preconfirmation support, stated plainly:

    This repository has no Flashblocks / preconfirmation ingestion path. The
    live Base lanes are an RPC polling loop, a WebSocket ingestor, and
    QuickNode Streams — all of which deliver CONFIRMED or FINALIZED blocks.

``PreconfirmationTelemetryProvider`` below is the seam a real preconfirmation
provider would implement. Until one is registered, ``resolve_stage`` reports
CONFIRMED / FINALIZED / UNKNOWN, and the UI shows the actual source ("RPC
polling", "WebSocket", …). A stage is upgraded to PRECONFIRMATION only when a
registered provider actually delivered a preconfirmation and stamped it on the
event — never because a config flag says a provider "supports" it.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol, runtime_checkable

from services.api.app.domains.operational_integrity import schemas

# Zero address: a transfer FROM it is a mint, a transfer TO it is a burn.
ZERO_ADDRESS = '0x0000000000000000000000000000000000000000'
BURN_ADDRESSES = frozenset({ZERO_ADDRESS, '0x000000000000000000000000000000000000dead'})

# Event types that can carry an issuance/redemption.
_TRANSFER_EVENT_TYPES = frozenset({
    'erc20_transfer', 'token_transfer', 'transfer', 'native_transfer', 'wallet_transfer_detected',
})
_MINT_EVENT_TYPES = frozenset({'mint', 'tokens_minted', 'issuance'})
_BURN_EVENT_TYPES = frozenset({'burn', 'tokens_burned', 'redemption'})

# How an ingestion source maps to the stage it can honestly claim. Derived from
# what each lane actually delivers in this repository.
_SOURCE_STAGE: dict[str, str] = {
    'rpc_polling': schemas.STAGE_FINALIZED,
    'evm_rpc': schemas.STAGE_FINALIZED,
    'backfill': schemas.STAGE_FINALIZED,
    'realtime_websocket': schemas.STAGE_CONFIRMED,
    'websocket': schemas.STAGE_CONFIRMED,
    'quicknode_stream': schemas.STAGE_CONFIRMED,
    'quicknode_http_fast_tail': schemas.STAGE_CONFIRMED,
    'webhook': schemas.STAGE_CONFIRMED,
}

# Customer-facing source labels. The frontend maps the key; these exist so the
# backend and the UI cannot drift.
SOURCE_LABELS: dict[str, str] = {
    'rpc_polling': 'RPC polling',
    'evm_rpc': 'RPC polling',
    'backfill': 'RPC backfill',
    'realtime_websocket': 'WebSocket',
    'websocket': 'WebSocket',
    'quicknode_stream': 'Streams',
    'quicknode_http_fast_tail': 'HTTP fast tail',
    'webhook': 'Webhook',
    'manual': 'Manual / imported',
    'simulator': 'Simulator',
    'unknown': 'Unknown',
}


# A uint256 has up to 78 decimal digits. Decimal's DEFAULT context keeps only 28
# significant digits, and that rounding applies to ARITHMETIC (``abs(x)``,
# ``a - b``) even though it does not apply to ``Decimal(str)`` construction. A
# real token amount would therefore survive parsing and then be silently
# truncated the moment a variance was computed — a reconciliation value that is
# quietly wrong is worse than one that is missing. Every operational computation
# runs inside this wider context instead.
UINT256_DIGITS = 78
DECIMAL_PRECISION = UINT256_DIGITS + 12


def exact_context() -> decimal.Context:
    """A Decimal context wide enough for full-range uint256 arithmetic."""
    return decimal.Context(prec=DECIMAL_PRECISION)


def exact_decimal(value: Any) -> Optional[Decimal]:
    """Parse a base-unit amount as an exact Decimal. Never float."""
    if value is None or value == '':
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


# Kept as the module's public name (used by the matcher and the service).
to_units = exact_decimal


@runtime_checkable
class PreconfirmationTelemetryProvider(Protocol):
    """The seam a genuine low-latency preconfirmation source implements.

    A conforming provider pushes normalized events as soon as a sequencer
    preconfirms them (on Base that is the Flashblocks sub-block stream, roughly
    every ~200 ms), and MUST stamp ``stage=PRECONFIRMATION`` plus the instant the
    preconfirmation was received. Nothing else in this package may set that
    stage, which is what keeps the UI honest: no registered provider, no
    "Preconfirmed" badge.

    Implementations are responsible for their own transport, credentials, and
    reconnection. This package only consumes normalized output.
    """

    #: Stable provider key recorded as the detection's telemetry_source.
    source_key: str

    def is_available(self) -> bool:
        """Whether a real endpoint is configured AND currently connected. A
        provider that is merely importable is not available."""
        ...

    def normalize(self, raw: dict[str, Any]) -> 'NormalizedChainEvent':
        """Map one provider payload to the canonical normalized event."""
        ...


#: Registered preconfirmation providers, keyed by source_key. Empty in this
#: repository — see the module docstring. A deployment that adds one registers
#: it here at startup; nothing else changes.
_PRECONFIRMATION_PROVIDERS: dict[str, PreconfirmationTelemetryProvider] = {}


def register_preconfirmation_provider(provider: PreconfirmationTelemetryProvider) -> None:
    _PRECONFIRMATION_PROVIDERS[str(provider.source_key)] = provider


def preconfirmation_provider(source_key: Any) -> Optional[PreconfirmationTelemetryProvider]:
    return _PRECONFIRMATION_PROVIDERS.get(str(source_key or ''))


def preconfirmation_available() -> bool:
    """True only when a registered provider reports a live connection."""
    for provider in _PRECONFIRMATION_PROVIDERS.values():
        try:
            if provider.is_available():
                return True
        except Exception:  # noqa: BLE001 - a broken provider is simply not available
            continue
    return False


@dataclass(frozen=True)
class NormalizedChainEvent:
    """One on-chain event, provider-independent."""

    chain_id: Optional[int] = None
    tx_hash: Optional[str] = None
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    method_selector: Optional[str] = None
    decoded_operation: Optional[str] = None  # 'mint' | 'burn' | None
    amount: Optional[Decimal] = None
    token_address: Optional[str] = None
    token_decimals: Optional[int] = None
    token_symbol: Optional[str] = None
    block_number: Optional[int] = None
    observed_at: Any = None
    source: str = 'unknown'
    stage: str = schemas.STAGE_UNKNOWN
    preconfirmation_received_at: Any = None
    external_reference: Optional[str] = None
    evidence_source: str = 'live'
    telemetry_id: Optional[str] = None
    asset_id: Optional[str] = None
    #: True when the transaction's signature/inclusion evidence is present. The
    #: chain accepted the transaction, which is exactly the point: cryptographic
    #: validity is not operational authorization.
    signature_valid: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_issuance(self) -> bool:
        return str(self.decoded_operation or '').lower() == 'mint'

    @property
    def is_redemption(self) -> bool:
        return str(self.decoded_operation or '').lower() == 'burn'

    def as_dict(self) -> dict[str, Any]:
        return {
            'chain_id': self.chain_id,
            'tx_hash': self.tx_hash,
            'from': self.from_address,
            'to': self.to_address,
            'method_selector': self.method_selector,
            'decoded_operation': self.decoded_operation,
            'amount': None if self.amount is None else str(self.amount),
            'token': self.token_address,
            'token_decimals': self.token_decimals,
            'block_number': self.block_number,
            'observed_at': schemas._iso(self.observed_at),
            'source': self.source,
            'stage': self.stage,
        }


def decode_operation(event_type: Any, payload: dict[str, Any]) -> Optional[str]:
    """Deterministically decode 'mint' | 'burn' from a normalized payload.

    Explicit event types win. Otherwise a transfer FROM the zero address is a
    mint and a transfer TO a burn address is a burn. Anything else returns None
    — the matcher then reports OPERATION_NOT_DECODED rather than guessing.
    """
    et = str(event_type or '').strip().lower()
    if et in _MINT_EVENT_TYPES:
        return 'mint'
    if et in _BURN_EVENT_TYPES:
        return 'burn'
    explicit = str(payload.get('operation') or payload.get('decoded_operation') or '').strip().lower()
    if explicit in ('mint', 'burn'):
        return explicit
    if et not in _TRANSFER_EVENT_TYPES:
        return None
    frm = str(payload.get('from') or payload.get('from_address') or '').strip().lower()
    to = str(payload.get('to') or payload.get('to_address') or '').strip().lower()
    if frm == ZERO_ADDRESS:
        return 'mint'
    if to in BURN_ADDRESSES:
        return 'burn'
    return None


def resolve_source(provider_type: Any, payload: dict[str, Any]) -> str:
    """Canonical ingestion-source key for one telemetry row."""
    explicit = str(payload.get('ingestion_source') or payload.get('detected_by') or '').strip().lower()
    if explicit:
        return explicit
    pt = str(provider_type or '').strip().lower()
    if 'quicknode' in pt:
        return 'quicknode_stream'
    if 'webhook' in pt:
        return 'webhook'
    if pt in ('evm_rpc', 'rpc', 'rpc_polling'):
        return 'rpc_polling'
    return pt or 'unknown'


def resolve_stage(source: Any, payload: dict[str, Any]) -> str:
    """The stage the platform may honestly claim for this telemetry.

    PRECONFIRMATION is returned ONLY when the row carries an explicit
    preconfirmation stamp AND a provider registered for that source confirms it
    is available. Every other path resolves to what the lane actually delivers.
    """
    key = str(source or '').strip().lower()
    stamped = str(payload.get('telemetry_stage') or payload.get('stage') or '').strip().upper()
    if stamped == schemas.STAGE_PRECONFIRMATION:
        provider = preconfirmation_provider(key)
        try:
            if provider is not None and provider.is_available():
                return schemas.STAGE_PRECONFIRMATION
        except Exception:  # noqa: BLE001 - unavailable provider cannot upgrade the claim
            pass
        # A stamp with no live provider behind it is NOT evidence of a
        # preconfirmation. Fall through to what the source can actually prove.
    if stamped in (schemas.STAGE_CONFIRMED, schemas.STAGE_FINALIZED):
        return stamped
    mapped = _SOURCE_STAGE.get(key)
    if mapped:
        return mapped
    # A row with a block number was included in a block; without one we cannot
    # say more than UNKNOWN.
    block = payload.get('block_number') or payload.get('block')
    return schemas.STAGE_CONFIRMED if block else schemas.STAGE_UNKNOWN


def source_label(source: Any) -> str:
    key = str(source or '').strip().lower()
    return SOURCE_LABELS.get(key, key.replace('_', ' ') or 'Unknown')


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_telemetry_row(row: dict[str, Any]) -> NormalizedChainEvent:
    """Map a stored ``telemetry_events`` row to a NormalizedChainEvent.

    The row is the canonical persisted evidence; nothing here re-fetches from a
    provider, so normalization is reproducible from stored facts alone.
    """
    payload = row.get('payload_json') or {}
    if not isinstance(payload, dict):
        payload = {}
    source = resolve_source(row.get('provider_type'), payload)
    amount = to_units(
        payload.get('amount_base_units')
        or payload.get('value_base_units')
        or payload.get('amount')
        or payload.get('value')
    )
    return NormalizedChainEvent(
        chain_id=_int_or_none(payload.get('chain_id') or row.get('chain_id')),
        tx_hash=payload.get('tx_hash') or payload.get('transaction_hash') or payload.get('hash'),
        from_address=payload.get('from') or payload.get('from_address'),
        to_address=payload.get('to') or payload.get('to_address'),
        method_selector=payload.get('method_selector') or payload.get('selector'),
        decoded_operation=decode_operation(row.get('event_type'), payload),
        amount=amount,
        token_address=payload.get('token') or payload.get('token_address') or payload.get('contract_address'),
        token_decimals=_int_or_none(payload.get('token_decimals') or payload.get('decimals')),
        token_symbol=payload.get('token_symbol') or payload.get('symbol'),
        block_number=_int_or_none(payload.get('block_number') or payload.get('block')),
        observed_at=row.get('observed_at'),
        source=source,
        stage=resolve_stage(source, payload),
        preconfirmation_received_at=payload.get('preconfirmation_received_at'),
        external_reference=(
            payload.get('external_reference')
            or payload.get('subscription_id')
            or payload.get('business_reference')
        ),
        evidence_source=str(row.get('evidence_source') or 'live'),
        telemetry_id=str(row['id']) if row.get('id') else None,
        asset_id=str(row['asset_id']) if row.get('asset_id') else None,
        # The row exists because the transaction was accepted by the chain. The
        # signature is therefore valid — the whole point of this lane.
        signature_valid=bool(payload.get('tx_hash') or payload.get('transaction_hash') or payload.get('hash')),
        raw=payload,
    )
