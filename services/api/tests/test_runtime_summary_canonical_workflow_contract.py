from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary_fallback


def test_canonical_summary_exposes_workflow_counts_timestamps_and_statuses() -> None:
    summary = build_workspace_monitoring_summary_fallback(status_reason='contract')

    assert isinstance(summary.get('workflow_steps'), list)
    assert isinstance(summary.get('workflow'), dict)
    assert summary['workflow']['steps'] == summary['workflow_steps']
    assert summary['workflow']['current_step'] == summary['current_step']
    assert summary['workflow']['next_required_action'] == summary['next_required_action']
    assert isinstance(summary.get('counts'), dict)
    assert isinstance(summary.get('timestamps'), dict)
    assert isinstance(summary.get('statuses'), dict)
    assert summary.get('evidence_source') in {'none', 'simulator', 'live_provider'}
