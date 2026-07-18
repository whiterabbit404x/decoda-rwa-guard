"""Screen 4 Section 5: aggregate routine QuickNode stream logs; keep security immediate.

Production logs carried several routine lines per block (route_hit, handler_started,
signature_valid, payload_parsed, transactions_normalized, batch_range,
chain_head_refresh_skipped, lag_unknown, stream_summary). These tests pin the bounded
per-minute aggregator that folds those routine counts into ONE
``quicknode_stream_periodic_summary`` while security/state-transition events stay immediate.
"""
from __future__ import annotations

import logging

import pytest

from services.api.app import quicknode_streams as qn

QN_LOGGER = 'services.api.app.quicknode_streams'


@pytest.fixture(autouse=True)
def _reset():
    qn.reset_stream_activity()
    yield
    qn.reset_stream_activity()


def _messages(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Test 10: routine stream logs are aggregated into one per-minute summary
# ---------------------------------------------------------------------------

def test_routine_stream_activity_aggregates_into_one_periodic_summary(caplog):
    # Many blocks inside one window emit NO periodic summary yet (aggregated, not spammed).
    with caplog.at_level(logging.INFO, logger=QN_LOGGER):
        for i in range(30):
            emitted = qn.record_stream_activity(
                blocks=1,
                transactions=2,
                matched=1,
                persisted=1,
                health_status='healthy',
                chain_head_status='known',
                latest_stream_block=1000 + i,
                now_monotonic=100.0 + i,  # all within the same 60s window
            )
            assert emitted is False
    assert 'quicknode_stream_periodic_summary' not in _messages(caplog)
    snap = qn.stream_activity_snapshot()
    assert snap['blocks_received'] == 30
    assert snap['transactions_received'] == 60
    assert snap['matched'] == 30
    assert snap['persisted'] == 30
    assert snap['latest_stream_block'] == 1029


def test_periodic_summary_emitted_once_per_window(caplog):
    for i in range(5):
        qn.record_stream_activity(blocks=1, transactions=3, matched=1, persisted=1,
                                  health_status='healthy', chain_head_status='known',
                                  latest_stream_block=2000 + i, now_monotonic=10.0 + i)
    with caplog.at_level(logging.INFO, logger=QN_LOGGER):
        # A record past the 60s window boundary flushes exactly one aggregated summary.
        emitted = qn.record_stream_activity(blocks=1, transactions=3, matched=0, persisted=0,
                                            now_monotonic=10.0 + 61)
    assert emitted is True
    text = _messages(caplog)
    assert text.count('quicknode_stream_periodic_summary') == 1
    assert 'window_seconds=60' in text
    assert 'blocks_received=6' in text          # 5 in-window + the flushing one
    assert 'transactions_received=18' in text
    assert 'matched=5' in text
    assert 'persisted=5' in text
    assert 'latest_stream_block=2004' in text
    # After the flush the window resets to empty.
    snap = qn.stream_activity_snapshot()
    assert snap['blocks_received'] == 0
    assert snap['transactions_received'] == 0


def test_summary_response_feeds_aggregator_without_emitting_per_post(caplog):
    """The per-POST _summary_response still logs its immediate outcome line but folds
    the counts into the aggregator instead of emitting a periodic summary every POST."""
    with caplog.at_level(logging.INFO, logger=QN_LOGGER):
        qn._summary_response(
            tx_count=4, targets_loaded=1, matched=1, persisted=1,
            duplicates=0, skipped=0, results=[],
        )
    text = _messages(caplog)
    assert 'quicknode_stream_periodic_summary' not in text
    snap = qn.stream_activity_snapshot()
    assert snap['blocks_received'] == 1
    assert snap['transactions_received'] == 4
    assert snap['matched'] == 1
    assert snap['persisted'] == 1


# ---------------------------------------------------------------------------
# Test 11: security and state-transition events remain immediate (never aggregated)
# ---------------------------------------------------------------------------

def test_security_and_state_transition_events_are_immediate():
    for event in (
        'invalid_signature',
        'parse_failure',
        'persistence_failure',
        'health_state_transition',
        'lag_threshold_crossing',
        'provider_recovery',
        'circuit_breaker_open',
        'circuit_breaker_closed',
    ):
        assert qn.is_immediate_stream_event(event) is True, event


def test_routine_events_are_not_immediate():
    for event in (
        'route_hit',
        'handler_started',
        'signature_valid',
        'payload_parsed',
        'transactions_normalized',
        'batch_range',
        'chain_head_refresh_skipped',
        'lag_unknown',
        'stream_summary',
        '',
        None,
    ):
        assert qn.is_immediate_stream_event(event) is False, event
