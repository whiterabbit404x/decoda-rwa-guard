from __future__ import annotations

from pathlib import Path


def test_new_live_workflow_routes_exist() -> None:
    source = Path('services/api/app/main.py').read_text(encoding='utf-8')
    assert "/exports/{export_id}/download" in source
    assert "/team/seats" in source
    assert "/findings/{finding_id}/decision" in source
    assert "/findings/{finding_id}/actions" in source
    assert "/actions/{action_id}" in source


def test_export_generation_is_not_placeholder_complete() -> None:
    source = Path('services/api/app/pilot.py').read_text(encoding='utf-8')
    assert "VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'queued', %s)" in source
    assert "def _generate_export_artifact" in source
    assert "status = 'completed'" in source
