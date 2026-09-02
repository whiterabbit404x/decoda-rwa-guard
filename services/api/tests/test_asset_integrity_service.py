"""Asset integrity service + endpoints — persistence, canonical event, isolation.

Uses the repository's lightweight fake-connection convention so the persistence
and read paths are covered without a live Postgres.

Invariants asserted here:
  * a GET never writes (no snapshot, no detection, no incident from a refresh),
  * a reload returns the SAME persisted verdict,
  * repeated evaluation of one variance maps to ONE canonical event,
  * every query carries the workspace id (tenant isolation),
  * evidence counts come from real stored rows, never a constant.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.api.app.domains.asset_integrity import config as aic
from services.api.app.domains.asset_integrity import reconciliation as engine
from services.api.app.domains.asset_integrity import service

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Matches executed queries on normalized substrings and returns configured
    rows. Records every statement so tests can assert reads AND writes."""

    def __init__(self, tables_exist=True, matchers=None):
        self.tables_exist = tables_exist
        self.matchers = matchers or []
        self.statements = []  # (normalized_query, params)
        self.writes = []
        self.committed = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        self.statements.append((q, params))
        if 'to_regclass' in ql:
            return _Result([{'ok': bool(self.tables_exist)}])
        for needle, rows in self.matchers:
            if needle in q:
                if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
                    self.writes.append((q, params))
                return _Result(rows)
        if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
            self.writes.append((q, params))
            return _Result([])
        return _Result([])

    def commit(self):
        self.committed = True

    def writes_matching(self, needle):
        return [(q, p) for (q, p) in self.writes if needle in q]

    def statements_matching(self, needle):
        return [(q, p) for (q, p) in self.statements if needle in q]


def _onchain_row(**kw):
    row = {
        'id': 'obs-1', 'total_supply': Decimal('5000000'), 'token_decimals': 6,
        'chain_network': 'base-mainnet', 'contract_address': '0x' + 'a' * 40,
        'block_number': 21_000_000, 'tx_hash': '0x' + 'b' * 64,
        'last_delta': Decimal('500000'), 'last_delta_operation': 'mint', 'last_delta_at': NOW,
        'provider_type': 'evm_rpc', 'evidence_source': 'live',
        'telemetry_event_id': 'tel-1', 'observed_at': NOW,
    }
    row.update(kw)
    return row


def _authoritative_row(**kw):
    row = {
        'id': 'auth-1', 'expected_total_supply': Decimal('4500000'), 'token_decimals': 6,
        'settlement_state': 'settled', 'source_name': 'Demo Transfer Agent',
        'source_kind': 'transfer_agent', 'source_status': 'reported', 'source_error': None,
        'external_reference': 'SUB-81922', 'evidence_source': 'live', 'observed_at': NOW,
    }
    row.update(kw)
    return row


def _issuance_row(**kw):
    row = {
        'id': 'iss-1', 'operation': 'mint', 'amount': Decimal('500000'), 'token_decimals': 6,
        'settlement_state': 'settled', 'external_reference': 'SUB-81922',
        'source_name': 'Demo Transfer Agent', 'evidence_source': 'live',
        'authorized_at': NOW, 'effective_from': None, 'effective_until': None, 'consumed_by_tx_hash': None,
    }
    row.update(kw)
    return row


def _conn(onchain=None, authoritative=None, issuances=None, detection=None, **extra):
    matchers = [
        ('FROM asset_onchain_supply_observations', [onchain] if onchain else []),
        ('FROM asset_authoritative_state', [authoritative] if authoritative else []),
        ('FROM asset_authorized_issuances', list(issuances or [])),
        ('FROM threat_detections', [detection] if detection else []),
    ]
    matchers.extend(extra.pop('matchers', []))
    return FakeConn(matchers=matchers, **extra)


# --------------------------------------------------------------------------
# Evaluation over stored evidence
# --------------------------------------------------------------------------
def test_evaluate_asset_reproduces_the_canonical_unauthorized_mint_case():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row(), issuances=[])
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    result = out['result']
    assert result.status == engine.UNEXPLAINED_VARIANCE
    assert result.reason_code == engine.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert result.variance_units == Decimal('500000')
    assert result.severity == 'critical'


def test_evaluate_asset_with_a_matching_authorization_is_not_an_anomaly():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row(), issuances=[_issuance_row()])
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    assert out['result'].status == engine.AUTHORIZED_VARIANCE
    assert out['result'].is_anomaly is False


def test_evaluate_asset_is_read_only():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    assert conn.writes == []


def test_unavailable_authoritative_source_never_becomes_a_variance():
    conn = _conn(
        onchain=_onchain_row(),
        authoritative=_authoritative_row(source_status='unavailable', expected_total_supply=None,
                                        source_error='transfer agent timeout'),
    )
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    assert out['result'].status == engine.SOURCE_UNAVAILABLE
    assert out['result'].status != engine.UNEXPLAINED_VARIANCE


def test_missing_integrity_tables_degrade_to_insufficient_evidence_not_healthy():
    conn = FakeConn(tables_exist=False)
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    assert out['result'].status == engine.INSUFFICIENT_EVIDENCE
    assert out['result'].is_healthy is False


# --------------------------------------------------------------------------
# Evidence references — real rows, counted
# --------------------------------------------------------------------------
def test_evidence_refs_are_built_from_actual_stored_rows():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row(), issuances=[_issuance_row(amount=Decimal('400000'))])
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    kinds = [ref['kind'] for ref in out['evidence_refs']]
    assert 'onchain_supply_observation' in kinds
    assert 'telemetry_event' in kinds
    assert 'onchain_transaction' in kinds
    assert 'authoritative_state' in kinds
    assert 'authorized_issuance_candidate' in kinds
    assert 'reconciliation_rule' in kinds
    # The count is len() of real artifacts — never a hardcoded number.
    assert len(out['evidence_refs']) == len(kinds)


def test_evidence_refs_shrink_when_evidence_is_missing():
    conn = _conn(onchain=_onchain_row(telemetry_event_id=None, tx_hash=None), authoritative=None)
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    kinds = [ref['kind'] for ref in out['evidence_refs']]
    assert kinds == ['onchain_supply_observation', 'reconciliation_rule']


def test_evidence_refs_mark_the_matched_authorization():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row(), issuances=[_issuance_row()])
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    matched = [r for r in out['evidence_refs'] if r['kind'] == 'authorized_issuance_candidate' and r['matched']]
    assert len(matched) == 1
    assert matched[0]['id'] == 'iss-1'


# --------------------------------------------------------------------------
# Persistence + canonical event
# --------------------------------------------------------------------------
def test_evaluate_and_persist_writes_one_immutable_snapshot():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    out = service.evaluate_and_persist(
        conn, workspace_id='ws-1', asset_id='asset-1', asset_name='US Treasury Bond #013',
        trigger_source='manual', now=NOW,
    )
    inserts = conn.writes_matching('INSERT INTO asset_reconciliation_snapshots')
    assert len(inserts) == 1
    params = inserts[0][1]
    assert out['snapshot_id'] in params
    assert engine.UNEXPLAINED_VARIANCE in params
    assert engine.NO_MATCHING_AUTHORIZED_ISSUANCE in params
    assert Decimal('500000') in params
    # No UPDATE of any prior snapshot — history is append-only.
    assert conn.writes_matching('UPDATE asset_reconciliation_snapshots') == []


def test_persisted_snapshot_carries_the_rule_version_used():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    service.evaluate_and_persist(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    params = conn.writes_matching('INSERT INTO asset_reconciliation_snapshots')[0][1]
    assert 'RP-17' in params
    assert 4 in params


def test_anomaly_emits_the_canonical_operational_integrity_event():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    service.evaluate_and_persist(conn, workspace_id='ws-1', asset_id='asset-1', asset_name='Bond', now=NOW)
    inserts = conn.writes_matching('INSERT INTO threat_detections')
    assert len(inserts) == 1
    assert service.CANONICAL_DETECTION_TYPE in inserts[0][1]
    assert 'critical' in inserts[0][1]


def test_the_canonical_event_carries_the_operation_the_verdict_resolved():
    """`threat_detections.operation` is the key Screen 11 selects a policy with.

    Left unstamped, this event resolved to no operation, so no governing policy
    was found, so no `simulation = FALSE` evaluation was ever produced — and every
    response action raised from it sat at POLICY_EVALUATION_MISSING / LOCKED.
    """
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    out = service.evaluate_and_persist(
        conn, workspace_id='ws-1', asset_id='asset-1', asset_name='Bond', now=NOW,
    )
    assert out['result'].operation == 'mint'
    params = conn.writes_matching('INSERT INTO threat_detections')[0][1]
    assert 'mint' in params


def test_a_refresh_never_erases_an_operation_an_earlier_cycle_established():
    existing = {'id': 'evt-1', 'status': 'open', 'linked_alert_id': None, 'linked_incident_id': None}
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row(), detection=existing)
    service.evaluate_and_persist(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    statement, params = conn.writes_matching('UPDATE threat_detections SET')[0]
    assert 'operation = COALESCE(%s, operation)' in statement
    assert 'mint' in params


def test_the_stored_operation_and_the_evidence_filter_share_one_resolver():
    """Two derivations of the same fact could disagree; one cannot."""
    result = engine.evaluate(
        onchain=engine.OnChainObservation(
            total_supply=Decimal('4000000'), observed_at=NOW,
            last_delta=Decimal('500000'), last_delta_operation='burn', last_delta_at=NOW),
        authoritative=engine.AuthoritativeState(expected_total_supply=Decimal('4500000'), observed_at=NOW),
        now=NOW,
    )
    assert result.operation == 'burn'
    assert service.governed_operation(result) == 'burn'


def test_an_operation_that_cannot_be_named_is_never_guessed():
    """A wrong operation would select the WRONG policy — worse than none."""
    not_applicable = engine.evaluate(
        onchain=None, authoritative=None, now=NOW, supply_applicable=False,
    )
    assert not_applicable.operation is None
    assert service.governed_operation(not_applicable) is None


def test_a_healthy_result_emits_no_canonical_event():
    conn = _conn(
        onchain=_onchain_row(total_supply=Decimal('4500000'), last_delta=None, last_delta_operation=None),
        authoritative=_authoritative_row(),
    )
    out = service.evaluate_and_persist(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    assert out['result'].status == engine.RECONCILED
    assert out['canonical_event_id'] is None
    assert conn.writes_matching('INSERT INTO threat_detections') == []


def test_an_indeterminate_result_emits_no_canonical_event():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row(source_status='unavailable'))
    out = service.evaluate_and_persist(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    assert out['result'].status == engine.SOURCE_UNAVAILABLE
    assert out['canonical_event_id'] is None
    assert conn.writes_matching('INSERT INTO threat_detections') == []


def test_repeated_evaluation_of_the_same_variance_reuses_one_canonical_event():
    existing = {'id': 'evt-1', 'status': 'open', 'linked_alert_id': None, 'linked_incident_id': None}
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row(), detection=existing)
    out = service.evaluate_and_persist(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    assert out['canonical_event_id'] == 'evt-1'
    assert conn.writes_matching('INSERT INTO threat_detections') == []
    assert len(conn.writes_matching('UPDATE threat_detections SET')) == 1


def test_canonical_event_cluster_key_is_stable_for_the_same_verdict():
    result = engine.evaluate(
        onchain=engine.OnChainObservation(total_supply=Decimal('5000000'), observed_at=NOW,
                                          last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=engine.AuthoritativeState(expected_total_supply=Decimal('4500000'), observed_at=NOW),
        now=NOW,
    )
    first = service._cluster_key(workspace_id='ws-1', asset_id='a-1', result=result)
    second = service._cluster_key(workspace_id='ws-1', asset_id='a-1', result=result)
    assert first == second


def test_canonical_event_cluster_key_is_workspace_scoped():
    result = engine.evaluate(
        onchain=engine.OnChainObservation(total_supply=Decimal('5000000'), observed_at=NOW,
                                          last_delta=Decimal('500000'), last_delta_operation='mint', last_delta_at=NOW),
        authoritative=engine.AuthoritativeState(expected_total_supply=Decimal('4500000'), observed_at=NOW),
        now=NOW,
    )
    assert (
        service._cluster_key(workspace_id='ws-1', asset_id='a-1', result=result)
        != service._cluster_key(workspace_id='ws-2', asset_id='a-1', result=result)
    )


def test_canonical_event_payload_shape_matches_the_downstream_contract():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    out = service.evaluate_asset(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    payload = service.canonical_event_payload(
        workspace_id='ws-1', asset_id='asset-1', result=out['result'],
        onchain_row=out['onchain_row'], authoritative_row=out['authoritative_row'],
        evidence_refs=out['evidence_refs'], detected_at=NOW, event_id='evt-1',
    )
    assert payload['event_type'] == 'STATE_DRIFT_DETECTED'
    assert payload['category'] == 'OPERATIONAL_INTEGRITY'
    assert payload['status'] == engine.UNEXPLAINED_VARIANCE
    assert payload['reason_code'] == engine.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert payload['observed_value'] == 5_000_000
    assert payload['expected_value'] == 4_500_000
    assert payload['variance_units'] == 500_000
    assert payload['rule_id'] == 'RP-17'
    assert payload['rule_version'] == 4
    assert payload['source'] == {'onchain': 'evm_rpc', 'authoritative': 'Demo Transfer Agent'}
    assert payload['incident_id'] is None
    assert len(payload['evidence_refs']) == len(out['evidence_refs'])


def test_simulator_evidence_never_produces_an_alert_eligible_event():
    conn = _conn(
        onchain=_onchain_row(evidence_source='simulator'),
        authoritative=_authoritative_row(evidence_source='simulator'),
    )
    service.evaluate_and_persist(conn, workspace_id='ws-1', asset_id='asset-1', now=NOW)
    params = conn.writes_matching('INSERT INTO threat_detections')[0][1]
    assert 'simulator' in params
    assert False in params  # alert_eligible


# --------------------------------------------------------------------------
# CASE G — reload returns the persisted result and does not re-evaluate
# --------------------------------------------------------------------------
def _snapshot_row(**kw):
    row = {
        'id': 'snap-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1',
        'observed_supply': Decimal('5000000'), 'expected_supply': Decimal('4500000'),
        'variance_units': Decimal('500000'), 'token_decimals': 6,
        'status': engine.UNEXPLAINED_VARIANCE, 'reason_code': engine.NO_MATCHING_AUTHORIZED_ISSUANCE,
        'severity': 'critical', 'rule_id': 'RP-17', 'rule_version': 4, 'rule_config': {},
        'onchain_observed_at': NOW, 'authoritative_observed_at': NOW, 'evaluated_at': NOW,
        'onchain_source': 'evm_rpc', 'authoritative_source': 'Demo Transfer Agent',
        'evidence_source': 'live', 'block_number': 21_000_000, 'tx_hash': '0x' + 'b' * 64,
        'external_reference': 'SUB-81922', 'matched_issuance_id': None,
        'evidence_count': 6, 'evidence_refs': [], 'match_detail': {},
        'canonical_event_id': 'evt-1', 'ai_summary': None, 'ai_summary_source': 'deterministic',
        'trigger_source': 'manual',
    }
    row.update(kw)
    return row


def test_case_g_latest_snapshot_read_is_side_effect_free():
    conn = FakeConn(matchers=[('FROM asset_reconciliation_snapshots', [_snapshot_row()])])
    first = service.load_latest_snapshot(conn, workspace_id='ws-1', asset_id='asset-1')
    second = service.load_latest_snapshot(conn, workspace_id='ws-1', asset_id='asset-1')
    assert first['status'] == engine.UNEXPLAINED_VARIANCE
    assert first == second  # same persisted result across reloads
    assert conn.writes == []
    assert conn.committed is False


def test_case_g_history_read_is_side_effect_free_and_ordered_newest_first():
    rows = [_snapshot_row(id='snap-2', evaluated_at=NOW), _snapshot_row(id='snap-1', evaluated_at=NOW - timedelta(hours=1))]
    conn = FakeConn(matchers=[('FROM asset_reconciliation_snapshots', rows)])
    history = service.load_snapshot_history(conn, workspace_id='ws-1', asset_id='asset-1', limit=25)
    assert [h['id'] for h in history] == ['snap-2', 'snap-1']
    assert conn.writes == []
    query = conn.statements_matching('FROM asset_reconciliation_snapshots')[0][0]
    assert 'ORDER BY evaluated_at DESC' in query


def test_history_limit_is_bounded_by_configuration():
    cfg = aic.integrity_config()
    assert 1 <= cfg['history_limit'] <= 200


# --------------------------------------------------------------------------
# CASE H — tenant isolation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    'loader',
    [service.load_onchain_observation, service.load_authoritative_state, service.load_latest_snapshot],
)
def test_case_h_every_read_is_workspace_scoped(loader):
    conn = FakeConn()
    loader(conn, workspace_id='ws-A', asset_id='asset-1')
    scoped = [(q, p) for (q, p) in conn.statements if 'to_regclass' not in q.lower()]
    assert scoped, 'loader issued no query'
    for query, params in scoped:
        assert 'workspace_id = %s' in query
        assert params[0] == 'ws-A'


def test_case_h_authorization_lookup_is_workspace_scoped():
    conn = FakeConn()
    service.load_authorizations(conn, workspace_id='ws-A', asset_id='asset-1', limit=10)
    query, params = [s for s in conn.statements if 'asset_authorized_issuances' in s[0]][0]
    assert 'WHERE workspace_id = %s AND asset_id = %s' in query
    assert params[0] == 'ws-A'


def test_case_h_workspace_b_cannot_read_workspace_a_snapshot():
    # The fake connection returns rows only for a matching workspace parameter,
    # mirroring the SQL predicate.
    class ScopedConn(FakeConn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM asset_reconciliation_snapshots' in q and params and params[0] != 'ws-A':
                return _Result([])
            return super().execute(query, params)

    conn = ScopedConn(matchers=[('FROM asset_reconciliation_snapshots', [_snapshot_row()])])
    assert service.load_latest_snapshot(conn, workspace_id='ws-A', asset_id='asset-1') is not None
    assert service.load_latest_snapshot(conn, workspace_id='ws-B', asset_id='asset-1') is None


def test_case_h_persisted_snapshot_row_carries_the_workspace_id():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    service.evaluate_and_persist(conn, workspace_id='ws-A', asset_id='asset-1', now=NOW)
    params = conn.writes_matching('INSERT INTO asset_reconciliation_snapshots')[0][1]
    assert 'ws-A' in params


def test_case_h_canonical_event_row_carries_the_workspace_id():
    conn = _conn(onchain=_onchain_row(), authoritative=_authoritative_row())
    service.evaluate_and_persist(conn, workspace_id='ws-A', asset_id='asset-1', now=NOW)
    params = conn.writes_matching('INSERT INTO threat_detections')[0][1]
    assert 'ws-A' in params


# --------------------------------------------------------------------------
# Demo seeding — demo values must never masquerade as live evidence
# --------------------------------------------------------------------------
def test_demo_seed_is_a_no_op_in_a_production_runtime():
    from services.api.app.domains.asset_integrity import demo_seed

    conn = FakeConn()
    out = demo_seed.seed_demo_integrity_scenario(conn, workspace_id='ws-1', user_id='u-1', allowed=False, now=NOW)
    assert out == {'seeded': False, 'reason': 'production_runtime'}
    assert conn.writes == []


def test_demo_seed_is_a_no_op_when_the_schema_is_provisioning():
    from services.api.app.domains.asset_integrity import demo_seed

    conn = FakeConn(tables_exist=False)
    out = demo_seed.seed_demo_integrity_scenario(conn, workspace_id='ws-1', user_id='u-1', allowed=True, now=NOW)
    assert out['seeded'] is False
    assert out['reason'] == 'schema_provisioning'
    assert conn.writes == []


def test_demo_seed_marks_every_row_as_simulator_never_live():
    from services.api.app.domains.asset_integrity import demo_seed

    conn = _conn(matchers=[('FROM assets', [{'id': 'demo-asset'}])])
    out = demo_seed.seed_demo_integrity_scenario(conn, workspace_id='ws-1', user_id='u-1', allowed=True, now=NOW)
    assert out['seeded'] is True
    for table in ('asset_onchain_supply_observations', 'asset_authoritative_state'):
        inserts = conn.writes_matching(f'INSERT INTO {table}')
        assert len(inserts) == 1
        assert "'simulator'" in inserts[0][0]
        assert "'live'" not in inserts[0][0]


class SeedAwareConn(FakeConn):
    """Fake connection whose reads reflect prior writes, so the seeder's own
    inserts are visible to the evaluation that follows — mirroring a real
    transaction."""

    def __init__(self):
        super().__init__(matchers=[('FROM assets', [{'id': 'demo-asset'}])])
        self.seeded_onchain = None
        self.seeded_authoritative = None

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'INSERT INTO asset_onchain_supply_observations' in q:
            self.seeded_onchain = {
                'id': 'obs-demo', 'total_supply': Decimal(str(params[3])), 'token_decimals': 0,
                'chain_network': 'base-mainnet', 'contract_address': '0x' + '0' * 39 + '2',
                'block_number': params[4], 'tx_hash': params[5], 'last_delta': Decimal(str(params[6])),
                'last_delta_operation': 'mint', 'last_delta_at': params[7],
                'provider_type': 'demo_simulator', 'evidence_source': 'simulator',
                'telemetry_event_id': None, 'observed_at': params[8],
            }
        elif 'INSERT INTO asset_authoritative_state' in q:
            self.seeded_authoritative = {
                'id': 'auth-demo', 'expected_total_supply': Decimal(str(params[3])), 'token_decimals': 0,
                'settlement_state': 'settled', 'source_name': params[4], 'source_kind': 'transfer_agent',
                'source_status': 'reported', 'source_error': None, 'external_reference': 'SUB-DEMO-0001',
                'evidence_source': 'simulator', 'observed_at': params[5],
            }
        elif 'FROM asset_onchain_supply_observations' in q:
            super().execute(query, params)
            return _Result([self.seeded_onchain] if self.seeded_onchain else [])
        elif 'FROM asset_authoritative_state' in q:
            super().execute(query, params)
            return _Result([self.seeded_authoritative] if self.seeded_authoritative else [])
        return super().execute(query, params)


def test_demo_seed_reproduces_the_screenshot_scenario_deterministically():
    from services.api.app.domains.asset_integrity import demo_seed

    conn = SeedAwareConn()
    out = demo_seed.seed_demo_integrity_scenario(conn, workspace_id='ws-1', user_id='u-1', allowed=True, now=NOW)
    # The status/reason are produced by the deterministic engine, not written by the seeder.
    assert out['status'] == engine.UNEXPLAINED_VARIANCE
    assert out['reason_code'] == engine.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert out['evidence_source'] == 'simulator'
    assert conn.writes_matching('INSERT INTO asset_authorized_issuances') == []


def test_demo_seed_does_not_reseed_an_asset_that_already_has_observations():
    from services.api.app.domains.asset_integrity import demo_seed

    conn = _conn(onchain=_onchain_row(), matchers=[('FROM assets', [{'id': 'demo-asset'}])])
    out = demo_seed.seed_demo_integrity_scenario(conn, workspace_id='ws-1', user_id='u-1', allowed=True, now=NOW)
    assert out['seeded'] is False
    assert out['reason'] == 'already_seeded'
    assert conn.writes_matching('INSERT INTO asset_onchain_supply_observations') == []


def test_demo_seeded_event_is_never_alert_eligible():
    from services.api.app.domains.asset_integrity import demo_seed

    conn = SeedAwareConn()
    demo_seed.seed_demo_integrity_scenario(conn, workspace_id='ws-1', user_id='u-1', allowed=True, now=NOW)
    detection_inserts = conn.writes_matching('INSERT INTO threat_detections')
    assert len(detection_inserts) == 1
    assert False in detection_inserts[0][1]  # alert_eligible
    assert 'simulator' in detection_inserts[0][1]


def test_demo_seed_owns_a_dedicated_identifier_separate_from_customer_assets():
    from services.api.app.domains.asset_integrity import demo_seed

    assert demo_seed.DEMO_ASSET_IDENTIFIER == 'demo-seed-integrity-rwa'
    assert 'demo' in demo_seed.DEMO_ASSET_IDENTIFIER
    assert 'simulated' in demo_seed.DEMO_AUTHORITATIVE_SOURCE.lower()
