"""Canonical threat-monitoring summary — count consistency, honest data-state
labels, and the strict GET-is-read-only guarantee.

Uses a configurable read-only fake (returns canned rows per query) so the summary
composition is tested without a live Postgres. The key invariants: zero !=
unavailable != no_telemetry, a trend is only emitted with a valid comparison
base, resolved detections are not active, and building the summary performs NO
writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import summary as tds


def _now():
    return datetime.now(timezone.utc)


class SummaryFakeDB:
    def __init__(self, *, tables=None, telemetry=None, detections=None, by_type=None, mttd=None,
                 top=None, watchlist=None, coverage=None, worker=None, volume=None):
        self.tables = set(tables if tables is not None else {'threat_detections', 'threat_detection_evidence', 'telemetry_events', 'threat_detection_worker_state'})
        self.telemetry = telemetry or {'current_count': 0, 'previous_count': 0, 'last_at': None, 'live_count': 0, 'sim_count': 0, 'replay_count': 0}
        self.detections = detections or {'detection_count': 0, 'previous_detection_count': 0, 'anomaly_count': 0, 'previous_anomaly_count': 0}
        self.by_type = by_type or []
        self.mttd = mttd if mttd is not None else {'mttd': None}
        self.top = top
        self.watchlist = watchlist or []
        self.coverage = coverage or {'active_total': 0, 'live_active': 0, 'non_live_active': 0}
        self.worker = worker or {'hb': None, 'done': None}
        self.volume = volume or []
        self.writes = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).lower()
        p = params or ()
        if 'to_regclass' in q:
            table = str(p[0]).split('.')[-1]
            return _R([{'ok': table in self.tables}])
        if 'max(heartbeat_at) as hb' in q:
            return _R([self.worker])
        if 'current_count' in q and 'telemetry_events' in q:
            return _R([self.telemetry])
        if 'floor(extract(epoch from observed_at)' in q:
            return _R(self.volume)
        if 'as detection_count' in q and 'as anomaly_count' in q:
            return _R([self.detections])
        if 'select detection_type, count(*) as count' in q:
            return _R(self.by_type)
        if 'as mttd' in q:
            return _R([self.mttd])
        if 'select * from threat_detections where workspace_id = %s and status = any(%s) order by' in q:
            return _R([self.top] if self.top else [])
        if 'transaction_hash is not null' in q:
            return _R([{'transaction_hash': '0xabc'}] if self.top else [])
        if 'actor_address is not null' in q and 'from threat_detection_evidence' in q and 'join' not in q:
            return _R([{'actor_address': '0xactor'}] if self.top else [])
        if 'select name from assets where id = %s' in q:
            return _R([{'name': 'US Treasury Bond #123'}])
        if 'from threat_detection_evidence e join threat_detections td' in q:
            return _R(self.watchlist)
        if 'as active_total' in q:
            return _R([self.coverage])
        if any(k in q for k in ('insert', 'update', 'delete')):
            self.writes.append((q, params))
        return _R([])

    def commit(self):
        self.writes.append(('commit', None))


class _R:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _build(db):
    return tds.build_threat_summary(db, workspace_id='ws-1', config=tdc.engine_config(), window_days=7)


# --------------------------------------------------------------------------
# Empty workspace
# --------------------------------------------------------------------------
def test_empty_workspace_is_honest():
    s = _build(SummaryFakeDB())
    assert s['telemetry_events_count'] == 0
    assert s['detection_count'] == 0
    assert s['anomaly_count'] == 0
    assert s['mean_time_to_detect_seconds'] is None
    assert s['top_detection'] is None
    assert s['data_freshness'] == 'no_telemetry'
    assert s['telemetry_change_percent'] is None
    assert s['engine_panel']['state'] == 'no_telemetry'
    assert 'no_telemetry' in s['degraded_reasons']


def test_unavailable_distinct_from_no_telemetry():
    # telemetry_events table missing -> unavailable, not "no telemetry / zero".
    s = _build(SummaryFakeDB(tables={'threat_detections'}))
    assert s['data_freshness'] == 'unavailable'
    assert s['engine_panel']['state'] == 'unavailable'


# --------------------------------------------------------------------------
# Telemetry present, no detections
# --------------------------------------------------------------------------
def test_telemetry_without_detections():
    db = SummaryFakeDB(telemetry={'current_count': 100, 'previous_count': 50, 'last_at': _now() - timedelta(seconds=60), 'live_count': 100, 'sim_count': 0, 'replay_count': 0})
    s = _build(db)
    assert s['telemetry_events_count'] == 100
    assert s['telemetry_change_percent'] == 100.0  # (100-50)/50
    assert s['detection_count'] == 0
    assert s['data_freshness'] == 'fresh'
    assert s['engine_panel']['state'] == 'no_material_threat'


def test_change_percent_none_without_comparison_base():
    db = SummaryFakeDB(telemetry={'current_count': 100, 'previous_count': 0, 'last_at': _now() - timedelta(seconds=60), 'live_count': 100, 'sim_count': 0, 'replay_count': 0})
    s = _build(db)
    assert s['telemetry_change_percent'] is None  # never fabricate +Inf / +100% from 0


# --------------------------------------------------------------------------
# Populated
# --------------------------------------------------------------------------
def _top_row():
    return {
        'id': 'det-1', 'detection_type': 'coordinated_activity', 'title': 'Coordinated low-and-slow activity',
        'ai_summary': 'Repeated low-value transfers accumulated above baseline.', 'explanation': 'x',
        'severity': 'high', 'confidence': 0.72, 'status': 'open', 'primary_asset_id': 'asset-1', 'chain_id': 8453,
        'first_seen_at': _now() - timedelta(hours=2), 'last_seen_at': _now() - timedelta(minutes=5), 'detected_at': _now() - timedelta(minutes=5),
        'event_count': 8, 'evidence_count': 8, 'evidence_quality': 'normalized_telemetry', 'evidence_source': 'live',
        'recommended_next_step': 'Investigate the related address cluster.', 'alert_eligible': True,
        'ai_summary_source': 'deterministic', 'linked_alert_id': None, 'linked_incident_id': None,
    }


def test_populated_overview_counts_and_top():
    db = SummaryFakeDB(
        detections={'detection_count': 3, 'previous_detection_count': 1, 'anomaly_count': 2, 'previous_anomaly_count': 0},
        by_type=[{'detection_type': 'coordinated_activity', 'count': 2}, {'detection_type': 'unusual_transfer', 'count': 1}],
        mttd={'mttd': 12.5}, top=_top_row(),
        telemetry={'current_count': 500, 'previous_count': 400, 'last_at': _now() - timedelta(seconds=30), 'live_count': 500, 'sim_count': 0, 'replay_count': 0},
        watchlist=[{'actor_address': '0xactor', 'detection_id': 'det-1', 'detection_type': 'coordinated_activity', 'title': 't', 'confidence': 0.7, 'status': 'open', 'severity': 'high', 'primary_asset_id': 'asset-1', 'evidence_source': 'live', 'first_seen': _now(), 'last_seen': _now(), 'hits': 3}],
        coverage={'active_total': 3, 'live_active': 3, 'non_live_active': 0},
    )
    s = _build(db)
    assert s['detection_count'] == 3
    assert s['detection_change_percent'] == 200.0
    assert s['anomaly_count'] == 2
    assert s['anomaly_change_percent'] is None  # previous anomaly 0
    assert s['mean_time_to_detect_seconds'] == 12.5
    assert s['top_detection'] is not None
    assert s['top_detection']['detection_type'] == 'coordinated_activity'
    assert s['engine_panel']['state'] == 'active_detection'
    assert len(s['active_watchlist_matches']) == 1


def test_detections_by_type_count_consistency():
    db = SummaryFakeDB(
        detections={'detection_count': 3, 'previous_detection_count': 0, 'anomaly_count': 0, 'previous_anomaly_count': 0},
        by_type=[{'detection_type': 'coordinated_activity', 'count': 2}, {'detection_type': 'unusual_transfer', 'count': 1}],
    )
    s = _build(db)
    supported_sum = sum(row['count'] for row in s['detections_by_type'])
    assert supported_sum == s['detection_count']  # by-type sum == canonical count


def test_detector_support_marks_unsupported_types():
    s = _build(SummaryFakeDB())
    by_type = {row['type']: row for row in s['detections_by_type']}
    assert by_type['reentrancy_pattern']['supported'] is False
    assert by_type['reentrancy_pattern']['unsupported_reason']
    assert by_type['unusual_transfer']['supported'] is True


# --------------------------------------------------------------------------
# Degraded / stale
# --------------------------------------------------------------------------
def test_stale_telemetry_degraded_state():
    db = SummaryFakeDB(telemetry={'current_count': 100, 'previous_count': 100, 'last_at': _now() - timedelta(hours=2), 'live_count': 100, 'sim_count': 0, 'replay_count': 0})
    s = _build(db)
    assert s['data_freshness'] == 'stale'
    assert 'telemetry_stale' in s['degraded_reasons']
    assert s['engine_panel']['state'] == 'telemetry_stale'


def test_detection_storage_provisioning_degraded():
    db = SummaryFakeDB(tables={'telemetry_events', 'threat_detection_worker_state'},
                       telemetry={'current_count': 10, 'previous_count': 0, 'last_at': _now() - timedelta(seconds=30), 'live_count': 10, 'sim_count': 0, 'replay_count': 0})
    s = _build(db)
    assert 'detection_storage_provisioning' in s['degraded_reasons']
    assert s['detection_count'] == 0


# --------------------------------------------------------------------------
# GET is strictly read-only
# --------------------------------------------------------------------------
def test_building_summary_performs_no_writes():
    db = SummaryFakeDB(
        detections={'detection_count': 3, 'previous_detection_count': 1, 'anomaly_count': 2, 'previous_anomaly_count': 0},
        by_type=[{'detection_type': 'coordinated_activity', 'count': 3}], top=_top_row(),
        telemetry={'current_count': 500, 'previous_count': 400, 'last_at': _now() - timedelta(seconds=30), 'live_count': 500, 'sim_count': 0, 'replay_count': 0},
    )
    _build(db)
    assert db.writes == []  # opening/refreshing Screen 5 never writes
