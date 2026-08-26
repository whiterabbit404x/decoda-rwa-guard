"""Evidence-integrity contradictions must never overwrite realtime/coverage facts.

Production shape that motivated this file (Base Mainnet, commit bd026f5). The
QuickNode Stream coverage-refresh fix is working exactly as designed::

    quicknode_stream_coverage_refresh health_status=healthy targets_eligible=1
        coverage_refreshed=1 refresh_interval_seconds=150 matched=0
    reporting_systems=1 fresh_live_reporting_systems=1 replay_only_systems=0
    chosen_evidence_source=live source_of_evidence=live
    realtime_ingestion_status=healthy realtime_live_coverage_fresh=True
    fallback_rpc_degraded=True

…yet the workspace rollup still ends at::

    monitoring_runtime_status_decision decision=limited
    status_reason=alerts_without_detection_evidence

That is NOT a regression of the Stream fix. It is a *separate, additive*
proof-chain contradiction:

    monitoring_runner.py  ('alert_without_detection',
                           workspace_configured and open_alerts_without_evidence_count > 0)
        -> contradiction_reason_overrides['alert_without_detection']
             == ('degraded', 'alerts_without_detection_evidence')
        -> contradiction_severity == 'degraded'
        -> runtime_status='degraded', monitoring_status='limited'

``open_alerts_without_evidence_count`` is
``raw_open_alerts - max(canonical_evidence_linked, legacy_evidence_linked)``, where

  * canonical = alerts -> detection_events -> telemetry_events
  * legacy    = alerts -> detections (raw_evidence_json OR detection_evidence)

so an OPEN alert that satisfies neither join is an alert Decoda cannot prove.
Refusing to claim LIVE while such an alert is open is the product's fail-closed
rule (CLAUDE.md: "No alert must not be shown as healthy", "Keep customer-facing
status labels truthful and fail-closed"), not a bug.

What these tests lock down is the SEPARATION the taxonomy depends on — the five
concepts must stay independently readable and must not overwrite one another:

    1. realtime ingestion health      -> summary['realtime_ingestion']
    2. monitoring coverage health     -> reporting_systems / fresh_live_reporting_systems
    3. fallback RPC health            -> summary['fallback_rpc']
    4. historical evidence integrity  -> contradiction_flags / status_reason
    5. current security alert state   -> active_alerts_count

A workspace may legitimately be (1) healthy, (2) healthy, (3) degraded and
(4) failing at the same time. The evidence-integrity failure must degrade the
workspace ROLLUP only — it must never rewrite the ingestion or coverage facts to
say the Stream stopped, that nothing is reporting, or that evidence is replay.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from services.api.app.monitoring_truth import REALTIME_INGESTION_HEALTHY
from services.api.tests.test_quicknode_stream_runtime_health_semantics import (  # noqa: F401
    DEFAULT_HEALTH,
    NOW,
    SYSTEM_ID,
    _clear_runtime_status_caches,
    _Result,
    _RuntimeConn,
    _runtime_payload,
)

# Fallback RPC leg reported degraded, exactly as production does, WITHOUT touching
# the Stream: this is the `fallback_rpc_degraded=True` half of the production line.
DEGRADED_FALLBACK_HEALTH = {
    **DEFAULT_HEALTH,
    'source_type': 'unavailable',
    'degraded_reason': 'all_rpc_providers_unavailable',
    'last_error': 'rpc timeout',
}


class _AlertEvidenceConn(_RuntimeConn):
    """``_RuntimeConn`` plus the three alert-evidence counters the rollup reads.

    ``_RuntimeConn`` answers every ``COUNT(...)`` with 0, i.e. a workspace with no
    alerts at all. This subclass models the alert lane explicitly so a test can put
    an open alert in the workspace and choose whether it is provable.
    """

    def __init__(
        self,
        *,
        open_alerts: int = 0,
        canonical_evidence_linked_alerts: int = 0,
        legacy_evidence_linked_alerts: int = 0,
        provable_open_alerts: int | None = None,
        detection_age_seconds: int | None = 120,
        coverage_age_seconds: int | None = 30,
        **kwargs,
    ) -> None:
        super().__init__(target_coverage_age_seconds=coverage_age_seconds, **kwargs)
        self.open_alerts = int(open_alerts)
        self.canonical_evidence_linked_alerts = int(canonical_evidence_linked_alerts)
        self.legacy_evidence_linked_alerts = int(legacy_evidence_linked_alerts)
        # |canonical set UNION legacy set| — what the runtime's union anti-join actually
        # measures. The two lanes are DISJOINT by construction in the application code
        # (create_alert_from_detection_event sets detection_event_id and never detection_id;
        # _upsert_alert / monitoring_proof_chain set detection_id and never
        # detection_event_id), so the honest default is the disjoint union, capped at the
        # number of open alerts. Pass provable_open_alerts explicitly to model an overlap.
        self.provable_open_alerts = int(
            min(self.open_alerts, self.canonical_evidence_linked_alerts + self.legacy_evidence_linked_alerts)
            if provable_open_alerts is None
            else provable_open_alerts
        )
        self.detection_at = (
            None if detection_age_seconds is None else NOW - timedelta(seconds=detection_age_seconds)
        )
        self.coverage_at = (
            None if coverage_age_seconds is None else NOW - timedelta(seconds=coverage_age_seconds)
        )

    def execute(self, q, p=None):
        text = ' '.join(str(q).split())

        # Union anti-join: open alerts backed by NEITHER proof chain. This is the counter
        # the rollup actually consumes; it must be answered from the UNION of the two
        # lanes, never from raw - MAX(canonical, legacy).
        if 'AS unprovable_c' in text and 'FROM alerts a' in text:
            return _Result(row={
                'unprovable_c': max(self.open_alerts - self.provable_open_alerts, 0),
                'provable_c': self.provable_open_alerts,
            })
        # raw_open_alerts_count — every open/acknowledged/investigating alert.
        if (
            "FROM alerts WHERE status IN ('open','acknowledged','investigating')" in text
            and 'FROM alerts a' not in text
        ):
            return _Result(row={'c': self.open_alerts})
        # Canonical proof chain: alerts -> detection_events -> telemetry_events.
        if (
            'FROM alerts a' in text
            and 'JOIN detection_events de' in text
            and 'JOIN telemetry_events te' in text
            and 'COUNT' in text
        ):
            return _Result(row={'c': self.canonical_evidence_linked_alerts})
        # Legacy proof chain: alerts -> detections (raw_evidence_json / detection_evidence).
        if 'FROM alerts a' in text and 'JOIN detections d' in text and 'raw_evidence_json IS NOT NULL' in text:
            return _Result(row={'c': self.legacy_evidence_linked_alerts})
        # Detections exist in the workspace (production does have them — otherwise the
        # session-13 HARD guard `alert_exists_without_detection` would have fired and
        # forced evidence_source to 'none', which production did not report).
        if 'FROM detection_events' in text and 'MAX(' in text:
            return _Result(row={'ts': self.detection_at})
        if 'AS detected_at' in text and 'FROM alerts a' in text and 'JOIN detection_events de' in text:
            return _Result(row={'detected_at': self.detection_at})
        # Fresh live coverage receipts written by the Stream coverage refresh.
        if 'WITH filtered_receipts AS (' in text:
            if self.coverage_at is None:
                return _Result(rows=[])
            return _Result(rows=[{
                'monitored_system_id': SYSTEM_ID,
                'latest_processed_at': self.coverage_at,
                'receipt_count': 20,
                'workspace_latest_processed_at': self.coverage_at,
                'workspace_receipt_count': 20,
            }])
        return super().execute(q, p)


def _production_shape(**overrides):
    """Healthy near-tip Stream + fresh coverage + a degraded 900s fallback RPC leg."""
    params = dict(
        stream_checkpoint_age_seconds=2,
        stream_lag_blocks=0,
        stream_telemetry_age_seconds=30,
        rpc_poll_age_seconds=3600,
        coverage_age_seconds=30,
        detection_age_seconds=120,
    )
    params.update(overrides)
    return _AlertEvidenceConn(**params)


def _assert_ingestion_and_coverage_intact(summary) -> None:
    """The PROVEN FIX: these facts must be identical with or without an orphan alert."""
    assert summary['realtime_ingestion']['healthy'] is True
    assert summary['realtime_ingestion']['status'] == REALTIME_INGESTION_HEALTHY
    assert summary['realtime_ingestion']['live_coverage_fresh'] is True
    assert summary['evidence_source'] == 'live'
    assert summary['source_of_evidence'] == 'live'
    assert summary['reporting_systems'] == 1
    assert summary['fresh_live_reporting_systems'] == 1
    assert summary['replay_only_systems'] == 0
    # The fallback leg is degraded, and stays reported under its own name.
    assert summary['fallback_rpc']['degraded_or_unreachable'] is True
    assert summary['provider_degraded_flag'] is False


# ---------------------------------------------------------------------------
# 1. The production state reproduces exactly
# ---------------------------------------------------------------------------

def test_orphan_open_alert_reproduces_the_production_limited_decision(monkeypatch):
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(open_alerts=1, canonical_evidence_linked_alerts=0, legacy_evidence_linked_alerts=0),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    assert summary['status_reason'] == 'alerts_without_detection_evidence'
    assert summary['monitoring_status'] == 'limited'
    assert summary['runtime_status'] == 'degraded'
    assert 'alert_without_detection' in summary['contradiction_flags']
    assert 'open_alerts_without_detection_evidence' in summary['contradiction_flags']


def test_orphan_open_alert_does_not_rewrite_ingestion_or_coverage_facts(monkeypatch):
    """The core separation: concept 4 must not overwrite concepts 1, 2 or 3."""
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(open_alerts=1),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    _assert_ingestion_and_coverage_intact(summary)


def test_ingestion_and_coverage_facts_are_identical_with_and_without_the_orphan_alert(monkeypatch):
    clean = _runtime_payload(
        monkeypatch, _production_shape(open_alerts=0), health=DEGRADED_FALLBACK_HEALTH,
    )
    orphaned = _runtime_payload(
        monkeypatch, _production_shape(open_alerts=1), health=DEGRADED_FALLBACK_HEALTH,
    )
    tracked = (
        'evidence_source', 'source_of_evidence', 'reporting_systems',
        'fresh_live_reporting_systems', 'replay_only_systems', 'provider_degraded_flag',
    )
    for key in tracked:
        assert clean[key] == orphaned[key], key
    assert clean['realtime_ingestion']['status'] == orphaned['realtime_ingestion']['status']
    assert clean['realtime_ingestion']['healthy'] == orphaned['realtime_ingestion']['healthy']
    # Only the integrity dimension differs.
    assert 'alert_without_detection' not in clean['contradiction_flags']
    assert 'alert_without_detection' in orphaned['contradiction_flags']


# ---------------------------------------------------------------------------
# 2. Fallback RPC degradation never flips evidence away from live
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('rpc_poll_age_seconds', [300, 900, 1800, 3600, None])
def test_fallback_rpc_degradation_never_changes_evidence_source_from_live(
    monkeypatch, rpc_poll_age_seconds,
):
    """Holds WITH the evidence-integrity contradiction present — the two are
    independent, so neither may be used to justify downgrading the other."""
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(open_alerts=1, rpc_poll_age_seconds=rpc_poll_age_seconds),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    assert summary['evidence_source'] == 'live', rpc_poll_age_seconds
    assert summary['source_of_evidence'] == 'live', rpc_poll_age_seconds
    assert summary['reporting_systems'] == 1, rpc_poll_age_seconds
    assert summary['fresh_live_reporting_systems'] == 1, rpc_poll_age_seconds
    assert summary['replay_only_systems'] == 0, rpc_poll_age_seconds
    assert summary['realtime_ingestion']['healthy'] is True, rpc_poll_age_seconds


# ---------------------------------------------------------------------------
# 3. Legitimate integrity outcomes keep their semantics
# ---------------------------------------------------------------------------

def test_canonical_evidence_linked_alert_clears_the_integrity_contradiction(monkeypatch):
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(open_alerts=1, canonical_evidence_linked_alerts=1),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    assert 'alert_without_detection' not in summary['contradiction_flags']
    assert 'open_alerts_without_detection_evidence' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'alerts_without_detection_evidence'
    _assert_ingestion_and_coverage_intact(summary)


def test_legacy_evidence_linked_alert_alone_clears_the_integrity_contradiction(monkeypatch):
    """``min(canonical_gap, legacy_gap)`` — a smoke-rule alert carrying
    raw_evidence_json is provable even with no detection_event_id."""
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(
            open_alerts=1, canonical_evidence_linked_alerts=0, legacy_evidence_linked_alerts=1,
        ),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    assert 'alert_without_detection' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'alerts_without_detection_evidence'


def test_partially_provable_alert_set_still_reports_the_unprovable_remainder(monkeypatch):
    """Three open alerts, two provable: the one that is not must still be reported.
    A resolvable subset must never be allowed to vouch for the whole set."""
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(open_alerts=3, canonical_evidence_linked_alerts=2),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    assert 'alert_without_detection' in summary['contradiction_flags']
    assert summary['status_reason'] == 'alerts_without_detection_evidence'
    assert summary['monitoring_status'] == 'limited'


def test_workspace_is_never_live_while_an_unprovable_open_alert_exists(monkeypatch):
    """Fail-closed rule: healthy ingestion may not buy a LIVE claim over an alert
    Decoda cannot prove. This is the property that makes `limited` correct here."""
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(open_alerts=1),
        health={**DEFAULT_HEALTH},  # fallback RPC healthy too — still not LIVE
    )
    assert summary['monitoring_status'] != 'live'
    assert summary['runtime_status'] != 'live'
    assert summary['status_reason'] == 'alerts_without_detection_evidence'


def test_zero_alert_workspace_never_fabricates_an_integrity_contradiction(monkeypatch):
    """A clean, zero-threat workspace must not be degraded for missing evidence
    it was never supposed to have."""
    summary = _runtime_payload(
        monkeypatch, _production_shape(open_alerts=0), health=DEGRADED_FALLBACK_HEALTH,
    )
    assert 'alert_without_detection' not in summary['contradiction_flags']
    assert 'open_alerts_without_detection_evidence' not in summary['contradiction_flags']
    assert summary['status_reason'] != 'alerts_without_detection_evidence'
    _assert_ingestion_and_coverage_intact(summary)


# ---------------------------------------------------------------------------
# 4. The integrity contradiction is age-blind (documents the current semantics)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('detection_age_seconds', [120, 86_400, 30 * 86_400, 365 * 86_400])
def test_integrity_contradiction_is_independent_of_record_age(monkeypatch, detection_age_seconds):
    """DOCUMENTED SEMANTICS, not an accident: the counter is bounded by alert
    *status* (open / acknowledged / investigating), never by ``created_at``. An
    unprovable alert that is still OPEN is an unresolved integrity failure however
    old it is — closing or resolving it is what clears the flag, not the passage of
    time. Any future change that adds a time bound here MUST update this test and
    the taxonomy note in
    docs/monitoring_limited_alerts_without_detection_evidence_diagnostics.sql.
    """
    summary = _runtime_payload(
        monkeypatch,
        _production_shape(open_alerts=1, detection_age_seconds=detection_age_seconds),
        health=DEGRADED_FALLBACK_HEALTH,
    )
    assert summary['status_reason'] == 'alerts_without_detection_evidence'
    assert summary['monitoring_status'] == 'limited'
    _assert_ingestion_and_coverage_intact(summary)
