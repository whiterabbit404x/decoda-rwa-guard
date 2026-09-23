"""Read-only chain access for external public monitoring.

Every RPC call External Watchlist makes goes through ``ReadOnlyRpcClient``. It
permits a closed list of READ methods and refuses everything else — sending a
transaction, signing, account access, node administration — with
``ExternalExecutionForbidden`` before a byte leaves the process. That is the
server-side half of ``execution_authority = NONE``: there is no code path from
an external target to a write, whatever a request or a future caller asks for.

Transport, retries, backoff and provider failover are NOT reimplemented here:
the worker path wraps ``evm_activity_provider.FailoverJsonRpcClient`` (per-host
429 backoff, 413 handling, failover), and the founder's request path wraps
``JsonRpcClient`` with a tight per-call bound so a slow provider cannot hold an
API request open.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from services.api.app import evm_activity_provider as evm
from services.api.app.domains.external_watchlist import config as ewc

logger = logging.getLogger(__name__)

#: The only JSON-RPC methods external monitoring may call. All are reads.
READ_ONLY_RPC_METHODS = frozenset({
    'eth_chainId',
    'eth_blockNumber',
    'eth_getLogs',
    'eth_getBlockByNumber',
    'eth_getCode',
    'eth_call',
    'eth_getStorageAt',
    'eth_getTransactionByHash',
})


class ExternalExecutionForbidden(PermissionError):
    """A write, signing or account method was requested for an external target."""

    code = 'EXTERNAL_EXECUTION_FORBIDDEN'


class RpcNotConfigured(RuntimeError):
    code = 'RPC_NOT_CONFIGURED'


class ChainMismatch(RuntimeError):
    code = 'CHAIN_MISMATCH'


class RpcBudgetExhausted(RuntimeError):
    code = 'RPC_BUDGET_EXHAUSTED'


class ChunkFetchFailed(RuntimeError):
    """eth_getLogs for a block range still failed after bounded retries."""

    def __init__(self, from_block: int, to_block: int, error: Exception):
        super().__init__(f'logs {from_block}-{to_block} failed: {sanitize_error(error)}')
        self.from_block = from_block
        self.to_block = to_block
        self.error = error


def sanitize_error(error: Any, limit: int = 240) -> str:
    """An error string safe to persist and show: no URL, so no embedded key."""
    text = f'{type(error).__name__}: {error}' if isinstance(error, BaseException) else str(error or '')
    cleaned = []
    for token in text.split():
        if '://' in token:
            host = token.split('://', 1)[1].split('/', 1)[0].split('@')[-1]
            cleaned.append(f'<{host}>')
        else:
            cleaned.append(token)
    result = ' '.join(cleaned)
    return result if len(result) <= limit else result[: limit - 1] + '…'


@dataclass
class CallBudget:
    """A hard cap on RPC calls, shared across one worker cycle."""

    limit: int
    used: int = 0

    def spend(self, count: int = 1) -> None:
        if self.used + count > self.limit:
            raise RpcBudgetExhausted(f'rpc budget of {self.limit} calls exhausted')
        self.used += count

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


@dataclass
class ReadOnlyRpcClient:
    inner: Any
    budget: CallBudget | None = None
    calls: int = field(default=0, init=False)

    def call(self, method: str, params: list[Any]) -> Any:
        if method not in READ_ONLY_RPC_METHODS:
            logger.error('external_watchlist_rpc_method_refused method=%s', method)
            raise ExternalExecutionForbidden(
                f'{method} is not a read method; external targets have execution_authority=NONE.'
            )
        if self.budget is not None:
            self.budget.spend()
        self.calls += 1
        return self.inner.call(method, params)

    @property
    def active_host(self) -> str | None:
        return getattr(self.inner, 'active_host', None)


@dataclass
class _BoundedFailoverClient:
    """Request-path failover: one attempt per provider, each with a hard timeout."""

    rpc_urls: list[str]
    timeout_seconds: float
    active_host: str | None = field(default=None, init=False)

    def call(self, method: str, params: list[Any]) -> Any:
        errors: list[str] = []
        for url in self.rpc_urls:
            host = evm._host_of(url)
            if evm.is_rpc_route_disabled(host):
                continue
            try:
                result = evm.JsonRpcClient(
                    url, timeout_seconds_override=self.timeout_seconds, max_attempts_override=1,
                ).call(method, params)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                errors.append(sanitize_error(exc, 120))
                continue
            self.active_host = host
            return result
        raise RuntimeError('all_rpc_providers_unavailable:' + (';'.join(errors) or 'no_route'))


def build_client(
    network: str, *, budget: CallBudget | None = None, request_path: bool = False,
    urls_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> ReadOnlyRpcClient:
    resolved = (urls_resolver or ewc.rpc_urls_for_network)(network)
    urls = list(resolved.get('rpc_urls') or [])
    if not urls:
        raise RpcNotConfigured(f'No RPC endpoint is configured for {network}.')
    inner: Any
    if request_path:
        inner = _BoundedFailoverClient(urls, ewc.validation_rpc_timeout_seconds())
    else:
        inner = evm.FailoverJsonRpcClient(urls)
    return ReadOnlyRpcClient(inner, budget=budget)


# ── small read helpers ───────────────────────────────────────────────────────
def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    try:
        return int(text, 16) if text.startswith('0x') else int(text)
    except ValueError:
        return None


def chain_id(client: ReadOnlyRpcClient) -> int | None:
    return _as_int(client.call('eth_chainId', []))


def verify_chain(client: ReadOnlyRpcClient, expected_chain_id: int) -> int:
    """Fail closed when the endpoint serves a different chain than the target's."""
    served = chain_id(client)
    if served != int(expected_chain_id):
        raise ChainMismatch(f'RPC endpoint serves chain {served}, expected {expected_chain_id}.')
    return served


def block_number(client: ReadOnlyRpcClient) -> int:
    value = _as_int(client.call('eth_blockNumber', []))
    if value is None:
        raise RuntimeError('eth_blockNumber returned no value')
    return value


def block_timestamp(client: ReadOnlyRpcClient, number: int, cache: dict[int, int] | None = None) -> int | None:
    if cache is not None and number in cache:
        return cache[number]
    block = client.call('eth_getBlockByNumber', [hex(int(number)), False])
    timestamp = _as_int((block or {}).get('timestamp')) if isinstance(block, dict) else None
    if cache is not None and timestamp is not None:
        cache[number] = timestamp
    return timestamp


def get_code(client: ReadOnlyRpcClient, address: str) -> str:
    return str(client.call('eth_getCode', [address, 'latest']) or '0x').lower()


def eth_call(client: ReadOnlyRpcClient, address: str, data: str, block: str = 'latest') -> str | None:
    """A read-only call. Carries no 'from', no value, no gas: it cannot pay or sign."""
    result = client.call('eth_call', [{'to': address, 'data': data}, block])
    return str(result).lower() if result else None


def transaction_sender(client: ReadOnlyRpcClient, tx_hash: str) -> str | None:
    tx = client.call('eth_getTransactionByHash', [tx_hash])
    sender = str((tx or {}).get('from') or '').lower() if isinstance(tx, dict) else ''
    return sender if len(sender) == 42 and sender.startswith('0x') else None


def estimate_block_at_timestamp(
    client: ReadOnlyRpcClient,
    *,
    target_timestamp: int,
    tip: int,
    tip_timestamp: int,
    avg_block_seconds: float,
    tolerance_seconds: int = 900,
    max_iterations: int = 6,
    cache: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Approximate the first block at or after ``target_timestamp``.

    Starts from the network's nominal block time, then corrects with measured
    timestamps (secant steps) until within ``tolerance_seconds`` or out of
    iterations. The result is labelled approximate: it is where a backfill
    starts, not a claim about any specific block.
    """
    if target_timestamp >= tip_timestamp:
        return {'block': tip, 'timestamp': tip_timestamp, 'iterations': 0, 'approximate': True}
    guess = max(0, int(tip - (tip_timestamp - target_timestamp) / max(avg_block_seconds, 0.01)))
    anchor_block, anchor_ts = tip, tip_timestamp
    iterations = 0
    guess_ts = None
    while iterations < max_iterations:
        iterations += 1
        guess_ts = block_timestamp(client, guess, cache)
        if guess_ts is None:
            break
        error = guess_ts - target_timestamp
        if abs(error) <= tolerance_seconds:
            break
        span_blocks = anchor_block - guess
        if span_blocks == 0:
            break
        seconds_per_block = (anchor_ts - guess_ts) / span_blocks
        if seconds_per_block <= 0:
            break
        anchor_block, anchor_ts = guess, guess_ts
        guess = max(0, min(tip, int(guess - error / seconds_per_block)))
    return {'block': guess, 'timestamp': guess_ts, 'iterations': iterations, 'approximate': True}


# ── chunked, adaptive eth_getLogs ────────────────────────────────────────────
#: A provider throttling us. Always backed off, never mistaken for a size
#: problem — shrinking the range would only multiply the calls being throttled.
_RATE_LIMIT_MARKERS = (
    'too many requests', 'rate limit', 'ratelimit', 'rate-limit', 'throttl',
    'compute units', 'capacity',
)
#: HTTP 429 as a standalone number — never a digit run inside a hex block
#: number such as the "0x1429…" a range-error message may quote.
_HTTP_429_RE = re.compile(r'(?<![0-9a-fx])429(?![0-9a-f])')
#: A provider refusing the SIZE of an eth_getLogs query. Seen in the wild as
#: "query returned more than 10000 results" (-32005), "Log response size
#: exceeded ... 2K block range", "eth_getLogs is limited to a 10,000 range",
#: "block range is too wide", "exceeds max results", and HTTP 413. The failover
#: client wraps a JSON-RPC error message verbatim, so the markers are matched in
#: the wrapped text too.
_RANGE_ERROR_MARKERS = (
    'request_too_large', 'too large', 'too wide', 'more than', 'response size',
    'block range', 'max range', 'maximum block range', 'max results', 'exceeds max',
    'is limited to', 'range', '-32005', 'http error 413',
)


def is_range_error(error: Exception) -> bool:
    """A provider refusing the SIZE of a query (not an outage): shrink and retry."""
    if isinstance(error, evm.RpcRequestTooLargeError):
        return True
    if isinstance(error, (ExternalExecutionForbidden, RpcBudgetExhausted)):
        return False
    text = str(error).lower()
    if _HTTP_429_RE.search(text) or any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return False
    return any(marker in text for marker in _RANGE_ERROR_MARKERS)


@dataclass
class LogChunk:
    from_block: int
    to_block: int
    logs: list[dict[str, Any]]
    chunk_size: int
    retries: int


def iter_log_chunks(
    client: ReadOnlyRpcClient,
    *,
    filters: list[dict[str, Any]],
    from_block: int,
    to_block: int,
    chunk_size: int,
    min_chunk_size: int = 1,
    max_retries: int = 3,
    backoff_seconds: float = 2.0,
    pacing_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[LogChunk]:
    """Scan ``[from_block, to_block]`` oldest-first in provider-safe chunks.

    * A size refusal halves the chunk (down to ``min_chunk_size``) and retries
      the SAME range, so nothing is skipped.
    * Any other failure retries the same range with exponential backoff; after
      ``max_retries`` the scan raises ``ChunkFetchFailed`` at that range. The
      caller has already persisted every earlier chunk, so the scan resumes
      from exactly there.
    * Logs are de-duplicated per (tx hash, log index) across filters, and logs a
      reorg removed are dropped.
    """
    if not filters or from_block > to_block:
        return
    cursor = int(from_block)
    size = max(int(min_chunk_size), int(chunk_size))
    while cursor <= to_block:
        end = min(int(to_block), cursor + size - 1)
        retries = 0
        while True:
            try:
                seen: dict[tuple[str, str], dict[str, Any]] = {}
                for flt in filters:
                    params = dict(flt)
                    params['fromBlock'] = hex(cursor)
                    params['toBlock'] = hex(end)
                    for log in client.call('eth_getLogs', [params]) or []:
                        if not isinstance(log, dict) or log.get('removed') is True:
                            continue
                        key = (str(log.get('transactionHash') or '').lower(), str(log.get('logIndex') or '').lower())
                        seen[key] = log
                break
            except (ExternalExecutionForbidden, RpcBudgetExhausted):
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                span = end - cursor + 1
                if is_range_error(exc) and span > min_chunk_size:
                    size = max(int(min_chunk_size), span // 2)
                    end = min(int(to_block), cursor + size - 1)
                    continue
                retries += 1
                if retries > max_retries:
                    raise ChunkFetchFailed(cursor, end, exc) from exc
                sleep(min(60.0, backoff_seconds * (2 ** (retries - 1))))
        yield LogChunk(from_block=cursor, to_block=end, logs=list(seen.values()), chunk_size=size, retries=retries)
        cursor = end + 1
        if pacing_seconds > 0 and cursor <= to_block:
            sleep(pacing_seconds)
