"""Event catalog and log decoding for external public monitoring (the normalizer).

Pure: no database, no network. Turns raw ``eth_getLogs`` entries into
normalized, decoded events the detection rules consume.

Topic hashes are keccak256 of the canonical event signature. Python's stdlib
has no keccak256 (``hashlib.sha3_256`` is FIPS SHA-3, a different function), so
they are written out here — the same convention ``onboarding_discovery`` uses
for function selectors — and a test recomputes every one of them with an
independent Keccak implementation, so a mistyped constant cannot ship.

Canonical ``event_type`` values reuse the threat-detection engine's vocabulary
(``PRIVILEGED_EVENT_TYPES`` / ``TRANSFER_EVENT_TYPES``) so the existing
detectors can evaluate external events without a second rule language.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from services.api.app.evidence_signing import canonical_json

ZERO_ADDRESS = '0x0000000000000000000000000000000000000000'
DEAD_ADDRESS = '0x000000000000000000000000000000000000dead'
BURN_ADDRESSES = frozenset({ZERO_ADDRESS, DEAD_ADDRESS})
ZERO_WORD = '0x' + '0' * 64

_ADDRESS_RE = re.compile(r'^0x[0-9a-f]{40}$')
_WORD_RE = re.compile(r'^0x[0-9a-f]{64}$')

# keccak256(signature) — verified in test_external_watchlist_abi.py.
TOPIC_ROLE_GRANTED = '0x2f8788117e7eff1d82e926ec794901d17c78024a50270940304540a733656f0d'
TOPIC_ROLE_REVOKED = '0xf6391f5c32d9c69d2a47ea670b442974b53935d1edc7fd64eb21e047a839171b'
TOPIC_ROLE_ADMIN_CHANGED = '0xbd79b86ffe0ab8e8776151514217cd7cacd52c909f66475c3af44e129f0b00ff'
TOPIC_OWNERSHIP_TRANSFERRED = '0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0'
TOPIC_OWNERSHIP_TRANSFER_STARTED = '0x38d16b8cac22d99fc7c124b9cd0de2d3fa1faef420bfe791d8c362d765e22700'
TOPIC_UPGRADED = '0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b'
TOPIC_ADMIN_CHANGED = '0x7e644d79422f17c01e4894b5f4f588d331ebfa28653d42ae832dc59e38c9798f'
TOPIC_BEACON_UPGRADED = '0x1cf3b03a6cf19fa2baba4df148e9dcabedea7f8a5c07840e207e5c089be95d3e'
TOPIC_DIAMOND_CUT = '0x8faa70878671ccd212d20771b795c50af8fd3ff6cf27f4bde57e5d4de0aeb673'
TOPIC_PAUSED = '0x62e78cea01bee320cd4e420270b5ea74000d11b0c9f74754ebdbfc544b05a258'
TOPIC_UNPAUSED = '0x5db9ee0a495bf2e6ff9c91a7834c1ba4fdd244a5e8aa4e537bd38aeae4b073aa'
TOPIC_TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
TOPIC_SAFE_ADDED_OWNER = '0x9465fa0c962cc76958e6373a993326400c1c94f8be2fe3a952adfa7f60b2ea26'
TOPIC_SAFE_REMOVED_OWNER = '0xf8d49fc529812e9a7c5c50e69c20f0dccc0db8fa95c98bc58cc9a4f1c1299eaf'
TOPIC_SAFE_CHANGED_THRESHOLD = '0x610f7ff2b304ae8903c3de74c60c6ab1f7d6226b3f52c5161905bb5ad4039c93'
TOPIC_SAFE_ENABLED_MODULE = '0xecdf3a3effea5783a3c4c2140e677577666428d44ed9d474a0b3a4c9943f8440'
TOPIC_SAFE_DISABLED_MODULE = '0xaab4fa2b463f581b2b32cb3b7e3b704b9ce37cc209b5fb4d77e593ace4054276'
TOPIC_SAFE_CHANGED_GUARD = '0x1151116914515bc0891ff9047a6cb32cf902546f83066499bcf8ba33d2353fa2'
TOPIC_SAFE_CHANGED_FALLBACK_HANDLER = '0x5ac6c46c93c8d0e53714ba3b53db3e7c046da994313d7ed0d192028bc7c228b0'
TOPIC_SAFE_CHANGED_MASTER_COPY = '0x75e41bc35ff1bf14d81d1d2f649c0084a0f974f9289c803ec9898eeec4c8d0b8'
TOPIC_ANSWER_UPDATED = '0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f'

#: Well-known AccessControl role identifiers (keccak256 of the role name, except
#: DEFAULT_ADMIN_ROLE which OpenZeppelin defines as bytes32(0)). An unknown role
#: is shown by its raw identifier, never guessed.
ROLE_LABELS: dict[str, str] = {
    ZERO_WORD: 'DEFAULT_ADMIN_ROLE',
    '0x9f2df0fed2c77648de5860a4cc508cd0818c85b8b8a1ab4ceeef8d981c8956a6': 'MINTER_ROLE',
    '0x65d7a28e3265b37a6474929f336521b332c1681b933f6cb9f3376673440d862a': 'PAUSER_ROLE',
    '0x3c11d16cbaffd01df69ce1c404f6340ee057498f5f00246190ea54220576a848': 'BURNER_ROLE',
    '0x189ab7a9244df0848122154315af71fe140f3db0fe014031783b0946b8c9d2e3': 'UPGRADER_ROLE',
    '0x97667070c54ef182b0f5858b034beac1b6f3089aa2d3188bb1e8929f4fa9b929': 'OPERATOR_ROLE',
    '0xa49807205ce4d355092ef5a8a18f56e8913cf4a201fbe287825b095693c21775': 'ADMIN_ROLE',
    '0x241ecf16d79d0f8dbfb92cbc07fe17840425976cf0667f022fe9877caa831b08': 'MANAGER_ROLE',
    '0x68e79a7bf1e0bc45d0a330c573bc367f9cf464fd326078812f301165fbda4ef1': 'ORACLE_ROLE',
    '0x442a94f1a1fac79af32856af2a64f63648cfa2ef3b98610a5bb7cbec4cee6985': 'COMPLIANCE_ROLE',
    '0x92de27771f92d6942691d73358b3a4673e4880de8356f8f2cf452be87e02d363': 'FREEZER_ROLE',
    '0xcf6f9f892731e14b8859835f2ff35575f447fb501f46243c4eb8bac19e31a050': 'RESCUER_ROLE',
    '0x98db8a220cd0f09badce9f22d0ba7e93edb3d404448cc3560d391ab096ad16e9': 'BLACKLISTER_ROLE',
}

#: Roles whose grant changes who controls the contract itself.
HIGH_IMPACT_ROLES = frozenset({ZERO_WORD, '0x189ab7a9244df0848122154315af71fe140f3db0fe014031783b0946b8c9d2e3',
                               '0xa49807205ce4d355092ef5a8a18f56e8913cf4a201fbe287825b095693c21775'})

# Read-only call selectors (keccak256(signature)[:4]) used by diagnostics.
SELECTOR_GET_THRESHOLD = '0xe75235b8'   # getThreshold()
SELECTOR_GET_OWNERS = '0xa0e67e2b'      # getOwners()
SELECTOR_AGGREGATOR = '0x245a7bfc'      # aggregator()
SELECTOR_OWNER = '0x8da5cb5b'           # owner()
SELECTOR_DECIMALS = '0x313ce567'        # decimals()
SELECTOR_SYMBOL = '0x95d89b41'          # symbol()

#: EIP-1967 implementation slot (same constant onboarding_discovery reads).
EIP1967_IMPLEMENTATION_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'


@dataclass(frozen=True)
class EventSpec:
    name: str
    signature: str
    topic0: str
    event_type: str
    category: str
    profiles: tuple[str, ...]
    emitter_types: tuple[str, ...]


EVENT_SPECS: tuple[EventSpec, ...] = (
    EventSpec('RoleGranted', 'RoleGranted(bytes32,address,address)', TOPIC_ROLE_GRANTED,
              'role_granted', 'access_control', ('privileged_role_changes',), ('contract', 'oracle')),
    EventSpec('RoleRevoked', 'RoleRevoked(bytes32,address,address)', TOPIC_ROLE_REVOKED,
              'role_revoked', 'access_control', ('privileged_role_changes',), ('contract', 'oracle')),
    EventSpec('RoleAdminChanged', 'RoleAdminChanged(bytes32,bytes32,bytes32)', TOPIC_ROLE_ADMIN_CHANGED,
              'config_changed', 'access_control', ('privileged_role_changes',), ('contract', 'oracle')),
    EventSpec('OwnershipTransferred', 'OwnershipTransferred(address,address)', TOPIC_OWNERSHIP_TRANSFERRED,
              'ownership_transferred', 'access_control', ('ownership_changes',), ('contract', 'oracle')),
    EventSpec('OwnershipTransferStarted', 'OwnershipTransferStarted(address,address)', TOPIC_OWNERSHIP_TRANSFER_STARTED,
              'owner_changed', 'access_control', ('ownership_changes',), ('contract', 'oracle')),
    EventSpec('Upgraded', 'Upgraded(address)', TOPIC_UPGRADED,
              'implementation_upgraded', 'upgradeability', ('contract_upgrades',), ('contract', 'oracle')),
    EventSpec('BeaconUpgraded', 'BeaconUpgraded(address)', TOPIC_BEACON_UPGRADED,
              'proxy_upgraded', 'upgradeability', ('contract_upgrades',), ('contract', 'oracle')),
    EventSpec('DiamondCut', 'DiamondCut((address,uint8,bytes4[])[],address,bytes)', TOPIC_DIAMOND_CUT,
              'upgraded', 'upgradeability', ('contract_upgrades',), ('contract',)),
    EventSpec('AdminChanged', 'AdminChanged(address,address)', TOPIC_ADMIN_CHANGED,
              'admin_changed', 'upgradeability', ('proxy_admin_changes',), ('contract', 'oracle')),
    EventSpec('Paused', 'Paused(address)', TOPIC_PAUSED,
              'paused', 'emergency_control', ('pause_unpause',), ('contract',)),
    EventSpec('Unpaused', 'Unpaused(address)', TOPIC_UNPAUSED,
              'unpaused', 'emergency_control', ('pause_unpause',), ('contract',)),
    EventSpec('Transfer', 'Transfer(address,address,uint256)', TOPIC_TRANSFER,
              'erc20_transfer', 'token_operation', ('mint_burn', 'large_transfers'), ('contract',)),
    EventSpec('AddedOwner', 'AddedOwner(address)', TOPIC_SAFE_ADDED_OWNER,
              'owner_changed', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('RemovedOwner', 'RemovedOwner(address)', TOPIC_SAFE_REMOVED_OWNER,
              'owner_changed', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('ChangedThreshold', 'ChangedThreshold(uint256)', TOPIC_SAFE_CHANGED_THRESHOLD,
              'config_changed', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('EnabledModule', 'EnabledModule(address)', TOPIC_SAFE_ENABLED_MODULE,
              'config_changed', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('DisabledModule', 'DisabledModule(address)', TOPIC_SAFE_DISABLED_MODULE,
              'config_changed', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('ChangedGuard', 'ChangedGuard(address)', TOPIC_SAFE_CHANGED_GUARD,
              'config_changed', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('ChangedFallbackHandler', 'ChangedFallbackHandler(address)', TOPIC_SAFE_CHANGED_FALLBACK_HANDLER,
              'config_changed', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('ChangedMasterCopy', 'ChangedMasterCopy(address)', TOPIC_SAFE_CHANGED_MASTER_COPY,
              'implementation_upgraded', 'multisig', ('multisig_configuration',), ('multisig',)),
    EventSpec('AnswerUpdated', 'AnswerUpdated(int256,uint256,uint256)', TOPIC_ANSWER_UPDATED,
              'oracle_answer_updated', 'oracle', ('oracle_updates',), ('oracle',)),
)

EVENTS_BY_TOPIC: dict[str, EventSpec] = {spec.topic0: spec for spec in EVENT_SPECS}


# ── low-level ABI helpers ────────────────────────────────────────────────────
def normalize_address(value: Any) -> str | None:
    text = str(value or '').strip().lower()
    return text if _ADDRESS_RE.fullmatch(text) else None


def _hex_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value or '').strip().lower()
    if not text.startswith('0x'):
        return int(text) if text.isdigit() else None
    try:
        return int(text, 16)
    except ValueError:
        return None


def data_words(data: Any) -> list[str]:
    text = str(data or '').strip().lower()
    if text.startswith('0x'):
        text = text[2:]
    if len(text) % 64:
        text = text[: len(text) - (len(text) % 64)]
    return [text[index:index + 64] for index in range(0, len(text), 64)]


def word_to_address(word: str) -> str:
    return '0x' + str(word)[-40:].lower()


def word_to_uint(word: str) -> int:
    return int(word, 16)


def word_to_int256(word: str) -> int:
    value = int(word, 16)
    return value - (1 << 256) if value >= (1 << 255) else value


def _topic(topics: list[str], index: int) -> str | None:
    return topics[index] if index < len(topics) else None


def _address_arg(topics: list[str], words: list[str], topic_index: int, word_index: int) -> str:
    """An address that is indexed in some deployed versions and not in others
    (e.g. Safe 1.3.0 vs 1.4.1). Indexed wins when present."""
    topic = _topic(topics, topic_index)
    if topic is not None:
        return word_to_address(topic[2:])
    return word_to_address(words[word_index])


def role_label(role: str | None) -> str | None:
    return ROLE_LABELS.get(str(role or '').lower())


# ── per-event decoders ───────────────────────────────────────────────────────
def _decode_role(topics: list[str], words: list[str]) -> dict[str, Any]:
    role = topics[1]
    return {
        'role': role,
        'role_label': role_label(role),
        'account': word_to_address(topics[2][2:]),
        'sender': word_to_address(topics[3][2:]),
    }


def _decode_role_admin_changed(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {
        'role': topics[1],
        'role_label': role_label(topics[1]),
        'previous_admin_role': topics[2],
        'previous_admin_role_label': role_label(topics[2]),
        'new_admin_role': topics[3],
        'new_admin_role_label': role_label(topics[3]),
    }


def _decode_ownership(topics: list[str], words: list[str]) -> dict[str, Any]:
    if len(topics) >= 3:
        return {'previous_owner': word_to_address(topics[1][2:]), 'new_owner': word_to_address(topics[2][2:])}
    return {'previous_owner': word_to_address(words[0]), 'new_owner': word_to_address(words[1])}


def _decode_upgraded(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'implementation': _address_arg(topics, words, 1, 0)}


def _decode_beacon_upgraded(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'beacon': _address_arg(topics, words, 1, 0)}


def _decode_admin_changed(topics: list[str], words: list[str]) -> dict[str, Any]:
    if len(topics) >= 3:
        return {'previous_admin': word_to_address(topics[1][2:]), 'new_admin': word_to_address(topics[2][2:])}
    return {'previous_admin': word_to_address(words[0]), 'new_admin': word_to_address(words[1])}


def _decode_pause(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'account': _address_arg(topics, words, 1, 0)}


def _decode_transfer(topics: list[str], words: list[str]) -> dict[str, Any]:
    decoded: dict[str, Any] = {
        'from': word_to_address(topics[1][2:]),
        'to': word_to_address(topics[2][2:]),
    }
    if len(topics) >= 4:
        # ERC-721 shares the Transfer topic but indexes the token id and has no
        # amount. It is recorded, but never treated as a fungible amount.
        decoded['token_standard'] = 'erc721'
        decoded['token_id'] = str(word_to_uint(topics[3][2:]))
        decoded['value'] = None
        return decoded
    decoded['token_standard'] = 'erc20'
    decoded['value'] = str(word_to_uint(words[0]))
    return decoded


def _decode_safe_owner(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'owner': _address_arg(topics, words, 1, 0)}


def _decode_safe_threshold(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'threshold': word_to_uint(words[0]) if words else word_to_uint(topics[1][2:])}


def _decode_safe_module(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'module': _address_arg(topics, words, 1, 0)}


def _decode_safe_guard(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'guard': _address_arg(topics, words, 1, 0)}


def _decode_safe_handler(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'handler': _address_arg(topics, words, 1, 0)}


def _decode_safe_master_copy(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {'implementation': _address_arg(topics, words, 1, 0)}


def _decode_answer_updated(topics: list[str], words: list[str]) -> dict[str, Any]:
    return {
        'current': str(word_to_int256(topics[1][2:])),
        'round_id': str(word_to_uint(topics[2][2:])),
        'updated_at': word_to_uint(words[0]),
    }


def _decode_diamond_cut(topics: list[str], words: list[str]) -> dict[str, Any]:
    """Best-effort decode of the dynamic DiamondCut payload: the facet list, each
    facet's action and selector count, and the init address. Bounds-checked; a
    malformed payload raises and is recorded as 'partial'."""
    init = word_to_address(words[1])
    cuts_offset = word_to_uint(words[0]) // 32
    count = word_to_uint(words[cuts_offset])
    if count > 256:
        raise ValueError('implausible facet count')
    base = cuts_offset + 1
    facets: list[dict[str, Any]] = []
    for index in range(count):
        tuple_start = base + word_to_uint(words[base + index]) // 32
        facet_address = word_to_address(words[tuple_start])
        action = word_to_uint(words[tuple_start + 1])
        selectors_start = tuple_start + word_to_uint(words[tuple_start + 2]) // 32
        selector_count = word_to_uint(words[selectors_start])
        if selector_count > 4096:
            raise ValueError('implausible selector count')
        facets.append({
            'facet_address': facet_address,
            'action': {0: 'add', 1: 'replace', 2: 'remove'}.get(action, str(action)),
            'selector_count': selector_count,
        })
    return {'init': init, 'facet_cuts': facets, 'facet_cut_count': len(facets)}


_DECODERS: dict[str, Callable[[list[str], list[str]], dict[str, Any]]] = {
    'RoleGranted': _decode_role,
    'RoleRevoked': _decode_role,
    'RoleAdminChanged': _decode_role_admin_changed,
    'OwnershipTransferred': _decode_ownership,
    'OwnershipTransferStarted': _decode_ownership,
    'Upgraded': _decode_upgraded,
    'BeaconUpgraded': _decode_beacon_upgraded,
    'DiamondCut': _decode_diamond_cut,
    'AdminChanged': _decode_admin_changed,
    'Paused': _decode_pause,
    'Unpaused': _decode_pause,
    'Transfer': _decode_transfer,
    'AddedOwner': _decode_safe_owner,
    'RemovedOwner': _decode_safe_owner,
    'ChangedThreshold': _decode_safe_threshold,
    'EnabledModule': _decode_safe_module,
    'DisabledModule': _decode_safe_module,
    'ChangedGuard': _decode_safe_guard,
    'ChangedFallbackHandler': _decode_safe_handler,
    'ChangedMasterCopy': _decode_safe_master_copy,
    'AnswerUpdated': _decode_answer_updated,
}


@dataclass
class DecodedLog:
    spec: EventSpec
    contract_address: str
    block_number: int
    block_hash: str | None
    tx_hash: str
    log_index: int
    topic0: str
    decoded: dict[str, Any]
    decode_status: str
    raw_log: dict[str, Any]
    payload_sha256: str
    block_timestamp: datetime | None = None
    initiator: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def event_name(self) -> str:
        return self.spec.name

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.block_number, self.log_index)


def canonical_raw_log(log: dict[str, Any]) -> dict[str, Any]:
    """The public fields of a log, normalised, in the form that is hashed."""
    return {
        'address': str(log.get('address') or '').lower(),
        'topics': [str(topic or '').lower() for topic in (log.get('topics') or [])],
        'data': str(log.get('data') or '0x').lower(),
        'blockNumber': str(log.get('blockNumber') or '').lower(),
        'blockHash': str(log.get('blockHash') or '').lower() or None,
        'transactionHash': str(log.get('transactionHash') or '').lower(),
        'transactionIndex': str(log.get('transactionIndex') or '').lower() or None,
        'logIndex': str(log.get('logIndex') or '').lower(),
    }


def payload_sha256(raw: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(raw)).hexdigest()


def decode_log(log: dict[str, Any]) -> DecodedLog | None:
    """Decode one provider log. Returns None for a log this catalog does not
    cover, a log a reorg removed, or a structurally unusable entry."""
    if not isinstance(log, dict) or log.get('removed') is True:
        return None
    topics = [str(topic or '').lower() for topic in (log.get('topics') or [])]
    if not topics or not _WORD_RE.fullmatch(topics[0]):
        return None
    spec = EVENTS_BY_TOPIC.get(topics[0])
    if spec is None:
        return None
    contract_address = normalize_address(log.get('address'))
    tx_hash = str(log.get('transactionHash') or '').lower()
    block_number = _hex_int(log.get('blockNumber'))
    log_index = _hex_int(log.get('logIndex'))
    if contract_address is None or not _WORD_RE.fullmatch(tx_hash) or block_number is None or log_index is None:
        return None
    block_hash = str(log.get('blockHash') or '').lower() or None
    if block_hash is not None and not _WORD_RE.fullmatch(block_hash):
        block_hash = None
    words = data_words(log.get('data'))
    try:
        decoded = _DECODERS[spec.name](topics, words)
        decode_status = 'decoded'
    except Exception:  # noqa: BLE001 - a malformed payload is recorded, not dropped
        decoded = {}
        decode_status = 'undecoded'
    if spec.name == 'DiamondCut' and decode_status == 'decoded':
        decode_status = 'partial' if not decoded.get('facet_cuts') else 'decoded'
    raw = canonical_raw_log(log)
    timestamp = _hex_int(log.get('blockTimestamp'))
    return DecodedLog(
        spec=spec,
        contract_address=contract_address,
        block_number=int(block_number),
        block_hash=block_hash,
        tx_hash=tx_hash,
        log_index=int(log_index),
        topic0=topics[0],
        decoded=decoded,
        decode_status=decode_status,
        raw_log=raw,
        payload_sha256=payload_sha256(raw),
        block_timestamp=(
            datetime.fromtimestamp(int(timestamp), tz=timezone.utc) if timestamp and timestamp > 0 else None
        ),
    )


# ── log filters per target ───────────────────────────────────────────────────
def emitter_topics(profiles: list[str] | tuple[str, ...], target_type: str) -> list[str]:
    """topic0 values to request from logs EMITTED by a target of this type."""
    enabled = set(profiles)
    return sorted(
        spec.topic0 for spec in EVENT_SPECS
        if target_type in spec.emitter_types and enabled.intersection(spec.profiles)
    )


def padded_address_topic(address: str) -> str:
    return '0x' + '0' * 24 + address[2:]


def log_filters_for_target(
    *, target_type: str, address: str, profiles: list[str] | tuple[str, ...],
    extra_emitters: list[str] | None = None,
) -> list[dict[str, Any]]:
    """eth_getLogs filters (without a block range) covering one target.

    * a contract / oracle / multisig: every enabled event it EMITS
    * a wallet / multisig: token transfers where it is the sender or receiver
    """
    filters: list[dict[str, Any]] = []
    topics = emitter_topics(profiles, target_type)
    if topics:
        emitters = [address, *[a for a in (extra_emitters or []) if a and a != address]]
        filters.append({'address': emitters if len(emitters) > 1 else emitters[0], 'topics': [topics]})
    if target_type in ('wallet', 'multisig') and set(profiles).intersection({'large_transfers', 'mint_burn'}):
        padded = padded_address_topic(address)
        filters.append({'topics': [TOPIC_TRANSFER, padded]})
        filters.append({'topics': [TOPIC_TRANSFER, None, padded]})
    return filters
