"""The alert counters that feed the guards must read the CANONICAL five-lane count.

PRODUCTION (Base Mainnet, read-only lookup, Rabbit workspace). One open alert,
five open incidents, every record genuinely provable::

    raw_open_alerts        = 1
    canonical_linked       = 0        <- lane 1 (detection_events -> telemetry_events)
    legacy_linked          = 0        <- lane 2 (detections raw_evidence_json / rows)
    either_chain_linked    = 1        <- the canonical five-lane anti-join
    raw_open_incidents     = 5
    open_incidents_provable= 5
    detections_count       = 47

    the one open alert:  lane3_asset_risk = true, all other lanes false

…and the workspace rollup still ended at::

    status_reason      = guard:incident_exists_without_alert
    monitoring_status  = limited
    next_required_action = open_incident

THE DEFECT
----------
The guard is right; its OPERANDS were stale. Three readers still measured
"provable open alerts" with the pre-lane-3 taxonomy:

    active_alerts_count = max(canonical_linked, legacy_linked)   == 0
    alerts_count        = canonical_linked                       == 0
    payload['active_alerts'] = max(canonical_linked, legacy_linked) == 0

while ``open_alerts_with_either_chain_count``, computed twenty lines above them
from ``proof_chain_sql.OPEN_ALERT_EVIDENCE_PROVABLE_SQL``, already reported 1.
An alert proven through ``asset_risk_findings.evidence`` (lane 3),
``threat_detection_evidence`` (lane 4) or ``analysis_runs.response_payload``
(lane 5) is invisible to both chain joins, so:

  * ``active_incidents_count`` (5, five-lane) sat beside ``active_alerts_count``
    (0, two-lane) and fired ``incident_exists_without_alert`` against a workspace
    whose only open alert was a genuine, provable asset-risk alert; and
  * the runtime setup chain believed no alert existed, leaving ``alert_created``
    blocked and ``next_required_action`` at ``open_incident``.

THE FIX
-------
ONE ``provable_open_alerts_count``, reused by every reader that means provable
open alerts — no second SQL query, no new taxonomy, and the guard untouched::

    provable_open_alerts_count = max(canonical, legacy, either_chain)

WHAT THESE TESTS LOCK DOWN
--------------------------
* All five evidence homes raise the counter, not just the two chain lanes.
* The guard itself is intact: a provable incident with genuinely zero provable
  OPEN alerts still fires ``incident_exists_without_alert``, and an alert in no
  evidence home is still unprovable.
* ``max()`` is the fail-closed fallback in both directions — a failed five-lane
  query drops back to the count the two chain joins actually measured and never
  manufactures a provable alert.
* Tenant isolation: another workspace's evidence never raises this workspace's
  count.
"""
from __future__ import annotations

import pytest

from services.api.app import monitoring_runner
from services.api.tests.test_monitoring_alert_evidence_home_taxonomy import (
    AnalysisRun,
    AssetRiskFinding,
    ThreatDetection,
    ThreatEvidenceRow,
)
from services.api.tests.test_monitoring_incident_proof_chain_provenance import (
    WORKSPACE_A,
    WORKSPACE_B,
    ProofAlert,
    ProofIncident,
    _IncidentProofChainConn,
    _alert_id,
    _incident_id,
    _run,
)
from services.api.tests.test_monitoring_status_evidence_integrity_separation import (
    _assert_ingestion_and_coverage_intact,
)
from services.api.tests.test_quicknode_stream_runtime_health_semantics import _Result

RUN_ID = '20000000-0000-0000-0000-0000000000b1'

# The production detection count, so `detection_created` completes and the setup
# chain actually reaches the `alert_created` step this fix is about.
DETECTIONS_COUNT = 47


class _CounterConn(_IncidentProofChainConn):
    """``_IncidentProofChainConn`` with the pre-alert setup chain already complete.

    ``_RuntimeConn`` answers every unrecognized ``COUNT`` with 0, which parks the
    runtime setup chain at ``asset_verified`` — far upstream of ``alert_created``,
    the step whose operand this file is about. Verified assets and detections are
    modelled here so ``next_required_action`` is genuinely decided by the alert
    counter, exactly as it is in production.

    ``fail_alert_evidence_query`` reproduces a deployment that has not applied the
    optional-table migrations (0131 / 0133), where the five-lane predicate cannot run.
    """

    def __init__(self, *, fail_alert_evidence_query: bool = False, **kwargs) -> None:
        self.fail_alert_evidence_query = bool(fail_alert_evidence_query)
        self.alert_evidence_sql: list[str] = []
        self.alert_evidence_params: list[tuple] = []
        super().__init__(**kwargs)

    def execute(self, q, p=None):
        text = ' '.join(str(q).split())
        if 'AS unprovable_c' in text and 'FROM alerts a' in text:
            if self.fail_alert_evidence_query:
                raise RuntimeError('relation "asset_risk_findings" does not exist')
            self.alert_evidence_sql.append(text)
            self.alert_evidence_params.append(tuple(p or ()))
        if 'FROM assets' in text and 'verification_status' in text:
            return _Result(row={'c': 1})
        if 'COUNT(*) AS c FROM detections' in text:
            return _Result(row={'c': DETECTIONS_COUNT})
        return super().execute(q, p)


def _run_counters(monkeypatch, conn):
    """Run the runtime-status GET and return (payload, summary, setup_chain_counters).

    ``alerts_count`` never reaches the response under its own name — it is an input
    to ``build_runtime_setup_chain``. Capturing the counters dict asserts the operand
    directly rather than inferring it from the step it produces.
    """
    captured: dict = {}
    original = monitoring_runner.build_runtime_setup_chain

    def _spy(*, counters, timestamps):
        captured.update(counters)
        return original(counters=counters, timestamps=timestamps)

    monkeypatch.setattr(monitoring_runner, 'build_runtime_setup_chain', _spy)
    payload, summary = _run(monkeypatch, conn)
    return payload, summary, captured


def _setup_step(summary, step_id: str) -> dict:
    steps = (summary.get('runtime_setup_chain') or {}).get('steps') or []
    return next(step for step in steps if step['id'] == step_id)


def _assert_counted_as_provable(payload, summary, counters, *, count: int = 1) -> None:
    """The three counters must agree, and agree with the canonical anti-join."""
    assert payload['open_alerts_with_either_detection_chain'] == count
    assert summary['active_alerts_count'] == count
    assert counters['alerts_count'] == count
    # The diagnostic payload counter must never disagree with the summary it explains.
    assert payload['active_alerts'] == count
    assert payload['active_alerts_count'] == count


# ---------------------------------------------------------------------------
# 1-3. The production shape: one lane-3 alert, five provable incidents
# ---------------------------------------------------------------------------

def _production_conn(**kwargs) -> _CounterConn:
    """The Rabbit workspace, exactly: one OPEN asset-risk alert proven only by
    ``asset_risk_findings.evidence``, and five open incidents whose provenance runs
    through it and through four resolved alerts carrying raw_evidence_json."""
    return _CounterConn(
        alerts=[
            ProofAlert(id=_alert_id(1), status='open', incident_id=_incident_id(1)),
            *(
                ProofAlert(
                    id=_alert_id(n), status='resolved', incident_id=_incident_id(n),
                    detection_link='detection_id', detection_raw_evidence_json=True,
                )
                for n in range(2, 6)
            ),
        ],
        incidents=[
            ProofIncident(id=_incident_id(n), source_alert_id=_alert_id(n))
            for n in range(1, 6)
        ],
        asset_risk_findings=[
            AssetRiskFinding(alert_id=_alert_id(1), evidence={'monitored_systems': 0, 'coverage_percent': 0}),
        ],
        **kwargs,
    )


def test_lane3_asset_risk_alert_raises_every_provable_open_alert_counter(monkeypatch):
    """REQUIREMENT 1. lane3_asset_risk=true with lane1=lane2=false is provable,
    and all three counters say so — not just the anti-join."""
    payload, summary, counters = _run_counters(monkeypatch, _production_conn())

    # The stale two-lane operands, still reported alongside, are BOTH zero: this is
    # the exact production shape and not an accident of the fake.
    assert payload['raw_open_alerts'] == 1
    assert payload['open_alerts_without_detection_evidence'] == 0
    _assert_counted_as_provable(payload, summary, counters)


def test_five_provable_incidents_with_a_lane3_alert_do_not_fire_the_guard(monkeypatch):
    """REQUIREMENT 2. The false ``incident_exists_without_alert`` disappears once
    the operand stops using the stale taxonomy — with the guard untouched."""
    payload, summary, _ = _run_counters(monkeypatch, _production_conn())

    assert payload['raw_open_incidents'] == 5
    assert summary['active_incidents_count'] == 5
    assert 'incident_exists_without_alert' not in summary['guard_flags']
    assert 'incident_exists_without_alert' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'guard:incident_exists_without_alert'
    # The Stream/coverage/evidence facts this fix must not touch are intact.
    _assert_ingestion_and_coverage_intact(summary)


def test_next_required_action_is_not_open_incident_when_only_lane1_and_lane2_are_zero(monkeypatch):
    """REQUIREMENT 3. ``NEXT_ACTION_BY_STEP['alert_created'] == 'open_incident'``,
    so a zero alerts_count made the banner demand an incident the workspace already
    had five of."""
    _, summary, counters = _run_counters(monkeypatch, _production_conn())

    assert counters['alerts_count'] == 1
    assert _setup_step(summary, 'alert_created')['status'] == 'complete'
    assert _setup_step(summary, 'incident_opened')['status'] == 'complete'
    assert summary['runtime_setup_chain']['current_step'] != 'alert_created'
    assert summary['next_required_action'] != 'open_incident'


# ---------------------------------------------------------------------------
# 4-6. Every evidence home raises the counter
# ---------------------------------------------------------------------------

def test_lane4_threat_detection_only_alert_counts_as_provable(monkeypatch):
    """REQUIREMENT 4. threat_detections.linked_alert_id -> threat_detection_evidence."""
    conn = _CounterConn(
        alerts=[ProofAlert(id=_alert_id(6), status='open')],
        threat_detections=[ThreatDetection(
            linked_alert_id=_alert_id(6), evidence=[ThreatEvidenceRow()],
        )],
    )
    payload, summary, counters = _run_counters(monkeypatch, conn)
    _assert_counted_as_provable(payload, summary, counters)


def test_lane5_analysis_run_only_alert_counts_as_provable(monkeypatch):
    """REQUIREMENT 5. alerts.analysis_run_id -> analysis_runs.response_payload."""
    conn = _CounterConn(
        alerts=[ProofAlert(id=_alert_id(7), status='open', analysis_run_id=RUN_ID)],
        analysis_runs=[AnalysisRun(id=RUN_ID, response_payload={'verdict': 'malicious'})],
    )
    payload, summary, counters = _run_counters(monkeypatch, conn)
    _assert_counted_as_provable(payload, summary, counters)


@pytest.mark.parametrize(
    ('label', 'alert'),
    [
        ('lane1_canonical', ProofAlert(id=_alert_id(8), status='open', has_canonical_chain=True)),
        ('lane2_raw_evidence_json', ProofAlert(
            id=_alert_id(9), status='open',
            detection_link='detection_id', detection_raw_evidence_json=True,
        )),
        ('lane2_linked_alert_id', ProofAlert(
            id=_alert_id(10), status='open',
            detection_link='linked_alert_id', detection_raw_evidence_json=True,
        )),
        ('lane2_detection_evidence_rows', ProofAlert(
            id=_alert_id(11), status='open',
            detection_link='detection_id', detection_evidence_rows=True,
        )),
    ],
)
def test_legacy_lane1_and_lane2_alerts_remain_supported(monkeypatch, label, alert):
    """REQUIREMENT 6. The lanes that already worked must keep working — the fix
    widens the operand, it never replaces one taxonomy with another."""
    payload, summary, counters = _run_counters(monkeypatch, _CounterConn(alerts=[alert]))
    assert payload['open_alerts_with_either_detection_chain'] == 1, label
    _assert_counted_as_provable(payload, summary, counters)


# ---------------------------------------------------------------------------
# 7-8. Fail-closed: the guard and the unprovable verdict both survive
# ---------------------------------------------------------------------------

def test_open_alert_in_no_evidence_home_remains_unprovable(monkeypatch):
    """REQUIREMENT 7. A label is not evidence: an alert matching no lane is still
    counted as unprovable and still degrades the rollup."""
    conn = _CounterConn(alerts=[ProofAlert(id=_alert_id(12), status='open')])
    payload, summary, counters = _run_counters(monkeypatch, conn)

    assert payload['raw_open_alerts'] == 1
    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert payload['open_alerts_without_detection_evidence'] == 1
    assert summary['active_alerts_count'] == 0
    assert counters['alerts_count'] == 0
    assert 'alert_without_detection' in summary['contradiction_flags']
    assert summary['monitoring_status'] == 'limited'


def test_provable_incident_with_zero_provable_open_alerts_still_fires_the_guard(monkeypatch):
    """REQUIREMENT 8. THE GUARD IS UNCHANGED. Incident provenance is historical, so
    an incident proven by a RESOLVED evidence-bearing alert is provable while the
    workspace has no provable OPEN alert at all — and that is exactly the state the
    invariant is for. The fix must not manufacture an alert to silence it."""
    conn = _CounterConn(
        alerts=[ProofAlert(
            id=_alert_id(13), status='resolved', incident_id=_incident_id(13),
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(id=_incident_id(13), source_alert_id=_alert_id(13))],
    )
    payload, summary, counters = _run_counters(monkeypatch, conn)

    assert payload['raw_open_alerts'] == 0
    assert summary['active_alerts_count'] == 0
    assert counters['alerts_count'] == 0
    assert summary['active_incidents_count'] == 1
    assert 'incident_exists_without_alert' in summary['guard_flags']
    assert summary['status_reason'] == 'guard:incident_exists_without_alert'
    assert summary['monitoring_status'] == 'limited'


def test_unprovable_open_alert_never_satisfies_the_guard_for_a_provable_incident(monkeypatch):
    """The same invariant from the other side: open alerts EXIST but none is
    provable, so the counter stays 0 and the guard still fires."""
    conn = _CounterConn(
        alerts=[
            ProofAlert(id=_alert_id(14), status='open'),
            ProofAlert(
                id=_alert_id(15), status='resolved', incident_id=_incident_id(15),
                detection_link='detection_id', detection_raw_evidence_json=True,
            ),
        ],
        incidents=[ProofIncident(id=_incident_id(15), source_alert_id=_alert_id(15))],
    )
    payload, summary, counters = _run_counters(monkeypatch, conn)

    assert payload['raw_open_alerts'] == 1
    assert summary['active_alerts_count'] == 0
    assert counters['alerts_count'] == 0
    assert 'incident_exists_without_alert' in summary['guard_flags']


# ---------------------------------------------------------------------------
# 9. Tenant isolation
# ---------------------------------------------------------------------------

def test_another_workspaces_provable_alert_never_raises_this_workspaces_counter(monkeypatch):
    """REQUIREMENT 9. WORKSPACE_B's open, lane-3-provable alert must not satisfy
    WORKSPACE_A's counter — nor silence WORKSPACE_A's guard."""
    conn = _CounterConn(
        alerts=[
            ProofAlert(id=_alert_id(16), status='open', workspace_id=WORKSPACE_B),
            ProofAlert(
                id=_alert_id(17), status='resolved', incident_id=_incident_id(17),
                detection_link='detection_id', detection_raw_evidence_json=True,
            ),
        ],
        incidents=[ProofIncident(id=_incident_id(17), source_alert_id=_alert_id(17))],
        asset_risk_findings=[AssetRiskFinding(
            alert_id=_alert_id(16), evidence={'k': 'v'}, workspace_id=WORKSPACE_B,
        )],
    )
    payload, summary, counters = _run_counters(monkeypatch, conn)

    assert payload['raw_open_alerts'] == 0
    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert summary['active_alerts_count'] == 0
    assert counters['alerts_count'] == 0
    assert 'incident_exists_without_alert' in summary['guard_flags']


def test_the_counter_reads_a_workspace_scoped_evidence_query(monkeypatch):
    """The count feeding the guard must come from a workspace-bound query."""
    conn = _production_conn()
    _run_counters(monkeypatch, conn)

    assert conn.alert_evidence_sql, 'the five-lane evidence counter must run'
    for sql, params in zip(conn.alert_evidence_sql, conn.alert_evidence_params):
        assert 'AND a.workspace_id = %s' in sql
        assert WORKSPACE_A in params
        assert WORKSPACE_B not in params


# ---------------------------------------------------------------------------
# 10. Query failure stays fail-closed
# ---------------------------------------------------------------------------

def test_evidence_query_failure_never_manufactures_a_provable_alert(monkeypatch):
    """REQUIREMENT 10. The five-lane predicate spans optional tables (migrations
    0131 / 0133). When it cannot run the counter falls back to what the two chain
    joins actually measured — 0 here — never to a value nothing proved."""
    payload, summary, counters = _run_counters(
        monkeypatch, _production_conn(fail_alert_evidence_query=True),
    )

    assert payload['open_alerts_without_detection_evidence_source'] == 'legacy_min_arithmetic_fallback'
    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert summary['active_alerts_count'] == 0
    assert counters['alerts_count'] == 0
    assert payload['active_alerts'] == 0
    # Fail-closed all the way through: the workspace is not claimed healthy.
    assert 'incident_exists_without_alert' in summary['guard_flags']
    assert summary['monitoring_status'] == 'limited'


def test_evidence_query_failure_still_counts_the_chain_lanes_it_can_measure(monkeypatch):
    """max() is the fallback in the OTHER direction too: a failed five-lane query
    must not shrink the count below what the canonical/legacy joins already proved."""
    conn = _CounterConn(
        alerts=[ProofAlert(
            id=_alert_id(18), status='open',
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        fail_alert_evidence_query=True,
    )
    payload, summary, counters = _run_counters(monkeypatch, conn)

    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert summary['active_alerts_count'] == 1
    assert counters['alerts_count'] == 1
    assert payload['active_alerts'] == 1


# ---------------------------------------------------------------------------
# 11. The counters are ONE value — no reader may drift back to a private read model
# ---------------------------------------------------------------------------

def test_the_summary_and_diagnostic_alert_counters_never_disagree(monkeypatch):
    """The defect was three readers answering the same question three ways. Any
    future reader that reintroduces a private count fails here."""
    for conn in (
        _production_conn(),
        _CounterConn(alerts=[ProofAlert(id=_alert_id(19), status='open')]),
        _CounterConn(alerts=[ProofAlert(id=_alert_id(20), status='open', has_canonical_chain=True)]),
    ):
        payload, summary, counters = _run_counters(monkeypatch, conn)
        assert (
            payload['active_alerts']
            == payload['active_alerts_count']
            == summary['active_alerts_count']
            == counters['alerts_count']
        )
