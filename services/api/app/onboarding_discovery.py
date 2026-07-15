"""Deterministic EVM discovery + RPC benchmark engine for the Onboarding Agent.

This module contains ONLY deterministic, dependency-light logic: no database, no
web framework, no LLM. Every contract fact is derived from real JSON-RPC calls
(eth_chainId / eth_getCode / eth_call / eth_getStorageAt) through a pluggable
transport, so the engine is fully unit-testable offline with a fake transport
and the production path uses a real urllib transport with strict timeouts and an
SSRF guard.

Nothing here ever asks an LLM to determine a contract standard, role, capability,
or provider measurement — those come from bytecode selectors, ERC-165 interface
checks, EIP-1967 storage slots, and safe view calls, each recorded with a
detection method, source RPC host, block number, evidence, confidence class and
timestamp.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

# ---------------------------------------------------------------------------
# Confidence classes (truthfulness: heuristic findings are never "confirmed").
# ---------------------------------------------------------------------------
CONFIRMED = 'confirmed'
PROBABLE = 'probable'
UNKNOWN = 'unknown'
REQUIRES_REVIEW = 'requires_review'

ZERO_ADDRESS = '0x0000000000000000000000000000000000000000'

# Chain metadata. Kept local (small, deterministic) but aligned with the
# evm_activity_provider CHAIN_MAP the monitoring workers already use.
CHAIN_NAMES: dict[int, str] = {
    1: 'ethereum-mainnet',
    8453: 'base-mainnet',
    42161: 'arbitrum-one',
    10: 'optimism',
    137: 'polygon',
    11155111: 'ethereum-sepolia',
    84532: 'base-sepolia',
}
CHAIN_ALIASES: dict[str, int] = {
    'ethereum': 1, 'ethereum-mainnet': 1, 'mainnet': 1, 'eth': 1, 'eth-mainnet': 1,
    'base': 8453, 'base-mainnet': 8453,
    'arbitrum': 42161, 'arbitrum-one': 42161, 'arb': 42161,
    'optimism': 10, 'optimism-mainnet': 10, 'op': 10,
    'polygon': 137, 'polygon-mainnet': 137, 'matic': 137,
    'ethereum-sepolia': 11155111, 'sepolia': 11155111,
    'base-sepolia': 84532,
}

# Function selectors (keccak256(signature)[:4]). Hardcoded because Python's
# stdlib has no keccak256 (hashlib.sha3_* is FIPS SHA3, not Ethereum Keccak).
SELECTORS: dict[str, str] = {
    'name': '0x06fdde03',
    'symbol': '0x95d89b41',
    'decimals': '0x313ce567',
    'totalSupply': '0x18160ddd',
    'balanceOf': '0x70a08231',
    'transfer': '0xa9059cbb',
    'transferFrom': '0x23b872dd',
    'approve': '0x095ea7b3',
    'allowance': '0xdd62ed3e',
    'owner': '0x8da5cb5b',
    'transferOwnership': '0xf2fde38b',
    'renounceOwnership': '0x715018a6',
    'supportsInterface': '0x01ffc9a7',
    # ERC-4626 vault
    'asset': '0x38d52e0f',
    'totalAssets': '0x01e1d114',
    'convertToAssets': '0x07a2d13a',
    'convertToShares': '0xc6e6f592',
    # Pausable
    'paused': '0x5c975abb',
    'pause': '0x8456cb59',
    'unpause': '0x3f4ba83a',
    # Mint / burn
    'mint': '0x40c10f19',            # mint(address,uint256)
    'burn': '0x42966c68',            # burn(uint256)
    'burnFrom': '0x79cc6790',        # burnFrom(address,uint256)
    # AccessControl
    'hasRole': '0x91d14854',
    'grantRole': '0x2f2ff15d',
    'revokeRole': '0xd547741f',
    'renounceRole': '0x36568abe',
    'getRoleAdmin': '0x248a9ca3',
    'DEFAULT_ADMIN_ROLE': '0xa217fddf',
    # Upgradeable proxies
    'upgradeTo': '0x3659cfe6',
    'upgradeToAndCall': '0x4f1ef286',
    'implementation': '0x5c60da1b',
    'admin': '0xf851a440',
    # Blacklist / allowlist / freeze (common stablecoin controls)
    'isBlacklisted': '0xfe575a87',
    'addBlackList': '0x0ecb93c0',
    'blacklist': '0xf9f92be4',
    'isFrozen': '0xe5839836',
    'freeze': '0x8d1fdf2f',
    'freezeAccount': '0xe724529c',
    'isWhitelisted': '0x3af32abf',
    'addToWhitelist': '0xe43252d7',
    # Oracle (Chainlink-style aggregator)
    'latestRoundData': '0xfeaf968c',
    'latestAnswer': '0x50d25bcd',
    'decimalsOracle': '0x313ce567',
}

# ERC-165 interface IDs.
INTERFACE_IDS: dict[str, str] = {
    'ERC165': '0x01ffc9a7',
    'ERC721': '0x80ac58cd',
    'ERC721Metadata': '0x5b5e139f',
    'ERC721Enumerable': '0x780e9d63',
    'ERC1155': '0xd9b67a26',
    'ERC1155MetadataURI': '0x0e89341c',
    'AccessControl': '0x7965db0b',
    'ERC4626': '0x87dfe5a0',
}

# EIP-1967 standard storage slots.
EIP1967_IMPLEMENTATION_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
EIP1967_ADMIN_SLOT = '0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103'
EIP1967_BEACON_SLOT = '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50'
# Legacy OpenZeppelin implementation slot (pre-1967 fallback).
OZ_LEGACY_IMPL_SLOT = '0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3'

_RPC_TIMEOUT_DEFAULT = 8.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# ABI helpers (minimal, no eth-abi dependency).
# ---------------------------------------------------------------------------
def _strip0x(value: str) -> str:
    v = str(value or '')
    return v[2:] if v.lower().startswith('0x') else v


def _pad_left(hex_no_prefix: str, width: int = 64) -> str:
    return hex_no_prefix.rjust(width, '0')


def encode_calldata(selector_hex: str, *args: tuple[str, Any]) -> str:
    """Encode calldata for a static view call. Supports address/uint256/bytes4/bytes32."""
    data = _strip0x(selector_hex)
    for kind, raw in args:
        if kind == 'address':
            data += _pad_left(_strip0x(str(raw)).lower())
        elif kind in ('uint256', 'uint'):
            data += _pad_left(format(int(raw), 'x'))
        elif kind == 'bytes4':
            data += _strip0x(str(raw)).ljust(64, '0')
        elif kind == 'bytes32':
            data += _pad_left(_strip0x(str(raw)))
        else:
            raise ValueError(f'unsupported abi kind {kind}')
    return '0x' + data


def decode_uint(hexdata: str | None) -> int | None:
    raw = _strip0x(hexdata or '')
    if not raw:
        return None
    try:
        return int(raw[:64] or '0', 16)
    except ValueError:
        return None


def decode_bool(hexdata: str | None) -> bool | None:
    value = decode_uint(hexdata)
    if value is None:
        return None
    return value != 0


def decode_address(hexdata: str | None) -> str | None:
    raw = _strip0x(hexdata or '')
    if len(raw) < 64:
        return None
    tail = raw[-40:]
    if int(raw[:64] or '0', 16) == 0:
        return None
    return '0x' + tail


def decode_string(hexdata: str | None) -> str | None:
    """Decode an ABI dynamic string, tolerating bytes32-packed strings."""
    raw = _strip0x(hexdata or '')
    if not raw:
        return None
    try:
        if len(raw) >= 128:
            offset = int(raw[0:64], 16) * 2
            if offset + 64 <= len(raw):
                length = int(raw[offset:offset + 64], 16) * 2
                body = raw[offset + 64: offset + 64 + length]
                text = bytes.fromhex(body).decode('utf-8', errors='ignore').strip('\x00')
                if text:
                    return text
        # bytes32-packed string fallback (some legacy tokens)
        text = bytes.fromhex(raw[:64]).decode('utf-8', errors='ignore').strip('\x00')
        return text or None
    except (ValueError, UnicodeDecodeError):
        return None


def address_slot_value(slot_hex: str | None) -> str | None:
    """Extract an address from a 32-byte storage slot value (EIP-1967)."""
    raw = _strip0x(slot_hex or '')
    if not raw or int(raw or '0', 16) == 0:
        return None
    return '0x' + raw[-40:]


# ---------------------------------------------------------------------------
# Address validation (format + zero-address; EOA is checked separately via code).
# ---------------------------------------------------------------------------
def is_hex_address(value: str) -> bool:
    v = str(value or '').strip()
    if not v.lower().startswith('0x'):
        return False
    body = v[2:]
    if len(body) != 40:
        return False
    try:
        int(body, 16)
    except ValueError:
        return False
    return True


def normalize_address(value: str) -> str:
    return '0x' + _strip0x(str(value or '').strip()).lower()


class AddressValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_contract_address(value: str) -> str:
    """Validate format and reject the zero address. Returns normalized lowercase address."""
    raw = str(value or '').strip()
    if not raw:
        raise AddressValidationError('address_required', 'A contract address is required.')
    if not is_hex_address(raw):
        raise AddressValidationError(
            'invalid_address_format',
            'Invalid address format. Provide a 0x-prefixed 40-hex-character EVM address.',
        )
    normalized = normalize_address(raw)
    if normalized == ZERO_ADDRESS:
        raise AddressValidationError('zero_address', 'The zero address is not a valid contract.')
    return normalized


def resolve_chain_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.isdigit():
        return int(text)
    if text.startswith('0x'):
        try:
            return int(text, 16)
        except ValueError:
            return None
    return CHAIN_ALIASES.get(text)


def chain_network_name(chain_id: int | None) -> str:
    if chain_id is None:
        return 'unknown'
    return CHAIN_NAMES.get(int(chain_id), f'evm-{chain_id}')


# ---------------------------------------------------------------------------
# RPC transport abstraction + SSRF-guarded urllib implementation.
# ---------------------------------------------------------------------------
class RpcError(Exception):
    def __init__(self, message: str, *, kind: str = 'error', http_status: int | None = None):
        super().__init__(message)
        self.kind = kind  # timeout | dns_error | rate_limited | rpc_error | http_error | error
        self.http_status = http_status


@dataclass
class RpcCallResult:
    result: Any = None
    latency_ms: float = 0.0
    ok: bool = False
    error: str | None = None
    kind: str | None = None
    http_status: int | None = None


class RpcTransport:
    """Interface: call() raises RpcError; timed_call() never raises."""

    host: str = ''

    def call(self, method: str, params: list[Any], *, timeout: float | None = None) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def timed_call(self, method: str, params: list[Any], *, timeout: float | None = None) -> RpcCallResult:
        start = time.monotonic()
        try:
            result = self.call(method, params, timeout=timeout)
            return RpcCallResult(result=result, latency_ms=(time.monotonic() - start) * 1000.0, ok=True)
        except RpcError as exc:
            return RpcCallResult(
                latency_ms=(time.monotonic() - start) * 1000.0, ok=False,
                error=str(exc)[:200], kind=exc.kind, http_status=exc.http_status,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return RpcCallResult(
                latency_ms=(time.monotonic() - start) * 1000.0, ok=False,
                error=str(exc)[:200], kind='error',
            )


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return bool(
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def allow_private_rpc() -> bool:
    return str(os.getenv('ONBOARDING_ALLOW_PRIVATE_RPC', '')).strip().lower() in ('1', 'true', 'yes')


class SsrfValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_rpc_url(url: str, *, allow_private: bool | None = None) -> tuple[str, str]:
    """SSRF guard for a custom RPC endpoint. Returns (host, redacted_url).

    Blocks loopback, private, link-local (incl. 169.254.169.254 metadata),
    reserved, multicast and unspecified destinations unless explicitly enabled by
    secure server configuration (ONBOARDING_ALLOW_PRIVATE_RPC=true).
    """
    if allow_private is None:
        allow_private = allow_private_rpc()
    raw = str(url or '').strip()
    if not raw:
        raise SsrfValidationError('rpc_url_required', 'An RPC endpoint URL is required.')
    parsed = urllib_parse.urlsplit(raw)
    scheme = (parsed.scheme or '').lower()
    if scheme not in ('http', 'https'):
        raise SsrfValidationError('rpc_scheme_unsupported', 'RPC endpoint must be an http(s) URL.')
    if scheme == 'http' and not allow_private:
        raise SsrfValidationError('rpc_insecure_scheme', 'RPC endpoint must use https.')
    host = parsed.hostname or ''
    if not host:
        raise SsrfValidationError('rpc_host_missing', 'RPC endpoint URL is missing a host.')
    if not allow_private:
        blocked_names = {'localhost', 'metadata', 'metadata.google.internal'}
        if host.lower() in blocked_names:
            raise SsrfValidationError('rpc_host_blocked', 'RPC endpoint host is not allowed.')
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if scheme == 'https' else 80), proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise SsrfValidationError('rpc_dns_error', f'Could not resolve RPC host: {host}.') from exc
        for info in infos:
            ip = info[4][0]
            if _is_blocked_ip(ip):
                raise SsrfValidationError(
                    'rpc_host_blocked',
                    'RPC endpoint resolves to a private, loopback, or metadata address, which is not allowed.',
                )
    return host, redact_rpc_url(raw)


def redact_rpc_url(url: str) -> str:
    """Return a display-safe URL with any embedded API key/token removed."""
    raw = str(url or '').strip()
    if not raw:
        return ''
    try:
        parsed = urllib_parse.urlsplit(raw)
    except ValueError:
        return _host_from_url(raw)
    host = parsed.hostname or ''
    port = f':{parsed.port}' if parsed.port else ''
    # Redact any path segment that looks like a key (long token). Keep short,
    # human-meaningful segments like /v2 or /rpc.
    segments = []
    for seg in (parsed.path or '').split('/'):
        if not seg:
            continue
        if len(seg) >= 16 or _looks_like_secret(seg):
            segments.append('***')
        else:
            segments.append(seg)
    path = ('/' + '/'.join(segments)) if segments else ''
    query = '?***' if parsed.query else ''
    scheme = parsed.scheme or 'https'
    return f'{scheme}://{host}{port}{path}{query}'


def _looks_like_secret(segment: str) -> bool:
    s = str(segment or '')
    if len(s) < 12:
        return False
    alnum = sum(1 for c in s if c.isalnum())
    return alnum / max(1, len(s)) > 0.8 and any(c.isdigit() for c in s)


def _host_from_url(url: str) -> str:
    try:
        return urllib_parse.urlsplit(str(url or '')).hostname or ''
    except ValueError:
        return ''


class HttpRpcTransport(RpcTransport):
    """Real JSON-RPC transport over urllib with strict timeouts (no new deps)."""

    def __init__(self, url: str, *, host: str | None = None, default_timeout: float = _RPC_TIMEOUT_DEFAULT):
        self.url = url
        self.host = host or _host_from_url(url)
        self.default_timeout = default_timeout
        self._id = 0

    def call(self, method: str, params: list[Any], *, timeout: float | None = None) -> Any:
        self._id += 1
        payload = json.dumps({'jsonrpc': '2.0', 'id': self._id, 'method': method, 'params': params}).encode('utf-8')
        req = urllib_request.Request(self.url, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with urllib_request.urlopen(req, timeout=timeout or self.default_timeout) as resp:  # nosec B310
                body = json.loads(resp.read().decode('utf-8'))
        except urllib_error.HTTPError as exc:
            kind = 'rate_limited' if exc.code == 429 else 'http_error'
            raise RpcError(f'http {exc.code}', kind=kind, http_status=exc.code) from exc
        except socket.timeout as exc:
            raise RpcError('timeout', kind='timeout') from exc
        except urllib_error.URLError as exc:
            reason = getattr(exc, 'reason', exc)
            if isinstance(reason, socket.timeout):
                raise RpcError('timeout', kind='timeout') from exc
            if isinstance(reason, socket.gaierror):
                raise RpcError('dns_error', kind='dns_error') from exc
            raise RpcError(f'connection error: {reason}', kind='error') from exc
        except (TimeoutError, ConnectionError) as exc:
            raise RpcError('timeout', kind='timeout') from exc
        if isinstance(body, dict) and body.get('error'):
            raise RpcError(f"rpc error: {str(body['error'])[:120]}", kind='rpc_error')
        return (body or {}).get('result') if isinstance(body, dict) else None


# ---------------------------------------------------------------------------
# Findings.
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    finding_type: str
    value: Any
    detection_method: str
    confidence: str = UNKNOWN
    source_contract: str | None = None
    block_number: int | None = None
    rpc_source_host: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)

    def evidence_hash(self) -> str:
        canonical = json.dumps(
            {'type': self.finding_type, 'value': self.value, 'evidence': self.evidence},
            sort_keys=True, separators=(',', ':'), default=str,
        )
        return 'sha256:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            'finding_type': self.finding_type,
            'value': self.value,
            'detection_method': self.detection_method,
            'confidence': self.confidence,
            'source_contract': self.source_contract,
            'block_number': self.block_number,
            'rpc_source_host': self.rpc_source_host,
            'evidence': self.evidence,
            'evidence_hash': self.evidence_hash(),
            'created_at': self.created_at,
        }


@dataclass
class DiscoveryResult:
    ok: bool
    chain_id: int | None = None
    chain_network: str | None = None
    block_number: int | None = None
    rpc_host: str | None = None
    findings: list[Finding] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def finding_map(self) -> dict[str, Finding]:
        return {f.finding_type: f for f in self.findings}


def _selector_in_code(code_hex: str, selector: str) -> bool:
    return _strip0x(selector).lower() in _strip0x(code_hex).lower()


def _safe_view_string(transport: RpcTransport, address: str, name: str) -> str | None:
    res = transport.timed_call('eth_call', [{'to': address, 'data': SELECTORS[name]}, 'latest'])
    if not res.ok:
        return None
    return decode_string(res.result if isinstance(res.result, str) else None)


def _safe_view_uint(transport: RpcTransport, address: str, name: str) -> int | None:
    res = transport.timed_call('eth_call', [{'to': address, 'data': SELECTORS[name]}, 'latest'])
    if not res.ok:
        return None
    return decode_uint(res.result if isinstance(res.result, str) else None)


def _safe_view_address(transport: RpcTransport, address: str, name: str) -> str | None:
    res = transport.timed_call('eth_call', [{'to': address, 'data': SELECTORS[name]}, 'latest'])
    if not res.ok:
        return None
    return decode_address(res.result if isinstance(res.result, str) else None)


def _supports_interface(transport: RpcTransport, address: str, interface_id: str) -> bool | None:
    data = encode_calldata(SELECTORS['supportsInterface'], ('bytes4', interface_id))
    res = transport.timed_call('eth_call', [{'to': address, 'data': data}, 'latest'])
    if not res.ok:
        return None
    return decode_bool(res.result if isinstance(res.result, str) else None)


def _storage_at(transport: RpcTransport, address: str, slot: str) -> str | None:
    res = transport.timed_call('eth_getStorageAt', [address, slot, 'latest'])
    if not res.ok:
        return None
    return res.result if isinstance(res.result, str) else None


# ---------------------------------------------------------------------------
# Discovery pipeline.
# ---------------------------------------------------------------------------
def discover_contract(
    transport: RpcTransport,
    *,
    address: str,
    selected_chain_id: int | None,
) -> DiscoveryResult:
    """Run the full deterministic discovery pipeline against one contract."""
    host = getattr(transport, 'host', '') or None

    try:
        normalized = validate_contract_address(address)
    except AddressValidationError as exc:
        return DiscoveryResult(ok=False, rpc_host=host, error_code=exc.code, error_message=exc.message)

    findings: list[Finding] = []

    # 1. Chain id verification (mandatory before trusting any contract fact).
    chain_res = transport.timed_call('eth_chainId', [])
    if not chain_res.ok:
        return DiscoveryResult(
            ok=False, rpc_host=host, error_code='rpc_unreachable',
            error_message=f'RPC endpoint did not return eth_chainId ({chain_res.error or "no response"}).',
        )
    returned_chain = decode_uint(chain_res.result if isinstance(chain_res.result, str) else None)
    if returned_chain is None:
        return DiscoveryResult(ok=False, rpc_host=host, error_code='invalid_chain_response',
                               error_message='RPC endpoint returned an invalid chain id.')
    if selected_chain_id is not None and returned_chain != selected_chain_id:
        return DiscoveryResult(
            ok=False, chain_id=returned_chain, rpc_host=host, error_code='chain_mismatch',
            error_message=(
                f'RPC endpoint reports chain id {returned_chain} but chain id '
                f'{selected_chain_id} was selected.'
            ),
        )
    chain_id = selected_chain_id if selected_chain_id is not None else returned_chain
    findings.append(Finding(
        'network', chain_network_name(chain_id), 'eth_chainId', CONFIRMED,
        source_contract=normalized, rpc_source_host=host,
        evidence={'chain_id': chain_id, 'returned_chain_id': returned_chain},
    ))
    findings.append(Finding(
        'chain_id', chain_id, 'eth_chainId', CONFIRMED,
        source_contract=normalized, rpc_source_host=host,
        evidence={'returned_chain_id': returned_chain, 'selected_chain_id': selected_chain_id},
    ))

    # 2. Latest block for evidence anchoring.
    block_res = transport.timed_call('eth_blockNumber', [])
    block_number = decode_uint(block_res.result if isinstance(block_res.result, str) else None) if block_res.ok else None

    # 3. Bytecode: reject EOAs.
    code_res = transport.timed_call('eth_getCode', [normalized, 'latest'])
    if not code_res.ok:
        return DiscoveryResult(ok=False, chain_id=chain_id, block_number=block_number, rpc_host=host,
                               error_code='rpc_unreachable',
                               error_message=f'RPC endpoint did not return eth_getCode ({code_res.error or "no response"}).')
    code = code_res.result if isinstance(code_res.result, str) else '0x'
    if _strip0x(code) == '' or set(_strip0x(code)) <= {'0'}:
        return DiscoveryResult(
            ok=False, chain_id=chain_id, block_number=block_number, rpc_host=host,
            error_code='no_deployed_contract',
            error_message='No deployed bytecode found at this address (it appears to be an externally owned account).',
        )
    code_size = len(_strip0x(code)) // 2
    code_hash = 'sha256:' + hashlib.sha256(_strip0x(code).encode('utf-8')).hexdigest()
    findings.append(Finding(
        'bytecode', 'deployed', 'eth_getCode', CONFIRMED,
        source_contract=normalized, block_number=block_number, rpc_source_host=host,
        evidence={'code_size_bytes': code_size, 'code_hash': code_hash},
    ))

    def add(f: Finding) -> None:
        f.source_contract = f.source_contract or normalized
        f.block_number = f.block_number if f.block_number is not None else block_number
        f.rpc_source_host = f.rpc_source_host or host
        findings.append(f)

    # 4. Proxy detection (EIP-1967 slots).
    impl_slot = _storage_at(transport, normalized, EIP1967_IMPLEMENTATION_SLOT)
    admin_slot = _storage_at(transport, normalized, EIP1967_ADMIN_SLOT)
    beacon_slot = _storage_at(transport, normalized, EIP1967_BEACON_SLOT)
    impl_addr = address_slot_value(impl_slot)
    admin_addr = address_slot_value(admin_slot)
    beacon_addr = address_slot_value(beacon_slot)
    has_uups_selector = _selector_in_code(code, SELECTORS['upgradeTo']) or _selector_in_code(code, SELECTORS['upgradeToAndCall'])

    implementation_target = impl_addr
    if beacon_addr:
        add(Finding('proxy_type', 'beacon', 'eip1967_beacon_slot', CONFIRMED,
                    evidence={'slot': EIP1967_BEACON_SLOT, 'beacon': beacon_addr}))
    elif impl_addr and admin_addr:
        add(Finding('proxy_type', 'transparent', 'eip1967_implementation_admin_slots', CONFIRMED,
                    evidence={'implementation': impl_addr, 'admin': admin_addr}))
    elif impl_addr and has_uups_selector:
        add(Finding('proxy_type', 'uups', 'eip1967_implementation_slot_plus_upgradeTo_selector', CONFIRMED,
                    evidence={'implementation': impl_addr, 'upgradeTo_selector': True}))
    elif impl_addr:
        add(Finding('proxy_type', 'proxy', 'eip1967_implementation_slot', PROBABLE,
                    evidence={'implementation': impl_addr}))
    else:
        legacy_impl = address_slot_value(_storage_at(transport, normalized, OZ_LEGACY_IMPL_SLOT))
        if legacy_impl:
            implementation_target = legacy_impl
            add(Finding('proxy_type', 'legacy_proxy', 'oz_legacy_implementation_slot', PROBABLE,
                        evidence={'implementation': legacy_impl}))
        else:
            add(Finding('proxy_type', 'none', 'eip1967_slots_empty', PROBABLE,
                        evidence={'implementation_slot_empty': True}))

    if implementation_target:
        add(Finding('implementation_address', implementation_target, 'eip1967_storage_slot', CONFIRMED,
                    evidence={'implementation': implementation_target}))
    if admin_addr:
        add(Finding('proxy_admin', admin_addr, 'eip1967_admin_slot', CONFIRMED,
                    evidence={'admin': admin_addr}))
    if beacon_addr:
        add(Finding('beacon_address', beacon_addr, 'eip1967_beacon_slot', CONFIRMED,
                    evidence={'beacon': beacon_addr}))

    # For proxies, capability/standard detection should also inspect the
    # implementation bytecode (the proxy forwards logic there).
    logic_code = code
    if implementation_target:
        impl_code_res = transport.timed_call('eth_getCode', [implementation_target, 'latest'])
        if impl_code_res.ok and isinstance(impl_code_res.result, str) and _strip0x(impl_code_res.result):
            logic_code = code + impl_code_res.result  # union of selectors present in either

    # 5. Token standard detection (ERC-165 first, then heuristics).
    erc721 = _supports_interface(transport, normalized, INTERFACE_IDS['ERC721'])
    erc1155 = _supports_interface(transport, normalized, INTERFACE_IDS['ERC1155'])
    access_control_165 = _supports_interface(transport, normalized, INTERFACE_IDS['AccessControl'])

    token_standard: str | None = None
    if erc1155 is True:
        add(Finding('token_standard', 'ERC-1155', 'erc165_supportsInterface', CONFIRMED,
                    evidence={'interface_id': INTERFACE_IDS['ERC1155']}))
        token_standard = 'ERC-1155'
    elif erc721 is True:
        add(Finding('token_standard', 'ERC-721', 'erc165_supportsInterface', CONFIRMED,
                    evidence={'interface_id': INTERFACE_IDS['ERC721']}))
        token_standard = 'ERC-721'

    # ERC-20 / ERC-4626 (heuristic — no on-chain introspection standard).
    name = _safe_view_string(transport, normalized, 'name')
    symbol = _safe_view_string(transport, normalized, 'symbol')
    decimals = _safe_view_uint(transport, normalized, 'decimals')
    total_supply = _safe_view_uint(transport, normalized, 'totalSupply')
    has_transfer = _selector_in_code(logic_code, SELECTORS['transfer'])
    has_balanceof = _selector_in_code(logic_code, SELECTORS['balanceOf'])

    if token_standard is None and (total_supply is not None or (has_transfer and has_balanceof)):
        vault_asset = _safe_view_address(transport, normalized, 'asset')
        total_assets = _safe_view_uint(transport, normalized, 'totalAssets')
        if vault_asset and total_assets is not None:
            add(Finding('token_standard', 'ERC-4626', 'view_calls_asset_totalAssets', PROBABLE,
                        evidence={'asset': vault_asset, 'total_assets': str(total_assets)}))
            add(Finding('vault_asset', vault_asset, 'erc4626_asset_view', PROBABLE,
                        evidence={'asset': vault_asset}))
            token_standard = 'ERC-4626'
        else:
            add(Finding('token_standard', 'ERC-20', 'view_calls_and_bytecode_selectors', PROBABLE,
                        evidence={
                            'has_totalSupply': total_supply is not None,
                            'has_transfer_selector': has_transfer,
                            'has_balanceOf_selector': has_balanceof,
                        }))
            token_standard = 'ERC-20'

    # Token metadata (confirmed when a view call returns a value).
    if name:
        add(Finding('token_name', name, 'view_call_name', CONFIRMED, evidence={'name': name}))
    if symbol:
        add(Finding('token_symbol', symbol, 'view_call_symbol', CONFIRMED, evidence={'symbol': symbol}))
    if decimals is not None:
        add(Finding('token_decimals', decimals, 'view_call_decimals', CONFIRMED, evidence={'decimals': decimals}))
    if total_supply is not None:
        add(Finding('total_supply', str(total_supply), 'view_call_totalSupply', CONFIRMED,
                    evidence={'total_supply': str(total_supply)}))

    # 6. Ownership / access control (admin identity).
    owner_addr = _safe_view_address(transport, normalized, 'owner')
    if owner_addr:
        add(Finding('owner_address', owner_addr, 'view_call_owner', CONFIRMED, evidence={'owner': owner_addr}))
        add(Finding('access_model', 'Ownable', 'view_call_owner', PROBABLE, evidence={'owner': owner_addr}))
    if access_control_165 is True:
        add(Finding('access_model', 'AccessControl', 'erc165_supportsInterface', CONFIRMED,
                    evidence={'interface_id': INTERFACE_IDS['AccessControl']}))
    elif _selector_in_code(logic_code, SELECTORS['hasRole']) and _selector_in_code(logic_code, SELECTORS['grantRole']):
        add(Finding('access_model', 'AccessControl', 'bytecode_selectors_hasRole_grantRole', PROBABLE,
                    evidence={'selectors': ['hasRole', 'grantRole']}))

    # 7. Capabilities (bytecode selector heuristics → probable).
    capability_selectors: list[tuple[str, str, list[str]]] = [
        ('pausable', 'Pausable', ['paused', 'pause']),
        ('mint_capability', 'Mint', ['mint']),
        ('burn_capability', 'Burn', ['burn']),
        ('upgrade_capability', 'Upgradeable', ['upgradeTo']),
        ('blacklist_capability', 'Blacklist', ['isBlacklisted']),
        ('blacklist_capability', 'Blacklist', ['addBlackList']),
        ('allowlist_capability', 'Allowlist', ['isWhitelisted']),
        ('allowlist_capability', 'Allowlist', ['addToWhitelist']),
        ('freeze_capability', 'Freeze', ['freeze']),
        ('freeze_capability', 'Freeze', ['freezeAccount']),
    ]
    seen_caps: set[str] = set()
    for finding_type, label, selectors in capability_selectors:
        if finding_type in seen_caps:
            continue
        if all(_selector_in_code(logic_code, SELECTORS[s]) for s in selectors):
            add(Finding(finding_type, label, f'bytecode_selectors_{"_".join(selectors)}', PROBABLE,
                        evidence={'selectors': selectors}))
            seen_caps.add(finding_type)

    # 8. Oracle dependency (Chainlink-style aggregator interface).
    if _selector_in_code(logic_code, SELECTORS['latestRoundData']) or _selector_in_code(logic_code, SELECTORS['latestAnswer']):
        add(Finding('oracle_dependency', 'chainlink_aggregator_interface', 'bytecode_selectors_latestRoundData', PROBABLE,
                    evidence={'selectors': ['latestRoundData/latestAnswer']}))
    else:
        add(Finding('oracle_dependency', 'none_detected', 'bytecode_selector_scan', REQUIRES_REVIEW,
                    evidence={'note': 'No standard oracle interface detected in bytecode; confirm off-chain price dependencies manually.'}))

    # 9. Event signatures (standard, conditioned on detected token standard).
    if token_standard in ('ERC-20', 'ERC-4626'):
        add(Finding('event_signatures', ['Transfer(address,address,uint256)', 'Approval(address,address,uint256)'],
                    'erc20_standard_events', PROBABLE, evidence={'standard': token_standard}))
    elif token_standard == 'ERC-721':
        add(Finding('event_signatures', ['Transfer(address,address,uint256)', 'ApprovalForAll(address,address,bool)'],
                    'erc721_standard_events', PROBABLE, evidence={'standard': token_standard}))
    elif token_standard == 'ERC-1155':
        add(Finding('event_signatures', ['TransferSingle(address,address,address,uint256,uint256)', 'TransferBatch'],
                    'erc1155_standard_events', PROBABLE, evidence={'standard': token_standard}))

    return DiscoveryResult(
        ok=True, chain_id=chain_id, chain_network=chain_network_name(chain_id),
        block_number=block_number, rpc_host=host, findings=findings,
    )


# ---------------------------------------------------------------------------
# RPC benchmark + deterministic scoring.
# ---------------------------------------------------------------------------
MIN_SUCCESS_RATE_FOR_PRIMARY = 0.6
MAX_BLOCK_LAG_FOR_PRIMARY = 25


@dataclass
class BenchmarkEndpoint:
    host: str
    redacted_url: str
    transport: RpcTransport


@dataclass
class EndpointBenchmark:
    host: str
    redacted_url: str
    connection_status: str = 'error'
    median_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    success_rate: float = 0.0
    error_rate: float = 1.0
    timeout_count: int = 0
    error_count: int = 0
    latest_block: int | None = None
    block_lag: int | None = None
    chain_id_returned: int | None = None
    chain_id_ok: bool = False
    rate_limited: bool = False
    archive_supported: bool | None = None
    score: float = 0.0
    recommendation: str = 'rejected'
    reason: str = ''
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'host': self.host,
            'redacted_url': self.redacted_url,
            'connection_status': self.connection_status,
            'median_latency_ms': self.median_latency_ms,
            'p95_latency_ms': self.p95_latency_ms,
            'success_rate': round(self.success_rate, 4),
            'error_rate': round(self.error_rate, 4),
            'timeout_count': self.timeout_count,
            'error_count': self.error_count,
            'latest_block': self.latest_block,
            'block_lag': self.block_lag,
            'chain_id_returned': self.chain_id_returned,
            'chain_id_ok': self.chain_id_ok,
            'rate_limited': self.rate_limited,
            'archive_supported': self.archive_supported,
            'score': round(self.score, 4),
            'recommendation': self.recommendation,
            'reason': self.reason,
            'evidence': self.evidence,
        }


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def benchmark_endpoint(
    endpoint: BenchmarkEndpoint,
    *,
    selected_chain_id: int | None,
    target_address: str | None,
    iterations: int = 3,
    timeout: float = _RPC_TIMEOUT_DEFAULT,
) -> EndpointBenchmark:
    """Run a bounded, non-destructive benchmark for one endpoint. Never raises."""
    result = EndpointBenchmark(host=endpoint.host, redacted_url=endpoint.redacted_url)
    latencies: list[float] = []
    core_success = 0
    core_total = 0
    chain_ids: list[int] = []
    blocks: list[int] = []

    for _ in range(max(1, iterations)):
        for method, params in (('eth_chainId', []), ('eth_blockNumber', [])):
            core_total += 1
            call = endpoint.transport.timed_call(method, params, timeout=timeout)
            latencies.append(call.latency_ms)
            if call.ok:
                core_success += 1
                parsed = decode_uint(call.result if isinstance(call.result, str) else None)
                if method == 'eth_chainId' and parsed is not None:
                    chain_ids.append(parsed)
                elif method == 'eth_blockNumber' and parsed is not None:
                    blocks.append(parsed)
            else:
                if call.kind == 'timeout':
                    result.timeout_count += 1
                else:
                    result.error_count += 1
                if call.kind == 'rate_limited' or call.http_status == 429:
                    result.rate_limited = True
        if target_address:
            core_total += 1
            call = endpoint.transport.timed_call('eth_getCode', [target_address, 'latest'], timeout=timeout)
            latencies.append(call.latency_ms)
            if call.ok:
                core_success += 1
            elif call.kind == 'timeout':
                result.timeout_count += 1
            else:
                result.error_count += 1
                if call.kind == 'rate_limited' or call.http_status == 429:
                    result.rate_limited = True

    result.success_rate = (core_success / core_total) if core_total else 0.0
    result.error_rate = 1.0 - result.success_rate
    result.median_latency_ms = int(_percentile(latencies, 0.5) or 0) if latencies else None
    result.p95_latency_ms = int(_percentile(latencies, 0.95) or 0) if latencies else None
    result.chain_id_returned = chain_ids[0] if chain_ids else None
    result.chain_id_ok = bool(
        result.chain_id_returned is not None
        and (selected_chain_id is None or result.chain_id_returned == selected_chain_id)
    )
    result.latest_block = max(blocks) if blocks else None

    if result.timeout_count and core_success == 0:
        result.connection_status = 'timeout'
    elif result.rate_limited:
        result.connection_status = 'rate_limited'
    elif core_success == 0:
        result.connection_status = 'error'
    elif result.success_rate < 1.0:
        result.connection_status = 'degraded'
    else:
        result.connection_status = 'ok'

    result.evidence = {
        'iterations': iterations,
        'core_calls': core_total,
        'core_success': core_success,
        'chain_ids_seen': chain_ids,
        'sampled_latencies_ms': [round(v, 2) for v in latencies[:12]],
    }
    return result


def run_rpc_benchmark(
    endpoints: list[BenchmarkEndpoint],
    *,
    selected_chain_id: int | None,
    target_address: str | None = None,
    iterations: int = 3,
    timeout: float = _RPC_TIMEOUT_DEFAULT,
    max_workers: int = 8,
) -> tuple[list[EndpointBenchmark], dict[str, Any]]:
    """Benchmark all endpoints in parallel and produce a deterministic ranking.

    A slow/timing-out/rate-limited endpoint never blocks the others (each runs in
    its own worker with a bounded per-call timeout).
    """
    results: list[EndpointBenchmark] = []
    if endpoints:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(endpoints))) as pool:
            futures = {
                pool.submit(
                    benchmark_endpoint, ep, selected_chain_id=selected_chain_id,
                    target_address=target_address, iterations=iterations, timeout=timeout,
                ): ep
                for ep in endpoints
            }
            for fut in as_completed(futures):
                ep = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:  # pragma: no cover - defensive
                    eb = EndpointBenchmark(host=ep.host, redacted_url=ep.redacted_url)
                    eb.connection_status = 'error'
                    eb.reason = f'benchmark_error: {str(exc)[:80]}'
                    results.append(eb)

    summary = rank_endpoints(results, selected_chain_id=selected_chain_id)
    # Preserve stable ordering (best first) for display.
    results.sort(key=lambda r: (-(r.score), r.host))
    return results, summary


def _score(bench: EndpointBenchmark) -> float:
    score = bench.success_rate * 1000.0
    score -= (bench.block_lag or 0) * 40.0
    score -= (bench.p95_latency_ms or 0) * 0.5
    score -= (bench.median_latency_ms or 0) * 0.3
    if bench.rate_limited:
        score -= 500.0
    return score


def rank_endpoints(results: list[EndpointBenchmark], *, selected_chain_id: int | None) -> dict[str, Any]:
    """Deterministically assign primary / fallback / degraded / rejected."""
    healthy_blocks = [r.latest_block for r in results if r.chain_id_ok and r.latest_block is not None]
    best_block = max(healthy_blocks) if healthy_blocks else None
    for r in results:
        if best_block is not None and r.latest_block is not None and r.chain_id_ok:
            r.block_lag = max(0, best_block - r.latest_block)
        r.score = _score(r)

    def eligible_for_primary(r: EndpointBenchmark) -> bool:
        return bool(
            r.chain_id_ok
            and not r.rate_limited
            and r.success_rate >= MIN_SUCCESS_RATE_FOR_PRIMARY
            and (r.block_lag is None or r.block_lag <= MAX_BLOCK_LAG_FOR_PRIMARY)
        )

    eligible = sorted([r for r in results if eligible_for_primary(r)], key=lambda r: (-r.score, r.host))
    primary = eligible[0] if eligible else None
    fallback = None
    if primary is not None:
        for r in eligible[1:]:
            if r.host != primary.host:
                fallback = r
                break
        if fallback is None and len(eligible) > 1:
            fallback = eligible[1]

    for r in results:
        if primary is not None and r is primary:
            r.recommendation = 'primary'
        elif fallback is not None and r is fallback:
            r.recommendation = 'fallback'
        elif eligible_for_primary(r):
            r.recommendation = 'degraded'
        else:
            r.recommendation = 'rejected'
        r.reason = _recommendation_reason(r, selected_chain_id=selected_chain_id, best_block=best_block)

    explanation = None
    if primary is not None:
        parts = [f'{primary.host} was selected as primary because it']
        reasons = []
        if primary.chain_id_ok:
            reasons.append(f'returned the correct chain id ({primary.chain_id_returned})')
        if primary.success_rate >= 0.999:
            reasons.append('had a 100% success rate with zero observed errors')
        else:
            reasons.append(f'had the best measured reliability ({round(primary.success_rate * 100)}% success)')
        if primary.median_latency_ms is not None:
            reasons.append(f'the lowest median latency ({primary.median_latency_ms} ms)')
        if (primary.block_lag or 0) == 0:
            reasons.append('no block lag during the benchmark')
        explanation = parts[0] + ' ' + ', '.join(reasons) + '.'

    return {
        'best_block': best_block,
        'primary_host': primary.host if primary else None,
        'fallback_host': fallback.host if fallback else None,
        'explanation': explanation,
        'eligible_count': len(eligible),
        'total_count': len(results),
    }


def _recommendation_reason(bench: EndpointBenchmark, *, selected_chain_id: int | None, best_block: int | None) -> str:
    if not bench.chain_id_ok:
        if bench.chain_id_returned is None:
            return 'Rejected: endpoint did not return a valid chain id.'
        return (f'Rejected: endpoint returned chain id {bench.chain_id_returned} '
                f'but chain id {selected_chain_id} was selected.')
    if bench.rate_limited:
        return 'Rejected as primary: endpoint returned rate-limit (HTTP 429) responses during the benchmark.'
    if bench.success_rate < MIN_SUCCESS_RATE_FOR_PRIMARY:
        return (f'Rejected as primary: success rate {round(bench.success_rate * 100)}% is below the '
                f'{round(MIN_SUCCESS_RATE_FOR_PRIMARY * 100)}% reliability threshold.')
    if bench.block_lag is not None and bench.block_lag > MAX_BLOCK_LAG_FOR_PRIMARY:
        return (f'Downgraded: {bench.block_lag} blocks behind the leading endpoint '
                f'(> {MAX_BLOCK_LAG_FOR_PRIMARY} block primary threshold).')
    if bench.recommendation == 'primary':
        return 'Selected as primary provider based on correct chain id, reliability, latency and block freshness.'
    if bench.recommendation == 'fallback':
        return 'Selected as fallback provider (healthy but ranked below the primary).'
    bits = []
    if bench.block_lag:
        bits.append(f'{bench.block_lag} blocks behind')
    if bench.p95_latency_ms:
        bits.append(f'p95 latency {bench.p95_latency_ms} ms')
    return 'Healthy but not selected' + (': ' + ', '.join(bits) if bits else '') + '.'
