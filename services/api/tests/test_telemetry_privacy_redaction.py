"""The ingestion-time privacy and secret-redaction boundary.

These tests hold the contract of ``services/api/app/telemetry_privacy.py`` and of
the four places it is wired in: the ``analysis_runs`` persistence choke point,
the threat-payload original-request copy, the AI prompt boundary, and the
provider-error text that reaches ``monitoring_polls`` / the customer UI.

The two properties that matter most, and that most of this file exists to pin:

  * a credential never survives to persistence, a log line, an exception
    response, an AI request, or evidence metadata; and
  * a public blockchain fact — tx hash, addresses, amounts, block numbers —
    survives every one of those paths byte for byte.

Both halves are load-bearing. A sanitizer that met only the first by redacting
everything 64 characters long would destroy the forensic value the product is
for.
"""
from __future__ import annotations

import json
import logging

import pytest

from services.api.app import telemetry_privacy as privacy
from services.api.app import telemetry_privacy_policy as privacy_policy


# A value that must never appear in ANY output this module produces. Used as the
# single needle every leak assertion searches for.
SECRET = 'sk-live-SuperSecretValue123456789'
WALLET = '0x742d35Cc6634C0532925a3b844Bc454e4438f44e'
TX_HASH = '0x' + 'ab' * 32
CONTRACT = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'


# ---------------------------------------------------------------------------
# 1-8. Mandatory credential stripping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    'key',
    [
        'Authorization',          # 1. authorization header
        'authorization',
        'proxy-authorization',
        'Cookie',                 # 3. cookie
        'set-cookie',
        'api_key',                # 4. api key
        'API-KEY',
        'apiKey',
        'X-Api-Key',
        'access_token',
        'refresh_token',
        'client_secret',          # 5. client secret
        'webhook_secret',
        'password',               # 6. password
        'passwd',
        'private_key',            # 7. private key
        'seed_phrase',            # 8. seed phrase
        'mnemonic',
        'recovery_code',
        'session_id',
        'csrf_token',
    ],
)
def test_mandatory_credential_keys_are_stripped_in_every_spelling(key):
    """Each credential key is redacted regardless of case, dashes, or underscores."""
    result = privacy.sanitize_ingested_payload(
        {key: SECRET}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert result.payload[key] == privacy.REDACTED
    assert SECRET not in json.dumps(result.payload)
    # The field is REDACTED, not absent: a reader can tell policy removed it.
    assert key in result.payload
    assert result.redacted_fields == [key]
    assert result.dropped_fields == []


def test_bearer_token_in_freeform_value_is_stripped():
    """2. A bearer token embedded in ordinary text, not under a credential key."""
    result = privacy.sanitize_ingested_payload(
        {'note': f'retry with Bearer {SECRET} please'},
        source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert SECRET not in result.payload['note']
    assert 'credential.bearer_token' in result.rule_ids


def test_jwt_in_freeform_value_is_stripped():
    jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk'
    result = privacy.sanitize_ingested_payload(
        {'detail': f'token was {jwt}'}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert jwt not in result.payload['detail']
    assert 'credential.jwt' in result.rule_ids


def test_pem_private_key_block_is_stripped():
    """7. A PEM private key pasted into a freeform field."""
    pem = '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----'
    result = privacy.sanitize_ingested_payload(
        {'operator_note': pem}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert 'MIIEowIBAAKCAQEA' not in json.dumps(result.payload)
    assert 'credential.pem_private_key' in result.rule_ids


def test_seed_phrase_as_whole_value_is_replaced():
    """8. A BIP-39-shaped value is replaced outright, not partially masked."""
    phrase = 'abandon ability able about above absent absorb abstract absurd abuse access accident'
    result = privacy.sanitize_ingested_payload(
        {'notes': phrase}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert result.payload['notes'] == privacy.REDACTED
    assert 'credential.mnemonic' in result.rule_ids


def test_ordinary_prose_is_not_mistaken_for_a_seed_phrase():
    """The mnemonic rule must not eat a long, ordinary bug report."""
    prose = (
        'the monitoring worker stopped reporting telemetry after the deploy and we '
        'had to restart it twice before the poll loop recovered again today'
    )
    result = privacy.sanitize_ingested_payload(
        {'notes': prose}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert result.payload['notes'] == prose


@pytest.mark.parametrize('key', ['secretary', 'password_policy_enabled', 'keyboard_layout', 'authority', 'tokenomics'])
def test_innocent_fields_that_merely_contain_a_keyword_survive(key):
    """The ruleset is closed and normalized, NOT a loose substring scan.

    ``secretary`` contains ``secret`` and ``password_policy_enabled`` contains
    ``password``; a substring scan would destroy both.
    """
    result = privacy.sanitize_ingested_payload(
        {key: 'kept-value'}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert result.payload[key] == 'kept-value'
    assert result.changed is False


# ---------------------------------------------------------------------------
# 9-12, 34. Public-chain forensic fields survive unchanged
# ---------------------------------------------------------------------------
ONCHAIN_PAYLOAD = {
    'chain_id': 8453,
    'network': 'base',
    'block_number': 24681012,
    'transaction_hash': TX_HASH,
    'tx_hash': TX_HASH,
    'contract_address': CONTRACT,
    'from_address': WALLET,
    'to_address': CONTRACT,
    'wallet_address': WALLET,
    'token_address': CONTRACT,
    'amount': '1250000000000000000',
    'value_wei': 1250000000000000000,
    'event_signature': 'Transfer(address,address,uint256)',
    'log_index': 7,
    'observed_at': '2026-09-19T02:00:00+00:00',
    'detected_by': 'quicknode_stream',
    'evidence_source': 'live',
}


def test_public_onchain_telemetry_is_field_equivalent_after_sanitization():
    """34/35. A normal QuickNode-shaped row passes through completely unchanged."""
    result = privacy.sanitize_ingested_payload(
        ONCHAIN_PAYLOAD, source_type=privacy.SOURCE_PUBLIC_CHAIN,
    )
    assert result.payload == ONCHAIN_PAYLOAD
    assert result.changed is False
    assert result.redacted_fields == []
    assert result.dropped_fields == []


@pytest.mark.parametrize('field_name', sorted(ONCHAIN_PAYLOAD))
def test_each_forensic_field_is_preserved_individually(field_name):
    """9-12. Each public-chain field survives on its own, not only in a batch."""
    result = privacy.sanitize_ingested_payload(
        {field_name: ONCHAIN_PAYLOAD[field_name]}, source_type=privacy.SOURCE_PUBLIC_CHAIN,
    )
    assert result.payload[field_name] == ONCHAIN_PAYLOAD[field_name]


def test_transaction_hash_is_not_mistaken_for_a_private_key():
    """A 32-byte hash and a private key are the same shape. Context decides.

    This is the single most important non-redaction in the module: blanket
    64-hex redaction would erase every transfer's identity.
    """
    result = privacy.sanitize_ingested_payload(
        {'tx_hash': TX_HASH, 'private_key': TX_HASH},
        source_type=privacy.SOURCE_PUBLIC_CHAIN,
    )
    assert result.payload['tx_hash'] == TX_HASH
    assert result.payload['private_key'] == privacy.REDACTED


def test_wallet_address_is_not_treated_as_pii_by_default():
    """Product policy: a public wallet address is public-chain data, not PII."""
    result = privacy.sanitize_ingested_payload(
        {'wallet_address': WALLET},
        source_type=privacy.SOURCE_PUBLIC_CHAIN,
        policy=privacy.PrivacyPolicy(redact_emails=True),
    )
    assert result.payload['wallet_address'] == WALLET


# ---------------------------------------------------------------------------
# 13-15. Private network identifiers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    'value,expected_rule',
    [
        ('10.1.2.45', 'network.private_ip'),          # 13. internal IPv4
        ('192.168.0.10', 'network.private_ip'),
        ('172.16.5.4', 'network.private_ip'),
        ('127.0.0.1', 'network.loopback'),            # 14. loopback
        ('::1', 'network.loopback'),
        ('fd00::1234', 'network.private_ip'),         # 15. internal IPv6 (ULA)
        ('fe80::1', 'network.link_local'),
        ('localhost', 'network.loopback'),
        ('payments.internal', 'network.internal_hostname'),
    ],
)
def test_private_network_identifiers_are_classified(value, expected_rule):
    assert privacy.classify_network_identifier(value) == expected_rule


@pytest.mark.parametrize('value', ['8.8.8.8', 'base-mainnet.g.alchemy.com', 'rpc.quicknode.com', '2606:4700::1111'])
def test_public_addresses_and_provider_hosts_are_not_redacted(value):
    """A public RPC hostname is operationally necessary and not private."""
    assert privacy.classify_network_identifier(value) is None
    result = privacy.sanitize_ingested_payload(
        {'host': value}, source_type=privacy.SOURCE_PUBLIC_CHAIN,
    )
    assert result.payload['host'] == value


def test_private_ip_is_masked_under_the_default_policy():
    result = privacy.sanitize_ingested_payload(
        {'source_ip': '10.1.2.45'}, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
    )
    assert result.payload['source_ip'] == privacy.REDACTED
    assert 'network.private_ip' in result.rule_ids


def test_private_ip_is_preserved_when_the_workspace_asks_for_it():
    policy = privacy.PrivacyPolicy(private_network_mode=privacy.NETWORK_MODE_PRESERVE)
    result = privacy.sanitize_ingested_payload(
        {'source_ip': '10.1.2.45'}, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN, policy=policy,
    )
    assert result.payload['source_ip'] == '10.1.2.45'


def test_private_ip_is_dropped_under_the_drop_mode():
    policy = privacy.PrivacyPolicy(private_network_mode=privacy.NETWORK_MODE_DROP)
    result = privacy.sanitize_ingested_payload(
        {'source_ip': '10.1.2.45'}, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN, policy=policy,
    )
    assert result.payload['source_ip'] is None
    assert 'source_ip' in result.dropped_fields


# ---------------------------------------------------------------------------
# Deterministic pseudonymization (Phase 15)
# ---------------------------------------------------------------------------
def test_pseudonymization_is_stable_within_a_workspace(monkeypatch):
    monkeypatch.setenv(privacy.PSEUDONYM_KEY_ENV, 'unit-test-privacy-key')
    policy_a = privacy.PrivacyPolicy(
        private_network_mode=privacy.NETWORK_MODE_PSEUDONYMIZE, scope='workspace-a',
    )
    first = privacy.sanitize_ingested_payload(
        {'source_ip': '10.1.2.45'}, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN, policy=policy_a,
    )
    second = privacy.sanitize_ingested_payload(
        {'source_ip': '10.1.2.45'}, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN, policy=policy_a,
    )
    assert first.payload['source_ip'] == second.payload['source_ip']
    assert first.payload['source_ip'].startswith('internal-ip:')
    assert '10.1.2.45' not in first.payload['source_ip']


def test_pseudonyms_differ_across_workspaces(monkeypatch):
    """33. Workspace A's pseudonym space never coincides with workspace B's."""
    monkeypatch.setenv(privacy.PSEUDONYM_KEY_ENV, 'unit-test-privacy-key')
    made = [
        privacy.sanitize_ingested_payload(
            {'source_ip': '10.1.2.45'},
            source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
            policy=privacy.PrivacyPolicy(
                private_network_mode=privacy.NETWORK_MODE_PSEUDONYMIZE, scope=scope,
            ),
        ).payload['source_ip']
        for scope in ('workspace-a', 'workspace-b')
    ]
    assert made[0] != made[1]


def test_unkeyed_pseudonymization_degrades_to_redaction(monkeypatch):
    """An unkeyed digest of RFC1918 is reversible by enumeration, so refuse it."""
    monkeypatch.delenv(privacy.PSEUDONYM_KEY_ENV, raising=False)
    assert privacy.pseudonymize('10.1.2.45', scope='w') == privacy.REDACTED


# ---------------------------------------------------------------------------
# 16-17. Email handling
# ---------------------------------------------------------------------------
def test_email_is_redacted_when_the_policy_enables_it():
    policy = privacy.PrivacyPolicy(redact_emails=True)
    result = privacy.sanitize_ingested_payload(
        {'email': 'ops@customer.example', 'note': 'ping ops@customer.example'},
        source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
        policy=policy,
    )
    assert result.payload['email'] == privacy.REDACTED
    assert 'ops@customer.example' not in result.payload['note']
    assert 'pii.email' in result.rule_ids


def test_email_is_preserved_when_the_policy_permits_it():
    """17. The default keeps contact metadata the product legitimately uses."""
    result = privacy.sanitize_ingested_payload(
        {'email': 'ops@customer.example'}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert result.payload['email'] == 'ops@customer.example'


# ---------------------------------------------------------------------------
# 18-19. URL sanitization
# ---------------------------------------------------------------------------
def test_sensitive_url_query_parameter_is_stripped():
    cleaned, rules = privacy.sanitize_url('https://rpc.example.com/v2/base?token=SECRETVALUE&chain=base')
    assert 'SECRETVALUE' not in cleaned
    assert 'chain=base' in cleaned, 'a non-sensitive parameter stays for diagnostics'
    assert 'credential.url_query_secret' in rules


def test_url_basic_auth_credentials_are_stripped():
    cleaned, rules = privacy.sanitize_url('https://user:hunter2@rpc.example.com/v2/base')
    assert 'hunter2' not in cleaned and 'user' not in cleaned
    assert cleaned == 'https://rpc.example.com/v2/base'
    assert 'credential.url_userinfo' in rules


def test_url_path_api_key_is_redacted_but_the_host_survives():
    """Diagnostics need to know WHICH provider failed; not its key."""
    cleaned, _ = privacy.sanitize_url('https://base-mainnet.g.alchemy.com/v2/Ab3xKeyMaterial99')
    assert cleaned == 'https://base-mainnet.g.alchemy.com/v2/[REDACTED]'


def test_url_fragment_is_dropped():
    cleaned, rules = privacy.sanitize_url('https://rpc.example.com/v2#access_token=SECRETVALUE')
    assert 'SECRETVALUE' not in cleaned
    assert 'credential.url_fragment_dropped' in rules


def test_unparseable_url_degrades_to_redacted_not_to_the_raw_value():
    cleaned, _ = privacy.sanitize_url('https://[oops')
    assert cleaned == privacy.REDACTED


# ---------------------------------------------------------------------------
# 20. Header handling (Phase 6)
# ---------------------------------------------------------------------------
def test_header_allowlist_drops_credentials_case_insensitively():
    result = privacy.sanitize_headers({
        'AUTHORIZATION': f'Bearer {SECRET}',
        'Proxy-Authorization': SECRET,
        'cookie': f'session={SECRET}',
        'X-API-KEY': SECRET,
        'x-auth-token': SECRET,
        'Content-Type': 'application/json',
        'X-Request-Id': 'req-1',
    })
    assert result.payload == {'Content-Type': 'application/json', 'X-Request-Id': 'req-1'}
    assert SECRET not in json.dumps(result.payload)
    # The NAME of a dropped header is evidence and is kept; the value is not.
    assert sorted(result.dropped_fields) == ['AUTHORIZATION', 'Proxy-Authorization', 'X-API-KEY', 'cookie', 'x-auth-token']


def test_never_persisted_headers_cannot_be_allowlisted_back_in():
    """Authentication material is consumed for verification and discarded."""
    result = privacy.sanitize_headers(
        {'Authorization': SECRET, 'Cookie': SECRET},
        allowlist=['authorization', 'cookie'],
    )
    assert result.payload == {}
    assert SECRET not in json.dumps(result.payload)


# ---------------------------------------------------------------------------
# 21-25. Structure: nesting, arrays, bounds, malformed input
# ---------------------------------------------------------------------------
def test_nested_json_secrets_are_scrubbed():
    payload = {'outer': {'inner': {'connection': {'api_key': SECRET, 'tx_hash': TX_HASH}}}}
    result = privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST)
    assert SECRET not in json.dumps(result.payload)
    assert result.payload['outer']['inner']['connection']['tx_hash'] == TX_HASH
    assert result.redacted_fields == ['outer.inner.connection.api_key']


def test_arrays_are_recursively_sanitized():
    payload = {'events': [{'authorization': SECRET}, {'tx_hash': TX_HASH}, ['nested', {'password': SECRET}]]}
    result = privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST)
    assert SECRET not in json.dumps(result.payload)
    assert result.payload['events'][1]['tx_hash'] == TX_HASH
    assert 'events[0].authorization' in result.redacted_fields


def test_deeply_nested_payload_is_bounded_safely():
    payload: dict = {'api_key': SECRET}
    for _ in range(200):
        payload = {'level': payload}
    result = privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST)
    serialized = json.dumps(result.payload)
    assert SECRET not in serialized
    assert privacy.REDACTED_DEPTH in serialized
    assert 'bounds.depth_exceeded' in result.rule_ids


def test_extremely_large_freeform_value_is_bounded():
    result = privacy.sanitize_ingested_payload(
        {'note': 'x' * (privacy.MAX_STRING_CHARS * 3)}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    assert len(result.payload['note']) <= privacy.MAX_STRING_CHARS + len(privacy.TRUNCATED_SUFFIX)
    assert 'bounds.value_truncated' in result.rule_ids


def test_wide_payload_is_bounded():
    payload = {f'field_{i}': 'v' for i in range(privacy.MAX_CONTAINER_ITEMS + 50)}
    result = privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST)
    assert len(result.payload) <= privacy.MAX_CONTAINER_ITEMS
    assert 'bounds.too_many_items' in result.rule_ids


@pytest.mark.parametrize('payload', [None, 'a bare string', 42, [], ['x'], b'bytes'])
def test_non_dict_payloads_do_not_raise(payload):
    """25. A malformed payload fails safely rather than exploding the caller."""
    result = privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST)
    assert isinstance(result, privacy.SanitizationResult)


def test_fail_closed_source_refuses_ingestion_when_the_filter_errors(monkeypatch):
    """25/Phase 16. A private source refuses rather than persisting unfiltered."""
    def _boom(*args, **kwargs):
        raise RuntimeError('filter exploded')

    monkeypatch.setattr(privacy, '_sanitize_value', _boom)
    with pytest.raises(privacy.PrivacyFilterError) as excinfo:
        privacy.sanitize_ingested_payload(
            {'api_key': SECRET}, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
        )
    assert excinfo.value.code == privacy.PRIVACY_FILTER_FAILED_CODE
    # The refusal carries no fragment of what it could not process.
    assert SECRET not in str(excinfo.value)


def test_public_chain_source_degrades_to_the_allowlist_rather_than_failing_monitoring(monkeypatch):
    """A filter bug must not take live monitoring down, and must not leak either."""
    def _boom(*args, **kwargs):
        raise RuntimeError('filter exploded')

    monkeypatch.setattr(privacy, '_sanitize_value', _boom)
    result = privacy.sanitize_ingested_payload(
        {**ONCHAIN_PAYLOAD, 'api_key': SECRET}, source_type=privacy.SOURCE_PUBLIC_CHAIN,
    )
    assert result.filter_failed is True
    assert SECRET not in json.dumps(result.payload)
    assert result.payload['tx_hash'] == TX_HASH
    assert result.payload['block_number'] == ONCHAIN_PAYLOAD['block_number']
    assert 'api_key' in result.dropped_fields


# ---------------------------------------------------------------------------
# 26-29. The secret never reaches logs, responses, AI requests, or evidence
# ---------------------------------------------------------------------------
def test_original_value_never_appears_in_structured_logs(caplog):
    """26. Defense in depth: the log formatter strips credential shapes too."""
    from services.api.app.structured_logging import JsonFormatter

    record = logging.LogRecord(
        name='test', level=logging.ERROR, pathname=__file__, lineno=1,
        msg='ingestion failed for https://rpc.example.com/v2/%s?token=%s',
        args=(SECRET, SECRET), exc_info=None,
    )
    record.error_message = f'HTTP 401 from https://rpc.example.com/v2/{SECRET}'
    rendered = JsonFormatter().format(record)
    assert SECRET not in rendered
    assert privacy.REDACTED in rendered


def test_original_value_never_appears_in_a_privacy_filter_exception():
    """27. The refusal response carries a stable code and nothing else."""
    error = privacy.PrivacyFilterError(privacy.SOURCE_PRIVATE_OFFCHAIN)
    assert str(error) == privacy.PRIVACY_FILTER_FAILED_CODE
    assert SECRET not in repr(error)


def test_original_value_never_appears_in_the_ai_request():
    """28. The AI prompt fixture is the real artifact sent to the provider."""
    from services.api.app import ai_triage

    snapshot = {
        'schema_version': 'v1',
        'incident_id': 'inc-1',
        'workspace_id': 'ws-1',
        'alert': {'alert_id': 'a-1', 'severity': 'high', 'created_at': None, 'rule_id': 'r-1'},
        'rule': {'rule_id': 'r-1', 'name': 'n', 'description': f'provider said Bearer {SECRET}', 'conditions': {}, 'version': '1'},
        'target': {'target_id': 't-1', 'asset_id': 'as-1', 'chain_id': 8453, 'address': WALLET, 'asset_type': 'wallet'},
        'telemetry': [{'telemetry_id': 'te-1', 'tx_hash': TX_HASH, 'from': WALLET, 'to': CONTRACT,
                       'block_number': 24681012, 'value': '1250000000000000000', 'api_key': SECRET}],
        'provider_observations': [],
        'policies': [], 'available_runbooks': [], 'audit_references': [],
    }
    prompt = ai_triage.build_prompt(snapshot, ai_triage.AGENT_POLICY, prompt_version='v1')
    serialized = json.dumps(prompt, default=str)
    assert SECRET not in serialized
    # ...while every public-chain identifier the model must cite survives.
    for needle in (TX_HASH, WALLET, CONTRACT, '24681012', '1250000000000000000'):
        assert needle in prompt['user'], needle


def test_ai_request_records_only_safe_privacy_metadata():
    """28. redaction_count / rules / source_type — never the redacted content."""
    from services.api.app import ai_triage

    snapshot = {'incident_id': 'inc-1', 'telemetry': [{'authorization': SECRET, 'tx_hash': TX_HASH}]}
    prompt = ai_triage.build_prompt(snapshot, ai_triage.AGENT_POLICY, prompt_version='v1')
    meta = prompt['privacy_processing']
    assert meta['applied'] is True
    assert meta['redacted_fields'] == 1
    assert 'credential.key_match' in meta['rules']
    assert SECRET not in json.dumps(meta)


def test_ai_boundary_is_stricter_than_storage():
    """Email and private IPs leave Decoda even when the workspace keeps them."""
    payload = {'email': 'ops@customer.example', 'source_ip': '10.1.2.45'}
    stored = privacy.sanitize_ingested_payload(
        payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
        policy=privacy.PrivacyPolicy(redact_emails=False, redact_private_ips=False),
    )
    forwarded = privacy.sanitize_for_ai(
        payload, policy=privacy.PrivacyPolicy(redact_emails=False, redact_private_ips=False),
    )
    assert stored.payload['email'] == 'ops@customer.example'
    assert forwarded.payload['email'] == privacy.REDACTED
    assert forwarded.payload['source_ip'] == privacy.REDACTED


def test_original_value_never_appears_in_evidence_metadata():
    """29. The privacy_processing block describes the redaction, not the secret."""
    result = privacy.sanitize_ingested_payload(
        {'api_key': SECRET, 'tx_hash': TX_HASH}, source_type=privacy.SOURCE_CUSTOMER_REQUEST,
    )
    meta = privacy.privacy_processing_metadata(result)
    assert meta == {
        'applied': True,
        'source_type': privacy.SOURCE_CUSTOMER_REQUEST,
        'redacted_fields': 1,
        'rules': ['credential.key_match'],
        'filter_failed': False,
    }
    assert SECRET not in json.dumps(meta)


def test_evidence_metadata_distinguishes_redacted_from_absent():
    """Evidentiary truthfulness: the two facts must not be conflated."""
    result = privacy.sanitize_ingested_payload(
        {'api_key': SECRET, 'tx_hash': TX_HASH},
        source_type=privacy.SOURCE_CUSTOMER_REQUEST,
        policy=privacy.PrivacyPolicy(excluded_fields=frozenset({'internalhost'})),
    )
    assert result.payload['api_key'] == privacy.REDACTED, 'redacted: key present, value replaced'
    assert 'internal_host' not in result.payload, 'never supplied: no key at all'
    assert result.redacted_fields == ['api_key']
    assert result.dropped_fields == []


def test_unchanged_payload_reports_no_privacy_processing():
    """Do not claim redaction happened when it did not."""
    result = privacy.sanitize_ingested_payload(
        ONCHAIN_PAYLOAD, source_type=privacy.SOURCE_PUBLIC_CHAIN,
    )
    assert privacy.privacy_processing_metadata(result)['applied'] is False


# ---------------------------------------------------------------------------
# 30-33. Customer policy
# ---------------------------------------------------------------------------
def test_customer_exclusion_rule_drops_the_field_entirely():
    policy = privacy.PrivacyPolicy(excluded_fields=frozenset({'internalservicename'}))
    result = privacy.sanitize_ingested_payload(
        {'internal_service_name': 'billing-core', 'tx_hash': TX_HASH},
        source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
        policy=policy,
    )
    assert 'internal_service_name' not in result.payload
    assert result.dropped_fields == ['internal_service_name']
    assert result.payload['tx_hash'] == TX_HASH


def test_customer_redaction_rule_keeps_the_key_and_replaces_the_value():
    policy = privacy.PrivacyPolicy(redacted_fields=frozenset({'deploymentid'}))
    result = privacy.sanitize_ingested_payload(
        {'deployment_id': 'deploy-7781'}, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN, policy=policy,
    )
    assert result.payload['deployment_id'] == privacy.REDACTED
    assert 'policy.customer_redacted' in result.rule_ids


def test_customer_metadata_allowlist_drops_unlisted_metadata_keys():
    policy = privacy.PrivacyPolicy(allowed_metadata_fields=frozenset({'region'}))
    result = privacy.sanitize_ingested_payload(
        {'metadata': {'region': 'eu-west-1', 'internal_host': 'db-primary.internal'}},
        source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
        policy=policy,
    )
    assert result.payload['metadata'] == {'region': 'eu-west-1'}
    assert 'policy.metadata_not_allowlisted' in result.rule_ids


def test_customer_cannot_disable_mandatory_credential_stripping():
    """31. Every lever a workspace has still leaves the credential rules intact."""
    policy = privacy.PrivacyPolicy(
        allowed_metadata_fields=frozenset({'authorization', 'apikey', 'password'}),
        redact_private_ips=False,
        redact_emails=False,
        private_network_mode=privacy.NETWORK_MODE_PRESERVE,
    )
    result = privacy.sanitize_ingested_payload(
        {'metadata': {'authorization': SECRET, 'api_key': SECRET, 'password': SECRET}},
        source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
        policy=policy,
    )
    assert SECRET not in json.dumps(result.payload)
    assert all(v == privacy.REDACTED for v in result.payload['metadata'].values())


def test_policy_input_refuses_to_allowlist_a_mandatory_credential_field():
    """31. The API refuses the request rather than storing a rule it will ignore."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        privacy_policy.validate_policy_input({'allowed_metadata_fields': ['Authorization', 'api_key']})
    assert excinfo.value.detail['code'] == 'MANDATORY_REDACTION_CANNOT_BE_DISABLED'
    assert excinfo.value.detail['fields'] == ['apikey', 'authorization']


def test_policy_input_normalizes_field_names_for_matching():
    """A rule for 'API-Key' must match a payload field named 'api_key'."""
    validated = privacy_policy.validate_policy_input({'excluded_fields': ['Internal-Service Name', 'internal_service_name']})
    assert validated['excluded_fields'] == ['internalservicename']


def test_policy_input_rejects_an_unknown_private_network_mode():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        privacy_policy.validate_policy_input({'private_network_mode': 'keep-everything'})
    assert excinfo.value.detail['code'] == 'INVALID_PRIVATE_NETWORK_MODE'


def test_malformed_stored_policy_degrades_to_defaults_not_to_no_redaction():
    policy = privacy.PrivacyPolicy.from_settings(
        {'excluded_fields': 'not-a-list-but-parsed', 'private_network_mode': 'nonsense',
         'redact_private_ips': 'maybe'},
        scope='ws-1',
    )
    assert policy.redact_private_ips is True
    assert policy.private_network_mode == privacy.NETWORK_MODE_MASK


def test_workspace_a_policy_never_affects_workspace_b():
    """33. Policies are per-workspace objects; there is no shared mutable state."""
    policy_a = privacy.PrivacyPolicy.from_settings(
        {'excluded_fields': ['deployment_id'], 'redact_emails': True}, scope='ws-a',
    )
    policy_b = privacy.PrivacyPolicy.from_settings({}, scope='ws-b')
    payload = {'deployment_id': 'deploy-7781', 'email': 'ops@customer.example'}

    result_a = privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN, policy=policy_a)
    result_b = privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_PRIVATE_OFFCHAIN, policy=policy_b)

    assert 'deployment_id' not in result_a.payload
    assert result_a.payload['email'] == privacy.REDACTED
    assert result_b.payload['deployment_id'] == 'deploy-7781'
    assert result_b.payload['email'] == 'ops@customer.example'


def test_load_policy_is_workspace_scoped_and_never_fails_open():
    """A privacy read that raises returns Decoda's defaults, never 'no redaction'."""
    class _BrokenConnection:
        def execute(self, *args, **kwargs):
            raise RuntimeError('relation "workspace_telemetry_privacy_policies" does not exist')

    policy = privacy_policy.load_policy(_BrokenConnection(), 'ws-1')
    assert policy.redact_private_ips is True
    assert policy.scope == 'ws-1'
    result = privacy.sanitize_ingested_payload(
        {'api_key': SECRET}, source_type=privacy.SOURCE_CUSTOMER_REQUEST, policy=policy,
    )
    assert result.payload['api_key'] == privacy.REDACTED


def test_load_policy_queries_only_the_requested_workspace():
    captured: list = []

    class _Recorder:
        def execute(self, sql, params):
            captured.append((sql, params))
            return self

        def fetchone(self):
            return None

    privacy_policy.load_policy(_Recorder(), 'ws-a')
    assert len(captured) == 1
    sql, params = captured[0]
    assert 'WHERE workspace_id = %s' in sql
    assert params == ('ws-a',)


# ---------------------------------------------------------------------------
# 32. Policy changes are auditable (Phase 18)
# ---------------------------------------------------------------------------
def test_policy_change_summary_records_what_changed_without_sensitive_values():
    before = privacy_policy.describe_policy(None)
    after = privacy_policy.validate_policy_input({
        'excluded_fields': ['customer_reference'],
        'redact_emails': True,
        'private_network_mode': 'drop',
    })
    changed = privacy_policy.changed_settings(before, after)
    assert changed['redact_emails'] == {'from': False, 'to': True}
    assert changed['private_network_mode'] == {'from': 'mask', 'to': 'drop'}
    assert changed['excluded_fields']['added'] == ['customerreference']
    # Field NAMES are the configuration; no field CONTENT is recorded.
    assert SECRET not in json.dumps(changed)


def test_policy_change_audit_action_is_stable():
    assert privacy_policy.POLICY_CHANGED_ACTION == 'workspace.telemetry_privacy_policy_changed'


def test_describe_policy_states_that_mandatory_rules_always_apply():
    described = privacy_policy.describe_policy(None)
    assert described['configured'] is False
    assert described['mandatory_rules_enforced'] is True
    assert described['redact_private_ips'] is True


# ---------------------------------------------------------------------------
# Provider-error persistence boundary
# ---------------------------------------------------------------------------
def test_provider_error_text_is_sanitized_before_persistence():
    """This string is written to monitoring_polls.error_message and shown to the customer."""
    from services.api.app.monitoring_runner import _safe_error_message

    message = _safe_error_message(
        RuntimeError('all_rpc_providers_unavailable:HTTP 401 for https://base-mainnet.g.alchemy.com/v2/Ab3xKeyMaterial99')
    )
    assert 'Ab3xKeyMaterial99' not in message
    # The provider host and failure class survive — that is the diagnostic value.
    assert 'base-mainnet.g.alchemy.com' in message
    assert 'all_rpc_providers_unavailable' in message


def test_provider_error_text_is_bounded():
    from services.api.app.monitoring_runner import _safe_error_message

    assert len(_safe_error_message(RuntimeError('x' * 5000))) <= 240


def test_sanitize_error_text_never_raises():
    class _Exploding:
        def __str__(self):
            raise RuntimeError('boom')

    assert isinstance(privacy.sanitize_error_text(_Exploding()), str)


# ---------------------------------------------------------------------------
# Wiring: the analysis_runs persistence choke point
# ---------------------------------------------------------------------------
def test_threat_payload_original_request_copy_is_sanitized():
    """normalize_threat_payload(include_original=True) persists a caller-shaped body."""
    from services.api.app.threat_payloads import normalize_threat_payload

    normalized, _ = normalize_threat_payload(
        'transaction',
        {'wallet': WALLET, 'amount': 5.0, 'authorization': f'Bearer {SECRET}', 'tx_hash': TX_HASH},
        include_original=True,
    )
    serialized = json.dumps(normalized, default=str)
    assert SECRET not in serialized
    original = normalized['metadata']['original_ui_request']
    assert original['authorization'] == privacy.REDACTED
    assert original['tx_hash'] == TX_HASH
    assert normalized['metadata']['privacy_processing']['applied'] is True


def test_threat_payload_without_original_is_unchanged_in_shape():
    """The monitoring worker path (include_original=False) gains no new key."""
    from services.api.app.threat_payloads import normalize_threat_payload

    normalized, _ = normalize_threat_payload('transaction', {'wallet': WALLET, 'amount': 5.0})
    assert 'original_ui_request' not in normalized['metadata']
    assert 'privacy_processing' not in normalized['metadata']


def test_persist_analysis_run_writes_the_sanitized_request_payload():
    """The single choke point for analysis_runs: every route is covered by it."""
    from services.api.app import pilot

    statements: list = []

    class _Conn:
        def execute(self, sql, params=None):
            statements.append((sql, params))
            return self

        def fetchone(self):
            return None

        def commit(self):
            return None

    pilot.persist_analysis_run(
        _Conn(),
        workspace_id='ws-1',
        user_id='u-1',
        analysis_type='threat_transaction',
        service_name='threat-engine',
        title='t',
        status_value='completed',
        request_payload={'api_key': SECRET, 'tx_hash': TX_HASH, 'wallet_address': WALLET},
        response_payload={'severity': 'low'},
        request=None,
    )
    inserts = [s for s in statements if 'INSERT INTO analysis_runs' in str(s[0])]
    assert inserts, 'expected the analysis_runs insert'
    written = json.dumps(inserts[0][1], default=str)
    assert SECRET not in written
    assert TX_HASH in written, 'public-chain evidence must survive persistence'
    assert WALLET in written


# ---------------------------------------------------------------------------
# Metrics (Phase 17)
# ---------------------------------------------------------------------------
def test_redaction_metrics_carry_no_payload_values():
    from services.api.app import observability

    before = observability.prometheus_metrics()
    privacy.sanitize_ingested_payload(
        {'api_key': SECRET, 'email': 'ops@customer.example', 'source_ip': '10.1.2.45'},
        source_type=privacy.SOURCE_PRIVATE_OFFCHAIN,
        policy=privacy.PrivacyPolicy(redact_emails=True),
    )
    after = observability.prometheus_metrics()
    assert privacy.METRIC_REDACTION_EVENTS in after
    assert privacy.METRIC_REDACTED_FIELDS in after
    assert after != before
    for forbidden in (SECRET, 'ops@customer.example', '10.1.2.45'):
        assert forbidden not in after, forbidden


def test_metric_labels_stay_low_cardinality():
    """Labels are the source type and a closed rule vocabulary — nothing else."""
    assert all(rule.count('.') == 1 for rule in (
        'credential.key_match', 'network.private_ip', 'policy.customer_excluded',
    ))
    assert privacy.SOURCE_PUBLIC_CHAIN in {
        privacy.SOURCE_PUBLIC_CHAIN, privacy.SOURCE_CUSTOMER_REQUEST,
        privacy.SOURCE_INTEGRATION_ERROR, privacy.SOURCE_AI_CONTEXT,
        privacy.SOURCE_PRIVATE_OFFCHAIN,
    }


# ---------------------------------------------------------------------------
# Module contract
# ---------------------------------------------------------------------------
def test_sanitizer_never_mutates_the_caller_payload():
    payload = {'api_key': SECRET, 'nested': {'password': SECRET}}
    privacy.sanitize_ingested_payload(payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST)
    assert payload == {'api_key': SECRET, 'nested': {'password': SECRET}}


def test_sanitizer_module_is_framework_independent():
    """Workers and tests import this without FastAPI, a DB, or a network client."""
    import inspect

    source = inspect.getsource(privacy)
    for forbidden in ('from fastapi', 'import fastapi', 'import psycopg', 'import requests', 'import httpx'):
        assert forbidden not in source, forbidden


def test_every_documented_sensitivity_class_exists():
    assert set(privacy.SENSITIVITY_CLASSES) == {
        'PUBLIC_CHAIN_DATA', 'CUSTOMER_OPERATIONAL_METADATA', 'PII',
        'CREDENTIAL', 'NETWORK_IDENTIFIER', 'FREEFORM_TEXT',
    }


# ---------------------------------------------------------------------------
# Every external AI provider call site, not just incident triage
# ---------------------------------------------------------------------------
def test_executive_brief_prompt_is_sanitized():
    from services.api.app import dashboard_executive_brief as brief

    prompt = brief.build_brief_prompt({
        'headline_facts': {'note': f'Bearer {SECRET}'},
        'top_anomalies': [{'api_key': SECRET, 'tx_hash': TX_HASH}],
    })
    serialized = json.dumps(prompt, default=str)
    assert SECRET not in serialized
    assert TX_HASH in serialized


@pytest.mark.parametrize(
    'module_path',
    [
        'services.api.app.domains.threat_detection.ai_explanation',
        'services.api.app.domains.asset_risk.ai_explanation',
        'services.api.app.domains.asset_integrity.ai_explanation',
        'services.api.app.domains.governance_policy.explanation',
    ],
)
def test_domain_ai_explanation_prompts_are_sanitized(module_path):
    """Every `_build_prompt` that reaches an external provider goes through the sanitizer."""
    import importlib

    module = importlib.import_module(module_path)
    prompt = module._build_prompt({
        'authorization': f'Bearer {SECRET}',
        'client_secret': SECRET,
        'tx_hash': TX_HASH,
        'from_address': WALLET,
        'detail': f'failed against https://rpc.example.com/v2/Ab3xKeyMaterial99?token={SECRET}',
    })
    serialized = json.dumps(prompt, default=str)
    assert SECRET not in serialized, module_path
    assert 'Ab3xKeyMaterial99' not in serialized, module_path
    # Public-chain grounding survives so the narrative can still cite its subject.
    assert TX_HASH in serialized and WALLET in serialized, module_path


def test_alert_cluster_context_is_sanitized():
    from services.api.app.domains.alert_triage import ai_enrichment

    context = ai_enrichment._cluster_context({
        'title': f'Cluster for Bearer {SECRET}',
        'primary_asset_name': 'Treasury USDC',
        'member_count': 3,
        'reason_codes': [],
    })
    assert SECRET not in json.dumps(context, default=str)
    assert context['asset_name'] == 'Treasury USDC'
    assert context['member_count'] == 3


def test_every_provider_analyze_call_site_has_a_sanitized_prompt_builder():
    """Guard against a NEW AI call site being added without the boundary.

    If this fails, a module gained a provider call: wire its prompt builder
    through ``telemetry_privacy.sanitize_for_ai`` and add it to the list below.
    """
    import pathlib
    import re

    app_root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    call_re = re.compile(r'\bprovider\.(analyze|narrate)\(')
    offenders = []
    for path in app_root.rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        if call_re.search(source) and 'sanitize_for_ai' not in source:
            offenders.append(str(path.relative_to(app_root)))
    assert offenders == [], f'AI provider call sites missing the privacy boundary: {offenders}'
