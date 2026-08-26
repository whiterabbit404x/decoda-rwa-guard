"""The open-alert evidence counter must measure the UNION of both proof chains.

Production (Base Mainnet) reported a fully healthy realtime lane::

    stream_lane=live checkpoint_identity=quicknode:base:live lag_blocks=0
    health_status=healthy chain_head_status=known
    quicknode_stream_coverage_refresh targets_eligible=1 coverage_failed=0
        refresh_interval_seconds=150
    reporting_systems=1 fresh_live_reporting_systems=1 replay_only_systems=0
    status_reason=fresh_coverage_window_300s chosen_evidence_source=live
    realtime_ingestion_status=healthy realtime_live_coverage_fresh=True
    fallback_rpc_degraded=True

…and the workspace rollup still ended at::

    monitoring_runtime_truth status_reason=alerts_without_detection_evidence
    monitoring_runtime_status_summary monitoring_status=limited
    monitoring_runtime_status_decision decision=limited

THE DEFECT
----------
``count_open_alerts`` derived the unprovable remainder as::

    open_alerts_without_evidence_count = MIN(raw - canonical, raw - legacy)
                                       = raw - MAX(canonical, legacy)

That identity only equals the real unprovable count when one proved-set CONTAINS
the other. The two lanes are DISJOINT by construction in the application code:

  * canonical lane — ``create_alert_from_detection_event`` (pilot.py) writes
    ``detection_event_id`` and never ``detection_id``;
  * legacy lanes — ``_upsert_alert`` (monitoring_runner.py, the QuickNode wallet
    transfer path) and ``monitoring_proof_chain`` (pilot.py) write
    ``detection_id`` and never ``detection_event_id``.

So with C canonical-linked and L legacy-linked alerts on DIFFERENT rows, the
arithmetic reports MIN(C, L) alerts as unprovable that are in fact fully proven —
which raises ``alert_without_detection`` and degrades the entire workspace rollup
to ``limited`` through ``contradiction_reason_overrides``.

THE FIX
-------
Count the open alerts backed by NEITHER chain directly, with the same UNION
semantics ``count_open_incidents`` already applies twenty lines below. This is the
counter's own stated intent ("use the most generous count"), not a loosened
threshold: an alert provable by neither chain is still counted, so the
fail-closed rule survives intact.
"""
from __future__ import annotations

import pytest

from services.api.app import monitoring_runner
from services.api.app.monitoring_truth import REALTIME_INGESTION_HEALTHY
from services.api.tests.test_monitoring_status_evidence_integrity_separation import (
    DEGRADED_FALLBACK_HEALTH,
    _AlertEvidenceConn,
    _production_shape,
)
from services.api.tests.test_quicknode_stream_runtime_health_semantics import (  # noqa: F401
    WORKSPACE_ID,
    _Req,
    _runtime_payload,
)

# Data mutations. `CREATE TABLE IF NOT EXISTS` is handled separately: the runtime-status
# path issues idempotent schema-ensures, which change no row.
DATA_MUTATING_VERBS = ('INSERT ', 'UPDATE ', 'DELETE ', 'MERGE ', 'TRUNCATE ', 'DROP ', 'ALTER ')


def _runtime_status_payload(monkeypatch, conn, *, health=None):
    """The FULL runtime-status payload.

    ``_runtime_payload`` unwraps ``workspace_monitoring_summary``; the alert-evidence
    counters live on the top-level payload, so this returns both halves.
    """
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    monkeypatch.setattr(
        monitoring_runner, 'resolve_workspace_context_for_request',
        lambda *a, **k: ({'id': 'u'}, {'workspace_id': WORKSPACE_ID, 'workspace': {'slug': 'ws'}}, True),
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *a, **k: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: conn)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: dict(health or {}))
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    return monitoring_runner.monitoring_runtime_status(_Req())


class _RecordingConn(_AlertEvidenceConn):
    """``_AlertEvidenceConn`` that records every statement the GET path issues."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, q, p=None):
        self.statements.append((' '.join(str(q).split()), tuple(p or ())))
        return super().execute(q, p)

    def evidence_anti_join_statements(self) -> list[tuple[str, tuple]]:
        return [(text, params) for text, params in self.statements if 'AS unprovable_c' in text]


# The production shape, as keyword arguments, for the connections built directly.
_SHAPE = dict(
    stream_checkpoint_age_seconds=2,
    stream_lag_blocks=0,
    stream_telemetry_age_seconds=30,
    rpc_poll_age_seconds=3600,
    coverage_age_seconds=30,
    detection_age_seconds=120,
)


# ---------------------------------------------------------------------------
# THE FIX — split-lane evidence must not manufacture a phantom orphan alert
# ---------------------------------------------------------------------------

def _counters(monkeypatch, **shape):
    """Run the runtime-status GET and return (payload, workspace_monitoring_summary)."""
    payload = _runtime_status_payload(
        monkeypatch, _production_shape(**shape), health=DEGRADED_FALLBACK_HEALTH,
    )
    return payload, payload['workspace_monitoring_summary']


def test_disjoint_canonical_and_legacy_lanes_do_not_manufacture_an_orphan(monkeypatch):
    """Two open alerts, each proven by a DIFFERENT chain, are both proven.

    Old arithmetic: MIN(2 - 1, 2 - 1) == 1 phantom orphan -> limited.
    Union anti-join: |C u L| == 2, remainder 0 -> no contradiction.
    """
    payload, summary = _counters(
        monkeypatch,
        open_alerts=2,
        canonical_evidence_linked_alerts=1,
        legacy_evidence_linked_alerts=1,
    )
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert payload['open_alerts_with_either_detection_chain'] == 2
    # The old arithmetic is still reported alongside, so the delta is visible in prod.
    assert payload['open_alerts_without_detection_evidence_legacy_arithmetic'] == 1
    assert payload['open_alerts_without_detection_evidence_source'] == 'union_anti_join'
    assert 'alert_without_detection' not in payload['contradiction_flags']
    assert 'open_alerts_without_detection_evidence' not in payload['contradiction_flags']
    assert 'alert_without_detection' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'alerts_without_detection_evidence'


@pytest.mark.parametrize(
    ('open_alerts', 'canonical', 'legacy'),
    [(2, 1, 1), (4, 2, 2), (6, 1, 5), (3, 3, 3)],
)
def test_fully_proven_split_lane_workspaces_never_flag(monkeypatch, open_alerts, canonical, legacy):
    payload, summary = _counters(
        monkeypatch,
        open_alerts=open_alerts,
        canonical_evidence_linked_alerts=canonical,
        legacy_evidence_linked_alerts=legacy,
    )
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert 'alert_without_detection' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'alerts_without_detection_evidence'


# ---------------------------------------------------------------------------
# 1-4. The proven Stream/coverage/evidence facts must survive the change
# ---------------------------------------------------------------------------

def test_healthy_stream_coverage_still_selects_live_evidence(monkeypatch):
    _, summary = _counters(
        monkeypatch,
        open_alerts=2,
        canonical_evidence_linked_alerts=1,
        legacy_evidence_linked_alerts=1,
    )
    assert summary['evidence_source'] == 'live'
    assert summary['source_of_evidence'] == 'live'
    assert summary['realtime_ingestion']['status'] == REALTIME_INGESTION_HEALTHY
    assert summary['realtime_ingestion']['healthy'] is True
    assert summary['realtime_ingestion']['live_coverage_fresh'] is True
    assert summary['reporting_systems'] == 1
    assert summary['fresh_live_reporting_systems'] == 1
    assert summary['replay_only_systems'] == 0
    assert summary['reporting_systems_status_reason'].startswith('fresh_coverage_window_')


@pytest.mark.parametrize('rpc_poll_age_seconds', [300, 900, 1800, 3600, None])
def test_fallback_rpc_degradation_never_flips_live_evidence_to_replay(monkeypatch, rpc_poll_age_seconds):
    """The 900s fallback RPC interval is intentional; it must not demote the Stream."""
    _, summary = _counters(
        monkeypatch,
        open_alerts=2,
        canonical_evidence_linked_alerts=1,
        legacy_evidence_linked_alerts=1,
        rpc_poll_age_seconds=rpc_poll_age_seconds,
    )
    assert summary['evidence_source'] == 'live'
    assert summary['replay_only_systems'] == 0
    assert summary['fresh_live_reporting_systems'] == 1
    assert summary['fallback_rpc']['degraded_or_unreachable'] is True


# ---------------------------------------------------------------------------
# 5 & 7. Fail-closed is preserved in both directions
# ---------------------------------------------------------------------------

def test_current_alert_provable_by_neither_chain_still_warns(monkeypatch):
    """A genuinely unprovable open alert must STILL degrade the rollup."""
    payload, summary = _counters(
        monkeypatch,
        open_alerts=1,
        canonical_evidence_linked_alerts=0,
        legacy_evidence_linked_alerts=0,
    )
    assert payload['open_alerts_without_detection_evidence'] == 1
    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert summary['status_reason'] == 'alerts_without_detection_evidence'
    assert summary['monitoring_status'] == 'limited'
    assert 'alert_without_detection' in summary['contradiction_flags']
    # …and the realtime/coverage facts are still reported truthfully alongside it.
    assert summary['evidence_source'] == 'live'
    assert summary['reporting_systems'] == 1
    assert summary['fresh_live_reporting_systems'] == 1


def test_partially_proven_split_lane_reports_only_the_real_remainder(monkeypatch):
    """5 open alerts, 1 canonical + 2 legacy proven -> exactly 2 unprovable, not 4."""
    payload, summary = _counters(
        monkeypatch,
        open_alerts=5,
        canonical_evidence_linked_alerts=1,
        legacy_evidence_linked_alerts=2,
    )
    assert payload['open_alerts_without_detection_evidence'] == 2
    assert payload['open_alerts_with_either_detection_chain'] == 3
    assert payload['open_alerts_without_detection_evidence_legacy_arithmetic'] == 3
    assert summary['status_reason'] == 'alerts_without_detection_evidence'


def test_wallet_transfer_alert_linked_to_its_detection_never_flags(monkeypatch):
    """The proven QuickNode path: _upsert_alert sets detection_id and the detection
    carries raw_evidence_json, so the alert satisfies the LEGACY chain alone."""
    payload, summary = _counters(
        monkeypatch,
        open_alerts=1,
        canonical_evidence_linked_alerts=0,
        legacy_evidence_linked_alerts=1,
    )
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert 'alert_without_detection' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'alerts_without_detection_evidence'


def test_zero_open_alerts_never_fabricates_the_contradiction(monkeypatch):
    payload, summary = _counters(monkeypatch, open_alerts=0)
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert 'alert_without_detection' not in summary['contradiction_flags']


def test_anti_join_query_failure_falls_back_to_the_never_smaller_arithmetic(monkeypatch):
    """Fail closed: a broken counter must not claim every alert is proven."""

    class _BrokenAntiJoinConn(_AlertEvidenceConn):
        def execute(self, q, p=None):
            if 'AS unprovable_c' in ' '.join(str(q).split()):
                raise RuntimeError('relation "detection_evidence" does not exist')
            return super().execute(q, p)

    payload = _runtime_status_payload(
        monkeypatch,
        _BrokenAntiJoinConn(**_SHAPE, open_alerts=2,
                            canonical_evidence_linked_alerts=1,
                            legacy_evidence_linked_alerts=1),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    summary = payload['workspace_monitoring_summary']
    assert payload['open_alerts_without_detection_evidence_source'] == 'legacy_min_arithmetic_fallback'
    assert payload['open_alerts_without_detection_evidence'] == 1
    assert 'alert_without_detection' in summary['contradiction_flags']


# ---------------------------------------------------------------------------
# 8-10. Scope, isolation and read-only guarantees of the counter
# ---------------------------------------------------------------------------

def test_evidence_counter_is_workspace_scoped(monkeypatch):
    conn = _RecordingConn(**_SHAPE, open_alerts=2,
                          canonical_evidence_linked_alerts=1,
                          legacy_evidence_linked_alerts=1)
    _runtime_status_payload(monkeypatch, conn, health=DEGRADED_FALLBACK_HEALTH)

    anti_joins = conn.evidence_anti_join_statements()
    assert anti_joins, 'the union anti-join must run on the runtime-status path'
    for text, params in anti_joins:
        assert 'AND a.workspace_id = %s' in text, 'counter must be workspace-scoped'
        assert WORKSPACE_ID in params
        # Every correlated subquery binds the alert's own workspace — no cross-tenant join.
        assert 'WHERE de.workspace_id = a.workspace_id' in text
        assert 'WHERE d.workspace_id = a.workspace_id' in text
        assert 'WHERE dev.workspace_id = d.workspace_id' in text


def test_evidence_counter_ignores_target_and_system_state(monkeypatch):
    """Disabled or unrelated targets cannot influence evidence integrity.

    The counter is bound by alert STATUS and evidence LINKAGE only. It carries no
    target_id / monitored_system_id / enabled predicate, so runtime target state
    cannot leak into it — the same alert set yields the same count whether the
    workspace's coverage is fresh or stale.
    """
    counts = []
    for coverage_age_seconds in (30, 5000):
        shape = dict(_SHAPE)
        shape['coverage_age_seconds'] = coverage_age_seconds
        conn = _RecordingConn(**shape, open_alerts=3,
                              canonical_evidence_linked_alerts=1,
                              legacy_evidence_linked_alerts=1)
        payload = _runtime_status_payload(monkeypatch, conn, health=DEGRADED_FALLBACK_HEALTH)
        counts.append(payload['open_alerts_without_detection_evidence'])
        for text, _ in conn.evidence_anti_join_statements():
            assert 'target_id' not in text
            assert 'monitored_system_id' not in text
            assert 'enabled' not in text
    assert counts == [1, 1]


def test_runtime_status_get_path_never_mutates_data(monkeypatch):
    conn = _RecordingConn(**_SHAPE, open_alerts=2,
                          canonical_evidence_linked_alerts=1,
                          legacy_evidence_linked_alerts=1)
    _runtime_status_payload(monkeypatch, conn, health=DEGRADED_FALLBACK_HEALTH)

    assert conn.statements, 'the GET path must have issued queries'
    mutations = [
        text for text, _ in conn.statements
        if any(text.upper().lstrip().startswith(verb) for verb in DATA_MUTATING_VERBS)
    ]
    assert mutations == [], f'GET /ops/monitoring/runtime-status must not mutate data: {mutations}'
    # The only DDL the read path issues is an idempotent schema-ensure, which
    # creates no row and rewrites none.
    creates = [text for text, _ in conn.statements if text.upper().lstrip().startswith('CREATE ')]
    for text in creates:
        assert text.upper().lstrip().startswith('CREATE TABLE IF NOT EXISTS'), text
