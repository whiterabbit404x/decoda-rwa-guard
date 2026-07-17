"""Tests for QuickNode webhook log rate-limiting (task: reduce QuickNode log flooding).

The per-block webhook logs are rate-limited to one line per sample window while every
state transition is preserved. These tests exercise the pure sampler helpers with an
injected clock so no wall-clock sleeping is required.
"""
from __future__ import annotations

from services.api.app import quicknode_streams as qs


def test_sampled_log_emits_once_per_window(monkeypatch) -> None:
    monkeypatch.delenv('QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS', raising=False)  # default 60s
    qs.reset_quicknode_log_sampler_state()
    assert qs._should_emit_sampled_quicknode_log('k', now=1000.0) is True
    # Within the window → suppressed.
    assert qs._should_emit_sampled_quicknode_log('k', now=1030.0) is False
    assert qs._should_emit_sampled_quicknode_log('k', now=1059.0) is False
    # Window elapsed → emit again (periodic summary).
    assert qs._should_emit_sampled_quicknode_log('k', now=1061.0) is True


def test_sampled_log_emits_immediately_on_signature_change(monkeypatch) -> None:
    monkeypatch.delenv('QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS', raising=False)
    qs.reset_quicknode_log_sampler_state()
    assert qs._should_emit_sampled_quicknode_log('k', signature='a', now=1000.0) is True
    assert qs._should_emit_sampled_quicknode_log('k', signature='a', now=1005.0) is False
    # A changed signature is a state transition — always emit, even within the window.
    assert qs._should_emit_sampled_quicknode_log('k', signature='b', now=1006.0) is True
    assert qs._should_emit_sampled_quicknode_log('k', signature='b', now=1007.0) is False


def test_sampling_disabled_when_window_zero(monkeypatch) -> None:
    monkeypatch.setenv('QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS', '0')
    qs.reset_quicknode_log_sampler_state()
    assert qs._should_emit_sampled_quicknode_log('k', now=1.0) is True
    assert qs._should_emit_sampled_quicknode_log('k', now=1.0) is True
    assert qs._should_emit_sampled_quicknode_log('k', now=1.0) is True


def test_degraded_decision_transitions_and_periodic(monkeypatch) -> None:
    monkeypatch.delenv('QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS', raising=False)  # 60s window
    qs.reset_quicknode_log_sampler_state()
    # healthy -> degraded : always log
    assert qs._quicknode_degraded_log_decision('s', degraded=True, now=100.0) == 'transition_degraded'
    # still degraded within window : suppress the identical per-block warning
    assert qs._quicknode_degraded_log_decision('s', degraded=True, now=130.0) == 'suppress'
    # still degraded, window elapsed : periodic summary
    assert qs._quicknode_degraded_log_decision('s', degraded=True, now=161.0) == 'periodic'
    # degraded -> healthy : always log the recovery transition
    assert qs._quicknode_degraded_log_decision('s', degraded=False, now=170.0) == 'transition_recovered'
    # steady healthy : suppress
    assert qs._quicknode_degraded_log_decision('s', degraded=False, now=175.0) == 'suppress'


def test_degraded_decision_isolated_per_stream_key(monkeypatch) -> None:
    monkeypatch.delenv('QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS', raising=False)
    qs.reset_quicknode_log_sampler_state()
    assert qs._quicknode_degraded_log_decision('a', degraded=True, now=1.0) == 'transition_degraded'
    # A different stream key has its own state → still a fresh transition.
    assert qs._quicknode_degraded_log_decision('b', degraded=True, now=1.0) == 'transition_degraded'


def test_reset_clears_sampler_state() -> None:
    qs._should_emit_sampled_quicknode_log('x', now=1.0)
    qs._quicknode_degraded_log_decision('x', degraded=True, now=1.0)
    qs.reset_quicknode_log_sampler_state()
    assert qs._LAST_SAMPLED_QUICKNODE_LOG_AT == {}
    assert qs._LAST_QUICKNODE_DEGRADED_STATE == {}
    assert qs._LAST_QUICKNODE_LOG_SIGNATURE == {}
