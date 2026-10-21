"""Decoda product-event intake: signatures, envelopes, detectors and signal bodies (hermetic).

The database-backed flow (routing, alerts, incidents, delivery, tenant isolation) is in
test_decoda_products_postgres.py.
"""

from __future__ import annotations

import json
import pathlib
import time
import uuid

import pytest

from services.api.app import decoda_products as dp

VECTORS = pathlib.Path(__file__).resolve().parents[3] / 'docs' / 'contracts' / 'decoda-signature-v1.vectors.json'
SECRET = 'hermetic-intake-secret-0123456789abcdef'
PLATFORM = str(uuid.uuid4())


def _envelope(producer: str = 'decoda-assets', event_type: str = 'assets.asset.created', **overrides):
    tenant = {'platformOrganizationId': PLATFORM}
    tenant['assetsOrgId' if producer == 'decoda-assets' else 'vaultOrgId'] = str(uuid.uuid4())
    body = {
        'eventId': f'evt_{uuid.uuid4().hex}',
        'eventType': event_type,
        'producer': producer,
        'environment': 'testnet',
        'tenant': tenant,
        'payload': {'metadata': {}},
        'provenance': {'synthetic': True},
    }
    body.update(overrides)
    return body


def _headers(raw: bytes, event_id: str, *, producer: str = 'decoda-assets', secret: str = SECRET, ts: int | None = None):
    timestamp = int(ts if ts is not None else time.time())
    return {
        'X-Decoda-Producer': producer,
        'X-Decoda-Timestamp': str(timestamp),
        'X-Decoda-Signature': dp.signature(secret, raw, timestamp),
        'X-Decoda-Event-Id': event_id,
    }


# ── The shared signature vectors (identical in Assets, Vault and Guard) ──────


@pytest.mark.parametrize('case', json.loads(VECTORS.read_text())['cases'], ids=lambda c: c['name'])
def test_shared_signature_vectors(case):
    body = case['body'].encode()
    headers = {'X-Decoda-Timestamp': str(case['timestamp']), 'X-Decoda-Signature': case['signature'], 'X-Decoda-Event-Id': case['event_id']}
    if case['name'] == 'valid':
        assert dp.signature(case['secret'], body, case['timestamp']) == case['signature'], 'the signer produces the vector'
    if case['valid']:
        assert dp.verify_signature(case['secret'], body, headers, now=case['now']) == case['event_id']
    else:
        with pytest.raises(dp.IntakeError) as refused:
            dp.verify_signature(case['secret'], body, headers, now=case['now'])
        assert refused.value.status == 401
        expected = {'bad_signature': 'SIGNATURE_INVALID', 'stale_timestamp': 'SIGNATURE_STALE', 'missing_headers': 'SIGNATURE_MISSING'}
        assert refused.value.code == expected[case['reason']]


def test_an_unconfigured_secret_is_retryable_not_a_rejection():
    with pytest.raises(dp.IntakeError) as refused:
        dp.verify_signature('', b'{}', {})
    assert (refused.value.status, refused.value.code) == (503, 'INTAKE_NOT_CONFIGURED')


# ── Intake refusals (before any database access) ─────────────────────────────


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('DECODA_ASSETS_INBOUND_SECRET', SECRET)
    monkeypatch.setenv('DECODA_VAULT_INBOUND_SECRET', SECRET)


def _never_connect():
    raise AssertionError('a refused event must never reach the database')


@pytest.mark.parametrize(
    ('mutate', 'status', 'code'),
    [
        (lambda e: {**e, 'environment': 'mainnet'}, 422, 'ENVIRONMENT_NOT_SUPPORTED'),
        (lambda e: {**e, 'producer': 'decoda-vault'}, 422, 'PRODUCER_MISMATCH'),
        (lambda e: {**e, 'eventType': 'vault.transaction.blocked'}, 422, 'UNSUPPORTED_EVENT_TYPE'),
        (lambda e: {**e, 'tenant': {'assetsOrgId': 'org'}}, 422, 'TENANT_UNRESOLVED'),
        (lambda e: {**e, 'tenant': {'platformOrganizationId': 'not-a-uuid'}}, 422, 'TENANT_UNRESOLVED'),
    ],
)
def test_unsupported_envelopes_are_refused_before_the_database(configured, mutate, status, code):
    envelope = mutate(_envelope())
    raw = json.dumps(envelope).encode()
    result = dp.process_event(raw, _headers(raw, envelope['eventId']), connect=_never_connect)
    assert (result.status, result.body['code']) == (status, code)


def test_malformed_mismatched_oversized_and_unknown_producer_requests(configured):
    raw = b'not json'
    assert dp.process_event(raw, _headers(raw, 'evt_' + 'a' * 32), connect=_never_connect).body['code'] == 'MALFORMED_EVENT'
    envelope = _envelope()
    raw = json.dumps(envelope).encode()
    mismatched = dp.process_event(raw, _headers(raw, 'evt_' + 'b' * 32), connect=_never_connect)
    assert (mismatched.status, mismatched.body['code']) == (400, 'EVENT_ID_MISMATCH')
    big = json.dumps({**envelope, 'payload': {'blob': 'x' * dp.MAX_BODY_BYTES}}).encode()
    assert dp.process_event(big, _headers(big, envelope['eventId']), connect=_never_connect).status == 413
    unknown = dp.process_event(raw, _headers(raw, envelope['eventId'], producer='someone-else'), connect=_never_connect)
    assert (unknown.status, unknown.body['code']) == (422, 'UNKNOWN_PRODUCER')
    forged = dp.process_event(raw, _headers(raw, envelope['eventId'], secret='forged-secret'), connect=_never_connect)
    assert forged.status == 401


def test_an_oversized_timestamp_header_is_refused_not_an_error():
    huge = '9' * 5000  # longer than Python converts to int: must answer 401, never raise
    with pytest.raises(dp.IntakeError) as refused:
        dp.verify_signature(SECRET, b'{}', {'X-Decoda-Timestamp': huge, 'X-Decoda-Signature': 'v1=00', 'X-Decoda-Event-Id': 'evt_' + 'c' * 32})
    assert (refused.value.status, refused.value.code) == (401, 'SIGNATURE_MISSING')


def test_an_invalid_occurred_at_is_dropped_before_postgres_sees_it():
    assert dp._timestamp_or_none('2026-99-99T99:99:99Z') is None, 'regex-shaped but not a real time'
    assert dp._timestamp_or_none('yesterday') is None and dp._timestamp_or_none('2026-10-21T08:00:00Z' + ' ' * 60) is None
    assert dp._timestamp_or_none('2026-10-21T08:00:00Z') == '2026-10-21T08:00:00+00:00'
    assert dp._timestamp_or_none('2026-10-21T10:00:00.5+02:00') == '2026-10-21T10:00:00.500000+02:00'
    assert dp._timestamp_or_none('2026-10-21T08:00:00') == '2026-10-21T08:00:00+00:00', 'no offset: UTC'


def test_a_database_failure_is_retryable(configured):
    envelope = _envelope()
    raw = json.dumps(envelope).encode()

    def broken():
        raise OSError('connection refused')

    result = dp.process_event(raw, _headers(raw, envelope['eventId']), connect=broken)
    assert (result.status, result.body['code']) == (503, 'INTAKE_UNAVAILABLE')


# ── Detectors ────────────────────────────────────────────────────────────────


def _parsed(producer: str, event_type: str, payload: dict, severity: str | None = None):
    envelope = _envelope(producer, event_type, payload={**payload, 'severity_hint': severity})
    return dp.parse_envelope(json.dumps(envelope).encode(), dp.PRODUCERS[producer], envelope['eventId'])


CONTRACT = '0x' + 'ab' * 20


@pytest.mark.parametrize(
    ('producer', 'event_type', 'payload', 'severity', 'expected'),
    [
        ('decoda-assets', 'assets.ownership.mismatch', {'metadata': {'token_contract': CONTRACT, 'network': 'base_sepolia', 'scope': 'supply'}}, 'critical', ('alert', 'critical', True)),
        ('decoda-assets', 'assets.ownership.mismatch', {'metadata': {'scope': 'position'}}, 'high', ('alert', 'high', False)),
        ('decoda-assets', 'assets.nav.deviation', {'metadata': {'deviation_pct': '6'}}, 'high', ('alert', 'high', False)),
        ('decoda-assets', 'assets.operation.blocked', {'metadata': {}}, None, ('alert', 'medium', False)),
        ('decoda-assets', 'assets.settlement.failed', {'metadata': {}}, None, ('alert', 'medium', False)),
        ('decoda-assets', 'assets.distribution.blocked', {'metadata': {'blocked_before_vault': True}}, None, ('alert', 'low', False)),
        ('decoda-assets', 'assets.distribution.blocked', {'metadata': {}}, None, ('recorded', 'medium', False)),
        ('decoda-assets', 'assets.tokenization.completed', {'metadata': {}}, None, ('coverage', 'medium', False)),
        ('decoda-assets', 'assets.asset.created', {'metadata': {}}, None, ('recorded', 'medium', False)),
        ('decoda-vault', 'vault.transaction.abnormal_transfer', {'network': 'sepolia', 'destination': CONTRACT}, None, ('alert', 'high', False)),
        ('decoda-vault', 'vault.approval.repeated_failures', {'network': 'sepolia', 'wallet': CONTRACT}, None, ('alert', 'high', False)),
        ('decoda-vault', 'vault.transaction.blocked', {'reasons': [{'code': 'LIMIT'}]}, None, ('alert', 'medium', False)),
        ('decoda-vault', 'vault.transaction.executed', {}, None, ('recorded', 'medium', False)),
    ],
)
def test_the_detector_table(producer, event_type, payload, severity, expected):
    detection = dp.detect(_parsed(producer, event_type, payload, severity))
    assert (detection.kind, detection.severity, detection.incident) == expected


def test_a_supply_mismatch_names_its_contract_on_vaults_network():
    detection = dp.detect(
        _parsed('decoda-assets', 'assets.ownership.mismatch', {'metadata': {'token_contract': CONTRACT.upper().replace('0X', '0x'), 'network': 'base_sepolia'}}, 'critical')
    )
    assert detection.subjects == ((CONTRACT, 'base-sepolia'),)
    assert dp.detect(_parsed('decoda-assets', 'assets.ownership.mismatch', {'metadata': {'token_contract': 'nope', 'network': 'base_sepolia'}}, 'critical')).subjects == ()
    assert dp.detect(_parsed('decoda-assets', 'assets.ownership.mismatch', {'metadata': {'token_contract': CONTRACT, 'network': 'mainnet'}}, 'critical')).subjects == ()


def test_synthetic_provenance_is_carried():
    envelope = _parsed('decoda-assets', 'assets.asset.created', {})
    assert envelope.synthetic is True
    plain = _envelope('decoda-vault', 'vault.transaction.executed', provenance={})
    assert dp.parse_envelope(json.dumps(plain).encode(), dp.PRODUCERS['decoda-vault'], plain['eventId']).synthetic is False


# ── Signal bodies (the shapes Assets and Vault validate) ─────────────────────


def _alert(**overrides):
    alert = {
        'id': uuid.uuid4(),
        'alert_type': 'supply_mismatch',
        'title': 'Decoda Assets (testnet product event): supply differs',
        'severity': 'critical',
        'status': 'open',
        'incident_id': None,
        'payload': {
            'producer': 'decoda-assets',
            'product_tenant_ref': str(uuid.uuid4()),
            'asset_id': str(uuid.uuid4()),
            'event_id': 'evt_' + 'c' * 32,
            'synthetic': True,
            'subjects': [{'address': CONTRACT, 'network': 'base-sepolia'}],
        },
    }
    alert.update(overrides)
    return alert


def test_assets_alert_signal_has_the_shape_assets_validates():
    import re

    alert = _alert(incident_id=uuid.uuid4())
    body = dp.assets_alert_signal(alert)
    assert body['kind'] == 'alert' and body['organization_id'] == alert['payload']['product_tenant_ref']
    assert re.match(r'^[A-Za-z0-9._:-]{1,128}$', body['external_id'])
    assert body['severity'] in dp.SEVERITIES and re.match(r'^[a-z_]{1,32}$', body['status'])
    assert re.match(r'^/[A-Za-z0-9/_.-]{0,200}$', body['path']) and '..' not in body['path']
    assert body['detail']['evidence_source'] == 'product_event' and body['detail']['synthetic'] is True
    assert dp.assets_alert_signal(_alert(status='false_positive'))['status'] == 'dismissed'


@pytest.mark.parametrize(
    ('severity', 'status', 'risk'),
    [('critical', 'open', 'critical'), ('high', 'investigating', 'elevated'), ('critical', 'resolved', 'normal'), ('medium', 'open', None)],
)
def test_vault_risk_follows_the_alert(severity, status, risk):
    signals = dp.vault_risk_signals(_alert(severity=severity, status=status), PLATFORM)
    assert [s['risk_state'] for s in signals] == ([risk] if risk else [])
    for signal in signals:
        assert (signal['platform_organization_id'], signal['network'], signal['subject_address']) == (PLATFORM, 'base-sepolia', CONTRACT)
        assert len(signal['external_id']) <= 128


def test_coverage_claims_only_what_guard_monitors():
    mint, treasury = '0x' + '11' * 20, '0x' + '22' * 20
    completed = [{'event_id': 'evt_x', 'payload': {'asset_id': 'a1', 'metadata': {'token_contract': CONTRACT, 'mint_authority': mint, 'issuer_treasury': treasury}}}]
    none = dp.assets_coverage_signals('org', completed, monitored_contracts=set(), monitored_wallets=set())
    asset = next(s for s in none if s['asset_id'] == 'a1')['detail']
    assert (asset['contract_monitoring'], asset['wallet_monitoring']) == ('not_configured', 'not_configured')
    partial = dp.assets_coverage_signals('org', completed, monitored_contracts={CONTRACT}, monitored_wallets={mint})
    asset = next(s for s in partial if s['asset_id'] == 'a1')['detail']
    assert (asset['contract_monitoring'], asset['wallet_monitoring']) == ('active', 'not_configured'), 'one of two wallets is not enough'
    org = next(s for s in partial if s['asset_id'] is None)['detail']
    assert org['contract_monitoring'] == 'active' and org['wallet_monitoring'] == 'not_configured'
    vault = dp.vault_coverage_signal(PLATFORM, wallets=set(), contracts=set(), monitored_wallets=set(), monitored_contracts=set())
    assert (vault['wallet_monitoring'], vault['contract_monitoring']) == ('not_connected', 'not_connected'), 'nothing seen: nothing claimed'


def test_signals_go_only_to_https_endpoints(monkeypatch):
    producer = dp.PRODUCERS['decoda-assets']
    monkeypatch.setenv(producer.signals_secret_env, 's' * 32)
    for url, ok in [
        ('https://assets.example/v1/integrations/rwa-guard/signals', True),
        ('http://assets.example/v1/integrations/rwa-guard/signals', False),
        ('http://localhost:8000/v1/integrations/rwa-guard/signals', True),
        ('ftp://assets.example/x', False),
    ]:
        monkeypatch.setenv(producer.signals_url_env, url)
        assert (dp.signals_endpoint(producer) is not None) is ok, url
    monkeypatch.delenv(producer.signals_secret_env)
    assert dp.signals_endpoint(producer) is None


def test_each_signal_version_has_its_own_stable_event_id():
    first, again, second = dp.signal_event_id('s1', 1), dp.signal_event_id('s1', 1), dp.signal_event_id('s1', 2)
    assert first == again != second
    import re

    assert re.match(r'^[A-Za-z0-9._:-]{8,128}$', first)


# ── The route ───────────────────────────────────────────────────────────────


def test_the_route_answers_with_the_intake_status_and_drains_after_the_response(monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    drained: list[str | None] = []
    monkeypatch.setattr(api_main.decoda_products, 'process_event', lambda raw, headers: dp.IntakeResult(202, {'accepted': True}, workspace_id='ws-1'))
    monkeypatch.setattr(api_main.decoda_products, 'drain_safely', lambda workspace_id=None: drained.append(workspace_id))
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.post('/api/integrations/decoda/events', content=b'{}', headers={'Content-Type': 'application/json'})
    assert (response.status_code, response.json()) == (202, {'accepted': True})
    assert drained == ['ws-1']
    monkeypatch.setattr(
        api_main.decoda_products, 'process_event', lambda raw, headers: dp.IntakeResult(401, {'accepted': False, 'code': 'SIGNATURE_INVALID'})
    )
    refused = client.post('/api/integrations/decoda/events', content=b'{}', headers={'Content-Type': 'application/json'})
    assert refused.status_code == 401 and drained == ['ws-1'], 'nothing is drained for a refusal'


def test_the_body_limit_holds_while_streaming(monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    def never(raw, headers):
        raise AssertionError('an oversized body is refused before intake')

    monkeypatch.setattr(api_main.decoda_products, 'process_event', never)
    client = TestClient(api_main.app, raise_server_exceptions=False)

    def chunks():  # chunked transfer encoding: no Content-Length for the size middleware to check
        for _ in range(dp.MAX_BODY_BYTES // 4096 + 2):
            yield b'x' * 4096

    streamed = client.post('/api/integrations/decoda/events', content=chunks(), headers={'Content-Type': 'application/json'})
    assert (streamed.status_code, streamed.json()['code']) == (413, 'EVENT_TOO_LARGE')
    declared = client.post('/api/integrations/decoda/events', content=b'x' * (dp.MAX_BODY_BYTES + 1), headers={'Content-Type': 'application/json'})
    assert (declared.status_code, declared.json()['code']) == (413, 'EVENT_TOO_LARGE')


def test_the_routes_go_through_the_pilot_entry_points():
    from services.api.app import main as api_main
    from services.api.app import pilot

    for name in ('ingest_decoda_product_event', 'get_decoda_products_status', 'set_decoda_product_link', 'retry_decoda_product_signals'):
        assert getattr(api_main, name) is getattr(pilot, name), name
    assert not hasattr(dp, 'http_status') and not hasattr(dp, 'http_set_link'), 'no second API layer in the domain module'
