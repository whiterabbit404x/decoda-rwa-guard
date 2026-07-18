"""Canonical polling-only vs. real-time monitoring-mode resolver.

services/api/app/monitoring_runtime_mode.py

The polling-only MVP operates on stable scheduled RPC polling only; real-time
QuickNode Streams / WebSocket / mempool monitoring are paused and reversible
purely through configuration. These tests pin the single canonical switch
(REALTIME_STREAMS_ENABLED, default false => polling) and the startup log line
every service emits so the active mode is provable from logs.

Covers task test requirements 5 (scheduled polling always on) and 14 (reversible).
"""
from __future__ import annotations

import pytest

from services.api.app import monitoring_runtime_mode as mode

_LOGGER = 'services.api.app.monitoring_runtime_mode'


def _clear(monkeypatch):
    for name in ('REALTIME_STREAMS_ENABLED', 'BASE_REALTIME_ENABLED', 'MEMPOOL_MONITORING_ENABLED'):
        monkeypatch.delenv(name, raising=False)


def test_defaults_to_polling_only(monkeypatch):
    _clear(monkeypatch)
    resolved = mode.resolve_monitoring_runtime_mode()
    assert resolved.mode == 'polling'
    assert resolved.polling_only is True
    assert resolved.scheduled_polling_enabled is True
    assert resolved.realtime_streams_enabled is False
    assert resolved.websocket_enabled is False
    assert resolved.mempool_enabled is False
    assert mode.polling_only_mode() is True
    assert mode.realtime_streams_enabled() is False


def test_realtime_when_flag_true(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    resolved = mode.resolve_monitoring_runtime_mode()
    assert resolved.mode == 'realtime'
    assert resolved.polling_only is False
    assert resolved.realtime_streams_enabled is True
    # Scheduled RPC polling remains the canonical path even in real-time mode (req 5).
    assert resolved.scheduled_polling_enabled is True
    assert mode.polling_only_mode() is False


@pytest.mark.parametrize('value', ['', '   ', 'maybe', '0', 'false', 'no', 'off'])
def test_fail_closed_unknown_values_are_polling(monkeypatch, value):
    _clear(monkeypatch)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', value)
    assert mode.polling_only_mode() is True
    assert mode.resolve_monitoring_runtime_mode().mode == 'polling'


@pytest.mark.parametrize('value', ['1', 'true', 'TRUE', 'Yes', 'on', 'ON'])
def test_truthy_values_enable_realtime(monkeypatch, value):
    _clear(monkeypatch)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', value)
    assert mode.realtime_streams_enabled() is True
    assert mode.polling_only_mode() is False


def test_scheduled_polling_enabled_in_both_modes(monkeypatch):
    # Requirement 5 + 14: flipping the switch changes the mode but never disables
    # scheduled polling — the loop runs in both postures.
    _clear(monkeypatch)
    for value, expected_mode in (('false', 'polling'), ('true', 'realtime')):
        monkeypatch.setenv('REALTIME_STREAMS_ENABLED', value)
        resolved = mode.resolve_monitoring_runtime_mode()
        assert resolved.scheduled_polling_enabled is True
        assert resolved.mode == expected_mode


def test_websocket_and_mempool_reflect_their_own_flags(monkeypatch):
    # The resolved-mode report is truthful about each subsystem: websocket reflects
    # BASE_REALTIME_ENABLED, mempool reflects MEMPOOL_MONITORING_ENABLED (no component
    # today, so it stays false), rather than a hard-coded value.
    _clear(monkeypatch)
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    resolved = mode.resolve_monitoring_runtime_mode()
    assert resolved.websocket_enabled is True
    assert resolved.mempool_enabled is False


def test_to_dict_shape(monkeypatch):
    _clear(monkeypatch)
    payload = mode.resolve_monitoring_runtime_mode().to_dict()
    assert set(payload) == {
        'mode',
        'scheduled_polling_enabled',
        'realtime_streams_enabled',
        'websocket_enabled',
        'mempool_enabled',
    }


def test_startup_log_line_polling(monkeypatch, caplog):
    _clear(monkeypatch)
    with caplog.at_level('INFO', logger=_LOGGER):
        resolved = mode.log_monitoring_mode_resolved()
    assert resolved.mode == 'polling'
    assert 'event=monitoring_mode_resolved' in caplog.text
    assert 'mode=polling' in caplog.text
    assert 'scheduled_polling_enabled=true' in caplog.text
    assert 'realtime_streams_enabled=false' in caplog.text
    assert 'websocket_enabled=false' in caplog.text
    assert 'mempool_enabled=false' in caplog.text


def test_startup_log_line_realtime(monkeypatch, caplog):
    _clear(monkeypatch)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    with caplog.at_level('INFO', logger=_LOGGER):
        mode.log_monitoring_mode_resolved()
    assert 'mode=realtime' in caplog.text
    assert 'realtime_streams_enabled=true' in caplog.text
    assert 'scheduled_polling_enabled=true' in caplog.text
