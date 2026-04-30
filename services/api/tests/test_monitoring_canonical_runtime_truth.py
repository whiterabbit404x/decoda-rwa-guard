from services.api.app import monitoring_runner


def test_canonical_runtime_truth_enabled_defaults_true_when_unset(monkeypatch) -> None:
    monkeypatch.delenv('CANONICAL_RUNTIME_TRUTH_ENABLED', raising=False)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    monkeypatch.setattr(monitoring_runner, '_runtime_status_debug_enabled', lambda: True)

    assert monitoring_runner.is_canonical_runtime_truth_enabled() is True


def test_canonical_runtime_truth_allows_explicit_disable_only_in_non_production(monkeypatch) -> None:
    monkeypatch.setenv('CANONICAL_RUNTIME_TRUTH_ENABLED', 'false')
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    monkeypatch.setattr(monitoring_runner, '_runtime_status_debug_enabled', lambda: True)

    assert monitoring_runner.is_canonical_runtime_truth_enabled() is False


def test_canonical_runtime_truth_ignores_explicit_disable_in_live_or_production(monkeypatch) -> None:
    monkeypatch.setenv('CANONICAL_RUNTIME_TRUTH_ENABLED', 'false')
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, '_runtime_status_debug_enabled', lambda: True)

    assert monitoring_runner.is_canonical_runtime_truth_enabled() is True


def test_canonical_runtime_truth_ignores_explicit_disable_in_production_even_when_not_live(monkeypatch) -> None:
    monkeypatch.setenv('CANONICAL_RUNTIME_TRUTH_ENABLED', 'false')
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    monkeypatch.setattr(monitoring_runner, '_runtime_status_debug_enabled', lambda: False)

    assert monitoring_runner.is_canonical_runtime_truth_enabled() is True
