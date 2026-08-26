"""Every canonical evidence home must count as proof for an open alert.

PRODUCTION (Base Mainnet, read-only lookup). One open alert held the whole
workspace at ``limited``::

    status=investigating severity=high alert_type=asset_monitoring_gap
    module_key=asset_risk source=asset_risk_assessor
    source_service=asset-risk-assessor

    has_threat_detection_evidence=false
    has_asset_risk_evidence=true          <- REAL evidence exists
    has_analysis_run_evidence=false

…producing ``open_alerts_without_detection_evidence=1`` and
``status_reason=alerts_without_detection_evidence``.

THE DEFECT
----------
The runtime's provability predicate recognized two evidence homes:

    1. alerts.detection_event_id -> detection_events -> telemetry_events
    2. alerts.detection_id / detections.linked_alert_id -> detections
       (raw_evidence_json OR detection_evidence)

but the shipped product writes three more, and an alert raised into one of them
structurally NEVER carries a chain detection:

    3. asset_risk_findings.alert_id      -> asset_risk_findings.evidence
    4. threat_detections.linked_alert_id -> threat_detection_evidence
    5. alerts.analysis_run_id            -> analysis_runs.response_payload

So an asset-risk alert with genuine evidence was reported as unprovable. That is
a FALSE POSITIVE, and it degraded the rollup to ``limited`` while realtime
ingestion and coverage were both healthy.

WHAT THESE TESTS LOCK DOWN
--------------------------
Recognizing a lane is NOT the same as trusting a label. Every lane requires an
evidence-BEARING ROW:

  * ``module_key='asset_risk'`` / ``'threat_detection'`` confers nothing — the
    predicate never reads module_key, source, source_service or alert_type.
  * ``asset_risk_findings.evidence``, ``analysis_runs.response_payload`` and
    ``threat_detection_evidence.evidence_payload`` are all
    ``JSONB NOT NULL DEFAULT '{}'::jsonb``, so ``IS NOT NULL`` is true of every
    row. Emptiness, not nullability, is the test.
  * Simulator-sourced threat detections never prove anything (CLAUDE.md:
    simulator data must never be presented as customer evidence).
  * An alert in no lane is STILL counted, and still degrades the rollup.

HOW THE FAKE WORKS
------------------
``_EvidenceHomeConn`` models real evidence ROWS, not booleans, and evaluates a
lane only when the runtime's own emitted SQL actually queries that lane's table
(``_LANE_SQL_MARKERS``). Dropping a lane from
``monitoring_runner.OPEN_ALERT_EVIDENCE_PROVABLE_SQL`` therefore fails these
tests rather than silently passing them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from services.api.app import monitoring_runner
from services.api.app.monitoring_truth import REALTIME_INGESTION_HEALTHY
from services.api.tests.test_monitoring_alerts_evidence_union_anti_join import (
    _SHAPE,
    _runtime_status_payload,
)
from services.api.tests.test_monitoring_status_evidence_integrity_separation import (
    DEGRADED_FALLBACK_HEALTH,
    _AlertEvidenceConn,
    _assert_ingestion_and_coverage_intact,
)
from services.api.tests.test_quicknode_stream_runtime_health_semantics import (  # noqa: F401
    WORKSPACE_ID,
    _Result,
)

OTHER_WORKSPACE_ID = '00000000-0000-0000-0000-0000000000ff'

ALERT_ID = '10000000-0000-0000-0000-000000000001'
RUN_ID = '20000000-0000-0000-0000-000000000001'


# ---------------------------------------------------------------------------
# Modelled evidence rows
# ---------------------------------------------------------------------------

def _carries_real_json(value: Any) -> bool:
    """Python mirror of ``monitoring_runner._non_empty_jsonb_sql``.

    SQL NULL, JSON ``null``, ``{}`` and ``[]`` are all "row exists, carries no
    evidence" — the exact case the ``NOT NULL DEFAULT '{}'::jsonb`` columns make
    the common one.
    """
    return value is not None and value != {} and value != []


@dataclass
class AssetRiskFinding:
    alert_id: str
    evidence: Any
    workspace_id: str = WORKSPACE_ID


@dataclass
class ThreatEvidenceRow:
    telemetry_id: str | None = '30000000-0000-0000-0000-000000000001'
    evidence_payload: Any = field(default_factory=lambda: {'tx_hash': '0xabc'})
    workspace_id: str = WORKSPACE_ID


@dataclass
class ThreatDetection:
    linked_alert_id: str
    evidence_source: str = 'live'
    evidence: list[ThreatEvidenceRow] = field(default_factory=list)
    workspace_id: str = WORKSPACE_ID


@dataclass
class AnalysisRun:
    id: str
    response_payload: Any
    workspace_id: str = WORKSPACE_ID


@dataclass
class Alert:
    """One open alert. ``module_key`` / ``source`` / ``source_service`` are carried
    only to prove the predicate never reads them."""
    id: str = ALERT_ID
    module_key: str = 'strategic_infrastructure_guard'
    source: str = 'monitoring_runner'
    source_service: str = 'api'
    analysis_run_id: str | None = None
    has_canonical_chain: bool = False
    has_legacy_detection_evidence: bool = False
    workspace_id: str = WORKSPACE_ID


# Each lane may prove an alert ONLY when the runtime's emitted SQL queries that
# lane's table. This gate is what makes a dropped lane a test failure.
_LANE_SQL_MARKERS = {
    'canonical_chain': 'FROM detection_events de',
    'legacy_detection': 'FROM detections d',
    'asset_risk': 'FROM asset_risk_findings f',
    'threat_detection': 'FROM threat_detections td',
    'analysis_run': 'FROM analysis_runs ar',
}


class _EvidenceHomeConn(_AlertEvidenceConn):
    """Answers the union anti-join from modelled rows instead of fixed counts."""

    def __init__(
        self,
        *,
        alerts: list[Alert],
        asset_risk_findings: list[AssetRiskFinding] | None = None,
        threat_detections: list[ThreatDetection] | None = None,
        analysis_runs: list[AnalysisRun] | None = None,
        **kwargs,
    ) -> None:
        self.alerts = list(alerts)
        self.asset_risk_findings = list(asset_risk_findings or [])
        self.threat_detections = list(threat_detections or [])
        self.analysis_runs = list(analysis_runs or [])
        self.anti_join_sql: list[str] = []
        self.anti_join_params: list[tuple] = []
        scoped = self._scoped_alerts()
        shape = dict(_SHAPE)
        shape.update(kwargs)
        super().__init__(
            open_alerts=len(scoped),
            # The two chain-lane COUNT queries the rollup still reports alongside.
            canonical_evidence_linked_alerts=sum(1 for a in scoped if a.has_canonical_chain),
            legacy_evidence_linked_alerts=sum(1 for a in scoped if a.has_legacy_detection_evidence),
            **shape,
        )

    def _scoped_alerts(self) -> list[Alert]:
        """The counter is workspace-scoped, so only this workspace's alerts exist."""
        return [a for a in self.alerts if a.workspace_id == WORKSPACE_ID]

    def _lane_facts(self, alert: Alert) -> dict[str, bool]:
        return {
            'canonical_chain': alert.has_canonical_chain,
            'legacy_detection': alert.has_legacy_detection_evidence,
            'asset_risk': any(
                f.workspace_id == alert.workspace_id
                and f.alert_id == alert.id
                and _carries_real_json(f.evidence)
                for f in self.asset_risk_findings
            ),
            'threat_detection': any(
                td.workspace_id == alert.workspace_id
                and td.linked_alert_id == alert.id
                and td.evidence_source != 'simulator'
                and any(
                    ev.workspace_id == td.workspace_id
                    and (ev.telemetry_id is not None or _carries_real_json(ev.evidence_payload))
                    for ev in td.evidence
                )
                for td in self.threat_detections
            ),
            'analysis_run': any(
                alert.analysis_run_id is not None
                and ar.workspace_id == alert.workspace_id
                and ar.id == alert.analysis_run_id
                and _carries_real_json(ar.response_payload)
                for ar in self.analysis_runs
            ),
        }

    def _is_provable(self, alert: Alert, sql: str) -> bool:
        return any(
            fact for lane, fact in self._lane_facts(alert).items()
            if _LANE_SQL_MARKERS[lane] in sql
        )

    def execute(self, q, p=None):
        text = ' '.join(str(q).split())
        if 'AS unprovable_c' in text and 'FROM alerts a' in text:
            self.anti_join_sql.append(text)
            self.anti_join_params.append(tuple(p or ()))
            scoped = self._scoped_alerts()
            provable = sum(1 for alert in scoped if self._is_provable(alert, text))
            return _Result(row={'unprovable_c': len(scoped) - provable, 'provable_c': provable})
        return super().execute(q, p)


def _run(monkeypatch, conn, *, health=None):
    payload = _runtime_status_payload(monkeypatch, conn, health=health or DEGRADED_FALLBACK_HEALTH)
    return payload, payload['workspace_monitoring_summary']


def _assert_provable(payload, summary, *, open_alerts: int = 1) -> None:
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert payload['open_alerts_with_either_detection_chain'] == open_alerts
    assert payload['open_alerts_without_detection_evidence_source'] == 'union_anti_join'
    assert 'alert_without_detection' not in summary['contradiction_flags']
    assert 'open_alerts_without_detection_evidence' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'alerts_without_detection_evidence'


def _assert_unprovable(payload, summary, *, count: int = 1) -> None:
    assert payload['open_alerts_without_detection_evidence'] == count
    assert 'alert_without_detection' in summary['contradiction_flags']
    assert summary['status_reason'] == 'alerts_without_detection_evidence'
    assert summary['monitoring_status'] == 'limited'


# ---------------------------------------------------------------------------
# 1-3. Asset risk — the production lane
# ---------------------------------------------------------------------------

def test_asset_risk_alert_with_real_finding_evidence_is_provable(monkeypatch):
    """The production row: an asset_monitoring_gap alert whose evidence lives in
    asset_risk_findings.evidence is PROVEN, not an orphan."""
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='asset_risk', source='asset_risk_assessor',
                      source_service='asset-risk-assessor')],
        asset_risk_findings=[AssetRiskFinding(
            alert_id=ALERT_ID,
            evidence={'monitored_systems': 0, 'coverage_percent': 0, 'observed_at': '2026-08-26T00:00:00Z'},
        )],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_provable(payload, summary)


@pytest.mark.parametrize(
    ('label', 'findings'),
    [
        ('no_finding_row', []),
        ('empty_object_evidence', [AssetRiskFinding(alert_id=ALERT_ID, evidence={})]),
        ('empty_array_evidence', [AssetRiskFinding(alert_id=ALERT_ID, evidence=[])]),
        ('json_null_evidence', [AssetRiskFinding(alert_id=ALERT_ID, evidence=None)]),
        ('finding_for_a_different_alert',
         [AssetRiskFinding(alert_id='10000000-0000-0000-0000-0000000000aa', evidence={'k': 'v'})]),
    ],
)
def test_asset_risk_alert_without_real_evidence_stays_unprovable(monkeypatch, label, findings):
    """``evidence`` is NOT NULL DEFAULT '{}'::jsonb, so a row existing proves nothing."""
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='asset_risk', source='asset_risk_assessor',
                      source_service='asset-risk-assessor')],
        asset_risk_findings=findings,
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_unprovable(payload, summary)


def test_asset_risk_label_alone_never_confers_provability(monkeypatch):
    """A LABEL IS NOT EVIDENCE: module_key/source/source_service are never read."""
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='asset_risk', source='asset_risk_assessor',
                      source_service='asset-risk-assessor')],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_unprovable(payload, summary)

    sql = conn.anti_join_sql[0]
    for label_column in ('module_key', 'source_service', 'alert_type', 'a.source'):
        assert label_column not in sql, f'{label_column} must not appear in the provability predicate'


def test_asset_risk_lane_requires_non_empty_jsonb_in_the_emitted_sql(monkeypatch):
    """The exact fail-closed guard the production predicate must carry."""
    conn = _EvidenceHomeConn(
        alerts=[Alert()],
        asset_risk_findings=[AssetRiskFinding(alert_id=ALERT_ID, evidence={'k': 'v'})],
    )
    _run(monkeypatch, conn)
    sql = conn.anti_join_sql[0]
    assert 'FROM asset_risk_findings f' in sql
    assert 'AND f.alert_id = a.id' in sql
    assert "f.evidence <> '{}'::jsonb" in sql, 'IS NOT NULL is not sufficient for a NOT NULL DEFAULT column'


# ---------------------------------------------------------------------------
# 4-5. Threat detection
# ---------------------------------------------------------------------------

def test_threat_detection_alert_with_real_evidence_is_provable(monkeypatch):
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='threat_detection', source='threat_detection_engineer',
                      source_service='threat-detection-engineer')],
        threat_detections=[ThreatDetection(linked_alert_id=ALERT_ID, evidence=[ThreatEvidenceRow()])],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_provable(payload, summary)


@pytest.mark.parametrize(
    ('label', 'detections'),
    [
        ('no_detection_row', []),
        ('detection_without_evidence_rows', [ThreatDetection(linked_alert_id=ALERT_ID, evidence=[])]),
        ('evidence_row_with_no_telemetry_and_empty_payload',
         [ThreatDetection(linked_alert_id=ALERT_ID,
                          evidence=[ThreatEvidenceRow(telemetry_id=None, evidence_payload={})])]),
        ('simulator_sourced_detection',
         [ThreatDetection(linked_alert_id=ALERT_ID, evidence_source='simulator',
                          evidence=[ThreatEvidenceRow()])]),
        ('detection_linked_to_a_different_alert',
         [ThreatDetection(linked_alert_id='10000000-0000-0000-0000-0000000000bb',
                          evidence=[ThreatEvidenceRow()])]),
    ],
)
def test_threat_detection_label_without_real_evidence_stays_unprovable(monkeypatch, label, detections):
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='threat_detection', source='threat_detection_engineer',
                      source_service='threat-detection-engineer')],
        threat_detections=detections,
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_unprovable(payload, summary)


def test_threat_detection_lane_excludes_simulator_evidence_in_the_emitted_sql(monkeypatch):
    """CLAUDE.md: simulator data must never be presented as customer evidence."""
    conn = _EvidenceHomeConn(
        alerts=[Alert()],
        threat_detections=[ThreatDetection(linked_alert_id=ALERT_ID, evidence=[ThreatEvidenceRow()])],
    )
    _run(monkeypatch, conn)
    sql = conn.anti_join_sql[0]
    assert 'FROM threat_detections td' in sql
    assert 'AND td.linked_alert_id = a.id' in sql
    assert "td.evidence_source <> 'simulator'" in sql
    assert 'FROM threat_detection_evidence tde' in sql, 'the lane must require an evidence-bearing row'


# ---------------------------------------------------------------------------
# 6-7. Analysis runs
# ---------------------------------------------------------------------------

def test_analysis_run_alert_with_non_empty_response_payload_is_provable(monkeypatch):
    conn = _EvidenceHomeConn(
        alerts=[Alert(analysis_run_id=RUN_ID)],
        analysis_runs=[AnalysisRun(id=RUN_ID, response_payload={'findings': [{'kind': 'reserve_gap'}]})],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_provable(payload, summary)


@pytest.mark.parametrize(
    ('label', 'runs'),
    [
        ('empty_object_payload', [AnalysisRun(id=RUN_ID, response_payload={})]),
        ('empty_array_payload', [AnalysisRun(id=RUN_ID, response_payload=[])]),
        ('json_null_payload', [AnalysisRun(id=RUN_ID, response_payload=None)]),
        ('no_analysis_run_row', []),
    ],
)
def test_analysis_run_alert_without_real_payload_stays_unprovable(monkeypatch, label, runs):
    """``response_payload`` is NOT NULL DEFAULT '{}'::jsonb — emptiness is the test."""
    conn = _EvidenceHomeConn(alerts=[Alert(analysis_run_id=RUN_ID)], analysis_runs=runs)
    payload, summary = _run(monkeypatch, conn)
    _assert_unprovable(payload, summary)


def test_analysis_run_lane_requires_non_empty_payload_in_the_emitted_sql(monkeypatch):
    conn = _EvidenceHomeConn(
        alerts=[Alert(analysis_run_id=RUN_ID)],
        analysis_runs=[AnalysisRun(id=RUN_ID, response_payload={'k': 'v'})],
    )
    _run(monkeypatch, conn)
    sql = conn.anti_join_sql[0]
    assert 'FROM analysis_runs ar' in sql
    assert 'AND ar.id = a.analysis_run_id' in sql or 'WHERE ar.id = a.analysis_run_id' in sql
    assert "ar.response_payload <> '{}'::jsonb" in sql


# ---------------------------------------------------------------------------
# 8-9. The two pre-existing chain lanes must be untouched
# ---------------------------------------------------------------------------

def test_wallet_transfer_legacy_detection_evidence_remains_provable(monkeypatch):
    """_upsert_alert sets detection_id and the detection carries raw_evidence_json."""
    conn = _EvidenceHomeConn(alerts=[Alert(has_legacy_detection_evidence=True)])
    payload, summary = _run(monkeypatch, conn)
    _assert_provable(payload, summary)


def test_canonical_detection_event_to_telemetry_event_remains_provable(monkeypatch):
    """create_alert_from_detection_event sets detection_event_id -> telemetry_event."""
    conn = _EvidenceHomeConn(alerts=[Alert(has_canonical_chain=True)])
    payload, summary = _run(monkeypatch, conn)
    _assert_provable(payload, summary)


def test_mixed_evidence_homes_are_counted_as_one_union(monkeypatch):
    """Five open alerts, one per evidence home, all proven — remainder 0."""
    ids = [f'10000000-0000-0000-0000-00000000000{n}' for n in range(1, 6)]
    conn = _EvidenceHomeConn(
        alerts=[
            Alert(id=ids[0], has_canonical_chain=True),
            Alert(id=ids[1], has_legacy_detection_evidence=True),
            Alert(id=ids[2], module_key='asset_risk'),
            Alert(id=ids[3], module_key='threat_detection'),
            Alert(id=ids[4], analysis_run_id=RUN_ID),
        ],
        asset_risk_findings=[AssetRiskFinding(alert_id=ids[2], evidence={'coverage_percent': 0})],
        threat_detections=[ThreatDetection(linked_alert_id=ids[3], evidence=[ThreatEvidenceRow()])],
        analysis_runs=[AnalysisRun(id=RUN_ID, response_payload={'summary': 'x'})],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_provable(payload, summary, open_alerts=5)


def test_partially_proven_workspace_reports_only_the_real_remainder(monkeypatch):
    """Two proven by new lanes, two proven by nothing -> exactly 2 unprovable."""
    ids = [f'10000000-0000-0000-0000-00000000001{n}' for n in range(1, 5)]
    conn = _EvidenceHomeConn(
        alerts=[
            Alert(id=ids[0], module_key='asset_risk'),
            Alert(id=ids[1], module_key='threat_detection'),
            Alert(id=ids[2], module_key='asset_risk'),
            Alert(id=ids[3], module_key='threat_detection'),
        ],
        asset_risk_findings=[
            AssetRiskFinding(alert_id=ids[0], evidence={'coverage_percent': 0}),
            AssetRiskFinding(alert_id=ids[2], evidence={}),
        ],
        threat_detections=[ThreatDetection(linked_alert_id=ids[1], evidence=[ThreatEvidenceRow()])],
    )
    payload, summary = _run(monkeypatch, conn)
    assert payload['open_alerts_with_either_detection_chain'] == 2
    _assert_unprovable(payload, summary, count=2)


# ---------------------------------------------------------------------------
# 10. Fail-closed: a genuinely evidence-less alert still degrades the rollup
# ---------------------------------------------------------------------------

def test_alert_in_no_evidence_home_still_produces_the_limited_state(monkeypatch):
    conn = _EvidenceHomeConn(alerts=[Alert()])
    payload, summary = _run(monkeypatch, conn)
    _assert_unprovable(payload, summary)
    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert 'proof_chain_link_missing' in summary['contradiction_flags']
    # …and the realtime facts are still reported truthfully alongside the failure.
    _assert_ingestion_and_coverage_intact(summary)


def test_anti_join_failure_still_falls_back_to_the_never_smaller_arithmetic(monkeypatch):
    """A missing optional table (pre-0131/0133 schema) must not claim everything is proven."""

    class _BrokenConn(_EvidenceHomeConn):
        def execute(self, q, p=None):
            text = ' '.join(str(q).split())
            if 'AS unprovable_c' in text:
                raise RuntimeError('relation "asset_risk_findings" does not exist')
            return super().execute(q, p)

    conn = _BrokenConn(
        alerts=[Alert(module_key='asset_risk')],
        asset_risk_findings=[AssetRiskFinding(alert_id=ALERT_ID, evidence={'k': 'v'})],
    )
    payload, summary = _run(monkeypatch, conn)
    assert payload['open_alerts_without_detection_evidence_source'] == 'legacy_min_arithmetic_fallback'
    assert payload['open_alerts_without_detection_evidence'] == 1
    assert payload['open_alerts_with_either_detection_chain'] == 0
    assert 'alert_without_detection' in summary['contradiction_flags']


# ---------------------------------------------------------------------------
# 11. Workspace isolation
# ---------------------------------------------------------------------------

def test_evidence_in_another_workspace_never_proves_this_workspaces_alert(monkeypatch):
    """Cross-tenant evidence must not leak: every lane binds a.workspace_id."""
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='asset_risk')],
        asset_risk_findings=[AssetRiskFinding(
            alert_id=ALERT_ID, evidence={'k': 'v'}, workspace_id=OTHER_WORKSPACE_ID,
        )],
        threat_detections=[ThreatDetection(
            linked_alert_id=ALERT_ID, workspace_id=OTHER_WORKSPACE_ID, evidence=[ThreatEvidenceRow()],
        )],
        analysis_runs=[AnalysisRun(
            id=RUN_ID, response_payload={'k': 'v'}, workspace_id=OTHER_WORKSPACE_ID,
        )],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_unprovable(payload, summary)


def test_every_evidence_lane_is_workspace_scoped_in_the_emitted_sql(monkeypatch):
    conn = _EvidenceHomeConn(alerts=[Alert(has_canonical_chain=True)])
    _run(monkeypatch, conn)

    assert conn.anti_join_sql, 'the union anti-join must run on the runtime-status path'
    for sql, params in zip(conn.anti_join_sql, conn.anti_join_params):
        assert 'AND a.workspace_id = %s' in sql
        assert WORKSPACE_ID in params
        # Every correlated lane binds the alert's own workspace — no cross-tenant join.
        assert 'WHERE de.workspace_id = a.workspace_id' in sql
        assert 'WHERE d.workspace_id = a.workspace_id' in sql
        assert 'WHERE dev.workspace_id = d.workspace_id' in sql
        assert 'WHERE f.workspace_id = a.workspace_id' in sql
        assert 'WHERE td.workspace_id = a.workspace_id' in sql
        assert 'WHERE tde.workspace_id = td.workspace_id' in sql
        assert 'AND ar.workspace_id = a.workspace_id' in sql


def test_the_counter_issues_one_aggregate_pass_not_a_query_per_lane(monkeypatch):
    """Performance: the extra evidence homes cost no additional round trip."""
    conn = _EvidenceHomeConn(alerts=[Alert(module_key='asset_risk')])
    _run(monkeypatch, conn)
    assert len(conn.anti_join_sql) == 1
    for marker in _LANE_SQL_MARKERS.values():
        assert marker in conn.anti_join_sql[0]


# ---------------------------------------------------------------------------
# 12. The healthy realtime Stream / coverage facts are unchanged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('provable', [True, False])
def test_realtime_and_coverage_facts_are_identical_either_way(monkeypatch, provable):
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='asset_risk')],
        asset_risk_findings=(
            [AssetRiskFinding(alert_id=ALERT_ID, evidence={'coverage_percent': 0})] if provable else []
        ),
    )
    _, summary = _run(monkeypatch, conn)
    _assert_ingestion_and_coverage_intact(summary)
    assert summary['realtime_ingestion']['status'] == REALTIME_INGESTION_HEALTHY
    assert summary['reporting_systems'] == 1
    assert summary['fresh_live_reporting_systems'] == 1
    assert summary['replay_only_systems'] == 0
    assert summary['reporting_systems_status_reason'].startswith('fresh_coverage_window_')


def test_provable_asset_risk_alert_clears_every_evidence_contradiction(monkeypatch):
    """The production expectation, derived — not hard-coded.

    ``proof_chain_link_missing`` is raised from its OWN chain count, and a single
    contradiction flag is enough for _normalized_monitoring_status to return
    ``limited``. Recognizing the asset-risk evidence home has to clear that flag
    too, or the workspace stays limited under a different reason token.
    """
    conn = _EvidenceHomeConn(
        alerts=[Alert(module_key='asset_risk', source='asset_risk_assessor',
                      source_service='asset-risk-assessor')],
        asset_risk_findings=[AssetRiskFinding(alert_id=ALERT_ID, evidence={'monitored_systems': 0})],
    )
    payload, summary = _run(monkeypatch, conn)
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert payload['open_alerts_with_either_detection_chain'] == 1
    assert summary['contradiction_flags'] == []
    assert summary['status_reason'] != 'alerts_without_detection_evidence'


# ---------------------------------------------------------------------------
# The shared predicate itself
# ---------------------------------------------------------------------------

def test_predicate_constant_covers_every_canonical_evidence_home():
    predicate = monitoring_runner.OPEN_ALERT_EVIDENCE_PROVABLE_SQL
    for table in ('detection_events', 'detections', 'asset_risk_findings',
                  'threat_detections', 'analysis_runs'):
        assert f'FROM {table} ' in predicate
    # Fail-closed guards, verbatim.
    assert "f.evidence <> '{}'::jsonb" in predicate
    assert "ar.response_payload <> '{}'::jsonb" in predicate
    assert "tde.evidence_payload <> '{}'::jsonb" in predicate
    assert "td.evidence_source <> 'simulator'" in predicate


def test_chain_only_predicate_keeps_the_two_original_lanes_available():
    """The pre-0131/0133 definition stays named, so tooling can say which half it means."""
    chain_only = monitoring_runner.OPEN_ALERT_CHAIN_EVIDENCE_PROVABLE_SQL
    assert 'FROM detection_events ' in chain_only
    assert 'FROM detections ' in chain_only
    assert 'asset_risk_findings' not in chain_only
    assert 'threat_detections' not in chain_only
    assert 'analysis_runs' not in chain_only
    assert chain_only in monitoring_runner.OPEN_ALERT_EVIDENCE_PROVABLE_SQL


@pytest.mark.parametrize(
    ('value', 'expected'),
    [({'k': 'v'}, True), ([1], True), ({}, False), ([], False), (None, False)],
)
def test_non_empty_jsonb_mirror_matches_the_sql_it_documents(value, expected):
    """The fake's Python mirror and the generated SQL must reject the same values."""
    sql = monitoring_runner._non_empty_jsonb_sql('x.evidence')
    assert 'x.evidence IS NOT NULL' in sql
    assert "jsonb_typeof(x.evidence) <> 'null'" in sql
    assert "x.evidence <> '{}'::jsonb" in sql
    assert "x.evidence <> '[]'::jsonb" in sql
    assert _carries_real_json(value) is expected
