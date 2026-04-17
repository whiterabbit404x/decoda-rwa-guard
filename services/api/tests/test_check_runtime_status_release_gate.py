from __future__ import annotations

import json

import pytest

from services.api.scripts import check_runtime_status_release_gate


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200):
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _payload(*, runtime_status: str, configured_systems: int) -> dict[str, object]:
    return {
        'workspace_id': 'ws-prod',
        'workspace_slug': 'prod',
        'runtime_status': runtime_status,
        'configured_systems': configured_systems,
        'status_reason': 'runtime_status_ok' if runtime_status == 'healthy' else 'runtime_status_degraded:database_error',
        'workspace_monitoring_summary': {
            'runtime_status': runtime_status,
            'configured_systems': configured_systems,
        },
    }


def test_release_gate_passes_when_status_recovers_before_retry_limit(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    responses = iter([
        _FakeResponse(_payload(runtime_status='degraded', configured_systems=2)),
        _FakeResponse(_payload(runtime_status='healthy', configured_systems=2)),
    ])
    monkeypatch.setattr(check_runtime_status_release_gate, 'urlopen', lambda *_a, **_k: next(responses))
    monkeypatch.setattr(check_runtime_status_release_gate.time, 'sleep', lambda _seconds: None)
    monkeypatch.setenv('RUNTIME_STATUS_RELEASE_GATE_ATTEMPTS', '2')
    monkeypatch.setenv('RUNTIME_STATUS_RELEASE_GATE_INTERVAL_SECONDS', '0')

    code = check_runtime_status_release_gate.main()
    assert code == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload['ok'] is True
    assert payload['attempts'] == 2


def test_release_gate_fails_when_configured_workspace_remains_degraded(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        check_runtime_status_release_gate,
        'urlopen',
        lambda *_a, **_k: _FakeResponse(_payload(runtime_status='degraded', configured_systems=3)),
    )
    monkeypatch.setenv('RUNTIME_STATUS_RELEASE_GATE_ATTEMPTS', '1')

    code = check_runtime_status_release_gate.main()
    assert code == 2

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload['ok'] is False
    assert any('runtime_status=degraded' in failure for failure in payload['failures'])


def test_release_gate_allows_degraded_when_workspace_not_configured(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        check_runtime_status_release_gate,
        'urlopen',
        lambda *_a, **_k: _FakeResponse(_payload(runtime_status='degraded', configured_systems=0)),
    )
    monkeypatch.setenv('RUNTIME_STATUS_RELEASE_GATE_ATTEMPTS', '1')

    code = check_runtime_status_release_gate.main()
    assert code == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload['ok'] is True
