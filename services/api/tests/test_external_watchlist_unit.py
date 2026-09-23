"""External Watchlist — unit tests (no database, no network).

What is pinned here, without PostgreSQL (the end-to-end flow is in
test_external_watchlist_postgres.py):

  ABI        every hard-coded topic / role / selector / slot is recomputed
             with an independent Keccak-256; decoders handle indexed and
             non-indexed variants, ERC-721, negative oracle answers, junk
  RPC        the gateway refuses every write/signing method before it reaches
             a provider; budgets; size refusals halve, outages back off,
             rate limits are never mistaken for size errors; errors lose URLs
  RULES      privileged changes, upgrades with truthful previous state,
             relative transfer/mint baselines, oracle deviation and heartbeat,
             careful language, severity never above 'high'
  HISTORY    a historical scan never reads or overwrites the live state
  STATUS     heartbeat / poll / backfill facts → status, fail-closed
  ANALYSIS   three separate statements; authorization never claimed; no
             invented addresses or transactions
  EVIDENCE   sealed package verifies, tampering is detected, the prospect
             report carries no internal identifiers or scoring
  ACCESS     authorization precedes the feature flag; execution refused for
             everyone; credentials refused by field name and by content
  BOUNDARY   no other module touches the external tables; the external
             domain touches customer tables only in the explicit conversion
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app.domains.external_watchlist import abi, analysis, backfill, detection, endpoints, evidence, ingest, rpc
from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.external_watchlist import status as status_model
from services.api.tests.external_watchlist_support import (
    FakeChain, addr, data_words, keccak256, make_log, topic_address, topic_uint, tx,
)

APP = pathlib.Path(__file__).resolve().parents[1] / 'app'
DOMAIN = APP / 'domains' / 'external_watchlist'
MIGRATION = pathlib.Path(__file__).resolve().parents[1] / 'migrations' / '0157_external_watchlist.sql'

TOKEN = addr(0xA11CE)
SAFE = addr(0x5AFE)
FEED = addr(0xFEED)
ADMIN = addr(0xAD)
OTHER = addr(0xB0B)
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _code(exc: BaseException) -> str | None:
    detail = getattr(exc, 'detail', None)
    return detail.get('code') if isinstance(detail, dict) else None


# ── ABI ──────────────────────────────────────────────────────────────────────
def test_every_topic_is_the_keccak_of_its_signature():
    for spec in abi.EVENT_SPECS:
        assert keccak256(spec.signature) == spec.topic0, spec.name
    assert len(abi.EVENTS_BY_TOPIC) == len(abi.EVENT_SPECS)


def test_role_selector_and_slot_constants():
    for role, label in abi.ROLE_LABELS.items():
        expected = abi.ZERO_WORD if label == 'DEFAULT_ADMIN_ROLE' else keccak256(label)
        assert role == expected, label
    for selector, signature in (
        (abi.SELECTOR_GET_THRESHOLD, 'getThreshold()'), (abi.SELECTOR_GET_OWNERS, 'getOwners()'),
        (abi.SELECTOR_AGGREGATOR, 'aggregator()'), (abi.SELECTOR_OWNER, 'owner()'),
        (abi.SELECTOR_DECIMALS, 'decimals()'), (abi.SELECTOR_SYMBOL, 'symbol()'),
    ):
        assert selector == keccak256(signature)[:10], signature
    slot = int(keccak256('eip1967.proxy.implementation'), 16) - 1
    assert abi.EIP1967_IMPLEMENTATION_SLOT == '0x' + format(slot, '064x')


def _log(topics, data='0x', *, block=100, n=1, log_index=0, address=TOKEN):
    return make_log(address=address, topics=topics, data=data, block=block, tx_hash=tx(n), log_index=log_index)


MINTER = keccak256('MINTER_ROLE')


def test_decoders():
    granted = abi.decode_log(_log([abi.TOPIC_ROLE_GRANTED, MINTER, topic_address(OTHER), topic_address(ADMIN)]))
    assert granted.decoded == {'role': MINTER, 'role_label': 'MINTER_ROLE', 'account': OTHER, 'sender': ADMIN}
    assert granted.spec.category == 'access_control' and granted.decode_status == 'decoded'

    # Safe 1.3 (non-indexed) and 1.4 (indexed) AddedOwner decode the same.
    plain = abi.decode_log(_log([abi.TOPIC_SAFE_ADDED_OWNER], data_words(OTHER), address=SAFE))
    indexed = abi.decode_log(_log([abi.TOPIC_SAFE_ADDED_OWNER, topic_address(OTHER)], address=SAFE))
    assert plain.decoded == indexed.decoded == {'owner': OTHER}

    answer = abi.decode_log(_log([abi.TOPIC_ANSWER_UPDATED, topic_uint(-5), topic_uint(7)], data_words(1_700_000_000), address=FEED))
    assert answer.decoded == {'current': '-5', 'round_id': '7', 'updated_at': 1_700_000_000}

    erc20 = abi.decode_log(_log([abi.TOPIC_TRANSFER, topic_address(ADMIN), topic_address(OTHER)], data_words(10**18)))
    assert erc20.decoded['token_standard'] == 'erc20' and erc20.decoded['value'] == str(10**18)
    erc721 = abi.decode_log(_log([abi.TOPIC_TRANSFER, topic_address(ADMIN), topic_address(OTHER), topic_uint(9)]))
    assert erc721.decoded['token_standard'] == 'erc721' and erc721.decoded['value'] is None

    # A known event with a malformed payload is recorded as undecoded, not dropped.
    broken = abi.decode_log(_log([abi.TOPIC_TRANSFER, topic_address(ADMIN), topic_address(OTHER)], '0x'))
    assert broken.decode_status == 'undecoded'
    assert abi.decode_log(_log(['0x' + '12' * 32])) is None
    assert abi.decode_log({**_log([abi.TOPIC_PAUSED], data_words(ADMIN)), 'removed': True}) is None
    assert abi.decode_log({**_log([abi.TOPIC_PAUSED], data_words(ADMIN)), 'transactionHash': 'junk'}) is None


def test_payload_hash_is_canonical():
    log = _log([abi.TOPIC_PAUSED], data_words(ADMIN))
    reordered = json.loads(json.dumps(dict(reversed(list(log.items())))))
    assert abi.decode_log(log).payload_sha256 == abi.decode_log(reordered).payload_sha256
    assert re.fullmatch(r'[0-9a-f]{64}', abi.decode_log(log).payload_sha256)


def test_log_filters_per_target_type():
    all_profiles = list(ewc.DETECTION_PROFILE_KEYS)
    contract = abi.log_filters_for_target(target_type='contract', address=TOKEN, profiles=all_profiles)
    assert len(contract) == 1 and contract[0]['address'] == TOKEN
    assert abi.TOPIC_SAFE_ADDED_OWNER not in contract[0]['topics'][0]
    wallet = abi.log_filters_for_target(target_type='wallet', address=OTHER, profiles=all_profiles)
    assert wallet == [
        {'topics': [abi.TOPIC_TRANSFER, abi.padded_address_topic(OTHER)]},
        {'topics': [abi.TOPIC_TRANSFER, None, abi.padded_address_topic(OTHER)]},
    ]
    safe = abi.log_filters_for_target(target_type='multisig', address=SAFE, profiles=['multisig_configuration'])
    assert len(safe) == 1 and abi.TOPIC_SAFE_CHANGED_THRESHOLD in safe[0]['topics'][0]
    only_pause = abi.log_filters_for_target(target_type='contract', address=TOKEN, profiles=['pause_unpause'])
    assert sorted(only_pause[0]['topics'][0]) == sorted([abi.TOPIC_PAUSED, abi.TOPIC_UNPAUSED])


# ── RPC gateway ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize('method', [
    'eth_sendRawTransaction', 'eth_sendTransaction', 'eth_sign', 'personal_sign', 'eth_signTypedData_v4',
    'eth_accounts', 'eth_requestAccounts', 'wallet_addEthereumChain', 'eth_signTransaction',
])
def test_gateway_refuses_every_write_or_signing_method(method):
    chain = FakeChain()
    client = rpc.ReadOnlyRpcClient(chain)
    with pytest.raises(rpc.ExternalExecutionForbidden):
        client.call(method, [])
    assert chain.calls == []


def test_budget_and_read_helpers():
    chain = FakeChain(tip=500, eth_call_results={(TOKEN, '0x8da5cb5b'): '0x' + '0' * 24 + ADMIN[2:]})
    client = rpc.ReadOnlyRpcClient(chain, budget=rpc.CallBudget(3))
    assert rpc.block_number(client) == 500
    assert rpc.eth_call(client, TOKEN, abi.SELECTOR_OWNER).endswith(ADMIN[2:])
    assert set(chain.calls[-1][1][0]) == {'to', 'data'}  # no from, no value, no gas
    rpc.chain_id(client)
    with pytest.raises(rpc.RpcBudgetExhausted):
        rpc.block_number(client)


def test_errors_never_carry_endpoint_urls():
    text = rpc.sanitize_error(RuntimeError('503 from https://base.example-rpc.com/v2/SECRETKEY123 now'))
    assert 'SECRETKEY123' not in text and '<base.example-rpc.com>' in text


@pytest.mark.parametrize('message,expected', [
    ("all_rpc_providers_unavailable:json-rpc error: {'code': -32005, 'message': 'query returned more than 10000 results'}", True),
    ('Log response size exceeded. block range 0x1429 to 0x2000', True),
    ('HTTP Error 413: Request Entity Too Large', True),
    ('HTTP Error 429: Too Many Requests', False),
    ('exceeded compute units per second capacity, block range too wide', False),
    ('HTTP Error 503: Service Unavailable', False),
])
def test_size_refusals_are_told_apart_from_outages_and_throttling(message, expected):
    assert rpc.is_range_error(RuntimeError(message)) is expected


def _transfers(count, start=10):
    return [
        _log([abi.TOPIC_TRANSFER, topic_address(ADMIN), topic_address(OTHER)], data_words(100), block=start + i * 7, n=i + 1)
        for i in range(count)
    ]


def test_log_chunks_halve_on_size_refusal_and_miss_nothing():
    logs = _transfers(40)
    chain = FakeChain(tip=1000, logs=logs, max_range=64)
    chunks = list(rpc.iter_log_chunks(rpc.ReadOnlyRpcClient(chain), filters=[{'address': TOKEN}],
                                      from_block=0, to_block=999, chunk_size=1000, sleep=lambda s: None))
    assert chunks[0].from_block == 0 and chunks[-1].to_block == 999
    for before, after in zip(chunks, chunks[1:]):
        assert after.from_block == before.to_block + 1
    assert all(chunk.to_block - chunk.from_block + 1 <= 64 for chunk in chunks)
    assert sum(len(chunk.logs) for chunk in chunks) == 40


def test_outages_back_off_exponentially_then_stop_at_the_failing_range():
    sleeps: list[float] = []
    chain = FakeChain(tip=1000, logs=_transfers(5), fail_ranges=[(0, 99)], fail_times=2)
    chunks = list(rpc.iter_log_chunks(rpc.ReadOnlyRpcClient(chain), filters=[{'address': TOKEN}],
                                      from_block=0, to_block=199, chunk_size=100, backoff_seconds=2.0,
                                      sleep=sleeps.append))
    assert sleeps == [2.0, 4.0] and chunks[0].retries == 2 and len(chunks) == 2

    chain = FakeChain(tip=1000, fail_ranges=[(100, 199)])
    seen = []
    with pytest.raises(rpc.ChunkFetchFailed) as failure:
        for chunk in rpc.iter_log_chunks(rpc.ReadOnlyRpcClient(chain), filters=[{'address': TOKEN}], from_block=0,
                                         to_block=299, chunk_size=100, max_retries=2, sleep=lambda s: None):
            seen.append((chunk.from_block, chunk.to_block))
    assert seen == [(0, 99)]  # everything before the failing range was delivered
    assert (failure.value.from_block, failure.value.to_block) == (100, 199)


def test_rate_limits_back_off_without_shrinking_the_range():
    sleeps: list[float] = []
    chain = FakeChain(tip=1000, rate_limit_times=1)
    chunks = list(rpc.iter_log_chunks(rpc.ReadOnlyRpcClient(chain), filters=[{'address': TOKEN}],
                                      from_block=0, to_block=99, chunk_size=100, sleep=sleeps.append))
    assert [(c.from_block, c.to_block) for c in chunks] == [(0, 99)] and sleeps == [2.0]


def test_duplicate_and_removed_logs_are_dropped():
    log = _transfers(1)[0]
    chain = FakeChain(tip=100, logs=[log, {**_transfers(2)[1], 'removed': True}])
    chunks = list(rpc.iter_log_chunks(rpc.ReadOnlyRpcClient(chain), filters=[{'address': TOKEN}, {'topics': [abi.TOPIC_TRANSFER]}],
                                      from_block=0, to_block=99, chunk_size=100, sleep=lambda s: None))
    assert len(chunks[0].logs) == 1


def test_block_estimation_lands_near_the_requested_time():
    chain = FakeChain(tip=1_000_000, block_seconds=2)
    target = chain.timestamp_of(700_000)
    estimate = rpc.estimate_block_at_timestamp(
        rpc.ReadOnlyRpcClient(chain), target_timestamp=target, tip=chain.tip,
        tip_timestamp=chain.timestamp_of(chain.tip), avg_block_seconds=2.3,
    )
    assert abs(estimate['block'] - 700_000) * 2 <= 900 and estimate['approximate'] is True


# ── detection rules ──────────────────────────────────────────────────────────
def _ctx(target_type='contract', address=TOKEN, profiles=None, config=None):
    watchlist = {'id': 'w1', 'detection_profiles': profiles if profiles is not None else list(ewc.DETECTION_PROFILE_KEYS),
                 'detection_config': config or {}}
    target = {'id': 't1', 'target_type': target_type, 'address': address, 'network': 'base-mainnet', 'chain_id': 8453}
    return detection.build_context(watchlist=watchlist, target=target)


def _event(topics, data='0x', *, n=1, block=100, address=TOKEN, initiator=ADMIN):
    event = abi.decode_log(_log(topics, data, n=n, block=block, address=address))
    event.initiator = initiator
    return event


def _all_text(drafts):
    return [text for d in drafts for text in (d.title, d.explanation, d.interpretation)]


def test_privileged_role_grant():
    state = detection.RuleState()
    drafts = detection.evaluate_event(_ctx(), _event([abi.TOPIC_ROLE_GRANTED, MINTER, topic_address(OTHER), topic_address(ADMIN)]),
                                      state, observed_at=T0)
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.title == 'Privileged role granted: MINTER_ROLE'
    assert draft.finding_class == 'privileged_configuration_change'
    assert draft.severity in ewc.EXTERNAL_SEVERITIES
    assert draft.dedupe_key == f'access_control.role_granted:{tx(1)}:0'
    assert state.runtime_state['roles'][MINTER] == [OTHER]
    # A disabled profile produces nothing.
    assert detection.evaluate_event(_ctx(profiles=['pause_unpause']),
                                    _event([abi.TOPIC_ROLE_GRANTED, MINTER, topic_address(OTHER), topic_address(ADMIN)]),
                                    detection.RuleState(), observed_at=T0) == []


def test_upgrade_previous_state_is_only_what_was_observed():
    state = detection.RuleState()
    first = detection.evaluate_event(_ctx(), _event([abi.TOPIC_UPGRADED, topic_address(addr(1))], n=1), state, observed_at=T0)
    second = detection.evaluate_event(_ctx(), _event([abi.TOPIC_UPGRADED, topic_address(addr(2))], n=2), state, observed_at=T0)
    assert first[0].previous_state == {'implementation': 'not observed in monitored history'}
    assert second[0].previous_state == {'implementation': addr(1)}
    assert second[0].new_state == {'implementation': addr(2)}


def test_initial_ownership_is_review_not_alarm():
    drafts = detection.evaluate_event(
        _ctx(), _event([abi.TOPIC_OWNERSHIP_TRANSFERRED, topic_address(abi.ZERO_ADDRESS), topic_address(ADMIN)]),
        detection.RuleState(), observed_at=T0)
    assert drafts[0].finding_class == 'review' and drafts[0].severity == 'low'


def test_lowered_multisig_threshold():
    state = detection.RuleState(runtime_state={'safe': {'threshold': 3}})
    drafts = detection.evaluate_event(_ctx('multisig', SAFE), _event([abi.TOPIC_SAFE_CHANGED_THRESHOLD], data_words(1), address=SAFE),
                                      state, observed_at=T0)
    assert drafts[0].previous_state == {'threshold': 3} and drafts[0].new_state == {'threshold': 1}
    assert 'lowered' in drafts[0].interpretation


def test_transfers_are_judged_against_the_tokens_own_history():
    ctx, state = _ctx(), detection.RuleState()
    transfer = lambda amount, n: _event([abi.TOPIC_TRANSFER, topic_address(ADMIN), topic_address(OTHER)], data_words(amount), n=n)
    # Before a baseline exists, even a huge transfer is not called unusual.
    assert detection.evaluate_event(ctx, transfer(10**30, 1), detection.RuleState(), observed_at=T0) == []
    for i in range(25):
        assert detection.evaluate_event(ctx, transfer(100, i + 2), state, observed_at=T0) == []
    drafts = detection.evaluate_event(ctx, transfer(100_000, 99), state, observed_at=T0)
    assert [d.title for d in drafts] == ['Unusually large transfer observed']
    assert drafts[0].finding_class == 'unusual_activity' and 'observed so far' in drafts[0].explanation

    mint = lambda amount, n: _event([abi.TOPIC_TRANSFER, topic_address(abi.ZERO_ADDRESS), topic_address(OTHER)], data_words(amount), n=n)
    for i in range(25):
        detection.evaluate_event(ctx, mint(1000, 200 + i), state, observed_at=T0)
    assert [d.title for d in detection.evaluate_event(ctx, mint(10**9, 300), state, observed_at=T0)] == ['Large mint observed']
    nft = _event([abi.TOPIC_TRANSFER, topic_address(ADMIN), topic_address(OTHER), topic_uint(5)], n=400)
    assert detection.evaluate_event(ctx, nft, state, observed_at=T0) == []


def test_oracle_deviation_gap_and_heartbeat():
    ctx = _ctx('oracle', FEED, config={'oracle_heartbeat_seconds': 3600})
    state = detection.RuleState()
    update = lambda answer, at, n: _event([abi.TOPIC_ANSWER_UPDATED, topic_uint(answer), topic_uint(n)], data_words(at), n=n, address=FEED)
    base = 1_700_000_000
    assert detection.evaluate_event(ctx, update(1000, base, 1), state, observed_at=T0) == []
    assert detection.evaluate_event(ctx, update(1010, base + 3600, 2), state, observed_at=T0) == []
    titles = [d.title for d in detection.evaluate_event(ctx, update(1200, base + 3600 * 5, 3), state, observed_at=T0)]
    assert titles == ['Unusual oracle price change observed', 'Oracle update gap observed']
    now = datetime.fromtimestamp(base + 3600 * 5 + 3600 * 4, tz=timezone.utc)
    heartbeat = detection.evaluate_heartbeat(ctx, state=state, now=now, network='base-mainnet', chain_id=8453)
    assert [d.title for d in heartbeat] == ['Oracle heartbeat interruption observed']
    assert detection.evaluate_heartbeat(ctx, state=state, now=now - timedelta(hours=3), network='base-mainnet', chain_id=8453) == []


def test_every_rule_uses_careful_language_and_never_says_critical():
    state = detection.RuleState()
    ctx = _ctx()
    drafts = []
    for n, topics in enumerate([
        [abi.TOPIC_ROLE_GRANTED, abi.ZERO_WORD, topic_address(OTHER), topic_address(ADMIN)],
        [abi.TOPIC_ROLE_REVOKED, abi.ZERO_WORD, topic_address(OTHER), topic_address(ADMIN)],
        [abi.TOPIC_ROLE_ADMIN_CHANGED, MINTER, abi.ZERO_WORD, MINTER],
        [abi.TOPIC_OWNERSHIP_TRANSFERRED, topic_address(ADMIN), topic_address(abi.ZERO_ADDRESS)],
        [abi.TOPIC_OWNERSHIP_TRANSFER_STARTED, topic_address(ADMIN), topic_address(OTHER)],
        [abi.TOPIC_ADMIN_CHANGED],
        [abi.TOPIC_BEACON_UPGRADED, topic_address(OTHER)],
    ], start=1):
        data = data_words(ADMIN, OTHER) if topics[0] == abi.TOPIC_ADMIN_CHANGED else '0x'
        drafts += detection.evaluate_event(ctx, _event(topics, data, n=n), state, observed_at=T0)
    drafts += detection.evaluate_event(ctx, _event([abi.TOPIC_PAUSED], data_words(ADMIN), n=20), state, observed_at=T0)
    for n, topic in enumerate([abi.TOPIC_SAFE_ADDED_OWNER, abi.TOPIC_SAFE_REMOVED_OWNER, abi.TOPIC_SAFE_ENABLED_MODULE,
                               abi.TOPIC_SAFE_CHANGED_GUARD, abi.TOPIC_SAFE_CHANGED_MASTER_COPY], start=30):
        drafts += detection.evaluate_event(_ctx('multisig', SAFE), _event([topic], data_words(OTHER), n=n, address=SAFE),
                                           state, observed_at=T0)
    assert len(drafts) == 13
    banned = re.compile(ewc.BANNED_LANGUAGE_PATTERN, re.IGNORECASE)
    assert not [text for text in _all_text(drafts) if banned.search(text)]
    assert {d.severity for d in drafts} <= set(ewc.EXTERNAL_SEVERITIES)
    assert {d.finding_class for d in drafts} <= set(ewc.FINDING_CLASSES)
    with pytest.raises(detection.LanguageViolation):
        detection.assert_careful_language('Possible exploit of the bridge')


# ── historical scans never touch the live state ──────────────────────────────
def test_history_state_is_per_job_and_per_range():
    job = {'rule_state': {'range_index': 1, 'state': {'proxy': {'implementation': addr(1)}}}}
    assert backfill.history_rule_state(job, 1) == {'proxy': {'implementation': addr(1)}}
    assert backfill.history_rule_state(job, 0) == {}
    assert backfill.history_rule_state({}, 0) == {}


class _RecordingConnection:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((' '.join(sql.split()), params))
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


def test_a_historical_scan_saves_its_state_on_the_job_not_the_target():
    connection = _RecordingConnection()
    state = detection.RuleState(runtime_state={'proxy': {'implementation': addr(2)}})
    state.mark_dirty()
    ingest.persist_rule_state(connection, 't1', state, backfill_id='job-1', history_range_index=0)
    assert len(connection.statements) == 1
    sql, params = connection.statements[0]
    assert sql.startswith('UPDATE external_watchlist_backfills SET rule_state')
    assert json.loads(params[0]) == {'range_index': 0, 'state': {'proxy': {'implementation': addr(2)}}}

    live = _RecordingConnection()
    state.mark_dirty()
    ingest.persist_rule_state(live, 't1', state)
    assert live.statements[0][0].startswith('UPDATE external_watchlist_targets SET runtime_state')


def test_live_seed_is_not_the_previous_state_of_a_historical_upgrade():
    # The probe seeded the live state with TODAY's implementation. Replaying an
    # older upgrade must not claim today's implementation was its predecessor.
    target = {'id': 't1', 'runtime_state': {'proxy': {'implementation': addr(9)}}}
    connection = SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(fetchall=lambda: []))
    import services.api.app.domains.external_watchlist.service as service
    original = service.load_baselines
    service.load_baselines = lambda *_a, **_k: []
    try:
        history = ingest.load_rule_state(connection, target, runtime_state={})
        live = ingest.load_rule_state(connection, target)
    finally:
        service.load_baselines = original
    drafts = detection.evaluate_event(_ctx(), _event([abi.TOPIC_UPGRADED, topic_address(addr(3))]), history, observed_at=T0)
    assert drafts[0].previous_state == {'implementation': 'not observed in monitored history'}
    assert live.runtime_state['proxy']['implementation'] == addr(9)


# ── status derivation ────────────────────────────────────────────────────────
NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
CFG = {'error_failure_threshold': 5, 'worker_heartbeat_stale_seconds': 360, 'poll_stale_seconds': 360}


def _status(*, watchlist=None, targets=None, jobs=(), heartbeat=NOW - timedelta(seconds=30), rpc_ok=True):
    target = {'id': 't1', 'network': 'base-mainnet', 'monitoring_enabled': True,
              'last_successful_poll_at': NOW - timedelta(seconds=60), 'consecutive_failures': 0}
    return status_model.derive_status(
        watchlist=watchlist or {'monitoring_enabled': True},
        targets=[target] if targets is None else targets, latest_backfills=list(jobs), worker_heartbeat_at=heartbeat,
        now=NOW, config=CFG, rpc_configured=lambda network: rpc_ok,
    )


def _target(**overrides):
    base = {'id': 't1', 'network': 'base-mainnet', 'monitoring_enabled': True,
            'last_successful_poll_at': NOW - timedelta(seconds=60), 'consecutive_failures': 0}
    return {**base, **overrides}


def test_status_truth_table():
    assert _status() == {'status': 'live', 'reason': None}
    assert _status(watchlist={'monitoring_enabled': False})['status'] == 'paused'
    assert _status(targets=[]) == {'status': 'paused', 'reason': 'no_active_targets'}
    assert _status(rpc_ok=False)['reason'] == 'rpc_not_configured'
    assert _status(targets=[_target(consecutive_failures=5)])['reason'] == 'repeated_poll_failures'
    running = [{'target_id': 't1', 'status': 'running'}]
    assert _status(jobs=running)['status'] == 'backfilling'
    # No heartbeat: never "live", never "backfilling" — nothing is running.
    assert _status(heartbeat=None) == {'status': 'degraded', 'reason': 'worker_not_running'}
    assert _status(jobs=running, heartbeat=NOW - timedelta(hours=1))['reason'] == 'worker_not_running'
    assert _status(targets=[_target(last_successful_poll_at=None)])['reason'] == 'awaiting_first_poll'
    assert _status(targets=[_target(last_successful_poll_at=NOW - timedelta(hours=2))])['reason'] == 'poll_stale'
    assert _status(jobs=[{'target_id': 't1', 'status': 'partial'}])['reason'] == 'history_scan_incomplete'
    assert _status(targets=[_target(consecutive_failures=1)])['reason'] == 'recent_poll_failures'


def test_every_non_live_reason_has_a_label():
    reasons = set(re.findall(r"'reason': '([a-z_]+)'", (DOMAIN / 'status.py').read_text()))
    reasons.update({'no_active_targets', 'all_targets_paused'})
    assert reasons <= set(status_model.STATUS_REASON_LABELS)


def test_backfill_progress_and_rpc_health():
    progress = status_model.backfill_progress({'status': 'running', 'requested_days': 30, 'total_blocks': 1000,
                                               'scanned_blocks': 700, 'planned_at': NOW})
    assert (progress['percent'], progress['days_analyzed']) == (70.0, 21.0)
    assert status_model.backfill_progress({'status': 'completed', 'requested_days': 7})['percent'] == 100.0
    assert status_model.backfill_progress({'status': 'pending', 'requested_days': 7})['percent'] == 0.0
    health = status_model.rpc_health([_target(last_successful_poll_at=None)], now=NOW, config=CFG, rpc_configured=lambda n: True)
    assert health[0]['state'] == 'unknown'
    assert status_model.rpc_health([_target()], now=NOW, config=CFG, rpc_configured=lambda n: False)[0]['state'] == 'not_configured'


# ── analysis ─────────────────────────────────────────────────────────────────
def _facts():
    draft = detection.evaluate_event(_ctx(), _event([abi.TOPIC_ROLE_GRANTED, MINTER, topic_address(OTHER), topic_address(ADMIN)]),
                                     detection.RuleState(), observed_at=T0)[0]
    return analysis.build_facts(draft=draft, watchlist={'name': 'Example RWA'},
                                target={'network': 'base-mainnet', 'target_type': 'contract', 'address': TOKEN})


def test_deterministic_analysis_has_three_separate_statements():
    result = analysis.build_deterministic_analysis(_facts())
    assert tx(1) in result['observed_fact'] and 'successfully confirmed on-chain' in result['observed_fact']
    assert result['operational_authorization'] == ewc.AUTHORIZATION_UNKNOWN_STATEMENT
    assert result['monitoring_scope'] == 'external_public'
    assert analysis.validate_analysis(result, facts=_facts())


@pytest.mark.parametrize('override', [
    {'decoda_interpretation': ''},
    {'decoda_interpretation': 'This looks like an attack on the token.'},
    {'operational_authorization': 'The organization approved this change.'},
    {'observed_fact': f'Transaction {tx(0xDEAD)} changed the role.'},
    # A made-up hash built around a real address is still made up.
    {'observed_fact': f'Transaction {OTHER}{"ab" * 12} changed the role.'},
])
def test_validation_rejects_unsafe_analyses(override):
    candidate = {**analysis.build_deterministic_analysis(_facts()), **override}
    with pytest.raises(analysis.AnalysisValidationError):
        analysis.validate_analysis(candidate, facts=_facts())


def test_ai_output_is_validated_and_falls_back(monkeypatch):
    from services.api.app import ai_providers

    cfg = {'enabled': True, 'provider': 'openai', 'has_key': True, 'model': 'test-model'}
    good = analysis.build_deterministic_analysis(_facts())
    replies = {'text': json.dumps({k: good[k] for k in ('observed_fact', 'decoda_interpretation', 'operational_authorization')})}

    class Provider:
        def analyze(self, **_kwargs):
            return SimpleNamespace(raw_text=replies['text'], provider='openai', model='test-model')

    monkeypatch.setattr(ai_providers, 'get_triage_provider', lambda _name: Provider())
    assert analysis.generate_analysis(_facts(), config=cfg)['source'] == 'ai'
    replies['text'] = json.dumps({**good, 'decoda_interpretation': 'A hack in progress.'})
    fallback = analysis.generate_analysis(_facts(), config=cfg)
    assert fallback['source'] == 'deterministic' and fallback['ai_fallback_reason']
    assert analysis.generate_analysis(_facts(), config={'enabled': False})['source'] == 'deterministic'


# ── evidence ─────────────────────────────────────────────────────────────────
FINDING = {
    'id': 'finding-uuid-1', 'watchlist_id': 'watchlist-uuid-1', 'network': 'base-mainnet', 'chain_id': 8453,
    'contract_address': TOKEN, 'tx_hash': tx(1), 'block_number': 100, 'log_index': 0, 'observed_at': T0,
    'title': 'Privileged role granted: MINTER_ROLE', 'finding_type': 'privileged_role_granted',
    'finding_class': 'privileged_configuration_change', 'severity': 'high', 'confidence': 0.91,
    'status': 'outreach_candidate', 'status_note': 'call them', 'score_inputs': {'x': 1}, 'rule_key': 'access_control.role_granted',
    'explanation': 'MINTER_ROLE was granted.', 'decoded': {'role': MINTER}, 'primary_event_id': 'event-uuid-1',
    'ai_analysis': analysis.build_deterministic_analysis({'explanation': 'x', 'network': 'Base Mainnet', 'interpretation': 'y'}),
}
EVENTS = [{'id': 'event-uuid-1', 'event_name': 'RoleGranted', 'tx_hash': tx(1), 'block_number': 100, 'log_index': 0,
           'payload_sha256': 'a' * 64, 'raw_log': {'topics': []}, 'observed_at_source': 'block_timestamp'}]


def test_evidence_package_is_sealed_verifiable_and_tamper_evident():
    package = evidence.build_evidence_package(watchlist={'name': 'Example RWA', 'website_url': None},
                                              target={'address': TOKEN, 'target_type': 'contract'},
                                              finding=FINDING, events=EVENTS, generated_by=None, now=T0)
    document = package['files'][evidence.EVIDENCE_FILE]
    assert document['monitoring_scope'] == 'external_public' and document['execution_authority'] == 'NONE'
    assert document['disclaimer'] == ewc.EVIDENCE_DISCLAIMER
    assert document['provenance']['organization_authorized_monitoring'] == 'not_established'
    assert re.fullmatch(r'[0-9a-f]{64}', package['evidence_sha256'])
    verified = evidence.verify_package(package)
    assert verified['valid'] is True and verified['status'] == 'verified'
    assert verified['production_secret'] is False  # the test runner uses the development key, and says so

    tampered = json.loads(json.dumps(package, default=str))
    tampered['files'][evidence.EVIDENCE_FILE]['block_number'] = 101
    assert evidence.verify_package(tampered)['valid'] is False


def test_prospect_report_excludes_internal_information():
    verification = {'status': 'verified', 'evidence_sha256': 'e' * 64, 'manifest_sha256': 'f' * 64,
                    'signature_algorithm': 'HMAC-SHA256', 'public_key_signature': 'absent', 'production_secret': True}
    built = evidence.build_prospect_report(watchlist={'name': 'Example RWA', 'id': 'watchlist-uuid-1'},
                                           finding=FINDING, verification=verification, generated_by='founder-uuid', now=T0)
    report = built['report']
    serialized = json.dumps(report)
    for internal in ('finding-uuid-1', 'watchlist-uuid-1', 'event-uuid-1', 'founder-uuid', 'outreach_candidate',
                     'call them', '0.91', 'score_inputs', 'rule_key', '"severity"', 'rpc'):
        assert internal not in serialized, internal
    assert report['heading'] == 'Administrative Change Detected'
    assert report['disclaimer'] == ewc.EVIDENCE_DISCLAIMER
    assert report['transaction_explorer_url'] == f'https://basescan.org/tx/{tx(1)}'
    assert evidence.verify_package(built)['valid'] is True


# ── access, flag and execution boundary ──────────────────────────────────────
class _Refused(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


@pytest.fixture()
def fake_backend(monkeypatch):
    """pg_connection + require_internal_admin, with the caller chosen per test."""
    caller = {'admin': True}

    @contextlib.contextmanager
    def connection():
        yield SimpleNamespace(execute=lambda *a, **k: pytest.fail('no data may be read'), commit=lambda: None)

    def require_internal_admin(_connection, _request):
        if not caller['admin']:
            raise endpoints.HTTPException(status_code=403, detail={'code': 'INTERNAL_ADMIN_REQUIRED'})
        return {'id': 'founder-uuid', 'email': 'founder@decoda.test'}

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', connection)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(org_service, 'require_internal_admin', require_internal_admin)
    monkeypatch.delenv('EXTERNAL_WATCHLIST_ENABLED', raising=False)
    return caller


def test_the_feature_is_off_by_default():
    assert ewc.feature_enabled() is False or pytest.skip('flag set in this environment')


def test_a_customer_is_refused_before_the_flag_is_even_consulted(fake_backend, monkeypatch):
    fake_backend['admin'] = False
    for enabled in ('', 'true'):
        monkeypatch.setenv('EXTERNAL_WATCHLIST_ENABLED', enabled)
        with pytest.raises(endpoints.HTTPException) as refused:
            endpoints.list_watchlists(object())
        assert refused.value.status_code == 403
        with pytest.raises(endpoints.HTTPException) as refused:
            endpoints.get_console_config(object())
        assert refused.value.status_code == 403


def test_a_founder_sees_404_when_disabled_and_503_without_the_migration(fake_backend, monkeypatch):
    with pytest.raises(endpoints.HTTPException) as disabled:
        endpoints.list_watchlists(object())
    assert disabled.value.status_code == 404 and _code(disabled.value) == 'EXTERNAL_WATCHLIST_DISABLED'
    assert endpoints.get_console_config(object()) == {'internal_admin': True, 'features': {'external_watchlist': {'enabled': False}}}

    monkeypatch.setenv('EXTERNAL_WATCHLIST_ENABLED', 'true')
    monkeypatch.setattr(endpoints.service, 'schema_ready', lambda _c: False)
    with pytest.raises(endpoints.HTTPException) as missing:
        endpoints.list_watchlists(object())
    assert missing.value.status_code == 503


@pytest.mark.parametrize('capability', endpoints.FORBIDDEN_CAPABILITIES)
def test_execution_is_refused_for_founders_and_customers(fake_backend, capability):
    with pytest.raises(endpoints.HTTPException) as founder:
        endpoints.refuse_execution('any-id', capability, object())
    assert founder.value.status_code == 403 and _code(founder.value) == 'EXTERNAL_EXECUTION_FORBIDDEN'
    fake_backend['admin'] = False
    with pytest.raises(endpoints.HTTPException) as customer:
        endpoints.refuse_execution('any-id', capability, object())
    assert customer.value.status_code == 403 and _code(customer.value) == 'INTERNAL_ADMIN_REQUIRED'


def test_every_forbidden_capability_is_a_registered_refusal_route():
    main = (APP / 'main.py').read_text()
    assert 'external_watchlist_endpoints.FORBIDDEN_CAPABILITIES' in main
    assert 'external_watchlist_endpoints.refuse_execution' in main


@pytest.mark.parametrize('body', [
    {'name': 'x', 'private_key': '0xabc'},
    {'targets': [{'address': TOKEN, 'signer': ADMIN}]},
    {'wallet_connect_session': 'x'},
    {'meta': {'seed_phrase': 'x'}},
    {'rawTransaction': '0x'},
])
def test_credential_fields_are_refused_anywhere(body):
    with pytest.raises(endpoints.HTTPException) as refused:
        endpoints.refuse_credentials(body)
    assert refused.value.status_code == 400 and _code(refused.value) == 'EXTERNAL_CREDENTIALS_REFUSED'


def test_execution_authority_cannot_be_raised():
    endpoints.refuse_credentials({'execution_authority': 'none'})
    with pytest.raises(endpoints.HTTPException) as refused:
        endpoints.refuse_credentials({'execution_authority': 'FULL'})
    assert refused.value.status_code == 403


def test_target_input_validation():
    ok = endpoints._target_input({'address': '0x' + 'Ab' * 20, 'target_type': 'MULTISIG'}, network_value='8453')
    assert ok == {'network': 'base-mainnet', 'chain_id': 8453, 'address': '0x' + 'ab' * 20,
                  'target_type': 'multisig', 'label': None}
    cases = [
        ({'address': '0x123'}, 'base-mainnet', 'INVALID_ADDRESS'),
        ({'address': abi.ZERO_ADDRESS}, 'base-mainnet', 'INVALID_ADDRESS'),
        ({'address': TOKEN}, 'solana', 'UNSUPPORTED_NETWORK'),
        ({'address': TOKEN}, '137', 'UNSUPPORTED_NETWORK'),
        ({'address': TOKEN, 'target_type': 'bridge'}, 'base-mainnet', 'INVALID_TARGET_TYPE'),
        ({'address': TOKEN, 'label': '0x' + 'ab' * 32}, 'base-mainnet', 'EXTERNAL_CREDENTIALS_REFUSED'),
    ]
    for body, network, code in cases:
        with pytest.raises(endpoints.HTTPException) as refused:
            endpoints._target_input(body, network_value=network)
        assert _code(refused.value) == code, body


def test_other_input_validation_and_pagination():
    assert endpoints._backfill_days(None) == 30 and endpoints._backfill_days(0) == 0
    for bad, allow_zero in ((5, True), ('x', True), (0, False)):
        with pytest.raises(endpoints.HTTPException):
            endpoints._backfill_days(bad, allow_zero=allow_zero)
    assert endpoints._website('example.org') == 'https://example.org'
    assert endpoints._website('https://spiko.io/en') == 'https://spiko.io/en'
    for bad in ('javascript:alert(1)', 'ftp://example.org', 'https://a b', 'https://user:pw@example.org', 'localhost'):
        with pytest.raises(endpoints.HTTPException):
            endpoints._website(bad)
    assert endpoints._profiles(None) == list(ewc.DETECTION_PROFILE_KEYS)
    with pytest.raises(endpoints.HTTPException):
        endpoints._profiles(['unknown_profile'])
    assert endpoints._page(10_000, -5) == (200, 0)
    assert endpoints._page('junk', 'junk') == (50, 0)
    assert endpoints._page_meta(120, 50, 100, 20) == {'total': 120, 'limit': 50, 'offset': 100, 'returned': 20, 'has_more': False}


# ── structural boundary ──────────────────────────────────────────────────────
_EXTERNAL_TABLE = re.compile(r'\bexternal_watchlist(?:s|_targets|_events|_findings|_evidence|_backfills|_baselines|_conversions|_worker_state)\b')
_SQL_TABLE = re.compile(r'\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)')  # SQL keywords are upper case here


def test_no_other_module_touches_the_external_tables():
    offenders = [
        str(path.relative_to(APP)) for path in APP.rglob('*.py')
        if DOMAIN not in path.parents and _EXTERNAL_TABLE.search(path.read_text(encoding='utf-8'))
    ]
    assert offenders == []


def test_the_external_domain_only_touches_customer_tables_in_the_conversion():
    for path in DOMAIN.glob('*.py'):
        source = path.read_text(encoding='utf-8')
        ctes = set(re.findall(r'\b(\w+) AS \(', source))
        tables = {t for t in _SQL_TABLE.findall(source) if t not in ctes and not t.startswith('external_watchlist')}
        if path.name == 'conversion.py':
            # Copies drafts into the NEW Pilot workspace and restores the
            # founder's own active workspace — nothing else.
            assert tables <= {'assets', 'targets', 'users'}, tables
        elif path.name == 'service.py':
            assert tables <= {'information_schema'}, tables
        else:
            assert tables == set(), (path.name, tables)


def test_migration_locks_scope_authority_and_append_only_telemetry():
    sql = MIGRATION.read_text()
    assert sql.count("CHECK (monitoring_scope = 'external_public')") >= 4
    assert sql.count("CHECK (execution_authority = 'NONE')") >= 2
    assert "'^0x[0-9a-f]{40}$'" in sql
    assert 'BEFORE UPDATE ON external_watchlist_events' in sql
    assert 'BEFORE UPDATE ON external_watchlist_evidence' in sql
    assert 'workspace_id UUID NOT NULL' not in sql.split('external_watchlist_conversions')[0]


def test_worker_is_wired_but_idle_unless_enabled():
    root = APP.parents[2]
    assert 'external-watchlist-worker: python -m services.api.app.run_external_watchlist_worker' in (root / 'Procfile').read_text()
    assert 'external-watchlist-worker' in (APP.parent / 'docker-entrypoint.sh').read_text()
    from services.api.app.domains.external_watchlist import worker

    assert worker.resolve_startup_state({'enabled': False}) == ('disabled', [])
