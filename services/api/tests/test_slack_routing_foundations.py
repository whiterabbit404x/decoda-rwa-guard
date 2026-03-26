from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from services.api.app import pilot

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_slack_integration_routes_exist() -> None:
    source = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    assert '/integrations/slack' in source
    assert '/integrations/slack/{integration_id}/test' in source
    assert '/integrations/routing/{channel_type}' in source
    assert '/system/integrations/health' in source


def test_slack_secret_is_not_returned_from_list_query() -> None:
    source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')
    assert 'SELECT id, display_name, slack_mode, webhook_last4, bot_token_last4, default_channel, severity_routing, enabled, created_at, updated_at' in source
    list_block = source.split('def list_slack_integrations', 1)[1].split('def create_slack_integration', 1)[0]
    assert 'webhook_url_encrypted' not in list_block
    assert 'bot_token_encrypted' not in list_block


def test_routing_payload_validation() -> None:
    normalized = pilot._normalize_routing_payload({'severity_threshold': 'high', 'enabled': False, 'event_types': ['alert.created']}, channel_type='slack')
    assert normalized['severity_threshold'] == 'high'
    assert normalized['enabled'] is False

    try:
        pilot._normalize_routing_payload({'severity_threshold': 'urgent'}, channel_type='slack')
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError('Expected invalid severity threshold to fail.')


def test_encode_decode_slack_webhook_roundtrip() -> None:
    webhook = 'https://hooks.slack.com/services/T000/B000/secret'
    encoded = pilot._encode_secret_value(webhook)
    assert encoded != webhook
    assert pilot._decode_secret_value(encoded) == webhook


def test_slack_mode_normalization() -> None:
    assert pilot._normalize_slack_mode({'mode': 'bot'}) == 'bot'
    assert pilot._normalize_slack_mode({'mode': 'webhook'}) == 'webhook'
    try:
      pilot._normalize_slack_mode({'mode': 'bad'})
    except HTTPException as exc:
      assert exc.status_code == 400
    else:
      raise AssertionError('Expected invalid slack mode.')
