"""Recovery state transition Critical -> Recovering -> Healthy (Screen 4 part 10).

Policy: after a provider outage a source must not flip straight back to Healthy on a
single successful poll — the configured number of CONSECUTIVE successful scheduled
polls is required first. The first success(es) read Recovering; only once the streak
reaches the threshold does the source read Healthy again.
"""
from __future__ import annotations

import json

from services.api.app import monitoring_health_engine as he
from services.api.app import pilot

WS = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
TARGET = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
PROVIDER = 'base-mainnet.g.alchemy.com'


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _HealthConn:
    """Stateful fake carrying the last inserted metadata forward as the 'previous' row."""

    def __init__(self):
        self.last_metadata = None
        self.inserted: list[dict] = []

    def execute(self, sql, params=None):
        q = ' '.join(str(sql).split()).lower()
        if q.startswith('select metadata from provider_health_records'):
            return _Result(row={'metadata': self.last_metadata} if self.last_metadata is not None else None)
        if q.startswith('insert into provider_health_records'):
            meta = json.loads(params[9])   # metadata is the 10th positional param
            self.last_metadata = meta
            self.inserted.append(meta)
        return _Result(None)

    def commit(self):
        pass


def _poll(conn, *, success, status, latency_ms, block=None):
    pilot.persist_provider_health_evidence(
        conn, workspace_id=WS, target_id=TARGET, provider_host=PROVIDER,
        status=status, success=success, latency_ms=latency_ms, latest_block=block,
        evidence_source='live',
    )
    return conn.inserted[-1]


# ---------------------------------------------------------------------------
# Pure classifier
# ---------------------------------------------------------------------------

def test_classifier_transitions():
    # Failed observation -> critical.
    assert he.classify_recovery_state(latest_success=False, consecutive_success=0) == he.RECOVERY_CRITICAL
    # First success after a failure -> recovering (below the required streak).
    assert he.classify_recovery_state(
        latest_success=True, consecutive_success=1, required_consecutive_success=2,
        prev_consecutive_failure=1,
    ) == he.RECOVERY_RECOVERING
    # Second consecutive success -> healthy.
    assert he.classify_recovery_state(
        latest_success=True, consecutive_success=2, required_consecutive_success=2,
        prev_recovery_state=he.RECOVERY_RECOVERING,
    ) == he.RECOVERY_HEALTHY
    # A brand-new steady success (no prior failure) is healthy immediately.
    assert he.classify_recovery_state(
        latest_success=True, consecutive_success=1, required_consecutive_success=2,
        prev_consecutive_failure=0, prev_recovery_state=None,
    ) == he.RECOVERY_HEALTHY
    # None observation -> unknown.
    assert he.classify_recovery_state(latest_success=None, consecutive_success=0) == he.RECOVERY_UNKNOWN


def test_required_of_one_collapses_recovering_to_healthy():
    assert he.classify_recovery_state(
        latest_success=True, consecutive_success=1, required_consecutive_success=1,
        prev_consecutive_failure=3,
    ) == he.RECOVERY_HEALTHY


# ---------------------------------------------------------------------------
# 15. Persisted recovery requires the configured consecutive successful polls.
# ---------------------------------------------------------------------------

def test_recovery_requires_two_consecutive_successful_polls(monkeypatch):
    monkeypatch.setenv('RECOVERY_REQUIRED_CONSECUTIVE_SUCCESS', '2')
    conn = _HealthConn()

    # 1. A failed poll -> Critical.
    m1 = _poll(conn, success=False, status='error', latency_ms=21)
    assert m1['recovery_state'] == 'critical'
    assert m1['consecutive_failure'] == 1
    assert m1['last_failure_at'] is not None

    # 2. First successful poll -> Recovering (NOT straight to Healthy).
    m2 = _poll(conn, success=True, status='healthy', latency_ms=120, block=50_000_000)
    assert m2['recovery_state'] == 'recovering'
    assert m2['consecutive_success'] == 1
    assert m2['recovery_started_at'] is not None
    assert m2['last_successful_block'] == 50_000_000

    # 3. Second consecutive successful poll -> Healthy.
    m3 = _poll(conn, success=True, status='healthy', latency_ms=118, block=50_000_001)
    assert m3['recovery_state'] == 'healthy'
    assert m3['consecutive_success'] == 2


def test_recovery_requires_three_when_configured(monkeypatch):
    monkeypatch.setenv('RECOVERY_REQUIRED_CONSECUTIVE_SUCCESS', '3')
    conn = _HealthConn()
    _poll(conn, success=False, status='error', latency_ms=20)
    assert _poll(conn, success=True, status='healthy', latency_ms=100, block=1)['recovery_state'] == 'recovering'
    # Second success is STILL recovering (2 of 3) — must not flip to healthy early.
    assert _poll(conn, success=True, status='healthy', latency_ms=100, block=2)['recovery_state'] == 'recovering'
    assert _poll(conn, success=True, status='healthy', latency_ms=100, block=3)['recovery_state'] == 'healthy'


def test_new_failure_after_recovery_returns_to_critical(monkeypatch):
    monkeypatch.setenv('RECOVERY_REQUIRED_CONSECUTIVE_SUCCESS', '2')
    conn = _HealthConn()
    _poll(conn, success=True, status='healthy', latency_ms=100, block=1)
    _poll(conn, success=True, status='healthy', latency_ms=100, block=2)  # healthy
    m = _poll(conn, success=False, status='error', latency_ms=19)
    assert m['recovery_state'] == 'critical'
    # The last successful block is retained across the failure for the recovery display.
    assert m['last_successful_block'] == 2
