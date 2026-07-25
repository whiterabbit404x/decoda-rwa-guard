"""Threat Detection Engineer service layer — clustering, dedup, promotion, and
the idempotent Start Investigation action.

Uses a small stateful in-memory fake DB (the repository's DB-test convention) so
the persistence + idempotency paths are covered without a live Postgres. The fake
models just the two tables the engine writes (threat_detections +
threat_detection_evidence) plus alerts/incidents for the investigation flow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import detectors, scoring, service
from services.api.app.domains.threat_detection.detectors import AssetBaseline, TelemetryEvent

BASE = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeThreatDB:
    """Stateful fake for the two engine tables + alerts/incidents/assets."""

    def __init__(self, *, tables=None, telemetry=None, alerts=None, incidents=None, asset_user_id=None):
        self.tables = set(tables or {'threat_detections', 'threat_detection_evidence', 'threat_detection_checkpoints', 'telemetry_events', 'alerts', 'incidents', 'assets'})
        self.detections = {}   # id -> row
        self.by_cluster = {}   # (workspace_id, cluster_key) -> id
        self.evidence = []     # list of dict rows
        self.checkpoints = {}
        self.alerts = dict(alerts or {})
        self.incidents = dict(incidents or {})
        self.telemetry = list(telemetry or [])
        self.asset_user_id = asset_user_id
        self.writes = []
        self.committed = False

    # -- helpers ---------------------------------------------------------
    def _record(self, q, params):
        self.writes.append((q, params))

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        p = params or ()

        if 'to_regclass' in ql:
            table = str(p[0]).split('.')[-1]
            return _Result([{'ok': table in self.tables}])

        # Checkpoint read
        if 'from threat_detection_checkpoints where workspace_id' in ql and ql.startswith('select'):
            row = self.checkpoints.get(str(p[0]))
            return _Result([row] if row else [])
        if 'insert into threat_detection_checkpoints' in ql:
            self._record(q, params)
            self.checkpoints[str(p[0])] = {'last_processed_at': p[1], 'last_processed_telemetry_id': p[2]}
            return _Result([])

        # Baseline query
        if 'avg(amt) filter' in ql:
            return _Result([])

        # Telemetry scan window
        if 'from telemetry_events' in ql and 'order by observed_at asc' in ql:
            return _Result(self.telemetry)

        # Existing-detection lookup by cluster key
        if 'from threat_detections where workspace_id = %s and cluster_key = %s' in ql:
            did = self.by_cluster.get((str(p[0]), str(p[1])))
            return _Result([self.detections[did]] if did else [])

        # Insert detection
        if 'insert into threat_detections (' in ql:
            self._record(q, params)
            wsid, key = str(p[1]), str(p[2])
            if (wsid, key) not in self.by_cluster:
                did = str(p[0])
                self.detections[did] = {
                    'id': did, 'workspace_id': wsid, 'cluster_key': key, 'detection_type': p[3], 'title': p[4],
                    'chain_id': p[5], 'primary_asset_id': p[6], 'evidence_source': p[7], 'evidence_quality': p[8],
                    'baseline_window': p[9], 'explanation': p[10], 'recommended_next_step': p[11],
                    'severity': 'low', 'confidence': 0, 'status': 'anomaly',
                    'event_count': 0, 'actor_count': 0, 'transaction_count': 0, 'evidence_count': 0,
                    'alert_eligible': False, 'linked_alert_id': None, 'linked_incident_id': None,
                    'first_seen_at': p[13], 'last_seen_at': p[14], 'detected_at': p[15], 'ai_summary': None, 'ai_summary_source': 'deterministic',
                }
                self.by_cluster[(wsid, key)] = did
            return _Result([])

        # Insert evidence (dedup on (detection_id, dedupe_key))
        if 'insert into threat_detection_evidence (' in ql:
            self._record(q, params)
            det_id, dedupe = str(p[2]), str(p[10])
            if not any(e['detection_id'] == det_id and e['dedupe_key'] == dedupe for e in self.evidence):
                self.evidence.append({
                    'id': str(p[0]), 'detection_id': det_id, 'telemetry_id': p[3], 'transaction_hash': p[4],
                    'block_number': p[5], 'actor_address': p[6], 'evidence_type': p[7], 'dedupe_key': dedupe,
                    'observed_at': p[11],
                })
                return _Result([{'id': str(p[0])}])
            return _Result([])

        # Evidence aggregate
        if 'from threat_detection_evidence where detection_id = %s' in ql and 'count(*) as evidence_count' in ql:
            rows = [e for e in self.evidence if e['detection_id'] == str(p[0])]
            actors = {e['actor_address'] for e in rows if e['actor_address']}
            txs = {e['transaction_hash'] for e in rows if e['transaction_hash']}
            times = [e['observed_at'] for e in rows if e['observed_at'] is not None]
            return _Result([{'evidence_count': len(rows), 'actor_count': len(actors), 'tx_count': len(txs),
                             'min_at': min(times) if times else None, 'max_at': max(times) if times else None}])

        # Update detection (scoring result)
        if 'update threat_detections set title = %s' in ql:
            self._record(q, params)
            did = str(p[23])
            row = self.detections.get(did)
            if row is not None:
                row.update({'severity': p[1], 'confidence': p[2], 'status': p[3], 'evidence_source': p[4],
                            'event_count': p[7], 'actor_count': p[8], 'transaction_count': p[9],
                            'evidence_count': p[10], 'alert_eligible': p[14]})
            return _Result([])

        # Detection detail lookups by id (workspace-scoped)
        if 'from threat_detections where id = %s' in ql:
            did = str(p[0])
            row = self.detections.get(did)
            if row is not None and len(p) > 1 and str(row.get('workspace_id')) != str(p[1]):
                row = None
            return _Result([row] if row else [])

        # Incidents / alerts lookups
        if 'from incidents where id = %s' in ql:
            inc = self.incidents.get(str(p[0]))
            return _Result([inc] if inc else [])
        if 'from alerts where id = %s' in ql:
            al = self.alerts.get(str(p[0]))
            return _Result([al] if al else [])

        # Insert/refresh alert
        if 'insert into alerts (' in ql:
            self._record(q, params)
            self.alerts[str(p[0])] = {'id': str(p[0]), 'status': 'open', 'incident_id': None}
            return _Result([])
        if "update threat_detections set linked_alert_id = %s" in ql:
            self._record(q, params)
            row = self.detections.get(str(p[2]))
            if row is not None and row.get('linked_alert_id') is None:
                row['linked_alert_id'] = p[0]
            return _Result([])
        if "update threat_detections set status = 'investigating'" in ql:
            self._record(q, params)
            row = self.detections.get(str(p[2]))
            if row is not None:
                row['status'] = 'investigating'
                row['linked_alert_id'] = p[0]
            return _Result([])

        # Asset owner
        if 'select created_by_user_id from assets' in ql:
            return _Result([{'created_by_user_id': self.asset_user_id}] if self.asset_user_id else [])

        if any(k in ql for k in ('insert', 'update', 'delete')):
            self._record(q, params)
        return _Result([])

    def commit(self):
        self.committed = True


# --------------------------------------------------------------------------
# Candidate builders
# --------------------------------------------------------------------------
def _coordinated_candidate():
    actors = ['0xa', '0xa', '0xb', '0xb', '0xc']
    events = [
        TelemetryEvent(id=f't{i}', workspace_id='ws-1', event_type='wallet_transfer_detected',
                       observed_at=BASE + timedelta(seconds=i * 60), evidence_source='live', asset_id='asset-1',
                       from_address=actors[i], to_address='0xpool', amount=Decimal('5'), tx_hash=f'0xh{i}', chain_id=8453)
        for i in range(5)
    ]
    return detectors.detect_coordinated_activity(events, config=tdc.engine_config())[0]


def _privileged_candidate():
    ev = TelemetryEvent(id='p1', workspace_id='ws-1', event_type='paused', observed_at=BASE,
                        evidence_source='live', asset_id='asset-1', from_address='0xadmin', tx_hash='0xpause', chain_id=8453)
    return detectors.detect_privileged_actions([ev], config=tdc.engine_config())[0]


def _cfg():
    return tdc.engine_config()


# --------------------------------------------------------------------------
# Cluster upsert idempotency
# --------------------------------------------------------------------------
def test_upsert_creates_detection_with_evidence():
    db = FakeThreatDB()
    outcome = service.upsert_candidate(db, workspace_id='ws-1', candidate=_coordinated_candidate(), config=_cfg(), now=BASE)
    assert outcome['created'] is True
    assert len(db.detections) == 1
    assert len([e for e in db.evidence]) == 5
    row = next(iter(db.detections.values()))
    assert row['status'] == 'open'  # coordinated cluster promotes
    assert row['evidence_count'] == 5


def test_reprocessing_same_candidate_is_idempotent():
    db = FakeThreatDB()
    cand = _coordinated_candidate()
    service.upsert_candidate(db, workspace_id='ws-1', candidate=cand, config=_cfg(), now=BASE)
    outcome2 = service.upsert_candidate(db, workspace_id='ws-1', candidate=cand, config=_cfg(), now=BASE)
    assert outcome2['created'] is False
    assert outcome2['evidence_added'] == 0
    assert len(db.detections) == 1
    assert len(db.evidence) == 5  # no duplicate evidence


def test_cluster_update_adds_new_evidence():
    db = FakeThreatDB()
    cand = _coordinated_candidate()
    service.upsert_candidate(db, workspace_id='ws-1', candidate=cand, config=_cfg(), now=BASE)
    # A later related event in the same cluster window adds one evidence item.
    extra = TelemetryEvent(id='t9', workspace_id='ws-1', event_type='wallet_transfer_detected',
                           observed_at=BASE + timedelta(seconds=300), evidence_source='live', asset_id='asset-1',
                           from_address='0xd', to_address='0xpool', amount=Decimal('5'), tx_hash='0xh9', chain_id=8453)
    cand2 = detectors.detect_coordinated_activity(
        [TelemetryEvent(id=f't{i}', workspace_id='ws-1', event_type='wallet_transfer_detected',
                        observed_at=BASE + timedelta(seconds=i * 60), evidence_source='live', asset_id='asset-1',
                        from_address=['0xa', '0xa', '0xb', '0xb', '0xc'][i], to_address='0xpool', amount=Decimal('5'),
                        tx_hash=f'0xh{i}', chain_id=8453) for i in range(5)] + [extra],
        config=_cfg(),
    )[0]
    outcome = service.upsert_candidate(db, workspace_id='ws-1', candidate=cand2, config=_cfg(), now=BASE)
    assert outcome['created'] is False
    assert len(db.detections) == 1
    row = next(iter(db.detections.values()))
    assert row['evidence_count'] == 6


def test_lone_privileged_action_stays_anomaly():
    db = FakeThreatDB()
    outcome = service.upsert_candidate(db, workspace_id='ws-1', candidate=_privileged_candidate(), config=_cfg(), now=BASE)
    # A single admin action is real but not yet a confirmed threat -> anomaly.
    assert outcome['status'] == 'anomaly'
    assert outcome['alert_eligible'] is False


def test_alert_eligibility_requires_live_high_severity_confidence():
    db = FakeThreatDB()
    # Simulator coordinated cluster: promoted but never alert-eligible (not live).
    actors = ['0xa', '0xa', '0xb', '0xb', '0xc']
    events = [TelemetryEvent(id=f's{i}', workspace_id='ws-1', event_type='wallet_transfer_detected',
                             observed_at=BASE + timedelta(seconds=i * 60), evidence_source='simulator', asset_id='asset-1',
                             from_address=actors[i], to_address='0xpool', amount=Decimal('5'), tx_hash=f'0xs{i}') for i in range(5)]
    cand = detectors.detect_coordinated_activity(events, config=_cfg())[0]
    outcome = service.upsert_candidate(db, workspace_id='ws-1', candidate=cand, config=_cfg(), now=BASE)
    assert outcome['alert_eligible'] is False


# --------------------------------------------------------------------------
# process_workspace integration
# --------------------------------------------------------------------------
def _telemetry_rows():
    actors = ['0xa', '0xa', '0xb', '0xb', '0xc']
    return [
        {'id': f't{i}', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': None,
         'event_type': 'wallet_transfer_detected', 'observed_at': BASE + timedelta(seconds=i * 60),
         'evidence_source': 'live', 'payload_json': {'from': actors[i], 'to': '0xpool', 'value': '5', 'tx_hash': f'0xh{i}', 'chain_id': 8453}}
        for i in range(5)
    ]


def test_process_workspace_creates_detection_and_checkpoint():
    db = FakeThreatDB(telemetry=_telemetry_rows())
    stats = service.process_workspace(db, workspace_id='ws-1', config=_cfg(), now=BASE + timedelta(hours=1))
    assert stats['detections_created'] == 1
    assert 'ws-1' in db.checkpoints  # checkpoint advanced


def test_process_workspace_reprocess_no_duplicate():
    rows = _telemetry_rows()
    db = FakeThreatDB(telemetry=rows)
    service.process_workspace(db, workspace_id='ws-1', config=_cfg(), now=BASE + timedelta(hours=1))
    # Simulate a fresh cycle re-scanning the same telemetry (checkpoint cleared).
    db.checkpoints.clear()
    service.process_workspace(db, workspace_id='ws-1', config=_cfg(), now=BASE + timedelta(hours=2))
    assert len(db.detections) == 1
    assert len(db.evidence) == 5


# --------------------------------------------------------------------------
# Start Investigation (idempotent + truthful refusals)
# --------------------------------------------------------------------------
def _open_detection(db, *, status='open', evidence_count=5, linked_alert_id=None, linked_incident_id=None, workspace_id='ws-1'):
    did = 'det-1'
    db.detections[did] = {
        'id': did, 'workspace_id': workspace_id, 'cluster_key': 'ck-1', 'detection_type': 'coordinated_activity',
        'title': 'Coordinated low-and-slow activity', 'explanation': 'x', 'severity': 'medium', 'confidence': 0.6,
        'status': status, 'evidence_source': 'live', 'evidence_quality': 'normalized_telemetry',
        'evidence_count': evidence_count, 'linked_alert_id': linked_alert_id, 'linked_incident_id': linked_incident_id,
        'last_seen_at': BASE,
    }
    return did


def test_investigate_creates_alert_and_marks_investigating():
    db = FakeThreatDB()
    did = _open_detection(db)
    result = service.investigate_detection(db, workspace_id='ws-1', user_id='u-1', detection_id=did, now=BASE)
    assert result['status'] == 'created'
    assert result['alert_id']
    assert db.detections[did]['status'] == 'investigating'
    assert len(db.alerts) == 1


def test_investigate_is_idempotent_returns_existing_alert():
    db = FakeThreatDB(alerts={'alert-x': {'id': 'alert-x', 'status': 'open', 'incident_id': None}})
    did = _open_detection(db, status='investigating', linked_alert_id='alert-x')
    result = service.investigate_detection(db, workspace_id='ws-1', user_id='u-1', detection_id=did, now=BASE)
    assert result['status'] == 'existing_alert'
    assert result['alert_id'] == 'alert-x'
    assert len(db.alerts) == 1  # no duplicate alert


def test_investigate_returns_existing_incident():
    db = FakeThreatDB(incidents={'inc-1': {'id': 'inc-1', 'status': 'open'}})
    did = _open_detection(db, status='investigating', linked_incident_id='inc-1')
    result = service.investigate_detection(db, workspace_id='ws-1', user_id='u-1', detection_id=did, now=BASE)
    assert result['status'] == 'existing_incident'
    assert result['incident_id'] == 'inc-1'
    assert result['destination'] == '/incidents/inc-1'


def test_investigate_refuses_anomaly_with_422():
    db = FakeThreatDB()
    did = _open_detection(db, status='anomaly')
    with pytest.raises(HTTPException) as exc:
        service.investigate_detection(db, workspace_id='ws-1', user_id='u-1', detection_id=did, now=BASE)
    assert exc.value.status_code == 422


def test_investigate_refuses_resolved_with_409():
    db = FakeThreatDB()
    did = _open_detection(db, status='resolved')
    with pytest.raises(HTTPException) as exc:
        service.investigate_detection(db, workspace_id='ws-1', user_id='u-1', detection_id=did, now=BASE)
    assert exc.value.status_code == 409


def test_investigate_workspace_isolation_404():
    db = FakeThreatDB()
    _open_detection(db, workspace_id='ws-1')
    with pytest.raises(HTTPException) as exc:
        service.investigate_detection(db, workspace_id='ws-OTHER', user_id='u-1', detection_id='det-1', now=BASE)
    assert exc.value.status_code == 404
