"""Shared fakes for the External Watchlist tests (not collected by pytest).

* ``keccak256`` — an independent, pure-Python Keccak-256 (Ethereum's hash, not
  FIPS SHA-3), used to RECOMPUTE every event topic the production catalog
  hard-codes, so a mistyped constant fails a test instead of silently never
  matching a real log.
* ``FakeChain`` — a deterministic in-memory EVM JSON-RPC endpoint: blocks with
  timestamps, bytecode, eth_call answers, transactions, and logs, with optional
  provider-style failures (range too large, transient 503, rate limits).
* log builders for every event the catalog decodes.
"""

from __future__ import annotations

import json
from typing import Any

# ── Keccak-256 reference implementation ──────────────────────────────────────
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
_MASK = (1 << 64) - 1


def _rotl(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _keccak_f(state: list[list[int]]) -> list[list[int]]:
    for round_constant in _RC:
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        state = [[state[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(state[x][y], _ROT[x][y])
        state = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        state[0][0] ^= round_constant
    return state


def keccak256(data: bytes | str) -> str:
    message = bytearray(data.encode('utf-8') if isinstance(data, str) else data)
    rate = 136
    message.append(0x01)
    while len(message) % rate:
        message.append(0)
    message[-1] |= 0x80
    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(message), rate):
        block = message[offset:offset + rate]
        for index in range(rate // 8):
            state[index % 5][index // 5] ^= int.from_bytes(block[index * 8:(index + 1) * 8], 'little')
        state = _keccak_f(state)
    digest = b''.join(state[i % 5][i // 5].to_bytes(8, 'little') for i in range(4))
    return '0x' + digest.hex()


# ── encoding helpers ─────────────────────────────────────────────────────────
ZERO = '0x' + '0' * 40


def addr(n: int) -> str:
    """A deterministic, valid, lowercase address."""
    return '0x' + format(n, '040x')


def tx(n: int) -> str:
    return '0x' + format(n, '064x')


def word(value: int) -> str:
    return format(value % (1 << 256), '064x')


def topic_address(address: str) -> str:
    return '0x' + '0' * 24 + address[2:].lower()


def topic_uint(value: int) -> str:
    return '0x' + word(value)


def data_words(*values: int | str) -> str:
    out = []
    for value in values:
        if isinstance(value, str):
            out.append('0' * 24 + value[2:].lower() if len(value) == 42 else value.replace('0x', '').rjust(64, '0'))
        else:
            out.append(word(value))
    return '0x' + ''.join(out)


def make_log(*, address: str, topics: list[str], data: str = '0x', block: int, tx_hash: str,
             log_index: int = 0) -> dict[str, Any]:
    return {
        'address': address,
        'topics': topics,
        'data': data,
        'blockNumber': hex(block),
        'blockHash': '0x' + format(block, '064x'),
        'transactionHash': tx_hash,
        'transactionIndex': '0x0',
        'logIndex': hex(log_index),
        'removed': False,
    }


# ── the fake chain ───────────────────────────────────────────────────────────
class FakeChain:
    """A deterministic EVM node. Block n has timestamp genesis + n * block_seconds."""

    def __init__(
        self,
        *,
        chain_id: int = 8453,
        tip: int = 1_000_000,
        genesis_ts: int = 1_700_000_000,
        block_seconds: int = 2,
        code: dict[str, str] | None = None,
        logs: list[dict[str, Any]] | None = None,
        tx_senders: dict[str, str] | None = None,
        eth_call_results: dict[tuple[str, str], str] | None = None,
        storage: dict[tuple[str, str], str] | None = None,
        max_range: int | None = None,
        fail_ranges: list[tuple[int, int]] | None = None,
        fail_times: int | None = None,
        rate_limit_times: int = 0,
    ) -> None:
        self.chain_id = chain_id
        self.tip = tip
        self.genesis_ts = genesis_ts
        self.block_seconds = block_seconds
        self.code = {k.lower(): v for k, v in (code or {}).items()}
        self.logs = list(logs or [])
        self.tx_senders = {k.lower(): v for k, v in (tx_senders or {}).items()}
        self.eth_call_results = eth_call_results or {}
        self.storage = storage or {}
        self.max_range = max_range
        self.fail_ranges = list(fail_ranges or [])
        self.fail_times = fail_times
        self.rate_limit_times = rate_limit_times
        self.calls: list[tuple[str, Any]] = []
        self.active_host = 'fake-rpc.local'

    def timestamp_of(self, block: int) -> int:
        return self.genesis_ts + int(block) * self.block_seconds

    def call(self, method: str, params: list[Any]) -> Any:
        self.calls.append((method, json.loads(json.dumps(params))))
        if method == 'eth_chainId':
            return hex(self.chain_id)
        if method == 'eth_blockNumber':
            return hex(self.tip)
        if method == 'eth_getBlockByNumber':
            number = int(params[0], 16)
            if number > self.tip:
                return None
            return {'number': hex(number), 'timestamp': hex(self.timestamp_of(number))}
        if method == 'eth_getCode':
            return self.code.get(str(params[0]).lower(), '0x')
        if method == 'eth_call':
            call = params[0]
            return self.eth_call_results.get((str(call['to']).lower(), str(call['data'])[:10]), '0x')
        if method == 'eth_getStorageAt':
            return self.storage.get((str(params[0]).lower(), str(params[1]).lower()), '0x' + '0' * 64)
        if method == 'eth_getTransactionByHash':
            sender = self.tx_senders.get(str(params[0]).lower())
            return {'hash': params[0], 'from': sender} if sender else None
        if method == 'eth_getLogs':
            return self._get_logs(params[0])
        raise RuntimeError(f'json-rpc error: method {method} not supported by fake')

    def _get_logs(self, flt: dict[str, Any]) -> list[dict[str, Any]]:
        start, end = int(flt['fromBlock'], 16), int(flt['toBlock'], 16)
        if self.rate_limit_times > 0:
            self.rate_limit_times -= 1
            raise RuntimeError('HTTP Error 429: Too Many Requests')
        if self.max_range is not None and end - start + 1 > self.max_range:
            raise RuntimeError(
                "all_rpc_providers_unavailable:json-rpc error: {'code': -32005, "
                "'message': 'query returned more than 10000 results'}"
            )
        for fail_start, fail_end in self.fail_ranges:
            if start <= fail_end and end >= fail_start:
                if self.fail_times is None or self.fail_times > 0:
                    if self.fail_times is not None:
                        self.fail_times -= 1
                    raise RuntimeError('all_rpc_providers_unavailable:HTTP Error 503: Service Unavailable')
        addresses = flt.get('address')
        if isinstance(addresses, str):
            addresses = [addresses]
        wanted_addresses = {a.lower() for a in addresses} if addresses else None
        topic_filter = flt.get('topics') or []
        out = []
        for log in self.logs:
            number = int(log['blockNumber'], 16)
            if number < start or number > end:
                continue
            if wanted_addresses is not None and log['address'].lower() not in wanted_addresses:
                continue
            if not _topics_match(log['topics'], topic_filter):
                continue
            out.append(dict(log))
        return out


def _topics_match(topics: list[str], topic_filter: list[Any]) -> bool:
    for index, expected in enumerate(topic_filter):
        if expected is None:
            continue
        if index >= len(topics):
            return False
        actual = topics[index].lower()
        if isinstance(expected, list):
            if actual not in {e.lower() for e in expected}:
                return False
        elif actual != str(expected).lower():
            return False
    return True
