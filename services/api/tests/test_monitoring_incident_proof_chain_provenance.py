"""The open-incident proof chain must ask the canonical evidence question.

PRODUCTION (Base Mainnet, read-only lookup, Rabbit workspace). Five open
incidents, every one of them with a valid same-workspace incident<->alert link
and none of them demo-seeded::

    raw_open_incidents           = 5
    current_chain_open_incidents = 0      <- reported as unprovable
    current_offending_count      = 5

    1  Asset Risk monitoring coverage gap   alert_status=investigating
       has_asset_risk_evidence=true
    2  High-risk threat monitoring          alert_status=resolved
       has_raw_evidence_json=true, has_analysis_run_evidence=true
    3  Wallet transfer                      alert_status=resolved
       has_raw_evidence_json=true
    4  Wallet transfer                      alert_status=resolved
       has_raw_evidence_json=true
    5  Strategic Infrastructure Guard       alert_status=resolved
       has_raw_evidence_json=true

…so ``raw_open_incidents (5) > chain_open_incidents (0)`` raised
``incidents_without_proof_chain_alert`` -> ``proof_chain_link_missing``, and a
single contradiction flag is enough to hold the whole workspace at ``limited``.

THE TWO DEFECTS
---------------
BUG A — STALE EVIDENCE TAXONOMY. The incident query carried its own, older
definition of proof: canonical ``detection_events -> telemetry_events``, plus a
legacy lane that joined ``a.detection_id`` alone (never
``detections.linked_alert_id``) and demanded a ``detection_evidence`` row. So
``detections.raw_evidence_json`` — the canonical home the QuickNode
wallet-transfer path writes, and the one that proves incidents 2/3/4/5 —
counted for nothing, and ``asset_risk_findings.evidence`` (incident 1),
``threat_detection_evidence`` and ``analysis_runs.response_payload`` were
invisible entirely. The alert lane had already been corrected to
``OPEN_ALERT_EVIDENCE_PROVABLE_SQL``; the incident lane had not.

BUG B — STALE LIFECYCLE ASSUMPTION. The query drew its alerts from the
ACTIVE-alert status universe ``('open','acknowledged','investigating')``. That
is right for "how many open alerts can Decoda prove RIGHT NOW" and wrong for
incident provenance, which is HISTORICAL: the product deliberately preserves
escalation provenance after an alert is worked and resolved, so four of the five
production incidents reference a resolved alert. Every such incident looked
orphaned the moment its alert was resolved.

WHAT THESE TESTS LOCK DOWN
--------------------------
* All five canonical evidence homes prove an incident, exactly as they prove an
  alert — one shared predicate, no second taxonomy.
* ``a.status <> 'suppressed'`` is the eligibility rule. Resolved alerts carry
  historical provenance; suppressed alerts never do.
* STATUS ALONE PROVES NOTHING. Eligibility is a filter, not a grant — a resolved
  or investigating alert with no canonical evidence leaves its incident
  unprovable and the rollup degraded.
* All three legitimate linkages count (``alerts.incident_id``,
  ``incidents.source_alert_id``, ``incidents.alert_id``), and every one of them
  is same-workspace.
* Query failure stays fail-closed, and the integrity warning itself is intact:
  a genuinely unprovable open incident still degrades the rollup.

HOW THE FAKE WORKS
------------------
``_IncidentProofChainConn`` models alert and incident ROWS and answers the
incident counter by evaluating the runtime's OWN emitted SQL against them: a
lane counts only when its table is queried, a linkage counts only when its
equality is present, and the status universe is read out of the SQL text. Drop a
lane, drop a linkage, or restore the active-only status filter and these tests
fail rather than silently passing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from services.api.app import monitoring_runner, proof_chain_sql
from services.api.tests.test_monitoring_alert_evidence_home_taxonomy import (
    _LANE_SQL_MARKERS,
    AnalysisRun,
    AssetRiskFinding,
    ThreatDetection,
    ThreatEvidenceRow,
    _carries_real_json,
)
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

WORKSPACE_A = WORKSPACE_ID
WORKSPACE_B = '00000000-0000-0000-0000-0000000000fe'

ACTIVE_ALERT_STATUSES = ('open', 'acknowledged', 'investigating')
ACTIVE_STATUS_SQL = "a.status IN ('open','acknowledged','investigating')"
NON_SUPPRESSED_STATUS_SQL = "a.status <> 'suppressed'"

RUN_ID = '20000000-0000-0000-0000-0000000000a1'


def _alert_id(n: int) -> str:
    return f'10000000-0000-0000-0000-0000000000{n:02d}'


def _incident_id(n: int) -> str:
    return f'40000000-0000-0000-0000-0000000000{n:02d}'


# ---------------------------------------------------------------------------
# Modelled rows
# ---------------------------------------------------------------------------

@dataclass
class ProofAlert:
    """One alert row, with its evidence expressed as rows rather than booleans."""

    id: str
    status: str = 'resolved'
    workspace_id: str = WORKSPACE_A
    # alerts.incident_id — set when an alert is escalated into an incident.
    incident_id: str | None = None
    analysis_run_id: str | None = None
    # Lane 1: alerts.detection_event_id -> detection_events -> telemetry_events.
    has_canonical_chain: bool = False
    # Lane 2: a detections row carrying raw_evidence_json and/or detection_evidence,
    # linked either by alerts.detection_id or by detections.linked_alert_id.
    detection_link: str | None = None  # 'detection_id' | 'linked_alert_id'
    detection_raw_evidence_json: bool = False
    detection_evidence_rows: bool = False


@dataclass
class ProofIncident:
    id: str
    status: str = 'open'
    workspace_id: str = WORKSPACE_A
    # incidents.source_alert_id (migrations 0043/0051/0054/0055).
    source_alert_id: str | None = None
    # incidents.alert_id (migration 0074, FK-aligned to (alert_workspace_id, alert_id)).
    alert_id: str | None = None


@dataclass
class _Model:
    alerts: list[ProofAlert] = field(default_factory=list)
    incidents: list[ProofIncident] = field(default_factory=list)
    asset_risk_findings: list[AssetRiskFinding] = field(default_factory=list)
    threat_detections: list[ThreatDetection] = field(default_factory=list)
    analysis_runs: list[AnalysisRun] = field(default_factory=list)


class _IncidentProofChainConn(_AlertEvidenceConn):
    """Answers the incident proof-chain counter from modelled rows.

    Every decision is driven by the SQL the runtime actually emitted, so this fake
    cannot agree with a predicate the runtime no longer contains.
    """

    def __init__(
        self,
        *,
        alerts: list[ProofAlert] | None = None,
        incidents: list[ProofIncident] | None = None,
        asset_risk_findings: list[AssetRiskFinding] | None = None,
        threat_detections: list[ThreatDetection] | None = None,
        analysis_runs: list[AnalysisRun] | None = None,
        fail_incident_chain_query: bool = False,
        **kwargs,
    ) -> None:
        self.model = _Model(
            alerts=list(alerts or []),
            incidents=list(incidents or []),
            asset_risk_findings=list(asset_risk_findings or []),
            threat_detections=list(threat_detections or []),
            analysis_runs=list(analysis_runs or []),
        )
        self.fail_incident_chain_query = bool(fail_incident_chain_query)
        self.incident_chain_sql: list[str] = []
        self.incident_chain_params: list[tuple] = []
        active = [
            a for a in self.model.alerts
            if a.workspace_id == WORKSPACE_A and a.status in ACTIVE_ALERT_STATUSES
        ]
        shape = dict(_SHAPE)
        shape.update(kwargs)
        super().__init__(
            open_alerts=len(active),
            canonical_evidence_linked_alerts=sum(1 for a in active if a.has_canonical_chain),
            legacy_evidence_linked_alerts=sum(
                1 for a in active
                if a.detection_link and (a.detection_raw_evidence_json or a.detection_evidence_rows)
            ),
            provable_open_alerts=sum(
                1 for a in active if any(self._lane_facts(a).values())
            ),
            **shape,
        )

    # -- evidence -----------------------------------------------------------
    def _legacy_detection_fact(self, alert: ProofAlert, sql: str = '') -> bool:
        """Lane 2, split into the sub-conditions the canonical predicate carries."""
        if alert.detection_link is None:
            return False
        if alert.detection_link == 'linked_alert_id' and sql and 'd.linked_alert_id = a.id' not in sql:
            return False
        if alert.detection_link == 'detection_id' and sql and 'd.id = a.detection_id' not in sql:
            return False
        if alert.detection_raw_evidence_json and (not sql or 'd.raw_evidence_json IS NOT NULL' in sql):
            return True
        if alert.detection_evidence_rows and (not sql or 'FROM detection_evidence dev' in sql):
            return True
        return False

    def _lane_facts(self, alert: ProofAlert, sql: str = '') -> dict[str, bool]:
        return {
            'canonical_chain': alert.has_canonical_chain,
            'legacy_detection': self._legacy_detection_fact(alert, sql),
            'asset_risk': any(
                f.workspace_id == alert.workspace_id
                and f.alert_id == alert.id
                and _carries_real_json(f.evidence)
                for f in self.model.asset_risk_findings
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
                for td in self.model.threat_detections
            ),
            'analysis_run': any(
                alert.analysis_run_id is not None
                and ar.workspace_id == alert.workspace_id
                and ar.id == alert.analysis_run_id
                and _carries_real_json(ar.response_payload)
                for ar in self.model.analysis_runs
            ),
        }

    def _is_provable(self, alert: ProofAlert, sql: str) -> bool:
        facts = self._lane_facts(alert, sql)
        return any(
            fact for lane, fact in facts.items()
            if not sql or _LANE_SQL_MARKERS[lane] in sql
        )

    # -- eligibility / linkage, both read out of the emitted SQL -------------
    @staticmethod
    def _status_eligible(alert: ProofAlert, sql: str) -> bool:
        if NON_SUPPRESSED_STATUS_SQL in sql:
            return alert.status != 'suppressed'
        if ACTIVE_STATUS_SQL in sql:
            return alert.status in ACTIVE_ALERT_STATUSES
        return False  # an unrecognized rule proves nothing

    @staticmethod
    def _linked(alert: ProofAlert, incident: ProofIncident, sql: str) -> bool:
        if 'pca.workspace_id = i.workspace_id' in sql and alert.workspace_id != incident.workspace_id:
            return False
        if 'pca.incident_id = i.id' in sql and alert.incident_id == incident.id:
            return True
        if 'i.source_alert_id = pca.id' in sql and incident.source_alert_id == alert.id:
            return True
        if 'i.alert_id = pca.id' in sql and incident.alert_id == alert.id:
            return True
        return False

    @staticmethod
    def _any_alert_linked(alert: ProofAlert, incident: ProofIncident) -> bool:
        """Linkage only — the ``incident_without_alert`` hard contradiction's question."""
        if alert.workspace_id != incident.workspace_id:
            return False
        return (
            alert.incident_id == incident.id
            or (incident.source_alert_id is not None and incident.source_alert_id == alert.id)
            or (incident.alert_id is not None and incident.alert_id == alert.id)
        )

    # -- counters -----------------------------------------------------------
    def _open_incidents(self) -> list[ProofIncident]:
        return [
            i for i in self.model.incidents
            if i.workspace_id == WORKSPACE_A and i.status in ('open', 'acknowledged')
        ]

    def _chain_open_incidents(self, sql: str) -> int:
        alert_scoped = 'AND a.workspace_id = %s' in sql
        proof_chain_alerts = [
            a for a in self.model.alerts
            if (not alert_scoped or a.workspace_id == WORKSPACE_A)
            and self._status_eligible(a, sql)
            and self._is_provable(a, sql)
        ]
        return sum(
            1 for incident in self._open_incidents()
            if any(self._linked(a, incident, sql) for a in proof_chain_alerts)
        )

    def _incidents_without_alert(self) -> int:
        return sum(
            1 for incident in self._open_incidents()
            if not any(self._any_alert_linked(a, incident) for a in self.model.alerts)
        )

    def execute(self, q, p=None):
        text = ' '.join(str(q).split())

        if 'WITH proof_chain_alerts AS (' in text and 'SELECT COUNT(DISTINCT i.id) AS c' in text:
            if self.fail_incident_chain_query:
                raise RuntimeError('relation "asset_risk_findings" does not exist')
            self.incident_chain_sql.append(text)
            self.incident_chain_params.append(tuple(p or ()))
            return _Result(row={'c': self._chain_open_incidents(text)})
        if "FROM incidents WHERE status IN ('open','acknowledged')" in text:
            return _Result(row={'c': len(self._open_incidents())})
        if 'FROM incidents i' in text and 'NOT EXISTS (' in text and 'FROM alerts a' in text:
            return _Result(row={'c': self._incidents_without_alert()})
        if 'AS unprovable_c' in text and 'FROM alerts a' in text:
            active = [
                a for a in self.model.alerts
                if a.workspace_id == WORKSPACE_A and a.status in ACTIVE_ALERT_STATUSES
            ]
            provable = sum(1 for a in active if self._is_provable(a, text))
            return _Result(row={'unprovable_c': len(active) - provable, 'provable_c': provable})
        return super().execute(q, p)


def _run(monkeypatch, conn):
    payload = _runtime_status_payload(monkeypatch, conn, health=DEGRADED_FALLBACK_HEALTH)
    return payload, payload['workspace_monitoring_summary']


def _assert_incident_chain_proven(payload, summary, *, raw: int) -> None:
    """raw == proven, no offending incidents, and the warning is gone."""
    assert payload['raw_open_incidents'] == raw
    assert payload['open_incidents'] == raw
    assert payload['proof_chain_status'] == 'complete'
    assert 'proof_chain_link_missing' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'incidents_without_proof_chain_alert'


def _assert_incident_chain_unprovable(payload, summary, *, raw: int, proven: int = 0) -> None:
    """The integrity warning survives: a genuinely unprovable incident degrades.

    ``status_reason`` is deliberately not asserted here — the production shape
    runs with a degraded fallback RPC leg, whose own reason outranks the
    proof-chain token. ``proof_chain_link_missing`` is the fact this counter owns.
    """
    assert payload['raw_open_incidents'] == raw
    assert payload['open_incidents'] == proven
    assert payload['proof_chain_status'] == 'incomplete'
    assert 'proof_chain_link_missing' in summary['contradiction_flags']
    assert summary['monitoring_status'] == 'limited'


# ---------------------------------------------------------------------------
# 1-3. The production rows, one per proving evidence home
# ---------------------------------------------------------------------------

def test_open_incident_with_investigating_asset_risk_alert_is_provable(monkeypatch):
    """PRODUCTION INCIDENT 1: asset_monitoring_gap, alert_status=investigating,
    evidence in asset_risk_findings.evidence. Never carries a chain detection."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(1), status='investigating', incident_id=_incident_id(1))],
        incidents=[ProofIncident(id=_incident_id(1), source_alert_id=_alert_id(1))],
        asset_risk_findings=[AssetRiskFinding(
            alert_id=_alert_id(1),
            evidence={'monitored_systems': 0, 'coverage_percent': 0},
        )],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_open_incident_with_resolved_alert_and_raw_evidence_json_is_provable(monkeypatch):
    """PRODUCTION INCIDENTS 3/4/5: wallet transfer / Strategic Infrastructure Guard,
    alert_status=resolved, evidence in detections.raw_evidence_json."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(3), status='resolved', incident_id=_incident_id(3),
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(id=_incident_id(3), source_alert_id=_alert_id(3))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_open_incident_with_resolved_alert_and_analysis_run_payload_is_provable(monkeypatch):
    """PRODUCTION INCIDENT 2: high-risk threat monitoring, alert_status=resolved,
    evidence in analysis_runs.response_payload."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(2), status='resolved', incident_id=_incident_id(2),
            analysis_run_id=RUN_ID,
        )],
        incidents=[ProofIncident(id=_incident_id(2), source_alert_id=_alert_id(2))],
        analysis_runs=[AnalysisRun(id=RUN_ID, response_payload={'findings': [{'kind': 'threat'}]})],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_production_workspace_shape_reports_five_of_five_proven(monkeypatch):
    """THE PRODUCTION EXPECTATION, DERIVED — not hard-coded.

    The five Rabbit incidents exactly as production reports them: one
    investigating asset-risk alert and four resolved threat-monitoring alerts,
    across three different evidence homes. raw=5 must equal proven=5 and the
    offending count must fall to 0 without a single row being edited.
    """
    alerts = [
        ProofAlert(id=_alert_id(1), status='investigating', incident_id=_incident_id(1)),
        ProofAlert(id=_alert_id(2), status='resolved', incident_id=_incident_id(2),
                   analysis_run_id=RUN_ID, detection_link='detection_id',
                   detection_raw_evidence_json=True),
        ProofAlert(id=_alert_id(3), status='resolved', incident_id=_incident_id(3),
                   detection_link='detection_id', detection_raw_evidence_json=True),
        ProofAlert(id=_alert_id(4), status='resolved', incident_id=_incident_id(4),
                   detection_link='detection_id', detection_raw_evidence_json=True),
        ProofAlert(id=_alert_id(5), status='resolved', incident_id=_incident_id(5),
                   detection_link='detection_id', detection_raw_evidence_json=True),
    ]
    conn = _IncidentProofChainConn(
        alerts=alerts,
        incidents=[ProofIncident(id=_incident_id(n), source_alert_id=_alert_id(n))
                   for n in range(1, 6)],
        asset_risk_findings=[AssetRiskFinding(
            alert_id=_alert_id(1), evidence={'monitored_systems': 0, 'coverage_percent': 0},
        )],
        analysis_runs=[AnalysisRun(id=RUN_ID, response_payload={'findings': [{'kind': 'threat'}]})],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=5)
    assert summary['contradiction_flags'] == []
    # …and the healthy realtime/coverage facts are untouched by the incident lane.
    _assert_ingestion_and_coverage_intact(summary)


# ---------------------------------------------------------------------------
# 4-6. Status is a filter, never a grant
# ---------------------------------------------------------------------------

def test_resolved_alert_without_canonical_evidence_leaves_the_incident_unprovable(monkeypatch):
    """FAIL-CLOSED: widening the status universe must not turn 'resolved' into proof."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(6), status='resolved', incident_id=_incident_id(6))],
        incidents=[ProofIncident(id=_incident_id(6), source_alert_id=_alert_id(6))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=1)


def test_suppressed_alert_with_real_evidence_is_excluded_from_provenance(monkeypatch):
    """PRODUCT RULE: a suppressed alert is an explicit operator statement not to act
    on the signal, so it may not carry incident provenance however good its evidence."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(7), status='suppressed', incident_id=_incident_id(7),
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(id=_incident_id(7), source_alert_id=_alert_id(7))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=1)


@pytest.mark.parametrize('status', ['open', 'acknowledged', 'investigating'])
def test_active_alert_with_raw_evidence_json_remains_provable(monkeypatch, status):
    """The pre-existing active-alert cases must keep working unchanged."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(8), status=status, incident_id=_incident_id(8),
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(id=_incident_id(8), source_alert_id=_alert_id(8))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_eligibility_rule_in_the_emitted_sql_is_non_suppressed_not_active_only(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(9), status='resolved', incident_id=_incident_id(9),
                           has_canonical_chain=True)],
        incidents=[ProofIncident(id=_incident_id(9))],
    )
    _run(monkeypatch, conn)
    sql = conn.incident_chain_sql[0]
    assert NON_SUPPRESSED_STATUS_SQL in sql
    assert ACTIVE_STATUS_SQL not in sql, 'incident provenance is historical, not active-only'


# ---------------------------------------------------------------------------
# 7-10. Every canonical evidence home, and the two chain lanes intact
# ---------------------------------------------------------------------------

def test_detection_linked_by_detections_linked_alert_id_is_provable(monkeypatch):
    """The legacy lane must follow detections.linked_alert_id, not detection_id alone."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(10), status='resolved', incident_id=_incident_id(10),
            detection_link='linked_alert_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(id=_incident_id(10))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_detection_evidence_rows_remain_supported(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(11), status='open', incident_id=_incident_id(11),
            detection_link='detection_id', detection_evidence_rows=True,
        )],
        incidents=[ProofIncident(id=_incident_id(11))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_canonical_detection_event_to_telemetry_event_remains_supported(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(12), status='open', incident_id=_incident_id(12),
                           has_canonical_chain=True)],
        incidents=[ProofIncident(id=_incident_id(12))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_threat_detection_evidence_remains_supported(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(13), status='resolved', incident_id=_incident_id(13))],
        incidents=[ProofIncident(id=_incident_id(13))],
        threat_detections=[ThreatDetection(
            linked_alert_id=_alert_id(13), evidence=[ThreatEvidenceRow()],
        )],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_simulator_threat_detection_never_proves_an_incident(monkeypatch):
    """CLAUDE.md: simulator data must never be presented as customer evidence."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(14), status='resolved', incident_id=_incident_id(14))],
        incidents=[ProofIncident(id=_incident_id(14))],
        threat_detections=[ThreatDetection(
            linked_alert_id=_alert_id(14), evidence_source='simulator',
            evidence=[ThreatEvidenceRow()],
        )],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=1)


def test_incident_chain_queries_every_canonical_evidence_home(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(15), has_canonical_chain=True, incident_id=_incident_id(15))],
        incidents=[ProofIncident(id=_incident_id(15))],
    )
    _run(monkeypatch, conn)
    sql = conn.incident_chain_sql[0]
    for marker in _LANE_SQL_MARKERS.values():
        assert marker in sql, f'the incident proof chain must recognize {marker}'
    # The fail-closed guards travel with the shared predicate, verbatim.
    assert "f.evidence <> '{}'::jsonb" in sql
    assert "ar.response_payload <> '{}'::jsonb" in sql
    assert "td.evidence_source <> 'simulator'" in sql


# ---------------------------------------------------------------------------
# 11-13. Linkage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ('label', 'alert_incident_id', 'source_alert_id', 'alert_id'),
    [
        ('alerts_incident_id', _incident_id(16), None, None),
        ('incidents_source_alert_id', None, _alert_id(16), None),
        ('incidents_alert_id', None, None, _alert_id(16)),
    ],
)
def test_every_legitimate_linkage_proves_the_incident(
    monkeypatch, label, alert_incident_id, source_alert_id, alert_id,
):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(16), status='resolved', incident_id=alert_incident_id,
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(
            id=_incident_id(16), source_alert_id=source_alert_id, alert_id=alert_id,
        )],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_incident_with_no_linked_alert_keeps_the_hard_contradiction(monkeypatch):
    """A genuine orphan incident is still an orphan: linkage is what is missing."""
    conn = _IncidentProofChainConn(
        alerts=[],
        incidents=[ProofIncident(id=_incident_id(17))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=1)
    assert 'incident_without_alert' in summary['contradiction_flags']


def test_incident_with_a_linked_alert_but_no_evidence_is_an_evidence_failure_not_an_orphan(monkeypatch):
    """The two failures stay distinguishable: linkage is fine, proof is not."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(18), status='resolved', incident_id=_incident_id(18))],
        incidents=[ProofIncident(id=_incident_id(18))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=1)
    assert 'incident_without_alert' not in summary['contradiction_flags']


# ---------------------------------------------------------------------------
# 14-15. Tenant isolation
# ---------------------------------------------------------------------------

def test_workspace_b_alert_cannot_prove_a_workspace_a_incident(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(19), status='resolved', workspace_id=WORKSPACE_B,
            incident_id=_incident_id(19),
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(id=_incident_id(19), workspace_id=WORKSPACE_A)],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=1)


def test_only_this_workspaces_incidents_are_counted(monkeypatch):
    """Workspace B's open incident is neither raw-counted nor proven here."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(20), status='resolved', incident_id=_incident_id(20),
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[
            ProofIncident(id=_incident_id(20)),
            ProofIncident(id=_incident_id(21), workspace_id=WORKSPACE_B),
        ],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_proven(payload, summary, raw=1)


def test_incident_chain_sql_is_workspace_scoped_on_every_branch(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(22), has_canonical_chain=True, incident_id=_incident_id(22))],
        incidents=[ProofIncident(id=_incident_id(22))],
    )
    _run(monkeypatch, conn)
    assert conn.incident_chain_sql, 'the incident proof chain must run on the runtime-status path'
    for sql, params in zip(conn.incident_chain_sql, conn.incident_chain_params):
        assert 'AND a.workspace_id = %s' in sql
        assert 'AND i.workspace_id = %s' in sql
        assert params == (WORKSPACE_A, WORKSPACE_A)
        # The linkage itself binds the incident's own workspace — no cross-tenant join.
        assert 'pca.workspace_id = i.workspace_id' in sql
        # …and every evidence lane still binds the alert's own workspace.
        assert 'WHERE de.workspace_id = a.workspace_id' in sql
        assert 'WHERE d.workspace_id = a.workspace_id' in sql
        assert 'WHERE f.workspace_id = a.workspace_id' in sql
        assert 'WHERE td.workspace_id = a.workspace_id' in sql
        assert 'AND ar.workspace_id = a.workspace_id' in sql


# ---------------------------------------------------------------------------
# 16-17. Fail-closed
# ---------------------------------------------------------------------------

def test_incident_chain_query_failure_stays_fail_closed(monkeypatch):
    """A missing optional table must never be read as 'every incident is proven'."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(
            id=_alert_id(23), status='resolved', incident_id=_incident_id(23),
            detection_link='detection_id', detection_raw_evidence_json=True,
        )],
        incidents=[ProofIncident(id=_incident_id(23))],
        fail_incident_chain_query=True,
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=1)


def test_partially_proven_incident_set_reports_only_the_real_remainder(monkeypatch):
    """One proven, one not: a provable incident must never vouch for the other."""
    conn = _IncidentProofChainConn(
        alerts=[
            ProofAlert(id=_alert_id(24), status='resolved', incident_id=_incident_id(24),
                       detection_link='detection_id', detection_raw_evidence_json=True),
            ProofAlert(id=_alert_id(25), status='resolved', incident_id=_incident_id(25)),
        ],
        incidents=[ProofIncident(id=_incident_id(24)), ProofIncident(id=_incident_id(25))],
    )
    payload, summary = _run(monkeypatch, conn)
    _assert_incident_chain_unprovable(payload, summary, raw=2, proven=1)


def test_the_integrity_warning_itself_is_preserved(monkeypatch):
    """The policy is correct and must survive the fix: an unprovable open incident
    still degrades the rollup, and never reads as healthy."""
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(26), status='resolved', incident_id=_incident_id(26))],
        incidents=[ProofIncident(id=_incident_id(26))],
    )
    payload, summary = _run(monkeypatch, conn)
    assert summary['monitoring_status'] == 'limited'
    assert summary['monitoring_status'] != 'live'
    assert payload['proof_chain_status'] == 'incomplete'
    # The realtime and coverage facts are still reported truthfully alongside it.
    _assert_ingestion_and_coverage_intact(summary)


def test_incident_counter_issues_one_aggregate_pass(monkeypatch):
    conn = _IncidentProofChainConn(
        alerts=[ProofAlert(id=_alert_id(27), has_canonical_chain=True, incident_id=_incident_id(27))],
        incidents=[ProofIncident(id=_incident_id(27))],
    )
    _run(monkeypatch, conn)
    assert len(conn.incident_chain_sql) == 1


# ---------------------------------------------------------------------------
# 18. The shared definition — runtime and repair tooling cannot drift
# ---------------------------------------------------------------------------

def test_incident_chain_embeds_the_shared_alert_predicate_verbatim():
    sql = proof_chain_sql.incident_proof_chain_count_sql()
    assert proof_chain_sql.OPEN_ALERT_EVIDENCE_PROVABLE_SQL in sql
    assert proof_chain_sql.INCIDENT_PROOF_CHAIN_ELIGIBLE_ALERT_STATUS_SQL in sql
    assert 'WITH proof_chain_alerts AS (' in sql
    assert 'SELECT COUNT(DISTINCT i.id) AS c' in sql


def test_runtime_re_exports_the_shared_definition():
    assert monitoring_runner.OPEN_ALERT_EVIDENCE_PROVABLE_SQL is (
        proof_chain_sql.OPEN_ALERT_EVIDENCE_PROVABLE_SQL
    )
    assert monitoring_runner.incident_proof_chain_count_sql is (
        proof_chain_sql.incident_proof_chain_count_sql
    )


def test_repair_script_uses_the_same_incident_chain_and_a_no_broader_orphan_predicate():
    """MIRROR: the repair/diagnostic script mutates customer rows, so its orphan
    predicate must be the runtime's own definition negated — never broader."""
    from services.api.scripts import repair_live_rpc_proof_chain as repair

    statements: list[str] = []

    class _Conn:
        def execute(self, query, params=()):
            statements.append(' '.join(str(query).split()))

            class _R:
                @staticmethod
                def fetchone():
                    return {'c': 0}
            return _R()

    repair._archive_orphan_alerts(_Conn(), WORKSPACE_A, dry_run=True)
    orphan_sql = statements[0]
    for table in ('detection_events', 'detections', 'asset_risk_findings',
                  'threat_detections', 'analysis_runs'):
        assert f'FROM {table} ' in orphan_sql, (
            f'the orphan-alert predicate must not archive alerts proven via {table}'
        )
    assert repair.OPEN_ALERT_EVIDENCE_PROVABLE_SQL is proof_chain_sql.OPEN_ALERT_EVIDENCE_PROVABLE_SQL
    assert repair.incident_proof_chain_count_sql is proof_chain_sql.incident_proof_chain_count_sql
