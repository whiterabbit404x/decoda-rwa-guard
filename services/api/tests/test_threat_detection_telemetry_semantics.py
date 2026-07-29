"""Screen 5 canonical telemetry semantics — the fixes for the tab-window /
telemetry-truthfulness audit.

Covers:
  * one canonical selected window (24h / 7d / 30d + safe fallback) shared by the
    summary and the list endpoints;
  * security telemetry vs ingestion/runtime telemetry (rpc_polling heartbeats are
    NOT on-chain security events and never enter counts, the default table, or the
    detectors);
  * duplicate raw/derived representations of one transaction are not double-counted;
  * live evidence mode can still be stale (source != freshness);
  * canonical worker status (a 71h heartbeat is never "stable") + next-action
    priority (zero detections never yields open_incident; stale ingestion yields
    diagnose_ingestion; an existing investigation yields open_investigation);
  * GET builds perform NO writes.

pilot's Postgres is not required: pure predicates are tested directly, and the
composition/SQL-shape is exercised with a query-recording fake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import detectors
from services.api.app.domains.threat_detection import summary as tds


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# 1-4. Canonical window resolution (shared + safe fallback for invalid input)
# --------------------------------------------------------------------------
def test_window_presets_resolve_to_expected_seconds():
    assert tdc.resolve_window('24h')['seconds'] == 24 * 3600
    assert tdc.resolve_window('7d')['seconds'] == 7 * 24 * 3600
    assert tdc.resolve_window('30d')['seconds'] == 30 * 24 * 3600
    assert tdc.resolve_window('24h')['key'] == '24h'


def test_invalid_window_falls_back_to_seven_days():
    assert tdc.resolve_window('bogus')['key'] == '7d'
    assert tdc.resolve_window('')['key'] == '7d'
    assert tdc.resolve_window(None)['key'] == '7d'
    # A legacy window_days value still maps to a preset key.
    assert tdc.resolve_window(None, 1)['key'] == '24h'
    assert tdc.resolve_window(None, 30)['key'] == '30d'
    assert tdc.resolve_window(None, 'nonsense')['key'] == '7d'


def test_summary_and_list_share_one_window_source():
    # The summary and the detections list resolve the SAME canonical window, so a
    # KPI and its table can never disagree on the period.
    for w in ('24h', '7d', '30d'):
        assert tds.tdc.resolve_window(w)['seconds'] == tdc.resolve_window(w)['seconds']


# --------------------------------------------------------------------------
# 5-7. Security vs runtime/ingestion telemetry predicate
# --------------------------------------------------------------------------
def test_rpc_polling_is_runtime_not_security():
    assert tdc.is_runtime_event_type('rpc_polling') is True
    assert tdc.is_security_event_type('rpc_polling') is False
    for et in ('live_provider', 'poll_heartbeat', 'provider_check', 'cursor_update', 'worker_heartbeat', 'diagnostic_result'):
        assert tdc.is_runtime_event_type(et) is True, et
        assert tdc.is_security_event_type(et) is False, et


def test_transfer_and_privileged_events_are_security():
    for et in ('native_transfer', 'wallet_transfer_detected', 'erc20_transfer', 'ownership_transferred', 'role_granted'):
        assert tdc.is_security_event_type(et) is True, et
        assert tdc.is_runtime_event_type(et) is False, et


def test_unknown_event_type_is_not_counted_as_security():
    # Fail-closed: an empty/unknown type never inflates the security count.
    assert tdc.is_security_event_type('') is False
    assert tdc.is_security_event_type(None) is False


def test_summary_count_query_excludes_runtime_and_dedups_transactions():
    db = _RecordingSummaryDB()
    tds.build_threat_summary(db, workspace_id='ws-1', config=tdc.engine_config(), window='7d')
    count_sql = _find(db.queries, 'current_count', 'security_events')
    assert count_sql is not None, 'security telemetry count query not found'
    # Runtime rows are excluded, and a transaction is de-duplicated (distinct canon).
    assert 'lower(event_type) <> all(%s)' in count_sql
    assert 'count(distinct canon)' in count_sql


def test_telemetry_list_defaults_to_security_only_and_dedups():
    from services.api.app.domains.threat_detection import endpoints

    conn, _req, patch = _telemetry_conn()
    with patch():
        endpoints.telemetry_endpoint(_req, limit=10, offset=0)
    # The row-listing query is the one using DISTINCT ON (canonical de-duplication).
    list_sql = _find(conn.queries, 'distinct on', 'from telemetry_events te')
    assert list_sql is not None, 'deduplicated telemetry list query not found'
    # Default category is security: runtime event types are excluded.
    assert 'lower(te.event_type) <> all(%s)' in list_sql
    # Total reflects the deduplicated canonical count.
    count_sql = _find(conn.queries, 'count(*) as n', 'canonical_events')
    assert count_sql is not None


def test_runtime_category_returns_only_runtime_rows_for_ingestion_health():
    from services.api.app.domains.threat_detection import endpoints

    conn, _req, patch = _telemetry_conn()
    with patch():
        endpoints.telemetry_endpoint(_req, category='runtime', limit=10, offset=0)
    list_sql = _find(conn.queries, 'from telemetry_events te')
    assert 'lower(te.event_type) = any(%s)' in list_sql


def test_detector_scan_window_excludes_runtime_rows():
    from services.api.app.domains.threat_detection import service

    conn = _ScanConn()
    service._load_scan_window(conn, workspace_id='ws-1', config=tdc.engine_config(), now=_now(),
                              checkpoint={'last_processed_at': None, 'last_processed_telemetry_id': None})
    scan_sql = _find(conn.queries, 'from telemetry_events')
    assert 'lower(event_type) <> all(%s)' in scan_sql


# --------------------------------------------------------------------------
# 6. One rpc heartbeat + five transactions -> five security events (detectors)
# --------------------------------------------------------------------------
def test_runtime_heartbeat_never_enters_detector_evaluation():
    now = _now()
    events = [
        detectors.TelemetryEvent(id='hb', workspace_id='ws-1', event_type='rpc_polling', observed_at=now,
                                 evidence_source='live'),
    ]
    for i in range(5):
        events.append(detectors.TelemetryEvent(
            id=f'tx-{i}', workspace_id='ws-1', event_type='native_transfer', observed_at=now,
            evidence_source='live', asset_id='asset-1', from_address='0xsource', to_address=f'0xdest{i}',
            tx_hash=f'0xhash{i}', amount=None,
        ))
    transfers = [e for e in events if e.event_type in tdc.TRANSFER_EVENT_TYPES]
    privileged = [e for e in events if e.event_type in tdc.PRIVILEGED_EVENT_TYPES]
    # The rpc_polling heartbeat is neither a transfer nor a privileged event.
    assert len(transfers) == 5
    assert len(privileged) == 0
    assert all(e.event_type != 'rpc_polling' for e in transfers)


# --------------------------------------------------------------------------
# 8. Duplicate raw/derived representation of one transaction -> one canonical key
# --------------------------------------------------------------------------
def test_duplicate_transaction_representations_share_one_canonical_key():
    # native_transfer (raw) + wallet_transfer_detected (derived) for the SAME tx
    # collapse onto one canonical key, so the KPI/table never double-count them.
    assert "payload_json->>'tx_hash'" in tdc.TELEMETRY_TX_HASH_SQL
    raw = {'payload_json': {'tx_hash': '0xdup'}}
    derived = {'payload_json': {'transaction_hash': '0xdup'}}

    def canon(row):
        p = row['payload_json']
        return p.get('tx_hash') or p.get('transaction_hash') or p.get('hash')

    assert canon(raw) == canon(derived) == '0xdup'


# --------------------------------------------------------------------------
# 9. Live evidence mode can still be stale (source != freshness)
# --------------------------------------------------------------------------
def test_live_evidence_mode_can_be_stale():
    from services.api.app.domains.threat_detection import endpoints

    now = _now()
    old = now - timedelta(hours=89)
    # A row captured live 89h ago is stale today; the evidence MODE is still live.
    assert endpoints._row_freshness(old, now, 900) == 'stale'
    assert endpoints._row_freshness(now, now, 900) == 'fresh'
    assert endpoints._row_freshness(None, now, 900) == 'unknown'
    # Ingestion source and evidence mode are independent fields.
    assert endpoints._ingestion_source('evm_rpc', 'rpc_polling') == 'rpc_polling'
    assert endpoints._ingestion_source('quicknode', 'native_transfer') == 'quicknode_stream'


# --------------------------------------------------------------------------
# 10. A stale heartbeat is never "stable"/healthy
# --------------------------------------------------------------------------
def test_stale_heartbeat_is_not_stable():
    now = _now()
    cfg = tdc.engine_config()
    seventy_one_hours = now - timedelta(hours=71)
    assert tdc.classify_worker_status(last_ingestion_at=seventy_one_hours, now=now, config=cfg, worker_enabled=True) == 'stale'
    assert tdc.classify_worker_status(last_ingestion_at=now, now=now, config=cfg) == 'healthy'
    assert tdc.classify_worker_status(last_ingestion_at=None, now=now, config=cfg, worker_enabled=True) == 'offline'
    assert tdc.classify_worker_status(last_ingestion_at=None, now=now, config=cfg, worker_enabled=False) == 'unknown'


# --------------------------------------------------------------------------
# 11-14. Next-action priority ladder (never open_incident)
# --------------------------------------------------------------------------
def test_zero_detections_stale_yields_diagnose_ingestion_never_open_incident():
    action = tds._next_action(data_freshness='stale', worker_status='stale', top=None, counts={'detection_count': 0})
    assert action == 'diagnose_ingestion'
    assert action != 'open_incident'


def test_fresh_open_detection_yields_start_investigation():
    top = {'status': 'open', 'evidence_count': 3}
    action = tds._next_action(data_freshness='fresh', worker_status='healthy', top=top, counts={'detection_count': 1})
    assert action == 'start_investigation'


def test_existing_investigation_yields_open_investigation():
    top = {'status': 'investigating', 'evidence_count': 3}
    action = tds._next_action(data_freshness='fresh', worker_status='healthy', top=top, counts={'detection_count': 1})
    assert action == 'open_investigation'


def test_healthy_no_threat_yields_no_action():
    action = tds._next_action(data_freshness='fresh', worker_status='healthy', top=None, counts={'detection_count': 0})
    assert action == 'none'


def test_next_action_never_open_incident_across_states():
    for freshness in ('fresh', 'stale', 'no_telemetry', 'unavailable'):
        for ws in ('healthy', 'degraded', 'stale', 'offline', 'unknown'):
            for top in (None, {'status': 'open', 'evidence_count': 1}, {'status': 'investigating', 'evidence_count': 1}):
                assert tds._next_action(data_freshness=freshness, worker_status=ws, top=top, counts={'detection_count': 0}) != 'open_incident'


# --------------------------------------------------------------------------
# Summary composition: stale telemetry -> diagnose_ingestion + stale worker
# --------------------------------------------------------------------------
def test_summary_stale_telemetry_recommends_diagnose_ingestion():
    now = _now()
    stale_at = now - timedelta(hours=89)
    db = _RecordingSummaryDB(last_security_at=stale_at, last_ingestion_at=now - timedelta(hours=71))
    s = db_build(db)
    assert s['data_freshness'] == 'stale'
    assert s['worker_status'] == 'stale'
    assert s['next_action'] == 'diagnose_ingestion'
    assert s['engine_panel']['recommended_action'] == 'diagnose_ingestion'
    # Panel finding/explanation are the truthful stale copy.
    assert s['engine_panel']['finding'] == 'No fresh evidence available'
    assert 'fresh on-chain security telemetry is unavailable' in s['engine_panel']['explanation']
    # can_investigate must be false with no detection.
    assert s['engine_panel']['can_investigate'] is False


def test_summary_window_key_is_echoed():
    db = _RecordingSummaryDB()
    assert db_build(db, window='24h')['window'] == '24h'
    assert db_build(db, window='30d')['window'] == '30d'
    assert db_build(db, window='bogus')['window'] == '7d'


# --------------------------------------------------------------------------
# Canonical empty-state reason: "no data in the selected window" is DISTINCT
# from "no data ever" (the top banner reports a non-windowed telemetry age).
# --------------------------------------------------------------------------
def test_empty_state_no_telemetry_ever():
    # Nothing in-window, nothing ever, no ingestion heartbeat -> a truly empty workspace.
    db = _RecordingSummaryDB(last_security_at=None, last_ingestion_at=None, last_security_ever=None)
    s = db_build(db)
    assert s['data_freshness'] == 'no_telemetry'
    assert s['empty_state_reason'] == 'no_telemetry_ever'
    assert s['last_security_telemetry_ever_at'] is None
    assert s['engine_panel']['finding'] == 'No telemetry received'


def test_empty_state_no_security_telemetry_in_window_when_older_exists():
    # Nothing in the selected window, but security telemetry exists OUTSIDE it.
    now = _now()
    db = _RecordingSummaryDB(last_security_at=None, last_ingestion_at=None, last_security_ever=now - timedelta(days=40))
    s = db_build(db)
    assert s['data_freshness'] == 'no_telemetry'
    assert s['empty_state_reason'] == 'no_security_telemetry_in_window'
    assert s['last_security_telemetry_ever_at'] is not None
    # Must NOT claim "no telemetry has arrived yet" when older telemetry exists.
    assert 'has arrived yet' not in s['engine_panel']['headline'].lower()
    assert s['engine_panel']['finding'] == 'No security telemetry in the selected period'


def test_empty_state_runtime_only_telemetry():
    # No security telemetry ever, but ingestion heartbeats exist (worker reporting).
    now = _now()
    db = _RecordingSummaryDB(last_security_at=None, last_ingestion_at=now - timedelta(hours=1), last_security_ever=None)
    s = db_build(db)
    assert s['data_freshness'] == 'no_telemetry'
    assert s['empty_state_reason'] == 'runtime_only_telemetry'


def test_engine_panel_copy_is_not_repeated():
    # headline / finding / explanation must be three DISTINCT sentences (no refrain).
    now = _now()
    db = _RecordingSummaryDB(last_security_at=None, last_ingestion_at=None, last_security_ever=now - timedelta(days=40))
    panel = db_build(db)['engine_panel']
    assert len({panel['headline'], panel['finding'], panel['explanation']}) == 3


def test_engine_panel_exposes_historical_telemetry_and_ingestion_heartbeat():
    # Deployment shape: nothing in-window, but security telemetry exists ~40d ago and
    # the ingestion (poll) heartbeat is ~147h old. The panel must surface BOTH — never
    # collapse to "—" while the summary already carries these canonical timestamps.
    now = _now()
    ever = now - timedelta(days=40)
    poll = now - timedelta(hours=147)
    db = _RecordingSummaryDB(last_security_at=None, last_ingestion_at=poll, last_security_ever=ever)
    s = db_build(db)
    fields = s['engine_panel']['fields']
    # Latest security telemetry falls back to the newest-ever timestamp (not None).
    assert fields['last_security_telemetry_at'] is not None
    assert fields['last_security_telemetry_at'] == s['last_security_telemetry_ever_at']
    # Worker heartbeat falls back to the canonical ingestion signal that drives the
    # stale worker_status, so the panel age matches the global runtime strip.
    assert fields['worker_heartbeat_at'] is not None
    assert fields['worker_heartbeat_at'] == s['ingestion_health']['last_ingestion_at']


def test_degraded_reason_distinguishes_in_window_from_ever():
    now = _now()
    # (a) Older telemetry exists outside the window → in-window reason, NOT "no_telemetry".
    older = _RecordingSummaryDB(last_security_at=None, last_ingestion_at=None, last_security_ever=now - timedelta(days=40))
    s_older = db_build(older)
    assert 'no_security_telemetry_in_window' in s_older['degraded_reasons']
    assert 'no_telemetry' not in s_older['degraded_reasons']
    # (b) Never any telemetry → the honest "no telemetry ever" reason is used.
    never = _RecordingSummaryDB(last_security_at=None, last_ingestion_at=None, last_security_ever=None)
    s_never = db_build(never)
    assert 'no_telemetry' in s_never['degraded_reasons']
    assert 'no_security_telemetry_in_window' not in s_never['degraded_reasons']


# --------------------------------------------------------------------------
# 15-16. GET builds / telemetry list perform NO writes
# --------------------------------------------------------------------------
def test_summary_build_performs_no_writes():
    db = _RecordingSummaryDB()
    db_build(db)
    assert db.writes == [], f'summary GET must not write, saw: {db.writes}'


def test_telemetry_endpoint_performs_no_writes():
    from services.api.app.domains.threat_detection import endpoints

    conn, _req, patch = _telemetry_conn()
    with patch():
        endpoints.telemetry_endpoint(_req, limit=10, offset=0)
    assert conn.writes == []


# --------------------------------------------------------------------------
# 18. Workspace isolation — telemetry list binds workspace_id first
# --------------------------------------------------------------------------
def test_telemetry_list_is_workspace_scoped():
    from services.api.app.domains.threat_detection import endpoints

    conn, _req, patch = _telemetry_conn(workspace='ws-42')
    with patch():
        endpoints.telemetry_endpoint(_req, limit=10, offset=0)
    assert conn.select_params is not None
    assert conn.select_params[0] == 'ws-42'


# --------------------------------------------------------------------------
# 20. Shared RuntimeBanner canonical selector regression: zero detections must
#     not produce open_incident from the workspace runtime setup chain.
# --------------------------------------------------------------------------
def test_workspace_next_action_zero_detections_is_not_open_incident():
    from services.api.app import workspace_monitoring_summary as wms

    chain = wms.build_runtime_setup_chain(
        counters={
            'workspaces_count': 1, 'assets_count': 1, 'verified_assets_count': 1,
            'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1,
            'detections_count': 0, 'alerts_count': 0, 'incidents_count': 0,
            'response_actions_count': 0, 'evidence_count': 0,
        },
        timestamps={'last_heartbeat_at': '2026-07-01T00:00:00Z', 'last_telemetry_at': '2026-07-01T00:00:00Z'},
    )
    action = wms.resolve_next_required_action(chain)
    assert action != 'open_incident', 'zero detections must never resolve to open_incident'


def _full_chain(*, detections, alerts, incidents):
    from services.api.app import workspace_monitoring_summary as wms

    return wms.build_runtime_setup_chain(
        counters={
            'workspaces_count': 1, 'assets_count': 1, 'verified_assets_count': 1,
            'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1,
            'detections_count': detections, 'alerts_count': alerts, 'incidents_count': incidents,
            'response_actions_count': 0, 'evidence_count': 0,
        },
        timestamps={'last_heartbeat_at': '2026-07-01T00:00:00Z', 'last_telemetry_at': '2026-07-01T00:00:00Z'},
    )


def test_stale_ingestion_overrides_open_incident_workflow_action():
    # A pre-existing alert with no incident normally resolves to open_incident; when
    # ingestion is stale, restoring ingestion takes priority (canonical, fail-closed).
    from services.api.app import workspace_monitoring_summary as wms

    chain = _full_chain(detections=1, alerts=1, incidents=0)
    assert wms.resolve_next_required_action(chain) == 'open_incident'
    assert wms.resolve_next_required_action(chain, ingestion_stale=True) == 'diagnose_ingestion'


def test_stale_ingestion_zero_detections_never_open_incident():
    # Zero detections + stale ingestion → diagnose_ingestion, and never open_incident.
    from services.api.app import workspace_monitoring_summary as wms

    chain = _full_chain(detections=0, alerts=0, incidents=0)
    assert wms.resolve_next_required_action(chain, ingestion_stale=True) == 'diagnose_ingestion'
    assert wms.resolve_next_required_action(chain, ingestion_stale=True) != 'open_incident'


def test_stale_ingestion_does_not_override_setup_actions():
    # Before the worker ever reports, "add asset" etc. are true prerequisites that a
    # stale-ingestion signal must NOT hijack into diagnose_ingestion.
    from services.api.app import workspace_monitoring_summary as wms

    chain = wms.build_runtime_setup_chain(
        counters={'workspaces_count': 1, 'assets_count': 0, 'verified_assets_count': 0,
                  'targets_count': 0, 'monitored_systems_count': 0, 'enabled_monitored_systems_count': 0,
                  'detections_count': 0, 'alerts_count': 0, 'incidents_count': 0,
                  'response_actions_count': 0, 'evidence_count': 0},
        timestamps={'last_heartbeat_at': None, 'last_telemetry_at': None},
    )
    assert wms.resolve_next_required_action(chain, ingestion_stale=True) == 'add_asset'


# ==========================================================================
# Fakes
# ==========================================================================
def _find(queries, *needles):
    for q in queries:
        norm = ' '.join(str(q).split()).lower()
        if all(n in norm for n in needles):
            return norm
    return None


class _R:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _RecordingSummaryDB:
    """Read-only fake that records every executed query and returns canned rows,
    so both the SQL shape and the composed summary can be asserted."""

    def __init__(self, *, last_security_at=None, last_ingestion_at=None, last_security_ever=None):
        self.tables = {'threat_detections', 'threat_detection_evidence', 'telemetry_events', 'threat_detection_worker_state'}
        self.last_security_at = last_security_at
        self.last_ingestion_at = last_ingestion_at
        # Non-windowed newest security telemetry (defaults to the in-window value so
        # existing callers keep their behaviour). Set explicitly to model "older
        # security telemetry exists outside the selected window".
        self.last_security_ever = last_security_ever if last_security_ever is not None else last_security_at
        self.queries = []
        self.writes = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).lower()
        self.queries.append(q)
        if any(k in q for k in ('insert', 'update ', 'delete')):
            self.writes.append((q, params))
        if 'to_regclass' in q:
            table = str((params or ('',))[0]).split('.')[-1]
            return _R([{'ok': table in self.tables}])
        if 'max(heartbeat_at) as hb' in q:
            return _R([{'hb': None, 'done': None}])
        if 'max(observed_at) as last_ingestion_at' in q:
            return _R([{'last_ingestion_at': self.last_ingestion_at}])
        if 'max(observed_at) as last_at' in q and '<> all' in q:
            # Non-windowed "newest security telemetry ever" query.
            return _R([{'last_at': self.last_security_ever}])
        if 'current_count' in q and 'security_events' in q:
            return _R([{'current_count': 0, 'previous_count': 0, 'last_at': self.last_security_at,
                        'live_count': 0, 'sim_count': 0, 'replay_count': 0}])
        if 'floor(extract(epoch from observed_at)' in q:
            return _R([])
        if 'as detection_count' in q and 'as anomaly_count' in q:
            return _R([{'detection_count': 0, 'previous_detection_count': 0, 'anomaly_count': 0, 'previous_anomaly_count': 0}])
        if 'select detection_type, count(*) as count' in q:
            return _R([])
        if 'as mttd' in q:
            return _R([{'mttd': None}])
        if 'select * from threat_detections where workspace_id = %s and status = any(%s) order by' in q:
            return _R([])
        if 'as active_total' in q:
            return _R([{'active_total': 0, 'live_active': 0, 'non_live_active': 0}])
        return _R([])

    def commit(self):
        self.writes.append(('commit', None))


def db_build(db, *, window=None):
    return tds.build_threat_summary(db, workspace_id='ws-1', config=tdc.engine_config(), window=window)


class _ScanConn:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append(' '.join(str(query).split()).lower())
        return _R([])


class _TelemetryConn:
    def __init__(self, *, total=2):
        self.tables = {'telemetry_events', 'assets'}
        self.total = total
        self.queries = []
        self.writes = []
        self.select_params = None

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).lower()
        self.queries.append(q)
        if any(k in q for k in ('insert', 'update ', 'delete')):
            self.writes.append((q, params))
        if 'to_regclass' in q:
            table = str((params or ('',))[0]).split('.')[-1]
            return _R([{'ok': table in self.tables}])
        if 'count(*) as n' in q:
            return _R([{'n': self.total}])
        if 'from telemetry_events te' in q:
            self.select_params = params
            return _R([])
        return _R([])

    def commit(self):
        pass


def _telemetry_conn(*, workspace='ws-1'):
    import contextlib

    from services.api.app import pilot

    conn = _TelemetryConn()
    req = type('Req', (), {'headers': {'x-workspace-id': workspace}})()

    @contextlib.contextmanager
    def _patch():
        import unittest.mock as m

        with m.patch.object(pilot, 'require_live_mode', lambda: None), \
             m.patch.object(pilot, 'ensure_pilot_schema', lambda c: None), \
             m.patch.object(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'u-1'}), \
             m.patch.object(pilot, 'resolve_workspace', lambda c, uid, wsid: {'workspace_id': workspace, 'workspace': {'id': workspace}}), \
             m.patch.object(pilot, 'pg_connection', lambda: _cm(conn)):
            yield

    return conn, req, _patch


def _cm(conn):
    import contextlib

    @contextlib.contextmanager
    def _c():
        yield conn

    return _c()
