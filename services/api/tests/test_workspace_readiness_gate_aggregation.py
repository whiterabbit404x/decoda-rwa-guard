from __future__ import annotations

from contextlib import contextmanager

from services.api.app import pilot


class _Result:
    def __init__(self, count: int):
        self._count = count

    def fetchone(self):
        return {'count': self._count}


class _Conn:
    def __init__(self, counts: dict[str, int]):
        self._counts = counts

    def execute(self, query: str, params):
        table = query.split('FROM ')[1].split(' WHERE')[0]
        return _Result(self._counts.get(table, 0))


def _patch_readiness_dependencies(monkeypatch, *, counts: dict[str, int], billing_available: bool, email_verified: bool, provider_status: str = 'healthy', redis_url: str = 'redis://example') -> None:
    conn = _Conn(counts)

    @contextmanager
    def _pg_connection():
        yield conn

    monkeypatch.setattr(pilot, 'pg_connection', _pg_connection)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_admin',
        lambda _c, _r: (
            {'id': 'u_1', 'email_verified_at': '2026-01-01T00:00:00Z' if email_verified else None},
            {'workspace': {'id': 'w_1'}, 'workspace_id': 'w_1'},
        ),
    )
    monkeypatch.setattr(pilot, 'billing_runtime_status', lambda: {'available': billing_available, 'provider': 'paddle'})
    monkeypatch.setattr(
        pilot,
        'integration_health_snapshot',
        lambda _c: {
            'email': {'status': provider_status},
            'auth_rate_limiter': {'status': provider_status},
        },
    )
    monkeypatch.setattr(pilot.Path, 'exists', lambda _self: True)
    monkeypatch.setenv('REDIS_URL', redis_url)


def test_workspace_readiness_exposes_gate_aggregation_and_ready_claim(monkeypatch) -> None:
    _patch_readiness_dependencies(
        monkeypatch,
        counts={
            'assets': 1,
            'monitoring_configs': 1,
            'telemetry_events': 1,
            'detections': 1,
            'detection_events': 0,
            'alerts': 1,
            'incidents': 1,
            'response_actions': 1,
            'evidence': 1,
            'detection_evidence': 0,
        },
        billing_available=True,
        email_verified=True,
    )

    payload = pilot.get_workspace_readiness(request=object())

    assert payload['controlled_pilot_ready'] is True
    assert payload['broad_self_serve_ready'] is True
    assert payload['enterprise_procurement_ready'] is True
    assert payload['enterprise_broad_self_serve_ready'] is True
    assert payload['hard_gates_pass'] is True
    assert payload['details']['gate_aggregation']['billing']['pass'] is True
    assert payload['details']['billing_email_provider_checks_passing'] is True
    assert payload['details']['production_validation_proof_bundle_complete'] is True


def test_workspace_readiness_fails_gate_reasons_deterministically(monkeypatch) -> None:
    _patch_readiness_dependencies(
        monkeypatch,
        counts={
            'assets': 1,
            'monitoring_configs': 1,
            'telemetry_events': 1,
            'detections': 1,
            'detection_events': 0,
            'alerts': 0,
            'incidents': 0,
            'response_actions': 0,
            'evidence': 1,
            'detection_evidence': 0,
        },
        billing_available=False,
        email_verified=False,
        provider_status='warning',
        redis_url='',
    )

    payload = pilot.get_workspace_readiness(request=object())

    assert payload['controlled_pilot_ready'] is False
    assert payload['broad_self_serve_ready'] is False
    assert payload['enterprise_procurement_ready'] is False
    assert payload['enterprise_broad_self_serve_ready'] is False
    assert payload['hard_gates_pass'] is False
    assert payload['details']['billing_email_provider_checks_passing'] is False
    assert payload['details']['production_validation_proof_bundle_complete'] is False
    assert payload['details']['gate_aggregation']['billing']['reason_code'] == 'billing_runtime_unavailable'
    assert payload['details']['gate_aggregation']['email']['reason_code'] == 'email_not_verified'
    assert payload['details']['gate_aggregation']['provider']['reason_code'] == 'provider_dependencies_unhealthy'
    assert 'billing_runtime_unavailable' in payload['blocking_failure_reason_codes']
    assert 'email_not_verified' in payload['blocking_failure_reason_codes']
    assert 'provider_dependencies_unhealthy' in payload['blocking_failure_reason_codes']
    assert 'production_validation_proof_bundle_incomplete' in payload['blocking_failure_reason_codes']
    assert 'alert_exists_failed' in payload['controlled_pilot_blocking_reason_codes']
    assert 'billing_runtime_unavailable' in payload['broad_self_serve_blocking_reason_codes']
    assert 'production_validation_proof_bundle_incomplete' in payload['enterprise_procurement_blocking_reason_codes']


def test_workspace_readiness_fails_when_billing_email_provider_checks_missing(monkeypatch) -> None:
    _patch_readiness_dependencies(
        monkeypatch,
        counts={
            'assets': 1,
            'monitoring_configs': 1,
            'telemetry_events': 1,
            'detections': 1,
            'detection_events': 0,
            'alerts': 1,
            'incidents': 1,
            'monitoring_runs': 1,
            'response_actions': 1,
            'evidence': 1,
            'detection_evidence': 0,
        },
        billing_available=False,
        email_verified=False,
        provider_status='warning',
    )

    payload = pilot.get_workspace_readiness(request=object())

    assert payload['hard_gates_pass'] is False
    assert payload['details']['gate_aggregation']['billing']['pass'] is False
    assert payload['details']['gate_aggregation']['email']['pass'] is False
    assert payload['details']['gate_aggregation']['provider']['pass'] is False


def test_workspace_readiness_proof_bundle_incomplete_when_alerts_incidents_or_runs_empty(monkeypatch) -> None:
    _patch_readiness_dependencies(
        monkeypatch,
        counts={
            'assets': 1,
            'monitoring_configs': 1,
            'telemetry_events': 1,
            'detections': 1,
            'detection_events': 0,
            'alerts': 0,
            'incidents': 0,
            'monitoring_runs': 0,
            'response_actions': 1,
            'evidence': 1,
            'detection_evidence': 0,
        },
        billing_available=True,
        email_verified=True,
        provider_status='healthy',
    )

    payload = pilot.get_workspace_readiness(request=object())

    assert payload['details']['production_validation_proof_bundle_complete'] is False
    assert 'production_validation_proof_bundle_incomplete' in payload['blocking_failure_reason_codes']
    assert payload['details']['counts']['alerts'] == 0
    assert payload['details']['counts']['incidents'] == 0
