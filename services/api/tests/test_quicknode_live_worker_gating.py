"""Startup gating + configuration tests for the quicknode-live-worker entrypoint.

services/api/app/run_quicknode_live_worker.py

The production incident was in part a DEPLOYMENT gap: the live worker was never started
(railway-worker.json ran only the monitoring worker), so QuickNode never detected at the
chain tip. These tests pin the worker's explicit, non-silent startup behavior:

  * disabled  -> event=quicknode_live_lane_disabled (idles, never claims to be running)
  * enabled + valid config   -> quicknode_live_worker_started, then the tick loop
  * enabled + missing config -> event=quicknode_live_lane_configuration_error, exit 2
                                (Railway restarts it visibly; it never silently idles)
  * QUICKNODE_BACKFILL_ENABLED=false suspends the historical lane without touching the tip
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from services.api.app import run_quicknode_live_worker as w
from services.api.app import quicknode_streams as qn


class _StopLoop(Exception):
    pass


def test_config_error_helper_flags_missing_base_rpc():
    with patch.object(qn, '_make_base_rpc_client', lambda: None):
        assert w._live_worker_config_error() is not None
    with patch.object(qn, '_make_base_rpc_client', lambda: object()):
        assert w._live_worker_config_error() is None


def test_disabled_emits_lane_disabled_and_does_not_run(monkeypatch, caplog):
    monkeypatch.setenv('QUICKNODE_LIVE_ENABLED', 'false')
    # Break out of the idle loop after the first disabled log.
    monkeypatch.setattr(w.time, 'sleep', lambda _s: (_ for _ in ()).throw(_StopLoop()))
    with caplog.at_level('INFO', logger='services.api.app.run_quicknode_live_worker'):
        with pytest.raises(_StopLoop):
            w.main()
    assert 'event=quicknode_live_lane_disabled' in caplog.text
    assert 'enabled=false' in caplog.text


def test_enabled_but_missing_rpc_reports_configuration_error_and_exits_nonzero(monkeypatch, caplog):
    monkeypatch.setenv('QUICKNODE_LIVE_ENABLED', 'true')
    with patch.object(qn, '_make_base_rpc_client', lambda: None):
        with caplog.at_level('ERROR', logger='services.api.app.run_quicknode_live_worker'):
            rc = w.main()
    assert rc == 2  # non-zero -> Railway marks the service failed and restarts it
    assert 'event=quicknode_live_lane_configuration_error' in caplog.text
    assert 'severity=high' in caplog.text


def test_enabled_with_valid_config_starts_then_ticks(monkeypatch, caplog):
    monkeypatch.setenv('QUICKNODE_LIVE_ENABLED', 'true')
    monkeypatch.setenv('QUICKNODE_BACKFILL_ENABLED', 'true')
    with patch.object(qn, '_make_base_rpc_client', lambda: object()), \
         patch.object(w, 'run_one_tick', lambda: {'status': 'processed'}), \
         patch.object(w.time, 'sleep', lambda _s: (_ for _ in ()).throw(_StopLoop())):
        with caplog.at_level('INFO', logger='services.api.app.run_quicknode_live_worker'):
            with pytest.raises(_StopLoop):
                w.main()
    assert 'quicknode_live_worker_started' in caplog.text
    assert 'backfill_enabled=true' in caplog.text


# ---------------------------------------------------------------------------
# run_one_tick: backfill lane is independently gateable and never delays the tip
# ---------------------------------------------------------------------------

class _TickConn:
    def execute(self, query, params=None):
        class _R:
            def fetchone(self_inner):
                q = (query or '').strip().lower()
                if 'pg_try_advisory_lock' in q:
                    return {'acquired': True}
                return None

            def fetchall(self_inner):
                return []
        return _R()

    def commit(self):
        pass

    def rollback(self):
        pass


@contextmanager
def _fake_pg():
    yield _TickConn()


def _patch_tick(monkeypatch, backfill_calls):
    from services.api.app import pilot
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(qn, 'try_acquire_live_lane_lock', lambda _c: True)
    monkeypatch.setattr(qn, 'release_live_lane_lock', lambda _c: None)
    monkeypatch.setattr(qn, '_make_base_rpc_client', lambda: object())
    monkeypatch.setattr(qn, 'emit_quicknode_live_lane_started', lambda *a, **k: {})
    monkeypatch.setattr(qn, 'seed_backfill_from_base_checkpoint', lambda _c: False)
    monkeypatch.setattr(qn, 'backfill_start_block', lambda: None)
    monkeypatch.setattr(qn, '_load_all_base_wallet_targets', lambda _c: [])
    monkeypatch.setattr(qn, 'run_live_tip_ingest', lambda *a, **k: {'checkpoint_after': 100})

    def _run_backfill(*a, **k):
        backfill_calls.append(True)
        return {'lane': 'backfill'}
    monkeypatch.setattr(qn, 'run_backfill_step', _run_backfill)


def test_run_one_tick_runs_backfill_when_enabled(monkeypatch):
    monkeypatch.setenv('QUICKNODE_BACKFILL_ENABLED', 'true')
    calls: list = []
    _patch_tick(monkeypatch, calls)
    result = w.run_one_tick()
    assert result['status'] == 'processed'
    assert result['backfill'] == {'lane': 'backfill'}
    assert calls == [True]


def test_run_one_tick_skips_backfill_when_disabled_but_still_runs_live(monkeypatch):
    monkeypatch.setenv('QUICKNODE_BACKFILL_ENABLED', 'false')
    calls: list = []
    _patch_tick(monkeypatch, calls)
    result = w.run_one_tick()
    assert result['status'] == 'processed'
    assert result['live'] == {'checkpoint_after': 100}  # tip still processed
    assert result['backfill'] is None                   # historical lane suspended
    assert calls == []
