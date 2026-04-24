from __future__ import annotations

from services.api.app import monitoring_runner


def test_latency_alert_state_requires_sustained_window() -> None:
    workspace_key = 'ws-latency-window'
    monitoring_runner.RUNTIME_STATUS_ALERT_BREACH_HISTORY.pop(workspace_key, None)

    for _ in range(monitoring_runner.RUNTIME_STATUS_ALERT_WINDOW_SAMPLES - 1):
        sustained, breach_count, samples = monitoring_runner._latency_alert_state(
            workspace_key=workspace_key,
            metric='p95',
            breached=True,
        )
        assert sustained is False
        assert breach_count == samples


def test_latency_alert_state_alerts_after_required_breaches() -> None:
    workspace_key = 'ws-latency-sustained'
    monitoring_runner.RUNTIME_STATUS_ALERT_BREACH_HISTORY.pop(workspace_key, None)

    window = monitoring_runner.RUNTIME_STATUS_ALERT_WINDOW_SAMPLES
    required = min(monitoring_runner.RUNTIME_STATUS_ALERT_REQUIRED_BREACHES, window)

    for _ in range(required):
        sustained, _, _ = monitoring_runner._latency_alert_state(
            workspace_key=workspace_key,
            metric='p99',
            breached=True,
        )

    for _ in range(max(0, window - required)):
        sustained, breach_count, samples = monitoring_runner._latency_alert_state(
            workspace_key=workspace_key,
            metric='p99',
            breached=False,
        )

    assert samples == window
    assert breach_count >= required
    assert sustained is True
