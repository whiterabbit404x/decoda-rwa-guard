"""Operational Integrity — the Screen 5 request surface.

Covers the first-class Category filter (a real narrowing of backend records, not
a label), the search filter, the enriched detection serializer, the analysis
payload behind the Operational Integrity Analysis panel, workspace isolation,
and the guarantee that the existing cyber-security lane is unchanged.

pilot auth/DB are monkeypatched so the thin request wrappers are covered without
a live Postgres, matching test_threat_detection_endpoints.py.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.domains.operational_integrity import schemas
from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import endpoints

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, *, tables=('threat_detections', 'threat_detection_evidence', 'telemetry_events', 'assets'), rows=None, total=1):
        self.tables = set(tables)
        self.rows = rows or []
        self.total = total
        self.queries: list[tuple[str, tuple]] = []
        self.select_params = None

    def execute(self, query, params=None):
        raw = ' '.join(str(query).split())
        q = raw.lower()
        self.queries.append((raw, params or ()))
        if 'to_regclass' in q:
            table = str((params or ('',))[0]).split('.')[-1]
            return _Result([{'ok': table in self.tables}])
        if 'count(*) as n' in q:
            return _Result([{'n': self.total}])
        if 'from threat_detections td' in q:
            self.select_params = params
            return _Result(self.rows)
        if 'from threat_detection_evidence' in q:
            return _Result([])
        return _Result([])

    def commit(self):
        pass

    def find(self, needle: str):
        return [(q, p) for q, p in self.queries if needle in q.lower()]


class _Request:
    def __init__(self, workspace='ws-1'):
        self.headers = {'x-workspace-id': workspace}


@pytest.fixture
def patched(monkeypatch):
    def _install(conn, *, workspace_id='ws-1'):
        monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
        monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
        monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'u-1'})
        monkeypatch.setattr(
            pilot, 'resolve_workspace',
            lambda c, uid, wsid: {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}},
        )

        @contextlib.contextmanager
        def _conn():
            yield conn

        monkeypatch.setattr(pilot, 'pg_connection', _conn)
        return conn

    return _install


def _checks(transfer_agent='FAIL', settlement='FAIL'):
    return {
        'on_chain_event': {'key': 'on_chain_event', 'label': 'On-Chain Event', 'status': 'PASS', 'reason': 'Mint observed', 'source': 'RPC polling'},
        'transfer_agent_match': {'key': 'transfer_agent_match', 'label': 'Transfer-Agent', 'status': transfer_agent, 'reason': 'No authorized issuance', 'source': 'Acme Transfer Agent'},
        'settlement_match': {'key': 'settlement_match', 'label': 'Settlement', 'status': settlement, 'reason': 'No matching settlement', 'source': 'Acme Transfer Agent'},
        'signer_validity': {'key': 'signer_validity', 'label': 'Signer Validity', 'status': 'PASS', 'reason': 'Cryptographically valid', 'source': 'chain 8453'},
    }


def _operational_row(**kw):
    row = {
        'id': 'det-1', 'detection_type': 'unmatched_issuance', 'category': 'OPERATIONAL_INTEGRITY',
        'title': 'Unmatched Issuance on US Treasury Bond #013', 'severity': 'critical',
        'confidence': 0.991, 'status': 'open', 'chain_id': 8453, 'primary_asset_id': 'asset-1',
        'asset_name': 'US Treasury Bond #013', 'evidence_source': 'live', 'evidence_quality': 'event_logs',
        'event_count': 1, 'actor_count': 0, 'transaction_count': 1, 'evidence_count': 6,
        'explanation': 'A cryptographically valid mint is not supported by authorized business state.',
        'recommended_next_step': 'Confirm with the authoritative source.',
        'alert_eligible': True, 'ai_summary': None, 'ai_summary_source': 'deterministic',
        'linked_alert_id': None, 'linked_incident_id': None,
        'first_seen_at': NOW, 'last_seen_at': NOW, 'detected_at': NOW,
        'deterministic_reason_code': 'NO_MATCHING_AUTHORIZED_ISSUANCE',
        'operational_checks': _checks(), 'matcher_version': 'op-integrity-v1',
        # Base-unit amounts as the driver would return them from NUMERIC(78, 0).
        'observed_amount': 5_000_000, 'expected_amount': 0, 'variance_amount': 5_000_000,
        'amount_decimals': 0, 'amount_unit': 'USTB', 'operation': 'mint',
        'tx_hash': '0x' + 'ab' * 32, 'block_number': 21_000_000,
        'telemetry_source': 'rpc_polling', 'telemetry_stage': 'FINALIZED',
        'telemetry_observed_at': NOW, 'preconfirmation_received_at': None,
        'provenance': {'telemetry_id': 'tel-1', 'asset_name': 'US Treasury Bond #013'},
        'score_inputs': {}, 'cluster_key': 'ck-1',
    }
    row.update(kw)
    return row


def _cyber_row(**kw):
    row = _operational_row(
        id='det-2', detection_type='unusual_transfer', category='CYBER_SECURITY',
        title='Unusual Transfer', severity='high', deterministic_reason_code=None,
        operational_checks={}, matcher_version=None, observed_amount=None,
        expected_amount=None, variance_amount=None, operation=None,
        telemetry_source=None, telemetry_stage=None,
    )
    row.update(kw)
    return row


# --------------------------------------------------------------------------
# 11 + 12. Category is a first-class filter over real records
# --------------------------------------------------------------------------
def test_the_category_filter_narrows_the_backend_query(patched):
    conn = patched(FakeConn(rows=[_operational_row()]))
    result = endpoints.detections_endpoint(_Request(), category='OPERATIONAL_INTEGRITY')
    assert result['total'] == 1
    select = conn.find('from threat_detections td')[0]
    assert 'td.category' in select[0]
    assert tdc.CATEGORY_OPERATIONAL_INTEGRITY in select[1]


def test_the_category_filter_accepts_the_lowercase_form(patched):
    conn = patched(FakeConn(rows=[_operational_row()]))
    endpoints.detections_endpoint(_Request(), category='operational_integrity')
    assert tdc.CATEGORY_OPERATIONAL_INTEGRITY in conn.find('from threat_detections td')[0][1]


def test_an_unknown_category_is_rejected_rather_than_silently_ignored(patched):
    patched(FakeConn())
    with pytest.raises(HTTPException) as exc:
        endpoints.detections_endpoint(_Request(), category='made_up_lane')
    assert exc.value.status_code == 400


def test_omitting_the_category_returns_both_lanes_exactly_as_before(patched):
    conn = patched(FakeConn(rows=[_operational_row(), _cyber_row()]))
    result = endpoints.detections_endpoint(_Request())
    assert len(result['detections']) == 2
    assert 'td.category' not in conn.find('from threat_detections td')[0][0]


def test_pre_migration_rows_without_a_category_still_classify(patched):
    # A row written before the column existed reports the lane its type implies,
    # so a filter can never silently drop it into the wrong bucket.
    patched(FakeConn(rows=[_operational_row(category=None), _cyber_row(category='')]))
    result = endpoints.detections_endpoint(_Request())
    by_id = {d['id']: d for d in result['detections']}
    assert by_id['det-1']['category'] == 'OPERATIONAL_INTEGRITY'
    assert by_id['det-2']['category'] == 'CYBER_SECURITY'


def test_the_operational_detection_types_are_valid_filter_values(patched):
    conn = patched(FakeConn(rows=[_operational_row()]))
    endpoints.detections_endpoint(_Request(), detection_type='unmatched_issuance')
    assert 'unmatched_issuance' in conn.find('from threat_detections td')[0][1]


def test_search_is_a_bound_parameter_not_interpolated_sql(patched):
    conn = patched(FakeConn(rows=[_operational_row()]))
    endpoints.detections_endpoint(_Request(), search="'; DROP TABLE threat_detections; --")
    query, params = conn.find('from threat_detections td')[0]
    assert 'DROP TABLE' not in query
    assert any('drop table' in str(p).lower() for p in params)


# --------------------------------------------------------------------------
# Serializer
# --------------------------------------------------------------------------
def test_amounts_are_serialized_as_strings_to_survive_json(patched):
    patched(FakeConn(rows=[_operational_row(observed_amount=10 ** 40, variance_amount=10 ** 40)]))
    detection = endpoints.detections_endpoint(_Request())['detections'][0]
    # A uint256 amount through a JSON number would be rounded by any consumer's
    # double; as a string every digit survives.
    assert detection['observed_amount'] == str(10 ** 40)
    assert isinstance(detection['observed_amount'], str)
    assert detection['expected_amount'] == '0'


def test_a_cyber_row_serializes_the_operational_fields_as_empty_not_as_zero(patched):
    patched(FakeConn(rows=[_cyber_row()]))
    detection = endpoints.detections_endpoint(_Request())['detections'][0]
    assert detection['observed_amount'] is None
    assert detection['expected_amount'] is None
    assert detection['deterministic_reason_code'] is None
    assert detection['operational_checks'] == {}


def test_the_operational_detection_type_label_is_resolved(patched):
    patched(FakeConn(rows=[_operational_row()]))
    detection = endpoints.detections_endpoint(_Request())['detections'][0]
    assert detection['detection_type_label'] == 'Unmatched Issuance'


# --------------------------------------------------------------------------
# 14. The analysis payload behind the Operational Integrity Analysis panel
# --------------------------------------------------------------------------
def test_the_analysis_returns_the_stored_checks_in_display_order():
    analysis = endpoints.operational_analysis(_operational_row())
    assert analysis is not None
    assert [c['key'] for c in analysis['checks']] == list(schemas.CHECK_ORDER)
    assert analysis['checks_available'] is True
    assert analysis['conclusion'] == schemas.CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY
    assert analysis['deterministic_reason_code'] == 'NO_MATCHING_AUTHORIZED_ISSUANCE'
    assert analysis['confidence'] == pytest.approx(0.991)


def test_a_cyber_detection_has_no_operational_analysis():
    assert endpoints.operational_analysis(_cyber_row()) is None


def test_an_operational_row_with_no_recorded_checks_is_indeterminate_not_clear():
    # An empty check set has proven nothing. Reporting it as authorized would be
    # the exact failure mode this lane exists to prevent.
    analysis = endpoints.operational_analysis(_operational_row(operational_checks={}))
    assert analysis is not None
    assert analysis['checks_available'] is False
    assert analysis['conclusion'] == schemas.CONCLUSION_INDETERMINATE


def test_an_unknown_check_never_reads_as_authorized():
    checks = _checks(transfer_agent='UNKNOWN', settlement='UNKNOWN')
    analysis = endpoints.operational_analysis(_operational_row(operational_checks=checks, severity='medium'))
    assert analysis['conclusion'] == schemas.CONCLUSION_INDETERMINATE


def test_all_passing_checks_read_as_operationally_authorized():
    checks = _checks(transfer_agent='PASS', settlement='PASS')
    analysis = endpoints.operational_analysis(_operational_row(operational_checks=checks))
    assert analysis['conclusion'] == schemas.CONCLUSION_OPERATIONALLY_AUTHORIZED


def test_the_analysis_labels_ai_text_as_explanation_only():
    analysis = endpoints.operational_analysis(_operational_row(ai_summary='A narrative.', ai_summary_source='ai'))
    assert analysis['ai_authority'] == 'AI Analysis: Explanation only'
    assert analysis['ai_summary'] == 'A narrative.'
    # The deterministic narrative is still present and still authoritative.
    assert analysis['narrative']['source'] == 'deterministic'
    assert analysis['narrative']['finding']


class _DetailConn(FakeConn):
    """Answers the detail endpoint's single-row lookup with a stored row."""

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).lower()
        if 'where td.id = %s::uuid' in q:
            self.queries.append((' '.join(str(query).split()), params or ()))
            return _Result(self.rows)
        return super().execute(query, params)


def test_the_detail_endpoint_attaches_the_analysis(patched):
    patched(_DetailConn(rows=[_operational_row()]))
    result = endpoints.detection_detail_endpoint('det-1', _Request())
    assert 'operational_analysis' in result['detection']
    assert result['detection']['operational_analysis']['conclusion'] == schemas.CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY


def test_the_detail_endpoint_leaves_a_cyber_detection_without_an_analysis(patched):
    patched(_DetailConn(rows=[_cyber_row()]))
    result = endpoints.detection_detail_endpoint('det-2', _Request())
    assert 'operational_analysis' not in result['detection']


# --------------------------------------------------------------------------
# 5. Workspace isolation
# --------------------------------------------------------------------------
def test_every_detection_query_is_scoped_to_the_authenticated_workspace(patched):
    conn = patched(FakeConn(rows=[_operational_row()]))
    endpoints.detections_endpoint(_Request(workspace='ws-99'), category='OPERATIONAL_INTEGRITY')
    for query, params in conn.find('threat_detections td'):
        assert 'td.workspace_id = %s' in query
        # The workspace comes from the authenticated context, never from the
        # header the caller supplied.
        assert 'ws-1' in params
        assert 'ws-99' not in params


def test_a_detection_detail_for_another_workspace_is_not_found(patched):
    class _EmptyConn(FakeConn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).lower()
            if 'where td.id = %s::uuid' in q:
                return _Result([])  # scoped query matched nothing
            return super().execute(query, params)

    patched(_EmptyConn())
    with pytest.raises(HTTPException) as exc:
        endpoints.detection_detail_endpoint('det-from-other-ws', _Request())
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------
# 10. The existing cyber-security lane still works
# --------------------------------------------------------------------------
def test_the_existing_cyber_filters_are_unchanged(patched):
    conn = patched(FakeConn(rows=[_cyber_row()]))
    result = endpoints.detections_endpoint(
        _Request(), detection_type='unusual_transfer', severity='high', status_value='open',
    )
    assert len(result['detections']) == 1
    assert result['detections'][0]['detection_type'] == 'unusual_transfer'
    query, params = conn.find('from threat_detections td')[0]
    assert 'td.detection_type = %s' in query
    assert 'td.severity = %s' in query
    assert 'unusual_transfer' in params and 'high' in params


def test_a_cyber_category_filter_excludes_the_operational_lane(patched):
    conn = patched(FakeConn(rows=[_cyber_row()]))
    endpoints.detections_endpoint(_Request(), category='CYBER_SECURITY')
    assert tdc.CATEGORY_CYBER_SECURITY in conn.find('from threat_detections td')[0][1]


def test_an_invalid_detection_type_is_still_rejected(patched):
    patched(FakeConn())
    with pytest.raises(HTTPException) as exc:
        endpoints.detections_endpoint(_Request(), detection_type='not_a_detector')
    assert exc.value.status_code == 400
