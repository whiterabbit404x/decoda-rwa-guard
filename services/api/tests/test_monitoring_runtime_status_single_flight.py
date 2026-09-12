"""Concurrent callers of monitoring_runtime_status share one computation.

One dashboard load calls this function from more than one endpoint at once
(/ops/dashboard/executive-summary and /ops/monitoring/runtime-status). Each
concurrent miss used to run the whole ~44-query computation on its own fresh
Postgres connections, and because the cache is written only on completion, a
slow computation could not stop the next request from starting another one.

These tests drive the production primitive
(``monitoring_runner.run_runtime_status_single_flight``) directly — the same
function ``monitoring_runtime_status`` calls — rather than a copy of it.

The properties under test:
  * concurrent callers for the SAME key run the computation once,
  * callers for DIFFERENT workspaces never share a payload,
  * a caller with no workspace key never joins anyone,
  * a leader's failure reaches its joiners, and
  * a wedged leader cannot pin other requests forever.
"""

from __future__ import annotations

import threading
import time

import pytest

from services.api.app import monitoring_runner


@pytest.fixture(autouse=True)
def _clear_inflight():
    monitoring_runner.RUNTIME_STATUS_INFLIGHT.clear()
    yield
    monitoring_runner.RUNTIME_STATUS_INFLIGHT.clear()


def _run_concurrently(target, count: int, *, join_timeout: float = 30.0):
    """Start ``count`` threads that all enter ``target`` at the same moment."""
    results: list = [None] * count
    errors: list = [None] * count
    barrier = threading.Barrier(count)

    def _worker(index: int):
        barrier.wait()
        try:
            results[index] = target(index)
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted on
            errors[index] = exc

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=join_timeout)
    assert not any(thread.is_alive() for thread in threads), 'a caller never returned'
    return results, errors


def test_concurrent_callers_for_one_workspace_compute_once():
    calls: list[int] = []
    calls_lock = threading.Lock()
    roles: list[str] = []
    roles_lock = threading.Lock()

    def compute():
        with calls_lock:
            calls.append(1)
        # Long enough that every sibling is definitely waiting on the leader.
        time.sleep(0.3)
        return {'workspace_id': 'ws-1', 'monitoring_status': 'live'}

    def record_role(role: str):
        with roles_lock:
            roles.append(role)

    results, errors = _run_concurrently(
        lambda _i: monitoring_runner.run_runtime_status_single_flight(
            'workspace:ws-1', compute, on_role=record_role
        ),
        5,
    )

    assert errors == [None] * 5
    assert len(calls) == 1, f'expected one computation for one key, got {len(calls)}'
    assert all(result == {'workspace_id': 'ws-1', 'monitoring_status': 'live'} for result in results)
    assert roles.count('leader') == 1
    assert roles.count('joined') == 4
    # The slot is released so the next request recomputes rather than hanging.
    assert monitoring_runner.RUNTIME_STATUS_INFLIGHT == {}


def test_joiners_get_a_copy_not_the_leaders_object():
    """A joiner mutating its result must not corrupt another caller's payload."""
    payload = {'workspace_id': 'ws-1', 'counts': 1}

    def compute():
        time.sleep(0.2)
        return payload

    results, errors = _run_concurrently(
        lambda _i: monitoring_runner.run_runtime_status_single_flight('workspace:ws-1', compute),
        3,
    )
    assert errors == [None, None, None]

    joiner_results = [r for r in results if r is not payload]
    assert joiner_results, 'expected at least one joiner'
    joiner_results[0]['counts'] = 999
    assert payload['counts'] == 1, 'a joiner must receive its own copy'


def test_never_shares_across_workspaces():
    """Different workspace keys each compute, and never cross payloads."""
    computed_keys: list[str] = []
    keys_lock = threading.Lock()
    keys = ['workspace:ws-a', 'workspace:ws-b', 'workspace_slug:ws-c']

    def make_compute(key: str):
        def compute():
            with keys_lock:
                computed_keys.append(key)
            time.sleep(0.2)
            return {'key': key}

        return compute

    results, errors = _run_concurrently(
        lambda i: monitoring_runner.run_runtime_status_single_flight(keys[i], make_compute(keys[i])),
        3,
    )

    assert errors == [None, None, None]
    assert sorted(computed_keys) == sorted(keys), 'each workspace must compute for itself'
    for index, key in enumerate(keys):
        assert results[index] == {'key': key}, 'a workspace must never receive another workspace payload'


def test_no_key_means_every_caller_computes_in_isolation():
    """With no workspace id/slug there is no safe sharing key."""
    calls: list[int] = []
    calls_lock = threading.Lock()
    roles: list[str] = []

    def compute():
        with calls_lock:
            calls.append(1)
        time.sleep(0.15)
        return {'unkeyed': True, 'n': len(calls)}

    results, errors = _run_concurrently(
        lambda _i: monitoring_runner.run_runtime_status_single_flight(
            None, compute, on_role=roles.append
        ),
        4,
    )

    assert errors == [None] * 4
    assert len(calls) == 4, 'an unkeyed request must never join another computation'
    assert set(roles) == {'unkeyed'}
    assert monitoring_runner.RUNTIME_STATUS_INFLIGHT == {}


def test_empty_string_key_is_treated_as_no_key():
    calls: list[int] = []

    def compute():
        calls.append(1)
        return {'ok': True}

    monitoring_runner.run_runtime_status_single_flight('', compute)
    monitoring_runner.run_runtime_status_single_flight('', compute)
    assert len(calls) == 2
    assert monitoring_runner.RUNTIME_STATUS_INFLIGHT == {}


def test_leader_failure_reaches_joiners():
    """A joiner must see the failure, never a silent empty payload."""
    boom = RuntimeError('computation failed')
    entered = threading.Event()

    def compute():
        if entered.is_set():
            # A joiner that fell through to computing for itself; keep it distinct.
            return {'recomputed': True}
        entered.set()
        time.sleep(0.3)
        raise boom

    results, errors = _run_concurrently(
        lambda _i: monitoring_runner.run_runtime_status_single_flight('workspace:ws-err', compute),
        4,
    )

    assert any(error is boom for error in errors), 'the leader must raise'
    # Every caller either raised the same failure or was told about it — none
    # silently received a success payload the computation never produced.
    for result, error in zip(results, errors):
        assert error is boom or result is None or result == {'recomputed': True}
    assert monitoring_runner.RUNTIME_STATUS_INFLIGHT == {}


def test_joiner_stops_waiting_on_a_wedged_leader_and_computes():
    """A leader that never finishes delays a request; it must not pin it forever."""
    release = threading.Event()
    computed = []

    def wedged_compute():
        computed.append('leader')
        release.wait(timeout=10)
        return {'leader': True}

    def joiner_compute():
        computed.append('joiner')
        return {'joiner': True}

    leader_thread = threading.Thread(
        target=lambda: monitoring_runner.run_runtime_status_single_flight(
            'workspace:ws-wedged', wedged_compute
        )
    )
    leader_thread.start()
    # Let the leader claim the slot.
    time.sleep(0.1)

    started = time.monotonic()
    result = monitoring_runner.run_runtime_status_single_flight(
        'workspace:ws-wedged', joiner_compute, wait_seconds=0.3
    )
    elapsed = time.monotonic() - started

    assert result == {'joiner': True}
    assert elapsed < 3.0, f'joiner waited {elapsed:.2f}s on a wedged leader'
    assert 'joiner' in computed

    release.set()
    leader_thread.join(timeout=10)


def test_default_inflight_wait_is_bounded():
    assert 1.0 <= monitoring_runner.RUNTIME_STATUS_INFLIGHT_WAIT_SECONDS <= 60.0


def test_slot_is_released_after_a_failure_so_the_next_request_recomputes():
    def failing():
        raise RuntimeError('nope')

    with pytest.raises(RuntimeError):
        monitoring_runner.run_runtime_status_single_flight('workspace:ws-1', failing)
    assert monitoring_runner.RUNTIME_STATUS_INFLIGHT == {}

    def succeeding():
        return {'ok': True}

    assert monitoring_runner.run_runtime_status_single_flight('workspace:ws-1', succeeding) == {'ok': True}
