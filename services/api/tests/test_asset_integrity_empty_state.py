"""Screen 3 — the Integrity GET must always return a renderable, truthful payload.

The regression this file exists for: the Integrity tab collapsed to the single
sentence "Asset integrity state is unavailable right now." That sentence is the
frontend's ``!response.ok`` branch, so the GET had returned a non-2xx. Two things
are asserted here:

  1. the read path returns 200 with a COMPLETE structure for every domain state
     (nothing configured, source unavailable, stale, never evaluated, evaluated),
     so a "no data" asset is never an error, and
  2. a drifted production schema — a column one of migrations 0023/0024/0028/0131
     added that a deployment has not applied — degrades to a rendered asset
     instead of an unhandled 500.

Truthfulness invariants: a projection never carries a variance, a severity or a
rule stamp; an anomaly and a clean result BOTH require a persisted snapshot.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.api.app import pilot
from services.api.app.domains.asset_integrity import config as aic
from services.api.app.domains.asset_integrity import endpoints
from services.api.app.domains.asset_integrity import reconciliation as engine

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
CFG = aic.integrity_config()

WALLET_ASSET = {
    'id': 'aaaaaaaa-1111-2222-3333-444444444444',
    'name': 'Test MetaMask Wallet',
    'asset_type': 'wallet',
    'rwa_asset_type': None,
    'chain_network': 'base-mainnet',
    'identifier': '0x' + 'c' * 40,
    'custodian': None,
    'token_symbol': None,
    'token_contract_address': None,
    'token_decimals': None,
    'value_usd': None,
    'reserve_feed_type': 'none',
    'verification_status': 'verified',
    'created_by_user_id': 'u1',
}

TOKEN_ASSET = {
    **WALLET_ASSET,
    'id': 'bbbbbbbb-1111-2222-3333-444444444444',
    'name': 'Demo Seed Tokenized Bond',
    'asset_type': 'contract',
    'rwa_asset_type': 'tokenized_treasury',
    'token_contract_address': '0x' + 'a' * 40,
    'token_decimals': 6,
}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Fake connection following the repository convention.

    ``missing_columns`` simulates schema drift: naming one of those columns raises
    the same "does not exist" error psycopg raises for an unapplied migration.
    """

    def __init__(self, *, asset=WALLET_ASSET, onchain=None, authoritative=None,
                 snapshot=None, missing_columns=(), detection=None):
        self.asset = asset
        self.onchain = onchain
        self.authoritative = authoritative
        self.snapshot = snapshot
        self.detection = detection
        self.missing_columns = set(missing_columns)
        self.statements: list[tuple[str, object]] = []
        self.writes: list[tuple[str, object]] = []
        self.used_jsonb_fallback = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        self.statements.append((q, params))
        if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
            self.writes.append((q, params))
        if 'to_regclass' in ql:
            return _Result([{'ok': True}])
        if 'to_jsonb(a) as asset_json' in ql:
            self.used_jsonb_fallback = True
            snapshot = {k: v for k, v in (self.asset or {}).items() if k not in self.missing_columns}
            return _Result([{'id': self.asset['id'], 'asset_json': snapshot}] if self.asset else [])
        if 'from assets' in ql:
            for column in self.missing_columns:
                if column in q:
                    raise RuntimeError(f'column "{column}" does not exist')
            return _Result([self.asset] if self.asset else [])
        if 'asset_onchain_supply_observations' in ql:
            return _Result([self.onchain] if self.onchain else [])
        if 'asset_authoritative_state' in ql:
            return _Result([self.authoritative] if self.authoritative else [])
        if 'asset_reconciliation_snapshots' in ql:
            return _Result([self.snapshot] if self.snapshot else [])
        if 'threat_detections' in ql:
            return _Result([self.detection] if self.detection else [])
        return _Result([])

    def commit(self):
        pass


class FakeRequest:
    def __init__(self):
        self.headers = {'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'}


@pytest.fixture
def integrity_get(monkeypatch):
    """Call the read endpoint against a FakeConn, with auth/workspace stubbed."""
    def _call(conn, asset_id=None):
        @contextlib.contextmanager
        def _pg():
            yield conn

        monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
        monkeypatch.setattr(pilot, 'pg_connection', _pg)
        monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
        monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'u1'})
        monkeypatch.setattr(pilot, 'utc_now', lambda: NOW)
        monkeypatch.setattr(pilot, 'resolve_workspace', lambda c, uid, wid=None: {
            'workspace_id': 'ws-1', 'role': 'owner',
            'workspace': {'id': 'ws-1', 'name': 'Workspace', 'slug': 'workspace'},
        })
        return endpoints.integrity_state_endpoint(asset_id or conn.asset['id'], FakeRequest())
    return _call


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
        'settlement_state': 'settled', 'source_name': 'Transfer Agent', 'source_kind': 'transfer_agent',
        'source_status': 'reported', 'source_error': None, 'external_reference': 'SUB-1',
        'evidence_source': 'live', 'observed_at': NOW,
    }
    row.update(kw)
    return row


def _snapshot_row(**kw):
    row = {
        'id': 'snap-1', 'status': engine.UNEXPLAINED_VARIANCE,
        'reason_code': engine.NO_MATCHING_AUTHORIZED_ISSUANCE, 'severity': 'critical',
        'observed_supply': Decimal('5000000'), 'expected_supply': Decimal('4500000'),
        'variance_units': Decimal('500000'), 'token_decimals': 6,
        'rule_id': 'RP-17', 'rule_version': 4, 'rule_config': {},
        'evaluated_at': NOW, 'onchain_observed_at': NOW, 'authoritative_observed_at': NOW,
        'onchain_source': 'evm_rpc', 'authoritative_source': 'Transfer Agent', 'evidence_source': 'live',
        'block_number': 21_000_000, 'tx_hash': '0x' + 'b' * 64, 'external_reference': 'SUB-1',
        'evidence_count': 6, 'evidence_refs': [], 'match_detail': {},
        'canonical_event_id': None, 'matched_issuance_id': None,
        'ai_summary': None, 'ai_summary_source': 'deterministic', 'trigger_source': 'worker',
    }
    row.update(kw)
    return row


# --------------------------------------------------------------------------
# The payload is always complete
# --------------------------------------------------------------------------
def _assert_renderable(payload):
    """Every panel the Integrity tab draws has a structure to draw from."""
    assert payload['onchain_state'] is not None
    assert payload['authoritative_state'] is not None
    assert payload['reconciliation_view'] is not None
    assert payload['ai_assessment_view'] is not None
    assert payload['onchain_state']['availability']
    assert payload['authoritative_state']['availability']
    assert payload['reconciliation_view']['status']
    assert payload['ai_assessment_view']['explanation']


def test_wallet_with_nothing_configured_is_a_200_not_an_error(integrity_get):
    payload = integrity_get(FakeConn())
    _assert_renderable(payload)
    assert payload['state'] == 'not_configured'


def test_every_domain_state_returns_a_complete_payload(integrity_get):
    cases = [
        FakeConn(),                                                                   # nothing configured
        FakeConn(onchain=_onchain_row()),                                             # observed, no source
        FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row(source_status='unavailable')),
        FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row(observed_at=NOW - timedelta(days=2))),
        FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row()),         # configured, unevaluated
        FakeConn(asset=TOKEN_ASSET, onchain=_onchain_row(), authoritative=_authoritative_row(),
                 snapshot=_snapshot_row()),                                           # evaluated
    ]
    for conn in cases:
        _assert_renderable(integrity_get(conn))


def test_the_read_path_writes_nothing_in_any_state(integrity_get):
    for conn in (FakeConn(), FakeConn(onchain=_onchain_row()), FakeConn(snapshot=_snapshot_row())):
        integrity_get(conn)
        assert conn.writes == []


# --------------------------------------------------------------------------
# Root cause: schema drift must not 500 the whole tab
# --------------------------------------------------------------------------
@pytest.mark.parametrize('column', [
    'rwa_asset_type', 'custodian', 'value_usd', 'token_symbol',
    'token_decimals', 'token_contract_address', 'verification_status', 'reserve_feed_type',
])
def test_a_drifted_assets_column_degrades_instead_of_erroring(integrity_get, column):
    conn = FakeConn(missing_columns=(column,))
    payload = integrity_get(conn)
    assert conn.used_jsonb_fallback is True
    _assert_renderable(payload)
    # The absent column is None — never a fabricated value.
    assert payload['asset'].get(column) is None
    # Identity that does not depend on the drifted column still resolves.
    assert payload['asset']['id'] == WALLET_ASSET['id']
    assert payload['asset']['name'] == 'Test MetaMask Wallet'


def test_a_non_drift_database_failure_still_surfaces(integrity_get):
    class Exploding(FakeConn):
        def execute(self, query, params=None):
            if 'FROM assets' in ' '.join(str(query).split()):
                raise RuntimeError('connection already closed')
            return super().execute(query, params)

    # Only schema drift is recoverable. A real failure must not be silently
    # turned into an empty asset.
    with pytest.raises(RuntimeError, match='connection already closed'):
        integrity_get(Exploding())


def test_a_missing_asset_is_still_a_404(integrity_get):
    with pytest.raises(Exception) as excinfo:
        integrity_get(FakeConn(asset=None), asset_id=WALLET_ASSET['id'])
    assert getattr(excinfo.value, 'status_code', None) == 404


# --------------------------------------------------------------------------
# Applicability: a wallet has no token supply
# --------------------------------------------------------------------------
def test_wallet_token_supply_is_not_applicable(integrity_get):
    payload = integrity_get(FakeConn())
    onchain = payload['onchain_state']
    assert onchain['total_supply_applicability'] == endpoints.APPLICABILITY_NOT_APPLICABLE
    assert onchain['total_supply'] is None
    # The registry identity the card renders is still present.
    assert onchain['asset_type'] == 'wallet'
    assert onchain['asset_chain_network'] == 'base-mainnet'
    assert onchain['asset_address'] == WALLET_ASSET['identifier']


def test_a_token_asset_keeps_supply_applicable(integrity_get):
    payload = integrity_get(FakeConn(asset=TOKEN_ASSET))
    assert payload['onchain_state']['total_supply_applicability'] == endpoints.APPLICABILITY_APPLICABLE


def test_an_observed_supply_is_applicable_whatever_the_asset_type(integrity_get):
    payload = integrity_get(FakeConn(onchain=_onchain_row()))
    assert payload['onchain_state']['total_supply_applicability'] == endpoints.APPLICABILITY_APPLICABLE


# --------------------------------------------------------------------------
# Availability mapping (error vs domain state)
# --------------------------------------------------------------------------
def test_availability_distinguishes_not_configured_unavailable_and_stale(integrity_get):
    nothing = integrity_get(FakeConn())
    assert nothing['authoritative_state']['availability'] == endpoints.AVAILABILITY_NOT_CONFIGURED
    assert nothing['onchain_state']['availability'] == endpoints.AVAILABILITY_NOT_CONFIGURED

    failed = integrity_get(FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row(source_status='unavailable')))
    assert failed['authoritative_state']['availability'] == endpoints.AVAILABILITY_SOURCE_UNAVAILABLE

    stale = integrity_get(FakeConn(
        onchain=_onchain_row(observed_at=NOW - timedelta(days=2)),
        authoritative=_authoritative_row(observed_at=NOW - timedelta(days=2)),
    ))
    assert stale['authoritative_state']['availability'] == endpoints.AVAILABILITY_STALE
    assert stale['onchain_state']['availability'] == endpoints.AVAILABILITY_STALE

    healthy = integrity_get(FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row()))
    assert healthy['authoritative_state']['availability'] == endpoints.AVAILABILITY_AVAILABLE
    assert healthy['onchain_state']['availability'] == endpoints.AVAILABILITY_AVAILABLE


# --------------------------------------------------------------------------
# The projection never fabricates a verdict
# --------------------------------------------------------------------------
def test_missing_authoritative_source_projects_missing_data_not_a_variance(integrity_get):
    payload = integrity_get(FakeConn(onchain=_onchain_row()))
    view = payload['reconciliation_view']
    assert view['evaluated'] is False
    assert view['status'] == engine.MISSING_AUTHORITATIVE_DATA
    assert view['reason_code'] == engine.AUTHORITATIVE_SOURCE_MISSING
    # Never UNEXPLAINED_VARIANCE: there is no baseline to compare against.
    assert view['status'] != engine.UNEXPLAINED_VARIANCE
    assert view['variance_units'] is None
    assert view['rule_id'] is None
    assert view['severity'] is None
    assert view['evaluated_at'] is None
    assert view['evidence_count'] == 0


def test_a_failed_source_projects_source_unavailable(integrity_get):
    view = integrity_get(FakeConn(
        onchain=_onchain_row(), authoritative=_authoritative_row(source_status='unavailable'),
    ))['reconciliation_view']
    assert view['status'] == engine.SOURCE_UNAVAILABLE
    assert view['reason_code'] == engine.AUTHORITATIVE_SOURCE_UNAVAILABLE
    assert view['variance_units'] is None


def test_a_stale_source_projects_stale_data(integrity_get):
    view = integrity_get(FakeConn(
        onchain=_onchain_row(), authoritative=_authoritative_row(observed_at=NOW - timedelta(days=2)),
    ))['reconciliation_view']
    assert view['status'] == engine.STALE_AUTHORITATIVE_DATA
    assert view['variance_units'] is None


def test_no_observation_projects_insufficient_evidence(integrity_get):
    view = integrity_get(FakeConn())['reconciliation_view']
    assert view['status'] == engine.INSUFFICIENT_EVIDENCE
    assert view['reason_code'] == engine.ONCHAIN_OBSERVATION_MISSING


def test_a_projection_never_reports_an_anomaly_or_a_clean_result(integrity_get):
    """Both verdicts require a persisted evaluation — the projection reports neither."""
    # Inputs that WOULD reconcile cleanly, with no snapshot recorded.
    clean = integrity_get(FakeConn(
        onchain=_onchain_row(total_supply=Decimal('4500000'), last_delta=None, last_delta_operation=None),
        authoritative=_authoritative_row(),
    ))['reconciliation_view']
    assert clean['evaluated'] is False
    assert clean['status'] == engine.INSUFFICIENT_EVIDENCE
    assert clean['reason_code'] == endpoints.RECONCILIATION_NOT_EVALUATED
    assert clean['status'] not in engine.ANOMALY_STATUSES
    assert clean['is_anomaly'] is False

    # Inputs that WOULD look like an unexplained variance, with no snapshot.
    variance = integrity_get(FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row()))['reconciliation_view']
    assert variance['evaluated'] is False
    assert variance['status'] != engine.UNEXPLAINED_VARIANCE
    assert variance['variance_units'] is None


def test_a_projection_always_stays_in_the_existing_status_vocabulary(integrity_get):
    for conn in (
        FakeConn(),
        FakeConn(onchain=_onchain_row()),
        FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row(source_status='error')),
        FakeConn(onchain=_onchain_row(), authoritative=_authoritative_row()),
    ):
        view = integrity_get(conn)['reconciliation_view']
        assert view['status'] in engine.RECONCILIATION_STATUSES
        assert view['status'] in engine.INDETERMINATE_STATUSES


# --------------------------------------------------------------------------
# A persisted evaluation is rendered verbatim
# --------------------------------------------------------------------------
def test_a_persisted_anomaly_is_reported_as_evaluated_with_its_real_numbers(integrity_get):
    payload = integrity_get(FakeConn(
        asset=TOKEN_ASSET, onchain=_onchain_row(), authoritative=_authoritative_row(), snapshot=_snapshot_row(),
    ))
    view = payload['reconciliation_view']
    assert view['evaluated'] is True
    assert view['status'] == engine.UNEXPLAINED_VARIANCE
    assert view['reason_code'] == engine.NO_MATCHING_AUTHORIZED_ISSUANCE
    # Base-unit supply values cross the wire as exact strings (uint256 range).
    assert str(view['variance_units']) == '500000'
    assert str(view['observed_supply']) == '5000000'
    assert str(view['expected_supply']) == '4500000'
    assert view['severity'] == 'critical'
    assert view['rule_id'] == 'RP-17'
    assert view['evidence_count'] == 6
    # The persisted snapshot contract is unchanged for existing consumers.
    assert payload['reconciliation']['status'] == engine.UNEXPLAINED_VARIANCE


def test_the_demo_anomaly_fixture_produces_the_variance_the_same_ui_renders(integrity_get):
    """The demo scenario end to end: 5,000,000 observed vs 4,500,000 expected, a
    +500,000 mint with no matching authorization.

    The engine is run on those exact inputs (pure, no persistence), and the
    resulting verdict is then read back through the endpoint — so the SAME four
    panels that render the missing-data state above render a real anomaly here.
    """
    onchain = _onchain_row(total_supply=Decimal('5000000'), last_delta=Decimal('500000'), last_delta_operation='mint')
    authoritative = _authoritative_row(expected_total_supply=Decimal('4500000'))

    result = engine.evaluate(
        onchain=engine.OnChainObservation(
            total_supply=engine.to_units(onchain['total_supply']), observed_at=NOW,
            block_number=onchain['block_number'], tx_hash=onchain['tx_hash'],
            last_delta=engine.to_units(onchain['last_delta']), last_delta_operation='mint',
            last_delta_at=NOW, external_reference=None, provider_type='evm_rpc',
            evidence_source='live', available=True,
        ),
        authoritative=engine.AuthoritativeState(
            expected_total_supply=engine.to_units(authoritative['expected_total_supply']),
            observed_at=NOW, settlement_state='settled', source_name='Transfer Agent',
            external_reference='SUB-1', evidence_source='live', source_status='reported',
        ),
        authorizations=(),  # no authorized issuance explains the mint
        rules=aic.rules_from_config(CFG),
        now=NOW,
    )
    assert result.status == engine.UNEXPLAINED_VARIANCE
    assert result.reason_code == engine.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert int(result.variance_units) == 500000

    payload = integrity_get(FakeConn(
        asset=TOKEN_ASSET, onchain=onchain, authoritative=authoritative,
        snapshot=_snapshot_row(
            status=result.status, reason_code=result.reason_code,
            variance_units=Decimal(result.variance_units), severity=result.severity,
        ),
    ))
    _assert_renderable(payload)
    view = payload['reconciliation_view']
    assert view['evaluated'] is True
    assert view['status'] == engine.UNEXPLAINED_VARIANCE
    assert view['reason_code'] == engine.NO_MATCHING_AUTHORIZED_ISSUANCE
    assert str(view['variance_units']) == '500000'
    assert payload['onchain_state']['total_supply_applicability'] == endpoints.APPLICABILITY_APPLICABLE
    assert payload['authoritative_state']['availability'] == endpoints.AVAILABILITY_AVAILABLE


# --------------------------------------------------------------------------
# AI assessment is always present and never asked to infer
# --------------------------------------------------------------------------
def test_the_assessor_payload_exists_without_ai_and_without_a_snapshot(integrity_get):
    view = integrity_get(FakeConn())['ai_assessment_view']
    assert view['source'] == 'deterministic'
    assert view['assessment'] == 'Limited'
    # No severity was computed, so no risk impact is claimed.
    assert view['risk_impact'] is None
    assert view['explanation']
    assert view['cta'] == 'configure_monitoring_source'


def test_the_assessor_cta_is_investigate_only_for_a_persisted_anomaly(integrity_get):
    evaluated = integrity_get(FakeConn(
        asset=TOKEN_ASSET, onchain=_onchain_row(), authoritative=_authoritative_row(), snapshot=_snapshot_row(),
    ))['ai_assessment_view']
    assert evaluated['cta'] == 'investigate_variance'
    assert evaluated['assessment'] == 'Complete'


def test_the_assessor_never_claims_a_risk_impact_it_cannot_evidence(integrity_get):
    for conn in (FakeConn(), FakeConn(onchain=_onchain_row())):
        assert integrity_get(conn)['ai_assessment_view']['risk_impact'] is None
