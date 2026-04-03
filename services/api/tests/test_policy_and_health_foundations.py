from __future__ import annotations

from services.api.app import pilot


def test_module_config_normalization_supports_guided_policy_shape() -> None:
    threat = pilot.normalize_module_config('threat', {'large_transfer_threshold': '300000', 'unknown_target_threshold': -1})
    assert threat['large_transfer_threshold'] == 300000.0
    assert threat['unknown_target_threshold'] == 0

    compliance = pilot.normalize_module_config('compliance', {'required_approvers_count': 0})
    assert compliance['required_approvers_count'] == 1

    resilience = pilot.normalize_module_config('resilience', {'monitoring_cadence_minutes': '5'})
    assert resilience['monitoring_cadence_minutes'] == 5


def test_integration_health_snapshot_masks_and_reports_flags(monkeypatch) -> None:
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_123')
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_123')
    monkeypatch.setenv('EMAIL_PROVIDER', 'console')
    monkeypatch.setenv('EMAIL_FROM', 'no-reply@example.com')

    snapshot = pilot.integration_health_snapshot(None)
    assert snapshot['stripe']['checks']['secret_key_present'] is True
    assert 'STRIPE_SECRET_KEY' in snapshot['stripe']['message'] or snapshot['stripe']['status'] in {'healthy', 'warning'}
    assert snapshot['slack']['checks']['bot_mode_supported'] is True
    assert snapshot['slack']['checks']['oauth_configured'] is False
