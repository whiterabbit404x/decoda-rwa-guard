"""Decoda product events (Assets, Vault) end to end against real PostgreSQL.

One ordered scenario through the real intake, routing, detectors, alert/incident
creation, signal reconciliation and delivery, with two tenants:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_decoda_products_postgres.py -q

Skipped entirely when that DSN is absent, so the default suite stays hermetic.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

_DSN = os.environ.get('DECODA_MIGRATION_TEST_DSN')


def _real_psycopg():
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _DSN, reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database)'),
]

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
_PASSWORD = 'Str0ng!Passw0rd#2026'
ASSETS_SECRET = 'test-assets-to-guard-secret-0123456789abcdef'
VAULT_SECRET = 'test-vault-to-guard-secret-0123456789abcdef'
ASSETS_SIGNALS_SECRET = 'test-guard-to-assets-secret-0123456789abcdef'
VAULT_SIGNALS_SECRET = 'test-guard-to-vault-secret-0123456789abcdef'
ENV = {
    'DECODA_ASSETS_INBOUND_SECRET': ASSETS_SECRET,
    'DECODA_VAULT_INBOUND_SECRET': VAULT_SECRET,
    'DECODA_ASSETS_SIGNALS_URL': 'https://assets.test/v1/integrations/rwa-guard/signals',
    'DECODA_ASSETS_SIGNALS_SECRET': ASSETS_SIGNALS_SECRET,
    'DECODA_VAULT_SIGNALS_URL': 'https://vault.test/v1/integrations/rwa-guard/signals',
    'DECODA_VAULT_SIGNALS_SECRET': VAULT_SIGNALS_SECRET,
}
CONTRACT = '0x' + 'ab' * 20
MINT = '0x' + '11' * 20
TREASURY = '0x' + '22' * 20


def _request(token: str, workspace_id: str | None = None) -> SimpleNamespace:
    headers = {'authorization': f'Bearer {token}', 'user-agent': 'decoda-products-test'}
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    return SimpleNamespace(
        headers=headers, client=SimpleNamespace(host='127.0.0.1'), scope={'path': '/test', 'type': 'http'}, method='POST', query_params={}
    )


@pytest.fixture(scope='module')
def live():
    previous = {key: os.environ.get(key) for key in ('DATABASE_URL', 'LIVE_MODE_ENABLED', 'APP_MODE', 'EMAIL_PROVIDER', *ENV)}
    os.environ.update({'DATABASE_URL': _DSN, 'LIVE_MODE_ENABLED': 'true', 'APP_MODE': 'live', 'EMAIL_PROVIDER': 'console', **ENV})
    os.environ.setdefault('AUTH_TOKEN_SECRET', 'x' * 48)
    os.environ.setdefault('TOKEN_SECRET', 'x' * 48)
    from psycopg.rows import dict_row

    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
        for path in sorted(_MIGRATIONS.glob('*.sql')):
            connection.execute(path.read_text())
    with psycopg.connect(_DSN, autocommit=True, row_factory=dict_row) as connection:
        yield connection
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')


def _sign_up(connection, *, label: str, email: str, mfa: bool) -> dict[str, str]:
    from services.api.app import pilot

    result = pilot.signup_user(
        {'email': email, 'password': _PASSWORD, 'full_name': label, 'workspace_name': f'{label} Workspace'}, _request('bootstrap')
    )
    user_id = result['user']['id']
    connection.execute('UPDATE users SET email_verified_at = NOW() WHERE id = %s', (user_id,))
    token = pilot.create_access_token(user_id, 1)
    connection.execute(
        'INSERT INTO auth_sessions (id, user_id, session_token_hash, expires_at, created_at, updated_at) VALUES (%s, %s, %s, %s, NOW(), NOW())',
        (str(uuid.uuid4()), user_id, pilot._auth_token_hash(token), datetime.now(timezone.utc) + timedelta(hours=8)),
    )
    if mfa:  # an enrolled account whose session completed its second factor (Pilot requires MFA for every member)
        connection.execute('UPDATE users SET mfa_enabled_at = NOW() WHERE id = %s', (user_id,))
        connection.execute(
            """UPDATE auth_sessions SET mfa_verified_at = NOW(), authentication_methods = '["password", "totp"]'::jsonb WHERE user_id = %s""",
            (user_id,),
        )
    # Signing up creates an account only; an approved tenant gets its Pilot organization and first workspace here.
    pilot.provision_pilot_organization(connection, user_id=user_id, organization_name=label)
    workspace_id = str(connection.execute('SELECT current_workspace_id AS w FROM users WHERE id = %s', (user_id,)).fetchone()['w'])
    organization_id = str(connection.execute('SELECT organization_id AS o FROM workspaces WHERE id = %s', (workspace_id,)).fetchone()['o'])
    platform_id = str(uuid.uuid4())
    connection.execute('UPDATE organizations SET platform_organization_id = %s WHERE id = %s', (platform_id, organization_id))
    return {
        'user_id': user_id,
        'token': token,
        'workspace_id': workspace_id,
        'organization_id': organization_id,
        'platform_id': platform_id,
        'assets_org': str(uuid.uuid4()),
        'vault_org': str(uuid.uuid4()),
    }


@pytest.fixture(scope='module')
def tenants(live):
    return {
        'a': _sign_up(live, label='ABC Tokenization', email='owner@abc-products.example', mfa=True),
        'b': _sign_up(live, label='XYZ Capital', email='owner@xyz-products.example', mfa=False),
    }


def _assets_event(tenant: dict, event_type: str, *, severity: str = 'medium', metadata: dict | None = None, asset_id: str | None = None):
    event_id = f'evt_{uuid.uuid4().hex}'
    return {
        'eventId': event_id,
        'eventType': event_type,
        'version': 1,
        'producer': 'decoda-assets',
        'environment': 'testnet',
        'tenant': {'assetsOrgId': tenant['assets_org'], 'platformOrganizationId': tenant['platform_id']},
        'entityType': 'asset',
        'entityId': asset_id,
        'occurredAt': datetime.now(timezone.utc).isoformat(),
        'payload': {
            'event_type': event_type,
            'organization_id': tenant['assets_org'],
            'asset_id': asset_id,
            'severity_hint': severity,
            'metadata': metadata or {},
        },
        'provenance': {'sourceSystem': 'decoda-assets', 'synthetic': True},
    }


def _send(envelope: dict, *, secret: str = ASSETS_SECRET, producer: str = 'decoda-assets', ts: int | None = None, tamper: bool = False):
    from services.api.app import decoda_products

    raw = json.dumps(envelope).encode()
    timestamp = int(ts if ts is not None else time.time())
    headers = {
        'X-Decoda-Producer': producer,
        'X-Decoda-Timestamp': str(timestamp),
        'X-Decoda-Signature': decoda_products.signature(secret, raw, timestamp),
        'X-Decoda-Event-Id': envelope['eventId'],
    }
    return decoda_products.process_event(raw + (b' ' if tamper else b''), headers)


class Receiver:
    """Decoda Assets' and Vault's signal endpoints: verifies each request like they do, records the body."""

    def __init__(self):
        self.received: list[tuple[str, dict, dict]] = []
        self.status = {'assets.test': 202, 'vault.test': 202}
        self.intercept = None  # (host, body) -> a status for this request, or None

    def client(self):
        import httpx

        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        import httpx

        from services.api.app import decoda_products

        host = request.url.host
        secret = ASSETS_SIGNALS_SECRET if host == 'assets.test' else VAULT_SIGNALS_SECRET
        event_id = decoda_products.verify_signature(secret, request.content, dict(request.headers))
        assert request.headers['x-decoda-producer'] == 'decoda-rwa-guard'
        body = json.loads(request.content)
        assert body.get('event_id') == event_id, 'the event id is inside the signed body: a replay cannot change it'
        self.received.append((host, body, {'event_id': event_id}))
        status = self.intercept(host, body) if self.intercept else None
        return httpx.Response(status or self.status[host], json={'accepted': True})

    def take(self) -> list[tuple[str, dict, dict]]:
        out, self.received = self.received, []
        return out


def _drain(receiver: Receiver, workspace_id: str | None = None) -> list[tuple[str, dict, dict]]:
    from services.api.app import decoda_products

    with receiver.client() as client:
        decoda_products.drain(workspace_id, client=client)
    return receiver.take()


def _status(live, tenant: dict, producer: str) -> dict:
    from services.api.app import decoda_products

    view = decoda_products.status_view(live, workspace_id=tenant['workspace_id'], user_id=tenant['user_id'], workspace_admin=True)
    return next(p for p in view['products'] if p['producer'] == producer)


receiver = Receiver()


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_refusals_answer_with_the_status_the_sender_acts_on(live, tenants):
    a = tenants['a']
    envelope = _assets_event(a, 'assets.asset.created')
    assert _send(envelope, secret='wrong-secret-0123456789abcdef').status == 401
    assert _send(envelope, ts=int(time.time()) - 600).status == 401
    assert _send(envelope, tamper=True).status == 401
    unknown = _assets_event({**a, 'platform_id': str(uuid.uuid4())}, 'assets.asset.created')
    refused = _send(unknown)
    assert (refused.status, refused.body['code']) == (422, 'TENANT_NOT_FOUND')
    mainnet = {**_assets_event(a, 'assets.asset.created'), 'environment': 'mainnet'}
    assert (_send(mainnet).status, _send(mainnet).body['code']) == (422, 'ENVIRONMENT_NOT_SUPPORTED')
    wrong_prefix = _assets_event(a, 'vault.transaction.blocked')
    assert _send(wrong_prefix).body['code'] == 'UNSUPPORTED_EVENT_TYPE'
    os.environ.pop('DECODA_ASSETS_INBOUND_SECRET')
    try:
        assert _send(_assets_event(a, 'assets.asset.created')).status == 503, 'not configured: the sender retries'
    finally:
        os.environ['DECODA_ASSETS_INBOUND_SECRET'] = ASSETS_SECRET
    assert live.execute('SELECT count(*) AS n FROM decoda_product_events').fetchone()['n'] == 0, 'nothing refused was stored'


def test_an_event_for_an_organization_without_a_link_is_kept_unrouted(live, tenants):
    a = tenants['a']
    result = _send(_assets_event(a, 'assets.ownership.mismatch', severity='critical', metadata={'token_contract': CONTRACT}))
    assert (result.status, result.body) == (202, {'accepted': True, 'duplicate': False, 'routed': False, 'detection': 'none'})
    assert live.execute('SELECT count(*) AS n FROM alerts').fetchone()['n'] == 0, 'unrouted: no detection'
    status = _status(live, a, 'decoda-assets')
    assert (status['state'], status['unrouted_events']) == ('not_linked', 1)


# ── Routing ─────────────────────────────────────────────────────────────────


def test_only_an_admin_of_the_organization_routes_its_products(live, tenants):
    from fastapi import HTTPException

    from services.api.app import decoda_products, pilot

    a, b = tenants['a'], tenants['b']
    with pytest.raises(HTTPException) as foreign:
        pilot.set_decoda_product_link(_request(b['token'], a['workspace_id']), 'decoda-assets', link=True)
    assert foreign.value.status_code in (403, 404), 'another tenant cannot route into this workspace'
    with pytest.raises(HTTPException) as no_mfa:
        pilot.set_decoda_product_link(_request(b['token'], b['workspace_id']), 'decoda-assets', link=True)
    assert no_mfa.value.status_code == 403 and no_mfa.value.detail['code'] == 'MFA_ENROLLMENT_REQUIRED', 'MFA is still required'
    with pytest.raises(decoda_products.IntakeError) as stranger:
        decoda_products.link_producer(live, workspace_id=a['workspace_id'], user_id=b['user_id'], producer='decoda-assets')
    assert stranger.value.status == 403
    for producer in ('decoda-assets', 'decoda-vault'):
        linked = pilot.set_decoda_product_link(_request(a['token'], a['workspace_id']), producer, link=True)
        assert linked['linked'] is True
    assert _status(live, a, 'decoda-assets')['state'] == 'awaiting_first_event'
    audit = live.execute("SELECT count(*) AS n FROM audit_logs WHERE action = 'decoda_product.linked'").fetchone()['n']
    assert audit == 2


# ── Detection ───────────────────────────────────────────────────────────────


def test_a_critical_supply_mismatch_opens_a_labelled_alert_and_incident(live, tenants):
    a = tenants['a']
    asset_id = str(uuid.uuid4())
    envelope = _assets_event(
        a,
        'assets.ownership.mismatch',
        severity='critical',
        asset_id=asset_id,
        metadata={
            'headline': 'On-chain supply differs from the issuer register',
            'scope': 'supply',
            'network': 'base_sepolia',
            'token_contract': CONTRACT,
            'expected_supply': '50000',
            'onchain_supply': '51000',
            'supply_difference': '1000',
        },
    )
    result = _send(envelope)
    assert (result.status, result.body['detection'], result.workspace_id) == (202, 'alert_and_incident', a['workspace_id'])
    alert = live.execute('SELECT * FROM alerts WHERE workspace_id = %s', (a['workspace_id'],)).fetchone()
    assert alert['source'] == 'decoda_product_event' and alert['severity'] == 'critical' and alert['status'] == 'open'
    assert alert['title'].startswith('Decoda Assets (testnet product event):')
    assert alert['user_id'] == uuid.UUID(a['user_id']), "the linking admin is the alert's user"
    assert alert['payload']['evidence_source'] == 'product_event' and alert['payload']['synthetic'] is True
    assert alert['payload']['subjects'] == [{'address': CONTRACT, 'network': 'base-sepolia'}]
    incident = live.execute('SELECT * FROM incidents WHERE id = %s', (alert['incident_id'],)).fetchone()
    assert incident['severity'] == 'critical' and incident['payload']['evidence'][0]['event_id'] == envelope['eventId']
    assert incident['payload']['evidence'][0]['label'] == 'Testnet product event'
    timeline = live.execute('SELECT event_type FROM incident_timeline WHERE incident_id = %s', (incident['id'],)).fetchall()
    assert [r['event_type'] for r in timeline] == ['incident.auto_created']
    # Never live telemetry: nothing reaches the monitoring tables.
    for table in ('telemetry_events', 'detection_events'):
        assert live.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] == 0, table
    replay = _send(envelope)
    assert replay.body == {'accepted': True, 'duplicate': True}
    assert live.execute('SELECT count(*) AS n FROM alerts').fetchone()['n'] == 1, 'a replay is never processed twice'
    assert _status(live, a, 'decoda-assets')['state'] == 'receiving'


# ── Signals back ────────────────────────────────────────────────────────────


def test_signals_reach_assets_and_vault_and_only_changes_are_sent_again(live, tenants):
    a = tenants['a']
    sent = _drain(receiver, a['workspace_id'])
    by_kind = {(host, body['kind']): body for host, body, _ in sent}
    alert = by_kind[('assets.test', 'alert')]
    assert alert['organization_id'] == a['assets_org'] and alert['severity'] == 'critical' and alert['status'] == 'open'
    assert alert['path'].startswith('/incidents/') and alert['detail']['evidence_source'] == 'product_event'
    assert by_kind[('assets.test', 'incident')]['status'] == 'open'
    risk = by_kind[('vault.test', 'risk')]
    assert (risk['platform_organization_id'], risk['network'], risk['subject_address'], risk['risk_state']) == (
        a['platform_id'],
        'base-sepolia',
        CONTRACT,
        'critical',
    )
    coverage = by_kind[('vault.test', 'coverage')]
    assert coverage['wallet_monitoring'] == 'not_connected', 'no Vault wallet seen yet: nothing claimed'
    assert _drain(receiver, a['workspace_id']) == [], 'unchanged state is not sent again'

    live.execute("UPDATE alerts SET status = 'resolved', updated_at = NOW() WHERE workspace_id = %s", (a['workspace_id'],))
    resent = _drain(receiver, a['workspace_id'])
    kinds = {(host, body['kind']): (body, meta) for host, body, meta in resent}
    assert kinds[('vault.test', 'risk')][0]['risk_state'] == 'normal', 'resolving the alert clears the risk in Vault'
    assert kinds[('vault.test', 'risk')][1]['event_id'] != sent[[b['kind'] for _, b, _ in sent].index('risk')][2]['event_id']
    assert kinds[('assets.test', 'alert')][0]['status'] == 'resolved'


def test_coverage_is_active_only_for_what_guard_actually_monitors(live, tenants):
    a = tenants['a']
    asset_id = str(uuid.uuid4())
    completed = _assets_event(
        a,
        'assets.tokenization.completed',
        asset_id=asset_id,
        metadata={'token_contract': CONTRACT, 'network': 'base_sepolia', 'mint_authority': MINT, 'issuer_treasury': TREASURY},
    )
    assert _send(completed).body['detection'] == 'coverage'
    coverage = {b['external_id']: b for h, b, _ in _drain(receiver, a['workspace_id']) if b['kind'] == 'coverage' and h == 'assets.test'}
    asset = coverage[f'guard-asset-coverage-{asset_id}']['detail']
    assert (asset['asset_monitoring'], asset['contract_monitoring'], asset['wallet_monitoring']) == ('active', 'not_configured', 'not_configured')
    # A Guard monitoring target for the contract makes contract monitoring active.
    live.execute(
        """
        INSERT INTO targets (id, workspace_id, name, target_type, chain_network, contract_identifier, created_by_user_id, updated_by_user_id)
        VALUES (%s, %s, 'Token', 'contract', 'base-sepolia', %s, %s, %s)
        """,
        (str(uuid.uuid4()), a['workspace_id'], CONTRACT, a['user_id'], a['user_id']),
    )
    again = {b['external_id']: b for h, b, _ in _drain(receiver, a['workspace_id']) if b['kind'] == 'coverage' and h == 'assets.test'}
    assert again[f'guard-asset-coverage-{asset_id}']['detail']['contract_monitoring'] == 'active'


def test_failed_deliveries_retry_and_rejected_ones_stop(live, tenants):
    from services.api.app import decoda_products

    a = tenants['a']
    receiver.status['assets.test'] = 503
    _send(_assets_event(a, 'assets.settlement.failed', metadata={'reason': 'token leg reverted'}))
    _drain(receiver, a['workspace_id'])
    failed = live.execute(
        "SELECT status, attempts, next_attempt_at > NOW() AS later FROM decoda_product_signals WHERE destination = 'decoda-assets' AND kind = 'alert' AND status <> 'delivered'"
    ).fetchall()
    assert failed and all((r['status'], r['attempts'], r['later']) == ('failed', 1, True) for r in failed)
    receiver.status['assets.test'] = 422
    live.execute("UPDATE decoda_product_signals SET next_attempt_at = NOW() WHERE status = 'failed'")
    _drain(receiver, a['workspace_id'])
    assert {r['status'] for r in live.execute("SELECT status FROM decoda_product_signals WHERE attempts > 0").fetchall()} == {'dead'}
    receiver.status['assets.test'] = 202
    assert decoda_products.retry_signals(live, workspace_id=a['workspace_id']) >= 1
    _drain(receiver, a['workspace_id'])
    assert live.execute("SELECT count(*) AS n FROM decoda_product_signals WHERE status = 'dead'").fetchone()['n'] == 0


def test_a_rejected_old_version_never_buries_the_newer_one(live, tenants):
    from services.api.app import decoda_products

    a = tenants['a']
    _send(_assets_event(a, 'assets.operation.blocked', metadata={'reason': 'policy: amount above limit'}))
    alert_id = str(live.execute('SELECT id FROM alerts WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1', (a['workspace_id'],)).fetchone()['id'])
    external_id = f'guard-alert-{alert_id}'

    def resolve_while_in_flight(host, body):
        if host != 'assets.test' or body.get('external_id') != external_id or body['status'] != 'open':
            return None
        # The alert is resolved while its 'open' version is being sent; that send is then rejected.
        live.execute("UPDATE alerts SET status = 'resolved', updated_at = NOW() WHERE id = %s", (alert_id,))
        decoda_products.refresh_signals(live, a['workspace_id'])
        return 422

    receiver.intercept = resolve_while_in_flight
    try:
        first = _drain(receiver, a['workspace_id'])
    finally:
        receiver.intercept = None
    assert [b['status'] for h, b, _ in first if b.get('external_id') == external_id] == ['open']
    row = live.execute(
        "SELECT status, version, attempts, last_error FROM decoda_product_signals WHERE destination = 'decoda-assets' AND external_id = %s",
        (external_id,),
    ).fetchone()
    assert (row['status'], row['version'], row['attempts'], row['last_error']) == ('pending', 2, 0, None), 'the newer version is not dead'
    second = _drain(receiver, a['workspace_id'])
    assert [b['status'] for h, b, _ in second if b.get('external_id') == external_id] == ['resolved']
    assert live.execute('SELECT status FROM decoda_product_signals WHERE external_id = %s', (external_id,)).fetchone()['status'] == 'delivered'


def test_tenants_never_see_each_others_product_events(live, tenants):
    a, b = tenants['a'], tenants['b']
    _send(_assets_event(b, 'assets.nav.deviation', severity='high', metadata={'deviation_pct': '7.5'}))
    b_status = _status(live, b, 'decoda-assets')
    assert (b_status['state'], b_status['unrouted_events'], b_status['events_received']) == ('not_linked', 1, 0)
    a_status = _status(live, a, 'decoda-assets')
    assert a_status['unrouted_events'] == 1, "B's unrouted event is not counted in A's organization"
    assert live.execute('SELECT count(*) AS n FROM alerts WHERE workspace_id = %s', (b['workspace_id'],)).fetchone()['n'] == 0
    assert all(body.get('organization_id') != b['assets_org'] for _, body, _ in _drain(receiver))


def test_a_vault_abnormal_transfer_marks_the_destination_elevated_in_vault(live, tenants):
    a = tenants['a']
    destination = '0x' + '33' * 20
    envelope = {
        'eventId': f'evt_{uuid.uuid4().hex}',
        'eventType': 'vault.transaction.abnormal_transfer',
        'version': 1,
        'producer': 'decoda-vault',
        'environment': 'testnet',
        'tenant': {'vaultOrgId': a['vault_org'], 'platformOrganizationId': a['platform_id']},
        'payload': {'network': 'base-sepolia', 'wallet': MINT, 'wallet_name': 'Treasury', 'destination': destination, 'tx_type': 'transfer'},
        'provenance': {'sourceSystem': 'decoda-vault'},
    }
    assert _send(envelope, secret=VAULT_SECRET, producer='decoda-vault').body['detection'] == 'alert'
    alert = live.execute("SELECT title, payload FROM alerts WHERE alert_type = 'abnormal_transfer'").fetchone()
    assert alert['title'].startswith('Decoda Vault (testnet product event):') and alert['payload']['synthetic'] is False
    sent = _drain(receiver, a['workspace_id'])
    risk = [b for h, b, _ in sent if h == 'vault.test' and b['kind'] == 'risk' and b['subject_address'] == destination]
    assert [r['risk_state'] for r in risk] == ['elevated']
    coverage = [b for h, b, _ in sent if h == 'vault.test' and b['kind'] == 'coverage']
    assert coverage and coverage[0]['wallet_monitoring'] == 'inactive', 'the Vault wallet seen has no Guard target'
